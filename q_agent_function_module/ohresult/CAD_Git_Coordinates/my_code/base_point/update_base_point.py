"""Helper to format base point payload for Revit BasePointSetting."""

from __future__ import annotations

from typing import Any, Dict, Optional


def _clean_str(val: Any) -> str:
    if val is None:
        return ""
    s = str(val).strip()
    return "" if s.lower() == "none" else s


def build_base_point_payload(
    step1_data: Optional[Dict[str, Any]] = None,
    step2_data: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Build Coordinates payload with IsGeneral flags for Revit BasePointSetting.

    - If step2_data contains DWG coordinates (drawing extraction mode):
      Each DWG candidate coordinate is sent with IsGeneral: 0, allowing the Revit
      plugin to automatically compute spatial matching and pick the best base point.
    - If only step1_data is provided (user specified or previous discipline mode):
      Sent with IsGeneral: 1 as the explicit target base point to apply directly.
    """
    coordinates_list: list[Dict[str, Any]] = []

    if step2_data and step2_data.get("coordinates"):
        dwg_coords = step2_data.get("coordinates", [])
        global_elevation = _clean_str(step2_data.get("Elevation"))
        global_angleton = _clean_str(step2_data.get("Angleton"))
        s1_el = _clean_str(step1_data.get("Elevation")) if step1_data else ""
        s1_an = _clean_str(step1_data.get("Angleton")) if step1_data else ""

        for coord in dwg_coords:
            coordinates_list.append({
                "Northsouth": _clean_str(coord.get("Northsouth")),
                "Eastwest": _clean_str(coord.get("Eastwest")),
                "Elevation": global_elevation or s1_el,
                "Angleton": global_angleton or s1_an,
                "IsGeneral": 0,
            })
    elif step1_data:
        s1_ns = _clean_str(step1_data.get("Northsouth"))
        s1_ew = _clean_str(step1_data.get("Eastwest"))
        s1_el = _clean_str(step1_data.get("Elevation"))
        s1_an = _clean_str(step1_data.get("Angleton"))
        coordinates_list.append({
            "Northsouth": s1_ns,
            "Eastwest": s1_ew,
            "Elevation": s1_el,
            "Angleton": s1_an,
            "IsGeneral": 1,
        })

    return {"Coordinates": coordinates_list}
