import os
import asyncio
import math
import yaml
import requests
from typing import List, Dict, Any, Tuple
from coordinate_transformation import (
    calculate_segment_translation_vector,
    transform_room_texts,
)
from dwg_room_extractor import process_room_extraction_from_texts
from git_cad_floor import judge_floor_from_texts
from ohresult.logger_config import setup_logger
logger = setup_logger()

_config_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config.yaml")
with open(_config_path, "r", encoding="utf-8") as f:
    config = yaml.safe_load(f)

get_dwg_text_coordinates_layer = config["revit"]["get_dwg_text_coordinates_layer"]
get_rvtdwgGriddata = config["revit"]["get_rvtdwgGriddata"]

# ---- 重试配置 ----
RETRY_TIMES = config["room_coordinates"]["RETRY_TIMES"]         # 最多重试 2 次（每项最多尝试 3 遍）
RETRY_BASE_DELAY = config["room_coordinates"]["RETRY_BASE_DELAY"]     # 指数退避基础间隔（秒）：3s → 9s → 27s

llm_room_name_max_workers = config["room_coordinates"]["room_name_llm_max_workers"]     # 模型判断判断房间名称最大并发数


class FileSharingViolationError(Exception):
    """
    Revit 接口返回 eFileSharingViolation（DWG 文件共享冲突）：
    图纸正被 AutoCAD 或其他程序以任何方式打开，Revit 无法读取。
    此类错误与瞬时网络抖动不同，重试无意义，必须终止整个流程并提示用户关闭图纸。
    """


def fetch_dwg_revit_grid_data(dwg_path: str) -> dict:
    fname = os.path.basename(dwg_path)
    logger.info(f"  [轴网接口] 正在请求 DwgRevitGridData: {fname}")
    headers = {"Content-Type": "application/json"}
    payload = {"dwgFilePath": dwg_path}
    try:
        response = requests.post(get_rvtdwgGriddata, json=payload, headers=headers, timeout=300)

        # eFileSharingViolation：DWG 文件共享冲突（图纸被 AutoCAD 等程序以任何方式打开），
        # 重试无意义，直接终止整个流程并提示用户关闭图纸
        if "eFileSharingViolation" in response.text:
            logger.error(f"  [轴网接口] {fname} 触发 eFileSharingViolation（DWG 文件被占用）→ 终止整个流程")
            raise FileSharingViolationError("关闭任何方式打开的图纸")

        if response.status_code == 200:
            logger.info(f"  [轴网接口] 返回成功: {fname}")
            return response.json()
        raise RuntimeError(f"获取网格数据失败 [HTTP {response.status_code}]: {response.text}")
    except FileSharingViolationError:
        raise
    except Exception as e:
        raise RuntimeError(f"请求 DwgRevitGridData 接口异常: {str(e)}")


def fetch_dwg_text(dwg_path: str) -> dict:
    """
    请求 Revit 接口 GetDwgText 获取整张 DWG 的全部文字信息。
    返回结构: {"code": 200, "msg": "成功",
                "data": [{"Text", "Text_coordinates", "Text_layer"}, ...]}
    """
    fname = os.path.basename(dwg_path)
    logger.info(f"  [文字接口] 正在请求 GetDwgText: {fname}")
    headers = {"Content-Type": "application/json"}
    payload = {"dwgFilePath": dwg_path}
    try:
        response = requests.post(get_dwg_text_coordinates_layer, json=payload, headers=headers, timeout=300)

        # eFileSharingViolation：DWG 文件共享冲突（图纸被 AutoCAD 等程序以任何方式打开），
        # 重试无意义，直接终止整个流程并提示用户关闭图纸
        if "eFileSharingViolation" in response.text:
            logger.error(f"  [文字接口] {fname} 触发 eFileSharingViolation（DWG 文件被占用）→ 终止整个流程")
            raise FileSharingViolationError("关闭任何方式打开的图纸")

        if response.status_code == 200:
            data = response.json()
            count = len(data.get("data", [])) if isinstance(data, dict) else 0
            logger.info(f"  [文字接口] 返回成功: {fname}，共 {count} 条文字")
            return data
        raise RuntimeError(f"获取图纸文字失败 [HTTP {response.status_code}]: {response.text}")
    except FileSharingViolationError:
        raise
    except Exception as e:
        raise RuntimeError(f"请求 GetDwgText 接口异常: {str(e)}")


# ---- 接口并发保护 ----
# 规则：所有 Revit 接口请求一律串行（单图内先轴网后文字，批量层逐张请求）。
# 信号量保留作为双保险：即使未来调整调用方式，同一接口也不会出现并发在途请求。
grid_semaphore = asyncio.Semaphore(1)   # 轴网接口 DwgRevitGridData
text_semaphore = asyncio.Semaphore(1)   # 文字接口 GetDwgText

async def fetch_grid_data_with_lock(dwg_path: str) -> dict:
    """带信号量的轴网接口请求：同一时刻全局最多 1 个在途请求。"""
    async with grid_semaphore:
        return await asyncio.to_thread(fetch_dwg_revit_grid_data, dwg_path)

async def fetch_dwg_text_with_lock(dwg_path: str) -> dict:
    """带信号量的文字接口请求：同一时刻全局最多 1 个在途请求。"""
    async with text_semaphore:
        return await asyncio.to_thread(fetch_dwg_text, dwg_path)


async def _retry_async(func, *args, retries: int = RETRY_TIMES,
                       base_delay: float = RETRY_BASE_DELAY,
                       label: str = "", **kwargs):
    """
    通用指数退避重试包装：间隔 = base_delay * (3 ** attempt) → 3s、9s、27s。
    每次失败后 sleep 再试；重试耗尽后抛出最后一次异常（由上层记日志跳过）。
    """
    last_exc = None
    for attempt in range(retries + 1):
        try:
            return await func(*args, **kwargs)
        except FileSharingViolationError:
            # eFileSharingViolation（DWG 被占用）：重试无意义，直接穿透终止整个流程
            raise
        except Exception as e:
            last_exc = e
            if attempt < retries:
                delay = base_delay * (3 ** attempt)
                logger.warning(f"{label}失败（第 {attempt + 1} 次尝试），{delay:.0f}s 后重试: {str(e)}")
                await asyncio.sleep(delay)
    raise last_exc


def parse_grid_points(grid_node: dict) -> Tuple[Tuple[float, ...], Tuple[float, ...]]:
    if not grid_node:
        raise ValueError("轴网数据节点为空")

    begin_str = grid_node.get("Begin_Position")or []
    end_str = grid_node.get("End_Position")or []

    if len(begin_str) < 2 or len(end_str) < 2:
        raise ValueError("轴网坐标点维度不足")

    begin_pt = tuple(float(x) for x in begin_str)
    end_pt = tuple(float(x) for x in end_str)
    return begin_pt, end_pt

async def _request_dwg_data_once(dwg_path: str) -> Tuple[dict, dict]:
    """单次请求轴网 + 文字（所有 Revit 接口请求全部串行：先轴网，后文字）。失败抛异常，由上层重试。"""
    fname = os.path.basename(dwg_path)
    logger.info(f"[{fname}] [请求阶段] 串行请求轴网接口 → 文字接口（Revit 接口一律不并发）...")
    grid_res = await fetch_grid_data_with_lock(dwg_path)
    text_res = await fetch_dwg_text_with_lock(dwg_path)

    text_items = text_res.get("data", []) if isinstance(text_res, dict) else []
    logger.info(f"[{fname}] [请求完成] 轴网 + 文字接口均返回（{len(text_items)} 条文字）")
    return grid_res, text_res


async def request_dwg_data(dwg_path: str) -> Tuple[dict, dict]:
    """
    请求段（串行瓶颈）：串行请求轴网接口 → GetDwgText，返回 (grid_res, text_res)。
    所有 Revit 接口请求一律串行（单图内先轴网后文字，批量层逐张请求）。
    失败自动重试（简单版：整段两个接口一起重试，指数退避 3s → 9s）。
    """
    if not os.path.exists(dwg_path):
        raise FileNotFoundError(f"文件路径不存在: '{dwg_path}'")
    if not dwg_path.lower().endswith(".dwg"):
        raise ValueError(f"文件格式错误（非 DWG 文件）: '{dwg_path}'")

    fname = os.path.basename(dwg_path)
    return await _retry_async(
        _request_dwg_data_once, dwg_path,
        label=f"[{fname}] 接口请求",
    )


async def process_dwg_data(dwg_path: str, grid_res: dict, text_res: dict) -> List[Dict[str, Any]]:
    """
    处理段（可并行）：房间抽取 + 楼层判断 + 平移量计算 + 坐标转换 + 楼层展开。
    仅使用请求段拿到的数据，不发起任何 Revit 接口请求。
    """
    fname = os.path.basename(dwg_path)

    # 2. 从 GetDwgText 返回的文字列表中解析房间与楼层
    #    房间抽取 / 楼层判断都涉及 Qwen 判定（可能超时/限流），套指数退避重试
    text_items = text_res.get("data", []) if isinstance(text_res, dict) else []
    logger.info(f"[{fname}] [处理阶段] 房间抽取 + 楼层判断（基于 {len(text_items)} 条文字）...")

    async def _extract_room_and_floor():
        #    房间抽取：复用正则命中 + Qwen 补充判定
        task_room_data = asyncio.to_thread(process_room_extraction_from_texts, text_items, max_workers=llm_room_name_max_workers)
        #    楼层判断：复用正则初筛 + Qwen 判定；图纸文件名不做正则、直接并入候选送 LLM
        task_floor_data = asyncio.to_thread(
            judge_floor_from_texts,
            [item.get("Text", "") for item in text_items],
            fname
        )
        return await asyncio.gather(task_room_data, task_floor_data)

    room_res, floor_res = await _retry_async(
        _extract_room_and_floor,
        label=f"[{fname}] 房间/楼层判定",
    )

    # 3. 计算轴网坐标平移量（仍使用原 DwgRevitGridData 接口数据）
    logger.info(f"[{fname}] [处理阶段] 计算轴网坐标平移量 ...")
    rvt_grid = grid_res.get("rvtGrid", {})
    dwg_grid = grid_res.get("dwgGrid", {})

    rvt_begin, rvt_end = parse_grid_points(rvt_grid)
    dwg_begin, dwg_end = parse_grid_points(dwg_grid)

    line_a = (rvt_begin, rvt_end)  # Revit 目标线段
    line_b = (dwg_begin, dwg_end)  # DWG 原始线段

    translation_vector = calculate_segment_translation_vector(line_a, line_b)
    logger.info(f"[{fname}] 平移量: {translation_vector}")

    # 4. 进行房间坐标转换
    logger.info(f"[{fname}] [处理阶段] 进行房间坐标转换 ...")
    transformed_rooms = transform_room_texts(room_res, translation_vector)

    room_texts = []
    if isinstance(transformed_rooms, dict):
        room_texts = transformed_rooms.get("room_texts", [])
    elif isinstance(transformed_rooms, list):
        room_texts = transformed_rooms

    min_f = 0.0
    max_f = 0.0
    if isinstance(floor_res, dict):
        min_f = float(floor_res.get("min_floor", 0.0))
        max_f = float(floor_res.get("max_floor", 0.0))

    logger.info(f"[{fname}] 房间抽取 {len(room_texts)} 个, 楼层范围: {min_f} ~ {max_f}")

    # 5. 根据需求逻辑处理 min_floor 与 max_floor 复制展开
    logger.info(f"[{fname}] [处理阶段] 按楼层区间展开复制 ...")
    expanded_results = []
    if min_f == max_f:
        # 相同或者均为0时取其一
        floor_val = int(min_f) if min_f.is_integer() else min_f
        expanded_results.append({
            "floor_num": floor_val,
            "room_texts": room_texts
        })
    else:
        # 区间不相同时，按整数区间逐层展开复制
        start_f = math.ceil(min_f)   # 小数下界向上取整（如 5.5 → 整数层从 6 开始）
        end_f = math.floor(max_f)    # 小数上界向下取整（如 29.5 → 整数层到 29 为止）
        for f in range(start_f, end_f + 1):
            expanded_results.append({
                "floor_num": f,
                "room_texts": room_texts
            })
        # 小数端点单独补一层（例如 min=6.0, max=29.5：整数层 6~29 之外再补 29.5 层）
        if not min_f.is_integer():
            expanded_results.append({"floor_num": min_f, "room_texts": room_texts})
        if not max_f.is_integer():
            expanded_results.append({"floor_num": max_f, "room_texts": room_texts})

    logger.info(f"[{fname}] [处理完成] 展开为 {len(expanded_results)} 层, 共 {len(room_texts)} 个房间")
    return expanded_results


def get_sort_key(item: Dict[str, Any]) -> float:
    num = item.get("floor_num", 0)
    try:
        val = float(num)
        if val == 0.0:
            return float('inf')
        return val
    except (ValueError, TypeError):
        return float('inf')


async def batch_process_dwg_files(dwg_paths: List[str]) -> List[Dict[str, Any]]:
    if not dwg_paths:
        logger.error("【警告】传入的 DWG 文件路径列表为空！")
        return []

    logger.info(f"========== 开始流水线处理 {len(dwg_paths)} 张 DWG 图纸（接口请求串行 + 本地处理并行） ==========")
    for i, p in enumerate(dwg_paths, 1):
        logger.info(f"  [{i}/{len(dwg_paths)}] {os.path.basename(p)}")

    # 阶段一：请求串行——逐张 await 请求段，拿到结果后立即提交处理任务（后台并行），不等处理完就请求下一张
    processing_tasks = []  # [(path, asyncio.Task)]
    failed_count = 0

    for i, path in enumerate(dwg_paths, 1):
        logger.info(f"----> [{i}/{len(dwg_paths)}] 请求接口: {os.path.basename(path)}")
        try:
            grid_res, text_res = await request_dwg_data(path)
            logger.info(f"      [{i}/{len(dwg_paths)}] 接口返回，本地处理已提交后台: {os.path.basename(path)}")
            processing_tasks.append(
                (path, asyncio.create_task(process_dwg_data(path, grid_res, text_res)))
            )
        except FileSharingViolationError as e:
            # eFileSharingViolation：DWG 文件被占用（共享冲突），重试无意义，
            # 直接结束整个流程：取消已提交的后台处理任务，不再处理后续图纸
            logger.error(f"【流程终止】{e}（{os.path.basename(path)} 触发文件共享冲突，已取消 {len(processing_tasks)} 个待处理任务）")
            for _, task in processing_tasks:
                task.cancel()
            if processing_tasks:
                await asyncio.gather(*(t for _, t in processing_tasks), return_exceptions=True)
            return str(e)
        except Exception as e:
            logger.error(f"【请求失败】文件: {os.path.basename(path)}，异常原因: {str(e)}")
            failed_count += 1

    # 阶段二：统一收集所有后台处理任务的结果（各张图处理并行执行，互不阻塞）
    all_flattened_results = []
    for path, task in processing_tasks:
        try:
            res = await task
            all_flattened_results.extend(res)
        except Exception as e:
            logger.error(f"【处理失败】文件: {os.path.basename(path)}，异常原因: {str(e)}")
            failed_count += 1

    sorted_results = sorted(
        all_flattened_results,
        key=get_sort_key,
        reverse=False
    )

    success_count = len(dwg_paths) - failed_count
    logger.info(f"========== 批量处理完成: 成功 {success_count} 张, 失败 {failed_count} 张, "
                f"共展开 {len(sorted_results)} 条楼层数据 ==========")

    return {"RoomData": sorted_results}
