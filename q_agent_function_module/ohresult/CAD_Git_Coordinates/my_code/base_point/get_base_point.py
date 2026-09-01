"""Extract survey/base-point coordinates and elevations from DWG texts returned by Revit."""

from __future__ import annotations

import math
import re
from typing import Any, Dict, List, Optional, Tuple

from app.logger import logger

PAIR_MAX_DISTANCE: float = 20000.0
MAD_THRESHOLD: float = 3.5

X_PATTERN = re.compile(r"[xX]\s*(?:[:=\s(]|\s+)\s*(-?\d+\.\d+|-?\d+)\s*\)?")
Y_PATTERN = re.compile(r"[yY]\s*(?:[:=\s(]|\s+)\s*(-?\d+\.\d+|-?\d+)\s*\)?")
ELEVATION_PATTERN = re.compile(
    r"(?:绝对标高|[\?\s\\A1;]{2,}(?:绝对标高)?)(?:相当于|为)?\s*(-?\d+\.\d+|-?\d+)\s*(?:米)?"
)
ROTATION_PATTERN = re.compile(
    r"(?:旋转角(?:度)?|北偏东|旋转)\s*(?:相当于|为|[:=\s])?\s*(-?\d+\.\d+|-?\d+)\s*(?:°|度)?"
)


def clean_cad_text(raw_text: str) -> str:
    """Clean CAD formatting control characters from text."""
    if not raw_text:
        return ""
    cleaned = raw_text
    cleaned = re.sub(r"\\[A-Za-z0-9.]+;", "", cleaned)
    cleaned = re.sub(r"\\[Pp]", " ", cleaned)
    cleaned = cleaned.replace("{", "").replace("}", "")
    return cleaned.replace("\r", "").replace("\n", "").strip()


def parse_text_coordinates(coord_str: str) -> Optional[Tuple[float, float]]:
    """Parse Text_coordinates string '(east, north, z)' -> (east, north)."""
    if not coord_str:
        return None
    try:
        s = str(coord_str).strip().strip("()")
        parts = [p.strip() for p in s.split(",") if p.strip() != ""]
        if len(parts) < 2:
            return None
        east = float(parts[0])
        north = float(parts[1])
        return (east, north)
    except (ValueError, TypeError):
        return None


def pair_split_coordinates(
    found_xs: List[Dict[str, Any]],
    found_ys: List[Dict[str, Any]],
    max_distance: float = PAIR_MAX_DISTANCE,
) -> List[Dict[str, Any]]:
    """Bidirectional mutual nearest neighbor coordinate pairing."""
    if not found_xs or not found_ys:
        return []

    def _distance_2d(p1: Tuple[float, float], p2: Tuple[float, float]) -> float:
        return math.hypot(p1[0] - p2[0], p1[1] - p2[1])

    best_y_for_x: Dict[int, Tuple[int, float]] = {}
    for i, x_item in enumerate(found_xs):
        best_j, best_d = -1, float("inf")
        for j, y_item in enumerate(found_ys):
            d = _distance_2d(x_item["pos"], y_item["pos"])
            if d < best_d:
                best_d, best_j = d, j
        best_y_for_x[i] = (best_j, best_d)

    best_x_for_y: Dict[int, Tuple[int, float]] = {}
    for j, y_item in enumerate(found_ys):
        best_i, best_d = -1, float("inf")
        for i, x_item in enumerate(found_xs):
            d = _distance_2d(y_item["pos"], x_item["pos"])
            if d < best_d:
                best_d, best_i = d, i
        best_x_for_y[j] = (best_i, best_d)

    pairs: List[Dict[str, Any]] = []
    used_xs, used_ys = set(), set()
    for i, (j, dist) in best_y_for_x.items():
        if j == -1 or dist >= max_distance:
            continue
        mutual_i, _ = best_x_for_y.get(j, (-1, float("inf")))
        if mutual_i == i and i not in used_xs and j not in used_ys:
            pairs.append({
                "X": found_xs[i]["val"],
                "Y": found_ys[j]["val"],
                "pos": found_xs[i]["pos"],
            })
            used_xs.add(i)
            used_ys.add(j)
    return pairs


def filter_outliers_mad(
    paired_list: List[Dict[str, Any]],
    threshold: float = MAD_THRESHOLD,
) -> List[Dict[str, Any]]:
    """Median Absolute Deviation (MAD) outlier filtering for coordinates."""
    if len(paired_list) < 3:
        return paired_list

    def _median(vals: List[float]) -> float:
        s = sorted(vals)
        n = len(s)
        return s[n // 2] if n % 2 == 1 else (s[n // 2 - 1] + s[n // 2]) / 2

    xs = [p["X"] for p in paired_list]
    ys = [p["Y"] for p in paired_list]
    med_x, med_y = _median(xs), _median(ys)

    mad_x = _median([abs(v - med_x) for v in xs]) or 1e-6
    mad_y = _median([abs(v - med_y) for v in ys]) or 1e-6

    cleaned_list = []
    for p in paired_list:
        z_x = 0.6745 * abs(p["X"] - med_x) / mad_x
        z_y = 0.6745 * abs(p["Y"] - med_y) / mad_y
        if z_x < threshold and z_y < threshold:
            cleaned_list.append(p)

    dropped_count = len(paired_list) - len(cleaned_list)
    if dropped_count > 0:
        logger.info(f"[MAD降噪] 动态识别并剥离了 {dropped_count} 个离群异常坐标。")
    return cleaned_list


def extract_info_from_dwg_texts(text_items: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Extract coordinates, elevation, and rotation angle from text list."""
    found_xs: List[Dict[str, Any]] = []
    found_ys: List[Dict[str, Any]] = []
    paired_coordinates: List[Dict[str, Any]] = []
    absolute_elevation: Optional[float] = None
    rotation_angle: Optional[float] = None

    for item in text_items or []:
        cleaned_text = clean_cad_text(item.get("Text", ""))
        if not cleaned_text:
            continue
        pos = parse_text_coordinates(item.get("Text_coordinates", ""))

        if absolute_elevation is None:
            elev_match = ELEVATION_PATTERN.search(cleaned_text)
            if elev_match:
                absolute_elevation = float(elev_match.group(1))

        if rotation_angle is None:
            rot_match = ROTATION_PATTERN.search(cleaned_text)
            if rot_match:
                rotation_angle = float(rot_match.group(1))

        x_match = X_PATTERN.search(cleaned_text)
        y_match = Y_PATTERN.search(cleaned_text)

        if x_match and y_match:
            paired_coordinates.append({
                "X": float(x_match.group(1)),
                "Y": float(y_match.group(1)),
                "pos": pos,
            })
        elif x_match:
            if pos is not None:
                found_xs.append({"val": float(x_match.group(1)), "pos": pos})
        elif y_match:
            if pos is not None:
                found_ys.append({"val": float(y_match.group(1)), "pos": pos})

    if found_xs:
        logger.info(f"分离 X 标注 {len(found_xs)} 条，分离 Y 标注 {len(found_ys)} 条，开始互为最近邻配对。")
    paired_coordinates.extend(pair_split_coordinates(found_xs, found_ys))

    paired_coordinates = filter_outliers_mad(paired_coordinates)
    unique_pairs = []
    seen = set()
    for pt in paired_coordinates:
        key = (round(pt["X"], 4), round(pt["Y"], 4))
        if key not in seen:
            seen.add(key)
            unique_pairs.append(pt)

    logger.info(f"共提取到 {len(unique_pairs)} 组有效坐标标注。")

    formatted_coordinates = [
        {
            "Northsouth": str(pt["X"]),
            "Eastwest": str(pt["Y"]),
        }
        for pt in unique_pairs
    ]

    return {
        "coordinates": formatted_coordinates,
        "Elevation": str(absolute_elevation) if absolute_elevation is not None else None,
        "Angleton": str(rotation_angle) if rotation_angle is not None else None,
    }
