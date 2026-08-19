import os
import time
import tempfile
import asyncio
import requests
import threading
import pythoncom
import win32com.client
import win32process
from typing import List, Dict, Any, Tuple
from .coordinate_transformation import (
    calculate_segment_translation_vector,
    transform_room_texts,
)
from .dwg_room_extractor import process_dwg_room_extraction
from .git_cad_floor import get_cad_floor_info
from q_agent_function_module.ohresult.logger_config import setup_logger
from app.tool_progress import set_tool_progress
logger = setup_logger()

# 常量定义
GRID_DATA_API_URL = "http://localhost:5000/api/RevitApi/DwgRevitGridData"

autocad_lock = threading.Lock()

_AUTOCAD_BUSY_MARKERS = (
    "-2147418111",  # RPC_E_CALL_REJECTED
    "rpc_e_call_rejected",
    "call was rejected by callee",
    "被呼叫方拒绝接收呼叫",
    "<unknown>.open",
    "open.sendcommand",
    "sendcommand",
    "-2147417846",  # RPC_E_SERVERFAULT
    "-2147467259",  # E_FAIL
    "-2147352567",  # DISP_E_EXCEPTION
    "0x80010108",   # RPC_E_DISCONNECTED
    "0x80010105",   # RPC_E_SERVERFAULT
    "0x800706be",   # RPC_S_CALL_FAILED
    "0x80004005",   # E_FAIL
    "0x80010001",   # RPC_E_CALL_REJECTED
    "rpc_s_",
    "rpc_e_",
    "server execution failed",
    "服务器运行失败",
    "the message filter indicated that the application is busy",
    "消息筛选器显示应用程序忙",
)


def _is_autocad_busy(error: Exception) -> bool:
    return any(marker in str(error).casefold() for marker in _AUTOCAD_BUSY_MARKERS)


def _wait_for_autocad_idle(acad, timeout_seconds: float = 30.0) -> None:
    """Wait for an asynchronous SendCommand sequence without restarting CAD."""
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            if bool(acad.GetAcadState().IsQuiescent):
                return
        except Exception as error:
            # Some AutoCAD/Tianzheng versions do not expose AcadState through
            # automation.  The subsequent operation still has its own busy
            # retry, so do not turn that compatibility gap into a hard error.
            if not _is_autocad_busy(error):
                return
        time.sleep(0.5)
    logger.info("AutoCAD is still processing a command; continuing with guarded retry")


def _call_autocad_with_retry(label: str, callback, acad, attempts: int = 12):
    """Run one COM operation only after the existing CAD session is ready."""
    for attempt in range(attempts):
        try:
            return callback()
        except Exception as error:
            if not _is_autocad_busy(error) or attempt == attempts - 1:
                raise
            delay = min(5.0, 1.0 + attempt * 0.5)
            logger.info(
                "AutoCAD/Tianzheng is busy during %s; waiting %.1fs (%s/%s)",
                label, delay, attempt + 1, attempts,
            )
            time.sleep(delay)
            _wait_for_autocad_idle(acad)


def _get_running_autocad(attempts: int = 12):
    """Attach only to AutoCAD 2020; never bind the room workflow to 2026."""
    for attempt in range(attempts):
        try:
            acad = win32com.client.GetActiveObject("AutoCAD.Application.23.1")
            version = str(getattr(acad, "Version", ""))
            if version and not version.startswith("23.1"):
                raise RuntimeError(f"版本化 COM 返回了非 AutoCAD 2020 会话：{version}")
            expected_pid = os.environ.get("BEESYNC_AUTOCAD_2020_PID")
            hwnd = int(acad.HWND)
            _, actual_pid = win32process.GetWindowThreadProcessId(hwnd)
            if expected_pid and actual_pid != int(expected_pid):
                raise RuntimeError(
                    f"AutoCAD 2020 COM PID {actual_pid} 与已选实例 {expected_pid} 不一致"
                )
            return acad
        except Exception as error:
            if not _is_autocad_busy(error):
                raise RuntimeError(
                    "未检测到可用的 AutoCAD 2020/天正会话。请先启动 AutoCAD 2020，再执行房间工具。"
                ) from error
            delay = min(5.0, 1.0 + attempt * 0.5)
            logger.info(
                "AutoCAD/Tianzheng is busy while attaching; waiting %.1fs (%s/%s)",
                delay, attempt + 1, attempts,
            )
            time.sleep(delay)
    raise RuntimeError("AutoCAD/天正持续忙碌，未能连接到现有 CAD 会话。")


def _open_drawing_with_retry(acad, dwg_absolute: str, attempts: int = 12):
    """Open a drawing after CAD/Tianzheng finishes the previous command."""
    last_error = None
    for attempt in range(attempts):
        try:
            try:
                # Keep the proven AutoCAD 2020/Tianzheng invocation.  Although
                # we never save the source document and always close it with
                # False, passing True here makes this COM implementation return
                # before the document is ready for SetVariable/SendCommand.
                return acad.Documents.Open(dwg_absolute, False)
            except Exception as first_error:
                # Older AutoCAD automation implementations do not accept the
                # ReadOnly argument; retain that compatibility path.
                if not _is_autocad_busy(first_error):
                    return acad.Documents.Open(dwg_absolute)
                raise
        except Exception as error:
            last_error = error
            if not _is_autocad_busy(error) or attempt == attempts - 1:
                raise
            delay = min(5.0, 1.0 + attempt * 0.5)
            logger.info("AutoCAD/Tianzheng is busy while opening %s; waiting %.1fs (%s/%s)",
                        os.path.basename(dwg_absolute), delay, attempt + 1, attempts)
            time.sleep(delay)
            _wait_for_autocad_idle(acad)
    raise last_error  # pragma: no cover - loop always returns or raises


def _convert_dwg_to_dxf_via_autocad(dwg_path: str) -> str:
    with autocad_lock:
        # 3. 子线程必须显式初始化 COM 环境
        pythoncom.CoInitialize()
        dwg_absolute = os.path.abspath(dwg_path)
        temp_dir = tempfile.gettempdir()
        safe_temp_name = f"acad_convert_{int(time.time() * 1000)}.dxf"
        dxf_absolute = os.path.join(temp_dir, safe_temp_name)
        if os.path.exists(dxf_absolute):
            try:
                os.remove(dxf_absolute)
            except Exception:
                pass
        acad = None
        doc = None
        dwg_absolute = dwg_absolute.replace("\\", "/")
        dxf_absolute = dxf_absolute.replace("\\", "/")
        try:
            logger.info(f"调用本地天正/AutoCAD后台dwg转换dxf: {os.path.basename(dwg_path)} ...")
            set_tool_progress("revit_create_and_name_ar_rooms", "正在连接 AutoCAD 2020")
            # CAD starts and loads Tianzheng as one desktop session.  Do not
            # use Dispatch as a fallback: GetActiveObject can also fail while
            # a running CAD is busy, and Dispatch would create another CAD
            # instance that competes for the same COM automation channel.
            acad = _get_running_autocad()
            try:
                acad.preferences.User.DisplayAlerts = False
            except Exception:
                pass
            doc = _open_drawing_with_retry(acad, dwg_absolute)
            # AutoCAD 2020 can report the application quiescent before the
            # opened drawing has a fully usable COM document object.  Retain
            # the established settling delay before issuing document commands.
            time.sleep(2.0)
            set_tool_progress("revit_create_and_name_ar_rooms", "正在等待 CAD 命令完成")
            _wait_for_autocad_idle(acad)
            try:
                _call_autocad_with_retry("setting FILEDIA", lambda: doc.SetVariable("FILEDIA", 0), acad)
                _call_autocad_with_retry("setting EXPERT", lambda: doc.SetVariable("EXPERT", 5), acad)

                # 🔥 天正转布核心逻辑修改：
                # 1. 尝试调用天正导出命令 TSAVEAS 将天正图块/对象转为标准 CAD 实体
                # 2. 调用 _TEXPLODE (天正分解) 拆解天正自定义房间/文字对象（注意：绝非炸碎文字的 TEXPLODE）
                tarch_convert_lisp = '''(progn 
                  (if (c:TSAVEAS) (c:TSAVEAS) 
                    (if (c:TExplode) (c:TExplode (ssget "X")) 
                      (command "_.EXPLODE" (ssget "X") "")
                    )
                  )
                )\n'''
                _call_autocad_with_retry("Tianzheng preprocessing", lambda: doc.SendCommand(tarch_convert_lisp), acad)
                _wait_for_autocad_idle(acad)

                _call_autocad_with_retry("DWG audit", lambda: doc.SendCommand("_audit\n_y\n"), acad)
                _wait_for_autocad_idle(acad)
                _call_autocad_with_retry("DWG purge", lambda: doc.SendCommand("_-purge\n_a\n*\n_n\n"), acad)
                _wait_for_autocad_idle(acad)
                # 解决中文乱码，强选国标字体
                reset_font_lisp = '(vlax-for style (vla-get-textstyles (vla-get-activedocument (vlax-get-acad-object))) (vla-put-bigfontfile style "gbcbig.shx"))\n'
                _call_autocad_with_retry("resetting fonts", lambda: doc.SendCommand(reset_font_lisp), acad)
                _wait_for_autocad_idle(acad)
            except Exception as e:
                if _is_autocad_busy(e):
                    raise
                logger.warning(f"天正预处理命令不可用，将降级导出: {e}")

            acR18_DXF = 37
            set_tool_progress("revit_create_and_name_ar_rooms", "正在生成临时 DXF")
            try:
                _call_autocad_with_retry("DXF export", lambda: doc.SaveAs(dxf_absolute, acR18_DXF), acad)
            except Exception as save_error:
                if _is_autocad_busy(save_error):
                    raise
                cmd = f'_(command "_.dxfout" "{dxf_absolute}" "V" "2018" "16" "")\n'
                _call_autocad_with_retry("DXFOUT fallback", lambda: doc.SendCommand(cmd), acad)
                _wait_for_autocad_idle(acad)
            try:
                _call_autocad_with_retry("restoring FILEDIA", lambda: doc.SetVariable("FILEDIA", 1), acad)
                _call_autocad_with_retry("restoring EXPERT", lambda: doc.SetVariable("EXPERT", 0), acad)
            except Exception:
                pass
            file_complete = False
            for i in range(225):
                if os.path.exists(dxf_absolute) and os.path.getsize(dxf_absolute) > 500:
                    try:
                        with open(dxf_absolute, 'rb') as f:
                            f.seek(-200, os.SEEK_END)
                            tail_bytes = f.read()
                            if b'EOF' in tail_bytes:
                                file_complete = True
                                break
                    except IOError:
                        pass
                time.sleep(0.2)
            if file_complete:
                time.sleep(0.5)
                return dxf_absolute
            raise FileNotFoundError("AutoCAD 转换超时，磁盘未检测到 EOF 闭合标识。")
        except Exception as e:
            raise RuntimeError(f"调用本地 AutoCAD/天正 原生服务链路失败，详细异常: {e}")
        finally:
            try:
                if doc:
                    doc.Close(False)
            except Exception:
                pass
            pythoncom.CoUninitialize()

def fetch_dwg_revit_grid_data(dwg_path: str) -> dict:
    headers = {"Content-Type": "application/json"}
    payload = {"dwgFilePath": dwg_path}
    try:
        response = requests.post(GRID_DATA_API_URL, json=payload, headers=headers, timeout=300)
        if response.status_code == 200:
            return response.json()
        raise RuntimeError(f"获取网格数据失败 [HTTP {response.status_code}]: {response.text}")
    except Exception as e:
        raise RuntimeError(f"请求 DwgRevitGridData 接口异常: {str(e)}")


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

async def run_single_dwg_pipeline(dwg_path: str) -> Dict[str, Any]:
    if not os.path.exists(dwg_path):
        raise FileNotFoundError(f"文件路径不存在: '{dwg_path}'")
    if not dwg_path.lower().endswith(".dwg"):
        raise ValueError(f"文件格式错误（非 DWG 文件）: '{dwg_path}'")

    # print(f"--> [开始处理] 文件: {os.path.basename(dwg_path)}")

    # 1. 集中转换 DWG -> DXF（每张图纸全流程只转换一次）
    dxf_path = await asyncio.to_thread(_convert_dwg_to_dxf_via_autocad, dwg_path)

    try:
        # 2. 三路任务并发执行
        # A路：请求 Revit 轴网接口（使用原始 DWG 路径）
        task_grid_data = asyncio.to_thread(fetch_dwg_revit_grid_data, dwg_path)
        # B路：抽取房间（直接传入已转换好的 dxf_path）
        task_room_data = asyncio.to_thread(process_dwg_room_extraction, dxf_path)
        # C路：获取楼层信息（直接传入已转换好的 dxf_path）
        task_floor_data = asyncio.to_thread(get_cad_floor_info, dxf_path)

        grid_res, room_res, floor_res = await asyncio.gather(
            task_grid_data,
            task_room_data,
            task_floor_data
        )

        # 3. 计算轴网坐标平移量
        rvt_grid = grid_res.get("rvtGrid", {})
        dwg_grid = grid_res.get("dwgGrid", {})

        rvt_begin, rvt_end = parse_grid_points(rvt_grid)
        dwg_begin, dwg_end = parse_grid_points(dwg_grid)

        line_a = (rvt_begin, rvt_end)  # Revit 目标线段
        line_b = (dwg_begin, dwg_end)  # DWG 原始线段

        # print(f"起点line_a：\n{line_a}")
        # print(f"终点line_a：\n{line_b}")

        translation_vector = calculate_segment_translation_vector(line_a, line_b)

        # 4. 进行房间坐标转换
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

        expanded_results = []

        # 4. 根据需求逻辑处理 min_floor 与 max_floor 复制展开
        if min_f == max_f:
            # 相同或者均为0时取其一
            floor_val = int(min_f) if min_f.is_integer() else min_f
            expanded_results.append({
                "floor_num": floor_val,
                "room_texts": room_texts
            })
        else:
            # 区间不相同时，按整数区间逐层展开复制
            start_f = int(min_f)
            end_f = int(max_f)
            # 如果存在小数（例如 屋顶29.5层），则不按整数 step 生成
            if not min_f.is_integer() or not max_f.is_integer():
                expanded_results.append({"floor_num": min_f, "room_texts": room_texts})
                expanded_results.append({"floor_num": max_f, "room_texts": room_texts})
            else:
                for f in range(start_f, end_f + 1):
                    expanded_results.append({
                        "floor_num": f,
                        "room_texts": room_texts
                    })
        # print(f"expanded_results:{expanded_results}")
        # print(f"<-- [处理完成] 文件: {os.path.basename(dwg_path)}")
        return expanded_results

    finally:
        # 5. 集中统一清理临时 DXF 文件
        if os.path.exists(dxf_path):
            try:
                os.remove(dxf_path)
                # print(f"【清理】临时 DXF 文件已销毁: {os.path.basename(dxf_path)}")
            except Exception:
                pass


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

    # print(f"========== 开始并发处理 {len(dwg_paths)} 个 DWG 图纸文件 ==========")

    tasks = [run_single_dwg_pipeline(path) for path in dwg_paths]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    all_flattened_results = []
    failed_count = 0

    for path, res in zip(dwg_paths, results):
        if isinstance(res, Exception):
            logger.error(f"【处理失败】文件: {path}，异常原因: {str(res)}")
            failed_count += 1
        else:
            all_flattened_results.extend(res)

    sorted_results = sorted(
        all_flattened_results,
        key=get_sort_key,
        reverse=False
    )

    # print(f"成功展开条数: {len(sorted_results)} 条, 失败文件数: {failed_count} 个")
    # print({"RoomData": sorted_results})

    return {"RoomData": sorted_results}
