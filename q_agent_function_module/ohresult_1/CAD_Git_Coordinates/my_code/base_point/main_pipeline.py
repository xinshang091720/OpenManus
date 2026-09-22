import json
from typing import Dict, Any, List
from check_base_point import check_base_point
from get_base_point import get_base_point_from_dwg
from update_base_point import update_base_point
from ohresult.logger_config import setup_logger
logger = setup_logger()

# 映射英文字段名到中文，便于友好提示
FIELD_NAME_MAP = {
    "Northsouth": "调整基点坐标北南",
    "Eastwest": "调整基点坐标东西",
    "Elevation": "调整项目高程",
    "Angleton": "修改正北角度"
}


def get_missing_fields(data: Dict[str, Any]) -> List[str]:
    """检查 data 中值为 None 或空字符串的字段，返回缺失字段的中文列表"""
    missing = []
    for eng_field, chn_name in FIELD_NAME_MAP.items():
        val = data.get(eng_field)
        if val is None or str(val).strip() == "" or str(val).strip().lower() == "none":
            missing.append(chn_name)
    return missing


def run_pipeline(dwg_path: str):
    """主逻辑控制"""

    # ----------------------------------------------------
    # 步骤 1: 验证模型原始基点
    # ----------------------------------------------------
    is_step1_complete, step1_data = check_base_point()

    if is_step1_complete:
        return {"status": "SUCCESS", "msg": "原始模型基点完整无误", "data": step1_data}

    # ----------------------------------------------------
    # 步骤 2: 提取 DWG 图纸基点信息（即使不完整也不拦截）
    # ----------------------------------------------------
    logger.info(f"原始模型基点有缺失{json.dumps(step1_data, ensure_ascii=False, indent=4)} \n 开始解析 DWG 图纸")
    step2_data = get_base_point_from_dwg(dwg_path)

    # ----------------------------------------------------
    # 步骤 3: 直接调用修改接口（同时带上 IsGeneral:1 与 IsGeneral:0）
    # ----------------------------------------------------
    logger.info("直接调用修改原始模型基点数据接口")
    api_success, result_data = update_base_point(step1_data=step1_data, step2_data=step2_data)

    if not api_success and not result_data:
        logger.error("修改原始模型基点数据接口调用失败或网络异常！")
        return {"status": "FAILED", "msg": "调用修改基点接口失败"}

    # ----------------------------------------------------
    # 步骤 4: 校验修改接口返回的 data 是否有空字段
    # ----------------------------------------------------
    missing_fields = get_missing_fields(result_data)

    if missing_fields:
        # 有缺失字段，拼接提示信息
        missing_str = "、".join(missing_fields)
        error_msg = f"修改错误，缺少：{missing_str}"

        logger.warning(f"修改结果字段不完整: {error_msg}")

        return {
            "status": "ERROR_INCOMPLETE",
            "msg": error_msg,
            "data": result_data
        }
    else:
        # 字段全覆盖，修改彻底成功
        logger.info("修改基点成功且所有数据完整！")

        return {
            "status": "SUCCESS",
            "msg": "基点修改成功",
            "data": result_data
        }


if __name__ == "__main__":
    USER_DWG_PATH = r"D:\project\ai_agent\data\dwg\总图.dwg"
    run_pipeline(USER_DWG_PATH)