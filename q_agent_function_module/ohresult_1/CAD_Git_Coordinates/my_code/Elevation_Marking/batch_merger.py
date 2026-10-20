# -*- coding: utf-8 -*-
"""
多图标高拼接与融合后处理模块 (batch_merger.py)
负责将批量上传的多张图纸提取结果进行全量拼接、去重与互补融合：
1. 标高数值全集覆盖：所有图纸中出现过的合法标高全部保留，取并集互补；
2. 楼层信息最大化继承：相同标高优先采用明确层号（非未知楼层），优先保留结构标记 (S)；
3. 同层多标高自适应重整：同一楼层若融合出多个标高，自适应添加数值后缀规范化（如 B2_-18.000 与 B2_-16.870）；
4. 屋面层自底向上统一编号：重新梳理屋顶/屋面为 RF, RF01, RF02...；
5. 按标高自底向上单调递增排序输出。
"""

import re
from typing import Any, Dict, List, Optional, Tuple


class BatchElevationMerger:
    """多图标高拼接与融合后处理器"""

    @classmethod
    def merge_drawings_results(cls, all_results: List[List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
        """
        合并多张图纸的提取结果
        :param all_results: 每张图纸的 [{"floor": "...", "elevation": "..."}, ...] 列表构成的列表
        :return: 融合后全局最完整、无遗漏的标准楼层标高列表
        """
        if not all_results:
            return []

        # 过滤空结果
        valid_results = [r for r in all_results if r]
        if not valid_results:
            return []
        if len(valid_results) == 1:
            # 单张图纸自身已经是标准化的，但也做一次自底向上重整确保统一
            return cls._reorganize_records(valid_results[0])

        # 1. 扁平化收集所有记录，按物理标高数值（保留3位小数）归类
        elev_group_map: Dict[float, List[Dict[str, Any]]] = {}
        for drawing_records in valid_results:
            for item in drawing_records:
                elev_str = str(item.get("elevation", "")).strip()
                if not elev_str:
                    continue
                try:
                    clean_val = elev_str.replace('±', '').replace('+', '').strip()
                    val = round(float(clean_val), 3)
                except ValueError:
                    continue

                if val not in elev_group_map:
                    elev_group_map[val] = []
                elev_group_map[val].append(item)

        if not elev_group_map:
            return []

        # 2. 对每个标高数值，仲裁出信息量最全、最可靠的“基础楼层名”和“结构标记”
        merged_nodes: List[Dict[str, Any]] = []
        for val, items in elev_group_map.items():
            best_base_floor, has_s, best_disp_elev = cls._arbitrate_best_floor(val, items)
            merged_nodes.append({
                "val": val,
                "disp_elev": best_disp_elev,
                "base_floor": best_base_floor,
                "has_s": has_s,
            })

        # 3. 按真实物理标高自底向上严格升序排列
        merged_nodes.sort(key=lambda node: node["val"])

        # 4. 执行同层双标高探测与自适应后缀重整
        return cls._format_and_standardize_nodes(merged_nodes)

    @classmethod
    def _arbitrate_best_floor(cls, val: float, items: List[Dict[str, Any]]) -> Tuple[str, bool, str]:
        """
        在属于同一标高数值的所有候选项中，仲裁出最佳的基础楼层名、是否带(S)以及显示字符串
        """
        # 提取原样显示字符串（图纸中肉眼看到什么就保存什么）
        disp_elev = str(items[0].get("elevation", str(val)))

        # 统计是否带有结构标记 (S)
        has_s = False
        for item in items:
            f = str(item.get("floor", ""))
            if any(k in f.upper() for k in ("(S)", "（S）", "结构", "结")):
                has_s = True
                break

        # 候选基础楼层名质量评估
        best_base = "未知楼层"
        best_score = -1

        for item in items:
            raw_floor = str(item.get("floor", "")).strip()
            if not raw_floor:
                continue

            # 去掉已有的后缀，提取纯净的基础楼层名（如 "B2_-18.000" -> "B2", "RF(S)" -> "RF"）
            base = raw_floor.split("_")[0]
            base_clean = re.sub(r'[\(（][sS]?[）\)]', '', base).strip()

            score = cls._score_floor_name(base_clean)
            if score > best_score:
                best_score = score
                best_base = base_clean

        return best_base, has_s, disp_elev

    @staticmethod
    def _score_floor_name(name: str) -> int:
        """评估楼层名称的确定性得分，越高越优先"""
        if not name or name == "未知楼层":
            return 0
        if any(k in name for k in ("室外地坪", "室外地面", "女儿墙", "机房", "水箱间")):
            return 110  # 原图原生特定语义标高最高优先级
        if re.match(r'^[bB]\d+$', name):  # 如 B1, B2
            return 100
        if re.match(r'^[fF]\d+$', name):  # 如 F1, F2, F14
            return 100
        if re.match(r'^-?\d+[fF]?$', name):  # 如 1F, -1F
            return 90
        if any(k in name for k in ("屋面", "屋顶", "RF")):
            return 80
        return 50

    @classmethod
    def _reorganize_records(cls, records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """对单组记录做自底向上重整"""
        nodes: List[Dict[str, Any]] = []
        for r in records:
            elev_str = str(r.get("elevation", "")).strip()
            try:
                clean_val = elev_str.replace('±', '').replace('+', '').strip()
                val = round(float(clean_val), 3)
            except ValueError:
                continue
            raw_f = str(r.get("floor", "")).strip()
            has_s = "(S)" in raw_f.upper() or "（S）" in raw_f.upper()
            base = raw_f.split("_")[0]
            base_clean = re.sub(r'[\(（][sS]?[）\)]', '', base).strip()
            nodes.append({
                "val": val,
                "disp_elev": elev_str,
                "base_floor": base_clean or "未知楼层",
                "has_s": has_s
            })
        nodes.sort(key=lambda n: n["val"])
        return cls._format_and_standardize_nodes(nodes)

    @classmethod
    def _format_and_standardize_nodes(cls, nodes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        对已排序的全局标高节点执行同层双标高识别与屋面统一编号：
        - 若同属于某层（如 B2），存在多个标高，自适应添加数值后缀（B2_-18.000, B2_-16.870）；
        - 若只有单个标高，保持基础楼层名（如 B2）；
        - 屋面层序列统一生成 RF, RF01, RF02...
        """
        if not nodes:
            return []

        # 1. 统计常规楼层（非屋面、非未知楼层、非特殊语义标高）的出现次数
        base_floor_counts: Dict[str, int] = {}
        for node in nodes:
            bf = node["base_floor"]
            if bf != "未知楼层" and not any(k in bf for k in ("屋", "RF", "室外地坪", "女儿墙")):
                base_floor_counts[bf] = base_floor_counts.get(bf, 0) + 1

        # 2. 统计通用屋顶/屋面节点的索引位置（排除女儿墙、室外地坪等独立特征层）
        roof_indices = [
            i for i, node in enumerate(nodes)
            if node["base_floor"] in ("屋顶", "屋顶层", "屋面", "屋面层") or re.match(r'^RF\d*$', node["base_floor"], re.IGNORECASE)
        ]

        results: List[Dict[str, Any]] = []
        for i, node in enumerate(nodes):
            bf = node["base_floor"]
            disp_elev = node["disp_elev"]
            has_s = node["has_s"]

            if bf == "未知楼层":
                final_name = "未知楼层"
            elif i in roof_indices:
                order_in_roof = roof_indices.index(i)
                if order_in_roof == 0:
                    final_name = "RF"
                else:
                    final_name = f"RF{order_in_roof:02d}"
            else:
                # 检查同层是否出现多标高（并集融合后的关键处理）
                count = base_floor_counts.get(bf, 1)
                if count > 1:
                    final_name = f"{bf}_{disp_elev}"
                else:
                    final_name = bf

            # 附加结构标记 (S)（特定语义名称如“结构”、“室外地坪”、“女儿墙”不追加）
            if has_s and "(S)" not in final_name and "（S）" not in final_name:
                if final_name not in ("结构", "室外地坪", "女儿墙"):
                    final_name = f"{final_name}(S)"

            results.append({
                "floor": final_name,
                "elevation": disp_elev
            })

        return results
