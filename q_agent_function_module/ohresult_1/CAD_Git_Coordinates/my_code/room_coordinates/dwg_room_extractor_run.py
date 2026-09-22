import os
import re
import asyncio
import json
import yaml
import requests
from typing import List, Dict, Any
from dwg_room_extractor_main import batch_process_dwg_files
from ohresult.logger_config import setup_logger
logger = setup_logger()

_config_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config.yaml")
with open(_config_path, "r", encoding="utf-8") as f:
    config = yaml.safe_load(f)

update_roomName = config["revit"]["update_roomName"]


# 正则表达：匹配所有非中文字符和非英文字母（即包含数字或特殊符号）
PURE_CHINESE_PATTERN = re.compile(r'^[\u4e00-\u9fa5]+$')

def send_update_room_name_request(payload: Dict[str, Any], timeout: int = 60) -> bool:
    headers = {"Content-Type": "application/json"}

    try:
        response = requests.post(
            update_roomName,
            json=payload,
            headers=headers,
            timeout=timeout
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
            logger.error(f"❌ [HTTP 请求失败] 状态码: {response.status_code} 错误详情: {response.text}")
            return False


    except requests.exceptions.Timeout:

        logger.error(f"❌ [连接超时] 请求超出 {timeout} 秒未响应，请检查 Revit 插件服务是否被阻塞。")

    except requests.exceptions.ConnectionError:

        logger.error("❌ [连接拒绝] 无法连接到 http://localhost:5000，请确认 Revit 后台 API 服务已启动。")

    except requests.exceptions.RequestException as e:

        logger.error(f"❌ [请求异常] 详细信息: {str(e)}")

    return False


async def run_pipeline_and_update(dwg_paths: List[str]):
    logger.info(f"################ 主流程启动 ################")

    valid_dwg_files = [
        path for path in dwg_paths
        if os.path.exists(path) and path.lower().endswith(".dwg")
    ]

    if not valid_dwg_files:
        logger.error("❌ [错误] 传入的 DWG 路径列表中没有检测到有效的本地文件，流程已终止。")
        return

    invalid_count = len(dwg_paths) - len(valid_dwg_files)
    logger.info(f"[校验] 有效 {len(valid_dwg_files)}/{len(dwg_paths)} 张 DWG"
                + (f"，跳过 {invalid_count} 个不存在/非DWG文件" if invalid_count else ""))

    logger.info("[阶段A] 开始批量提取房间数据 ...")
    extracted_data = await batch_process_dwg_files(valid_dwg_files)

    # eFileSharingViolation：DWG 文件被占用，批量流程直接终止，返回的是提示字符串
    if isinstance(extracted_data, str):
        logger.error(f"❌ [流程终止] {extracted_data}")
        return

    data_list = extracted_data.get("RoomData", [])
    if not data_list:
        logger.error("⚠️ [提示] 未提取到任何有效房间数据，跳过 API 推送环节。")
        return

    logger.info(f"[阶段A完成] 提取到 {len(data_list)} 条楼层数据")

    # 阶段B: 数据清洗（过滤非纯中文房间名）
    logger.info("[阶段B] 数据清洗: 过滤非纯中文房间名 ...")
    cleaned_room_data = []
    before_total = 0
    after_total = 0
    for item in data_list:
        floor_num = item.get("floor_num")
        room_texts = item.get("room_texts", [])

        before_total += len(room_texts)
        # 过滤掉 RoomName 中包含数字或特殊符号的记录
        filtered_room_texts = [
            room for room in room_texts
            if room.get("RoomName") and PURE_CHINESE_PATTERN.match(room["RoomName"].strip())
        ]
        after_total += len(filtered_room_texts)

        cleaned_room_data.append({
            "floor_num": floor_num,
            "room_texts": filtered_room_texts
        })

    # 覆盖清洗后的 RoomData 数据
    extracted_data["RoomData"] = cleaned_room_data
    logger.info(f"[阶段B完成] 房间数清洗: {before_total} -> {after_total}"
                + (f"（过滤 {before_total - after_total} 条非纯中文）" if before_total != after_total else ""))

    # 阶段C: 推送至 Revit
    logger.info(f"[阶段C] 准备推送至 Revit UpdateRoomName（{len(cleaned_room_data)} 条楼层数据）...")
    # 打印准备提交的数据结构预览
    print(json.dumps(extracted_data, indent=2, ensure_ascii=False))

    success = send_update_room_name_request(extracted_data)
    logger.info(f"################ 主流程结束: 推送 {'成功' if success else '失败'} ################")


if __name__ == "__main__":
    # 配置你的 DWG 文件绝对路径列表
    # test_dwg_files = [
    #     r"D:\project\ai_agent\data\test\教育基地\图纸\综合楼  二层平面图.dwg",
    #     r"D:\project\ai_agent\data\test\教育基地\图纸\综合楼  三层平面图.dwg",
    #     r"D:\project\ai_agent\data\test\教育基地\图纸\综合楼  四层平面图.dwg",
    #     r"D:\project\ai_agent\data\test\教育基地\图纸\综合楼  五层平面图.dwg",
    # ]
    test_dwg_files = [
        r"D:\project\ai_agent\data\test\地下室\测试文件\dwg\地下一层平面图.dwg",
        r"D:\project\ai_agent\data\test\地下室\测试文件\dwg\地下二层平面图.dwg",
        r"D:\project\ai_agent\data\test\地下室\测试文件\dwg\地下三层平面图.dwg",
        r"D:\project\ai_agent\data\test\地下室\测试文件\dwg\地下室顶板平面图.dwg",
    ]
    # test_dwg_files = [
    #     r"D:\project\ai_agent\data\room_coordinates_test\深职院\dwg\分割_JS-D-101 地下二层平时组合平面图_t3.dwg"
    # ]

    # 运行异步主任务
    asyncio.run(run_pipeline_and_update(test_dwg_files))