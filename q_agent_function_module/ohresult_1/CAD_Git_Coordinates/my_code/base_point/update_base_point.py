import os
import yaml
import json
import requests
from typing import Dict, Any, Optional, Tuple
from ohresult.logger_config import setup_logger
logger = setup_logger()

_config_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config.yaml")
with open(_config_path, "r", encoding="utf-8") as f:
    config = yaml.safe_load(f)
update_datum_mark = config["revit"]["update_datum_mark"]

def _clean_str(val: Any) -> str:
    """辅助函数：安全转换字符串，避免将 None 转成 'None' 字符串"""
    if val is None:
        return ""
    s = str(val).strip()
    return "" if s.lower() == "none" else s

def update_base_point(
        step1_data: Dict[str, Any],
        step2_data: Optional[Dict[str, Any]] = None,
) -> Tuple[bool, Dict[str, Any]]:
    logger.info("开始调用 Revit 接口修改基点")

    coordinates_list = [
        {
            "Northsouth": _clean_str(step1_data.get("Northsouth")),
            "Eastwest": _clean_str(step1_data.get("Eastwest")),
            "Elevation": _clean_str(step1_data.get("Elevation")),
            "Angleton": _clean_str(step1_data.get("Angleton")),
            "IsGeneral": 1  # 无论何时，step1 或单独调用均默认为 1
        }
    ]

    if step2_data:
        dwg_coords = step2_data.get("coordinates", [])
        global_elevation = _clean_str(step2_data.get("Elevation"))
        global_angleton = _clean_str(step2_data.get("Angleton"))

        for coord in dwg_coords:
            coordinates_list.append({
                "Northsouth": _clean_str(coord.get("Northsouth")),
                "Eastwest": _clean_str(coord.get("Eastwest")),
                "Elevation": global_elevation,
                "Angleton": global_angleton,
                "IsGeneral": 0
            })

    payload = {"Coordinates": coordinates_list}
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    print("\n========== 修改数据 ==========")
    print(json.dumps(payload, ensure_ascii=False, indent=4))
    print("===============================================\n")

    try:
        response = requests.post(update_datum_mark, json=payload, timeout=15)
        if response.status_code == 200:
            res_json = response.json()
            if res_json.get("code") == 200:
                returned_data = res_json.get("data", {})
                logger.info("调用修改基点接口 HTTP 及业务成功！")
                return True, returned_data
            else:
                logger.error(f"修改基点服务端业务异常: {res_json}")
                return False, res_json.get("data", {})
        else:
            logger.error(f"修改基点 HTTP 请求失败，状态码: {response.status_code}")
            return False, {}
    except Exception as e:
        logger.error(f"调用修改基点接口异常: {e}")
        return False, {}


if __name__ == "__main__":
    test_data = {
        "Northsouth": "2506045399.00",
        "Eastwest": "507491908.00",
        "Elevation": "73900.00",
        "Angleton": "120"
    }
    success, res_data = update_base_point(test_data)
    print(f"单独调用结果: {success}, 返回数据: {res_data}")