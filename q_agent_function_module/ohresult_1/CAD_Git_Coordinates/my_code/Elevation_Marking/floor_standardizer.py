# -*- coding: utf-8 -*-
"""
楼层名称标准化处理模块
负责将初步推导的楼层名转换为统一规范命名：
- 正楼层：1F -> F1, 2F -> F2, 14F -> F14
- 负楼层：-1F -> B1, -2F -> B2
- 屋顶层：屋顶/屋顶层 -> RF
- 屋面层：自下而上多屋面统一编号为 RF01, RF02...
- 结构标记：若 raw_text 包含“结构”或“(S)”，在对应楼层名后附加 (S)，如 RF(S), F14(S)
- 转换完成后彻底剔除 raw_text 字段，只返回纯净的 floor 与 elevation
"""

import re
from typing import Any, Dict, List


class FloorStandardizer:
    """楼层名称标准化器"""

    @classmethod
    def standardize(cls, records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        对提取到的楼层标高列表进行统一标准化转换，并剔除 raw_text 字段
        :param records: 带有初步 floor、elevation 以及 raw_text 的记录列表
        :return: 标准化转换后的纯净记录列表 [{"floor": "...", "elevation": "..."}]
        """
        if not records:
            return []

        # 1. 统计顶层集合（屋顶、屋面、RF）的出现索引
        roof_indices = []
        for idx, item in enumerate(records):
            floor_name = str(item.get("floor") or "").strip()
            if any(k in floor_name for k in ("屋顶", "屋面", "RF")):
                roof_indices.append(idx)

        standardized_results: List[Dict[str, Any]] = []

        for idx, item in enumerate(records):
            raw_floor = str(item.get("floor") or "").strip()
            elevation = item.get("elevation", "")
            raw_text = str(item.get("raw_text") or "").strip()

            # 2. 检查是否有“结构”标记（包含“结构”、单字“结”、(S) 等）
            has_structure_mark = (
                "结构" in raw_text or
                "结" in raw_text or
                "(S)" in raw_text.upper() or
                "（S）" in raw_text.upper() or
                "结构" in raw_floor or
                "结" in raw_floor
            )

            # 3. 基础楼层名标准化映射
            std_floor = cls._map_single_floor(raw_floor)

            # 4. 顶层集合（屋顶/屋面）自底向上规范化：
            # 首个（最底部的封顶层）为 RF，后续上方多屋面自底向上为 RF01, RF02...
            if idx in roof_indices:
                order_in_roof = roof_indices.index(idx)
                if order_in_roof == 0:
                    std_floor = "RF"
                else:
                    std_floor = f"RF{order_in_roof:02d}"

            # 5. 如果有结构标记，追加 (S)
            if has_structure_mark and "(S)" not in std_floor:
                std_floor = f"{std_floor}(S)"

            # 6. 组装最终结果：只保留 floor 和 elevation，彻底剔除 raw_text
            standardized_results.append({
                "floor": std_floor,
                "elevation": elevation
            })

        return standardized_results

    @classmethod
    def _map_single_floor(cls, raw_floor: str) -> str:
        """转换单个楼层名称"""
        # 处理同层双标高拼接情况，如 "-2F_-18.200" 或 "2F_5.400"
        suffix = ""
        base_floor = raw_floor
        if "_" in raw_floor:
            parts = raw_floor.split("_", 1)
            base_floor = parts[0]
            suffix = f"_{parts[1]}"

        # 规则 1：屋顶/屋顶层 -> RF
        if "屋顶" in base_floor:
            return f"RF{suffix}"

        # 规则 2：负楼层 -1F -> B1, -2F -> B2
        match_neg = re.match(r'^-(\d+)[fF]?$', base_floor)
        if match_neg:
            num = match_neg.group(1)
            return f"B{num}{suffix}"

        # 兼容已经是 B1, B2 的情况
        match_b = re.match(r'^[bB](\d+)$', base_floor)
        if match_b:
            num = match_b.group(1)
            return f"B{num}{suffix}"

        # 规则 3：正楼层 1F -> F1, 2F -> F2, 14F -> F14
        match_pos = re.match(r'^\+?(\d+)[fF]$', base_floor)
        if match_pos:
            num = match_pos.group(1)
            return f"F{num}{suffix}"

        # 兼容已经是 F1, F2 的情况
        match_f = re.match(r'^[fF](\d+)$', base_floor)
        if match_f:
            num = match_f.group(1)
            return f"F{num}{suffix}"

        # 规则 4：SF 结构顶层通常也是 RF(S) 或 SF
        if base_floor.upper() == "SF":
            return f"RF(S){suffix}"

        # 其他未匹配类型保持原样
        return f"{base_floor}{suffix}"
