"""Local CAD/Revit delivery operations retained from q_agent_function_module."""

from __future__ import annotations

import asyncio
import importlib.util
import json
import logging
import os
import re
import threading
import time
import zipfile
from pathlib import Path
from typing import Any

from app.mcp.revit_lock import RevitProcessLock
from app.revit.client import RevitApiClient
from app.revit.operations import RevitOperationUnknown, call_revit_operation
from app.revit.save_result import saved_model_name_collision
from app.tool.windows_app import running_revit_processes
from app.tool_progress import set_tool_progress


logger = logging.getLogger(__name__)


_PROFESSION_BY_CODE = {
    "AR": "建筑",
    "A": "建筑",
    "ST": "结构",
    "S": "结构",
    "FS": "结构",
    "SS": "结构",
    "AC": "通风空调",
    "M": "通风空调",
    "PD": "给排水",
    "P": "给排水",
    "EL": "电气",
    "E": "电气",
    "T": "电气",
}


def _configured_delivery_timeout_seconds(default_seconds: int = 86400) -> int:
    raw = os.environ.get("BEESYNC_DELIVERY_TIMEOUT_SECONDS")
    if raw is not None and raw.strip():
        try:
            return int(raw.strip())
        except ValueError:
            pass
    # ponytail: default 86400 (24h) avoids the rigid 7200s ceiling on slow machines.
    return default_seconds


_MAX_OPERATION_TIMEOUT_SECONDS = _configured_delivery_timeout_seconds()
_IFC_INITIAL_SAVE_WAIT_SECONDS = 30
_FILE_STABILITY_INTERVAL_SECONDS = 2.0
_FILE_STABILITY_POLLS = 3


def _extract_base_point_from_texts(text_items: list[dict[str, Any]]) -> dict[str, Any]:
    from q_agent_function_module.ohresult.CAD_Git_Coordinates.my_code.base_point import (
        extract_info_from_dwg_texts,
    )
    return extract_info_from_dwg_texts(text_items)


def _extract_base_point(dwg_path: str) -> dict[str, Any]:
    """Compatibility wrapper extracting base point info."""
    from q_agent_function_module.ohresult.CAD_Git_Coordinates.my_code.base_point import (
        extract_info_from_dwg_texts,
    )
    return extract_info_from_dwg_texts([])


def _close_export_dialog(wait_timeout: int = 8) -> bool:
    from q_agent_function_module.ohresult.CAD_Git_Coordinates.my_code.ifc_function.git_ifc import (
        auto_close_export_dialog,
    )
    return auto_close_export_dialog(wait_timeout=wait_timeout, fallback_enter=False)


def _confirm_export_save_dialog(
    wait_timeout: int, cancel_event: threading.Event
) -> bool:
    """Confirm only Revit's initial IFC save-path dialog during ExportIFC."""
    from q_agent_function_module.ohresult.CAD_Git_Coordinates.my_code.ifc_function.git_ifc import (
        auto_close_export_dialog,
    )

    return auto_close_export_dialog(
        wait_timeout=wait_timeout,
        fallback_enter=False,
        initial_save_only=True,
        cancel_event=cancel_event,
    )


def _new_export_dialog_state(
    target_process_id: int | None = None,
    baseline_window_handles: set[int] | None = None,
    baseline_window_texts: dict[int, tuple[str, ...]] | None = None,
    external_plugin_baseline_window_handles: set[int] | None = None,
):
    from q_agent_function_module.ohresult.CAD_Git_Coordinates.my_code.ifc_function.git_ifc import (
        RevitIfcExportDialogState,
    )

    return RevitIfcExportDialogState(
        target_process_id=target_process_id,
        baseline_window_handles=set(baseline_window_handles or ()),
        baseline_window_texts=dict(baseline_window_texts or {}),
        external_plugin_baseline_window_handles=(
            set(external_plugin_baseline_window_handles)
            if external_plugin_baseline_window_handles is not None
            else None
        ),
    )


def _snapshot_revit_window_handles(process_id: int | None) -> set[int]:
    if process_id is None:
        return set()
    from q_agent_function_module.ohresult.CAD_Git_Coordinates.my_code.ifc_function.git_ifc import (
        snapshot_process_window_handles,
    )

    return snapshot_process_window_handles(process_id)


def _snapshot_revit_window_texts(process_id: int | None) -> dict[int, tuple[str, ...]]:
    if process_id is None:
        return {}
    from q_agent_function_module.ohresult.CAD_Git_Coordinates.my_code.ifc_function.git_ifc import (
        snapshot_process_window_texts,
    )

    return snapshot_process_window_texts(process_id)


def _snapshot_visible_window_handles() -> set[int]:
    """Capture windows before ExportIFC for the converter-helper compatibility path."""
    from q_agent_function_module.ohresult.CAD_Git_Coordinates.my_code.ifc_function.git_ifc import (
        snapshot_visible_window_handles,
    )

    return snapshot_visible_window_handles()


def _monitor_export_dialogs(state: Any, cancel_event: threading.Event) -> Any:
    from q_agent_function_module.ohresult.CAD_Git_Coordinates.my_code.ifc_function.git_ifc import (
        monitor_revit_ifc_export_dialogs,
    )

    return monitor_revit_ifc_export_dialogs(
        state,
        cancel_event,
        initial_wait_seconds=_IFC_INITIAL_SAVE_WAIT_SECONDS,
    )


class ProfessionSelectionRequired(ValueError):
    def __init__(self, message: str, candidates: list[str]):
        self.candidates = candidates
        super().__init__(message)


def _load_sz_ifc_module():
    script = (
        Path(__file__).resolve().parents[2]
        / "q_agent_function_module"
        / "ohresult"
        / "CAD_Git_Coordinates"
        / "my_code"
        / "ifc_function"
        / "ifc_SZ-IFC_to_docx.py"
    )
    spec = importlib.util.spec_from_file_location("q_agent_sz_ifc", script)
    if spec is None or spec.loader is None:
        raise RuntimeError("无法加载 SZ-IFC 检查脚本")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run_sz_ifc_inspection(
    ifc_path: str,
    profession: str,
    output_path: str | None,
    rule_name: str | None,
    timeout_seconds: int,
    cancel_event: threading.Event | None = None,
) -> str | dict[str, Any]:
    module = _load_sz_ifc_module()
    return module.run_sz_ifc_full_inspection(
        ifc_path,
        profession,
        output_path,
        rule_name=rule_name,
        timeout_seconds=timeout_seconds,
        cancel_event=cancel_event,
    )


def prepare_save_folder(folder_path: str, source_model_path: str | None) -> Path:
    target = Path(folder_path)
    if not target.is_absolute():
        raise ValueError("save_folder_path 必须是绝对路径")
    if target.exists() and not target.is_dir():
        raise ValueError("save_folder_path 必须是文件夹")
    target.mkdir(parents=True, exist_ok=True)
    if not source_model_path or not saved_model_name_collision(target, source_model_path):
        return target
    index = 0
    while True:
        candidate = target / ("result" if index == 0 else f"result-{index}")
        try:
            candidate.mkdir()
            return candidate
        except FileExistsError:
            index += 1


def prepare_output_file_path(
    output_path: str, *, sidecar_suffixes: tuple[str, ...] = ()
) -> Path:
    """Validate an output location and choose a non-conflicting filename."""
    requested = Path(output_path)
    if not requested.is_absolute():
        raise ValueError("输出文件路径必须是绝对路径")
    parent = requested.parent
    if parent.exists() and not parent.is_dir():
        raise ValueError(f"输出目录不是文件夹：{parent}")
    parent.mkdir(parents=True, exist_ok=True)
    index = 0
    while True:
        suffix = "" if index == 0 else f"({index})"
        candidate = requested.with_name(f"{requested.stem}{suffix}{requested.suffix}")
        related = [candidate, *(Path(f"{candidate}{item}") for item in sidecar_suffixes)]
        if not any(item.exists() for item in related):
            return candidate
        index += 1


def resolve_sz_ifc_profession(profession: str | None, source_path: str = "") -> str:
    """Resolve an explicit or uniquely inferred profession without filename enforcement."""
    value = (profession or "").strip()
    normalized = value.upper()
    if normalized == "G":
        raise ProfessionSelectionRequired(
            "专业代码 G 可能表示总图或燃气，请选择质检专业。",
            ["总图", "燃气"],
        )
    if normalized in _PROFESSION_BY_CODE:
        return _PROFESSION_BY_CODE[normalized]
    chinese_aliases = {
        "建筑": "建筑",
        "结构": "结构",
        "通风空调": "通风空调",
        "暖通": "通风空调",
        "燃气": "燃气",
        "给排水": "给排水",
        "电气": "电气",
        "智能化": "电气",
        "总图": "总图",
    }
    if value in chinese_aliases:
        return chinese_aliases[value]
    if value:
        return value
    tokens = {
        token.upper()
        for token in re.split(r"[_\-.\s]+", Path(source_path).stem)
        if token
    }
    if "G" in tokens:
        stem = Path(source_path).stem
        if "总图" in stem:
            return "总图"
        if "燃气" in stem:
            return "燃气"
        raise ProfessionSelectionRequired(
            "从文件名识别到代码 G，但它可能表示总图或燃气，请选择质检专业。",
            ["总图", "燃气"],
        )
    inferred = {_PROFESSION_BY_CODE[token] for token in tokens if token in _PROFESSION_BY_CODE}
    if len(inferred) == 1:
        return inferred.pop()
    if len(inferred) > 1:
        raise ProfessionSelectionRequired(
            "文件名中存在多个专业代码，请选择本次 SZ-IFC 质检专业。",
            sorted(inferred),
        )
    raise ProfessionSelectionRequired(
        "未能从文件名唯一识别专业，请选择 SZ-IFC 质检专业。",
        ["建筑", "结构", "通风空调", "燃气", "给排水", "电气", "总图"],
    )


class RevitProjectDelivery:
    """Serialize base-point, IFC export, and external SZ-IFC operations."""

    def __init__(self, client: RevitApiClient | None = None) -> None:
        self.client = client or RevitApiClient()
        self.lock = RevitProcessLock()

    async def _open_model(self, rvt_file_path: str | None) -> None:
        if rvt_file_path is None:
            return
        path = Path(rvt_file_path)
        if path.suffix.lower() != ".rvt" or not path.is_file():
            raise ValueError("rvt_file_path 必须是存在的 .rvt 文件")
        set_tool_progress("revit_open_project_model", f"正在打开 Revit 模型：{path.name}")
        async with self.lock.hold():
            await call_revit_operation(
                self.client, "OpenRevitFile", self.client.open_revit_file, str(path)
            )

    async def _save_if_requested(
        self, save_folder_path: str | None, source_model_path: str | None
    ) -> str | None:
        if not save_folder_path:
            return None
        target = prepare_save_folder(save_folder_path, source_model_path)
        async with self.lock.hold():
            await call_revit_operation(self.client, "SaveAs", self.client.save_as, str(target))
        return str(target)

    async def set_base_point(
        self,
        dwg_path: str | None = None,
        rvt_file_path: str | None = None,
        save_folder_path: str | None = None,
        *,
        north_south: str | float | None = None,
        east_west: str | float | None = None,
        elevation: str | float | None = None,
        angle_to_north: str | float | None = None,
        base_point_coordinates: dict[str, Any] | list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        if dwg_path is not None and (Path(dwg_path).suffix.lower() != ".dwg" or not Path(dwg_path).is_file()):
            raise ValueError("dwg_path 必须是存在的 .dwg 文件")

        has_user_coords = bool(
            base_point_coordinates
            or north_south is not None
            or east_west is not None
            or elevation is not None
            or angle_to_north is not None
        )
        if not dwg_path and not has_user_coords:
            raise ValueError("必须提供 DWG 图纸路径 (dwg_path) 或用户指定的基点坐标参数")

        await self._open_model(rvt_file_path)

        from q_agent_function_module.ohresult.CAD_Git_Coordinates.my_code.base_point import (
            build_base_point_payload,
            extract_info_from_dwg_texts,
            get_missing_fields,
            is_base_point_complete,
        )

        set_tool_progress("revit_set_project_base_point", "正在校验 Revit 模型基准点状态")
        async with self.lock.hold():
            # 步骤 1: 验证模型原始基点
            check_res = await call_revit_operation(
                self.client,
                "BasePointSettingIsCorrect",
                self.client.base_point_setting_is_correct,
            )
            step1_data = check_res.get("data", {}) if isinstance(check_res, dict) else {}
            if not has_user_coords and is_base_point_complete(step1_data):
                saved_to = await self._save_if_requested(save_folder_path, rvt_file_path)
                return {
                    "status": "completed",
                    "coordinate_count": 0,
                    "elevation": step1_data.get("Elevation"),
                    "angle": step1_data.get("Angleton"),
                    "message": "原始模型基点完整无误",
                    "data": step1_data,
                    "saved_to": saved_to,
                }

            # 步骤 2: 提取或组装基点信息
            if has_user_coords:
                coords_dict: dict[str, Any] = {}
                if isinstance(base_point_coordinates, str):
                    try:
                        parsed = json.loads(base_point_coordinates)
                        if isinstance(parsed, dict):
                            coords_dict = parsed
                        elif isinstance(parsed, list) and parsed and isinstance(parsed[0], dict):
                            coords_dict = parsed[0]
                    except Exception:
                        pass
                elif isinstance(base_point_coordinates, dict):
                    coords_dict = base_point_coordinates
                elif isinstance(base_point_coordinates, list) and base_point_coordinates and isinstance(base_point_coordinates[0], dict):
                    coords_dict = base_point_coordinates[0]

                def _extract_val(*keys: str, default_val: Any = None) -> str:
                    for k in keys:
                        if k in coords_dict and coords_dict[k] is not None:
                            v = str(coords_dict[k]).strip()
                            if v and v.lower() != "none":
                                return v
                    if default_val is not None:
                        v = str(default_val).strip()
                        if v and v.lower() != "none":
                            return v
                    return ""

                user_ns = _extract_val("Northsouth", "north_south", "northsouth", "北南", "北", "\u5317\u5357", "\u5317", "X", "x", default_val=north_south)
                user_ew = _extract_val("Eastwest", "east_west", "eastwest", "东西", "东", "\u4e1c\u897f", "\u4e1c", "Y", "y", default_val=east_west)
                user_el = _extract_val("Elevation", "elevation", "高程", "标高", "\u9ad8\u7a0b", "\u6807\u9ad8", "Z", "z", default_val=elevation)
                user_an = _extract_val("Angleton", "angle_to_north", "angleton", "角度", "正北角度", "正北", "\u89d2\u5ea6", "\u6b63\u5317\u89d2\u5ea6", "\u6b63\u5317", default_val=angle_to_north)

                # 用户指定修改基点时，用户坐标直接作为 IsGeneral: 1 目标数据传入
                target_user_data = {
                    "Northsouth": user_ns,
                    "Eastwest": user_ew,
                    "Elevation": user_el,
                    "Angleton": user_an,
                }
                payload = build_base_point_payload(step1_data=target_user_data, step2_data=None)
                step2_data = {"coordinates": [{"Northsouth": user_ns, "Eastwest": user_ew}], "Elevation": user_el, "Angleton": user_an}
            elif dwg_path:
                text_res = await call_revit_operation(
                    self.client,
                    "GetDwgText",
                    self.client.get_dwg_text,
                    str(dwg_path),
                )
                text_items = text_res.get("data", []) if isinstance(text_res, dict) else []
                step2_data = extract_info_from_dwg_texts(text_items)
                payload = build_base_point_payload(step1_data=step1_data, step2_data=step2_data)
            else:
                step2_data = {"coordinates": [], "Elevation": "", "Angleton": ""}
                payload = build_base_point_payload(step1_data=step1_data, step2_data=None)

            # 步骤 3: 调用 BasePointSetting
            set_tool_progress("revit_set_project_base_point", "正在向 Revit 写入新的项目基准点与测量点坐标")
            response = await call_revit_operation(
                self.client,
                "BasePointSetting",
                self.client.base_point_setting,
                payload,
            )
            result_data = response.get("data", {}) if isinstance(response, dict) else {}
            if not result_data and isinstance(response, dict) and response.get("code") == 200:
                # Revit 的 BasePointSetting 成功时返回 {"code": 200, "msg": "成功"}，不携带 data 字段
                # 重新读取模型当前基点验证并获取修改后的实际数据
                try:
                    verify_res = await call_revit_operation(
                        self.client,
                        "BasePointSettingIsCorrect",
                        self.client.base_point_setting_is_correct,
                    )
                    if isinstance(verify_res, dict) and isinstance(verify_res.get("data"), dict) and verify_res["data"]:
                        result_data = verify_res["data"]
                except Exception:
                    pass
                if not result_data and has_user_coords:
                    result_data = {k: v for k, v in target_user_data.items() if v}

            # 步骤 4: 校验修改接口返回字段完整性
            missing_fields = get_missing_fields(result_data)
            if missing_fields:
                missing_str = "、".join(missing_fields)
                msg = f"修改错误，缺少：{missing_str}"
                status = "error_incomplete"
            else:
                msg = response.get("msg", "基点修改成功")
                status = "completed"

        saved_to = await self._save_if_requested(save_folder_path, rvt_file_path)
        return {
            "status": status,
            "coordinate_count": len(step2_data.get("coordinates", [])),
            "elevation": result_data.get("Elevation") or step2_data.get("Elevation"),
            "angle": result_data.get("Angleton") or step2_data.get("Angleton"),
            "message": msg,
            "data": result_data,
            "saved_to": saved_to,
        }

    async def export_ifc(
        self,
        ifc_file_path: str,
        timeout_seconds: int = 7200,
        rvt_file_path: str | None = None,
        recovery_model_path: str | None = None,
    ) -> dict[str, Any]:
        # ponytail: when timeout_seconds is omitted or legacy 7200 default, upgrade to configured maximum (default 86400s).
        if timeout_seconds is None or int(timeout_seconds) == 7200 or int(timeout_seconds) <= 0:
            timeout_seconds = _MAX_OPERATION_TIMEOUT_SECONDS
        else:
            timeout_seconds = (
                min(int(timeout_seconds), _MAX_OPERATION_TIMEOUT_SECONDS)
                if _MAX_OPERATION_TIMEOUT_SECONDS > 0
                else int(timeout_seconds)
            )
        timeout_seconds = max(1, timeout_seconds)
        path = Path(ifc_file_path)
        if not path.is_absolute() or path.suffix.lower() != ".ifc":
            raise ValueError("ifc_file_path 必须以 .ifc 结尾")
        requested_path = path
        # Never delete an earlier delivery.  The plugin receives the selected
        # non-conflicting name and every later check verifies a fresh artifact.
        # Some SZ-IFC plugin releases nevertheless retain the active model's
        # original filename.  Retain that name as an observed-output candidate;
        # it is accepted only when both artifacts changed after this request.
        path = prepare_output_file_path(str(path), sidecar_suffixes=(".xlsx",))
        await self._open_model(rvt_file_path)
        candidate_ifc_paths = self._ifc_candidates(
            path,
            requested_ifc_path=requested_path,
            rvt_file_path=rvt_file_path,
            recovery_model_path=recovery_model_path,
        )
        logger.info(
            "IFC export output paths requested=%s prepared=%s candidates=%s",
            requested_path,
            path,
            [str(candidate) for candidate in candidate_ifc_paths],
        )
        before = {
            artifact: self._file_fingerprint(artifact)
            for candidate in candidate_ifc_paths
            for artifact in (candidate, Path(f"{candidate}.xlsx"))
        }
        processes = running_revit_processes()
        if len(processes) != 1:
            raise RuntimeError(
                "必须且只能有一个 Revit 实例，才能把本次 IFC 导出窗口限定到准确进程。"
            )
        target_process_id = processes[0].pid
        baseline = _snapshot_revit_window_handles(target_process_id)
        baseline_texts = _snapshot_revit_window_texts(target_process_id)
        external_plugin_baseline = _snapshot_visible_window_handles()
        save_dialog_stop = threading.Event()
        dialog_state = _new_export_dialog_state(
            target_process_id,
            baseline,
            baseline_texts,
            external_plugin_baseline,
        )
        save_dialog_task: asyncio.Task[Any] | None = None
        response: dict[str, Any] = {}
        operation_started_at = time.monotonic()
        try:
            async with self.lock.hold():
                save_dialog_task = asyncio.create_task(
                    asyncio.to_thread(
                        _monitor_export_dialogs,
                        dialog_state,
                        save_dialog_stop,
                    )
                )
                await asyncio.sleep(0)
                try:
                    set_tool_progress("revit_export_ifc", f"正在调用 Revit 提交 IFC 导出任务：{path.name}")
                    response = await call_revit_operation(
                        self.client,
                        "ExportIFC",
                        self.client.export_ifc,
                        str(path),
                        timeout_seconds=timeout_seconds,
                    )
                except RevitOperationUnknown:
                    return self._export_timeout_result(path, timeout_seconds)
                found = await self._wait_for_export_delivery(
                    candidates=candidate_ifc_paths,
                    before=before,
                    dialog_state=dialog_state,
                    dialog_task=save_dialog_task,
                    operation_started_at=operation_started_at,
                    timeout_seconds=timeout_seconds,
                    path=path,
                    requested_ifc_path=requested_path,
                    rvt_file_path=rvt_file_path,
                    recovery_model_path=recovery_model_path,
                )
        finally:
            save_dialog_stop.set()
            if save_dialog_task is not None and not save_dialog_task.done():
                try:
                    await asyncio.wait_for(save_dialog_task, timeout=2)
                except (asyncio.TimeoutError, asyncio.CancelledError):
                    save_dialog_task.cancel()

        if found:
            actual_ifc, actual_xlsx = found
            return {
                "status": "completed",
                "ifc_path": str(actual_ifc),
                "xlsx_path": str(actual_xlsx),
                "message": response.get("msg", "IFC 导出完成"),
                "export_attempt": 1,
                "export_session": 1,
            }
        return self._export_timeout_result(path, timeout_seconds)

    @staticmethod
    def _export_timeout_result(path: Path, timeout_seconds: int | None = None) -> dict[str, Any]:
        hours = f"{round(timeout_seconds / 3600, 1):g}小时" if timeout_seconds and timeout_seconds > 0 else "设定"
        return {
            "status": "timed_out_unknown",
            "ifc_path": str(path),
            "message": (
                f"IFC 导出等待已达到{hours}上限或调用方设置的更短上限，"
                "Revit 最终状态未知。"
            ),
            "export_attempt": 1,
            "export_session": 1,
        }

    @staticmethod
    def _file_fingerprint(path: Path) -> tuple[int, int] | None:
        try:
            stat = path.stat()
        except OSError:
            return None
        return stat.st_size, stat.st_mtime_ns

    @classmethod
    def _fresh_ifc_pair(
        cls, candidates: list[Path], before: dict[Path, tuple[int, int] | None]
    ) -> tuple[Path, Path] | None:
        for candidate in candidates:
            sidecar = Path(f"{candidate}.xlsx")
            current_ifc = cls._file_fingerprint(candidate)
            current_xlsx = cls._file_fingerprint(sidecar)
            if current_ifc is None or current_xlsx is None:
                continue
            if current_ifc != before.get(candidate) and current_xlsx != before.get(sidecar):
                return candidate, sidecar
        return None

    @staticmethod
    def _ifc_is_complete(path: Path) -> bool:
        try:
            with path.open("rb") as stream:
                stream.seek(max(0, path.stat().st_size - 4096))
                tail = stream.read().decode("utf-8", errors="ignore")
        except OSError:
            return False
        return "END-ISO-10303-21;" in tail

    @staticmethod
    def _xlsx_is_complete(path: Path) -> bool:
        try:
            with zipfile.ZipFile(path) as workbook:
                return workbook.testzip() is None and "[Content_Types].xml" in workbook.namelist()
        except (OSError, zipfile.BadZipFile):
            return False

    async def _wait_for_export_delivery(
        self,
        *,
        candidates: list[Path],
        before: dict[Path, tuple[int, int] | None],
        dialog_state: Any,
        dialog_task: asyncio.Task[Any],
        operation_started_at: float,
        timeout_seconds: int,
        path: Path | None = None,
        requested_ifc_path: Path | None = None,
        rvt_file_path: str | None = None,
        recovery_model_path: str | None = None,
    ) -> tuple[Path, Path] | None:
        """Wait for final Revit UI completion, then verify the file pair.

        Some plugin builds acknowledge ExportIFC before opening their native UI.
        A missing file may therefore be evaluated only after the dialog lifecycle
        has gone quiet, never immediately after that early acknowledgement.
        """
        # The API request and the native UI/file phases share one total
        # deadline.  A plugin that acknowledges late must not receive a second
        # two-hour file-wait window.
        deadline = operation_started_at + timeout_seconds
        last_pair: tuple[Path, Path] | None = None
        last_fingerprint: tuple[tuple[int, int] | None, tuple[int, int] | None] | None = None
        stable_polls = 0
        next_file_check = 0.0
        waiting_for_final_confirmation_logged = False
        set_tool_progress("revit_export_ifc", "正在等待 IFC 与 Excel 交付物落盘并校验完整性")
        while True:
            if dialog_task.done():
                try:
                    dialog_task.result()
                except asyncio.CancelledError:
                    raise RuntimeError("Revit IFC export dialog monitor was cancelled unexpectedly")
                except Exception as error:
                    raise RuntimeError("Revit IFC export dialog automation failed") from error

            now = time.monotonic()
            if now >= deadline:
                return None

            if now >= next_file_check:
                pair = self._fresh_ifc_pair(candidates, before)
                if not pair and path is not None:
                    # Dynamically discover any candidate locations created during export
                    current_candidates = self._ifc_candidates(
                        path,
                        recovery_model_path=recovery_model_path,
                        requested_ifc_path=requested_ifc_path,
                        rvt_file_path=rvt_file_path,
                    )
                    for cand in current_candidates:
                        if cand not in candidates:
                            candidates.append(cand)
                            if cand not in before:
                                before[cand] = None
                            sidecar = Path(f"{cand}.xlsx")
                            if sidecar not in before:
                                before[sidecar] = None
                    pair = self._fresh_ifc_pair(candidates, before)
                if pair:
                    fingerprint = (
                        self._file_fingerprint(pair[0]),
                        self._file_fingerprint(pair[1]),
                    )
                    if pair == last_pair and fingerprint == last_fingerprint:
                        stable_polls += 1
                    else:
                        last_pair = pair
                        last_fingerprint = fingerprint
                        stable_polls = 1
                    files_ready = (
                        stable_polls >= _FILE_STABILITY_POLLS
                        and self._ifc_is_complete(pair[0])
                        and self._xlsx_is_complete(pair[1])
                    )
                    if files_ready and (
                        dialog_state.final_confirmation_clicked
                        or getattr(dialog_state, "final_confirmation_resolved", False)
                    ):
                        return pair
                    if not waiting_for_final_confirmation_logged:
                        logger.info(
                            "IFC and XLSX are complete at ifc_path=%s xlsx_path=%s; "
                            "waiting for the scoped Revit export confirmation",
                            pair[0],
                            pair[1],
                        )
                        waiting_for_final_confirmation_logged = True
                else:
                    last_pair = None
                    last_fingerprint = None
                    stable_polls = 0
                next_file_check = now + _FILE_STABILITY_INTERVAL_SECONDS

            await asyncio.sleep(min(0.5, max(0.01, deadline - now)))

    @staticmethod
    def _ifc_candidates(
        path: Path,
        recovery_model_path: str | None = None,
        *,
        requested_ifc_path: Path | None = None,
        rvt_file_path: str | None = None,
    ) -> list[Path]:
        # The legacy plugin can write alongside the active assigned RVT rather
        # than the requested folder.  Some releases also ignore a collision
        # suffix selected by this Runtime and retain the original requested
        # filename.  Check both names in explicit locations, but accept only a
        # file changed by this attempt.
        candidate_dirs = [path.parent, path.parent / "ifc-assigned"]
        # Some SZ-IFC plugin releases ignore a requested ``result\\ifc``
        # folder and write beside the active model in the sibling
        # ``result\\ifc-assigned`` folder instead.  This is a bounded
        # compatibility location, not a recursive search: it applies only to
        # an explicitly requested ``ifc`` directory and the fresh-file
        # fingerprint check below still prevents an older delivery being
        # accepted as this export.
        if path.parent.name.casefold() == "ifc":
            candidate_dirs.append(path.parent.parent / "ifc-assigned")
        if path.parent.name.casefold() == "ifc-assigned":
            candidate_dirs.append(path.parent.parent)
        # Revit 调用 ExportIFC 接口时，SZ-IFC 导出功能默认会将 .ifc 和 .ifc.xlsx 保存在当前打开模型的根目录下。
        # 当导出的目标路径位于 result、result-N、ifc-assigned 或 ifc 等中间子目录时，
        # 逐级向上把模型的根目录也加入候选路径中进行监听。
        current = path.parent
        while current.parent != current:
            name = current.name.casefold()
            if name in ("ifc-assigned", "ifc", "result") or name.startswith("result-"):
                current = current.parent
                if current not in candidate_dirs:
                    candidate_dirs.append(current)
            else:
                break
        for model_path in (rvt_file_path, recovery_model_path):
            if model_path:
                rvt_dir = Path(model_path).parent
                for extra in (
                    rvt_dir,
                    rvt_dir / "ifc-assigned",
                    rvt_dir / "result",
                    rvt_dir / "ifc-assigned" / "result",
                    rvt_dir / "result" / "ifc-assigned",
                ):
                    if extra not in candidate_dirs:
                        candidate_dirs.append(extra)
        # 当目标路径是项目根目录，但 Revit 中当前打开的模型位于中间子目录（如 rvt、result、ifc-assigned 等）时，
        # 插件会将交付物保存在模型所在目录或其 result 子目录。将这些常见的交付物和模型子目录也加入候选监听。
        sub_folders = (
            "rvt",
            "result",
            "ifc",
            "ifc-assigned",
            "rvt/result",
            "rvt/ifc-assigned",
            "rvt/ifc-assigned/result",
            "rvt/result/ifc-assigned",
            "result/ifc-assigned",
            "result/ifc",
            "ifc-assigned/result",
        )
        for base in list(candidate_dirs):
            if not base.is_dir():
                continue
            for rel in sub_folders:
                sub = base / Path(rel)
                if sub.is_dir() and sub not in candidate_dirs:
                    candidate_dirs.append(sub)
            if len(base.parts) > 2:
                try:
                    for root, dirs, files in os.walk(base):
                        rel_parts = Path(root).relative_to(base).parts
                        if len(rel_parts) > 3:
                            dirs.clear()
                            continue
                        dirs[:] = [
                            d for d in dirs
                            if not d.startswith(".")
                            and d.lower() not in (
                                "node_modules", "venv", ".git", "__pycache__", ".agents"
                            )
                        ]
                        if any(f.lower().endswith(".rvt") for f in files):
                            r_path = Path(root)
                            if r_path not in candidate_dirs:
                                candidate_dirs.append(r_path)
                            for extra_name in (
                                "ifc-assigned",
                                "result",
                                "ifc-assigned/result",
                                "result/ifc-assigned",
                            ):
                                extra_dir = r_path / Path(extra_name)
                                if extra_dir.is_dir() and extra_dir not in candidate_dirs:
                                    candidate_dirs.append(extra_dir)
                except OSError:
                    pass
        candidate_names = [path.name]
        if requested_ifc_path is not None and requested_ifc_path.name not in candidate_names:
            candidate_names.append(requested_ifc_path.name)
        candidate_ifc_paths: list[Path] = []
        for d in candidate_dirs:
            for name in candidate_names:
                cand_file = d / name
                if cand_file not in candidate_ifc_paths:
                    candidate_ifc_paths.append(cand_file)
        return candidate_ifc_paths

    async def inspect_ifc(
        self,
        ifc_file_path: str,
        profession: str | None = None,
        output_docx_path: str | None = None,
        rule_name: str | None = None,
        timeout_seconds: int = 7200,
    ) -> dict[str, Any]:
        # ponytail: when timeout_seconds is omitted or legacy 7200 default, upgrade to configured maximum (default 86400s).
        if timeout_seconds is None or int(timeout_seconds) == 7200 or int(timeout_seconds) <= 0:
            timeout_seconds = _MAX_OPERATION_TIMEOUT_SECONDS
        else:
            timeout_seconds = (
                min(int(timeout_seconds), _MAX_OPERATION_TIMEOUT_SECONDS)
                if _MAX_OPERATION_TIMEOUT_SECONDS > 0
                else int(timeout_seconds)
            )
        timeout_seconds = max(1, timeout_seconds)
        ifc_path = Path(ifc_file_path)
        if not ifc_path.is_absolute() or not ifc_path.is_file():
            raise ValueError("待检查的 IFC 文件不存在")
        default_report = ifc_path.with_name(f"{ifc_path.stem}_质检报告.docx")
        requested_report = Path(output_docx_path) if output_docx_path else default_report
        if requested_report.suffix.lower() != ".docx":
            requested_report = requested_report / default_report.name
        report_path = prepare_output_file_path(str(requested_report))
        try:
            resolved_profession = resolve_sz_ifc_profession(profession, str(ifc_path))
        except ProfessionSelectionRequired as error:
            return {
                "status": "selection_required",
                "message": str(error),
                "candidates": [
                    {"display_name": item, "display_version": "SZ-IFC 专业", "profession": item}
                    for item in error.candidates
                ],
                "ifc_path": str(ifc_path),
            }
        cancel_event = threading.Event()
        set_tool_progress("revit_inspect_ifc", f"正在执行 SZ-IFC 规范自检（专业：{resolved_profession}）并等待 DOCX 报告生成")
        worker = asyncio.create_task(
            asyncio.to_thread(
                _run_sz_ifc_inspection,
                str(ifc_path),
                resolved_profession,
                str(report_path),
                rule_name,
                timeout_seconds,
                cancel_event,
            )
        )
        try:
            report = await asyncio.shield(worker)
        except asyncio.CancelledError:
            cancel_event.set()
            try:
                await worker
            except Exception:
                pass
            raise
        except TimeoutError as error:
            return {
                "status": "timed_out_unknown",
                "message": str(error),
                "ifc_path": str(ifc_path),
                "report_path": str(report_path),
                "profession": resolved_profession,
            }
        finally:
            cancel_event.set()
        if isinstance(report, dict):
            return report
        delivered_report = Path(report)
        if (
            not delivered_report.is_file()
            or delivered_report.stat().st_size <= 0
            or not self._docx_is_complete(delivered_report)
        ):
            raise RuntimeError(
                "SZ-IFC 未生成 DOCX 报告，不能将质检标记为成功："
                f"{delivered_report}"
            )
        failed_ids: list[str] = []
        try:
            sz_mod = _load_sz_ifc_module()
            extract_fn = getattr(sz_mod, "extract_failed_element_ids_from_docx", None)
            if extract_fn:
                failed_ids = extract_fn(delivered_report)
        except Exception:
            pass

        result: dict[str, Any] = {
            "status": "completed",
            "ifc_path": str(ifc_path),
            "report_path": str(delivered_report),
            "profession": resolved_profession,
        }
        if failed_ids:
            result["failed_element_count"] = len(failed_ids)
            result["failed_element_ids"] = failed_ids[:10]
            result["delivery_status"] = f"已自动在 Revit 中打开一键交付协同修改界面（包含 {len(failed_ids)} 个不合格构件）"
        else:
            result["failed_element_count"] = 0
            result["delivery_status"] = "质检全部通过（100%），无不合格构件"
        return result

    @staticmethod
    def _docx_is_complete(path: Path) -> bool:
        try:
            with zipfile.ZipFile(path) as document:
                return document.testzip() is None and "word/document.xml" in document.namelist()
        except (OSError, zipfile.BadZipFile):
            return False

    def close(self) -> None:
        self.lock.close()
