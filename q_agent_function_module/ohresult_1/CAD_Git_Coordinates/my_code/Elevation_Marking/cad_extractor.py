# -*- coding: utf-8 -*-
"""
DWG 图纸文字与坐标提取模块
基于 Revit 接口获取 DWG 图纸中的文字、坐标与图层，并进行语义清洗、标高识别与楼层号分类。
"""

import os
import re
import yaml
import requests
from dataclasses import dataclass
from typing import List, Optional, Tuple

_config_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config.yaml")
with open(_config_path, "r", encoding="utf-8") as f:
    config = yaml.safe_load(f)

get_dwg_text_coordinates_layer = config["revit"]["get_dwg_text_coordinates_layer"]


@dataclass
class ElevationCandidate:
    """标高候选点数据结构"""
    x: float
    y: float
    elevation_val: float
    elev_str: Optional[str] = None
    raw_text: str = ""
    floor_hint: Optional[str] = None

    def __post_init__(self):
        if self.elev_str is None:
            self.elev_str = str(self.elevation_val)


@dataclass
class FloorCandidate:
    """楼层号文字候选点数据结构"""
    x: float
    y: float
    floor_text: str


class RevitClientError(Exception):
    """Revit 接口请求异常"""
    pass


class RevitDWGExtractor:
    """通过 Revit API 接口提取 DWG 图元文字与坐标"""

    ELEVATION_PATTERN = re.compile(
        r'(?:(?<![a-zA-Z0-9])(?:±|(?:\+|-))\s*\d+(?:\.\d+)?(?![fF])|(?<![a-zA-Z0-9\./])\d+\.\d+)'
    )
    ELEVATION_LAYER_KEYWORDS = ("ELEV", "BG", "标高", "DIM_ELEV")
    FLOOR_PATTERN = re.compile(
        r'^(?:(?:\+|-)?\d+[fF]|B\d+|[bB][fF]|RF|SF|屋顶层?|屋面层?)$'
    )

    def __init__(self, api_url: Optional[str] = None, timeout: Optional[int] = None):
        self.api_url = api_url or get_dwg_text_coordinates_layer
        self.timeout = timeout  # 默认 None：不做任何时间限制，完全等待 Revit 处理完毕返回

    def extract_candidates(self, dwg_path: str) -> Tuple[List[ElevationCandidate], List[FloorCandidate]]:
        """
        调用 Revit 接口获取 DWG 中所有文字与坐标，提取标高与楼层号候选
        :param dwg_path: 目标 DWG 文件的绝对路径
        :return: (标高候选列表, 楼层候选列表)
        """
        abs_path = os.path.abspath(dwg_path)
        if not os.path.exists(abs_path):
            raise FileNotFoundError(f"指定的 DWG 图纸不存在: {abs_path}")

        payload = {"dwgFilePath": abs_path}
        try:
            resp = requests.post(self.api_url, json=payload, timeout=self.timeout)
            resp.raise_for_status()
            res_json = resp.json()
        except requests.RequestException as e:
            raise RevitClientError(f"请求 Revit 接口失败 ({self.api_url}): {e}") from e

        code = res_json.get("code")
        if code not in (200, "200", None) and "data" not in res_json:
            msg = res_json.get("msg") or res_json.get("message", "未知错误")
            raise RevitClientError(f"Revit 接口返回业务异常 (code={code}): {msg}")

        raw_items = res_json.get("data") or []
        elevations: List[ElevationCandidate] = []
        floors: List[FloorCandidate] = []
        raw_text_entities: List[Tuple[str, float, float]] = []

        coord_pattern = re.compile(r'[\(（]([^,\s]+)[,\s]+([^,\s]+)')

        for item in raw_items:
            text = item.get("Text") or ""
            if not text:
                continue

            clean_text = self._clean_cad_text(str(text).strip())
            if not clean_text:
                continue

            coord_str = str(item.get("Text_coordinates") or "")
            m = coord_pattern.search(coord_str)
            if not m:
                continue

            try:
                x = float(m.group(1))
                y = float(m.group(2))
            except ValueError:
                continue

            layer = str(item.get("Text_layer") or "")
            raw_text_entities.append((clean_text, x, y))
            self._classify_and_add(clean_text, x, y, elevations, floors, layer=layer)

        # 探测标高左右两侧同行/紧邻的说明文字 (如 "(结构)", "(结)", "(建筑)", "(檐口)")
        self._attach_horizontal_suffixes(elevations, raw_text_entities)

        return elevations, floors

    @staticmethod
    def _attach_horizontal_suffixes(elevations: List[ElevationCandidate], all_texts: List[Tuple[str, float, float]]):
        """探测标高左右两侧同行/紧邻的说明文字，并拼入 raw_text"""
        for elev in elevations:
            for text, tx, ty in all_texts:
                dx = tx - elev.x
                dy = abs(ty - elev.y)
                if -1500.0 <= dx <= 2500.0 and dy <= 500.0:
                    if any(k in text for k in ("结构", "结", "建筑", "檐口", "(S)", "（S）")):
                        if text not in elev.raw_text:
                            elev.raw_text = f"{elev.raw_text} {text}".strip()

    @classmethod
    def _clean_cad_text(cls, text: str) -> str:
        """清洗 AutoCAD MTEXT 控制字符与特殊空格"""
        text = text.replace('%%P', '±').replace('%%p', '±').replace('\\U+00B1', '±')
        text = text.replace('\u3000', ' ').replace('\\P', ' ')
        text = re.sub(r'\{[^\{\}]*\}', '', text)
        text = re.sub(r'\\A\d+;', '', text)
        text = re.sub(r'\\C\d+;', '', text)
        text = re.sub(r'\\H\d+;', '', text)
        text = re.sub(r'\\W[\d\.]+;', '', text)
        text = re.sub(r'\\T[\d\.]+;', '', text)
        text = re.sub(r'\^J', ' ', text)
        return text.strip()

    @classmethod
    def _classify_and_add(
        cls,
        text: str,
        x: float,
        y: float,
        elevations: List[ElevationCandidate],
        floors: List[FloorCandidate],
        layer: str = ""
    ):
        clean_strip = text.strip()
        if re.match(r'^[（\(]\s*-?\d+\.?\d*\s*[）\)]?$', clean_strip):
            return

        clean_floor = text.replace(' ', '')
        if cls.is_floor_text(clean_floor):
            std_floor_text = re.sub(r'^[（\(](.*?)[）\)]$', r'\1', clean_floor).strip().upper()
            floors.append(FloorCandidate(
                x=x,
                y=y,
                floor_text=std_floor_text
            ))
            return

        elev_match = cls.ELEVATION_PATTERN.search(text)
        is_elev = bool(elev_match)
        raw_elev_str = elev_match.group(0).strip() if elev_match else ""

        if not is_elev:
            clean_digits = text.strip()
            if re.match(r'^-?\d+$', clean_digits):
                is_elev_layer = any(k.upper() in layer.upper() for k in cls.ELEVATION_LAYER_KEYWORDS)
                int_val = int(clean_digits)
                if is_elev_layer and -100 <= int_val <= 1000:
                    is_elev = True
                    raw_elev_str = clean_digits

        if is_elev and raw_elev_str:
            clean_str = raw_elev_str.replace('±', '').replace('+', '').strip()
            try:
                val = float(clean_str)
            except ValueError:
                return

            bracket_match = re.search(r'[\(（]([^\)）]+)[\)）]', text)
            floor_hint = None
            if bracket_match:
                inside = bracket_match.group(1).strip()
                if cls.is_floor_text(inside):
                    floor_hint = re.sub(r'^[（\(](.*?)[）\)]$', r'\1', inside).strip().upper()

            elevations.append(ElevationCandidate(
                x=x,
                y=y,
                elevation_val=round(val, 4),
                elev_str=clean_str,
                raw_text=text,
                floor_hint=floor_hint
            ))

    @classmethod
    def is_floor_text(cls, text: str) -> bool:
        """判定一段简短文本是否为有效楼层标识"""
        if not text:
            return False
        t = re.sub(r'\s+', '', text).upper()
        t_clean = re.sub(r'^[（\(](.*?)[）\)]$', r'\1', t).strip()
        if re.match(r'^-?\d+\.?\d*$', t_clean):
            return False
        return bool(cls.FLOOR_PATTERN.match(t_clean))


# 兼容既有名称
CADExtractor = RevitDWGExtractor