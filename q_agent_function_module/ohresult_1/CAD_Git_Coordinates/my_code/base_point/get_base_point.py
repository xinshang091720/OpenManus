import re
import os
import json
import yaml
import math
import requests
from typing import Dict, Any, List, Optional, Tuple
from ohresult.logger_config import setup_logger
logger = setup_logger()

_config_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config.yaml")
with open(_config_path, "r", encoding="utf-8") as f:
    config = yaml.safe_load(f)

get_dwg_text_coordinates_layer = config["revit"]["get_dwg_text_coordinates_layer"]

# 拆分 X/Y 文字互配时允许的最大距离（图形单位），超出视为巧合不配对
PAIR_MAX_DISTANCE = config["get_base_point"]["PAIR_MAX_DISTANCE"]

# MAD 离群剥离阈值（0.6745 * |偏差| / MAD < threshold 视为正常）
MAD_THRESHOLD = config["get_base_point"]["MAD_THRESHOLD"]

# 正则表达式：支持 X=..., X: ..., X(123), X 123 等标注格式
X_PATTERN = re.compile(r'[xX]\s*(?:[:=\s(]|\s+)\s*(-?\d+\.\d+|-?\d+)\s*\)?')
Y_PATTERN = re.compile(r'[yY]\s*(?:[:=\s(]|\s+)\s*(-?\d+\.\d+|-?\d+)\s*\)?')
ELEVATION_PATTERN = re.compile(r'(?:绝对标高|[\?\s\\A1;]{2,}(?:绝对标高)?)(?:相当于|为)?\s*(-?\d+\.\d+|-?\d+)\s*(?:米)?')
ROTATION_PATTERN = re.compile(r'(?:旋转角(?:度)?|北偏东|旋转)\s*(?:相当于|为|[:=\s])?\s*(-?\d+\.\d+|-?\d+)\s*(?:°|度)?')


def clean_cad_text(raw_text: str) -> str:
    """清洗 CAD 文字中的格式控制符"""
    if not raw_text:
        return ""
    cleaned = raw_text
    cleaned = re.sub(r'\\[A-Za-z0-9.]+;', '', cleaned)
    cleaned = re.sub(r'\\[Pp]', ' ', cleaned)
    cleaned = cleaned.replace('{', '').replace('}', '')
    return cleaned.replace('\r', '').replace('\n', '').strip()


def parse_text_coordinates(coord_str: str) -> Optional[Tuple[float, float]]:
    """
    解析 Text_coordinates 字符串 "(东坐标, 北坐标, z)" → (east, north)
    """
    if not coord_str:
        return None
    try:
        s = str(coord_str).strip().strip('()')
        parts = [p.strip() for p in s.split(',') if p.strip() != '']
        if len(parts) < 2:
            return None
        east = float(parts[0])
        north = float(parts[1])
        return (east, north)
    except (ValueError, TypeError):
        return None


def fetch_dwg_texts(dwg_path: str) -> List[Dict[str, Any]]:
    """
    调用 Revit 接口 GetDwgText，获取 DWG 图纸内全部文字信息。
    请求失败或业务异常时返回空列表（不抛异常，不拦截主流程）。
    """
    logger.info(f"调用 GetDwgText 接口获取图纸文字: {dwg_path}")
    try:
        response = requests.post(
            get_dwg_text_coordinates_layer,
            json={"dwgFilePath": dwg_path},
            timeout=60
        )
        if response.status_code != 200:
            logger.error(f"GetDwgText 接口请求失败，HTTP状态码: {response.status_code}")
            return []

        res_json = response.json()
        if res_json.get("code") != 200:
            logger.warning(f"GetDwgText 接口返回业务异常: {res_json}")
            return []

        data = res_json.get("data", [])
        if not isinstance(data, list):
            logger.warning(f"GetDwgText 接口返回的 data 不是列表: {type(data)}")
            return []

        logger.info(f"GetDwgText 接口成功返回 {len(data)} 条文字信息。")
        return data

    except Exception as e:
        logger.error(f"调用 GetDwgText 接口异常: {e}")
        return []


def pair_split_coordinates(found_xs: List[Dict[str, Any]],
                           found_ys: List[Dict[str, Any]],
                           max_distance: float = PAIR_MAX_DISTANCE) -> List[Dict[str, Any]]:
    """
    双向互为最近邻配对：
    只有当某个 X 文字的最近 Y 文字、反过来也把这个 X 认作最近的 X 时，
    才判定为一对（对应图纸中 X/Y 两行一上一下紧邻的真实标注形态）。
    """
    if not found_xs or not found_ys:
        return []

    def _distance_2d(p1: Tuple[float, float], p2: Tuple[float, float]) -> float:
        return math.hypot(p1[0] - p2[0], p1[1] - p2[1])

    # 每个 X 的最近 Y
    best_y_for_x: Dict[int, Tuple[int, float]] = {}
    for i, x_item in enumerate(found_xs):
        best_j, best_d = -1, float('inf')
        for j, y_item in enumerate(found_ys):
            d = _distance_2d(x_item["pos"], y_item["pos"])
            if d < best_d:
                best_d, best_j = d, j
        best_y_for_x[i] = (best_j, best_d)

    # 每个 Y 的最近 X
    best_x_for_y: Dict[int, Tuple[int, float]] = {}
    for j, y_item in enumerate(found_ys):
        best_i, best_d = -1, float('inf')
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
        mutual_i, _ = best_x_for_y.get(j, (-1, float('inf')))
        if mutual_i == i and i not in used_xs and j not in used_ys:
            pairs.append({
                "X": found_xs[i]["val"],
                "Y": found_ys[j]["val"],
                "pos": found_xs[i]["pos"]
            })
            used_xs.add(i)
            used_ys.add(j)
    return pairs


def filter_outliers_mad(paired_list: List[Dict[str, Any]],
                        threshold: float = MAD_THRESHOLD) -> List[Dict[str, Any]]:
    """
    MAD（中位数绝对偏差）离群剥离，剔除明显偏离主群的错配假坐标。
    少于 3 组时不处理，直接原样返回。
    """
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
    """
    从 GetDwgText 返回的文字列表中提取基点信息。
    - 同一条 Text 内同时含 X/Y 标注 → 直接配对，不做距离计算
    - X/Y 分写在两条 Text → 按双向互为最近邻配对（距离用 Text_coordinates）
    - 全文检索"绝对标高"与"旋转角/北偏东"
    """
    found_xs: List[Dict[str, Any]] = []
    found_ys: List[Dict[str, Any]] = []
    paired_coordinates: List[Dict[str, Any]] = []
    absolute_elevation: Optional[float] = None
    rotation_angle: Optional[float] = None

    for item in text_items:
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

        # 情况 1: 同一条文本内同时包含 X 和 Y 标注，直接提取
        if x_match and y_match:
            paired_coordinates.append({
                "X": float(x_match.group(1)),
                "Y": float(y_match.group(1)),
                "pos": pos
            })
        # 情况 2: 单独匹配到 X 或 Y，留待互为最近邻配对
        elif x_match:
            if pos is not None:
                found_xs.append({"val": float(x_match.group(1)), "pos": pos})
        elif y_match:
            if pos is not None:
                found_ys.append({"val": float(y_match.group(1)), "pos": pos})

    if found_xs:
        logger.info(f"分离 X 标注 {len(found_xs)} 条，分离 Y 标注 {len(found_ys)} 条，开始互为最近邻配对。")
    paired_coordinates.extend(pair_split_coordinates(found_xs, found_ys))

    # MAD 离群剥离 + 去重
    paired_coordinates = filter_outliers_mad(paired_coordinates)
    unique_pairs = []
    seen = set()
    for pt in paired_coordinates:
        key = (round(pt["X"], 4), round(pt["Y"], 4))
        if key not in seen:
            seen.add(key)
            unique_pairs.append(pt)

    logger.info(f"共提取到 {len(unique_pairs)} 组有效坐标标注。")

    # 标注值映射：X 标注值 → Northsouth，Y 标注值 → Eastwest（沿用原版映射）
    formatted_coordinates = [
        {
            "Northsouth": str(pt["X"]),
            "Eastwest": str(pt["Y"])
        }
        for pt in unique_pairs
    ]

    return {
        "coordinates": formatted_coordinates,
        "Elevation": absolute_elevation,
        "Angleton": rotation_angle
    }


def get_base_point_from_dwg(dwg_path: str) -> Dict[str, Any]:
    """
    调用 Revit GetDwgText 接口提取 DWG 图纸中的基点数据。
    返回统一的标准格式字典:
    {
        "coordinates": [{"Northsouth": ..., "Eastwest": ...}, ...],
        "Elevation": ... or None,
        "Angleton": ... or None
    }
    任何失败均返回空字典或空 coordinates，不抛异常、不拦截主流程。
    """
    logger.info(f"【步骤 2】开始通过 GetDwgText 接口提取基点数据: {dwg_path}")

    try:
        text_items = fetch_dwg_texts(dwg_path)
        if not text_items:
            logger.warning("GetDwgText 未返回任何文字数据，提取结果为空。")
            return {}

        raw_result = extract_info_from_dwg_texts(text_items)

        coordinates = raw_result.get("coordinates", [])
        elevation = raw_result.get("Elevation")
        angleton = raw_result.get("Angleton")

        extracted_data = {
            "coordinates": coordinates,
            "Elevation": str(elevation) if elevation is not None else None,
            "Angleton": str(angleton) if angleton is not None else None
        }

        logger.info(
            f"DWG 提取完成：{len(coordinates)} 组坐标标注，高程: {elevation}, 旋转角: {angleton}")
        return extracted_data

    except Exception as e:
        logger.error(f"解析 DWG 文字提取基点发生异常: {e}")
        return {}


if __name__ == "__main__":
    test_dwg = r"D:\project\ai_agent\data\dwg\总图.dwg"
    res = get_base_point_from_dwg(test_dwg)
    print(json.dumps(res, indent=2, ensure_ascii=False))
