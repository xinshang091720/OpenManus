import asyncio
import json
import os
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

from app.tool.windows_app import (
    ApplicationSelectionRequired,
    InstalledApplication,
    RevitLaunchApplication,
    RevitLaunchSpec,
    RevitLaunchVersionedModel,
    RevitInstallation,
    RevitProcess,
    WindowsOpenApplication,
    _basic_file_info_version,
    _launch_revit,
    _revit_launch_spec,
    discover_revit_installations,
    select_compatible_revit,
)


def test_revit_version_selection_prefers_exact_then_lowest_newer():
    installations = [
        RevitInstallation(2026, Path("C:/Revit2026/Revit.exe")),
        RevitInstallation(2022, Path("C:/Revit2022/Revit.exe")),
        RevitInstallation(2024, Path("C:/Revit2024/Revit.exe")),
    ]
    assert select_compatible_revit(2024, installations).version == 2024
    assert select_compatible_revit(2023, installations).version == 2024


def test_revit_version_selection_rejects_only_older_installations():
    installations = [RevitInstallation(2022, Path("C:/Revit2022/Revit.exe"))]
    try:
        select_compatible_revit(2024, installations)
        assert False, "expected no compatible Revit installation"
    except LookupError as error:
        assert "2024" in str(error)


def test_basic_file_info_version_reads_newer_structured_header():
    # Revit 2020+ places the version in the first fixed header field, instead
    # of spelling out "Autodesk Revit <year>" in the stream text.
    raw = b"\r\x00" + (b"\x00" * 12)
    raw += (4).to_bytes(4, "little") + "2020".encode("utf-16-le")
    raw += "20190327_2315(x64)".encode("utf-16-le")

    assert _basic_file_info_version(raw) == 2020


def test_basic_file_info_version_reads_named_format_field():
    raw = "\x00Format: 2025\x00Last Save Path: C:\\Projects\\2026\\model.rvt".encode("utf-16-le")

    assert _basic_file_info_version(raw) == 2025


def test_basic_file_info_version_keeps_legacy_revit_build_layout():
    raw = "Autodesk Revit 2018 (Build: 20170223_1515(x64))".encode("utf-16-le")

    assert _basic_file_info_version(raw) == 2018


def test_basic_file_info_version_does_not_treat_an_unlabelled_year_as_version():
    raw = "Last Save Path: C:\\Projects\\2026\\model.rvt".encode("utf-16-le")

    assert _basic_file_info_version(raw) is None


def test_generic_windows_launcher_uses_resolved_command(monkeypatch):
    class Process:
        pid = 1234

    monkeypatch.setattr("app.tool.windows_app.resolve_application", lambda *_: ["C:/Apps/example.exe"])
    monkeypatch.setattr("app.tool.windows_app.subprocess.Popen", lambda command, **_: Process())
    result = asyncio.run(WindowsOpenApplication().execute("example", arguments=["--safe"]))
    payload = json.loads(result.output)
    assert payload["pid"] == 1234
    assert payload["command"] == ["C:/Apps/example.exe", "--safe"]


def test_revit_discovery_uses_windows_uninstall_install_location(monkeypatch, tmp_path):
    install = tmp_path / "Revit 2024"
    install.mkdir()
    (install / "Revit.exe").write_text("placeholder", encoding="utf-8")
    monkeypatch.setattr("app.tool.windows_app._registry_views", lambda: ())
    monkeypatch.setattr(
        "app.tool.windows_app._uninstall_entries",
        lambda: [
            {
                "display_name": "Autodesk Revit 2024",
                "display_version": "24.0",
                "install_location": str(install),
                "display_icon": "",
            }
        ],
    )
    assert discover_revit_installations() == [RevitInstallation(2024, install / "Revit.exe")]


def test_revit_discovery_reads_nested_revit_installation_location(monkeypatch, tmp_path):
    install = tmp_path / "Revit 2020"
    install.mkdir()
    executable = install / "Revit.exe"
    executable.write_text("placeholder", encoding="utf-8")

    class Key:
        def __init__(self, name):
            self.name = name

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    root = Key("root")
    version_key = Key("2020")
    revit_key = Key("REVIT-05:0804")

    def open_key(parent, name, *_args):
        if parent is fake_winreg.HKEY_LOCAL_MACHINE and name == r"SOFTWARE\Autodesk\Revit":
            return root
        if parent is root and name == "2020":
            return version_key
        if parent is version_key and name == "REVIT-05:0804":
            return revit_key
        raise OSError(name)

    def enum_key(key, index):
        entries = {
            "root": ["2020"],
            "2020": ["REVIT-05:0804"],
            "REVIT-05:0804": [],
        }[key.name]
        if index >= len(entries):
            raise OSError(index)
        return entries[index]

    def query_value(key, name):
        if key is revit_key and name == "InstallationLocation":
            return str(install), 1
        raise OSError(name)

    fake_winreg = SimpleNamespace(
        HKEY_LOCAL_MACHINE=object(),
        OpenKey=open_key,
        EnumKey=enum_key,
        QueryValueEx=query_value,
    )
    monkeypatch.setattr("app.tool.windows_app.winreg", fake_winreg)
    monkeypatch.setattr("app.tool.windows_app._registry_views", lambda: (0,))
    monkeypatch.setattr("app.tool.windows_app._uninstall_entries", lambda: [])

    assert discover_revit_installations() == [RevitInstallation(2020, executable)]


def test_revit_launch_spec_falls_back_to_installation_context_without_pywin32(
    monkeypatch, tmp_path
):
    installation_directory = tmp_path / "Revit 2020"
    installation_directory.mkdir()
    executable = installation_directory / "Revit.exe"
    executable.write_bytes(b"exe")
    monkeypatch.setattr("app.tool.windows_app.sys.platform", "win32")
    monkeypatch.setitem(sys.modules, "pythoncom", None)
    monkeypatch.setitem(sys.modules, "win32com", None)

    spec = _revit_launch_spec(RevitInstallation(2020, executable))

    assert spec == RevitLaunchSpec(
        executable=executable,
        arguments=(),
        working_directory=installation_directory,
        source="direct_installation",
    )


def test_revit_launch_spec_reads_matching_official_shortcut_language(monkeypatch, tmp_path):
    installation_directory = tmp_path / "Revit 2020"
    installation_directory.mkdir()
    executable = installation_directory / "Revit.exe"
    executable.write_bytes(b"exe")
    start_menu = tmp_path / "Start Menu"
    start_menu.mkdir()
    shortcut_path = start_menu / "Autodesk Revit 2020.lnk"
    shortcut_path.write_bytes(b"shortcut")
    calls = []

    class FakeShell:
        def CreateShortcut(self, path):
            assert path == str(shortcut_path)
            return SimpleNamespace(TargetPath=str(executable), Arguments="/language CHS")

    fake_pythoncom = ModuleType("pythoncom")
    fake_pythoncom.CoInitialize = lambda: calls.append("initialize")
    fake_pythoncom.CoUninitialize = lambda: calls.append("uninitialize")
    fake_client = ModuleType("win32com.client")
    fake_client.Dispatch = lambda _: FakeShell()
    fake_win32com = ModuleType("win32com")
    fake_win32com.client = fake_client
    monkeypatch.setitem(sys.modules, "pythoncom", fake_pythoncom)
    monkeypatch.setitem(sys.modules, "win32com", fake_win32com)
    monkeypatch.setitem(sys.modules, "win32com.client", fake_client)
    monkeypatch.setattr("app.tool.windows_app.sys.platform", "win32")
    monkeypatch.setattr("app.tool.windows_app._revit_start_menu_roots", lambda: [start_menu])

    spec = _revit_launch_spec(RevitInstallation(2020, executable))

    assert spec == RevitLaunchSpec(
        executable=executable,
        arguments=("/language", "CHS"),
        working_directory=installation_directory,
        source="official_shortcut_language",
        shortcut_path=shortcut_path,
    )
    assert calls == ["initialize", "uninitialize"]


def test_revit_launch_uses_install_context_and_clean_environment(monkeypatch, tmp_path):
    installation_directory = tmp_path / "Revit 2020"
    installation_directory.mkdir()
    executable = installation_directory / "Revit.exe"
    executable.write_bytes(b"exe")
    bundle = tmp_path / "_internal"
    bundle.mkdir()
    normal = tmp_path / "normal"
    normal.mkdir()
    spec = RevitLaunchSpec(
        executable=executable,
        arguments=("/language", "CHS"),
        working_directory=installation_directory,
        source="official_shortcut_language",
    )
    captured = {}

    class Process:
        pid = 2468

    def popen(command, **kwargs):
        captured["command"] = command
        captured.update(kwargs)
        return Process()

    monkeypatch.setattr("app.tool.windows_app._revit_launch_spec", lambda _: spec)
    monkeypatch.setattr("app.tool.windows_app.subprocess.Popen", popen)
    monkeypatch.setattr("app.tool.windows_app.sys._MEIPASS", str(bundle), raising=False)
    monkeypatch.setenv("PATH", os.pathsep.join((str(bundle), str(normal))))
    monkeypatch.setenv("PYTHONHOME", "embedded")
    monkeypatch.setenv("PYTHONPATH", "embedded-modules")
    monkeypatch.setenv("_PYI_ARCHIVE_FILE", "runtime.pkg")
    monkeypatch.setenv("PYINSTALLER_RESET_ENVIRONMENT", "1")
    monkeypatch.setenv("TCL_LIBRARY", "runtime-tcl")
    monkeypatch.setenv("BEESYNC_LLM_API_KEY", "must-not-reach-revit")
    monkeypatch.setenv("OPENMANUS_RUNTIME_TOKEN", "must-not-reach-revit")
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", "runtime-playwright")

    process, result_spec = _launch_revit(RevitInstallation(2020, executable))

    assert process.pid == 2468
    assert result_spec == spec
    assert captured["command"] == [str(executable), "/language", "CHS"]
    assert captured["cwd"] == str(installation_directory)
    assert captured["close_fds"] is True
    assert captured["env"]["PATH"] == str(normal)
    assert "PYTHONHOME" not in captured["env"]
    assert "PYTHONPATH" not in captured["env"]
    assert "_PYI_ARCHIVE_FILE" not in captured["env"]
    assert "PYINSTALLER_RESET_ENVIRONMENT" not in captured["env"]
    assert "TCL_LIBRARY" not in captured["env"]
    assert "BEESYNC_LLM_API_KEY" not in captured["env"]
    assert "OPENMANUS_RUNTIME_TOKEN" not in captured["env"]
    assert "PLAYWRIGHT_BROWSERS_PATH" not in captured["env"]


def test_revit_application_launcher_uses_requested_installed_version(monkeypatch):
    class Process:
        pid = 4321

    monkeypatch.setattr(
        "app.tool.windows_app.discover_revit_installations",
        lambda: [RevitInstallation(2024, Path("C:/Revit2024/Revit.exe"))],
    )
    monkeypatch.setattr("app.tool.windows_app.running_revit_processes", lambda: [])
    monkeypatch.setattr(
        "app.tool.windows_app._revit_launch_spec",
        lambda installation: RevitLaunchSpec(
            executable=installation.executable,
            arguments=(),
            working_directory=installation.executable.parent,
            source="direct_installation",
        ),
    )
    monkeypatch.setattr("app.tool.windows_app.subprocess.Popen", lambda command, **_: Process())
    result = asyncio.run(RevitLaunchApplication().execute(version=2024))
    payload = json.loads(result.output)
    assert payload["revit_version"] == 2024
    assert payload["pid"] == 4321


def test_revit_launcher_refuses_to_create_a_second_instance(monkeypatch):
    monkeypatch.setattr(
        "app.tool.windows_app.running_revit_processes", lambda: [RevitProcess(4321)]
    )
    monkeypatch.setattr(
        "app.tool.windows_app.subprocess.Popen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not launch")),
    )

    result = asyncio.run(RevitLaunchApplication().execute())
    payload = json.loads(result.output)
    assert payload["status"] == "user_action_required"
    assert payload["running_revit_pids"] == [4321]


def test_versioned_model_launcher_refuses_to_create_a_second_instance(monkeypatch, tmp_path):
    model = tmp_path / "project_AR.rvt"
    model.write_text("placeholder", encoding="utf-8")
    monkeypatch.setattr(
        "app.tool.windows_app.running_revit_processes", lambda: [RevitProcess(4321)]
    )
    monkeypatch.setattr(
        "app.tool.windows_app.subprocess.Popen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not launch")),
    )

    result = asyncio.run(RevitLaunchVersionedModel().execute(str(model)))
    payload = json.loads(result.output)
    assert payload["status"] == "user_action_required"
    assert payload["model_path"] == str(model)


def test_versioned_model_launcher_delegates_to_two_stage_project_opener(monkeypatch, tmp_path):
    model = tmp_path / "project_AR.rvt"
    model.write_text("placeholder", encoding="utf-8")
    captured = {}

    async def open_project(_self, path, discipline=None, allow_upgrade=False, timeout_seconds=7200):
        captured.update(
            path=path,
            discipline=discipline,
            allow_upgrade=allow_upgrade,
            timeout_seconds=timeout_seconds,
        )
        return RevitLaunchApplication().success_response({"status": "ready"})

    monkeypatch.setattr("app.tool.windows_app.running_revit_processes", lambda: [])
    monkeypatch.setattr("app.tool.desktop_bim.RevitOpenProjectModel.execute", open_project)
    monkeypatch.setattr(
        "app.tool.windows_app._launch_revit",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("versioned launcher must not pass a model to Revit.exe")
        ),
    )

    result = asyncio.run(RevitLaunchVersionedModel().execute(str(model), allow_upgrade=True))
    payload = json.loads(result.output)

    assert payload["status"] == "ready"
    assert captured == {
        "path": str(model),
        "discipline": None,
        "allow_upgrade": True,
        "timeout_seconds": 7200,
    }


def test_generic_revit_launcher_with_model_argument_uses_versioned_flow(monkeypatch, tmp_path):
    executable = tmp_path / "Revit.exe"
    executable.write_text("placeholder", encoding="utf-8")
    model = tmp_path / "project_AR.rvt"
    model.write_text("placeholder", encoding="utf-8")
    captured = []

    async def open_versioned(_self, path, allow_upgrade=False):
        captured.append((path, allow_upgrade))
        return RevitLaunchApplication().success_response({"status": "ready"})

    monkeypatch.setattr(
        "app.tool.windows_app.RevitLaunchVersionedModel.execute", open_versioned
    )
    monkeypatch.setattr(
        "app.tool.windows_app.subprocess.Popen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("must not pass an RVT directly to Revit.exe")
        ),
    )

    result = asyncio.run(
        WindowsOpenApplication().execute(str(executable), arguments=[str(model)])
    )
    payload = json.loads(result.output)

    assert payload["status"] == "ready"
    assert captured == [(str(model), False)]


def test_display_name_revit_launcher_with_model_argument_uses_versioned_flow(monkeypatch, tmp_path):
    executable = tmp_path / "Revit.exe"
    executable.write_text("placeholder", encoding="utf-8")
    model = tmp_path / "project_AR.rvt"
    model.write_text("placeholder", encoding="utf-8")
    captured = []

    async def open_versioned(_self, path, allow_upgrade=False):
        captured.append((path, allow_upgrade))
        return RevitLaunchApplication().success_response({"status": "ready"})

    monkeypatch.setattr(
        "app.tool.windows_app.RevitLaunchVersionedModel.execute", open_versioned
    )
    monkeypatch.setattr(
        "app.tool.windows_app.resolve_application", lambda *_args: [str(executable)]
    )
    monkeypatch.setattr(
        "app.tool.windows_app.subprocess.Popen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("must not pass an RVT directly to a resolved Revit.exe")
        ),
    )

    result = asyncio.run(
        WindowsOpenApplication().execute("Autodesk Revit 2020", arguments=[str(model)])
    )
    payload = json.loads(result.output)

    assert payload["status"] == "ready"
    assert captured == [(str(model), False)]


def test_revit_application_launcher_requests_selection_for_multiple_versions(monkeypatch):
    monkeypatch.setattr("app.tool.windows_app.running_revit_processes", lambda: [])
    monkeypatch.setattr(
        "app.tool.windows_app.discover_revit_installations",
        lambda: [
            RevitInstallation(2024, Path("C:/Revit2024/Revit.exe")),
            RevitInstallation(2025, Path("C:/Revit2025/Revit.exe")),
        ],
    )
    result = asyncio.run(RevitLaunchApplication().execute())
    payload = json.loads(result.output)
    assert payload["status"] == "selection_required"
    assert [candidate["display_version"] for candidate in payload["candidates"]] == ["2024", "2025"]


def test_generic_launcher_returns_candidates_when_application_is_ambiguous(monkeypatch):
    candidates = [
        InstalledApplication("Example App 2024", "2024", Path("C:/Example/2024/app.exe")),
        InstalledApplication("Example App 2025", "2025", Path("C:/Example/2025/app.exe")),
    ]
    monkeypatch.setattr(
        "app.tool.windows_app.resolve_application",
        lambda *_: (_ for _ in ()).throw(ApplicationSelectionRequired("Example App", candidates)),
    )
    result = asyncio.run(WindowsOpenApplication().execute("Example App"))
    payload = json.loads(result.output)
    assert payload["status"] == "selection_required"
    assert len(payload["candidates"]) == 2
