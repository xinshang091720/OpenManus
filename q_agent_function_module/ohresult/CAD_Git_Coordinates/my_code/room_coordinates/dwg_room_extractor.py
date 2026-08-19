import os
import re
import sys
import ezdxf
import json
import time
import tempfile
import win32com.client
from concurrent.futures import ThreadPoolExecutor, as_completed
from openai import OpenAI
from dotenv import load_dotenv
from q_agent_function_module.ohresult.logger_config import setup_logger

logger = setup_logger()

load_dotenv()
OPENAI_API_KEY = (
    os.getenv("BEESYNC_LLM_API_KEY")
    or os.getenv("QWEN_API_KEY")
    or os.getenv("DASHSCOPE_API_KEY")
    or os.getenv("OPENAI_API_KEY")
)
BASE_URL = (
    os.getenv("BEESYNC_LLM_BASE_URL")
    or os.getenv("QWEN_OPENAI_URL")
    or "https://dashscope.aliyuncs.com/compatible-mode/v1"
)
ROOM_TEXT_MODEL = (
    # Room-name classification is a small binary decision.  Prefer an
    # explicitly configured lightweight room-text model, but keep the
    # authenticated Runtime model as a compatibility fallback.
    os.getenv("BEESYNC_ROOM_TEXT_MODEL")
    or os.getenv("QWEN35_FLASH_MODEL")
    or os.getenv("BEESYNC_LLM_MODEL")
    or "qwen-plus"
)
# Retain the historic name for callers that imported it directly.
DEFAULT_MODEL = ROOM_TEXT_MODEL


def _bounded_positive_float(name: str, default: float, maximum: float) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except ValueError:
        return default
    return min(maximum, max(1.0, value))


# This request classifies one short label only.  It must never inherit the SDK
# default ten-minute network wait or delay a whole DWG because one label stalls.
ROOM_TEXT_TIMEOUT_SECONDS = _bounded_positive_float(
    "BEESYNC_ROOM_TEXT_TIMEOUT_SECONDS", default=30.0, maximum=60.0
)

client = (
    OpenAI(
        api_key=OPENAI_API_KEY,
        base_url=BASE_URL,
        timeout=ROOM_TEXT_TIMEOUT_SECONDS,
        max_retries=0,
    )
    if OPENAI_API_KEY and BASE_URL
    else None
)

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

def is_layer_visible(layer_name: str, doc) -> bool:
    """检查指定图层是否可见（未关闭且未冻结）"""
    if layer_name in doc.layers:
        layer = doc.layers.get(layer_name)
        if layer.is_off() or layer.is_frozen():
            return False
    return True

def is_entity_visible(entity, doc) -> bool:
    """判断单个实体自身的隐身标记与图层状态"""
    if entity.dxf.get('invisible', 0) == 1:
        return False
    return is_layer_visible(entity.dxf.layer, doc)

def parse_and_clean_element(raw_text: str, layer: str, entity_type: str, pos: tuple) -> dict:
    clean_text = re.sub(r'(\\{\\f[^;]+;|\\P|\\|\{|\})', '', raw_text).strip()

    # 仅提取汉字
    chinese_chars = re.findall(r'[\u4e00-\u9fa5]', clean_text)
    has_chinese = len(chinese_chars) > 0
    clean_chinese = "".join(chinese_chars) if has_chinese else ""

    return {
        "raw_text": raw_text,
        "clean_text": clean_text,
        "clean_chinese": clean_chinese,
        "has_chinese": has_chinese,
        "layer": layer,
        "type": entity_type,
        "pos": pos
    }

def is_room_name_pattern(raw_text: str) -> bool:
    if not raw_text:
        return False

    # 1. 剔除中英文括号以及括号内部的所有字符
    text_without_brackets = re.sub(r'[（(].*?[）)]', '', raw_text).strip()

    if not text_without_brackets:
        return False

    # 2. 正则匹配指定的结尾关键字
    suffix_pattern = r'.*(室|卧|房|厅|间|卫|男更|女更|办公|窗口)$'

    return bool(re.search(suffix_pattern, text_without_brackets))


def is_obvious_non_room_label(text: str) -> bool:
    """Avoid an LLM request for annotations that cannot be room names."""
    compact = re.sub(r"\s+", "", text or "")
    if len(compact) <= 1 or len(compact) > 20:
        return True
    if compact in {"上", "下", "左", "右", "东", "西", "南", "北"}:
        return True
    if any(marker in compact for marker in ("面积", "平方米", "㎡", "m²", "m2", "标高")):
        return True
    # Sheet titles and detail callouts are annotations, not room names.  Keep
    # this local filter deliberately small; uncertain labels still go through
    # the bounded classifier instead of being silently discarded.
    if any(
        marker in compact
        for marker in ("平面图", "图纸", "大样", "详图", "图例")
    ):
        return True
    if re.fullmatch(r"[+\-]?\d+(?:\.\d+)?", compact):
        return True
    return False


def _parse_room_decision(content: object) -> bool:
    """Accept only an explicit JSON boolean; malformed output is non-room."""
    text = str(content or "").strip()
    try:
        payload = json.loads(text)
    except (TypeError, ValueError, json.JSONDecodeError):
        # Some compatible endpoints wrap otherwise valid JSON in prose.  It is
        # safe to recover only an unambiguous boolean, never a guessed label.
        match = re.search(r'"is_room"\s*:\s*(true|false)', text, flags=re.IGNORECASE)
        if match:
            return match.group(1).casefold() == "true"
        raise ValueError("room classifier did not return a JSON is_room boolean")
    if not isinstance(payload, dict) or not isinstance(payload.get("is_room"), bool):
        raise ValueError("room classifier JSON has no boolean is_room field")
    return payload["is_room"]

def judge_room_by_qwen(item: dict) -> tuple:
    text = item['clean_text']

    if is_obvious_non_room_label(text):
        return item, False
    if client is None or not DEFAULT_MODEL:
        logger.info("未配置 Qwen；保留规则未命中的文本供预览，不发送外部请求")
        return item, False

    try:
        response = client.chat.completions.create(
            model=DEFAULT_MODEL,
            messages=[
                {"role": "system", "content": PROMPT_SYSTEM},
                {"role": "user", "content": f"待判断文本：\"{text}\""}
            ],
            response_format={"type": "json_object"},
            temperature=0.0,
            max_tokens=32,
            # Deliberately force non-thinking mode.  This is a binary label
            # classifier, not a reasoning step.
            extra_body={"enable_thinking": False},
        )
        return item, _parse_room_decision(response.choices[0].message.content)
    except Exception as e:
        logger.error(f"【Qwen 调用异常】文本: '{text}'，原因: {e}")
        return item, False

def extract_entities_from_block(doc, block_def, transform_matrix, main_pool):
    """
    递归遍历块内部（包括嵌套块/块中块），并完全保留文字原生图层
    """
    for sub_entity in block_def:
        if sub_entity.dxf.get('invisible', 0) == 1:
            continue

        # 1. 检查文字原生图层的显隐性（不继承宿主块图层）
        raw_layer = sub_entity.dxf.layer
        if not is_layer_visible(raw_layer, doc):
            continue

        dxftype = sub_entity.dxftype()

        if dxftype in ('TEXT', 'MTEXT'):
            if dxftype == 'TEXT':
                raw_text = sub_entity.dxf.text
                align_enum, align_pt, insert_pt = sub_entity.get_placement()
                pt = align_pt if align_pt is not None else insert_pt
            else:  # MTEXT
                raw_text = sub_entity.text
                pt = sub_entity.dxf.insert

            if raw_text and raw_text.strip():
                world_pt = transform_matrix.transform(pt) if pt else (0.0, 0.0, 0.0)
                pos = (world_pt.x, world_pt.y, world_pt.z) if hasattr(world_pt, 'x') else tuple(world_pt)

                el = parse_and_clean_element(raw_text, raw_layer, dxftype, pos)
                main_pool.append(el)

        elif dxftype == 'ATTRIB':
            raw_text = sub_entity.dxf.text
            pt = sub_entity.dxf.insert
            if raw_text and raw_text.strip():
                world_pt = transform_matrix.transform(pt) if pt else (0.0, 0.0, 0.0)
                pos = (world_pt.x, world_pt.y, world_pt.z) if hasattr(world_pt, 'x') else tuple(world_pt)

                el = parse_and_clean_element(raw_text, raw_layer, 'ATTRIB', pos)
                main_pool.append(el)

        # 2. 处理嵌套块（块中块）
        elif dxftype == 'INSERT':
            nested_block_name = sub_entity.dxf.name
            if nested_block_name in doc.blocks:
                nested_matrix = transform_matrix * sub_entity.matrix44()
                extract_entities_from_block(doc, doc.blocks[nested_block_name], nested_matrix, main_pool)

def process_dwg_room_extraction(input_path: str, max_workers: int = 10) -> dict:
    # This function consumes the private DXF produced by the AutoCAD/Tianzheng
    # converter.  Refusing a source DWG here prevents a future caller from
    # accidentally treating it as temporary and deleting a user drawing.
    if str(input_path).lower().endswith(".dwg"):
        raise ValueError(
            "process_dwg_room_extraction requires a converted .dxf input; "
            "source DWG files are never opened or deleted here"
        )

    main_pool = []
    dxf_path = input_path

    try:
        doc = ezdxf.readfile(dxf_path)
        msp = doc.modelspace()

        # 1. 提取模型空间顶层直接存放的 TEXT 和 MTEXT
        for entity in msp.query('TEXT MTEXT'):
            if not is_entity_visible(entity, doc):
                continue
            dxftype = entity.dxftype()
            layer = entity.dxf.layer

            if dxftype == 'TEXT':
                raw_text = entity.dxf.text
                align_enum, align_pt, insert_pt = entity.get_placement()
                pt = align_pt if align_pt is not None else insert_pt
                pos = (pt.x, pt.y, pt.z) if pt else (0.0, 0.0, 0.0)
            else:
                raw_text = entity.text
                pt = entity.dxf.insert
                pos = (pt.x, pt.y, pt.z) if pt else (0.0, 0.0, 0.0)

            if raw_text and raw_text.strip():
                el = parse_and_clean_element(raw_text, layer, dxftype, pos)
                main_pool.append(el)

        # 2. 提取块参照（INSERT）中的属性文字与内部文本（支持深层嵌套块）
        for insert in msp.query('INSERT'):
            if not is_entity_visible(insert, doc):
                continue

            # 处理块自身的属性文字 (ATTRIB)
            if hasattr(insert, 'attribs'):
                for attrib in insert.attribs:
                    raw_layer = attrib.dxf.layer
                    if not is_layer_visible(raw_layer, doc):
                        continue
                    raw_text = attrib.dxf.text
                    pt = attrib.dxf.insert
                    pos = (pt.x, pt.y, pt.z) if pt else (0.0, 0.0, 0.0)
                    if raw_text and raw_text.strip():
                        el = parse_and_clean_element(raw_text, raw_layer, 'ATTRIB', pos)
                        main_pool.append(el)

            # 递归解析块内部定义
            block_name = insert.dxf.name
            if block_name in doc.blocks:
                block_def = doc.blocks[block_name]
                matrix = insert.matrix44()
                extract_entities_from_block(doc, block_def, matrix, main_pool)

    finally:
        # The DXF is owned and deleted by the caller that created it.  In
        # particular, never clean up an input path in this low-level reader.
        pass

    extracted_rooms = []

    # 3. 按图层提取策略：收集命中正则的图层中的所有中文实体
    target_layers = set()
    for item in main_pool:
        if item['has_chinese'] and is_room_name_pattern(item['clean_chinese']):
            target_layers.add(item['layer'])

    remaining_pool = []
    for item in main_pool:
        if item['layer'] in target_layers:
            if item['has_chinese']:
                extracted_rooms.append(item)
        else:
            remaining_pool.append(item)

    # 4. 对其余未命中规则图层的中文文本，送 LLM 大模型进行补充判定
    remaining_chinese_items = [
        item
        for item in remaining_pool
        if item['has_chinese'] and not is_obvious_non_room_label(item['clean_text'])
    ]

    if remaining_chinese_items:
        # Classification depends only on clean_text.  Ask once for each unique
        # label, then apply the answer to every occurrence in the drawing.
        candidates_by_text = {}
        for item in remaining_chinese_items:
            candidates_by_text.setdefault(item['clean_text'], []).append(item)
        logger.info(
            "DWG room-text classification: unique_labels=%s occurrences=%s "
            "model=%s thinking=false timeout_seconds=%s",
            len(candidates_by_text),
            len(remaining_chinese_items),
            DEFAULT_MODEL,
            ROOM_TEXT_TIMEOUT_SECONDS,
        )
        with ThreadPoolExecutor(max_workers=min(max_workers, len(candidates_by_text))) as executor:
            futures = {
                executor.submit(judge_room_by_qwen, items[0]): clean_text
                for clean_text, items in candidates_by_text.items()
            }

            room_text_decisions = {}
            for future in as_completed(futures):
                clean_text = futures[future]
                _, is_room = future.result()
                room_text_decisions[clean_text] = is_room

        for clean_text, items in candidates_by_text.items():
            if room_text_decisions.get(clean_text, False):
                extracted_rooms.extend(items)

    # 5. 格式化输出
    formatted_room = [
        {
            "RoomName": item["raw_text"],
            "XYZ": str(item["pos"])
        }
        for item in extracted_rooms
    ]

    return {"room_texts": formatted_room}


if __name__ == "__main__":
    input_dwg_file = r"D:\project\ai_agent\data\room_coordinates_test\地下室\dwg\地下三层平面图.dwg"

    if not os.path.exists(input_dwg_file):
        print(f"【错误】未找到文件 '{input_dwg_file}'，请修改 input_dwg_file 为您本地真实的 DWG 文件绝对路径。")
        sys.exit(1)

    print("================ 正在开始抽取房间文本流水线 ================")
    results = process_dwg_room_extraction(input_dwg_file)
    print(json.dumps(results, indent=2, ensure_ascii=False))
