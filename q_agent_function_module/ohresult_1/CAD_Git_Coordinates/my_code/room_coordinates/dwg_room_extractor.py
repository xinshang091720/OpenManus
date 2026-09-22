import os
import re
import ast
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from openai import OpenAI
from dotenv import load_dotenv
from ohresult.logger_config import setup_logger

logger = setup_logger()

load_dotenv()
OPENAI_API_KEY = os.getenv("QWEN_API_KEY")
BASE_URL = os.getenv("QWEN_OPENAI_URL")
DEFAULT_MODEL = os.getenv("QWEN35_FLASH_MODEL")

client = OpenAI(
    api_key=OPENAI_API_KEY,
    base_url=BASE_URL
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


def strip_parentheses(text: str) -> str:
    """
    去除括号：
    1. 成对括号（中英文）连同其内部内容一并去除，如 "陶艺工坊（50人）" → "陶艺工坊"；
    2. 若去除后文本为空（整段文字都在括号内，如 "（50人）"），
       则只去掉括号字符、保留括号内内容 → "50人"；
    3. 兜底清理：残留的孤立括号字符（不成对的 "（" / "）" / "(" / ")"）
       一律删掉，如 "台分体空调室外机（" → "台分体空调室外机"。
    """
    removed = re.sub(r'[（(].*?[）)]', '', text)
    if removed.strip():
        return re.sub(r'[（）()]', '', removed).strip()
    return re.sub(r'[（）()]', '', text).strip()


def parse_and_clean_element(raw_text: str, layer: str, entity_type: str, pos: tuple) -> dict:
    clean_text = re.sub(r'(\\{\\f[^;]+;|\\P|\\|\{|\})', '', raw_text).strip()

    # 去除括号：成对括号连同内容一并去除；整段都在括号内时仅去括号字符、保留内容；孤立括号一并清掉
    clean_text = strip_parentheses(clean_text)

    # 去除文本两端的特殊符号（非汉字、非字母、非数字），如 【】、-、·、引号等装饰性符号
    clean_text = re.sub(r'^[^\u4e00-\u9fa5A-Za-z0-9]+|[^\u4e00-\u9fa5A-Za-z0-9]+$', '', clean_text).strip()

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
    suffix_pattern = r'.*(室|卧|房|厅|间|卫|男更|女更|办公|窗口|梯|井)$'

    return bool(re.search(suffix_pattern, text_without_brackets))


def judge_room_by_qwen(item: dict) -> tuple:
    text = item['clean_text']

    if len(text) > 20:
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
            extra_body={"enable_thinking": False}
        )
        result = json.loads(response.choices[0].message.content)
        return item, result.get("is_room", False)
    except Exception as e:
        logger.error(f"【Qwen 调用异常】文本: '{text}'，原因: {e}")
        return item, False


def build_pool_from_text_items(text_items: list) -> list:
    """
    将 Revit 接口 GetDwgText 返回的文字条目转为内部统一的 main_pool 结构。

    每项字段:
      - Text:                文字内容
      - Text_coordinates:    坐标字符串 "(x, y, z)"
      - Text_layer:          所在图层
    """
    main_pool = []
    for item in text_items or []:
        raw_text = item.get("Text", "") or ""
        layer = item.get("Text_layer", "") or ""
        pos_str = item.get("Text_coordinates", "") or ""
        pos = (0.0, 0.0, 0.0)
        if pos_str:
            try:
                pos = tuple(ast.literal_eval(pos_str))
            except (ValueError, SyntaxError):
                pos = (0.0, 0.0, 0.0)
        if raw_text and raw_text.strip():
            el = parse_and_clean_element(raw_text, layer, "TEXT", pos)
            main_pool.append(el)
    return main_pool


def extract_rooms_from_pool(main_pool: list, max_workers: int = 10) -> dict:
    """
    共享的房间判定与格式化逻辑（与数据来源无关）:
      1) 收集命中房间名正则的图层，整层中文全部纳入；
      2) 其余图层的中文文本送 LLM 补充判定；
      3) 输出 {"room_texts": [{"RoomName", "XYZ"}]}
    """
    extracted_rooms = []

    # 0. 单字符文本直接删除，不保留：
    #    - 正则前：清理后的 Text 本身仅 1 个字符（如 "门"、"3"）→ 删；
    #    - 正则后：仅提取出 1 个汉字（如 "房"、"室"、"人"，含 "（50人）"→"人"）→ 也删。
    main_pool = [
        item for item in main_pool
        if len(item['clean_text']) > 1 and len(item['clean_chinese']) > 1
    ]

    # 1. 按图层提取策略：收集命中正则的图层中的所有中文实体
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

    # 2. 对其余未命中规则图层的中文文本，送 LLM 大模型进行补充判定
    remaining_chinese_items = [x for x in remaining_pool if x['has_chinese']]

    if remaining_chinese_items:
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = [executor.submit(judge_room_by_qwen, item) for item in remaining_chinese_items]

            for future in as_completed(futures):
                item, is_room = future.result()
                if is_room:
                    extracted_rooms.append(item)

    # 3. 格式化输出
    formatted_room = [
        {
            "RoomName": item["clean_text"],
            "XYZ": str(item["pos"])
        }
        for item in extracted_rooms
    ]

    return {"room_texts": formatted_room}


def process_room_extraction_from_texts(text_items: list, max_workers: int = 10) -> dict:
    """
    新主入口：直接消费 Revit 接口 GetDwgText 返回的 data 列表，
    不再依赖本地 DWG/DXF 解析。返回格式与旧入口完全一致。
    """
    main_pool = build_pool_from_text_items(text_items)
    return extract_rooms_from_pool(main_pool, max_workers)
