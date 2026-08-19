import asyncio
import json
import os
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

from app.revit.client import RevitApiError
from app.tool.desktop_bim import (
    AutocadInstallation,
    AutocadLaunchSpec,
    EnsureAutocadRunning,
    RevitOpenProjectModel,
    RevitStartupExited,
    SzIfcOpenModel,
    _autocad_launch_spec,
    _autocad_com_status,
    _model_candidates,
    _shell_open_executable,
    _wait_for_autocad_com_ready,
    _wait_for_revit_application_ready,
    _wait_for_revit_model_ready,
    _windows_command_executable,
)
from app.tool.windows_app import RevitInstallation, RevitLaunchSpec, _external_process_environment


def test_folder_model_selection_uses_unique_architectural_code(tmp_path):
    architectural = tmp_path / "project_AR-B3.rvt"
    structural = tmp_path / "project_ST-B3.rvt"
    architectural.write_bytes(b"rvt")
    structural.write_bytes(b"rvt")

    assert _model_candidates(tmp_path, None) == [architectural]
    assert _model_candidates(tmp_path, "结构") == [structural]


def test_revit_project_model_requests_upgrade_confirmation(monkeypatch, tmp_path):
    model = tmp_path / "project_AR.rvt"
    model.write_bytes(b"rvt")
    monkeypatch.setattr("app.tool.desktop_bim.read_revit_model_version", lambda _: 2022)
    monkeypatch.setattr("app.tool.desktop_bim.running_revit_processes", lambda: [])
    monkeypatch.setattr(
        "app.tool.desktop_bim.discover_revit_installations",
        lambda: [RevitInstallation(2024, Path("C:/Revit2024/Revit.exe"))],
    )

    result = asyncio.run(RevitOpenProjectModel().execute(str(model)))
    payload = json.loads(result.output)

    assert payload["status"] == "selection_required"
    assert payload["candidates"][0]["display_version"] == "2024"


def test_revit_project_model_starts_revit_before_opening_model(monkeypatch, tmp_path):
    model = tmp_path / "project_AR.rvt"
    model.write_bytes(b"rvt")
    executable = tmp_path / "Revit2022.exe"
    executable.write_bytes(b"exe")

    monkeypatch.setattr("app.tool.desktop_bim.read_revit_model_version", lambda _: 2022)
    monkeypatch.setattr("app.tool.desktop_bim.running_revit_processes", lambda: [])
    monkeypatch.setattr(
        "app.tool.desktop_bim.discover_revit_installations",
        lambda: [RevitInstallation(2022, executable)],
    )

    class Process:
        pid = 2468

        def poll(self):
            return None

    launch_spec = RevitLaunchSpec(
        executable=executable,
        arguments=(),
        working_directory=executable.parent,
        source="direct_installation",
    )
    order = []

    def launch(installation):
        assert installation == RevitInstallation(2022, executable)
        order.append("launch")
        return Process(), launch_spec

    monkeypatch.setattr(
        "app.tool.desktop_bim._launch_revit",
        launch,
    )

    async def application_ready(process_id, _client, timeout, launched_process=None):
        assert process_id == 2468
        assert timeout == 7200
        assert launched_process is not None
        order.append("application_ready")
        return {"pid": 2468, "hwnd": 88, "plugin_status": "ready"}

    async def open_model(*_args, **_kwargs):
        assert 0 < _kwargs["timeout_seconds"] <= 7200
        order.append("open_model")
        return {"msg": "模型已打开"}

    async def ready(process_ids, opened_model, _client, timeout, launched_process=None):
        assert process_ids == [2468]
        assert opened_model == model
        assert timeout == 7200
        assert launched_process is not None
        order.append("model_ready")
        return {"pid": 2468, "hwnd": 99, "plugin_status": "ready"}

    monkeypatch.setattr(
        "app.tool.desktop_bim._wait_for_revit_application_ready", application_ready
    )
    monkeypatch.setattr("app.tool.desktop_bim.call_revit_operation", open_model)
    monkeypatch.setattr("app.tool.desktop_bim._wait_for_revit_model_ready", ready)

    result = asyncio.run(RevitOpenProjectModel().execute(str(model)))
    payload = json.loads(result.output)

    assert payload["status"] == "ready"
    assert payload["pid"] == 2468
    assert payload["window_handle"] == 99
    assert payload["plugin_status"] == "ready"
    assert "直接继续" in payload["message"]
    assert order == ["launch", "application_ready", "open_model", "model_ready"]


def test_revit_project_model_returns_immediately_when_new_process_exits(monkeypatch, tmp_path):
    model = tmp_path / "project_AR.rvt"
    model.write_bytes(b"rvt")
    executable = tmp_path / "Revit2020.exe"
    executable.write_bytes(b"exe")
    monkeypatch.setattr("app.tool.desktop_bim.read_revit_model_version", lambda _: 2020)
    monkeypatch.setattr("app.tool.desktop_bim.running_revit_processes", lambda: [])
    monkeypatch.setattr(
        "app.tool.desktop_bim.discover_revit_installations",
        lambda: [RevitInstallation(2020, executable)],
    )

    class Process:
        pid = 2468

        def poll(self):
            return 500

    launch_spec = RevitLaunchSpec(
        executable=executable,
        arguments=(),
        working_directory=executable.parent,
        source="direct_installation",
    )
    monkeypatch.setattr(
        "app.tool.desktop_bim._launch_revit",
        lambda installation: (Process(), launch_spec),
    )

    result = asyncio.run(RevitOpenProjectModel().execute(str(model)))
    payload = json.loads(result.output)

    assert payload["status"] == "user_action_required"
    assert payload["pid"] == 2468
    assert payload["exit_code"] == 500
    assert payload["stage"] == "application_startup"


def test_revit_project_model_does_not_open_model_before_startup_is_ready(monkeypatch, tmp_path):
    model = tmp_path / "project_AR.rvt"
    model.write_bytes(b"rvt")
    executable = tmp_path / "Revit2020.exe"
    executable.write_bytes(b"exe")

    class Process:
        pid = 2468

        def poll(self):
            return None

    launch_spec = RevitLaunchSpec(
        executable=executable,
        arguments=(),
        working_directory=executable.parent,
        source="direct_installation",
    )
    monkeypatch.setattr("app.tool.desktop_bim.read_revit_model_version", lambda _: 2020)
    monkeypatch.setattr("app.tool.desktop_bim.running_revit_processes", lambda: [])
    monkeypatch.setattr(
        "app.tool.desktop_bim.discover_revit_installations",
        lambda: [RevitInstallation(2020, executable)],
    )
    monkeypatch.setattr(
        "app.tool.desktop_bim._launch_revit", lambda installation: (Process(), launch_spec)
    )

    async def not_ready(*_args, **_kwargs):
        return None

    monkeypatch.setattr("app.tool.desktop_bim._wait_for_revit_application_ready", not_ready)
    monkeypatch.setattr(
        "app.tool.desktop_bim.call_revit_operation",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("model must not be opened before phase one is ready")
        ),
    )

    result = asyncio.run(RevitOpenProjectModel().execute(str(model)))
    payload = json.loads(result.output)

    assert payload["status"] == "user_action_required"
    assert payload["stage"] == "application_startup"
    assert payload["model_open_attempted"] is False
    assert payload["next_action"] == "resolve_revit_startup_and_retry"


def test_revit_project_model_keeps_existing_instance_open_path(monkeypatch, tmp_path):
    model = tmp_path / "project_AR.rvt"
    model.write_bytes(b"rvt")
    running = SimpleNamespace(pid=2468)
    calls = []

    monkeypatch.setattr("app.tool.desktop_bim.read_revit_model_version", lambda _: 2018)
    monkeypatch.setattr("app.tool.desktop_bim.running_revit_processes", lambda: [running])
    monkeypatch.setattr("app.tool.desktop_bim._running_revit_version", lambda _: 2018)

    async def open_in_existing_revit(*_args, **_kwargs):
        calls.append("open")
        return {"msg": "模型已打开"}

    async def ready(process_ids, opened_model, _client, timeout):
        assert process_ids == [2468]
        assert opened_model == model
        assert timeout == 7200
        return {"pid": 2468, "hwnd": 99, "plugin_status": "ready"}

    monkeypatch.setattr("app.tool.desktop_bim.call_revit_operation", open_in_existing_revit)
    monkeypatch.setattr("app.tool.desktop_bim._wait_for_revit_model_ready", ready)

    result = asyncio.run(RevitOpenProjectModel().execute(str(model)))
    payload = json.loads(result.output)

    assert calls == ["open"]
    assert payload["status"] == "ready"
    assert payload["pid"] == 2468


def test_revit_project_model_explains_existing_model_switch_failure(monkeypatch, tmp_path):
    model = tmp_path / "project_ST.rvt"
    model.write_bytes(b"rvt")
    running = SimpleNamespace(pid=2468)

    monkeypatch.setattr("app.tool.desktop_bim.read_revit_model_version", lambda _: 2018)
    monkeypatch.setattr("app.tool.desktop_bim.running_revit_processes", lambda: [running])
    monkeypatch.setattr("app.tool.desktop_bim._running_revit_version", lambda _: 2018)

    async def rejected_by_existing_document(*_args, **_kwargs):
        raise RevitApiError(
            "执行异常：Failed opening a Revit file.",
            endpoint="/OpenRevitFile",
            http_status=200,
            plugin_code=500,
        )

    monkeypatch.setattr(
        "app.tool.desktop_bim.call_revit_operation", rejected_by_existing_document
    )
    monkeypatch.setattr(
        "app.tool.desktop_bim._wait_for_revit_model_ready",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("must not wait after a rejected model switch")
        ),
    )

    result = asyncio.run(RevitOpenProjectModel().execute(str(model)))
    payload = json.loads(result.output)

    assert payload["status"] == "user_action_required"
    assert payload["next_action"] == "save_and_close_current_model"
    assert payload["pid"] == 2468
    assert "保存并关闭当前模型" in payload["message"]


def test_revit_readiness_probe_raises_as_soon_as_new_process_exits(tmp_path):
    class Process:
        pid = 2468

        def poll(self):
            return 500

    try:
        asyncio.run(
            _wait_for_revit_model_ready(
                [Process.pid],
                tmp_path / "project.rvt",
                object(),
                7200,
                launched_process=Process(),
            )
        )
        assert False, "expected the exited process to stop readiness waiting"
    except RevitStartupExited as error:
        assert error.process_id == 2468
        assert error.exit_code == 500
        assert error.stage == "model_open"


def test_revit_application_readiness_probe_raises_before_model_open(tmp_path):
    class Process:
        pid = 2468

        def poll(self):
            return 500

    try:
        asyncio.run(
            _wait_for_revit_application_ready(
                Process.pid,
                object(),
                7200,
                launched_process=Process(),
            )
        )
        assert False, "expected the exited process to stop before opening a model"
    except RevitStartupExited as error:
        assert error.process_id == 2468
        assert error.exit_code == 500
        assert error.stage == "application_startup"


def test_autocad_returns_manual_action_without_registry_candidate(monkeypatch):
    monkeypatch.setattr("app.tool.desktop_bim._running_processes", lambda _: [])
    monkeypatch.setattr("app.tool.desktop_bim._discover_autocad_installations", lambda _: [])
    monkeypatch.setattr("app.tool.desktop_bim._running_autocad_instances", lambda *_: [])

    result = asyncio.run(EnsureAutocadRunning().execute())
    payload = json.loads(result.output)

    assert payload["status"] == "user_action_required"
    assert "手动打开" in payload["message"]


def test_unquoted_autocad_localserver_command_resolves_executable(tmp_path):
    executable = tmp_path / "AutoCAD 2020" / "acad.exe"
    executable.parent.mkdir()
    executable.write_bytes(b"exe")

    assert _windows_command_executable(f"{executable} /Automation") == executable


def test_external_environment_removes_frozen_runtime_paths(monkeypatch, tmp_path):
    bundle = tmp_path / "_internal"
    bundle.mkdir()
    normal = tmp_path / "normal"
    normal.mkdir()
    monkeypatch.setattr("app.tool.windows_app.sys._MEIPASS", str(bundle), raising=False)
    monkeypatch.setenv("PATH", os.pathsep.join((str(bundle), str(normal))))
    monkeypatch.setenv("PYTHONHOME", "embedded")
    monkeypatch.setenv("PYTHONPATH", "embedded-modules")
    monkeypatch.setenv("_PYI_PARENT_PROCESS_LEVEL", "1")
    monkeypatch.setenv("TK_LIBRARY", "runtime-tk")
    monkeypatch.setenv("BEESYNC_RUNTIME_TOKEN", "must-not-reach-revit")

    environment, removed = _external_process_environment()

    assert removed == 1
    assert environment["PATH"] == str(normal)
    assert "PYTHONHOME" not in environment
    assert "PYTHONPATH" not in environment
    assert "_PYI_PARENT_PROCESS_LEVEL" not in environment
    assert "TK_LIBRARY" not in environment
    assert "BEESYNC_RUNTIME_TOKEN" not in environment


def test_autocad_launch_spec_requires_windows_shell_shortcut(monkeypatch, tmp_path):
    executable = tmp_path / "AutoCAD 2020" / "acad.exe"
    executable.parent.mkdir()
    executable.write_bytes(b"exe")
    installation = AutocadInstallation(
        2020, executable, "AutoCAD.Application.23.1"
    )
    monkeypatch.setattr("app.tool.desktop_bim.sys.platform", "not-windows")

    assert _autocad_launch_spec(installation) is None


def test_autocad_launch_spec_accepts_matching_user_shortcut_without_start_menu_scan(
    monkeypatch, tmp_path
):
    executable = tmp_path / "AutoCAD 2020" / "acad.exe"
    executable.parent.mkdir()
    executable.write_bytes(b"exe")
    shortcut = tmp_path / "user-provided-autocad-2020.lnk"
    shortcut.write_bytes(b"shortcut")
    installation = AutocadInstallation(
        2020, executable, "AutoCAD.Application.23.1"
    )
    opened_shortcuts = []

    class FakeShell:
        def CreateShortcut(self, path):
            opened_shortcuts.append(path)
            return SimpleNamespace(TargetPath=str(executable))

    fake_pythoncom = ModuleType("pythoncom")
    fake_pythoncom.CoInitialize = lambda: None
    fake_pythoncom.CoUninitialize = lambda: None
    fake_client = ModuleType("win32com.client")
    fake_client.Dispatch = lambda _: FakeShell()
    fake_win32com = ModuleType("win32com")
    fake_win32com.client = fake_client
    monkeypatch.setitem(sys.modules, "pythoncom", fake_pythoncom)
    monkeypatch.setitem(sys.modules, "win32com", fake_win32com)
    monkeypatch.setitem(sys.modules, "win32com.client", fake_client)
    monkeypatch.setattr("app.tool.desktop_bim.sys.platform", "win32")
    monkeypatch.setattr(
        "app.tool.desktop_bim._autocad_start_menu_roots",
        lambda: (_ for _ in ()).throw(AssertionError("Start Menu must not be scanned")),
    )

    spec = _autocad_launch_spec(installation, str(shortcut))

    assert spec is not None
    assert spec.source == "user_verified_shell_shortcut"
    assert spec.shortcut_path == shortcut
    assert opened_shortcuts == [str(shortcut)]


def test_autocad_launcher_uses_matching_windows_shell_shortcut(monkeypatch, tmp_path):
    executable = tmp_path / "AutoCAD 2020" / "acad.exe"
    executable.parent.mkdir()
    executable.write_bytes(b"exe")
    spec = AutocadLaunchSpec(
        executable=executable,
        arguments=(),
        working_directory=None,
        source="official_shell_shortcut",
        shortcut_path=tmp_path / "AutoCAD 2020 - 简体中文.lnk",
    )
    opened = []

    monkeypatch.setattr("app.tool.desktop_bim.sys.platform", "win32")
    monkeypatch.setattr(
        "app.tool.desktop_bim.os.startfile",
        lambda path: opened.append(path),
        raising=False,
    )

    assert _shell_open_executable(spec) is None
    assert opened == [str(spec.shortcut_path)]


def test_autocad_2026_running_does_not_prevent_starting_2020(monkeypatch, tmp_path):
    acad_2020 = tmp_path / "AutoCAD 2020" / "acad.exe"
    acad_2020.parent.mkdir()
    acad_2020.write_bytes(b"exe")
    launched = []

    monkeypatch.setattr(
        "app.tool.desktop_bim._discover_autocad_installations",
        lambda version: [
            AutocadInstallation(version, acad_2020, "AutoCAD.Application.23.1")
        ],
    )
    monkeypatch.setattr("app.tool.desktop_bim._running_autocad_instances", lambda *_: [])
    monkeypatch.setattr("app.tool.desktop_bim._running_processes", lambda _: [2026])
    launch_spec = AutocadLaunchSpec(
        executable=acad_2020,
        arguments=(),
        working_directory=None,
        source="official_shell_shortcut",
        shortcut_path=tmp_path / "AutoCAD 2020 - 简体中文.lnk",
    )
    monkeypatch.setattr(
        "app.tool.desktop_bim._autocad_launch_spec", lambda _: launch_spec
    )
    monkeypatch.setattr(
        "app.tool.desktop_bim._shell_open_executable",
        lambda spec: launched.append(spec),
    )

    async def ready(installations, timeout):
        assert installations[0].executable == acad_2020
        return {
            "pid": 2020,
            "hwnd": 88,
            "prog_id": "AutoCAD.Application.23.1",
            "executable": str(acad_2020),
        }

    monkeypatch.setattr("app.tool.desktop_bim._wait_for_autocad_com_ready", ready)

    result = asyncio.run(EnsureAutocadRunning().execute())
    payload = json.loads(result.output)
    assert launched == [launch_spec]
    assert payload == {
        "status": "ready",
        "version": 2020,
        "pid": 2020,
        "hwnd": 88,
        "prog_id": "AutoCAD.Application.23.1",
        "application": str(acad_2020),
        "launch_source": "official_shell_shortcut",
        "launch_arguments": [],
        "working_directory": None,
        "launch_shortcut": str(launch_spec.shortcut_path),
        "reused": False,
    }


def test_autocad_uses_user_supplied_shortcut_when_startup_is_required(monkeypatch, tmp_path):
    executable = tmp_path / "AutoCAD 2020" / "acad.exe"
    executable.parent.mkdir()
    executable.write_bytes(b"exe")
    shortcut = tmp_path / "AutoCAD 2020 - 简体中文.lnk"
    shortcut.write_bytes(b"shortcut")
    installation = AutocadInstallation(
        2020, executable, "AutoCAD.Application.23.1"
    )
    supplied_paths = []
    launched = []
    launch_spec = AutocadLaunchSpec(
        executable=executable,
        arguments=(),
        working_directory=None,
        source="user_verified_shell_shortcut",
        shortcut_path=shortcut,
    )

    monkeypatch.setattr(
        "app.tool.desktop_bim._discover_autocad_installations", lambda _: [installation]
    )
    monkeypatch.setattr("app.tool.desktop_bim._running_autocad_instances", lambda *_: [])
    monkeypatch.setattr("app.tool.desktop_bim._running_processes", lambda _: [])
    monkeypatch.setattr(
        "app.tool.desktop_bim._autocad_launch_spec",
        lambda selected, supplied: supplied_paths.append((selected, supplied)) or launch_spec,
    )
    monkeypatch.setattr(
        "app.tool.desktop_bim._shell_open_executable",
        lambda spec: launched.append(spec),
    )

    async def ready(_installations, _timeout):
        return {
            "pid": 2020,
            "hwnd": 88,
            "prog_id": "AutoCAD.Application.23.1",
            "executable": str(executable),
        }

    monkeypatch.setattr("app.tool.desktop_bim._wait_for_autocad_com_ready", ready)

    result = asyncio.run(
        EnsureAutocadRunning().execute(shortcut_path=str(shortcut))
    )
    payload = json.loads(result.output)

    assert supplied_paths == [(installation, str(shortcut))]
    assert launched == [launch_spec]
    assert payload["launch_source"] == "user_verified_shell_shortcut"
    assert payload["launch_shortcut"] == str(shortcut)


def test_autocad_window_is_not_ready_until_versioned_com_matches(monkeypatch, tmp_path):
    acad_2020 = tmp_path / "acad.exe"
    acad_2020.write_bytes(b"exe")
    installation = AutocadInstallation(
        2020, acad_2020, "AutoCAD.Application.23.1"
    )
    monkeypatch.setattr(
        "app.tool.desktop_bim._discover_autocad_installations", lambda _: [installation]
    )
    monkeypatch.setattr(
        "app.tool.desktop_bim._running_autocad_instances",
        lambda *_: [
            {
                "pid": 2020,
                "hwnd": 88,
                "prog_id": "AutoCAD.Application.23.1",
                "executable": str(acad_2020),
            }
        ],
    )

    async def not_ready(*_args):
        return None

    monkeypatch.setattr("app.tool.desktop_bim._wait_for_autocad_com_ready", not_ready)
    result = asyncio.run(EnsureAutocadRunning().execute(timeout_seconds=1))
    payload = json.loads(result.output)
    assert payload["status"] == "user_action_required"
    assert "COM" in payload["message"]


def test_autocad_com_status_accepts_start_page_without_document_or_quiescence(
    monkeypatch,
):
    """The launcher may attach while AutoCAD only shows its start page.

    A start page has no ActiveDocument yet, and querying IsQuiescent there can
    raise in some Tianzheng-enabled installations.  Those are DWG-operation
    checks, not prerequisites for attaching an already-running AutoCAD 2020.
    """

    class StartPageApp:
        Version = "23.1s (LMS Tech)"
        HWND = 88

        @property
        def ActiveDocument(self):
            raise AssertionError("startup attachment must not access ActiveDocument")

        def GetAcadState(self):
            raise AssertionError("startup attachment must not access IsQuiescent")

    initialized = []
    fake_pythoncom = ModuleType("pythoncom")
    fake_pythoncom.CoInitialize = lambda: initialized.append("initialize")
    fake_pythoncom.CoUninitialize = lambda: initialized.append("uninitialize")
    fake_client = ModuleType("win32com.client")
    fake_client.GetActiveObject = lambda prog_id: (
        StartPageApp()
        if prog_id == "AutoCAD.Application.23.1"
        else (_ for _ in ()).throw(AssertionError("unexpected ProgID"))
    )
    fake_win32com = ModuleType("win32com")
    fake_win32com.client = fake_client
    fake_win32process = ModuleType("win32process")
    fake_win32process.GetWindowThreadProcessId = lambda hwnd: (1, 2020)
    monkeypatch.setitem(sys.modules, "pythoncom", fake_pythoncom)
    monkeypatch.setitem(sys.modules, "win32com", fake_win32com)
    monkeypatch.setitem(sys.modules, "win32com.client", fake_client)
    monkeypatch.setitem(sys.modules, "win32process", fake_win32process)

    status = _autocad_com_status("AutoCAD.Application.23.1")

    assert status == {"pid": 2020, "hwnd": 88, "version": "23.1s (LMS Tech)"}
    assert initialized == ["initialize", "uninitialize"]


def test_ensure_autocad_reuses_running_2020_start_page(monkeypatch, tmp_path):
    """A visible 2020 start page should be reused before any new launch."""

    executable = tmp_path / "AutoCAD 2020" / "acad.exe"
    executable.parent.mkdir()
    executable.write_bytes(b"exe")
    installation = AutocadInstallation(
        2020, executable, "AutoCAD.Application.23.1"
    )
    running = {
        "pid": 2020,
        "hwnd": 88,
        "prog_id": "AutoCAD.Application.23.1",
        "executable": str(executable),
    }
    launched = []

    monkeypatch.setattr(
        "app.tool.desktop_bim._discover_autocad_installations", lambda _: [installation]
    )
    monkeypatch.setattr(
        "app.tool.desktop_bim._running_autocad_instances", lambda *_: [running]
    )

    async def attached(installations, timeout):
        assert installations == [installation]
        assert timeout == 300
        return running

    monkeypatch.setattr("app.tool.desktop_bim._wait_for_autocad_com_ready", attached)
    monkeypatch.setattr(
        "app.tool.desktop_bim._shell_open_executable",
        lambda spec: launched.append(spec),
    )

    result = asyncio.run(EnsureAutocadRunning().execute())
    payload = json.loads(result.output)

    assert launched == []
    assert payload == {
        "status": "ready",
        "version": 2020,
        "pid": 2020,
        "hwnd": 88,
        "prog_id": "AutoCAD.Application.23.1",
        "reused": True,
    }


def test_autocad_wait_rejects_com_pid_for_a_different_instance(monkeypatch, tmp_path):
    """A versioned COM object from another PID must never be attached to 2020."""

    executable = tmp_path / "AutoCAD 2020" / "acad.exe"
    executable.parent.mkdir()
    executable.write_bytes(b"exe")
    installation = AutocadInstallation(
        2020, executable, "AutoCAD.Application.23.1"
    )
    running_polls = iter(
        (
            [
                {
                    "pid": 2020,
                    "hwnd": 88,
                    "prog_id": "AutoCAD.Application.23.1",
                    "executable": str(executable),
                }
            ],
            [],
        )
    )
    monkeypatch.setattr(
        "app.tool.desktop_bim._running_autocad_instances", lambda *_: next(running_polls)
    )
    monkeypatch.setattr(
        "app.tool.desktop_bim._autocad_com_status",
        lambda _: {"pid": 2026, "hwnd": 99, "version": "23.1s (LMS Tech)"},
    )

    async def no_wait(_seconds):
        return None

    monkeypatch.setattr("app.tool.desktop_bim.asyncio.sleep", no_wait)

    ready = asyncio.run(_wait_for_autocad_com_ready([installation], 1))

    assert ready is None


def test_autocad_does_not_fallback_to_2026_when_2020_missing(monkeypatch):
    monkeypatch.setattr("app.tool.desktop_bim._discover_autocad_installations", lambda _: [])
    monkeypatch.setattr("app.tool.desktop_bim._running_autocad_instances", lambda *_: [])
    monkeypatch.setattr("app.tool.desktop_bim._running_processes", lambda _: [2026])
    result = asyncio.run(EnsureAutocadRunning().execute())
    payload = json.loads(result.output)
    assert payload["status"] == "user_action_required"
    assert payload["version"] == 2020


def test_sz_ifc_requires_manual_load_without_starting_process(monkeypatch, tmp_path):
    ifc = tmp_path / "project_AR.ifc"
    ifc.write_bytes(b"ifc")
    monkeypatch.setattr("app.tool.desktop_bim._prepared_sz_ifc_windows", lambda _: [])
    result = asyncio.run(SzIfcOpenModel().execute(str(ifc)))
    payload = json.loads(result.output)
    assert payload["status"] == "user_action_required"
    assert str(ifc) in payload["message"]


def test_sz_ifc_accepts_one_manually_prepared_window(monkeypatch, tmp_path):
    ifc = tmp_path / "project_AR.ifc"
    ifc.write_bytes(b"ifc")
    monkeypatch.setattr(
        "app.tool.desktop_bim._prepared_sz_ifc_windows",
        lambda _: [{"pid": 4321, "handle": 9876, "title": ""}],
    )
    result = asyncio.run(SzIfcOpenModel().execute(str(ifc)))
    payload = json.loads(result.output)

    assert payload["status"] == "ready"
    assert payload["open_method"] == "manual_existing"
    assert payload["pid"] == 4321
    assert payload["window_handle"] == 9876


def test_is_autocad_busy_matches_transient_com_errors():
    from q_agent_function_module.ohresult.CAD_Git_Coordinates.my_code.room_coordinates.dwg_room_extractor_main import (
        _is_autocad_busy,
    )

    assert _is_autocad_busy(Exception("Open.SendCommand")) is True
    assert _is_autocad_busy(Exception("(-2147418111, '被呼叫方拒绝接收呼叫。')")) is True
    assert _is_autocad_busy(Exception("0x80010108: The object invoked has disconnected")) is True
    assert _is_autocad_busy(Exception("RPC_S_CALL_FAILED")) is True
    assert _is_autocad_busy(Exception("Random other error")) is False

