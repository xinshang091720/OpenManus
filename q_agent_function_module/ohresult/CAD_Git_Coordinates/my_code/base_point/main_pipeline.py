"""Missing field definitions and checkers for base point setting."""

from __future__ import annotations

from typing import Any, Dict, List

FIELD_NAME_MAP = {
    "Northsouth": "调整基点坐标北南",
    "Eastwest": "调整基点坐标东西",
    "Elevation": "调整项目高程",
    "Angleton": "修改正北角度",
}


def get_missing_fields(data: Dict[str, Any]) -> List[str]:
    """Check data for missing or empty fields and return their Chinese names."""
    if not isinstance(data, dict):
        return list(FIELD_NAME_MAP.values())
    missing = []
    for eng_field, chn_name in FIELD_NAME_MAP.items():
        val = data.get(eng_field)
        if val is None or str(val).strip() == "" or str(val).strip().lower() == "none":
            missing.append(chn_name)
    return missing
