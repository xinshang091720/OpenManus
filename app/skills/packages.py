"""Anthropic-style filesystem Skill discovery and BeeSync capability manifests.

Only a Skill's name and description participate in the initial Agent context.
The body and executable capabilities are deliberately loaded after the model calls
``activate_skill``; this module never routes tasks by keywords.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


class SkillPackageError(ValueError):
    """A malformed Skill package that must not be activated."""


@dataclass(frozen=True)
class McpBinding:
    server_id: str
    tools: tuple[str, ...]


@dataclass(frozen=True)
class ScriptToolDefinition:
    name: str
    description: str
    script: Path
    parameters: dict[str, Any]


@dataclass(frozen=True)
class SkillPackage:
    name: str
    description: str
    path: Path
    source: str = "vendor"
    package_version: str = "1.0.0"
    mcp_bindings: tuple[McpBinding, ...] = ()
    runtime_tools: tuple[str, ...] = ()
    script_tools: tuple[ScriptToolDefinition, ...] = ()
    permissions: dict[str, Any] = field(default_factory=dict)

    @property
    def catalog_entry(self) -> dict[str, str]:
        return {"id": self.name, "description": self.description, "source": self.source}

    def instruction(self) -> str:
        """Load the instruction body only after an explicit activation."""
        text = (self.path / "SKILL.md").read_text(encoding="utf-8")
        parts = text.split("---", 2)
        return parts[2].strip() if len(parts) == 3 else text


class SkillPackageRegistry:
    """Discover official and user packages without deciding which one applies."""

    def __init__(self, root: Path, user_root: Path | None = None) -> None:
        self.root = root
        self.user_root = user_root
        self._packages: list[SkillPackage] = []

    def discover(self) -> list[SkillPackage]:
        packages = self._discover_root(self.root, source="vendor")
        if self.user_root and not self._same_directory(self.root, self.user_root):
            packages.extend(
                self._discover_root(
                    self.user_root,
                    source="user",
                    enabled_names=self._enabled_user_skill_names(),
                )
            )
        self._packages = packages
        return packages

    @property
    def packages(self) -> tuple[SkillPackage, ...]:
        return tuple(self._packages)

    def catalog(self) -> list[dict[str, str]]:
        return [package.catalog_entry for package in self._packages]

    def get(self, skill_id: str) -> SkillPackage:
        for package in self._packages:
            if package.name == skill_id:
                return package
        raise KeyError(f"Unknown or disabled Skill: {skill_id}")

    @staticmethod
    def _same_directory(first: Path, second: Path) -> bool:
        try:
            return first.resolve() == second.resolve()
        except OSError:
            return first == second

    def _enabled_user_skill_names(self) -> set[str]:
        """User packages are opt-in until the desktop extension UI enables them."""
        if not self.user_root:
            return set()
        enabled_file = self.user_root / "enabled.json"
        if not enabled_file.exists():
            return set()
        try:
            payload = yaml.safe_load(enabled_file.read_text(encoding="utf-8")) or {}
            values = payload.get("enabled", []) if isinstance(payload, dict) else []
            if not isinstance(values, list):
                return set()
            return {str(value).removeprefix("user.") for value in values if isinstance(value, str)}
        except (OSError, UnicodeDecodeError, yaml.YAMLError):
            return set()

    def _discover_root(
        self, root: Path, *, source: str, enabled_names: set[str] | None = None
    ) -> list[SkillPackage]:
        if not root.exists():
            return []
        packages: list[SkillPackage] = []
        for skill_file in root.glob("*/SKILL.md"):
            if source == "user" and skill_file.parent.name not in (enabled_names or set()):
                continue
            try:
                metadata = self._read_skill_metadata(skill_file)
                package = self._build_package(skill_file.parent, metadata, source)
            except (OSError, UnicodeDecodeError, SkillPackageError, yaml.YAMLError):
                # A bad user extension must never prevent official skills from loading.
                continue
            packages.append(package)
        return packages

    @staticmethod
    def _read_skill_metadata(skill_file: Path) -> dict[str, Any]:
        text = skill_file.read_text(encoding="utf-8")
        if not text.startswith("---"):
            raise SkillPackageError("SKILL.md must start with YAML front matter")
        parts = text.split("---", 2)
        if len(parts) < 3:
            raise SkillPackageError("SKILL.md front matter is incomplete")
        metadata = yaml.safe_load(parts[1]) or {}
        if not isinstance(metadata, dict):
            raise SkillPackageError("SKILL.md front matter must be an object")
        name = metadata.get("name")
        description = metadata.get("description")
        if not isinstance(name, str) or not name.strip() or not isinstance(description, str) or not description.strip():
            raise SkillPackageError("SKILL.md requires name and description")
        return metadata

    def _build_package(
        self, path: Path, metadata: dict[str, Any], source: str
    ) -> SkillPackage:
        declared_name = str(metadata["name"]).strip()
        package_name = f"user.{declared_name}" if source == "user" else declared_name
        manifest_path = path / "beesync.skill.yaml"
        manifest: dict[str, Any] = {}
        if manifest_path.exists():
            manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
            if not isinstance(manifest, dict):
                raise SkillPackageError("beesync.skill.yaml must be an object")

        bindings: list[McpBinding] = []
        for binding in manifest.get("mcp_bindings", []) or []:
            if not isinstance(binding, dict):
                raise SkillPackageError("mcp_bindings entries must be objects")
            server_id = binding.get("server_id")
            tools = binding.get("tools", [])
            if not isinstance(server_id, str) or not isinstance(tools, list) or not all(isinstance(item, str) for item in tools):
                raise SkillPackageError("invalid mcp binding")
            bindings.append(McpBinding(server_id=server_id, tools=tuple(tools)))

        script_tools: list[ScriptToolDefinition] = []
        for item in manifest.get("script_tools", []) or []:
            if not isinstance(item, dict):
                raise SkillPackageError("script_tools entries must be objects")
            name, description, script = item.get("name"), item.get("description"), item.get("script")
            parameters = item.get("parameters", {"type": "object", "properties": {}})
            if not all(isinstance(value, str) and value.strip() for value in (name, description, script)):
                raise SkillPackageError("script tool requires name, description and script")
            if not isinstance(parameters, dict):
                raise SkillPackageError("script tool parameters must be JSON Schema")
            script_path = (path / script).resolve()
            if path.resolve() not in script_path.parents or not script_path.is_file():
                raise SkillPackageError("script must remain inside its Skill package")
            script_tools.append(ScriptToolDefinition(name, description, script_path, parameters))

        runtime_tools = manifest.get("runtime_tools", []) or []
        if not isinstance(runtime_tools, list) or not all(isinstance(item, str) for item in runtime_tools):
            raise SkillPackageError("runtime_tools must be a string list")
        permissions = manifest.get("permissions", {}) or {}
        if not isinstance(permissions, dict):
            raise SkillPackageError("permissions must be an object")
        return SkillPackage(
            name=package_name,
            description=str(metadata["description"]).strip(),
            path=path,
            source=source,
            package_version=str(manifest.get("package_version", "1.0.0")),
            mcp_bindings=tuple(bindings),
            runtime_tools=tuple(runtime_tools),
            script_tools=tuple(script_tools),
            permissions=permissions,
        )
