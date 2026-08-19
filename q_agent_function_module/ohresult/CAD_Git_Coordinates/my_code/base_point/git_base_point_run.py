import requests
from .git_base_point import auto_process_user_dwg
from q_agent_function_module.ohresult.logger_config import setup_logger
logger = setup_logger()


def run_pipeline(dwg_path: str):
    # 1. 提取 DWG 坐标数据
    base_point_data = auto_process_user_dwg(dwg_path)

    # 2. 调用 Revit 接口
    url = "http://localhost:5000/api/RevitApi/BasePointSetting"
    response = requests.post(url, json=base_point_data, timeout=15)

    if response.status_code == 200:
        res_json = response.json()
        # 判断业务层面的 code
        if res_json.get("code") == 200:
            logger.info(f"成功: {res_json.get('msg')}")
        else:
            logger.error(f"服务端业务异常: {res_json}")
    else:
        logger.error(f" HTTP请求失败, 状态码: {response.status_code}")


if __name__ == "__main__":
    DWG_PATH = r"D:\project\ai_agent\data\base_point_test\1F建筑平面图.dwg"
    run_pipeline(DWG_PATH)
