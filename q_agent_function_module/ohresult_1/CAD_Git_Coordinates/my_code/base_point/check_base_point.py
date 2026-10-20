import os
import yaml
import requests
from typing import Dict, Any, Tuple
from ohresult.logger_config import setup_logger
logger = setup_logger()

_config_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config.yaml")
with open(_config_path, "r", encoding="utf-8") as f:
    config = yaml.safe_load(f)
_env_revit_base = os.environ.get("BEESYNC_REVIT_API_BASE_URL")
if _env_revit_base:
    verify_datum_mark = f"{_env_revit_base.rstrip('/')}/BasePointSettingIsCorrect"
else:
    verify_datum_mark = config["revit"]["verify_datum_mark"]


def check_base_point() -> Tuple[bool, Dict[str, Any]]:
    """
    调用 Revit 接口验证原始模型基点。
    返回: (is_complete, data_dict)
    - is_complete: 若 Northsouth, Eastwest, Elevation, Angleton 四个字段都不为空则为 True
    """
    logger.info("【步骤 1】正在调用接口验证原始模型基点...")
    try:
        response = requests.post(verify_datum_mark, timeout=15)
        if response.status_code != 200:
            logger.error(f"验证基点接口请求失败，HTTP状态码: {response.status_code}")
            return False, {}

        res_json = response.json()
        if res_json.get("code") != 200:
            logger.warning(f"验证基点接口返回业务异常: {res_json}")
            return False, {}

        data = res_json.get("data", {})

        # 校验 4 个关键字段是否均不为空
        fields = ["Northsouth", "Eastwest", "Elevation", "Angleton"]
        is_complete = all(data.get(f) is not None and str(data.get(f)).strip() != "" for f in fields)

        return is_complete, data

    except Exception as e:
        logger.error(f"验证基点接口异常: {e}")
        return False, {}


if __name__ == "__main__":
    is_valid, data = check_base_point()
    print(f"验证结果完整性: {is_valid}")
    print(f"返回数据: {data}")