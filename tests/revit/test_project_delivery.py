import asyncio
import importlib
import json
import threading
import time
import zipfile
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.revit.project_delivery import (
    ProfessionSelectionRequired,
    RevitProjectDelivery,
    _confirm_export_save_dialog,
    prepare_output_file_path,
    resolve_sz_ifc_profession,
)
from app.revit.operations import RevitOperationUnknown


class DeliveryClient:
    def __init__(self, export_path: Path | None = None):
        self.base_point_payloads = []
        self.export_paths = []
        self.export_path = export_path

    async def base_point_setting(self, payload):
        self.base_point_payloads.append(payload)
        return {"code": 200, "msg": "base point updated"}

    async def open_revit_file(self, path):
        self.opened = path
        return {"code": 200, "msg": "opened"}

    async def save_as(self, folder):
        self.saved = folder
        return {"code": 200, "msg": "saved"}

    async def export_ifc(self, path):
        self.export_paths.append(path)
        if self.export_path:
            _write_ifc_pair(self.export_path)
        return {"code": 200, "msg": "export started"}


class NoopRevitLock:
    """Keep path-selection tests independent from a live desktop Runtime mutex."""

    @asynccontextmanager
    async def hold(self):
        yield

    def close(self):
        pass


def _write_ifc_pair(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("ISO-10303-21;\nEND-ISO-10303-21;\n", encoding="utf-8")
    with zipfile.ZipFile(Path(f"{path}.xlsx"), "w") as workbook:
        workbook.writestr("[Content_Types].xml", "<Types />")


def _write_docx(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as document:
        document.writestr("[Content_Types].xml", "<Types />")
        document.writestr("word/document.xml", "<document />")


@pytest.fixture(autouse=True)
def no_real_export_wait(monkeypatch):
    # Unit tests must not contend with a user's live desktop Revit task.
    monkeypatch.setattr("app.revit.project_delivery.RevitProcessLock", NoopRevitLock)
    monkeypatch.setattr("app.revit.project_delivery._FILE_STABILITY_INTERVAL_SECONDS", 0)
    monkeypatch.setattr("app.revit.project_delivery._FILE_STABILITY_POLLS", 1)
    monkeypatch.setattr(
        "app.revit.project_delivery.running_revit_processes",
        lambda: [SimpleNamespace(pid=2468)],
    )
    monkeypatch.setattr(
        "app.revit.project_delivery._snapshot_revit_window_handles", lambda _pid: set()
    )
    monkeypatch.setattr(
        "app.revit.project_delivery._snapshot_revit_window_texts", lambda _pid: {}
    )
    monkeypatch.setattr(
        "app.revit.project_delivery._snapshot_visible_window_handles", lambda: set()
    )
    # Unit tests exercise the orchestration, not the real desktop. Individual
    # tests override this stub when they need to assert dialog behavior.
    def completed_dialog_lifecycle(state, _cancel_event):
        state.final_confirmation_clicked = True
        state.final_confirmation_at = time.monotonic()
        return state

    monkeypatch.setattr(
        "app.revit.project_delivery._monitor_export_dialogs",
        completed_dialog_lifecycle,
    )


def test_export_ifc_uses_explicit_new_path_and_waits_for_both_deliverables(tmp_path, monkeypatch):
    target = tmp_path / "B2.ifc"
    client = DeliveryClient(target)
    workflow = RevitProjectDelivery(client)
    monkeypatch.setattr("app.revit.project_delivery._close_export_dialog", lambda: True)
    try:
        result = asyncio.run(workflow.export_ifc(str(target), timeout_seconds=1))
    finally:
        workflow.close()
    assert client.export_paths == [str(target)]
    assert result["ifc_path"] == str(target)
    assert result["xlsx_path"] == f"{target}.xlsx"


def test_export_ifc_confirms_save_dialog_before_the_plugin_returns(tmp_path, monkeypatch):
    target = tmp_path / "B2.ifc"
    save_confirmed = threading.Event()
    sequence = []

    class SaveBlockedClient(DeliveryClient):
        async def export_ifc(self, path):
            self.export_paths.append(path)
            await asyncio.to_thread(save_confirmed.wait, 1)
            assert save_confirmed.is_set(), "ExportIFC must wait for save confirmation"
            sequence.append("export-returned")
            _write_ifc_pair(Path(path))
            return {"code": 200, "msg": "export started"}

    def confirm_save_dialog(state, cancel_event):
        assert not cancel_event.is_set()
        sequence.append("save-confirmed")
        save_confirmed.set()
        state.initial_save_confirmed = True
        state.final_confirmation_clicked = True
        state.final_confirmation_at = time.monotonic()
        return state

    monkeypatch.setattr(
        "app.revit.project_delivery._monitor_export_dialogs", confirm_save_dialog
    )
    monkeypatch.setattr("app.revit.project_delivery._close_export_dialog", lambda *_: True)
    workflow = RevitProjectDelivery(SaveBlockedClient())
    try:
        result = asyncio.run(workflow.export_ifc(str(target)))
    finally:
        workflow.close()

    assert result["ifc_path"] == str(target)
    assert sequence == ["save-confirmed", "export-returned"]


def test_export_ifc_does_not_retry_when_api_returns_before_native_dialog_finishes(
    tmp_path, monkeypatch
):
    target = tmp_path / "B2.ifc"

    class EarlyResponseClient(DeliveryClient):
        async def export_ifc(self, path):
            self.export_paths.append(path)
            return {"code": 200, "msg": "accepted"}

    def delayed_native_lifecycle(state, _cancel_event):
        state.matching_dialog_visible = True
        time.sleep(0.02)
        state.initial_save_confirmed = True
        _write_ifc_pair(target)
        state.matching_dialog_visible = False
        state.final_confirmation_clicked = True
        state.final_confirmation_at = time.monotonic()
        return state

    monkeypatch.setattr(
        "app.revit.project_delivery._monitor_export_dialogs",
        delayed_native_lifecycle,
    )
    workflow = RevitProjectDelivery(EarlyResponseClient())
    try:
        result = asyncio.run(workflow.export_ifc(str(target), timeout_seconds=1))
    finally:
        workflow.close()

    assert result["ifc_path"] == str(target)
    assert workflow.client.export_paths == [str(target)]


def test_export_ifc_waits_for_final_confirmation_after_files_are_complete(
    tmp_path, monkeypatch
):
    target = tmp_path / "B2.ifc"
    allow_confirmation = threading.Event()

    class EarlyFilesClient(DeliveryClient):
        async def export_ifc(self, path):
            self.export_paths.append(path)
            _write_ifc_pair(Path(path))
            return {"code": 200, "msg": "accepted"}

    def delayed_confirmation(state, _cancel_event):
        allow_confirmation.wait(1)
        state.final_confirmation_clicked = True
        state.final_confirmation_resolved = True
        state.final_confirmation_at = time.monotonic()
        return state

    monkeypatch.setattr(
        "app.revit.project_delivery._monitor_export_dialogs", delayed_confirmation
    )
    workflow = RevitProjectDelivery(EarlyFilesClient())

    async def scenario():
        task = asyncio.create_task(workflow.export_ifc(str(target), timeout_seconds=1))
        await asyncio.sleep(0.02)
        assert not task.done()
        allow_confirmation.set()
        return await task

    try:
        result = asyncio.run(scenario())
    finally:
        workflow.close()
    assert result["status"] == "completed"


def test_export_ifc_stops_lifecycle_monitor_after_delivery(
    tmp_path, monkeypatch
):
    target = tmp_path / "B2.ifc"
    monitor_stopped = threading.Event()

    class CompletedClient(DeliveryClient):
        async def export_ifc(self, path):
            _write_ifc_pair(Path(path))
            return {"code": 200, "msg": "export started"}

    def monitor_until_cancel(state, cancel_event):
        state.final_confirmation_clicked = True
        state.final_confirmation_at = time.monotonic()
        cancel_event.wait(1)
        monitor_stopped.set()
        return state

    monkeypatch.setattr(
        "app.revit.project_delivery._monitor_export_dialogs",
        monitor_until_cancel,
    )
    workflow = RevitProjectDelivery(CompletedClient())
    try:
        result = asyncio.run(workflow.export_ifc(str(target)))
    finally:
        workflow.close()

    assert result["ifc_path"] == str(target)
    assert monitor_stopped.is_set()


def test_initial_save_confirmation_uses_the_restricted_dialog_mode(tmp_path, monkeypatch):
    captured = {}

    def close_dialog(**kwargs):
        captured.update(kwargs)
        return True

    monkeypatch.setattr(
        "q_agent_function_module.ohresult.CAD_Git_Coordinates.my_code.ifc_function.git_ifc.auto_close_export_dialog",
        close_dialog,
    )
    cancel_event = threading.Event()
    assert _confirm_export_save_dialog(12, cancel_event) is True
    assert captured["initial_save_only"] is True
    assert captured["fallback_enter"] is False
    assert captured["cancel_event"] is cancel_event


def test_initial_save_confirmation_clicks_only_the_targeted_revit_dialog(monkeypatch):
    module = importlib.import_module(
        "q_agent_function_module.ohresult.CAD_Git_Coordinates.my_code.ifc_function.git_ifc"
    )
    clicks = []

    monkeypatch.setattr(module.win32gui, "EnumWindows", lambda callback, extra: callback(100, extra))
    monkeypatch.setattr(module.win32gui, "IsWindowVisible", lambda hwnd: True)
    monkeypatch.setattr(
        module.win32gui,
        "GetWindowText",
        lambda hwnd: "导出IFC" if hwnd == 100 else "保存" if hwnd == 200 else "",
    )
    monkeypatch.setattr(
        module.win32gui,
        "GetClassName",
        lambda hwnd: "#32770" if hwnd == 100 else "Button",
    )
    monkeypatch.setattr(
        module.win32gui,
        "EnumChildWindows",
        lambda hwnd, callback, extra: callback(200, extra),
    )
    monkeypatch.setattr(
        module.win32gui,
        "SendMessage",
        lambda hwnd, message, *_: clicks.append((hwnd, message)),
    )
    monkeypatch.setattr(module.win32gui, "ShowWindow", lambda *_: None)
    monkeypatch.setattr(module.win32gui, "SetForegroundWindow", lambda *_: None)
    monkeypatch.setattr(module.time, "sleep", lambda *_: None)
    monkeypatch.setattr(module, "_press_enter_fallback", lambda: pytest.fail("must not use global Enter"))

    assert module.auto_close_export_dialog(wait_timeout=1, initial_save_only=True) is True
    assert clicks and clicks[0][0] == 200


def test_initial_save_confirmation_never_uses_global_enter_when_not_found(monkeypatch):
    module = importlib.import_module(
        "q_agent_function_module.ohresult.CAD_Git_Coordinates.my_code.ifc_function.git_ifc"
    )
    presses = []
    ticks = iter((0, 0, 1))

    monkeypatch.setattr(module.win32gui, "EnumWindows", lambda *_: None)
    monkeypatch.setattr(module.time, "time", lambda: next(ticks))
    monkeypatch.setattr(module.time, "sleep", lambda *_: None)
    monkeypatch.setattr(module, "_press_enter_fallback", lambda: presses.append(True))

    assert (
        module.auto_close_export_dialog(
            wait_timeout=1, fallback_enter=True, initial_save_only=True
        )
        is False
    )
    assert presses == []


def test_export_dialog_lifecycle_clicks_save_and_final_confirmation_only(monkeypatch):
    module = importlib.import_module(
        "q_agent_function_module.ohresult.CAD_Git_Coordinates.my_code.ifc_function.git_ifc"
    )
    stage = {"value": 0}
    clicks = []

    def enum_windows(callback, extra):
        roots = (100, 200, 300, 400)
        hwnd = roots[min(stage["value"], len(roots) - 1)]
        stage["value"] += 1
        callback(hwnd, extra)

    def enum_children(hwnd, callback, extra):
        children = {
            100: (101,),
            200: (201,),
            300: (301,),
            400: (401, 402),
        }.get(hwnd, ())
        for child in children:
            callback(child, extra)

    titles = {
        100: "导出IFC",
        101: "保存",
        200: "导出模型",
        201: "正在处理",
        300: "模型压缩",
        301: "正在处理",
        400: "导出模型",
        401: "导出已完成！",
        402: "确认",
    }
    monkeypatch.setattr(module.win32gui, "EnumWindows", enum_windows)
    monkeypatch.setattr(module.win32gui, "EnumChildWindows", enum_children)
    monkeypatch.setattr(module.win32gui, "IsWindowVisible", lambda _: True)
    monkeypatch.setattr(module.win32gui, "GetWindowText", lambda hwnd: titles[hwnd])
    monkeypatch.setattr(
        module.win32gui,
        "GetClassName",
        lambda hwnd: "#32770" if hwnd == 100 else "Button",
    )
    monkeypatch.setattr(
        module.win32gui,
        "SendMessage",
        lambda hwnd, *_: clicks.append(hwnd),
    )
    monkeypatch.setattr(module.time, "sleep", lambda *_: None)

    state = module.RevitIfcExportDialogState()
    result = module.monitor_revit_ifc_export_dialogs(
        state, threading.Event(), poll_interval_seconds=0
    )

    assert result.initial_save_confirmed is True
    assert result.final_confirmation_clicked is True
    assert clicks == [101, 402]


def test_export_dialog_lifecycle_waits_past_statistics_report_progress(monkeypatch):
    module = importlib.import_module(
        "q_agent_function_module.ohresult.CAD_Git_Coordinates.my_code.ifc_function.git_ifc"
    )
    stage = {"value": 0}
    clicks = []
    titles = {
        100: "导出模型",
        101: "导出已完成，正在生成统计报告...",
        200: "导出模型",
        201: "导出已完成！",
        202: "确认",
    }

    def enum_windows(callback, extra):
        hwnd = 100 if stage["value"] == 0 else 200
        stage["value"] += 1
        callback(hwnd, extra)

    def enum_children(hwnd, callback, extra):
        children = {100: (101,), 200: (201, 202)}.get(hwnd, ())
        for child in children:
            callback(child, extra)

    monkeypatch.setattr(module.win32gui, "EnumWindows", enum_windows)
    monkeypatch.setattr(module.win32gui, "EnumChildWindows", enum_children)
    monkeypatch.setattr(module.win32gui, "IsWindowVisible", lambda _: True)
    monkeypatch.setattr(module.win32gui, "GetWindowText", lambda hwnd: titles[hwnd])
    monkeypatch.setattr(module.win32gui, "GetClassName", lambda _: "Button")
    monkeypatch.setattr(module.win32gui, "IsIconic", lambda _: False)
    monkeypatch.setattr(module.win32gui, "ShowWindow", lambda *_: None)
    monkeypatch.setattr(module.win32gui, "SetForegroundWindow", lambda *_: None)
    monkeypatch.setattr(
        module.win32gui, "SendMessage", lambda hwnd, *_: clicks.append(hwnd)
    )
    monkeypatch.setattr(module.time, "sleep", lambda *_: None)

    result = module.monitor_revit_ifc_export_dialogs(
        module.RevitIfcExportDialogState(),
        threading.Event(),
        poll_interval_seconds=0,
    )

    assert result.final_confirmation_clicked is True
    assert result.final_completion_window_handle == 200
    assert clicks == [202]


def test_export_dialog_lifecycle_ignores_other_revit_process(monkeypatch):
    module = importlib.import_module(
        "q_agent_function_module.ohresult.CAD_Git_Coordinates.my_code.ifc_function.git_ifc"
    )
    clicks = []
    titles = {
        100: "导出模型",
        101: "导出已完成！",
        102: "确认",
        200: "导出模型",
        201: "导出已完成！",
        202: "确认",
    }

    def enum_windows(callback, extra):
        callback(100, extra)
        callback(200, extra)

    def enum_children(hwnd, callback, extra):
        for child in ({100: (101, 102), 200: (201, 202)}.get(hwnd, ())):
            callback(child, extra)

    monkeypatch.setattr(module.win32gui, "EnumWindows", enum_windows)
    monkeypatch.setattr(module.win32gui, "EnumChildWindows", enum_children)
    monkeypatch.setattr(module.win32gui, "IsWindowVisible", lambda _: True)
    monkeypatch.setattr(module.win32gui, "GetWindowText", lambda hwnd: titles[hwnd])
    monkeypatch.setattr(module.win32gui, "GetClassName", lambda _: "Button")
    monkeypatch.setattr(module.win32gui, "SendMessage", lambda hwnd, *_: clicks.append(hwnd))
    monkeypatch.setattr(
        module.win32process,
        "GetWindowThreadProcessId",
        lambda hwnd: (1, 11 if hwnd == 100 else 22),
    )
    monkeypatch.setattr(module.time, "sleep", lambda *_: None)

    state = module.RevitIfcExportDialogState(target_process_id=22)
    result = module.monitor_revit_ifc_export_dialogs(
        state, threading.Event(), poll_interval_seconds=0
    )

    assert result.final_confirmation_clicked is True
    assert clicks == [202]


def test_export_dialog_lifecycle_accepts_new_external_sz_ifc_completion(monkeypatch):
    module = importlib.import_module(
        "q_agent_function_module.ohresult.CAD_Git_Coordinates.my_code.ifc_function.git_ifc"
    )
    clicks = []
    titles = {100: "导出模型", 101: "导出已完成！", 102: "确认"}

    monkeypatch.setattr(
        module.win32gui, "EnumWindows", lambda callback, extra: callback(100, extra)
    )
    monkeypatch.setattr(
        module.win32gui,
        "EnumChildWindows",
        lambda hwnd, callback, extra: [callback(child, extra) for child in (101, 102)],
    )
    monkeypatch.setattr(module.win32gui, "IsWindowVisible", lambda _: True)
    monkeypatch.setattr(module.win32gui, "GetWindowText", lambda hwnd: titles[hwnd])
    monkeypatch.setattr(module.win32gui, "GetClassName", lambda _: "Button")
    monkeypatch.setattr(module.win32gui, "SendMessage", lambda hwnd, *_: clicks.append(hwnd))
    monkeypatch.setattr(module.win32gui, "ShowWindow", lambda *_: None)
    monkeypatch.setattr(module.win32gui, "SetForegroundWindow", lambda *_: None)
    monkeypatch.setattr(
        module.win32process, "GetWindowThreadProcessId", lambda _: (1, 333)
    )
    monkeypatch.setattr(module.time, "sleep", lambda *_: None)

    state = module.RevitIfcExportDialogState(
        target_process_id=22,
        external_plugin_baseline_window_handles=set(),
    )
    result = module.monitor_revit_ifc_export_dialogs(
        state, threading.Event(), poll_interval_seconds=0
    )

    assert result.final_confirmation_clicked is True
    assert clicks == [102]


def test_export_dialog_lifecycle_detects_reused_window_by_completion_text(monkeypatch):
    module = importlib.import_module(
        "q_agent_function_module.ohresult.CAD_Git_Coordinates.my_code.ifc_function.git_ifc"
    )
    clicks = []
    titles = {400: "自定义导出窗口", 401: "导出已完成！", 402: "确认"}

    monkeypatch.setattr(
        module.win32gui,
        "EnumWindows",
        lambda callback, extra: callback(400, extra),
    )
    monkeypatch.setattr(
        module.win32gui,
        "EnumChildWindows",
        lambda hwnd, callback, extra: [callback(child, extra) for child in (401, 402)],
    )
    monkeypatch.setattr(module.win32gui, "IsWindowVisible", lambda _: True)
    monkeypatch.setattr(module.win32gui, "GetWindowText", lambda hwnd: titles[hwnd])
    monkeypatch.setattr(module.win32gui, "GetClassName", lambda _: "Button")
    monkeypatch.setattr(module.win32gui, "SendMessage", lambda hwnd, *_: clicks.append(hwnd))
    monkeypatch.setattr(module.time, "sleep", lambda *_: None)

    state = module.RevitIfcExportDialogState(
        baseline_window_handles={400},
        baseline_window_texts={400: ("导出模型", "正在处理")},
    )
    result = module.monitor_revit_ifc_export_dialogs(
        state, threading.Event(), poll_interval_seconds=0
    )

    assert result.final_confirmation_clicked is True
    assert clicks == [402]


def test_scoped_final_confirmation_uses_legacy_uia_fallback(monkeypatch):
    module = importlib.import_module(
        "q_agent_function_module.ohresult.CAD_Git_Coordinates.my_code.ifc_function.git_ifc"
    )
    fallback_handles = []

    monkeypatch.setattr(module, "_click_button_with_labels", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(
        module,
        "_try_click_confirm_button",
        lambda hwnd: fallback_handles.append(hwnd) or True,
    )

    assert module._click_scoped_final_confirmation(404) is True
    assert fallback_handles == [404]


def test_scoped_final_confirmation_restores_minimized_window_before_click(monkeypatch):
    module = importlib.import_module(
        "q_agent_function_module.ohresult.CAD_Git_Coordinates.my_code.ifc_function.git_ifc"
    )
    events = []
    iconic_states = iter((True, False))

    monkeypatch.setattr(module.win32gui, "IsIconic", lambda _: next(iconic_states))
    monkeypatch.setattr(
        module.win32gui,
        "ShowWindow",
        lambda hwnd, command: events.append(("restore", hwnd, command)) or 1,
    )
    monkeypatch.setattr(
        module.win32gui,
        "SetForegroundWindow",
        lambda hwnd: events.append(("foreground", hwnd)),
    )
    monkeypatch.setattr(
        module.time, "sleep", lambda seconds: events.append(("wait", seconds))
    )
    monkeypatch.setattr(
        module,
        "_click_button_with_labels",
        lambda hwnd, *_args, **_kwargs: events.append(("click", hwnd)) or True,
    )

    assert module._click_scoped_final_confirmation(404) is True
    assert events == [
        ("restore", 404, module.win32con.SW_RESTORE),
        ("foreground", 404),
        ("wait", 0.5),
        ("click", 404),
    ]


def test_scoped_final_confirmation_does_not_close_window_when_button_is_hidden(monkeypatch):
    module = importlib.import_module(
        "q_agent_function_module.ohresult.CAD_Git_Coordinates.my_code.ifc_function.git_ifc"
    )
    messages = []

    monkeypatch.setattr(module, "_click_button_with_labels", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(module, "_try_click_confirm_button", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(module, "_window_child_texts", lambda *_: ["导出已完成！"])
    monkeypatch.setattr(module.win32gui, "ShowWindow", lambda *_: None)
    monkeypatch.setattr(module.win32gui, "SetForegroundWindow", lambda *_: None)
    monkeypatch.setattr(module.win32gui, "IsIconic", lambda *_: False)
    monkeypatch.setattr(module.time, "sleep", lambda *_: None)
    monkeypatch.setattr(
        module.win32gui,
        "SendMessage",
        lambda hwnd, message, *_: messages.append((hwnd, message)),
    )

    assert module._click_scoped_final_confirmation(404) is False
    assert messages == []


def test_export_ifc_detects_files_in_ifc_assigned_subfolder(tmp_path, monkeypatch):
    target = tmp_path / "B2.ifc"
    subfolder_file = tmp_path / "ifc-assigned" / "B2.ifc"
    subfolder_file.parent.mkdir()
    client = DeliveryClient(subfolder_file)
    workflow = RevitProjectDelivery(client)
    monkeypatch.setattr("app.revit.project_delivery._close_export_dialog", lambda: True)
    try:
        result = asyncio.run(workflow.export_ifc(str(target), timeout_seconds=1))
    finally:
        workflow.close()
    assert result["ifc_path"] == str(subfolder_file)
    assert result["xlsx_path"] == f"{subfolder_file}.xlsx"


def test_export_ifc_detects_files_in_sibling_result_ifc_assigned_folder(
    tmp_path, monkeypatch
):
    """Accept the plugin's bounded legacy result/ifc-assigned output layout."""
    target = tmp_path / "result" / "ifc" / "B2.ifc"
    sibling_file = tmp_path / "result" / "ifc-assigned" / "B2.ifc"
    client = DeliveryClient(sibling_file)
    workflow = RevitProjectDelivery(client)
    workflow.lock = NoopRevitLock()
    monkeypatch.setattr("app.revit.project_delivery._close_export_dialog", lambda: True)
    try:
        result = asyncio.run(workflow.export_ifc(str(target), timeout_seconds=1))
    finally:
        workflow.close()

    assert client.export_paths == [str(target)]
    assert result["ifc_path"] == str(sibling_file)
    assert result["xlsx_path"] == f"{sibling_file}.xlsx"


def test_export_ifc_detects_files_in_project_root_when_target_in_result_ifc_assigned(
    tmp_path, monkeypatch
):
    """Detect .ifc/.xlsx in the project root directory when Revit exports to opened model location."""
    target = tmp_path / "result" / "ifc-assigned" / "B2.ifc"
    root_file = tmp_path / "B2.ifc"
    client = DeliveryClient(root_file)
    workflow = RevitProjectDelivery(client)
    workflow.lock = NoopRevitLock()
    monkeypatch.setattr("app.revit.project_delivery._close_export_dialog", lambda: True)
    try:
        result = asyncio.run(workflow.export_ifc(str(target), timeout_seconds=1))
    finally:
        workflow.close()

    assert client.export_paths == [str(target)]
    assert result["ifc_path"] == str(root_file)
    assert result["xlsx_path"] == f"{root_file}.xlsx"


def test_fresh_ifc_pair_rejects_unchanged_sibling_delivery(tmp_path):
    target = tmp_path / "result" / "ifc" / "B2.ifc"
    sibling_file = tmp_path / "result" / "ifc-assigned" / "B2.ifc"
    _write_ifc_pair(sibling_file)
    workflow = RevitProjectDelivery(DeliveryClient())
    try:
        candidates = workflow._ifc_candidates(target)
        before = {
            artifact: workflow._file_fingerprint(artifact)
            for candidate in candidates
            for artifact in (candidate, Path(f"{candidate}.xlsx"))
        }
        assert workflow._fresh_ifc_pair(candidates, before) is None
    finally:
        workflow.close()


def test_export_ifc_uses_numbered_path_when_delivery_already_exists(tmp_path, monkeypatch):
    target = tmp_path / "existing.ifc"
    target.write_text("existing", encoding="utf-8")
    numbered = tmp_path / "existing(1).ifc"
    client = DeliveryClient(numbered)
    workflow = RevitProjectDelivery(client)
    monkeypatch.setattr("app.revit.project_delivery._close_export_dialog", lambda: True)
    try:
        result = asyncio.run(workflow.export_ifc(str(target), timeout_seconds=1))
    finally:
        workflow.close()
    assert client.export_paths == [str(numbered)]
    assert result["ifc_path"] == str(numbered)


def test_export_ifc_accepts_fresh_original_name_when_plugin_ignores_collision_suffix(
    tmp_path, monkeypatch
):
    target = tmp_path / "existing.ifc"
    target.write_text("old export", encoding="utf-8")
    numbered = tmp_path / "existing(1).ifc"

    class OriginalNamePluginClient(DeliveryClient):
        async def export_ifc(self, path):
            self.export_paths.append(path)
            assert Path(path) == numbered
            _write_ifc_pair(target)
            return {"code": 200, "msg": "export started"}

    workflow = RevitProjectDelivery(OriginalNamePluginClient())
    monkeypatch.setattr("app.revit.project_delivery._close_export_dialog", lambda: True)
    try:
        result = asyncio.run(workflow.export_ifc(str(target), timeout_seconds=1))
    finally:
        workflow.close()

    assert workflow.client.export_paths == [str(numbered)]
    assert result["ifc_path"] == str(target)
    assert result["xlsx_path"] == f"{target}.xlsx"


def test_export_ifc_times_out_without_retry_or_restart(tmp_path, monkeypatch):
    target = tmp_path / "project_AR-B3.ifc"
    assigned = tmp_path / "ifc-assigned" / "project_AR-B3.rvt"
    assigned.parent.mkdir()
    assigned.write_text("assigned", encoding="utf-8")

    class RecoveringClient(DeliveryClient):
        async def export_ifc(self, path):
            self.export_paths.append(path)
            return {"code": 200, "msg": "accepted"}
    workflow = RevitProjectDelivery(RecoveringClient())
    try:
        result = asyncio.run(
            workflow.export_ifc(
                str(target), timeout_seconds=1, recovery_model_path=str(assigned)
            )
        )
    finally:
        workflow.close()

    assert workflow.client.export_paths == [str(target)]
    assert result["status"] == "timed_out_unknown"
    assert result["export_session"] == 1
    assert result["export_attempt"] == 1


def test_export_ifc_api_timeout_returns_unknown_without_retry(tmp_path, monkeypatch):
    target = tmp_path / "slow.ifc"
    client = DeliveryClient()

    async def timed_out(*_args, **_kwargs):
        raise RevitOperationUnknown("final state unknown")

    monkeypatch.setattr("app.revit.project_delivery.call_revit_operation", timed_out)
    workflow = RevitProjectDelivery(client)
    try:
        result = asyncio.run(workflow.export_ifc(str(target), timeout_seconds=1))
    finally:
        workflow.close()

    assert result["status"] == "timed_out_unknown"
    assert result["export_attempt"] == 1


def test_export_ifc_preserves_existing_files_when_recovery_fails(tmp_path, monkeypatch):
    target = tmp_path / "project_AR-B3.ifc"
    target.write_text("old-ifc", encoding="utf-8")
    Path(f"{target}.xlsx").write_text("old-xlsx", encoding="utf-8")
    assigned = tmp_path / "ifc-assigned" / "project_AR-B3.rvt"
    assigned.parent.mkdir()
    assigned.write_text("assigned", encoding="utf-8")

    workflow = RevitProjectDelivery(DeliveryClient())
    try:
        result = asyncio.run(
            workflow.export_ifc(
                str(target), timeout_seconds=1, recovery_model_path=str(assigned)
            )
        )
    finally:
        workflow.close()

    assert target.read_text(encoding="utf-8") == "old-ifc"
    assert Path(f"{target}.xlsx").read_text(encoding="utf-8") == "old-xlsx"
    assert result["status"] == "timed_out_unknown"
    assert len(workflow.client.export_paths) == 1


def test_export_ifc_remains_successful_when_closing_the_dialog_loses_focus(tmp_path, monkeypatch):
    target = tmp_path / "B3.ifc"
    client = DeliveryClient(target)
    workflow = RevitProjectDelivery(client)
    monkeypatch.setattr(
        "app.revit.project_delivery._close_export_dialog",
        lambda: (_ for _ in ()).throw(OSError("SetForegroundWindow denied")),
    )
    try:
        result = asyncio.run(workflow.export_ifc(str(target), timeout_seconds=1))
    finally:
        workflow.close()
    assert result["ifc_path"] == str(target)
    assert result["xlsx_path"] == f"{target}.xlsx"


def test_export_ifc_never_closes_a_revit_window_before_both_files_exist(tmp_path, monkeypatch):
    target = tmp_path / "B3.ifc"
    client = DeliveryClient(target)
    workflow = RevitProjectDelivery(client)

    def close_only_after_output():
        assert target.is_file()
        assert Path(f"{target}.xlsx").is_file()
        return True

    monkeypatch.setattr("app.revit.project_delivery._close_export_dialog", close_only_after_output)
    try:
        result = asyncio.run(workflow.export_ifc(str(target), timeout_seconds=1))
    finally:
        workflow.close()
    assert result["ifc_path"] == str(target)


def test_output_preflight_handles_sidecars_and_profession_mapping(tmp_path):
    requested = tmp_path / "new" / "delivery.ifc"
    requested.parent.mkdir()
    Path(f"{requested}.xlsx").write_text("sidecar", encoding="utf-8")
    selected = prepare_output_file_path(str(requested), sidecar_suffixes=(".xlsx",))
    assert selected == requested.with_name("delivery(1).ifc")
    assert resolve_sz_ifc_profession("AR", "project_without_code.ifc") == "建筑"
    assert resolve_sz_ifc_profession(None, "project_AR-B3.ifc") == "建筑"
    assert resolve_sz_ifc_profession("EL", "ignored.ifc") == "电气"
    assert resolve_sz_ifc_profession(None, "project_FS-B3.ifc") == "结构"
    assert resolve_sz_ifc_profession(None, "project_T-B3.ifc") == "电气"
    with pytest.raises(ProfessionSelectionRequired, match="可能表示总图或燃气"):
        resolve_sz_ifc_profession(None, "project_G-B3.ifc")
    assert resolve_sz_ifc_profession(None, "project_燃气_G-B3.ifc") == "燃气"
    assert resolve_sz_ifc_profession(None, "project_总图_G-B3.ifc") == "总图"
    with pytest.raises(ProfessionSelectionRequired, match="多个专业代码"):
        resolve_sz_ifc_profession(None, "project_AR_ST-B3.ifc")


def test_export_ifc_rejects_incomplete_artifacts_without_retry(tmp_path, monkeypatch):
    target = tmp_path / "incomplete.ifc"

    class IncompleteClient(DeliveryClient):
        async def export_ifc(self, path):
            self.export_paths.append(path)
            Path(path).write_text("ISO-10303-21;", encoding="utf-8")
            Path(f"{path}.xlsx").write_text("not-a-workbook", encoding="utf-8")
            return {"code": 200, "msg": "accepted"}

    workflow = RevitProjectDelivery(IncompleteClient())
    try:
        result = asyncio.run(workflow.export_ifc(str(target), timeout_seconds=1))
    finally:
        workflow.close()

    assert result["status"] == "timed_out_unknown"
    assert workflow.client.export_paths == [str(target)]


def test_sz_ifc_inspection_preflights_a_numbered_report_path(tmp_path, monkeypatch):
    ifc = tmp_path / "project_AR-B3.ifc"
    ifc.write_text("ifc", encoding="utf-8")
    existing = tmp_path / "reports" / "report.docx"
    existing.parent.mkdir()
    existing.write_text("existing", encoding="utf-8")
    received = {}

    def inspect(path, profession, output, rule_name, timeout_seconds, cancel_event):
        assert not cancel_event.is_set()
        received.update(path=path, profession=profession, output=output)
        _write_docx(Path(output))
        return output

    monkeypatch.setattr("app.revit.project_delivery._run_sz_ifc_inspection", inspect)
    workflow = RevitProjectDelivery(DeliveryClient())
    try:
        result = asyncio.run(workflow.inspect_ifc(str(ifc), "AR", str(existing)))
    finally:
        workflow.close()
    assert received["profession"] == "建筑"
    assert received["output"] == str(existing.with_name("report(1).docx"))
    assert result["report_path"] == received["output"]


def test_sz_ifc_inspection_does_not_claim_success_without_a_docx(tmp_path, monkeypatch):
    ifc = tmp_path / "project_AR-B3.ifc"
    ifc.write_text("ifc", encoding="utf-8")
    missing_report = tmp_path / "missing.docx"
    monkeypatch.setattr(
        "app.revit.project_delivery._run_sz_ifc_inspection",
        lambda *_: str(missing_report),
    )
    workflow = RevitProjectDelivery(DeliveryClient())
    try:
        try:
            asyncio.run(workflow.inspect_ifc(str(ifc), "AR", str(missing_report)))
        except RuntimeError as error:
            assert "未生成 DOCX 报告" in str(error)
        else:
            raise AssertionError("inspection must not succeed without a DOCX file")
    finally:
        workflow.close()


def test_sz_ifc_timeout_returns_unknown_status(tmp_path, monkeypatch):
    ifc = tmp_path / "project_AR-B3.ifc"
    ifc.write_text("ifc", encoding="utf-8")

    def inspect(*_args):
        raise TimeoutError("等待导出报告超时，SZ-IFC 最终状态未知")

    monkeypatch.setattr("app.revit.project_delivery._run_sz_ifc_inspection", inspect)
    workflow = RevitProjectDelivery(DeliveryClient())
    try:
        result = asyncio.run(workflow.inspect_ifc(str(ifc), "AR"))
    finally:
        workflow.close()

    assert result["status"] == "timed_out_unknown"
    assert result["profession"] == "建筑"


def test_sz_ifc_cancellation_waits_for_worker_to_release_ui(tmp_path, monkeypatch):
    ifc = tmp_path / "project_AR-B3.ifc"
    ifc.write_text("ifc", encoding="utf-8")
    started = threading.Event()
    stopped = threading.Event()

    def inspect(*args):
        cancel_event = args[-1]
        started.set()
        cancel_event.wait(2)
        stopped.set()
        raise RuntimeError("cancelled")

    monkeypatch.setattr("app.revit.project_delivery._run_sz_ifc_inspection", inspect)

    async def scenario():
        workflow = RevitProjectDelivery(DeliveryClient())
        task = asyncio.create_task(workflow.inspect_ifc(str(ifc), "AR"))
        while not started.is_set():
            await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        workflow.close()

    asyncio.run(scenario())
    assert stopped.is_set()


def test_combined_delivery_returns_all_three_actual_paths(tmp_path, monkeypatch):
    from app.tool.revit_delivery import RevitExportAndInspectIfc

    target = tmp_path / "project_AR-B3.ifc"
    report = tmp_path / "report.docx"
    client = DeliveryClient(target)
    monkeypatch.setattr("app.revit.project_delivery._close_export_dialog", lambda: True)

    def inspect(path, profession, output, rule_name, timeout_seconds, cancel_event):
        assert not cancel_event.is_set()
        _write_docx(Path(output))
        return output

    monkeypatch.setattr("app.revit.project_delivery._run_sz_ifc_inspection", inspect)
    result = asyncio.run(
        RevitExportAndInspectIfc(client=client).execute(
            ifc_file_path=str(target), profession="AR",
            output_docx_path=str(report), timeout_seconds=1
        )
    )

    assert not result.error
    payload = json.loads(result.output)
    assert payload["ifc_path"] == str(target)
    assert payload["xlsx_path"] == f"{target}.xlsx"
    assert payload["report_path"] == str(report)


def test_base_point_delegates_extracted_payload(tmp_path, monkeypatch):
    dwg = tmp_path / "floor-B2.dwg"
    dwg.write_bytes(b"dwg")
    payload = {"coordinates": [{"Northsouth": "1", "Eastwest": "2"}], "Elevation": 0, "Angleton": 0}
    monkeypatch.setattr("app.revit.project_delivery._extract_base_point", lambda _: payload)
    client = DeliveryClient()
    workflow = RevitProjectDelivery(client)
    try:
        result = asyncio.run(workflow.set_base_point(str(dwg)))
    finally:
        workflow.close()
    assert client.base_point_payloads == [payload]
    assert result["message"] == "base point updated"


def test_base_point_optionally_opens_and_saves_the_target_model(tmp_path, monkeypatch):
    dwg = tmp_path / "floor-B2.dwg"
    model = tmp_path / "model_AR-B2.rvt"
    dwg.write_bytes(b"dwg")
    model.write_bytes(b"rvt")
    monkeypatch.setattr(
        "app.revit.project_delivery._extract_base_point",
        lambda _: {"coordinates": [], "Elevation": 0, "Angleton": 0},
    )
    client = DeliveryClient()
    workflow = RevitProjectDelivery(client)
    try:
        result = asyncio.run(workflow.set_base_point(str(dwg), str(model), str(tmp_path / "results")))
    finally:
        workflow.close()
    assert client.opened == str(model)
    assert client.saved == str(tmp_path / "results")
    assert result["saved_to"] == str(tmp_path / "results")
