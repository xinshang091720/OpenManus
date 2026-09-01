"""Validation helpers for base point parameters."""

from __future__ import annotations

from typing import Any, Dict

REQUIRED_BASE_POINT_FIELDS = ["Northsouth", "Eastwest", "Elevation", "Angleton"]


def is_base_point_complete(data: Dict[str, Any]) -> bool:
    """Return True if all four key base point fields are present and non-empty."""
    if not isinstance(data, dict):
        return False
    return all(
        data.get(f) is not None
        and str(data.get(f)).strip() != ""
        and str(data.get(f)).strip().lower() != "none"
        for f in REQUIRED_BASE_POINT_FIELDS
    )
