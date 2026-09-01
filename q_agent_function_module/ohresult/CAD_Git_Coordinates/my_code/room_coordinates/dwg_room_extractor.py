"""Extract room names and coordinates from DWG text elements returned by Revit."""

from __future__ import annotations

import ast
import json
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Tuple

from dotenv import load_dotenv
import openai
from openai import OpenAI

from app.logger import logger

load_dotenv()


def _resolve_room_text_model() -> str:
    candidates = (
        os.getenv("BEESYNC_ROOM_TEXT_MODEL"),
        os.getenv("QWEN35_FLASH_MODEL"),
        os.getenv("BEESYNC_LLM_MODEL"),
    )
    for candidate in candidates:
        if candidate and candidate.strip():
            return candidate.strip()
    return "qwen-turbo"


ROOM_TEXT_MODEL = _resolve_room_text_model()


def _init_openai_client() -> OpenAI | None:
    api_key = (os.getenv("BEESYNC_LLM_API_KEY") or os.getenv("QWEN_API_KEY") or "").strip()
    base_url = (os.getenv("BEESYNC_LLM_BASE_URL") or os.getenv("QWEN_OPENAI_URL") or "").strip()
    if not api_key or not base_url:
        return None

    raw_timeout = os.getenv("BEESYNC_ROOM_TEXT_TIMEOUT_SECONDS", "60.0")
    try:
        timeout_val = min(60.0, float(raw_timeout))
    except (ValueError, TypeError):
        timeout_val = 60.0

    try:
        return openai.OpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=timeout_val,
            max_retries=0,
        )
    except Exception as error:
        logger.warning(f"无法初始化 Qwen 客户端: {error}")
        return None


client: OpenAI | None = _init_openai_client()

PROMPT_SYSTEM = """你是一个专业的建筑施工图纸分析专家，负责从 CAD 图纸文本中筛选出“建筑房间名称”或“空间功能区域名称”。

### 任务说明
判断给定的文本是否为建筑图纸中的【房间名称】或【空间功能区域名称】。

### 判定标准
1. 【正向标准 - 属于空间区域/功能标识】（返回 true）：
   - 封闭或半封闭房间：如 感染处理、主任办、卫生间、新风机房、101诊室、病房(2) 等。
   - 交通与过渡空间：如 洁净走廊、清洁走廊、过道、电梯厅、前室 等。
   - 开放/半开放/建筑构架区域：如 庭院上空、采光天井、中庭、采光顶、下沉广场、露台、阳台 等。
   - 功能等候与服务区域：如 滴液等候、候诊区、登记处、分诊台、污洗间、配餐室、茶水间、休息区、等待区 等。

2. 【反向标准 - 严格排除】（返回 false）：
   - 节点大样或图纸标题：如“商业S-LT1大样”、“生活水泵房大样”、“一层平面图”、“大样图”。
   - 构造/材料/施工做法说明：如“内墙变形缝建筑构造”、“距地350范围设置护墙板”、“厚聚氨酯防水”。
   - 边界与线型标注说明：如“上层开口边界投影线”、“防火分区界线”、“吊顶标高+3.000”。
   - 纯部件/设备/管线：如“消火栓”、“配电箱”、“给水管”。
   - 尺寸、标高或纯数字。

### 输出格式
必须输出标准 JSON，格式如下：
{"is_room": true} 或 {"is_room": false}
"""


def is_obvious_non_room_label(text: str) -> bool:
    """Return whether text is an obvious non-room annotation."""
    if not text:
        return True
    cleaned = clean_text_simple(text)
    if len(cleaned) <= 1:
        return True
    if any(keyword in cleaned for keyword in ("平面图", "面积", "标高", "大样", "说明", "构造")):
        return True
    return False


def clean_text_simple(raw_text: str) -> str:
    clean = re.sub(r"(\\{\\f[^;]+;|\\P|\\|\{|\})", "", str(raw_text or "")).strip()
    clean = strip_parentheses(clean)
    return re.sub(r"^[^\u4e00-\u9fa5A-Za-z0-9]+|[^\u4e00-\u9fa5A-Za-z0-9]+$", "", clean).strip()


def strip_parentheses(text: str) -> str:
    """Strip brackets and parenthesized content."""
    removed = re.sub(r"[（(].*?[）)]", "", text)
    if removed.strip():
        return re.sub(r"[（）()]", "", removed).strip()
    return re.sub(r"[（）()]", "", text).strip()


def parse_and_clean_element(
    raw_text: str, layer: str, entity_type: str, pos: Tuple[float, float, float]
) -> dict:
    clean_text = clean_text_simple(raw_text)
    chinese_chars = re.findall(r"[\u4e00-\u9fa5]", clean_text)
    has_chinese = len(chinese_chars) > 0
    clean_chinese = "".join(chinese_chars) if has_chinese else ""

    return {
        "raw_text": raw_text,
        "clean_text": clean_text,
        "clean_chinese": clean_chinese,
        "has_chinese": has_chinese,
        "layer": layer,
        "type": entity_type,
        "pos": pos,
    }


def is_room_name_pattern(raw_text: str) -> bool:
    if not raw_text:
        return False
    text_without_brackets = re.sub(r"[（(].*?[）)]", "", raw_text).strip()
    if not text_without_brackets:
        return False
    suffix_pattern = r".*(室|卧|房|厅|间|卫|男更|女更|办公|窗口|梯|井)$"
    return bool(re.search(suffix_pattern, text_without_brackets))


def judge_room_by_qwen(item: dict) -> Tuple[dict, bool]:
    text = item.get("clean_text") or ""
    if len(text) > 20 or is_obvious_non_room_label(text):
        return item, False

    active_client = client or _init_openai_client()
    if not active_client:
        return item, False

    try:
        response = active_client.chat.completions.create(
            model=ROOM_TEXT_MODEL,
            messages=[
                {"role": "system", "content": PROMPT_SYSTEM},
                {"role": "user", "content": f'待判断文本："{text}"'},
            ],
            response_format={"type": "json_object"},
            temperature=0.0,
            max_tokens=32,
            extra_body={"enable_thinking": False},
        )
        content = response.choices[0].message.content or "{}"
        result = json.loads(content)
        return item, bool(result.get("is_room", False))
    except Exception as e:
        logger.warning(f"【Qwen 调用异常】文本: '{text}'，原因: {e}")
        return item, False


def build_pool_from_text_items(text_items: list) -> list:
    """Convert GetDwgText response items into main_pool elements."""
    main_pool = []
    for item in text_items or []:
        raw_text = item.get("Text", "") or ""
        layer = item.get("Text_layer", "") or ""
        pos_str = item.get("Text_coordinates", "") or ""
        pos = (0.0, 0.0, 0.0)
        if pos_str:
            try:
                parsed = ast.literal_eval(pos_str)
                if isinstance(parsed, (tuple, list)) and len(parsed) >= 3:
                    pos = (float(parsed[0]), float(parsed[1]), float(parsed[2]))
                elif isinstance(parsed, (tuple, list)) and len(parsed) == 2:
                    pos = (float(parsed[0]), float(parsed[1]), 0.0)
            except (ValueError, SyntaxError):
                pos = (0.0, 0.0, 0.0)
        if raw_text and raw_text.strip():
            el = parse_and_clean_element(raw_text, layer, "TEXT", pos)
            main_pool.append(el)
    return main_pool


def extract_rooms_from_pool(main_pool: list, max_workers: int = 10) -> dict:
    """Extract room names from pool using regex layers + LLM assistance."""
    main_pool = [
        item
        for item in main_pool
        if len(item["clean_text"]) > 1 and len(item["clean_chinese"]) > 1
    ]

    target_layers = set()
    for item in main_pool:
        if item["has_chinese"] and is_room_name_pattern(item["clean_chinese"]):
            target_layers.add(item["layer"])

    extracted_rooms = []
    remaining_pool = []
    for item in main_pool:
        if item["layer"] in target_layers:
            if item["has_chinese"] and not is_obvious_non_room_label(item["clean_text"]):
                extracted_rooms.append(item)
        else:
            remaining_pool.append(item)

    remaining_chinese_items = [
        x for x in remaining_pool if x["has_chinese"] and not is_obvious_non_room_label(x["clean_text"])
    ]

    # Deduplicate candidate texts to avoid redundant LLM queries
    unique_candidates: dict[str, dict] = {}
    for item in remaining_chinese_items:
        unique_candidates.setdefault(item["clean_text"], item)

    if unique_candidates:
        workers = max(1, min(max_workers, len(unique_candidates)))
        llm_decisions: dict[str, bool] = {}
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {
                executor.submit(judge_room_by_qwen, item): text
                for text, item in unique_candidates.items()
            }
            for future in as_completed(futures):
                try:
                    item, is_room = future.result()
                    llm_decisions[item["clean_text"]] = is_room
                except Exception as error:
                    logger.warning(f"Room extraction worker error: {error}")

        for item in remaining_chinese_items:
            if llm_decisions.get(item["clean_text"], False):
                extracted_rooms.append(item)

    formatted_room = [
        {
            "RoomName": item["clean_text"],
            "XYZ": str(item["pos"]),
        }
        for item in extracted_rooms
    ]

    return {"room_texts": formatted_room}


def process_room_extraction_from_texts(
    text_items: list, max_workers: int = 10
) -> dict:
    """Extract rooms directly from Revit GetDwgText data."""
    main_pool = build_pool_from_text_items(text_items)
    return extract_rooms_from_pool(main_pool, max_workers)


def process_dwg_room_extraction(dxf_path: str, max_workers: int = 10) -> dict:
    """Compatibility wrapper that can extract from a DXF file if ezdxf is present."""
    if dxf_path.lower().endswith(".dwg"):
        raise ValueError("Source drawing was not converted to a temporary DXF: expected a converted .dxf")
    try:
        import ezdxf

        doc = ezdxf.readfile(dxf_path)
        msp = doc.modelspace()
        text_items = []
        for entity in msp:
            if entity.dxftype() in ("TEXT", "MTEXT"):
                raw_text = entity.plain_text() if hasattr(entity, "plain_text") else entity.dxf.text
                pos = getattr(entity.dxf, "insert", (0, 0, 0))
                layer = getattr(entity.dxf, "layer", "0")
                text_items.append({
                    "Text": raw_text,
                    "Text_coordinates": f"({pos[0]}, {pos[1]}, {pos[2] if len(pos) > 2 else 0.0})",
                    "Text_layer": layer,
                })
        return process_room_extraction_from_texts(text_items, max_workers=max_workers)
    except Exception as error:
        logger.warning(f"DXF fallback parsing failed: {error}")
        return {"room_texts": []}
