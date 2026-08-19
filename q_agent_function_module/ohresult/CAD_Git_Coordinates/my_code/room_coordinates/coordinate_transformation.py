import ast
import math
from typing import Any, Dict, List, Sequence, Tuple

Point = Sequence[float]


def validate_translation_alignment(
    revit_line: Tuple[Point, Point],
    dwg_line: Tuple[Point, Point],
    *,
    max_angle_degrees: float = 1.0,
    max_relative_length_error: float = 0.01,
    allow_length_mismatch: bool = False,
) -> Dict[str, Any]:
    """Validate the no-rotation/no-scaling contract before applying a translation."""
    r_start, r_end = revit_line
    d_start, d_end = dwg_line
    r_vector = (float(r_end[0]) - float(r_start[0]), float(r_end[1]) - float(r_start[1]))
    d_vector = (float(d_end[0]) - float(d_start[0]), float(d_end[1]) - float(d_start[1]))
    r_length, d_length = math.hypot(*r_vector), math.hypot(*d_vector)
    if not r_length or not d_length:
        raise ValueError("轴网线段长度必须大于零")

    cosine = max(-1.0, min(1.0, (r_vector[0] * d_vector[0] + r_vector[1] * d_vector[1]) / (r_length * d_length)))
    # Reversed endpoints describe the same axis and remain valid for translation.
    angle_degrees = math.degrees(math.acos(abs(cosine)))
    length_ratio = r_length / d_length
    relative_length_error = abs(r_length - d_length) / max(r_length, d_length)
    length_mismatch = relative_length_error > max_relative_length_error
    valid = angle_degrees <= max_angle_degrees and (allow_length_mismatch or not length_mismatch)
    return {
        "valid": valid,
        "angle_degrees": angle_degrees,
        "length_ratio": length_ratio,
        "relative_length_error": relative_length_error,
        "reason": (
            None
            if valid
            else "轴网存在旋转或比例差异，当前流程仅支持平移"
        ),
        "warning": (
            "轴网长度不一致；已按用户确认的旧逻辑，仅使用中点平移。"
            if valid and length_mismatch
            else None
        ),
    }

def _get_midpoint(p1: Point, p2: Point) -> Tuple[float, ...]:
    if len(p1) != len(p2):
        raise ValueError("线段起点和终点的维度必须一致！")
    return tuple((a + b) / 2.0 for a, b in zip(p1, p2))


def calculate_segment_translation_vector(
    line_a: Tuple[Point, Point], line_b: Tuple[Point, Point]
) -> Tuple[float, ...]:

    start_a, end_a = line_a
    start_b, end_b = line_b

    # 1. 分别求出两条线段的中点
    mid_a = _get_midpoint(start_a, end_a)
    mid_b = _get_midpoint(start_b, end_b)

    # 2. 计算 Mid_B 到 Mid_A 的平移向量
    translation_vector = (mid_a[0] - mid_b[0], mid_a[1] - mid_b[1], 0.0)
    return translation_vector


def transform_room_texts(
    data: Dict[str, List[Dict[str, str]]],
    translation_vector: Tuple[float, ...],
) -> Dict[str, List[Dict[str, str]]]:

    transformed_data = {"room_texts": []}

    for item in data.get("room_texts", []):
        room_name = item.get("RoomName", "")
        xyz_str = item.get("XYZ", "")

        try:
            x, y, z = ast.literal_eval(xyz_str)
        except (ValueError, SyntaxError) as e:
            raise ValueError(f"坐标格式解析失败: {xyz_str}") from e

        # 应用带方向的平移量
        delta_x = translation_vector[0] if len(translation_vector) > 0 else 0.0
        delta_y = translation_vector[1] if len(translation_vector) > 1 else 0.0
        # delta_z = translation_vector[2] if len(translation_vector) > 2 else 0.0
        delta_z = 0.0

        new_x = x + delta_x
        new_y = y + delta_y
        new_z = z + delta_z

        new_xyz_str = f"({new_x}, {new_y}, {new_z})"

        transformed_data["room_texts"].append(
            {"RoomName": room_name, "XYZ": new_xyz_str}
        )

    return transformed_data
