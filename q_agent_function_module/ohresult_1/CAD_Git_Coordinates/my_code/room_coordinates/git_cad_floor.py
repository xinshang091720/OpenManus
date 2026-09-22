import os
import re
import json
from typing import List, Dict, Any
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
    base_url=BASE_URL,
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


def judge_dxf_floor_with_qwen(candidate_texts: List[str], dwg_filename: str = None) -> Dict[str, Any]:
    """
    第二步：将整张图纸筛选出的所有候选文本 + 图纸文件名（单独传参）一次性传给 Qwen，做全局分析
    """
    if not candidate_texts and not dwg_filename:
        return {
            "floor_num": 0,
            "matched_text": "",
        }

    # 图纸文件名单独成行展示（不混入文本列表），让模型能明确识别
    if dwg_filename:
        filename_part = f"""【图纸文件名】：{dwg_filename}
注意：【图纸文件名】是判定楼层的最高优先级参考信息：当文件名与下方文本列表中的图纸大标题、图框标题冲突时，以文件名为准。
若最终判定依据是图纸文件名，matched_text 必须返回该文件名原文。
"""
    else:
        filename_part = ""

    prompt = f"""你是一个专业的建筑 CAD 图纸文本分析专家。
下面是【同一张 CAD 图纸】的相关信息：
{filename_part}从图纸中筛选出的所有包含楼层的文本列表：
{json.dumps(candidate_texts, ensure_ascii=False, indent=2)}
请你综合分析上述所有信息，最优先依据【图纸文件名】（如有），其次结合【图纸大标题、图框标题】判断【整张图纸】主要描述的是第几层。
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
6. 必须从输入的信息中（含图纸文件名），找出最关键的那一条原本文字作为 matched_text。
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


def judge_floor_from_texts(raw_texts: List[str], dwg_filename: str = None) -> Dict[str, Any]:
    """
    新入口：直接基于图纸全部文字内容列表判断楼层，不再读取 DXF。
    内部复用正则初筛 + Qwen 判定，返回 {"min_floor", "max_floor", "matched_text"}。

    参数 dwg_filename：上传图纸的文件名（如 "综合楼  二层平面图.dwg"）。
    文件名需通过与图内文字相同的正则初筛（命中楼层关键字）才会单独传入 LLM；
    传入时单独成行展示（不混入文本列表），作为最高优先级参考。
    """
    candidate_texts = filter_candidate_texts(raw_texts)

    # 图纸文件名走同一套正则初筛，命中才单独传入 LLM
    matched_filename = None
    if dwg_filename:
        clean_filename = dwg_filename.strip()
        if clean_filename and filter_candidate_texts([clean_filename]):
            matched_filename = clean_filename

    return judge_dxf_floor_with_qwen(candidate_texts, matched_filename)
