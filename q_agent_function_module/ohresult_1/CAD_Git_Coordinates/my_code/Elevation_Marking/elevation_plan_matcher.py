# -*- coding: utf-8 -*-
"""
elevation_plan_matcher
======================
建筑标高与平面图智能对齐挂载模块：
将【立剖面标高系统】提取的楼层标高链，与【图纸分类器】分流出的平面图列表进行自动化双向匹配，
在每个标高节点中精准填入对应的平面图 DWG 路径（"dwg": [...]）。

核心能力：
1. 单层平面图秒级匹配（首层、F1、三层、地下一层等）；
2. 跨层 / 标准层平面图自动离散区间展开（如 "六~三十层平面图.dwg" 自动展开为 6 到 30 层逐层复用）；
3. 屋面与特殊顶层（屋面层、屋架层、机房层）自动映射至 RF 系列标高；
4. 纯 Python 逻辑，零外部重量级依赖，支持独立单测与主流程无缝集成。
"""

import os
import re
import copy
from pathlib import Path
from typing import Any, Dict, List, Set, Union, Optional

# 中文数字转换字典
_CN_NUM_MAP = {
    '零': 0, '一': 1, '二': 2, '三': 3, '四': 4,
    '五': 5, '六': 6, '七': 7, '八': 8, '九': 9,
    '十': 10, '两': 2, '首': 1, '顶': 'ROOF'
}


def _parse_chinese_or_arabic_int(s: str) -> Optional[int]:
    """解析中文或阿拉伯数字字符串为整数 (支持 1~99, 如 '三', '二十', '三十五', '12')"""
    if not s:
        return None
    s = s.strip()
    if s.isdigit():
        return int(s)

    # 中文数字简单解析
    if s == '十':
        return 10
    if len(s) == 1 and s in _CN_NUM_MAP:
        val = _CN_NUM_MAP[s]
        return val if isinstance(val, int) else None

    # 处理 "十一" 到 "九十九"
    if '十' in s:
        parts = s.split('十')
        tens = _CN_NUM_MAP.get(parts[0], 1) if parts[0] else 1
        units = _CN_NUM_MAP.get(parts[1], 0) if len(parts) > 1 and parts[1] else 0
        if isinstance(tens, int) and isinstance(units, int):
            return tens * 10 + units

    return None


# 垃圾信息过滤正则：过滤工程括号标签、审查版本标记、修改标记、CAD版本后缀、时间戳等
_NOISE_PATTERNS = [
    r'【[^】]*】|\[[^\]]*\]',                                                      # 方括号、工程标签
    r'\([^\)]*(?:已审|修改|提资|蓝图|报审|版|阶段|最终|过程)[^\)]*\)',             # 包含特定词汇的圆括号
    r'（[^）]*(?:已审|修改|提资|蓝图|报审|版|阶段|最终|过程)[^）]*）',
    r'(?:最终版|最新版|修改版|过程稿|蓝图|报审版|已审|报建|提资|备份|副本|\(\d+\)|（\d+）)', # 常见工程状态词与副本号
    r'(?:[_\-\s]*(?:t3|t8|reve|rev\d+|v\d+|bak)[_\-\s]*)',                      # CAD 软件与备份标记
    r'\b20\d{6}\b',                                                              # 20230501 类似日期串
]


def clean_noise_from_filename(filename: str) -> str:
    """
    清洗图纸文件名中的垃圾信息、时间戳和干扰后缀
    例如：【建施-04】一栋一单元_六~三十层平面图(已审最终版)_t3_20230815 - 副本(1)
         -> 一栋一单元 六~三十层平面图
    """
    clean = filename
    for p in _NOISE_PATTERNS:
        clean = re.sub(p, ' ', clean, flags=re.IGNORECASE)
    # 清理多余空括号以及无意义尾部连接符
    clean = re.sub(r'[\(\)（）]', ' ', clean)
    clean = re.sub(r'\s*-\s*$', '', clean)
    # 压缩多余空格
    clean = re.sub(r'\s+', ' ', clean).strip()
    return clean


class ElevationPlanMatcher:
    """标高与平面图智能对齐挂载器"""

    # 1. 屋顶层图纸专属：字面必须包含“屋顶”或“屋架”
    ROOF_TOP_PATTERNS = [r"屋顶", r"屋架"]

    # 2. 屋面层图纸专属：字面必须包含“屋面”
    ROOF_SURFACE_PATTERNS = [r"屋面"]


    @classmethod
    def _call_git_cad_floor_fallback(cls, dwg_name: str, candidate_texts: Optional[List[str]] = None) -> Set[Union[int, str]]:
        """
        兜底通道：当本地正则未能识别明确楼层时，
        调用同级 room_coordinates/git_cad_floor.py 中的大模型进行楼层判断
        """
        try:
            import sys
            # 动态确保能够导入同级 room_coordinates 模块
            cur_dir = os.path.dirname(os.path.abspath(__file__))
            my_code_dir = os.path.dirname(cur_dir)
            if my_code_dir not in sys.path:
                sys.path.insert(0, my_code_dir)

            from room_coordinates.git_cad_floor import judge_dxf_floor_with_qwen
            res = judge_dxf_floor_with_qwen(candidate_texts or [], dwg_filename=dwg_name)
            if not res or not isinstance(res, dict):
                return set()

            min_f = float(res.get("min_floor", 0.0))
            max_f = float(res.get("max_floor", 0.0))

            cov: Set[Union[int, str]] = set()
            # 1. 严格区分：屋顶层字面必须含“屋顶”，屋面层字面必须含“屋面”
            if "屋顶" in dwg_name:
                cov.add("ROOF_TOP")
            elif "屋面" in dwg_name:
                cov.add("ROOF")


            # 2. 地下室区间展开 (如 -3.0 ~ -1.0)
            if min_f < 0:
                start_b = int(min_f)
                end_b = int(max_f)
                for f in range(min(start_b, end_b), max(start_b, end_b) + 1):
                    cov.add(f)

            # 3. 地上普通层区间展开 (如 6.0 ~ 30.0 或 1.0 ~ 1.0)
            if max_f >= 1.0 and max_f < 1000.0:
                start_f = max(1, int(min_f))
                end_f = int(max_f)
                for f in range(start_f, end_f + 1):
                    cov.add(f)

            return cov
        except Exception:
            # 大模型网络异常或模块未配置时，优雅降级，不中断主流程
            return set()

    @classmethod
    def parse_plan_floor_coverage(cls, dwg_path: str, use_llm_fallback: bool = True) -> Set[Union[int, str]]:
        """
        根据平面图文件名（及图纸上下文），解析出该图纸所覆盖的所有标准楼层集合
        :param dwg_path: 平面图路径或文件名
        :param use_llm_fallback: 当正则未命中时是否使用大模型兜底
        :return: 覆盖的楼层集合，例如 {3}, {6, 7, ..., 30}, {"ROOF_TOP"}, {"ROOF"}, {-1}
        """
        raw_name = Path(dwg_path).stem
        # 1. 第一步：垃圾信息过滤与清洗
        name = clean_noise_from_filename(raw_name)
        coverage: Set[Union[int, str]] = set()

        # 2. 第二步：检查是否为“屋顶层图纸”（屋顶层专属：字面含屋顶，对应RF）
        if any(re.search(p, name, re.IGNORECASE) for p in cls.ROOF_TOP_PATTERNS):
            coverage.add("ROOF_TOP")
            return coverage

        # 3. 第三步：屋面层图纸（屋面、天面、屋架、机房等）完全按照之前屋面层的逻辑处理
        if any(re.search(p, name, re.IGNORECASE) for p in cls.ROOF_SURFACE_PATTERNS):
            coverage.add("ROOF")

        # 4. 第四步：检查地下连续跨层区间（如 "地下一层~地下三层", "负1~负3层", "B1-B3"）
        basement_range = re.search(
            r'(?:地下|负|[bB])\s*([0-9一二三四五六七八九十]+)(?:层)?\s*[~～\-—–至到]\s*(?:地下|负|[bB])?\s*([0-9一二三四五六七八九十]+)\s*(?:层|[fF])?',
            name,
            re.IGNORECASE
        )
        if basement_range:
            b_start = _parse_chinese_or_arabic_int(basement_range.group(1))
            b_end = _parse_chinese_or_arabic_int(basement_range.group(2))
            if b_start is not None and b_end is not None:
                min_b, max_b = min(b_start, b_end), max(b_start, b_end)
                for f in range(-max_b, -min_b + 1):
                    coverage.add(f)
                return coverage

        # 5. 第五步：检查连续跨层 / 标准层区间（如 "6~30层", "六~三十层", "2-5F", "二至五层", "5~11", "5~11张", "5~11楼面"）
        range_match = re.search(
            r'([0-9一二三四五六七八九十百]+)\s*[~～\-—–至到]\s*([0-9一二三四五六七八九十百]+)\s*(?:层|[fF]|标准层|楼面|张)?',
            name
        )
        if range_match:
            start_num = _parse_chinese_or_arabic_int(range_match.group(1))
            end_num = _parse_chinese_or_arabic_int(range_match.group(2))
            if start_num is not None and end_num is not None and start_num <= end_num:
                for f in range(start_num, end_num + 1):
                    coverage.add(f)
                return coverage

        # 6. 第六步：检查地下层（如 "地下一层", "地下1层", "负一层", "-1F", "B1", "B1层", "BF1"）
        basement_match = re.search(
            r'(?:地下|负)\s*([0-9一二三四五六七八九十]+)\s*层|[bB](?:[fF])?\s*([0-9]+)(?:层|[fF])?|(?<!\d)-([0-9]+)[fF]',
            name,
            re.IGNORECASE
        )
        if basement_match:
            num_str = basement_match.group(1) or basement_match.group(2) or basement_match.group(3)
            b_num = _parse_chinese_or_arabic_int(num_str)
            if b_num is not None:
                coverage.add(-abs(b_num))
                return coverage

        # 7. 第七步：检查地上普通单层（如 "一层", "首层", "三层", "1F", "4F"）
        single_match = re.search(
            r'(?:第|\b)?([0-9一二三四五六七八九十首]+)\s*(?:层|[fF])',
            name,
            re.IGNORECASE
        )
        if single_match:
            raw_s = single_match.group(1)
            num = _parse_chinese_or_arabic_int(raw_s)
            if num is not None and num > 0:
                coverage.add(num)
                return coverage

        # 8. 若命中屋面，保留 ROOF
        if coverage and "ROOF" in coverage:
            return coverage

        # 8. 第八步：若文件名本身不含楼层信息（如“图框.dwg”、“未命名.dwg”），尝试从图纸内部真实图名读取楼层
        if not coverage:
            internal_titles = cls._fetch_internal_plan_titles(dwg_path)
            candidate_coverages = []
            for it_title in internal_titles:
                it_cov = cls.parse_plan_floor_coverage(it_title, use_llm_fallback=False)
                if it_cov:
                    candidate_coverages.append(it_cov)
            if candidate_coverages:
                # 优先选用覆盖楼层集合最大的候选（例如标准层 3~11 层优于单层 11 层）
                candidate_coverages.sort(key=lambda c: len(c), reverse=True)
                return candidate_coverages[0]

        # 9. 第九步：若依然未提取出明确楼层，激活 git_cad_floor 大模型兜底判断！
        if not coverage and use_llm_fallback:
            llm_cov = cls._call_git_cad_floor_fallback(dwg_name=name)
            if llm_cov:
                return llm_cov

        return coverage

    @classmethod
    def _fetch_internal_plan_titles(cls, dwg_path: str) -> List[str]:
        """当文件名无楼层语义时，从图纸内部图签/图名文字中获取候选平面图名"""
        p = Path(dwg_path)
        if not p.is_file() or p.suffix.lower() != ".dwg":
            return []
        try:
            import requests, yaml
            _config_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config.yaml")
            if not os.path.exists(_config_path):
                return []
            with open(_config_path, "r", encoding="utf-8") as f:
                cfg = yaml.safe_load(f)
            _env = os.environ.get("BEESYNC_REVIT_API_BASE_URL")
            url = f"{_env.rstrip('/')}/GetDwgText" if _env else cfg.get("revit", {}).get("get_dwg_text_coordinates_layer")
            if not url:
                return []
            resp = requests.post(url, json={"dwgFilePath": str(p.resolve())}, timeout=10)
            items = resp.json().get("data") or []
            titles: List[str] = []
            for it in items:
                t = str(it.get("Text") or "").strip()
                # 必须明确包含“平面图”或“布置图”，且长度小于35字，杜绝长句施工说明文字干扰
                if any(k in t for k in ("平面图", "布置图")) and len(t) < 35:
                    titles.append(t)
            # 越短越像标准图名的优先
            titles.sort(key=lambda x: (0 if "平面图" in x else 1, len(x)))
            return titles
        except Exception as e:
            return []

    @classmethod
    def normalize_elevation_floor_id(cls, floor_str: str) -> Union[int, str]:
        """
        将标高系统产出的楼层名称规范化：
        1. 纯 RF / 屋顶层 -> 归一化为 'ROOF_TOP'（匹配屋顶层图纸）
        2. RF01, RF02... / 屋面层 -> 归一化为 'ROOF'（按之前屋面层逻辑处理）
        :return: 整数层号（地上为正，地下为负）或特殊标识 'ROOF_TOP' / 'ROOF'
        """
        if not floor_str:
            return 0
        s = str(floor_str).strip().upper()

        # 0. 特殊非楼层参照标高：室外地坪、女儿墙、压顶、檐口等直接返回原样，严禁被当作地上层误匹配首层平面图
        if any(k in s for k in ("地坪", "女儿墙", "压顶", "檐口")):
            return s

        # 1. 屋面层系列：带有编号的 RF01, RF02, RF01(S)... 或包含“屋面/天面”，或顶层标注“结构”，按屋面层逻辑处理
        if re.search(r'RF0\d', s) or "屋面" in s or "天面" in s or s == "结构":
            return "ROOF"

        # 2. 屋顶层专属：纯 RF / RF(S)（不带01编号）或字面包含“屋顶”，专一匹配屋顶层图纸
        if "屋顶" in s or re.match(r'^RF(?:\([^\)]*\))?$', s, re.IGNORECASE):
            return "ROOF_TOP"

        # 3. 地下室系列：B1, B2, -1F, -2F, 半地下一层, 地下一层 等
        b_match = re.match(r'^(?:B|-)(\d+)', s)
        if b_match:
            return -int(b_match.group(1))
        cn_b_match = re.match(r'^(?:半)?地下([一二三四五六七八九十\d]+)层?$', s)
        if cn_b_match:
            c = cn_b_match.group(1)
            val = int(c) if c.isdigit() else _CN_NUM_MAP.get(c, 1)
            return -val

        # 4. 地上层系列：F1, F2, F1_0.000, 1F, 2F 等
        f_match = re.match(r'^(?:F)?(\d+)', s)
        if f_match:
            return int(f_match.group(1))

        return s

    @classmethod
    def match_elevations_with_plans(
        cls,
        elevation_records: List[Dict[str, Any]],
        plan_drawings: List[str],
        floor_overrides: Optional[List[Dict[str, Any]]] = None,
    ) -> List[Dict[str, Any]]:
        """
        核心匹配函数：输入标高列表与平面图路径列表，为每个标高挂载对应的平面图
        :param elevation_records: 标高系统产出的列表，例如 [{"floor": "F1", "elevation": "0.000"}, ...]
        :param plan_drawings: 平面图 DWG 路径列表
        :param floor_overrides: 可选楼层映射覆盖列表
        :return: 挂载后的新列表，每项保证包含 "dwg": List[str]
        """
        if not elevation_records:
            return []

        # 1. 预先解析所有平面图的楼层覆盖范围字典 {dwg_path: {covered_floors}}
        plan_coverage_map: Dict[str, Set[Union[int, str]]] = {}
        for dwg in plan_drawings:
            p_str = str(dwg).strip()
            if not p_str:
                continue
            cov = cls.parse_plan_floor_coverage(p_str)
            plan_coverage_map[p_str] = cov

        # 应用人工或模型决策覆盖映射 (floor_overrides)
        if floor_overrides:
            for ov in floor_overrides:
                if not isinstance(ov, dict):
                    continue
                ov_dwg = ov.get("dwg_path") or ov.get("dwg_name") or ov.get("dwg")
                if not ov_dwg:
                    continue
                ov_floors = set()
                raw_floors = ov.get("floors") or ov.get("floor_numbers") or ov.get("floor_range")
                if isinstance(raw_floors, (list, tuple, set)):
                    for f in raw_floors:
                        norm_f = cls.normalize_elevation_floor_id(str(f))
                        ov_floors.add(norm_f)
                elif isinstance(raw_floors, str):
                    cov = cls.parse_plan_floor_coverage(raw_floors)
                    ov_floors.update(cov)
                elif isinstance(raw_floors, int):
                    ov_floors.add(raw_floors)

                if ov_floors:
                    target_key = str(ov_dwg).strip().lower()
                    for p_str in list(plan_coverage_map.keys()):
                        if target_key in str(p_str).lower():
                            plan_coverage_map[p_str] = ov_floors

        # 检查图纸端：是否存在包含“屋顶”的图纸，是否存在包含“屋面”的图纸
        has_roof_top = any("ROOF_TOP" in cov for cov in plan_coverage_map.values())
        has_roof = any("ROOF" in cov for cov in plan_coverage_map.values())
        # 核心触发条件：当没有屋顶层图纸，但有屋面层图纸时，将屋面作为屋顶处理
        roof_as_roof_top = has_roof and (not has_roof_top)

        # 辅助定位：在标高链中，位于确定最高地上楼层之后的第一个顶层标高（即原本的屋顶层位置）
        dynamic_roof_idx = None
        if roof_as_roof_top and elevation_records:
            confirmed_plan_floors = {
                f for cov in plan_coverage_map.values()
                for f in cov if isinstance(f, int) and f > 0
            }
            if confirmed_plan_floors:
                max_confirmed_floor = max(confirmed_plan_floors)
                for idx, raw_item in enumerate(elevation_records):
                    fn = str(raw_item.get("floor", ""))
                    fid = cls.normalize_elevation_floor_id(fn)
                    if fid == max_confirmed_floor:
                        if idx + 1 < len(elevation_records):
                            dynamic_roof_idx = idx + 1
                        break

        # 2. 遍历每个标高节点，执行双向对齐
        matched_results: List[Dict[str, Any]] = []
        for idx, raw_item in enumerate(elevation_records):
            item = copy.deepcopy(raw_item)
            floor_name = str(item.get("floor", ""))
            floor_id = cls.normalize_elevation_floor_id(floor_name)

            # 寻找所有覆盖了该楼层 ID 的平面图
            assigned_dwgs: List[str] = []
            for dwg_path, covered_floors in plan_coverage_map.items():
                # 1. 基础标准匹配
                if floor_id in covered_floors:
                    assigned_dwgs.append(dwg_path)
                # 2. 核心规则：当没有屋顶层图纸时，标高本来应该是屋顶层(ROOF_TOP)，就将屋面作为屋顶挂载！
                elif roof_as_roof_top and (floor_id == "ROOF_TOP" or idx == dynamic_roof_idx) and "ROOF" in covered_floors:
                    assigned_dwgs.append(dwg_path)

            item["dwgsFilePaths"] = sorted(list(set(assigned_dwgs)))
            matched_results.append(item)

        return matched_results



    @classmethod
    def bind_pipeline_result(
        cls,
        elevation_pipeline_data: Union[Dict[str, Any], List[Dict[str, Any]]],
        plan_drawings_or_classifier_result: Union[List[str], Dict[str, Any]],
        marjorName: Union[str, List[str], None] = None,
        target_majors: Union[str, List[str], None] = None,
        floor_overrides: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """
        上层便捷接入入口：支持直接接收 pipeline 的返回字典与分类器的返回结果
        :param elevation_pipeline_data: 标高主流程字典 {"data": [{"floor": "...", "elevation": "..."}, ...]}
        :param plan_drawings_or_classifier_result: 平面图路径列表，或分类器返回的字典 MultiMajorClassifiedResult
        :param marjorName: 专业名称（如 '结构'，或 '建筑'）
        :param floor_overrides: 可选楼层映射覆盖列表
        :return: {"msg": "...", "data": [...], "leveDatas": [...]}
        """
        # 统一参数名
        chosen_major = marjorName if marjorName is not None else target_majors

        # 提取标高记录列表
        raw_records = elevation_pipeline_data.get("data", []) if isinstance(elevation_pipeline_data, dict) else elevation_pipeline_data

        # 格式化专业描述标签
        if chosen_major is None:
            target_desc = "全专业"
        elif isinstance(chosen_major, (list, tuple)):
            target_desc = ", ".join(chosen_major)
        else:
            target_desc = str(chosen_major)

        # 提取平面图列表
        plan_list: List[str] = []
        if isinstance(plan_drawings_or_classifier_result, dict):
            # 若具备 get_plans 方法（新版 MultiMajorClassifiedResult），直接提取所选大专业图纸
            if hasattr(plan_drawings_or_classifier_result, "get_plans"):
                plan_list = plan_drawings_or_classifier_result.get_plans(majors=chosen_major)
            else:
                # 兼容普通字典结构
                all_m = ["建筑", "电气", "给排水", "暖通", "结构"] if chosen_major is None else ([chosen_major] if isinstance(chosen_major, str) else list(chosen_major))
                for m in all_m:
                    fl = plan_drawings_or_classifier_result.get(m, [])
                    if isinstance(fl, list):
                        plan_list.extend(fl)
        elif isinstance(plan_drawings_or_classifier_result, (list, tuple)):
            plan_list = list(plan_drawings_or_classifier_result)

        # 自闭环质检 1：若所选专业下没有任何图纸，直接返回专业缺图警告
        if not plan_list:
            return {
                "msg": f"⚠️ 目标专业 [{target_desc}] 下未找到有效的平面图纸，无法进行对齐！",
                "data": [],
                "leveDatas": []
            }

        matched_data = cls.match_elevations_with_plans(raw_records, plan_list, floor_overrides=floor_overrides)

        # 自闭环质检 2：检测是否所有楼层都成功匹配到平面图 DWG
        unmatched_floors = [
            item.get("floor")
            for item in matched_data
            if not item.get("dwgsFilePaths")
        ]
        if unmatched_floors:
            msg = f"⚠️ 目标专业 [{target_desc}] 检测到楼层 [{', '.join(unmatched_floors)}] 未匹配到任何平面图DWG，请人工核验！"
        else:
            msg = "success"

        return {
            "msg": msg,
            "data": matched_data,
            "leveDatas": matched_data
        }


def bind_elevations_and_plans(
    elevation_data: Union[Dict[str, Any], List[Dict[str, Any]]],
    plan_drawings: Union[List[str], Dict[str, Any]],
    marjorName: Union[str, List[str], None] = None,
    target_majors: Union[str, List[str], None] = None,
    floor_overrides: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """便捷调用函数，统一参数名称 marjorName"""
    return ElevationPlanMatcher.bind_pipeline_result(
        elevation_data if isinstance(elevation_data, dict) else {"data": elevation_data},
        plan_drawings,
        marjorName=marjorName if marjorName is not None else target_majors,
        floor_overrides=floor_overrides,
    )


if __name__ == "__main__":
    import json
    import sys
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    print("================ 标高与平面图智能对齐测试 ================")

    # 1. 模拟标高系统
    mock_elevation_output = {
        "data": [
            {"floor": "F1", "elevation": "0.000"},
            {"floor": "F2", "elevation": "2.900"},
            {"floor": "F3", "elevation": "5.800"}
        ]
    }

    # 【用法 1：直接传入纯图纸路径列表】
    mock_plans = ["D:/三层平面图.dwg", "D:/二层平面图.dwg"]
    res1 = bind_elevations_and_plans(mock_elevation_output, mock_plans)
    print("\n[用法 1 结果 (纯列表)]:")
    print(json.dumps(res1, ensure_ascii=False, indent=2))

    # 【用法 2：直接传入纯大专业分类字典 + 支持同时传入多个大专业合并挂载】
    mock_classified_tree = {
        "建筑": ["D:/首层建筑平面图.dwg", "D:/二层建筑平面图.dwg"],
        "电气": ["D:/首层应急照明平面图.dwg", "D:/首层普通照明平面图.dwg", "D:/二层应急照明平面图.dwg"],
        "给排水": ["D:/首层喷淋平面图.dwg"]
    }
    # 比如同时传入 ["建筑", "电气", "给排水"]，一个标高层同时聚合多个专业的图纸
    res2 = bind_elevations_and_plans(
        mock_elevation_output,
        mock_classified_tree,
        target_majors=["建筑", "电气", "给排水"]
    )
    print("\n[用法 2 结果 (多大专业同时挂载到标高节点)]:")
    print(json.dumps(res2, ensure_ascii=False, indent=2))

