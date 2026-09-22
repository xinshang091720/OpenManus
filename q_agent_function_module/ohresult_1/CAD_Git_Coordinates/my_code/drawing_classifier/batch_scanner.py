# -*- coding: utf-8 -*-
"""
batch_scanner.py
全目录递归穿透扫描、垃圾图纸过滤与多专业批量分流归档器
"""

import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

# 动态确保项目根目录在 sys.path 中
_p = os.path.dirname(os.path.abspath(__file__))
while _p and _p != os.path.dirname(_p):
    if os.path.exists(os.path.join(_p, "ohresult")):
        if _p not in sys.path:
            sys.path.insert(0, _p)
        break
    _p = os.path.dirname(_p)

from .classifier import DrawingClassifier, classify_drawing

try:
    from ohresult.logger_config import setup_logger
    logger = setup_logger()
except Exception:
    import logging
    logger = logging.getLogger("batch_scanner")


def scan_dwg_files(folder_or_files: Union[str, List[str], Path]) -> List[Path]:
    """
    递归穿透扫描并收集所有合法的 DWG 文件
    自动过滤：
    1. 临时文件 (如 ~$*.dwg, .*.dwg)
    2. AutoCAD 备份文件 (*.bak)
    3. 损坏的 0KB 空文件
    """
    found_files: List[Path] = []

    if isinstance(folder_or_files, (list, tuple)):
        for item in folder_or_files:
            p = Path(item)
            if p.suffix.lower() == ".dwg":
                if not _is_invalid_file(p):
                    found_files.append(p)
    else:
        root = Path(folder_or_files).resolve()
        if root.is_file() and root.suffix.lower() == ".dwg":
            if not _is_invalid_file(root):
                found_files = [root]
        elif root.is_dir():
            # 递归穿透遍历所有深度子目录
            for p in root.rglob("*.dwg"):
                if not _is_invalid_file(p):
                    found_files.append(p)

    # 按文件路径自然排序
    found_files.sort(key=lambda x: str(x).lower())
    return found_files


def _is_invalid_file(p: Path) -> bool:
    """检查是否为垃圾临时文件或空文件"""
    name = p.name
    if name.startswith("~$") or name.startswith("."):
        return True
    if ".bak." in name.lower() or name.lower().endswith(".bak"):
        return True
    try:
        if p.exists() and p.is_file() and p.stat().st_size == 0:
            return True
    except Exception:
        pass
    return False


class MultiMajorClassifiedResult(dict):
    """
    分类结果增强字典：
    既支持层级访问 result["电气"]["强电"]、result["建筑"]["平面图"]，
    又向下完全兼容历史代码的直接访问 result.get("ELEVATION_SECTION") 与 result.get("FLOOR_PLAN")
    """

class MultiMajorClassifiedResult(dict):
    """
    分类结果增强字典（纯大专业扁平架构）：
    标准键名：
      - 建筑: List[str]
      - 电气: List[str]
      - 给排水: List[str]
      - 暖通: List[str]
      - 结构: List[str]
      - 立面_剖面图: List[str]
      - 排除的无用图纸: List[str]
    """

    @property
    def elevation_section_list(self) -> List[str]:
        """获取所有立面与剖面图纸的扁平路径列表"""
        return list(super().get("立面_剖面图", []))

    @property
    def floor_plan_list(self) -> List[str]:
        """获取建筑平面图纸列表（向下兼容）"""
        return list(super().get("建筑", []))

    def get_plans(self, majors: Union[str, List[str], None] = None) -> List[str]:
        """
        获取一个或多个大专业的平面图路径列表
        :param majors: 目标大专业名称（如 '电气'，或 ['建筑', '电气', '给排水']）。若为 None 则返回五大专业所有图纸
        :return: 聚合后的图纸路径列表 List[str]
        """
        all_construction_majors = ["建筑", "电气", "给排水", "暖通", "结构"]

        if majors is None:
            target_list = all_construction_majors
        elif isinstance(majors, str):
            target_list = [majors]
        else:
            target_list = list(majors)

        merged_plans: List[str] = []
        for m in target_list:
            flist = super().get(m, [])
            if isinstance(flist, list):
                merged_plans.extend(flist)
        return merged_plans

    @property
    def total_count(self) -> int:
        """获取所有已扫描 DWG 图纸总数（含立剖面、各专业与排除图纸）"""
        count = 0
        for flist in self.values():
            if isinstance(flist, list):
                count += len(flist)
        return count

    def get(self, key: Any, default: Any = None) -> Any:
        # 向后兼容历史大写键名
        if key == "ELEVATION_SECTION":
            return self.elevation_section_list
        if key == "FLOOR_PLAN":
            return self.floor_plan_list
        return super().get(key, default)

    def __getitem__(self, key: Any) -> Any:
        if key == "ELEVATION_SECTION":
            return self.elevation_section_list
        if key == "FLOOR_PLAN":
            return self.floor_plan_list
        return super().__getitem__(key)


def batch_classify_drawings(
    folder_or_files: Union[str, List[str], Path],
    target_majors: Union[str, List[str], None] = None,
    detail: bool = False
) -> MultiMajorClassifiedResult:
    """
    批量图纸分类主函数（纯大专业扁平架构）：
    1. 递归扫描全量子文件夹收集 DWG 图纸；
    2. 黑名单过滤说明/目录/系统图/大样；
    3. 识别提取立面_剖面图；
    4. 识别并直接归档至五大专业列表（建筑、电气、给排水、暖通、结构），同一个专业下的所有系统图纸合并在一起；
    :param folder_or_files: 目标文件夹或文件路径列表
    :param target_majors: 可选。指定目标专业（如 '电气' 或 ['建筑', '电气']）
    :param detail: 是否返回包含 decision_level、reason 等详细诊断信息
    :return: MultiMajorClassifiedResult 字典
    """
    files = scan_dwg_files(folder_or_files)
    classifier = DrawingClassifier()

    # 初始化 7 个顶层大专业纯列表
    all_categories = ["建筑", "电气", "给排水", "暖通", "结构", "立面_剖面图", "排除的无用图纸"]
    tree_results: Dict[str, List[Any]] = {cat: [] for cat in all_categories}

    for f in files:
        res = classifier.classify(str(f), detail=detail)
        major = res.get("major", "排除的无用图纸")
        item_val = res if detail else res.get("dwg_path", str(f.resolve()))

        if major in tree_results:
            tree_results[major].append(item_val)
        else:
            tree_results["排除的无用图纸"].append(item_val)

    wrapped_result = MultiMajorClassifiedResult(tree_results)
    return wrapped_result

