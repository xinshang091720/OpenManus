"""Deterministic launch and readiness tools for local BIM desktop software."""

from __future__ import annotations

import asyncio
import math
import os
import re
import shlex
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import Field

from app.revit.client import RevitApiClient, RevitApiError
from app.logger import logger
from app.revit.operations import call_revit_operation
from app.tool.base import BaseTool, ToolResult
from app.tool.windows_app import (
    _launch_revit,
    _registry_value,
    _uninstall_entries,
    discover_revit_installations,
    read_revit_model_version,
    running_revit_processes,
)

try:
    import winreg
except ImportError:  # pragma: no cover
    winreg = None


def _configured_desktop_timeout_seconds(default_seconds: int = 86400) -> int:
    raw = os.environ.get("BEESYNC_DELIVERY_TIMEOUT_SECONDS")
    if raw is not None and raw.strip():
        try:
            return int(raw.strip())
        except ValueError:
            pass
    return default_seconds


_MAX_TIMEOUT_SECONDS = _configured_desktop_timeout_seconds()
_DISCIPLINE_ALIASES = {
    "建筑": {"AR", "A", "建筑"},
    "结构": {"ST", "S", "FS", "SS", "结构"},
    "通风空调": {"AC", "M", "通风空调", "暖通"},
    "给排水": {"PD", "P", "给排水"},
    "电气": {"EL", "E", "T", "电气", "智能化"},
}


class RevitStartupExited(RuntimeError):
    """The Revit process started for this request exited before becoming ready."""

    def __init__(self, process_id: int, exit_code: int, stage: str = "application_startup"):
        self.process_id = process_id
        self.exit_code = exit_code
        self.stage = stage
        super().__init__(f"Revit process {process_id} exited with code {exit_code}")


def _bounded_timeout(value: int | float) -> int:
    return max(1, min(int(value), _MAX_TIMEOUT_SECONDS))


def _tokenize_filename(path: Path) -> set[str]:
    return {
        token.upper()
        for token in re.split(r"[_\-.\s]+", path.stem)
        if token.strip()
    }


def _model_candidates(path: Path, discipline: str | None) -> list[Path]:
    if path.is_file():
        if path.suffix.lower() != ".rvt":
            raise ValueError("path 必须是 .rvt 文件或包含 RVT 的文件夹")
        return [path]
    if not path.is_dir():
        raise ValueError("path 必须是存在的绝对 RVT 文件或文件夹")
    models = sorted(
        item
        for item in path.rglob("*.rvt")
        if item.is_file() and not re.search(r"\.\d{4}\.rvt$", item.name, re.IGNORECASE)
    )
    if not models:
        raise LookupError(f"指定文件夹中没有找到 RVT 模型：{path}")
    if discipline:
        aliases = _DISCIPLINE_ALIASES.get(discipline, {discipline.upper(), discipline})
        matching = [
            model
            for model in models
            if _tokenize_filename(model) & {alias.upper() for alias in aliases}
            or any(alias in model.stem for alias in aliases if not alias.isascii())
        ]
        if matching:
            models = matching
    if len(models) > 1 and not discipline:
        architectural = [
            model for model in models if _tokenize_filename(model) & {"AR", "A"}
        ]
        if len(architectural) == 1:
            return architectural
    return models


def _selection_result(message: str, candidates: list[Path]) -> dict[str, Any]:
    return {
        "status": "selection_required",
        "message": message,
        "candidates": [
            {"display_name": item.name, "display_version": "RVT", "path": str(item)}
            for item in candidates
        ],
    }


def _is_existing_model_switch_error(error: RevitApiError) -> bool:
    """Identify the plugin response seen when Revit cannot switch documents.

    The installed add-in reports this particular document-activation failure as
    an HTTP-successful response with its own 500 code. Keep the predicate
    deliberately narrow so unrelated Revit/plugin failures retain their
    existing handling.
    """
    return (
        error.endpoint == "/OpenRevitFile"
        and error.http_status == 200
        and error.plugin_code == 500
        and "failed opening a revit file" in str(error).casefold()
    )


def _running_processes(image_name: str) -> list[int]:
    if sys.platform != "win32":
        return []
    try:
        import psutil

        return sorted(
            process.pid
            for process in psutil.process_iter(["name"])
            if (process.info.get("name") or "").casefold() == image_name.casefold()
        )
    except Exception:
        return []


def _process_has_visible_window(process_id: int) -> bool:
    try:
        import win32gui
        import win32process
    except ImportError:
        return True
    visible = []

    def visit(hwnd, _):
        try:
            _, owner = win32process.GetWindowThreadProcessId(hwnd)
            if owner == process_id and win32gui.IsWindowVisible(hwnd):
                visible.append(hwnd)
        except Exception:
            return

    win32gui.EnumWindows(visit, None)
    return bool(visible)


def _visible_window_handle(process_id: int) -> int | None:
    try:
        import win32gui
        import win32process
    except ImportError:
        return None
    handles: list[int] = []

    def visit(hwnd, _):
        try:
            _, owner = win32process.GetWindowThreadProcessId(hwnd)
            if owner == process_id and win32gui.IsWindowVisible(hwnd):
                handles.append(hwnd)
        except Exception:
            return

    win32gui.EnumWindows(visit, None)
    return handles[0] if handles else None


async def _wait_for_visible_process(process_ids: list[int], timeout_seconds: int) -> int | None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        for process_id in process_ids:
            if _process_has_visible_window(process_id):
                return process_id
        await asyncio.sleep(min(1, max(0.01, deadline - time.monotonic())))
    return None


def _revit_model_window_handle(process_id: int, model: Path) -> int | None:
    """Return the Revit document window only after the requested model is shown."""
    try:
        import win32gui
        import win32process
    except ImportError:
        return _visible_window_handle(process_id)
    expected_name = model.stem.casefold()
    matches: list[int] = []

    def visit(hwnd, _):
        try:
            _, owner = win32process.GetWindowThreadProcessId(hwnd)
            if owner != process_id or not win32gui.IsWindowVisible(hwnd):
                return
            title = win32gui.GetWindowText(hwnd).casefold()
            if expected_name in title:
                matches.append(hwnd)
        except Exception:
            return

    win32gui.EnumWindows(visit, None)
    return matches[0] if matches else None


async def _wait_for_revit_application_ready(
    process_id: int,
    client: Any,
    timeout_seconds: int,
    launched_process: Any | None = None,
) -> dict[str, Any] | None:
    """Wait for a newly started, model-free Revit and its bridge to be ready."""
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if launched_process is not None:
            poll = getattr(launched_process, "poll", None)
            exit_code = poll() if callable(poll) else None
            if exit_code is not None:
                raise RevitStartupExited(process_id, int(exit_code))
        if not _process_has_visible_window(process_id):
            await asyncio.sleep(min(1, max(0.01, deadline - time.monotonic())))
            continue
        handle = _visible_window_handle(process_id)
        status_method = getattr(client, "plugin_status", None)
        if not callable(status_method):
            return {"pid": process_id, "hwnd": handle, "plugin_status": "unverified"}
        remaining = max(0.1, deadline - time.monotonic())
        try:
            status = await status_method(timeout_seconds=min(1.5, remaining))
        except Exception:
            status = {"status": "unavailable"}
        if status.get("status") == "ready":
            if status.get("revit_connected", True) and status.get(
                "ready_for_requests", True
            ):
                return {"pid": process_id, "hwnd": handle, "plugin_status": "ready"}
        elif status.get("status") == "unsupported":
            return {
                "pid": process_id,
                "hwnd": handle,
                "plugin_status": "legacy_health_unsupported",
            }
        await asyncio.sleep(min(1, max(0.01, deadline - time.monotonic())))
    return None


async def _wait_for_revit_model_ready(
    process_ids: list[int],
    model: Path,
    client: Any,
    timeout_seconds: int,
    launched_process: Any | None = None,
) -> dict[str, Any] | None:
    """Wait for the document title and the local Revit bridge to be usable."""
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if launched_process is not None:
            poll = getattr(launched_process, "poll", None)
            exit_code = poll() if callable(poll) else None
            if exit_code is not None:
                raise RevitStartupExited(process_ids[0], int(exit_code), "model_open")
        for process_id in process_ids:
            handle = _revit_model_window_handle(process_id, model)
            if handle is None:
                continue
            status_method = getattr(client, "plugin_status", None)
            if not callable(status_method):
                return {"pid": process_id, "hwnd": handle, "plugin_status": "unverified"}
            remaining = max(0.1, deadline - time.monotonic())
            try:
                status = await status_method(timeout_seconds=min(1.5, remaining))
            except Exception:
                status = {"status": "unavailable"}
            if status.get("status") == "ready":
                if status.get("revit_connected", True) and status.get(
                    "ready_for_requests", True
                ):
                    return {
                        "pid": process_id,
                        "hwnd": handle,
                        "plugin_status": "ready",
                    }
            elif status.get("status") == "unsupported":
                # Older plugin builds have no /Health endpoint. A responsive
                # bridge plus the exact document title is sufficient evidence
                # that the next serialized Revit operation can proceed.
                return {
                    "pid": process_id,
                    "hwnd": handle,
                    "plugin_status": "legacy_health_unsupported",
                }
        await asyncio.sleep(min(1, max(0.01, deadline - time.monotonic())))
    return None


def _association_command(extension: str) -> list[str] | None:
    if winreg is None:
        return None
    prog_id = None
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            rf"Software\Microsoft\Windows\CurrentVersion\Explorer\FileExts\{extension}\UserChoice",
        ) as key:
            prog_id = _registry_value(key, "ProgId")
    except OSError:
        pass
    if not prog_id:
        try:
            with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, extension) as key:
                prog_id = _registry_value(key, "")
        except OSError:
            return None
    if not isinstance(prog_id, str) or not prog_id:
        return None
    try:
        with winreg.OpenKey(
            winreg.HKEY_CLASSES_ROOT, rf"{prog_id}\shell\open\command"
        ) as key:
            command = _registry_value(key, "")
    except OSError:
        return None
    if not isinstance(command, str) or not command.strip():
        return None
    return shlex.split(command, posix=False)


def _command_executable(command: list[str] | None) -> Path | None:
    if not command:
        return None
    candidate = Path(os.path.expandvars(command[0].strip().strip('"')))
    return candidate if candidate.is_file() else None


def _windows_command_executable(command: str | None) -> Path | None:
    """Extract an executable from a Windows command, including unquoted paths.

    Autodesk's ``LocalServer32`` value is commonly written as
    ``D:\\AutoCAD 2020\\acad.exe /Automation`` without quotes.  ``shlex`` treats
    the spaces as argument separators and therefore loses the real executable.
    """
    if not isinstance(command, str) or not command.strip():
        return None
    expanded = os.path.expandvars(command.strip())
    if expanded.startswith('"'):
        closing_quote = expanded.find('"', 1)
        executable_text = expanded[1:closing_quote] if closing_quote > 1 else ""
    else:
        match = re.match(r"(?i)^(.+?\.exe)(?:\s|$)", expanded)
        executable_text = match.group(1) if match else ""
    if not executable_text:
        return None
    candidate = Path(executable_text)
    return candidate if candidate.is_file() else None


@dataclass(frozen=True)
class AutocadInstallation:
    version: int
    executable: Path
    prog_id: str


@dataclass(frozen=True)
class AutocadLaunchSpec:
    executable: Path
    arguments: tuple[str, ...]
    working_directory: Path | None
    source: str
    shortcut_path: Path | None = None


_AUTOCAD_PROG_IDS = {2020: "AutoCAD.Application.23.1"}


def _autocad_year(text: str, file_version: str = "") -> int | None:
    match = re.search(r"\b(20\d{2})\b", text)
    if match:
        return int(match.group(1))
    version = file_version.split(".")
    if version[:2] == ["23", "1"]:
        return 2020
    if version[:2] == ["25", "1"]:
        return 2026
    return None


def _autocad_com_executable(prog_id: str) -> Path | None:
    if winreg is None:
        return None
    try:
        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, rf"{prog_id}\CLSID") as key:
            clsid = _registry_value(key, "")
        with winreg.OpenKey(
            winreg.HKEY_CLASSES_ROOT, rf"CLSID\{clsid}\LocalServer32"
        ) as key:
            return _windows_command_executable(str(_registry_value(key, "")))
    except OSError:
        return None


def _discover_autocad_installations(version: int = 2020) -> list[AutocadInstallation]:
    """Resolve one requested AutoCAD version without using file association or disk scans."""
    discovered: dict[str, AutocadInstallation] = {}
    prog_id = _AUTOCAD_PROG_IDS.get(version)
    if prog_id:
        executable = _autocad_com_executable(prog_id)
        if executable and executable.name.casefold() == "acad.exe":
            discovered[str(executable).casefold()] = AutocadInstallation(
                version, executable, prog_id
            )
    for entry in _uninstall_entries():
        name = entry["display_name"].casefold()
        if "autodesk autocad" not in name and not name.startswith("autocad"):
            continue
        location = Path(os.path.expandvars(entry["install_location"].strip().strip('"')))
        executable = location / "acad.exe"
        if not executable.is_file():
            continue
        file_version, _ = _windows_file_metadata(executable)
        installed_year = _autocad_year(
            " ".join((entry["display_name"], entry["display_version"], str(executable))),
            file_version,
        )
        if installed_year == version and prog_id:
            discovered[str(executable).casefold()] = AutocadInstallation(
                version, executable, prog_id
            )
    return sorted(discovered.values(), key=lambda item: str(item.executable).casefold())


def _autocad_start_menu_roots() -> list[Path]:
    """Return only the bounded Windows Start Menu roots; never scan disks."""
    roots: list[Path] = []
    for environment_name in ("PROGRAMDATA", "APPDATA"):
        base = os.environ.get(environment_name)
        if not base:
            continue
        root = Path(base) / "Microsoft" / "Windows" / "Start Menu" / "Programs"
        if root.is_dir():
            roots.append(root)
    return roots


def _autocad_launch_spec(
    installation: AutocadInstallation, user_shortcut_path: str | None = None
) -> AutocadLaunchSpec | None:
    """Return a verified shortcut for the registry-validated installation.

    AutoCAD 2020 with Tianzheng is known to start successfully from its official
    Start Menu link but can exit during direct CreateProcess startup.  The link
    is therefore used only after its target is proven to be this exact registry
    installation.  A user-provided ``.lnk`` takes priority and avoids any Start
    Menu lookup, but it is accepted only when its target is that same AutoCAD
    2020 executable.
    """
    if sys.platform != "win32":
        return None
    try:
        import pythoncom
        import win32com.client
    except ImportError:
        return None

    expected = os.path.normcase(str(installation.executable.resolve()))
    candidates: list[Path] = []
    source = "official_shell_shortcut"
    if user_shortcut_path:
        candidate = Path(user_shortcut_path)
        if (
            not candidate.is_absolute()
            or candidate.suffix.casefold() != ".lnk"
            or not candidate.is_file()
        ):
            logger.warning(
                "Ignoring invalid user-provided AutoCAD shortcut path=%s",
                user_shortcut_path,
            )
            return None
        candidates.append(candidate)
        source = "user_verified_shell_shortcut"
    else:
        for root in _autocad_start_menu_roots():
            candidates.extend(
                sorted(
                    (
                        item
                        for item in root.rglob("*.lnk")
                        if "autocad" in item.stem.casefold()
                        and str(installation.version) in item.stem
                    ),
                    key=lambda item: str(item).casefold(),
                )
            )
    if not candidates:
        return None

    pythoncom.CoInitialize()
    shell = None
    try:
        shell = win32com.client.Dispatch("WScript.Shell")
        for shortcut_path in candidates:
            try:
                shortcut = shell.CreateShortcut(str(shortcut_path))
                target = Path(os.path.expandvars(str(shortcut.TargetPath).strip()))
                if (
                    target.is_file()
                    and target.name.casefold() == "acad.exe"
                    and os.path.normcase(str(target.resolve())) == expected
                ):
                    return AutocadLaunchSpec(
                        executable=installation.executable,
                        arguments=(),
                        working_directory=None,
                        source=source,
                        shortcut_path=shortcut_path,
                    )
            except Exception as error:
                logger.warning(
                    "Ignoring unreadable AutoCAD shortcut path={} error={}",
                    str(shortcut_path),
                    error,
                )
    finally:
        shell = None
        pythoncom.CoUninitialize()
    return None


def _running_autocad_instances(
    version: int, installations: list[AutocadInstallation]
) -> list[dict[str, Any]]:
    if sys.platform != "win32":
        return []
    install_paths = {
        os.path.normcase(str(item.executable.resolve())): item for item in installations
    }
    found: list[dict[str, Any]] = []
    try:
        import psutil

        for process in psutil.process_iter(["pid", "name", "exe"]):
            if (process.info.get("name") or "").casefold() != "acad.exe":
                continue
            executable = process.info.get("exe") or ""
            normalized = os.path.normcase(str(Path(executable).resolve())) if executable else ""
            installation = install_paths.get(normalized)
            file_version, _ = _windows_file_metadata(Path(executable)) if executable else ("", "")
            actual_year = installation.version if installation else _autocad_year(executable, file_version)
            if actual_year != version:
                continue
            found.append(
                {
                    "pid": int(process.info["pid"]),
                    "hwnd": _visible_window_handle(int(process.info["pid"])),
                    "executable": executable,
                    "prog_id": (installation.prog_id if installation else _AUTOCAD_PROG_IDS[version]),
                }
            )
    except Exception:
        return []
    return sorted(found, key=lambda item: item["pid"])


def _shell_open_executable(spec: AutocadLaunchSpec) -> None:
    """Use Windows Shell to execute the exact official AutoCAD shortcut."""
    if sys.platform != "win32" or spec.shortcut_path is None:
        raise OSError("AutoCAD 2020 official shortcut is unavailable")
    logger.info(
        "Launching AutoCAD through Windows Shell executable={} shortcut={} source={} frozen={}",
        str(spec.executable),
        str(spec.shortcut_path),
        spec.source,
        bool(getattr(sys, "frozen", False)),
    )
    os.startfile(str(spec.shortcut_path))


def _autocad_com_status(prog_id: str) -> dict[str, Any] | None:
    """Read one versioned AutoCAD COM registration without retaining the object.

    This is a *startup/attachment* probe, not a DWG-operation readiness probe.
    AutoCAD 2020 exposes a valid COM application and HWND while showing its start
    page, before ``ActiveDocument`` exists.  Requiring a document here caused an
    already manually opened CAD to be treated as unavailable indefinitely.  The
    room workflow performs its own document and busy-state checks immediately
    before it opens and processes each DWG.
    """
    try:
        import pythoncom
        import win32com.client
        import win32process
    except ImportError:
        return None
    pythoncom.CoInitialize()
    try:
        app = win32com.client.GetActiveObject(prog_id)
        version = str(getattr(app, "Version", ""))
        hwnd = int(app.HWND)
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        if not version.startswith("23.1"):
            return None
        return {"pid": int(pid), "hwnd": hwnd, "version": version}
    except Exception:
        return None
    finally:
        pythoncom.CoUninitialize()


async def _wait_for_autocad_com_ready(
    installations: list[AutocadInstallation], timeout_seconds: int
) -> dict[str, Any] | None:
    """Wait until AutoCAD 2020 has a visible window and matching versioned COM PID."""
    deadline = time.monotonic() + _bounded_timeout(timeout_seconds)
    prog_id = _AUTOCAD_PROG_IDS[2020]
    process_seen = False
    while time.monotonic() < deadline:
        running = _running_autocad_instances(2020, installations)
        running_by_pid = {item["pid"]: item for item in running}
        process_seen = process_seen or bool(running_by_pid)
        status = await asyncio.to_thread(_autocad_com_status, prog_id)
        if status and status["pid"] in running_by_pid and status["hwnd"]:
            selected = running_by_pid[status["pid"]]
            return {
                "pid": status["pid"],
                "hwnd": status["hwnd"],
                "prog_id": selected["prog_id"],
                "executable": selected["executable"],
            }
        # A process that appeared and then vanished cannot become COM-ready.
        if process_seen and not running_by_pid:
            return None
        await asyncio.sleep(min(0.5, max(0.01, deadline - time.monotonic())))
    return None


def _windows_file_metadata(path: Path) -> tuple[str, str]:
    try:
        import win32api

        fixed = win32api.GetFileVersionInfo(str(path), "\\")
        ms = fixed["FileVersionMS"]
        ls = fixed["FileVersionLS"]
        version = f"{ms >> 16}.{ms & 0xFFFF}.{ls >> 16}.{ls & 0xFFFF}"
        description = ""
        translations = win32api.GetFileVersionInfo(
            str(path), r"\VarFileInfo\Translation"
        )
        for language, codepage in translations:
            try:
                description = win32api.GetFileVersionInfo(
                    str(path),
                    rf"\StringFileInfo\{language:04x}{codepage:04x}\FileDescription",
                )
                if description:
                    break
            except Exception:
                continue
        return version, str(description or "")
    except Exception:
        return "", ""


def _running_revit_version(process_id: int) -> int | None:
    try:
        import psutil

        executable = Path(psutil.Process(process_id).exe())
    except Exception:
        return None
    match = re.search(r"20\d{2}", str(executable))
    if match:
        return int(match.group())
    file_version, _ = _windows_file_metadata(executable)
    major = file_version.split(".", 1)[0]
    if major.isdigit():
        # Autodesk file major versions use 18 for Revit 2018, 24 for 2024.
        value = int(major)
        if 10 <= value <= 99:
            return 2000 + value
    return None


def _cbims_windows(process_ids: set[int] | None = None) -> list[dict[str, Any]]:
    import win32gui
    import win32process

    windows: list[dict[str, Any]] = []

    def visit(hwnd, _):
        if not win32gui.IsWindowVisible(hwnd):
            return
        class_name = win32gui.GetClassName(hwnd)
        if not class_name.startswith("HwndWrapper[CBIMS.Manager;;"):
            return
        _, owner = win32process.GetWindowThreadProcessId(hwnd)
        if process_ids and owner not in process_ids:
            return
        windows.append(
            {
                "handle": hwnd,
                "pid": owner,
                "title": win32gui.GetWindowText(hwnd).strip(),
            }
        )

    win32gui.EnumWindows(visit, None)
    return windows


def _cbims_process_snapshot() -> dict[int, dict[str, Any]]:
    if sys.platform != "win32":
        return {}
    try:
        import psutil

        snapshot: dict[int, dict[str, Any]] = {}
        for process in psutil.process_iter(["pid", "name", "ppid", "create_time"]):
            if (process.info.get("name") or "").casefold() != "cbims.manager.exe":
                continue
            pid = int(process.info["pid"])
            snapshot[pid] = {
                "pid": pid,
                "parent_pid": process.info.get("ppid"),
                "created_at": process.info.get("create_time"),
            }
        return snapshot
    except Exception:
        return {pid: {"pid": pid} for pid in _running_processes("CBIMS.Manager.exe")}


def _sz_ifc_window_ready(hwnd: int, ifc_filename: str, *, full_scan: bool) -> bool:
    from pywinauto import Application

    app = Application(backend="uia").connect(handle=hwnd, timeout=3)
    window = app.window(handle=hwnd)
    model = window.child_window(title=ifc_filename, found_index=0)
    tab = window.child_window(title="模型检查", control_type="TabItem", found_index=0)
    if model.exists(timeout=0.2) and tab.exists(timeout=0.2) and tab.is_enabled():
        return True
    if not full_scan:
        return False
    texts = []
    for element in window.descendants():
        try:
            text = element.window_text().strip()
            if text:
                texts.append((text, element))
        except Exception:
            continue
    model_visible = any(ifc_filename == text for text, _ in texts)
    tab_enabled = any(
        text == "模型检查" and element.is_enabled() for text, element in texts
    )
    return model_visible and tab_enabled


def _prepared_sz_ifc_windows(ifc_filename: str) -> list[dict[str, Any]]:
    """Return existing SZ-IFC windows that already loaded the requested model."""
    process_ids = set(_cbims_process_snapshot())
    matches: list[dict[str, Any]] = []
    for window in _cbims_windows(process_ids or None):
        if window["title"] == "StartWindow":
            continue
        try:
            if _sz_ifc_window_ready(window["handle"], ifc_filename, full_scan=True):
                matches.append(window)
        except Exception:
            continue
    return matches


class RevitOpenProjectModel(BaseTool):
    name: str = "revit_open_project_model"
    description: str = (
        "Opens one explicit RVT or selects a unique RVT inside a user-supplied folder. "
        "It reads the model version, uses installed Revit registry data, and asks only for "
        "ambiguous model selection or a version upgrade."
    )
    parameters: dict = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Absolute RVT file or project folder."},
            "discipline": {"type": "string", "description": "Optional user-stated discipline."},
            "allow_upgrade": {"type": "boolean", "default": False, "description": "Set true only after the user accepts opening an older model in newer Revit."},
            "timeout_seconds": {"type": "integer", "default": 7200},
        },
        "required": ["path"],
        "additionalProperties": False,
    }
    client: Any = Field(default_factory=RevitApiClient, exclude=True)

    async def execute(
        self,
        path: str,
        discipline: str | None = None,
        allow_upgrade: bool = False,
        timeout_seconds: int = 7200,
    ) -> ToolResult:
        try:
            source = Path(path)
            if not source.is_absolute():
                raise ValueError("path 必须是绝对路径")
            models = _model_candidates(source, discipline)
            if len(models) != 1:
                return self.success_response(
                    _selection_result("找到多个可能的 Revit 模型，请选择目标模型。", models)
                )
            model = models[0]
            model_version = read_revit_model_version(model)
            running = running_revit_processes()
            if len(running) > 1:
                return self.success_response(
                    {
                        "status": "user_action_required",
                        "message": "检测到多个 Revit 实例，请保留目标实例后再继续。",
                        "model_path": str(model),
                    }
                )
            if running:
                running_version = _running_revit_version(running[0].pid)
                if running_version and running_version < model_version:
                    return self.success_response(
                        {
                            "status": "user_action_required",
                            "message": (
                                f"当前 Revit {running_version} 不能打开 Revit {model_version} 模型；"
                                "请关闭当前实例并打开对应版本后回复“已打开”。"
                            ),
                            "model_path": str(model),
                        }
                    )
                if running_version and running_version > model_version and not allow_upgrade:
                    return self.success_response(
                        {
                            "status": "selection_required",
                            "message": (
                                f"当前 Revit {running_version} 打开 Revit {model_version} 模型会升级模型，"
                                "请确认是否继续。"
                            ),
                            "candidates": [
                                {
                                    "display_name": f"当前 Revit {running_version}",
                                    "display_version": str(running_version),
                                    "model_path": str(model),
                                }
                            ],
                        }
                    )
                try:
                    response = await call_revit_operation(
                        self.client,
                        "OpenRevitFile",
                        self.client.open_revit_file,
                        str(model),
                    )
                except RevitApiError as error:
                    if not _is_existing_model_switch_error(error):
                        raise
                    return self.success_response(
                        {
                            "status": "user_action_required",
                            "message": (
                                "当前 Revit 实例无法安全切换到目标模型。为保护未保存的更改，"
                                "系统没有自动关闭任何模型；请在 Revit 中先保存并关闭当前模型，"
                                "再重新发起打开请求。"
                            ),
                            "model_path": str(model),
                            "pid": running[0].pid,
                            "next_action": "save_and_close_current_model",
                        }
                    )
                ready = await _wait_for_revit_model_ready(
                    [running[0].pid],
                    model,
                    self.client,
                    _bounded_timeout(timeout_seconds),
                )
                if ready is None:
                    return self.success_response(
                        {
                            "status": "timed_out_unknown",
                            "message": "Revit 已收到打开模型请求，但模型或插件未在等待时间内进入可继续执行状态。",
                            "model_path": str(model),
                            "pid": running[0].pid,
                        }
                    )
                return self.success_response(
                    {
                        "status": "ready",
                        "model_path": str(model),
                        "model_version": model_version,
                        "pid": ready["pid"],
                        "window_handle": ready["hwnd"],
                        "plugin_status": ready["plugin_status"],
                        "message": response.get("msg", "模型已打开")
                        + "；模型与 Revit 插件已经就绪，可直接继续下一步。",
                    }
                )

            installations = discover_revit_installations()
            selected = next((item for item in installations if item.version == model_version), None)
            if selected is None:
                newer = [item for item in installations if item.version > model_version]
                if newer:
                    selected = min(newer, key=lambda item: item.version)
                    if not allow_upgrade:
                        return self.success_response(
                            {
                                "status": "selection_required",
                                "message": (
                                    f"模型版本为 Revit {model_version}，本机只有 Revit {selected.version} "
                                    "可以打开；保存后会升级模型，请确认是否继续。"
                                ),
                                "candidates": [
                                    {
                                        "display_name": f"Autodesk Revit {selected.version}",
                                        "display_version": str(selected.version),
                                        "executable": str(selected.executable),
                                        "model_path": str(model),
                                    }
                                ],
                            }
                        )
                else:
                    raise LookupError(f"本机没有可打开 Revit {model_version} 模型的版本。")
            deadline = time.monotonic() + _bounded_timeout(timeout_seconds)
            process, launch_spec = _launch_revit(selected)
            application_ready = await _wait_for_revit_application_ready(
                process.pid,
                self.client,
                _bounded_timeout(timeout_seconds),
                launched_process=process,
            )
            if application_ready is None:
                return self.success_response(
                    {
                        "status": "user_action_required",
                        "message": (
                            "Revit 已启动，但未在等待时间内完成授权和插件就绪；"
                            "尚未尝试打开目标模型。"
                        ),
                        "stage": "application_startup",
                        "model_open_attempted": False,
                        "model_path": str(model),
                        "pid": process.pid,
                        "next_action": "resolve_revit_startup_and_retry",
                    }
                )
            remaining_timeout = deadline - time.monotonic()
            if remaining_timeout <= 0:
                return self.success_response(
                    {
                        "status": "user_action_required",
                        "message": (
                            "Revit 宸插惎鍔ㄥ苟灏辂€锛屼絾瓒呰繃浜嗘娆℃墦寮€妯″瀷鐨勭瓑寰呮椂闂达紱"
                            "灏氭湭灏濊瘯鎵撳紑鐩爣妯″瀷銆傝鍐嶆纭骞堕噸璇曘€?"
                        ),
                        "stage": "model_open",
                        "model_open_attempted": False,
                        "model_path": str(model),
                        "pid": process.pid,
                        "next_action": "retry_model_open",
                    }
                )
            response = await call_revit_operation(
                self.client,
                "OpenRevitFile",
                self.client.open_revit_file,
                str(model),
                timeout_seconds=remaining_timeout,
            )
            ready = await _wait_for_revit_model_ready(
                [process.pid],
                model,
                self.client,
                max(1, math.ceil(deadline - time.monotonic())),
                launched_process=process,
            )
            if ready is None:
                return self.success_response(
                    {
                        "status": "timed_out_unknown",
                        "message": "等待 Revit 模型和插件进入可继续执行状态超时，最终状态未知。",
                        "model_path": str(model),
                        "pid": process.pid,
                    }
                )
            return self.success_response(
                {
                    "status": "ready",
                    "model_path": str(model),
                "model_version": model_version,
                "revit_version": selected.version,
                "revit_executable": str(selected.executable),
                "launch_source": launch_spec.source,
                "launch_arguments": list(launch_spec.arguments),
                "working_directory": str(launch_spec.working_directory),
                "launch_shortcut": (
                    str(launch_spec.shortcut_path)
                    if launch_spec.shortcut_path is not None
                    else None
                ),
                "pid": ready["pid"],
                "window_handle": ready["hwnd"],
                "plugin_status": ready["plugin_status"],
                "message": "模型与 Revit 插件已经就绪，可直接继续下一步。",
                }
            )
        except RevitStartupExited as error:
            stage_message = (
                "Revit 在打开目标模型前异常退出"
                if error.stage == "application_startup"
                else "Revit 在打开目标模型过程中异常退出"
            )
            return self.success_response(
                {
                    "status": "user_action_required",
                    "message": (
                        f"{stage_message}（退出代码 {error.exit_code}），"
                        "请处理启动或许可提示后手动打开目标模型，再回复“已打开”。"
                    ),
                    "stage": error.stage,
                    "model_path": str(model),
                    "pid": error.process_id,
                    "exit_code": error.exit_code,
                }
            )
        except (OSError, ValueError, LookupError, RuntimeError) as error:
            return self.success_response(
                {
                    "status": "user_action_required",
                    "message": f"无法自动打开 Revit 模型：{error} 请手动打开目标模型后回复“已打开”。",
                    "path": path,
                }
            )


class EnsureAutocadRunning(BaseTool):
    name: str = "ensure_autocad_running"
    description: str = (
        "Reuses AutoCAD 2020 or launches a registry-validated Windows shortcut "
        "for the Tianzheng room workflow. When the user supplies a .lnk path, "
        "the tool verifies that it targets the registered AutoCAD 2020 before "
        "using it. Other AutoCAD versions remain open, and no disk scan is performed."
    )
    parameters: dict = {
        "type": "object",
        "properties": {
            "version": {"type": "integer", "default": 2020},
            "allow_parallel_different_version": {"type": "boolean", "default": True},
            "timeout_seconds": {"type": "integer", "default": 300},
            "shortcut_path": {
                "type": "string",
                "description": "Optional absolute AutoCAD 2020 .lnk supplied by the user.",
            },
        },
        "additionalProperties": False,
    }

    async def execute(
        self,
        version: int = 2020,
        allow_parallel_different_version: bool = True,
        timeout_seconds: int = 300,
        shortcut_path: str | None = None,
    ) -> ToolResult:
        if sys.platform != "win32":
            return self.fail_response("ensure_autocad_running 仅支持 Windows")
        if version != 2020:
            return self.fail_response("房间自动化当前仅支持 AutoCAD 2020")
        installations = _discover_autocad_installations(version)
        logger.info(
            "AutoCAD discovery requested_version={} candidates={}",
            version,
            [str(item.executable) for item in installations],
        )
        running = _running_autocad_instances(version, installations)
        if running:
            ready = await _wait_for_autocad_com_ready(installations, timeout_seconds)
            if ready:
                os.environ["BEESYNC_AUTOCAD_2020_PID"] = str(ready["pid"])
                return self.success_response(
                    {
                        "status": "ready",
                        "version": version,
                        "pid": ready["pid"],
                        "hwnd": ready["hwnd"],
                        "prog_id": ready["prog_id"],
                        "reused": True,
                    }
                )
            return self.success_response(
                {
                    "status": "user_action_required",
                    "message": "AutoCAD 2020 窗口已出现，但版本化 COM 尚未就绪或程序已经退出。请手动启动 AutoCAD 2020，等待天正自行加载完成后再继续。",
                    "version": version,
                }
            )
        if _running_processes("acad.exe") and not allow_parallel_different_version:
            return self.success_response(
                {
                    "status": "user_action_required",
                    "message": "检测到其他 AutoCAD 版本；请手动打开 AutoCAD 2020 后继续。",
                    "version": version,
                }
            )
        if not installations:
            return self.success_response(
                {
                    "status": "user_action_required",
                    "message": "未能从 Windows 注册信息定位 AutoCAD 2020，请手动打开 AutoCAD 2020，完成后回复“已打开”。",
                    "version": version,
                }
            )
        try:
            selected = installations[0]
            requested_shortcut = (shortcut_path or "").strip()
            if requested_shortcut:
                launch_spec = await asyncio.to_thread(
                    _autocad_launch_spec, selected, requested_shortcut
                )
            else:
                launch_spec = await asyncio.to_thread(_autocad_launch_spec, selected)
            if launch_spec is None:
                if requested_shortcut:
                    message = (
                        "提供的 AutoCAD 快捷方式无法验证为当前注册表定位的 "
                        "AutoCAD 2020。请检查快捷方式目标后手动打开 AutoCAD 2020。"
                    )
                else:
                    message = (
                        "已定位 AutoCAD 2020 安装，但未找到目标与该安装一致的官方启动快捷方式。"
                        "请手动使用“AutoCAD 2020 - 简体中文”快捷方式打开后回复“已打开”。"
                    )
                return self.success_response(
                    {
                        "status": "user_action_required",
                        "message": message,
                        "version": version,
                        "application": str(selected.executable),
                        "shortcut_path": requested_shortcut or None,
                    }
                )
            await asyncio.to_thread(_shell_open_executable, launch_spec)
            ready = await _wait_for_autocad_com_ready(installations, timeout_seconds)
        except OSError as error:
            return self.success_response(
                {
                    "status": "user_action_required",
                    "message": f"AutoCAD 2020 启动失败：{error}。请手动打开后回复“已打开”。",
                    "version": version,
                }
            )
        if ready is None:
            exit_code = None
            launched_pid = None
            running_after_launch = _running_autocad_instances(version, installations)
            logger.warning(
                "AutoCAD did not become COM-ready launched_pid={} exit_code={} running_instances={}",
                launched_pid,
                exit_code,
                [item.get("pid") for item in running_after_launch],
            )
            if running_after_launch:
                message = (
                    "AutoCAD 2020 窗口已经出现，但版本化 COM 尚未就绪。"
                    "请处理启动窗口并等待天正自行加载完成，然后回复“已打开”。"
                )
            else:
                message = (
                    "AutoCAD 2020 未在限定时间内进入可操作状态，"
                    "请手动打开或处理启动窗口后回复“已打开”。"
                )
            return self.success_response(
                {
                    "status": "user_action_required",
                    "message": message,
                    "version": version,
                    "launched_pid": launched_pid,
                    "exit_code": exit_code,
                    "launch_source": launch_spec.source,
                    "launch_arguments": list(launch_spec.arguments),
                    "working_directory": (
                        str(launch_spec.working_directory)
                        if launch_spec.working_directory is not None
                        else None
                    ),
                    "launch_shortcut": (
                        str(launch_spec.shortcut_path)
                        if launch_spec.shortcut_path is not None
                        else None
                    ),
                }
            )
        os.environ["BEESYNC_AUTOCAD_2020_PID"] = str(ready["pid"])
        return self.success_response(
            {
                "status": "ready",
                "version": version,
                "pid": ready["pid"],
                "hwnd": ready["hwnd"],
                "prog_id": ready["prog_id"],
                "application": str(selected.executable),
                "launch_source": launch_spec.source,
                "launch_arguments": list(launch_spec.arguments),
                "working_directory": (
                    str(launch_spec.working_directory)
                    if launch_spec.working_directory is not None
                    else None
                ),
                "launch_shortcut": (
                    str(launch_spec.shortcut_path)
                    if launch_spec.shortcut_path is not None
                    else None
                ),
                "reused": False,
            }
        )


class SzIfcOpenModel(BaseTool):
    name: str = "sz_ifc_open_model"
    description: str = (
        "Checks whether the user has manually loaded one exact IFC in SZ-IFC. "
        "It never launches SZ-IFC or changes Windows file associations."
    )
    parameters: dict = {
        "type": "object",
        "properties": {
            "ifc_file_path": {"type": "string"},
            "timeout_seconds": {"type": "integer", "default": 7200},
        },
        "required": ["ifc_file_path"],
        "additionalProperties": False,
    }

    async def execute(
        self, ifc_file_path: str, timeout_seconds: int = 7200
    ) -> ToolResult:
        if sys.platform != "win32":
            return self.fail_response("sz_ifc_open_model 仅支持 Windows")
        target = Path(ifc_file_path)
        if not target.is_absolute() or not target.is_file() or target.suffix.lower() != ".ifc":
            return self.fail_response("ifc_file_path 必须是存在的绝对 .ifc 文件")
        matches = await asyncio.to_thread(_prepared_sz_ifc_windows, target.name)
        if not matches:
            return self.success_response(
                {
                    "status": "user_action_required",
                    "message": f"请在 SZ-IFC 自检工具中手动打开此文件：{target}。确认模型文件名已经显示且“模型检查”可以进入后，回复“已加载，可以继续”。",
                    "ifc_file_path": str(target),
                }
            )
        if len(matches) > 1:
            return self.success_response(
                {
                    "status": "user_action_required",
                    "message": "检测到多个 SZ-IFC 窗口加载了同一个 IFC，请只保留或激活本次要质检的窗口后再继续。",
                    "ifc_file_path": str(target),
                }
            )
        ready = matches[0]
        logger.info(
            "SZ-IFC manual model ready ifc={} pid={} hwnd={}",
            str(target),
            ready["pid"],
            ready["handle"],
        )
        return self.success_response(
            {
                "status": "ready",
                "ifc_file_path": str(target),
                "pid": ready["pid"],
                "window_handle": ready["handle"],
                "open_method": "manual_existing",
            }
        )
