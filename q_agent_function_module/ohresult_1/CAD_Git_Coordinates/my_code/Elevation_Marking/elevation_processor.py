# -*- coding: utf-8 -*-
"""
标高与楼层智能推导核心算法模块
负责空间聚类分列、选最全标高套、自底向上排序、同层双标高判定、层号与屋顶/屋面推导
"""

import re
import math
import statistics
from typing import Any, Dict, List, Optional, Tuple

try:
    from .cad_extractor import ElevationCandidate, FloorCandidate
except ImportError:
    from cad_extractor import ElevationCandidate, FloorCandidate


class ElevationProcessor:
    """标高分析与楼层推导引擎"""

    # 同一竖直列 X 坐标容差 (毫米，适应不同字符长度与引线对齐偏移)
    COLUMN_X_TOLERANCE = 1800.0

    # 同层双标高判断最大高差 (米，如 200mm = 0.2m)
    DUAL_ELEVATION_DIFF_MAX = 0.50

    def __init__(self, elevations: List[ElevationCandidate], floors: List[FloorCandidate]):
        self.raw_elevations = elevations
        self.raw_floors = floors

    def process(self) -> List[Dict[str, Any]]:
        """
        主处理入口：采用纯空间几何与竖向标高链拓扑判定，输出全图最全的一套楼层标高列表
        :return: [ {"floor": "1F", "elevation": "0.000"}, ... ]
        """
        if not self.raw_elevations:
            return []

        # 1. 按 X 坐标空间走廊聚类 + 竖向拓扑连续梯子断层切片（自动剔除孤立噪点与大空白）
        columns = self._cluster_into_columns(self.raw_elevations)
        if not columns:
            return []

        # 2. 识别图纸中的立剖面视图单元（按物理 Y 轴投影重叠率进行空间隔离，彻底防止上下叠放立面混淆）
        units = self._group_columns_into_units(columns)

        # 3. 对每个独立的立剖面单元分别推导楼层，杜绝跨图纸/跨立面的物理 Y 轴倒挂
        all_unit_results: List[Dict[str, Any]] = []
        for unit_col in units:
            unit_res = self._process_single_unit(unit_col)
            all_unit_results.extend(unit_res)

        # 4. 全图各单元结果按标高数值互补并集汇总
        final_result = self._merge_unit_results(all_unit_results)
        return final_result

    def _cluster_into_columns(self, candidates: List[ElevationCandidate]) -> List[List[ElevationCandidate]]:
        """
        根据 X 坐标相近原则聚类为竖直走廊，并在每个走廊内执行【竖向标高链拓扑断层切片】：
        1. 沿物理世界坐标 Y 从底向上扫描；
        2. 几何梯子连续性约束：立面标高链相邻层物理垂直距离一般在 1.5m~12m 内。
           若两点间物理空白 dy > 15000mm（15米），判定为标高梯子断裂（如立面跨入平面图区域），强行切片；
        3. 比例尺模数一致性约束：若 dy > 8000mm 且绘图比例 dy/dv 严重背离正常建筑比例 (400~2500)，切片；
        4. 数值断崖下跌 (dv < -1.8m)：直接切片（上下叠放图纸的分界）。
        """
        sorted_by_x = sorted(candidates, key=lambda c: c.x)
        raw_clusters: List[List[ElevationCandidate]] = []

        for item in sorted_by_x:
            matched_cluster = None
            for c in raw_clusters:
                avg_x = sum(e.x for e in c) / len(c)
                if abs(item.x - avg_x) <= self.COLUMN_X_TOLERANCE:
                    matched_cluster = c
                    break

            if matched_cluster is not None:
                matched_cluster.append(item)
            else:
                raw_clusters.append([item])

        split_columns: List[List[ElevationCandidate]] = []
        for c in raw_clusters:
            c_sorted_by_y = sorted(c, key=lambda it: it.y)
            curr_segment: List[ElevationCandidate] = []

            for item in c_sorted_by_y:
                if not curr_segment:
                    curr_segment.append(item)
                    continue

                prev_item = curr_segment[-1]
                dy_physical = item.y - prev_item.y
                dv_elevation = item.elevation_val - prev_item.elevation_val

                is_split_point = False
                # ① 数值断崖式下跌 > 1.8m
                if dv_elevation < -1.8:
                    is_split_point = True
                # ② 物理绘图大空白 > 15米（标高梯子断裂，剔除远端平面图漂移噪点）
                elif dy_physical > 15000.0:
                    is_split_point = True
                # ③ 物理绘图空白 > 8米，且数值反向或比例尺严重偏离建筑常规制图模数
                elif dy_physical > 8000.0:
                    if dv_elevation <= 0:
                        is_split_point = True
                    else:
                        scale = dy_physical / dv_elevation
                        if scale < 400.0 or scale > 2500.0:
                            is_split_point = True

                if is_split_point:
                    if len(curr_segment) >= 2:
                        split_columns.append(curr_segment)
                    curr_segment = [item]
                else:
                    curr_segment.append(item)

            if len(curr_segment) >= 2:
                split_columns.append(curr_segment)

        return split_columns if split_columns else raw_clusters

    @staticmethod
    def _merge_two_columns(col_a: List[ElevationCandidate], col_b: List[ElevationCandidate]) -> List[ElevationCandidate]:
        """合并两列标高取并集，若存在相同标高，优先保留带结构标记或楼层线索的实体"""
        merged_map: Dict[float, ElevationCandidate] = {}
        for e in col_a + col_b:
            key = round(e.elevation_val, 3)
            if key not in merged_map:
                merged_map[key] = e
            else:
                existing = merged_map[key]
                has_s_new = any(k in e.raw_text for k in ("结构", "结", "(S)", "（S）"))
                has_s_old = any(k in existing.raw_text for k in ("结构", "结", "(S)", "（S）"))
                if has_s_new and not has_s_old:
                    merged_map[key] = e
                elif e.floor_hint and not existing.floor_hint:
                    merged_map[key] = e
        return list(merged_map.values())

    def _calculate_floor_association(self, col: List[ElevationCandidate]) -> int:
        """
        计算一列标高与真实楼层文字的动态伴生度：
        1. 废除固定 1.8m 限制，采用排好序的相邻标高之间的真实层间物理区间 [Y_i - 300, Y_{i+1} + 300] 进行搜索；
        2. 顶层和底层自动外延合理层高；
        3. 水平方向按常规引出线容差 (dx <= 8000mm) 匹配；
        4. 楼层文字必须具备明确楼层语义（1F, 2F, B1, RF 等），屏蔽大样、详图、节点等无关字。
        """
        if not col:
            return 0
        if not self.raw_floors:
            return sum(1 for e in col if e.floor_hint)

        # 过滤合法的正规楼层候选文字（排除大样、详图、节点等字样）
        valid_floors = []
        for f in self.raw_floors:
            txt = f.floor_text.strip().upper()
            if any(k in txt for k in ("大样", "详图", "节点", "剖切", "索引", "DETAIL")):
                continue
            if self._parse_floor_num(txt) is not None or any(k in txt for k in ("RF", "屋面", "机房", "顶层", "出屋面", "避难")):
                valid_floors.append(f)

        # 按绘图物理坐标 Y 从小到大排序
        col_sorted = sorted(col, key=lambda e: e.y)
        n = len(col_sorted)

        # 估算该列在图纸上的典型层高绘图跨度（物理 dy）
        diffs_y = [col_sorted[k + 1].y - col_sorted[k].y for k in range(n - 1) if (col_sorted[k + 1].y - col_sorted[k].y) > 500.0]
        typical_dy = statistics.median(diffs_y) if diffs_y else 4500.0

        count = 0
        for i, e in enumerate(col_sorted):
            if e.floor_hint:
                count += 1
                continue

            # 动态确定当前标高在物理空间上的层间垂直搜索区间
            if i < n - 1:
                y_min = e.y - 300.0
                y_max = col_sorted[i + 1].y + 300.0
            else:
                # 顶层：向上延伸典型层高
                y_min = e.y - 300.0
                y_max = e.y + typical_dy + 300.0

            if i == 0:
                # 底层额外向下延伸典型层高
                y_min = min(y_min, e.y - typical_dy - 300.0)

            # 在水平 8000mm，垂直落入层间区间的文字中寻找伴生
            matched = False
            for f in valid_floors:
                if abs(f.x - e.x) <= 8000.0 and y_min <= f.y <= y_max:
                    matched = True
                    break
            if matched:
                count += 1

        return count

    @staticmethod
    def _validate_elevation_spacing_modulus(col: List[ElevationCandidate]) -> bool:
        """
        自适应层高模数一致性校验（利用多数标高区间高度一致的众数规律，不写死固定层高数值）：
        1. 建筑正常立面/剖面的标准层层高基本一致（存在显著的众数基准层高，如 3.0m, 4.5m, 6.0m 等）；
        2. 局部大样、节点详图标高差极其跳跃（如一下差 10m，一下差 16m），无法形成多数一致的层高模数；
        3. 若该列标高的高差极度离散、缺乏占主导地位的一致层高区间，判定为非建筑楼层系统予以剔除。
        """
        if len(col) < 3:
            return False

        # 按数值升序排列并去重
        vals = sorted(list({round(e.elevation_val, 3) for e in col}))
        if len(vals) < 3:
            return False

        # 计算相邻标高差序列，剔除同层双标高微小差值 (<= 0.50m)
        raw_diffs = [round(vals[i + 1] - vals[i], 3) for i in range(len(vals) - 1)]
        effective_diffs = [d for d in raw_diffs if d > 0.50]

        if len(effective_diffs) < 2:
            return False

        # 统计高差分布，寻找出现频次最高的基准层高区间（聚类容差 0.35m）
        clusters: List[List[float]] = []
        for d in effective_diffs:
            matched = False
            for cl in clusters:
                avg_cl = sum(cl) / len(cl)
                if abs(d - avg_cl) <= 0.35:
                    cl.append(d)
                    matched = True
                    break
            if not matched:
                clusters.append([d])

        clusters.sort(key=lambda c: len(c), reverse=True)
        dominant_cluster = clusters[0]

        # 核心规律：正常的建筑标高，大部分标高区间都是一样的
        # 如果每个高差全都不一样（dominant_cluster 仅有 1 个高差，且总有效高差数 >= 2），直接判定为跳跃大样
        if len(dominant_cluster) < 2 and len(effective_diffs) >= 2:
            return False

        dominant_mode_h = sum(dominant_cluster) / len(dominant_cluster)

        # 主导层高一致性比例必须达到多数（占有效高差的 45% 以上）
        mode_ratio = len(dominant_cluster) / len(effective_diffs)
        if mode_ratio < 0.45:
            return False

        # 计算落在基准层高及合理过渡层（如首层双层大堂）内的高差数量
        consistent_count = float(len(dominant_cluster))
        for d in effective_diffs:
            if abs(d - dominant_mode_h) > 0.35:
                if 1.3 * dominant_mode_h <= d <= 2.2 * dominant_mode_h + 0.35:
                    consistent_count += 0.6

        modulus_consistency_ratio = consistent_count / len(effective_diffs)
        return modulus_consistency_ratio >= 0.50

    def _group_columns_into_units(self, columns: List[List[ElevationCandidate]]) -> List[List[ElevationCandidate]]:
        """
        根据楼层文字伴生度和层高模数选出有效列，
        并按【物理 Y 轴投影高度重叠率】将列划分到不同的立剖面视图单元中（上下叠放图纸彻底物理隔离）：
        1. 左右配对要求：水平间距 dx <= 150m，且在垂直 Y 轴方向有显著重叠 (重叠率 >= 50%)；
        2. 上下叠放的立剖面（Y 轴跨度完全脱节）：作为独立的视图单元，各自独立推导楼层，彻底消灭倒挂。
        """
        if not columns:
            return []

        if len(columns) == 1:
            return [columns[0]]

        # 1. 计算所有候选列的“标高-层号双元素伴生度”
        col_assoc_map = {id(c): self._calculate_floor_association(c) for c in columns}
        max_assoc = max(col_assoc_map.values()) if col_assoc_map else 0

        if max_assoc >= 2:
            min_assoc_thresh = max(2, int(max_assoc * 0.30))
            valid_cols = [
                c for c in columns 
                if col_assoc_map[id(c)] >= min_assoc_thresh and (col_assoc_map[id(c)] / len(c) >= 0.25)
            ]
        else:
            max_col_len = max(len(c) for c in columns)
            min_threshold = max(3, int(max_col_len * 0.35))
            valid_cols = [
                c for c in columns 
                if len(c) >= min_threshold and self._validate_elevation_spacing_modulus(c)
            ]

        if not valid_cols:
            columns.sort(key=lambda c: len(c), reverse=True)
            return [columns[0]] if columns else []

        # 计算每列的 Y 轴范围、平均 X 及标高数值集合
        col_info = []
        for c in valid_cols:
            ys = [e.y for e in c]
            xs = [e.x for e in c]
            col_info.append({
                "col": c,
                "min_y": min(ys),
                "max_y": max(ys),
                "avg_x": sum(xs) / len(xs),
                "vals": {round(e.elevation_val, 3) for e in c}
            })

        col_info.sort(key=lambda item: item["avg_x"])

        # 组装视图单元
        units: List[List[ElevationCandidate]] = []
        used = set()

        for i in range(len(col_info)):
            if i in used:
                continue
            curr = col_info[i]
            merged = list(curr["col"])
            used.add(i)

            # 寻找能配对为同一栋楼左右两侧的列（必须满足：X 跨度 <= 150m，且 Y 轴垂直重叠率 >= 50%）
            for j in range(i + 1, len(col_info)):
                if j in used:
                    continue
                nxt = col_info[j]
                dx = abs(nxt["avg_x"] - curr["avg_x"])
                if dx > 150000.0:
                    continue

                # 垂直 Y 轴投影重叠计算
                overlap_y = min(curr["max_y"], nxt["max_y"]) - max(curr["min_y"], nxt["min_y"])
                h_curr = curr["max_y"] - curr["min_y"]
                h_nxt = nxt["max_y"] - nxt["min_y"]
                min_h = min(h_curr, h_nxt)
                overlap_ratio = overlap_y / min_h if min_h > 0 else 0

                inter_vals = curr["vals"].intersection(nxt["vals"])

                if overlap_ratio >= 0.50 and len(inter_vals) >= min(len(curr["vals"]), len(nxt["vals"])) * 0.4:
                    merged = self._merge_two_columns(merged, nxt["col"])
                    used.add(j)

            units.append(merged)

        return units

    def _process_single_unit(self, unit_col: List[ElevationCandidate]) -> List[Dict[str, Any]]:
        """对单个立剖面单元独立执行楼层分析推导"""
        sorted_elevs = self._sort_and_unique(unit_col)
        elev_nodes = self._detect_dual_elevations(sorted_elevs)
        matched_floors = self._find_associated_floor_candidates(unit_col, self.raw_floors)
        return self._assign_floor_names(elev_nodes, matched_floors)

    @staticmethod
    def _merge_unit_results(results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """全图多个独立立面推导结果按标高数值去重互补汇总"""
        merged_map: Dict[float, Dict[str, Any]] = {}
        for r in results:
            try:
                elev_clean = str(r.get("elevation", "")).replace('±', '').replace('+', '').strip()
                val = round(float(elev_clean), 3)
            except (ValueError, TypeError):
                continue

            if val not in merged_map:
                merged_map[val] = r
            else:
                existing = merged_map[val]
                # 若已有项是未知楼层，而新项有明确楼层名，优先保留明确楼层
                if existing.get("floor") == "未知楼层" and r.get("floor") != "未知楼层":
                    merged_map[val] = r
                # 若已有项因单立面被误推为 B1 但标高 > 0.5m，而新项为正常楼层，优先采纳正常楼层
                elif "B1" in existing.get("floor", "") and val > 0.5 and "B1" not in r.get("floor", ""):
                    merged_map[val] = r

        sorted_keys = sorted(merged_map.keys())
        return [merged_map[k] for k in sorted_keys]

    @staticmethod
    def _sort_and_unique(column: List[ElevationCandidate]) -> List[ElevationCandidate]:
        """自底向上升序排列，并按标高数值去重"""
        # 以 elevation_val 为键去重
        unique_map: Dict[float, ElevationCandidate] = {}
        for item in column:
            key = round(item.elevation_val, 3)
            # 若已有相同标高，优先保留带有 floor_hint 的项
            if key in unique_map:
                if item.floor_hint and not unique_map[key].floor_hint:
                    unique_map[key] = item
            else:
                unique_map[key] = item

        sorted_list = sorted(unique_map.values(), key=lambda e: e.elevation_val)
        return sorted_list

    def _detect_dual_elevations(self, sorted_elevs: List[ElevationCandidate]) -> List[Dict[str, Any]]:
        """
        检测同层近距离双标高 (高差 <= 0.50m)
        构建带层组标记的节点列表
        """
        nodes = []
        n = len(sorted_elevs)
        i = 0
        group_id = 0

        while i < n:
            curr = sorted_elevs[i]
            # 检查与下一个标高的高差
            if i + 1 < n:
                next_item = sorted_elevs[i + 1]
                diff = abs(next_item.elevation_val - curr.elevation_val)
                # 判定是否允许组合为同层双标高：
                # 1) 室外地坪与室内 ±0.000 (或任何其它主楼层) 绝不是同层双标高
                is_outdoor = any(
                    ("地坪" in (it.raw_text or "") or (it.floor_hint and "地坪" in it.floor_hint))
                    for it in (curr, next_item)
                ) or (curr.elevation_val < 0 and next_item.elevation_val >= 0 and diff > 0.15)

                # 2) 女儿墙/压顶作为屋面附属构件，绝不与下方屋顶结构层合并为同层双标高
                is_parapet = any(
                    ("女儿墙" in (it.raw_text or "") or (it.floor_hint and "女儿墙" in it.floor_hint))
                    for it in (curr, next_item)
                )

                if diff <= self.DUAL_ELEVATION_DIFF_MAX and not is_outdoor and not is_parapet:
                    # 判定为同层双标高
                    nodes.append({
                        "item": curr,
                        "group_id": group_id,
                        "is_dual": True
                    })
                    nodes.append({
                        "item": next_item,
                        "group_id": group_id,
                        "is_dual": True
                    })
                    i += 2
                    group_id += 1
                    continue

            # 普通单标高
            nodes.append({
                "item": curr,
                "group_id": group_id,
                "is_dual": False
            })
            i += 1
            group_id += 1

        return nodes

    def _find_associated_floor_candidates(
        self,
        best_elevs: List[ElevationCandidate],
        all_floors: List[FloorCandidate]
    ) -> List[FloorCandidate]:
        """寻找与当前最佳标高列在空间上对应的层号候选对象集合"""
        if not all_floors or not best_elevs:
            return []

        # 计算标高列的 X 均值与 Y 跨度
        avg_x = sum(e.x for e in best_elevs) / len(best_elevs)
        min_y = min(e.y for e in best_elevs) - 5000.0
        max_y = max(e.y for e in best_elevs) + 5000.0

        # 在 X 均值左右 12000mm、Y 跨度范围内筛选层号文字
        nearby_floors: List[FloorCandidate] = []
        for f in all_floors:
            if min_y <= f.y <= max_y and abs(f.x - avg_x) <= 12000.0:
                nearby_floors.append(f)

        if not nearby_floors:
            # 放宽 X 范围尝试
            nearby_floors = [f for f in all_floors if min_y <= f.y <= max_y]

        # 按 Y 坐标从下到上排序
        nearby_floors.sort(key=lambda f: f.y)
        return nearby_floors

    @staticmethod
    def _parse_floor_num(floor_str: str) -> Optional[int]:
        """将标准楼层字符串解析为整型层号 (如 1F->1, 14F->14, -1F->-1, B1->-1, B2->-2)"""
        if not floor_str:
            return None
        m_pos = re.match(r'^\+?(\d+)[fF]$', floor_str)
        if m_pos:
            return int(m_pos.group(1))
        m_neg = re.match(r'^-(\d+)[fF]?$', floor_str)
        if m_neg:
            return -int(m_neg.group(1))
        m_b = re.match(r'^[bB](\d+)$', floor_str)
        if m_b:
            return -int(m_b.group(1))
        return None

    def _assign_floor_names(
        self,
        elev_nodes: List[Dict[str, Any]],
        floor_candidates: List[FloorCandidate]
    ) -> List[Dict[str, Any]]:
        """
        基于真实空间物理 Y 坐标的四阶段拓扑分配引擎：
        1. 第一阶段：同行水平吸附 (最高优先级，垂直偏差 <= 400mm 1对1锁定)
        2. 第二阶段：层间净空区间归属 (落入 [Y_底, Y_顶] 层间空间的文字精确绑定给底标高)
        3. 第三阶段：双向拓扑插值与首尾外推 (根据前后楼层数字闭合填补缺失层如 9F，推导地下与屋顶)
        4. 第四阶段：同层双标高与组名生成
        """
        if not elev_nodes:
            return []

        # 1. 整理物理楼层组 (group_id) 信息，同层双标高聚合为一个楼层组
        group_ids = []
        for node in elev_nodes:
            if node["group_id"] not in group_ids:
                group_ids.append(node["group_id"])

        groups_info = []
        for gid in group_ids:
            g_nodes = [n for n in elev_nodes if n["group_id"] == gid]
            avg_y = sum(n["item"].y for n in g_nodes) / len(g_nodes)
            avg_x = sum(n["item"].x for n in g_nodes) / len(g_nodes)
            min_elev = min(n["item"].elevation_val for n in g_nodes)
            hints = [n["item"].floor_hint for n in g_nodes if n["item"].floor_hint]
            groups_info.append({
                "group_id": gid,
                "y": avg_y,
                "x": avg_x,
                "elevation_val": min_elev,
                "hint": hints[0] if hints else None,
                "assigned_floor": None,
                "floor_num": None
            })

        # 按物理 Y 从下到上严格排序 (自底向上)
        groups_info.sort(key=lambda g: g["y"])
        n_groups = len(groups_info)
        used_floor_ids = set()

        # =========================================================================
        # 阶段 1：同行水平对齐吸附 (同行共线标注，最高优先级 1对1 锁定)
        # =========================================================================
        for g in groups_info:
            matched = []
            for f in floor_candidates:
                if id(f) in used_floor_ids:
                    continue
                dy = abs(f.y - g["y"])
                dx = abs(f.x - g["x"])
                # 垂直视线高差 <= 400mm，水平在 10000mm 内
                if dy <= 400.0 and dx <= 10000.0:
                    matched.append((dy, dx, f))
            if matched:
                # 优先选垂直偏差最小、水平距离最近的
                matched.sort(key=lambda item: (item[0], item[1]))
                best_f = matched[0][2]
                g["assigned_floor"] = best_f.floor_text
                used_floor_ids.add(id(best_f))

        # =========================================================================
        # 阶段 2：层间净空物理区间归属 (处理居中、偏上等错位标注)
        # 每个楼层组与其上方相邻组构成垂直净空区间 [Y_curr, Y_next)
        # =========================================================================
        for i, g in enumerate(groups_info):
            if g["assigned_floor"] is not None:
                continue

            # 确定当前层的垂直净空区间上下界
            y_lower = g["y"] - 150.0
            if i + 1 < n_groups:
                y_upper = groups_info[i + 1]["y"] - 150.0
            else:
                # 最顶层上方净空放宽 5000mm
                y_upper = g["y"] + 5000.0

            # 寻找物理坐标落在该净空区间的未分配文字
            in_interval = []
            for f in floor_candidates:
                if id(f) in used_floor_ids:
                    continue
                if y_lower <= f.y < y_upper:
                    dx = abs(f.x - g["x"])
                    in_interval.append((dx, f))

            if in_interval:
                # 取水平方向最近的文字
                in_interval.sort(key=lambda item: item[0])
                best_f = in_interval[0][1]
                g["assigned_floor"] = best_f.floor_text
                used_floor_ids.add(id(best_f))

        # 若自身带有明确的单项括号线索 (如 5.400 (2F))，在尚未分配时予以采纳
        for g in groups_info:
            if g["assigned_floor"] is None and g["hint"]:
                g["assigned_floor"] = g["hint"]

        # =========================================================================
        # 阶段 3：双向拓扑插值与首尾推导 (解决漏标 9F 或最底层漏标)
        # =========================================================================
        for g in groups_info:
            if g["assigned_floor"]:
                g["floor_num"] = self._parse_floor_num(g["assigned_floor"])

        # 3.1 拓扑插值：修复中间连续缺失的标准楼层 (如 8F 与 10F 中间插值 9F)
        i = 0
        while i < n_groups:
            if groups_info[i]["floor_num"] is None and (not groups_info[i]["assigned_floor"] or not any(k in groups_info[i]["assigned_floor"] for k in ("屋", "RF"))):
                start = i
                while i < n_groups and groups_info[i]["floor_num"] is None and (not groups_info[i]["assigned_floor"] or not any(k in groups_info[i]["assigned_floor"] for k in ("屋", "RF"))):
                    i += 1
                end = i
                prev_idx = start - 1
                next_idx = end
                if prev_idx >= 0 and next_idx < n_groups:
                    p_num = groups_info[prev_idx]["floor_num"]
                    n_num = groups_info[next_idx]["floor_num"]
                    k = end - start
                    # 检查是否满足等差连续递增条件 (如 10 - 8 = 2, k=1 -> 2 == 1+1)
                    if p_num is not None and n_num is not None and (n_num - p_num == k + 1):
                        for step, mid_idx in enumerate(range(start, end), start=1):
                            target_num = p_num + step
                            groups_info[mid_idx]["floor_num"] = target_num
                            if target_num > 0:
                                groups_info[mid_idx]["assigned_floor"] = f"{target_num}F"
                            elif target_num < 0:
                                groups_info[mid_idx]["assigned_floor"] = f"{target_num}F"
            else:
                i += 1

        # 记录是否有图纸自带的楼层文字命中
        has_real_floor_matched = any(g["assigned_floor"] is not None for g in groups_info)

        if has_real_floor_matched:
            # 3.2 基准锚点处理：若 0.000 标高未分配，锁定为 1F
            for g in groups_info:
                if abs(g["elevation_val"]) < 1e-4 and g["assigned_floor"] is None:
                    g["assigned_floor"] = "1F"
                    g["floor_num"] = 1

            # 3.3 首尾外推：
            known_indices = [idx for idx, g in enumerate(groups_info) if g["floor_num"] is not None]
            if known_indices:
                # 向下外推负楼层 (B1, B2...)
                lowest_idx = known_indices[0]
                lowest_num = groups_info[lowest_idx]["floor_num"]
                cur_num = lowest_num
                for idx in range(lowest_idx - 1, -1, -1):
                    if groups_info[idx]["assigned_floor"] is None:
                        # 建筑常识熔断：正标高 (> 0.200m) 严禁向下外推为地下室 (负楼层)
                        if groups_info[idx]["elevation_val"] > 0.200:
                            break
                        curr_hint = groups_info[idx].get("hint") or ""
                        # 若自身带有地坪标记或紧随0标高之下的微负标高，判定为室外地坪
                        if "地坪" in curr_hint or (cur_num == 1 and groups_info[idx]["elevation_val"] >= -0.6):
                            groups_info[idx]["assigned_floor"] = "室外地坪"
                            continue
                        cur_num -= 1
                        if cur_num == 0:
                            cur_num = -1
                        groups_info[idx]["floor_num"] = cur_num
                        groups_info[idx]["assigned_floor"] = f"{cur_num}F"

                # 向上外推顶层（屋顶/屋面/女儿墙/结构）
                highest_idx = known_indices[-1]
                unassigned_above = [idx for idx in range(highest_idx + 1, n_groups) if groups_info[idx]["assigned_floor"] is None]
                if unassigned_above:
                    for idx in unassigned_above:
                        h = groups_info[idx].get("hint") or ""
                        if "女儿墙" in h or "压顶" in h:
                            groups_info[idx]["assigned_floor"] = "女儿墙"
                        elif "结构" in h:
                            groups_info[idx]["assigned_floor"] = "结构"
                        elif any(k in h for k in ("机房", "水箱间")):
                            groups_info[idx]["assigned_floor"] = h

                    still_unassigned = [idx for idx in unassigned_above if groups_info[idx]["assigned_floor"] is None]
                    if still_unassigned:
                        # 紧随最高常规楼层的第一个赋予 "屋顶层"
                        groups_info[still_unassigned[0]]["assigned_floor"] = "屋顶层"
                        # 其余上方标高赋予 "屋面"
                        for idx in still_unassigned[1:]:
                            groups_info[idx]["assigned_floor"] = "屋面"

            # 3.4 层内局部附属标高归属（处理夹在已知连续层之间未命名的局部标高）
            for idx in range(n_groups):
                if groups_info[idx]["assigned_floor"] is None:
                    prev_idx = idx - 1
                    while prev_idx >= 0 and groups_info[prev_idx]["assigned_floor"] is None:
                        prev_idx -= 1
                    next_idx = idx + 1
                    while next_idx < n_groups and groups_info[next_idx]["assigned_floor"] is None:
                        next_idx += 1

                    if prev_idx >= 0:
                        prev_elev = groups_info[prev_idx]["elevation_val"]
                        curr_elev = groups_info[idx]["elevation_val"]
                        diff_prev = abs(curr_elev - prev_elev)

                        if next_idx < n_groups:
                            next_elev = groups_info[next_idx]["elevation_val"]
                            diff_next = abs(next_elev - curr_elev)
                            # 与前一层高差较小 (<= 1.8m)，且明显归属于前一层空间
                            if diff_prev <= 1.8 and diff_prev <= diff_next * 0.5:
                                groups_info[idx]["assigned_floor"] = groups_info[prev_idx]["assigned_floor"]
                                groups_info[idx]["floor_num"] = groups_info[prev_idx]["floor_num"]
                        else:
                            if diff_prev <= 1.8:
                                groups_info[idx]["assigned_floor"] = groups_info[prev_idx]["assigned_floor"]
                                groups_info[idx]["floor_num"] = groups_info[prev_idx]["floor_num"]
        else:
            # ponytail: 全图完全无楼层文字标注时，按标高自底向上排序推导楼层
            zero_candidates = [idx for idx, g in enumerate(groups_info) if abs(g["elevation_val"]) <= 0.3]
            base_1f_idx = zero_candidates[0] if zero_candidates else 0
            groups_info[base_1f_idx]["assigned_floor"] = "1F"
            groups_info[base_1f_idx]["floor_num"] = 1
            for idx in range(base_1f_idx + 1, n_groups):
                fn = idx - base_1f_idx + 1
                groups_info[idx]["assigned_floor"] = f"{fn}F"
                groups_info[idx]["floor_num"] = fn
            for step, idx in enumerate(range(base_1f_idx - 1, -1, -1), start=1):
                if step == 1 and groups_info[idx]["elevation_val"] >= -0.6:
                    groups_info[idx]["assigned_floor"] = "室外地坪"
                else:
                    groups_info[idx]["assigned_floor"] = f"-{step}F"
                    groups_info[idx]["floor_num"] = -step

        # =========================================================================
        # 阶段 4：组装最终结果与同层双标高拼接
        # =========================================================================
        assigned_map = {g["group_id"]: g["assigned_floor"] for g in groups_info}

        # 统计屋面标高的数量
        roof_surface_count = sum(1 for name in assigned_map.values() if name == "屋面")

        result: List[Dict[str, Any]] = []
        for node in elev_nodes:
            gid = node["group_id"]
            item: ElevationCandidate = node["item"]
            elev_val = item.elevation_val
            base_name = assigned_map.get(gid) or "未知楼层"

            # 提取原样肉眼所见字符串 (如 "±0.000", "5.400", "±0", "6")
            disp_elev = getattr(item, 'elev_str', None) or str(elev_val)

            # 确定最终 floor 字段显示值
            if base_name == "未知楼层":
                final_floor = "未知楼层"
            elif node["is_dual"]:
                final_floor = f"{base_name}_{disp_elev}"
            elif base_name == "屋面":
                if roof_surface_count > 1:
                    final_floor = f"屋面_{disp_elev}"
                else:
                    final_floor = "屋面层"
            else:
                final_floor = base_name

            result.append({
                "floor": final_floor,
                "elevation": disp_elev,
                "raw_text": item.raw_text
            })

        return result
