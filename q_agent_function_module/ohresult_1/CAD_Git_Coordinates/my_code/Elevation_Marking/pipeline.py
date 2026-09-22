# -*- coding: utf-8 -*-
"""
DWG 标高提取主调度流程模块
统一调度 Revit 接口提取器、标高算法推导引擎与多图全量融合器
"""

import os
import sys
import json
import time
from pathlib import Path
from typing import Any, Dict, List, Union

try:
    from .cad_extractor import RevitDWGExtractor, CADExtractor, ElevationCandidate, FloorCandidate
    from .elevation_processor import ElevationProcessor
    from .floor_standardizer import FloorStandardizer
    from .batch_merger import BatchElevationMerger
except ImportError:
    from cad_extractor import RevitDWGExtractor, CADExtractor, ElevationCandidate, FloorCandidate
    from elevation_processor import ElevationProcessor
    from floor_standardizer import FloorStandardizer
    from batch_merger import BatchElevationMerger

# 动态确保项目根目录在 sys.path 中
_p = os.path.dirname(os.path.abspath(__file__))
while _p and _p != os.path.dirname(_p):
    if os.path.exists(os.path.join(_p, "ohresult")):
        if _p not in sys.path:
            sys.path.insert(0, _p)
        break
    _p = os.path.dirname(_p)

from ohresult.logger_config import setup_logger
logger = setup_logger()


def extract_elevation_from_dwg(dwg_path: Union[str, List[str]]) -> Dict[str, Any]:
    """
    从 DWG 图纸中自动提取最全的一套标高及对应楼层
    支持传入单张图纸路径，也兼容批量传入图纸列表（自动调用多图拼接融合）。
    :param dwg_path: 输入图纸文件路径 (.dwg)，或路径列表
    :return: 规范化的字典结果，例如:
             {
               "data": [
                 {"floor": "B2", "elevation": "-6.250"},
                 {"floor": "B1", "elevation": "-3.250"},
                 {"floor": "F1_0.000", "elevation": "0.000"},
                 ...
               ]
             }
    """
    # 若传入的是批量列表，自动转发至批量融合调度引擎
    if isinstance(dwg_path, (list, tuple)):
        return extract_elevation_batch(list(dwg_path))

    start_time = time.time()
    dwg_p = Path(dwg_path).resolve()
    if not dwg_p.exists():
        raise FileNotFoundError(f"未找到指定的图纸文件: {dwg_p}")

    extractor = RevitDWGExtractor()
    elev_candidates, floor_candidates = extractor.extract_candidates(str(dwg_p))

    processor = ElevationProcessor(elev_candidates, floor_candidates)
    raw_result = processor.process()

    final_result = FloorStandardizer.standardize(raw_result)

    elapsed = time.time() - start_time
    logger.info(f"图纸 {dwg_p.name} 标高提取完成，耗时: {elapsed:.2f}s，共成功提取 {len(final_result)} 个节点。")

    return {"data": final_result}


def extract_elevation_batch(dwg_paths: List[str]) -> Dict[str, Any]:
    """
    批量上传并解析多张图纸，对提取结果执行全量互补拼接，输出一套最完整的标高规范集合
    :param dwg_paths: 图纸文件路径列表 (.dwg)
    :return: 包含全局最全标高的字典结果 {"data": [{"floor": "...", "elevation": "..."}, ...]}
    """
    if not dwg_paths:
        return {"data": []}

    logger.info(f"========== 开始批量提取图纸标高，共计 {len(dwg_paths)} 张图纸 ==========")
    all_drawing_records: List[List[Dict[str, Any]]] = []

    for idx, p in enumerate(dwg_paths, start=1):
        p_str = str(p).strip()
        if not p_str:
            continue
        dwg_name = Path(p_str).name
        logger.info(f"[{idx}/{len(dwg_paths)}] 正在向 Revit 接口发起请求并解析图纸: {dwg_name} ...")
        try:
            single_res = extract_elevation_from_dwg(p_str)
            records = single_res.get("data", [])
            all_drawing_records.append(records)
            logger.info(f"[{idx}/{len(dwg_paths)}] 图纸 {dwg_name} 解析完成，提取到 {len(records)} 个标高节点")
        except Exception as e:
            logger.error(f"图纸 {Path(p_str).name} 提取失败: {e}", exc_info=True)

    merged_data = BatchElevationMerger.merge_drawings_results(all_drawing_records)

    unknown_elevs = [
        item.get("elevation")
        for item in merged_data
        if item.get("floor") == "未知楼层"
    ]
    if unknown_elevs:
        msg = f"⚠️ 检测到存在未知楼层标高（{', '.join(str(e) for e in unknown_elevs)}），请人工核查！"
    else:
        msg = "success"

    return {
        "msg": msg,
        "data": merged_data
    }


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    batch_dwgs = [
        r"D:\project\ai_agent\data\立面图\建施01、02、03——16栋 平立剖面图-reve_t3.dwg"
    ]
    results = extract_elevation_batch(batch_dwgs)
    print(json.dumps(results, ensure_ascii=False, indent=2))
