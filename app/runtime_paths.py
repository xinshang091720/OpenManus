"""Paths used by the desktop Runtime in development and packaged Windows builds."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path


APP_VENDOR = "Anbi"
APP_NAME = "BeeSync"


def _environment_path(name: str) -> Path | None:
    value = os.environ.get(name)
    return Path(value).expanduser() if value else None


def _known_folder(name: str, fallback: Path) -> Path:
    value = os.environ.get(name)
    return Path(value) if value else fallback


@dataclass(frozen=True)
class RuntimePaths:
    """Filesystem layout with environment overrides for the C# process host."""

    install_root: Path
    resource_root: Path
    config_dir: Path
    data_dir: Path
    vendor_skills_dir: Path
    user_skills_dir: Path
    user_mcp_dir: Path
    workspace_dir: Path
    log_dir: Path

    @classmethod
    def discover(cls) -> "RuntimePaths":
        # In a PyInstaller --onedir build the executable is placed directly in
        # the versioned Runtime directory.  In source mode the repository root
        # remains the default, preserving the existing CLI behaviour.
        source_root = Path(__file__).resolve().parent.parent
        frozen = getattr(sys, "frozen", False)
        install_root = _environment_path("BEESYNC_INSTALL_DIR") or (
            Path(sys.executable).resolve().parent if frozen else source_root
        )
        # PyInstaller 6 --onedir stores --add-data resources under _internal
        # (exposed as sys._MEIPASS at runtime), while the executable itself
        # remains one directory higher.  Keep both locations: install_root is
        # useful for launching this EXE again, resource_root is for immutable
        # bundled config and official Skills.
        resource_root = (
            Path(getattr(sys, "_MEIPASS")).resolve()
            if frozen and getattr(sys, "_MEIPASS", None)
            else install_root
        )
        program_data = _known_folder("PROGRAMDATA", install_root / "data")
        local_app_data = _known_folder("LOCALAPPDATA", install_root / "data")
        machine_root = program_data / APP_VENDOR / APP_NAME
        user_root = local_app_data / APP_VENDOR / APP_NAME

        config_dir = _environment_path("BEESYNC_CONFIG_DIR") or (
            machine_root / "config" if frozen else install_root / "config"
        )
        data_dir = _environment_path("BEESYNC_DATA_DIR") or (
            user_root if frozen else install_root
        )
        vendor_skills_dir = _environment_path("BEESYNC_SKILLS_DIR") or resource_root / "skills"
        user_skills_dir = _environment_path("BEESYNC_USER_SKILLS_DIR") or data_dir / "skills"
        user_mcp_dir = _environment_path("BEESYNC_USER_MCP_DIR") or data_dir / "mcp-servers"
        workspace_dir = _environment_path("BEESYNC_WORKSPACE_DIR") or data_dir / "workspace"
        log_dir = _environment_path("BEESYNC_LOG_DIR") or data_dir / "logs"
        return cls(
            install_root=install_root,
            resource_root=resource_root,
            config_dir=config_dir,
            data_dir=data_dir,
            vendor_skills_dir=vendor_skills_dir,
            user_skills_dir=user_skills_dir,
            user_mcp_dir=user_mcp_dir,
            workspace_dir=workspace_dir,
            log_dir=log_dir,
        )

    def ensure_user_directories(self) -> None:
        """Create writable per-user Runtime locations, never install locations."""
        for directory in (
            self.data_dir,
            self.user_skills_dir,
            self.user_mcp_dir,
            self.workspace_dir,
            self.log_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True)


runtime_paths = RuntimePaths.discover()
