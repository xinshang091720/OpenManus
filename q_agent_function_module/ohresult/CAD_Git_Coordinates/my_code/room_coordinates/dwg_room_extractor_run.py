import asyncio
import json
import os
from typing import Any, Dict, List

import requests

from q_agent_function_module.ohresult.logger_config import setup_logger

from .dwg_room_extractor_main import batch_process_dwg_files

logger = setup_logger()

UPDATE_ROOM_NAME_API_URL = "http://localhost:5000/api/RevitApi/UpdateRoomName"


def send_update_room_name_request(payload: Dict[str, Any], timeout: int = 60) -> bool:
    headers = {"Content-Type": "application/json"}

    try:
        response = requests.post(
            UPDATE_ROOM_NAME_API_URL, json=payload, headers=headers, timeout=timeout
        )

        if response.status_code == 200:
            try:
                resp_json = response.json()
                code = resp_json.get("code")
                msg = resp_json.get("msg", "")

                if code == 200:
                    logger.info(f"🎉 [更新成功] Revit 接口响应: {msg} (code: {code})")
                    return True
                else:
                    logger.error(f"⚠️ [业务处理失败] Code: {code}, Message: {msg}")
                    return False

            except json.JSONDecodeError:
                logger.error(response.text)
                return False
        else:
            logger.error(
                f"❌ [HTTP 请求失败] 状态码: {response.status_code} 错误详情: {response.text}"
            )
            return False

    except requests.exceptions.Timeout:

        logger.error(
            f"❌ [连接超时] 请求超出 {timeout} 秒未响应，请检查 Revit 插件服务是否被阻塞。"
        )

    except requests.exceptions.ConnectionError:

        logger.error(
            "❌ [连接拒绝] 无法连接到 http://localhost:5000，请确认 Revit 后台 API 服务已启动。"
        )

    except requests.exceptions.RequestException as e:

        logger.error(f"❌ [请求异常] 详细信息: {str(e)}")

    return False


async def run_pipeline_and_update(dwg_paths: List[str]):
    valid_dwg_files = [
        path
        for path in dwg_paths
        if os.path.exists(path) and path.lower().endswith(".dwg")
    ]

    if not valid_dwg_files:
        logger.error(
            "❌ [错误] 传入的 DWG 路径列表中没有检测到有效的本地文件，流程已终止。"
        )
        return

    extracted_data = await batch_process_dwg_files(valid_dwg_files)

    data_list = extracted_data.get("RoomData", [])
    if not data_list:
        logger.error("⚠️ [提示] 未提取到任何有效房间数据，跳过 API 推送环节。")
        return

    cleaned_room_data = []
    for item in data_list:
        floor_num = item.get("floor_num")
        room_texts = item.get("room_texts", [])

        # Keep legitimate room labels such as "101诊室" and "A区候诊".
        filtered_room_texts = [
            room for room in room_texts if room.get("RoomName", "").strip()
        ]

        cleaned_room_data.append(
            {"floor_num": floor_num, "room_texts": filtered_room_texts}
        )

    # 覆盖清洗后的 RoomData 数据
    extracted_data["RoomData"] = cleaned_room_data
    print(f"extracted_data:{extracted_data}")

    # 打印准备提交的数据结构预览
    # print(f"\n================ 阶段 2: 准备推送至 Revit (共 {len(data_list)} 层/项数据) ================")
    # print(json.dumps(extracted_data, indent=2, ensure_ascii=False))

    send_update_room_name_request(extracted_data)


if __name__ == "__main__":
    # 配置你的 DWG 文件绝对路径列表
    # test_dwg_files = [
    #     r"D:\project\ai_agent\data\room_coordinates_test\地下室\dwg\地下室顶板平面图.dwg",
    #     r"D:\project\ai_agent\data\room_coordinates_test\地下室\dwg\地下一层平面图.dwg",
    #     r"D:\project\ai_agent\data\room_coordinates_test\地下室\dwg\地下二层平面图.dwg",
    #     r"D:\project\ai_agent\data\room_coordinates_test\地下室\dwg\地下三层平面图.dwg"
    # ]
    test_dwg_files = [r"C:\Users\jly23\Desktop\测试项目-1\1F.dwg"]

    # 运行异步主任务
    asyncio.run(run_pipeline_and_update(test_dwg_files))
