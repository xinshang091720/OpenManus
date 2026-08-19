import os
import re
import ezdxf
import json
from typing import List, Dict, Any
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
DEFAULT_MODEL = (
    os.getenv("BEESYNC_FLOOR_TITLE_MODEL")
    or os.getenv("BEESYNC_ROOM_TEXT_MODEL")
    or os.getenv("QWEN35_FLASH_MODEL")
    or os.getenv("BEESYNC_LLM_MODEL")
    or "qwen-plus"
)


def _bounded_positive_float(name: str, default: float, maximum: float) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except ValueError:
        return default
    return min(maximum, max(1.0, value))


# Floor-title analysis is a read-only fallback after deterministic title
# parsing.  It must not inherit the SDK's ten-minute default timeout: on a
# timeout the caller asks the user before any Revit write instead.
FLOOR_TITLE_TIMEOUT_SECONDS = _bounded_positive_float(
    "BEESYNC_FLOOR_TITLE_TIMEOUT_SECONDS", default=30.0, maximum=60.0
)

client = (
    OpenAI(
        api_key=OPENAI_API_KEY,
        base_url=BASE_URL,
        timeout=FLOOR_TITLE_TIMEOUT_SECONDS,
        max_retries=0,
    )
    if OPENAI_API_KEY and BASE_URL
    else None
)


def filter_candidate_texts(texts: List[str]) -> List[str]:
    num = r'[0-9一二三四五六七八九十半]'
    special_roof = r"(?:屋面|屋顶|天面|顶层|避难层|顶板|地下室顶板|地下室屋顶|地下屋面)"

    pattern_str = rf"(?:^(?=.*{num}).*?(?:{num}+\s*[FfBb层楼]|[FfBb层楼]\s*{num}+|平面图|示意图))|.*?{special_roof}"
    pattern = re.compile(pattern_str, re.IGNORECASE)

    candidates = []
    for text in texts:
        if not text or not isinstance(text, str):
            continue
        # 清理 AutoCAD 内部格式代码 (如 \A1; \fSimSun; 等转义)
        clean_text = re.sub(r'\\[a-zA-Z0-9]+;|\{[^{}]*\}', '', text).strip()

        if pattern.search(clean_text):
            candidates.append(clean_text)

    return list(set(candidates))


def judge_dxf_floor_with_qwen(candidate_texts: List[str]) -> Dict[str, Any]:
    """第二步：将整张图纸筛选出的所有候选文本一次性传给 Qwen，做全局分析"""
    if not candidate_texts:
        return {
            "floor_num": 0,
            "matched_text": "",
        }
    if client is None or not DEFAULT_MODEL:
        logger.info("未配置 Qwen，无法从图框文本判断楼层")
        return {"min_floor": 0.0, "max_floor": 0.0, "matched_text": ""}

    prompt = f"""你是一个专业的建筑 CAD 图纸文本分析专家。
下面是从【同一张 CAD 图纸】中筛选出的所有包含楼层/图纸名称相关关键字的文本列表：
{json.dumps(candidate_texts, ensure_ascii=False, indent=2)}
请你综合分析上述所有文本，优先根据【图纸大标题、图框标题】判断【整张图纸】主要描述的是第几层。
【判定与层号 (floor_num) 映射规则】：
1. 优先排除梁柱等结构构件编号（如 B10, BY3109b 等），聚焦于图纸标题（如"六～二十九层平面图"、"1~5层平面图"等）。
2. 提取起始楼层 min_floor 和结束楼层 max_floor（均为 float 浮点数类型）：
   - 普通地上层（如"一层平面图"、"15F"）：min_floor = 1.0, max_floor = 1.0
   - 标准层/区间（如"六～二十九层"）：min_floor = 6.0, max_floor = 29.0
   - 地下层（如"地下一层~地下三层"）：min_floor = -3.0, max_floor = -1.0
   - 半地下层：统一用 -0.5 表示（min_floor = -0.5, max_floor = -0.5）
3. 【屋顶、屋面、地下室顶板等特殊概念的泛化匹配规则】（忽略多字、少字、修饰词，基于核心语义判定）：
   a) 带有明确楼层数字的屋顶/屋面：
      * 包含"屋顶"或"机房"或"塔楼"（如"29F屋顶平面图"、"30层电梯机房平面图"）：在基础数字上 +0.8，即 min_floor = 29.8, max_floor = 29.8
      * 包含"屋面"（如"29F屋面平面图"）：在基础数字上 +0.5，即 min_floor = 29.5, max_floor = 29.5
   b) 无明确数字的顶部概念（不管前后是否有修饰词，如"主楼屋顶平面图"、"塔楼屋顶"、"机房层"等）：
      * 核心词为"屋顶"：统一返回 min_floor = 1000.8, max_floor = 1000.8
      * 核心词为"屋面"：统一返回 min_floor = 1000.5, max_floor = 1000.5
   c) 无明确数字的地下室顶部概念（不管前后是否有修饰词，如"地下室顶板平面图"、"地下室屋顶"、"地库顶板"等）：
      * 统一返回 min_floor = -0.8, max_floor = -0.8
4. 无法判断或者没有明确的层号时：min_floor = 0.0, max_floor = 0.0
5. 输入的列表中如果有对“战时”层的描述时优先级最高：min_floor = 0.0, max_floor = 0.0
6. 必须从输入的列表中，找出最关键的那一条原本文字作为 matched_text。
请严格返回 JSON 格式（不要包含任何 Markdown 标识符或额外文本）：
{{
    "matched_text": "匹配到的最主要的原本文本",
    "min_floor": 最低楼层数值(float),
    "max_floor": 最高楼层数值(float),
}}
"""

    try:
        response = client.chat.completions.create(
            model=DEFAULT_MODEL,
            messages=[
                {"role": "system", "content": "你是一个严格输出 JSON 格式的建筑图纸解析助手。"},
                {"role": "user", "content": prompt}
            ],
            temperature=0.0,
            max_tokens=128,
            response_format={"type": "json_object"},
            extra_body={"enable_thinking": False}
        )

        result_str = response.choices[0].message.content.strip()
        result_data = json.loads(result_str)
        return result_data

    except Exception as e:
        logger.error(f"调用 Qwen 大模型失败: {e}")
        return {
            "floor_num": 0,
            "matched_text": "",
        }


def extract_all_texts_from_dxf(dxf_path: str) -> List[str]:
    try:
        doc = ezdxf.readfile(dxf_path)
    except Exception as e:
        logger.error(f"读取 DXF 失败: {e}")
        return []

    msp = doc.modelspace()
    all_texts = []

    for e in msp.query('TEXT'):
        if e.dxf.text:
            all_texts.append(e.dxf.text)

    for e in msp.query('MTEXT'):
        if e.text:
            all_texts.append(e.text)

    for insert in msp.query('INSERT'):
        for attrib in insert.attribs:
            if attrib.dxf.text:
                all_texts.append(attrib.dxf.text)

    return all_texts


def get_cad_floor_info(dwg_path: str) -> Dict[str, Any]:
    raw_texts = extract_all_texts_from_dxf(dwg_path)
    candidate_texts = filter_candidate_texts(raw_texts)
    analysis_result = judge_dxf_floor_with_qwen(candidate_texts)
    return analysis_result
