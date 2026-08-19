import importlib.util
from pathlib import Path
import sys
import threading
import types

import pytest


def _load_sz_ifc_script(monkeypatch):
    fake_pywinauto = types.ModuleType("pywinauto")
    fake_pywinauto.Application = object
    fake_keyboard = types.ModuleType("pywinauto.keyboard")
    fake_keyboard.send_keys = lambda *_args, **_kwargs: None
    monkeypatch.setitem(sys.modules, "pywinauto", fake_pywinauto)
    monkeypatch.setitem(sys.modules, "pywinauto.keyboard", fake_keyboard)
    script = (
        Path(__file__).resolve().parents[2]
        / "q_agent_function_module"
        / "ohresult"
        / "CAD_Git_Coordinates"
        / "my_code"
        / "ifc_function"
        / "ifc_SZ-IFC_to_docx.py"
    )
    spec = importlib.util.spec_from_file_location("test_sz_ifc_script", script)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_sz_ifc_automation_only_attaches_to_user_prepared_window():
    script = (
        Path(__file__).resolve().parents[2]
        / "q_agent_function_module"
        / "ohresult"
        / "CAD_Git_Coordinates"
        / "my_code"
        / "ifc_function"
        / "ifc_SZ-IFC_to_docx.py"
    )
    source = script.read_text(encoding="utf-8")

    assert "Application(backend=\"uia\").connect(" in source
    assert ".connect(handle=handle" in source
    assert "title_re=\".*(SZ-IFC|CBIMS" not in source
    assert "ShellExecute" not in source
    assert "find_sz_ifc_by_registry" not in source
    assert "import winreg" not in source
    assert "subprocess.Popen" not in source
    # The report-save stage intentionally preserves the earlier EXE's focused
    # keyboard fallback for Windows dialogs that UI Automation cannot expose.
    assert "_legacy_save_report_dialog" in source


def test_sz_ifc_prefers_blank_cbims_main_window_over_start_window(monkeypatch):
    module = _load_sz_ifc_script(monkeypatch)

    def enum_windows(callback, extra):
        callback(10, extra)
        callback(20, extra)

    monkeypatch.setattr(module.win32gui, "EnumWindows", enum_windows)
    monkeypatch.setattr(module.win32gui, "IsWindowVisible", lambda _: True)
    monkeypatch.setattr(module.win32gui, "IsIconic", lambda _: False)
    monkeypatch.setattr(
        module.win32gui,
        "GetClassName",
        lambda _: "HwndWrapper[CBIMS.Manager;;test]",
    )
    monkeypatch.setattr(
        module.win32process, "GetWindowThreadProcessId", lambda _: (1, 1234)
    )
    monkeypatch.setattr(
        module.win32gui,
        "GetWindowText",
        lambda hwnd: "StartWindow" if hwnd == 10 else "",
    )
    monkeypatch.setattr(
        module.win32gui,
        "GetWindowRect",
        lambda hwnd: (0, 0, 100, 100) if hwnd == 10 else (0, 0, 1200, 800),
    )

    assert module._find_prepared_sz_ifc_main_window() == 20


def test_sz_ifc_reports_when_only_start_window_exists(monkeypatch):
    module = _load_sz_ifc_script(monkeypatch)
    monkeypatch.setattr(
        module.win32gui,
        "EnumWindows",
        lambda callback, extra: callback(10, extra),
    )
    monkeypatch.setattr(module.win32gui, "IsWindowVisible", lambda _: True)
    monkeypatch.setattr(module.win32gui, "IsIconic", lambda _: False)
    monkeypatch.setattr(
        module.win32gui,
        "GetClassName",
        lambda _: "HwndWrapper[CBIMS.Manager;;test]",
    )
    monkeypatch.setattr(module.win32gui, "GetWindowText", lambda _: "StartWindow")
    monkeypatch.setattr(module.win32gui, "GetWindowRect", lambda _: (0, 0, 100, 100))
    monkeypatch.setattr(
        module.win32process, "GetWindowThreadProcessId", lambda _: (1, 1234)
    )

    with pytest.raises(RuntimeError, match="StartWindow"):
        module._find_prepared_sz_ifc_main_window()


def test_sz_ifc_finds_new_pid_scoped_windows_save_dialog(monkeypatch):
    module = _load_sz_ifc_script(monkeypatch)

    class Edit:
        def exists(self, timeout=0):
            return True

        def is_visible(self):
            return True

        def is_enabled(self):
            return True

    class Window:
        def child_window(self, **_kwargs):
            return Edit()

    monkeypatch.setattr(
        module,
        "_top_level_windows",
        lambda: [
            {
                "handle": 10,
                "pid": 1234,
                "title": "保存",
                "class_name": "#32770",
            },
            {
                "handle": 20,
                "pid": 1234,
                "title": "保存",
                "class_name": "#32770",
            },
        ],
    )
    monkeypatch.setattr(module, "_window_related_to_target", lambda *_: True)
    monkeypatch.setattr(module, "_connect_top_level_window", lambda _: Window())

    item, _window = module._save_dialog(1234, 99, excluded_handles={10})
    assert item["handle"] == 20


def test_sz_ifc_accepts_unique_explorer_style_save_dialog_without_owner_link(monkeypatch):
    """The native dialog may be hosted outside CBIMS but still be this export."""
    module = _load_sz_ifc_script(monkeypatch)

    class Control:
        def exists(self, timeout=0):
            return True

        def is_visible(self):
            return True

        def is_enabled(self):
            return True

    class Window:
        def child_window(self, **_kwargs):
            return Control()

    monkeypatch.setattr(
        module,
        "_top_level_windows",
        lambda: [
            {
                "handle": 55,
                "pid": 9876,
                "title": "保存 在 桌面",
                "class_name": "#32770",
            }
        ],
    )
    monkeypatch.setattr(module, "_window_related_to_target", lambda *_: False)
    monkeypatch.setattr(module, "_connect_top_level_window", lambda _: Window())

    item, _window = module._save_dialog(1234, 99, excluded_handles={10, 20})
    assert item["handle"] == 55


def test_sz_ifc_dialog_wait_stops_when_cancelled(monkeypatch):
    module = _load_sz_ifc_script(monkeypatch)
    cancel_event = threading.Event()
    cancel_event.set()

    with pytest.raises(module.SzIfcInspectionCancelled):
        module._wait(
            999999999,
            "保存窗口",
            lambda: None,
            cancel_event=cancel_event,
        )
