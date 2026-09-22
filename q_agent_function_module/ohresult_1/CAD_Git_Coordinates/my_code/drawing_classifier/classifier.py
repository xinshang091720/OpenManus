# -*- coding: utf-8 -*-
"""
classifier.py
单张 CAD 图纸三级递进智能分类核心引擎
1. 级别 1：文件名优先判断（毫秒级无损）
2. 级别 2：图签/图框标题栏真实图名提取与判定（针对编号文件名，如 E-01.dwg）
3. 级别 3：全图文字与图层特征加权分析兜底
"""

import os
import re
import sys
import requests
import yaml
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

# 动态确保项目根目录在 sys.path 中
_p = os.path.dirname(os.path.abspath(__file__))
while _p and _p != os.path.dirname(_p):
    if os.path.exists(os.path.join(_p, "ohresult")):
        if _p not in sys.path:
            sys.path.insert(0, _p)
        break
    _p = os.path.dirname(_p)

from .rules import (
    EXCLUDE_PATTERNS,
    ELEVATION_SECTION_PATTERNS,
    MAJOR_CODE_PATTERNS,
    MAJOR_SUB_CATEGORIES,
    FLOOR_PREFIX_PATTERN,
    PLAN_SUFFIX_PATTERN
)

try:
    from ohresult.logger_config import setup_logger
    logger = setup_logger()
except Exception:
    import logging
    logger = logging.getLogger("drawing_classifier")

# 统一读取 config.yaml 中的 Revit 接口
_config_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config.yaml")
with open(_config_path, "r", encoding="utf-8") as f:
    config = yaml.safe_load(f)

get_dwg_text_coordinates_layer = config["revit"]["get_dwg_text_coordinates_layer"]


class DrawingClassifier:
    """单张 CAD 图纸三级智能识别引擎"""

    # 图签/图框典型图层关键词
    TITLE_BLOCK_LAYER_KEYWORDS = (
        "PUB_TITLE", "TITLE", "图框", "图签", "BORD", "PLOT", "BORDER"
    )

    def __init__(self, revit_api_url: Optional[str] = None, timeout: Optional[int] = None):
        self.revit_api_url = revit_api_url or get_dwg_text_coordinates_layer
        self.timeout = timeout  # 默认 None：不做任何时间限制，完全等待 Revit 执行完毕

    @classmethod
    def match_major_and_sub(cls, text: str) -> Optional[Tuple[str, str, str]]:
        """
        基于文本（图名/文件名）判断大专业与细分子类别
        :return: (major, sub_category, reason) 或 None
        """
        if not text:
            return None
        clean = text.strip()

        # -------------------------------------------------------------
        # 第 1 关：黑名单过滤（说明/目录/系统图/大样等非空间平面图）
        # -------------------------------------------------------------
        for p in EXCLUDE_PATTERNS:
            m = re.search(p, clean, re.IGNORECASE)
            if m:
                matched_kw = m.group(0)
                sub_name = matched_kw
                if any(k in matched_kw for k in ("系统", "原理", "单线", "接线")):
                    sub_name = "系统原理图"
                elif any(k in matched_kw for k in ("说明", "专篇")):
                    sub_name = "设计说明"
                elif any(k in matched_kw for k in ("目录", "封面")):
                    sub_name = "图纸目录"
                elif any(k in matched_kw for k in ("大样", "详图", "节点")):
                    sub_name = "大样节点图"
                return "排除的无用图纸", sub_name, f"命中黑名单关键词 '{matched_kw}'"

        # -------------------------------------------------------------
        # 第 2 关：立面图 / 剖面图专项提流
        # 前置保护：凡是包含“平面”二字的施工图，绝不属于立面图/剖面图！
        # -------------------------------------------------------------
        if "平面" not in clean:
            for p in ELEVATION_SECTION_PATTERNS:
                m = re.search(p, clean, re.IGNORECASE)
                if m:
                    sub_name = "剖面图" if "剖" in clean else "立面图"
                    return "立面_剖面图", sub_name, f"命中立剖面关键词 '{m.group(0)}'"


        # -------------------------------------------------------------
        # 第 3 关：四大机电与结构专业（电气 / 给排水 / 暖通 / 结构）长词优先匹配
        # -------------------------------------------------------------
        specialized_majors = ["电气", "给排水", "暖通", "结构"]

        # 优先按明确子关键词长词匹配
        for major in specialized_majors:
            for sub_kw in MAJOR_SUB_CATEGORIES[major]:
                if sub_kw in clean:
                    return major, sub_kw, f"命中{major}子关键词 '{sub_kw}'"

        # 检查专业图纸编号前缀（如 电施-01, 水施-02, 暖施-03, 结施-04）
        for major in specialized_majors:
            for code_regex in MAJOR_CODE_PATTERNS[major]:
                if re.search(code_regex, clean, re.IGNORECASE):
                    # 自适应提炼子类别名称：剥离楼层前缀与平面图后缀
                    auto_sub = cls._extract_adaptive_sub_name(clean)
                    return major, auto_sub or major, f"命中{major}专业代号 '{code_regex}'"

        # -------------------------------------------------------------
        # 第 4 关：建筑专业平面图（纯平面图，排除上述机电结构特征）
        # -------------------------------------------------------------
        is_arch_code = any(
            re.search(r, clean, re.IGNORECASE) for r in MAJOR_CODE_PATTERNS["建筑"]
        )
        is_plan = any(
            k in clean for k in ("平面图", "平面", "首层", "二层", "三层", "四层", "标准层", "屋顶", "屋面")
        )
        if is_arch_code or is_plan:
            return "建筑", "平面图", "命中建筑平面图特征"

        return None

    @classmethod
    def _extract_adaptive_sub_name(cls, text: str) -> str:
        """自适应提炼图名中间的核心子系统名称（剥离楼层前缀与平面图后缀）"""
        stem = Path(text).stem
        cleaned = re.sub(r'^[A-Za-z0-9\-_一二三四五六七八九十建结电水暖施]+\s*[-_—]\s*', '', stem)
        cleaned = FLOOR_PREFIX_PATTERN.sub('', cleaned)
        cleaned = PLAN_SUFFIX_PATTERN.sub('', cleaned)
        cleaned = cleaned.strip()
        return cleaned or "平面图"

    def classify_by_filename(self, dwg_path: str) -> Optional[Dict[str, Any]]:
        """级别 1：纯文件名规则秒级判断"""
        name = Path(dwg_path).stem
        res = self.match_major_and_sub(name)
        if res:
            major, sub_category, reason = res
            return {
                "major": major,
                "sub_category": sub_category,
                "decision_level": 1,
                "matched_text": name,
                "reason": f"文件名直接匹配 ({reason})",
                "source": "filename"
            }
        return None

    def _fetch_dwg_texts(self, dwg_path: str) -> List[Dict[str, Any]]:
        """调用本地 Revit 接口获取图纸内部全部文字图元"""
        abs_path = os.path.abspath(dwg_path)
        payload = {"dwgFilePath": abs_path}
        try:
            resp = requests.post(self.revit_api_url, json=payload, timeout=self.timeout)
            resp.raise_for_status()
            res_json = resp.json()
            return res_json.get("data") or []
        except Exception as e:
            logger.debug(f"接口读取图纸文字失败 ({abs_path}): {e}")
            return []

    TITLE_BLOCK_LAYER_KEYWORDS = (
        "PUB_TITLE", "TITLE", "TTLB", "图框", "图签", "BORD", "PLOT", "BORDER", "SYMB", "ANNO", "NAME", "HEADER"
    )

    def classify_by_title_block(
        self, dwg_path: str, raw_items: Optional[List[Dict[str, Any]]] = None
    ) -> Optional[Dict[str, Any]]:
        """级别 2：通过图签/图框标题栏读取真实中文图名"""
        if raw_items is None:
            raw_items = self._fetch_dwg_texts(dwg_path)
        if not raw_items:
            return None

        # 收集候选图名：
        # 1. 来自图签/标题/符号图层的文字
        # 2. 全局包含“平面图”、“布置图”、“立面图”、“剖面图”的文字图元
        title_texts: List[str] = []
        for it in raw_items:
            text = str(it.get("Text") or "").strip()
            layer = str(it.get("Text_layer") or "").upper()
            if not text:
                continue
            # 命中图签/标题栏图层
            if any(k in layer for k in self.TITLE_BLOCK_LAYER_KEYWORDS):
                title_texts.append(text)
            # 或者文字本身即是明确的图名（包含 平面图/布置图/立面/剖面）
            elif any(k in text for k in ("平面图", "布置图", "立面图", "剖面图")):
                title_texts.append(text)

        # 按照包含“平面图”或专业图纸特征优先排序
        title_texts.sort(key=lambda t: (
            0 if "平面图" in t else (1 if "布置图" in t else 2)
        ))

        # 在候选图名中尝试匹配专业
        for text in title_texts:
            res = self.match_major_and_sub(text)
            if res and res[0] != "排除的无用图纸":
                major, sub_cat, reason = res
                return {
                    "major": major,
                    "sub_category": sub_cat,
                    "decision_level": 2,
                    "matched_text": text,
                    "reason": f"内部图名匹配: {text} ({reason})",
                    "source": "title_block"
                }

        # 父目录智能辅助：若文件名无语义（如 图框.dwg、未命名.dwg），但父目录明确为专业名称
        parent_name = Path(dwg_path).parent.name
        if parent_name in ("建筑", "电气", "给排水", "暖通", "结构"):
            has_plan_text = any("平面" in str(it.get("Text") or "") for it in raw_items)
            if has_plan_text:
                return {
                    "major": parent_name,
                    "sub_category": "平面图",
                    "decision_level": 2,
                    "matched_text": parent_name,
                    "reason": f"图纸位于[{parent_name}]目录且内部含平面图特征",
                    "source": "parent_dir"
                }

        return None

    def classify_by_full_text(
        self, dwg_path: str, raw_items: Optional[List[Dict[str, Any]]] = None
    ) -> Dict[str, Any]:
        """级别 3：全图文字加权特征特征分析兜底"""
        if raw_items is None:
            raw_items = self._fetch_dwg_texts(dwg_path)

        if not raw_items:
            return {
                "major": "排除的无用图纸",
                "sub_category": "无法识别",
                "decision_level": 3,
                "matched_text": "",
                "reason": "未获取到图纸文字信息",
                "source": "full_text"
            }

        scores = {"电气": 0, "给排水": 0, "暖通": 0, "结构": 0, "建筑": 0, "立面_剖面图": 0}

        for it in raw_items:
            t = str(it.get("Text") or "").strip()
            l = str(it.get("Text_layer") or "").upper()
            if not t:
                continue

            if any(k in l for k in ("E-", "ELEC", "DIAN", "AL", "AP")) or any(k in t for k in ("配电箱", "插座", "照明", "强电")):
                scores["电气"] += 1
            if any(k in l for k in ("W-", "P-", "PLUMB", "SHUI")) or any(k in t for k in ("给水", "排水", "消火栓", "喷淋")):
                scores["给排水"] += 1
            if any(k in l for k in ("M-", "HVAC", "NUAN", "FENG")) or any(k in t for k in ("风管", "排烟", "送风", "空调")):
                scores["暖通"] += 1
            if any(k in l for k in ("S-", "STRUCT", "JIEGOU")) or any(k in t for k in ("配筋", "梁平法", "柱平法")):
                scores["结构"] += 1
            if "立面" in t or "剖面" in t or "室外地坪" in t:
                scores["立面_剖面图"] += 2
            if "平面" in t or "卧室" in t or "客厅" in t:
                scores["建筑"] += 1

        best_major = max(scores, key=scores.get)
        if scores[best_major] >= 3:
            return {
                "major": best_major,
                "sub_category": "综合平面图" if best_major != "立面_剖面图" else "立剖图",
                "decision_level": 3,
                "matched_text": "",
                "reason": f"全图特征统计最高得分: {best_major}({scores[best_major]}分)",
                "source": "full_text"
            }

        return {
            "major": "排除的无用图纸",
            "sub_category": "未分类图纸",
            "decision_level": 3,
            "matched_text": "",
            "reason": "全图特征不显著",
            "source": "full_text"
        }

    def classify(self, dwg_path: str, detail: bool = False) -> Dict[str, Any]:
        """
        单张图纸分类主入口
        """
        p = Path(dwg_path)
        abs_path = str(p.resolve())
        base_info = {
            "dwg_path": abs_path,
            "file_name": p.name,
        }

        # 级别 1：纯文件名规则判断（毫秒级秒回）
        res = self.classify_by_filename(str(p))
        if not res:
            # 级别 2：图签表格图名判定（调接口读取一次文字）
            raw_items = self._fetch_dwg_texts(str(p))
            res = self.classify_by_title_block(str(p), raw_items=raw_items)
            if not res:
                # 级别 3：全图文字加权特征兜底判断
                res = self.classify_by_full_text(str(p), raw_items=raw_items)

        base_info.update(res)

        if not detail:
            return {
                "dwg_path": abs_path,
                "major": base_info.get("major", "排除的无用图纸"),
                "sub_category": base_info.get("sub_category", "未分类图纸")
            }

        return base_info


def classify_drawing(dwg_path: str, detail: bool = False) -> Dict[str, Any]:
    """单张图纸分类便捷函数"""
    classifier = DrawingClassifier()
    return classifier.classify(dwg_path, detail=detail)
