# -*- coding: utf-8 -*-
"""
drawing_classifier
==================
建筑与机电 CAD 图纸智能分类与多专业分流归档模块
"""

from .classifier import (
    DrawingClassifier,
    classify_drawing,
)
from .batch_scanner import (
    scan_dwg_files,
    batch_classify_drawings,
    MultiMajorClassifiedResult,
)

__all__ = [
    "DrawingClassifier",
    "classify_drawing",
    "scan_dwg_files",
    "batch_classify_drawings",
    "MultiMajorClassifiedResult",
]
