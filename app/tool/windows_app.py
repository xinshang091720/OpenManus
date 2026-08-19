"""Deterministic Windows application launch tools."""

from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
import sys
import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

from app.tool.base import BaseTool, ToolResult

try:  # Imported lazily by the Revit tool at runtime, but available on Windows.
    import winreg
except ImportError:  # pragma: no cover - only relevant outside Windows.
    winreg = None


_REVIT_ROOT = r"SOFTWARE\Autodesk\Revit"
_APP_PATHS_ROOT = r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths"
_UNINSTALL_ROOT = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"
_VERSION_PATTERN = re.compile(r"\b(20\d{2})\b")
_REVIT_YEAR_PATTERN = re.compile(r"20\d{2}")
_REVIT_FORMAT_PATTERN = re.compile(r"\b(?:revit\s+)?format\s*[:=]\s*(20\d{2})\b", re.IGNORECASE)
_REVIT_LEGACY_BUILD_PATTERN = re.compile(
    r"\b(?:autodesk\s+)?revit\s+(20\d{2})\b", re.IGNORECASE
)


@dataclass(frozen=True)
class RevitInstallation:
    version: int
    executable: Path


@dataclass(frozen=True)
class InstalledApplication:
    display_name: str
    display_version: str
    executable: Path


@dataclass(frozen=True)
class RevitProcess:
    """A running Revit desktop process discovered without creating one."""

    pid: int


@dataclass(frozen=True)
class RevitLaunchSpec:
    """The verified context used to start one locally installed Revit release."""

    executable: Path
    arguments: tuple[str, ...]
    working_directory: Path
    source: str
    shortcut_path: Path | None = None


def running_revit_processes() -> list[RevitProcess]:
    """Return running Revit processes; never use this to start an application."""
    if sys.platform != "win32":
        return []
    try:
        completed = subprocess.run(
            ["tasklist", "/FO", "CSV", "/NH", "/FI", "IMAGENAME eq Revit.exe"],
            capture_output=True,
            check=False,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except OSError:
        return []
    processes: list[RevitProcess] = []
    for row in csv.reader(completed.stdout.splitlines()):
        if len(row) < 2 or row[0].casefold() != "revit.exe":
            continue
        try:
            processes.append(RevitProcess(pid=int(row[1].replace(",", ""))))
        except ValueError:
            continue
    return processes


def _external_process_environment() -> tuple[dict[str, str], int]:
    """Return an environment that does not leak the frozen Runtime into Revit."""
    environment = os.environ.copy()
    removed_path_entries = 0
    bundle_value = getattr(sys, "_MEIPASS", None)
    if bundle_value:
        try:
            bundle_root = Path(bundle_value).resolve()
        except OSError:
            bundle_root = Path(bundle_value)
        clean_entries: list[str] = []
        for entry in environment.get("PATH", "").split(os.pathsep):
            if not entry:
                continue
            try:
                candidate = Path(entry).resolve()
                inside_bundle = candidate == bundle_root or bundle_root in candidate.parents
            except OSError:
                inside_bundle = False
            if inside_bundle:
                removed_path_entries += 1
            else:
                clean_entries.append(entry)
        environment["PATH"] = os.pathsep.join(clean_entries)
    # Runtime-only configuration must not reach Revit or its native licensing
    # children.  Revit receives the desktop baseline assembled by RuntimeManager
    # instead, while the MCP child retains the BEESYNC settings it needs.
    runtime_prefixes = (
        "BEESYNC_",
        "OPENMANUS_",
        "PLAYWRIGHT_",
        "_PYI_",
        "PYI_",
        "PYINSTALLER_",
        "TCL_",
        "TK_",
    )
    for name in tuple(environment):
        normalized_name = name.upper()
        if normalized_name in {"PYTHONHOME", "PYTHONPATH"} or normalized_name.startswith(
            runtime_prefixes
        ):
            environment.pop(name, None)
    return environment, removed_path_entries


def _revit_start_menu_roots() -> list[Path]:
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


def _normalized_path(path: Path) -> str:
    try:
        path = path.resolve()
    except OSError:
        pass
    return os.path.normcase(str(path))


def _revit_shortcut_language_arguments(arguments: object) -> tuple[str, ...]:
    """Keep only a verified shortcut's language switch, if it has one."""
    if not isinstance(arguments, str) or not arguments.strip():
        return ()
    try:
        tokens = shlex.split(arguments, posix=False)
    except ValueError:
        return ()
    for index, token in enumerate(tokens):
        option = token.strip('"').casefold()
        if option in {"/language", "-language"} and index + 1 < len(tokens):
            value = tokens[index + 1]
            if not value.startswith(("/", "-")):
                return (token, value)
        if re.match(r"^[/-]language(?:=|:).+", option):
            return (token,)
    return ()


def _revit_launch_spec(installation: RevitInstallation) -> RevitLaunchSpec:
    """Use the matching official Start Menu language switch when available.

    Direct CreateProcess remains the fallback: not every Revit installation has
    a Start Menu shortcut and pywin32 is optional in the Runtime.
    """
    fallback = RevitLaunchSpec(
        executable=installation.executable,
        arguments=(),
        working_directory=installation.executable.parent,
        source="direct_installation",
    )
    if sys.platform != "win32":
        return fallback
    try:
        import pythoncom
        import win32com.client
    except ImportError:
        return fallback

    candidates: list[Path] = []
    for root in _revit_start_menu_roots():
        try:
            candidates.extend(
                item
                for item in root.rglob("*.lnk")
                if "revit" in item.stem.casefold() and str(installation.version) in str(item)
            )
        except OSError:
            continue
    if not candidates:
        return fallback

    expected = _normalized_path(installation.executable)
    pythoncom.CoInitialize()
    shell = None
    shortcut = None
    result = fallback
    try:
        shell = win32com.client.Dispatch("WScript.Shell")
        for shortcut_path in sorted(candidates, key=lambda item: str(item).casefold()):
            shortcut = None
            try:
                shortcut = shell.CreateShortcut(str(shortcut_path))
                target_value = getattr(shortcut, "TargetPath", "")
                target = Path(os.path.expandvars(str(target_value).strip().strip('"')))
                if (
                    target.name.casefold() != "revit.exe"
                    or not target.is_file()
                    or _normalized_path(target) != expected
                ):
                    continue
                arguments = _revit_shortcut_language_arguments(
                    getattr(shortcut, "Arguments", "")
                )
                if arguments:
                    result = RevitLaunchSpec(
                        executable=installation.executable,
                        arguments=arguments,
                        working_directory=installation.executable.parent,
                        source="official_shortcut_language",
                        shortcut_path=shortcut_path,
                    )
                    break
            except Exception:
                continue
    finally:
        shortcut = None
        shell = None
        pythoncom.CoUninitialize()
    return result


def _launch_revit(
    installation: RevitInstallation,
) -> tuple[subprocess.Popen, RevitLaunchSpec]:
    """Start Revit only; model opening is always a separate later operation."""
    spec = _revit_launch_spec(installation)
    command = [str(spec.executable), *spec.arguments]
    environment, _ = _external_process_environment()
    process = subprocess.Popen(
        command,
        cwd=str(spec.working_directory),
        env=environment,
        close_fds=True,
    )
    return process, spec


def _existing_instance_response(model_path: str | None = None) -> dict[str, object]:
    pids = [item.pid for item in running_revit_processes()]
    return {
        "status": "user_action_required",
        "message": (
            "检测到已运行的 Revit。为避免同时打开多个 Revit，系统不会启动新实例；"
            "请在现有 Revit 中打开目标模型并确认后继续。"
        ),
        "running_revit_pids": pids,
        **({"model_path": model_path} if model_path else {}),
    }


class ApplicationSelectionRequired(LookupError):
    """Raised when Windows discovery finds several equally plausible apps."""

    def __init__(self, application: str, candidates: list[InstalledApplication]):
        self.application = application
        self.candidates = candidates
        super().__init__(f"Multiple installed applications match '{application}'.")


def _registry_views() -> tuple[int, ...]:
    if winreg is None:
        return ()
    return (
        winreg.KEY_READ | winreg.KEY_WOW64_64KEY,
        winreg.KEY_READ | winreg.KEY_WOW64_32KEY,
    )


def _revit_version(value: str) -> Optional[int]:
    match = _VERSION_PATTERN.search(value)
    return int(match.group(1)) if match else None


def _existing_revit_executable(value: object) -> Optional[Path]:
    if not isinstance(value, str) or not value.strip():
        return None
    path = Path(os.path.expandvars(value.strip().strip('"')))
    if path.is_dir():
        path /= "Revit.exe"
    return path if path.is_file() and path.name.lower() == "revit.exe" else None


def _registry_value(key, name: str) -> object | None:
    try:
        value, _ = winreg.QueryValueEx(key, name)
        return value
    except OSError:
        return None


def _revit_executable_from_registry_key(key) -> Optional[Path]:
    """Read a validated Revit.exe path from one Autodesk registry key."""
    for value_name in ("InstallLocation", "InstallationLocation", "InstallPath", "Path"):
        try:
            value, _ = winreg.QueryValueEx(key, value_name)
        except OSError:
            continue
        executable = _existing_revit_executable(value)
        if executable:
            return executable
    return None


def _uninstall_entries() -> Iterable[dict[str, str]]:
    """Yield installed-application metadata from both Windows registry views."""
    if winreg is None:
        return []
    entries: list[dict[str, str]] = []
    for view in _registry_views():
        try:
            root = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, _UNINSTALL_ROOT, 0, view)
        except OSError:
            continue
        with root:
            index = 0
            while True:
                try:
                    key_name = winreg.EnumKey(root, index)
                except OSError:
                    break
                index += 1
                try:
                    key = winreg.OpenKey(root, key_name, 0, view)
                except OSError:
                    continue
                with key:
                    display_name = _registry_value(key, "DisplayName")
                    if not isinstance(display_name, str) or not display_name.strip():
                        continue
                    entries.append(
                        {
                            "display_name": display_name.strip(),
                            "display_version": str(_registry_value(key, "DisplayVersion") or ""),
                            "install_location": str(_registry_value(key, "InstallLocation") or ""),
                            "display_icon": str(_registry_value(key, "DisplayIcon") or ""),
                        }
                    )
    return entries


def _executable_from_uninstall_entry(entry: dict[str, str], application: str) -> Optional[Path]:
    icon = entry["display_icon"].strip().strip('"')
    if icon:
        icon_path = Path(os.path.expandvars(icon.rsplit(",", 1)[0].strip('"')))
        if icon_path.is_file() and icon_path.suffix.lower() == ".exe":
            return icon_path
    location = Path(os.path.expandvars(entry["install_location"].strip().strip('"')))
    if not location.is_dir():
        return None
    normalized = re.sub(r"[^a-z0-9]", "", application.lower())
    preferred = [location / f"{application}.exe"]
    if normalized:
        preferred.append(location / f"{normalized}.exe")
    for path in preferred:
        if path.is_file():
            return path
    executables = [path for path in location.glob("*.exe") if path.is_file()]
    return executables[0] if len(executables) == 1 else None


def discover_revit_installations() -> list[RevitInstallation]:
    """Read installed Revit executables from the Windows registry."""
    if winreg is None:
        return []
    found: dict[tuple[int, str], RevitInstallation] = {}
    for view in _registry_views():
        try:
            root = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, _REVIT_ROOT, 0, view)
        except OSError:
            continue
        with root:
            index = 0
            while True:
                try:
                    key_name = winreg.EnumKey(root, index)
                except OSError:
                    break
                index += 1
                version = _revit_version(key_name)
                if version is None:
                    continue
                try:
                    key = winreg.OpenKey(root, key_name, 0, view)
                except OSError:
                    continue
                with key:
                    executable = _revit_executable_from_registry_key(key)
                    if executable:
                        installation = RevitInstallation(version, executable)
                        found[(version, str(executable).lower())] = installation

                    # Older Revit releases keep InstallationLocation below a
                    # nested REVIT-* key. This mirrors the registry traversal
                    # used by the supplied working C# implementation.
                    sub_index = 0
                    while True:
                        try:
                            sub_key_name = winreg.EnumKey(key, sub_index)
                        except OSError:
                            break
                        sub_index += 1
                        if not sub_key_name.upper().startswith("REVIT"):
                            continue
                        try:
                            sub_key = winreg.OpenKey(key, sub_key_name)
                        except OSError:
                            continue
                        with sub_key:
                            executable = _revit_executable_from_registry_key(sub_key)
                        if executable:
                            installation = RevitInstallation(version, executable)
                            found[(version, str(executable).lower())] = installation
    # Some releases (including the current machine's Revit 2018 installation)
    # expose their install path only through Windows' uninstall metadata.
    for entry in _uninstall_entries():
        if "revit" not in entry["display_name"].lower():
            continue
        version = _revit_version(entry["display_name"])
        executable = _existing_revit_executable(entry["install_location"])
        if version is not None and executable:
            found[(version, str(executable).lower())] = RevitInstallation(version, executable)
    return sorted(found.values(), key=lambda item: (item.version, str(item.executable)))


def _basic_file_info_header_version(raw: bytes) -> Optional[int]:
    """Read the structured model-version field at the start of ``BasicFileInfo``.

    Revit's newer BasicFileInfo layouts store the release as the first
    length-prefixed UTF-16 value.  It is deliberately read only at this fixed,
    documented-in-practice location rather than by searching arbitrary years
    in the binary stream.  Older layouts put the build description there and
    are handled by the legacy textual fallback below.
    """
    # The BasicFileInfo schema marker is a 16-bit value followed by twelve
    # reserved bytes, so its first UTF-16 field length begins at byte 14.
    length_offset = 14
    value_offset = length_offset + 4
    if len(raw) < value_offset:
        return None
    character_count = int.from_bytes(raw[length_offset:value_offset], "little")
    # A Revit release is four characters.  The upper bound also prevents a
    # malformed OLE stream from causing an unnecessarily large slice.
    if not 1 <= character_count <= 64:
        return None
    value_end = value_offset + character_count * 2
    if value_end > len(raw):
        return None
    try:
        value = raw[value_offset:value_end].decode("utf-16-le")
    except UnicodeDecodeError:
        return None
    return int(value) if _REVIT_YEAR_PATTERN.fullmatch(value) else None


def _basic_file_info_version(raw: bytes) -> Optional[int]:
    """Return a Revit version from trusted BasicFileInfo representations."""
    structured_version = _basic_file_info_header_version(raw)
    if structured_version is not None:
        return structured_version

    # These are named fields inside the BasicFileInfo stream, not a generic
    # search for ``20xx`` values (which could otherwise match dates or paths).
    text = raw.decode("utf-16-le", errors="ignore")
    for pattern in (_REVIT_FORMAT_PATTERN, _REVIT_LEGACY_BUILD_PATTERN):
        match = pattern.search(text)
        if match:
            return int(match.group(1))
    return None


def read_revit_model_version(model_path: str | Path) -> int:
    """Extract the stored Revit version from the OLE ``BasicFileInfo`` stream."""
    path = Path(model_path)
    if path.suffix.lower() != ".rvt":
        raise ValueError("rvt_file_path must point to a .rvt file")
    if not path.is_file():
        raise ValueError(f"RVT file does not exist or cannot be accessed: {path}")
    try:
        import olefile
    except ImportError as error:  # pragma: no cover - guarded by requirements.
        raise RuntimeError("olefile is required to inspect Revit model versions") from error
    if not olefile.isOleFile(str(path)):
        raise ValueError("The selected .rvt file is not a readable OLE compound file")
    with olefile.OleFileIO(str(path)) as document:
        stream_name = next(
            (entry for entry in document.listdir() if entry and entry[-1].lower() == "basicfileinfo"),
            None,
        )
        if stream_name is None:
            raise ValueError("The Revit BasicFileInfo stream was not found")
        raw = document.openstream(stream_name).read()
    version = _basic_file_info_version(raw)
    if version is None:
        raise ValueError("The Revit version could not be read from BasicFileInfo")
    return version


def select_compatible_revit(
    model_version: int, installations: Iterable[RevitInstallation]
) -> RevitInstallation:
    """Prefer an exact version, otherwise the lowest installed newer version."""
    candidates = sorted(installations, key=lambda item: item.version)
    for installation in candidates:
        if installation.version == model_version:
            return installation
    for installation in candidates:
        if installation.version > model_version:
            return installation
    raise LookupError(f"No installed Revit version can open a {model_version} model")


def _registered_application_command(application: str) -> Optional[list[str]]:
    if winreg is None:
        return None
    executable_name = application if application.lower().endswith(".exe") else f"{application}.exe"
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for view in _registry_views():
            try:
                key = winreg.OpenKey(hive, f"{_APP_PATHS_ROOT}\\{executable_name}", 0, view)
            except OSError:
                continue
            with key:
                try:
                    command, _ = winreg.QueryValueEx(key, None)
                except OSError:
                    continue
            if isinstance(command, str) and command.strip():
                return shlex.split(command, posix=False)
    return None


def _discover_installed_applications(application: str, version: str | None) -> list[InstalledApplication]:
    tokens = [token for token in re.split(r"\s+", application.lower().strip()) if token]
    matches: dict[str, InstalledApplication] = {}
    for entry in _uninstall_entries():
        display_name = entry["display_name"]
        haystack = display_name.lower()
        if not tokens or not all(token in haystack for token in tokens):
            continue
        if version and version not in display_name and version not in entry["display_version"]:
            continue
        executable = _executable_from_uninstall_entry(entry, application)
        if executable:
            matches[str(executable).lower()] = InstalledApplication(
                display_name=display_name,
                display_version=entry["display_version"],
                executable=executable,
            )
    return sorted(matches.values(), key=lambda item: (item.display_name.lower(), str(item.executable)))


def resolve_application(application: str, version: str | None = None) -> list[str]:
    """Resolve an explicit executable, PATH command, registered app, or installed app metadata."""
    value = application.strip().strip('"')
    if not value:
        raise ValueError("application must not be empty")
    explicit = Path(os.path.expandvars(value))
    if explicit.is_file():
        return [str(explicit)]
    path_match = shutil.which(value) or shutil.which(f"{value}.exe")
    if path_match and not version:
        return [path_match]
    registered = _registered_application_command(value)
    if registered and not version:
        return registered
    candidates = _discover_installed_applications(value, version)
    if len(candidates) == 1:
        return [str(candidates[0].executable)]
    if len(candidates) > 1:
        raise ApplicationSelectionRequired(value, candidates)
    raise LookupError(
        f"Windows could not resolve '{application}'. Provide its absolute .exe path or an installed display name."
    )


class WindowsOpenApplication(BaseTool):
    """Open a Windows desktop application without shell guessing or browser search."""

    name: str = "windows_open_application"
    description: str = (
        "Launches a Windows application using an absolute .exe path, PATH command, or registered App Paths name. "
        "Use for opening a local desktop app; it does not search the web or wait for terminal input."
    )
    parameters: dict = {
        "type": "object",
        "properties": {
            "application": {"type": "string", "description": "Executable path or application name."},
            "version": {"type": "string", "description": "Optional requested application version, such as 2024."},
            "arguments": {"type": "array", "items": {"type": "string"}, "default": []},
            "file_path": {"type": "string", "description": "Optional local file to open with the application."},
            "working_directory": {"type": "string", "description": "Optional existing working directory."},
        },
        "required": ["application"],
        "additionalProperties": False,
    }

    async def execute(
        self,
        application: str,
        version: Optional[str] = None,
        arguments: Optional[list[str]] = None,
        file_path: Optional[str] = None,
        working_directory: Optional[str] = None,
    ) -> ToolResult:
        if sys.platform != "win32":
            return self.fail_response("windows_open_application is available only on Windows")
        rvt_target = None
        explicit_application = Path(os.path.expandvars(application.strip().strip('"')))
        if explicit_application.suffix.lower() == ".rvt" and explicit_application.is_file():
            rvt_target = explicit_application
        elif file_path:
            supplied_file = Path(file_path)
            if supplied_file.suffix.lower() == ".rvt" and supplied_file.is_file():
                rvt_target = supplied_file
        elif (
            explicit_application.name.casefold() == "revit.exe"
            or application.strip().casefold() in {"revit", "revit.exe"}
        ):
            for argument in arguments or []:
                supplied_file = Path(os.path.expandvars(str(argument).strip().strip('"')))
                if supplied_file.suffix.lower() == ".rvt" and supplied_file.is_file():
                    rvt_target = supplied_file
                    break
        if rvt_target is not None:
            return await RevitLaunchVersionedModel().execute(str(rvt_target))
        try:
            command = resolve_application(application, version)
            # A display-name lookup such as "Autodesk Revit 2020" resolves
            # only here, after the early explicit-Revit check above.  Do not
            # let that spelling bypass the model-free Revit startup flow.
            if Path(command[0]).name.casefold() == "revit.exe":
                for argument in arguments or []:
                    supplied_file = Path(
                        os.path.expandvars(str(argument).strip().strip('"'))
                    )
                    if supplied_file.suffix.lower() == ".rvt" and supplied_file.is_file():
                        return await RevitLaunchVersionedModel().execute(str(supplied_file))
            if file_path:
                target = Path(file_path)
                if not target.is_absolute() or not target.exists():
                    return self.fail_response("file_path must be an existing absolute path")
                command.append(str(target))
            command.extend(str(argument) for argument in (arguments or []))
            cwd = None
            if working_directory:
                directory = Path(working_directory)
                if not directory.is_dir():
                    return self.fail_response("working_directory must be an existing directory")
                cwd = str(directory)
            process = subprocess.Popen(command, cwd=cwd, close_fds=True)
        except ApplicationSelectionRequired as error:
            return self.success_response(
                {
                    "status": "selection_required",
                    "message": f"找到多个与 {error.application} 匹配的应用，请选择要打开的版本。",
                    "candidates": [
                        {
                            "display_name": item.display_name,
                            "display_version": item.display_version,
                            "executable": str(item.executable),
                        }
                        for item in error.candidates
                    ],
                }
            )
        except (OSError, ValueError, LookupError) as error:
            return self.fail_response(str(error))
        return self.success_response(
            {"application": command[0], "pid": process.pid, "command": command}
        )


class RevitLaunchApplication(BaseTool):
    """Launch a locally installed Revit application without opening a model."""

    name: str = "revit_launch_application"
    description: str = (
        "Launches Revit only, without selecting or searching for an RVT model. "
        "Use when the user asks to open/start Revit itself. An explicit version must match an installed release; "
        "when multiple versions are installed and none is specified, requests a user choice instead of guessing."
    )
    parameters: dict = {
        "type": "object",
        "properties": {
            "version": {"type": "integer", "description": "Optional installed Revit year, for example 2024."},
        },
        "additionalProperties": False,
    }

    async def execute(self, version: int | None = None) -> ToolResult:
        if sys.platform != "win32":
            return self.fail_response("revit_launch_application is available only on Windows")
        try:
            if running_revit_processes():
                return self.success_response(_existing_instance_response())
            installations = discover_revit_installations()
            if not installations:
                raise LookupError("No installed Revit executable was found in Windows registry metadata")
            if version is None:
                if len(installations) > 1:
                    return self.success_response(
                        {
                            "status": "selection_required",
                            "message": "检测到多个 Revit 版本，请选择要启动的版本。",
                            "candidates": [
                                {
                                    "display_name": f"Autodesk Revit {item.version}",
                                    "display_version": str(item.version),
                                    "executable": str(item.executable),
                                }
                                for item in installations
                            ],
                        }
                    )
                selected = installations[0]
            else:
                selected = next(item for item in installations if item.version == int(version))
            process, launch_spec = _launch_revit(selected)
        except (OSError, LookupError, StopIteration, ValueError) as error:
            installed = [item.version for item in discover_revit_installations()]
            suffix = f" Installed versions: {installed}." if installed else ""
            return self.fail_response(f"{error}{suffix}")
        return self.success_response(
            {
                "revit_version": selected.version,
                "revit_executable": str(selected.executable),
                "pid": process.pid,
                "launch_source": launch_spec.source,
                "launch_arguments": list(launch_spec.arguments),
                "working_directory": str(launch_spec.working_directory),
                "launch_shortcut": (
                    str(launch_spec.shortcut_path)
                    if launch_spec.shortcut_path is not None
                    else None
                ),
            }
        )


class RevitLaunchVersionedModel(BaseTool):
    """Open a Revit model through the shared two-stage project-opening flow."""

    name: str = "revit_launch_versioned_model"
    description: str = (
        "Reads a local RVT model's BasicFileInfo and delegates to the shared project-opening workflow. "
        "That workflow starts a compatible Revit without passing the model on its command line, waits for "
        "Revit and its plugin to become ready, then opens the model once."
    )
    parameters: dict = {
        "type": "object",
        "properties": {
            "rvt_file_path": {"type": "string", "description": "Existing absolute .rvt model path."},
            "allow_upgrade": {"type": "boolean", "default": False, "description": "Set true only after the user accepts opening in a newer Revit version."},
        },
        "required": ["rvt_file_path"],
        "additionalProperties": False,
    }

    async def execute(self, rvt_file_path: str, allow_upgrade: bool = False) -> ToolResult:
        if sys.platform != "win32":
            return self.fail_response("revit_launch_versioned_model is available only on Windows")
        try:
            model = Path(rvt_file_path)
            if not model.is_absolute():
                raise ValueError("rvt_file_path must be absolute")
            if not model.is_file():
                raise ValueError("rvt_file_path must be an existing .rvt file")
            if running_revit_processes():
                return self.success_response(_existing_instance_response(str(model)))
        except (OSError, ValueError, LookupError, RuntimeError) as error:
            return self.fail_response(str(error))
        # desktop_bim imports this module's registry and launch helpers, so
        # keep this compatibility delegation local to execution time.
        from app.tool.desktop_bim import RevitOpenProjectModel

        return await RevitOpenProjectModel().execute(
            str(model), allow_upgrade=allow_upgrade
        )
