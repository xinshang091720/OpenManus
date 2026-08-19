"""Safe in-process adapters for explicitly declared Skill scripts."""

from __future__ import annotations

import importlib.util
import inspect
from pathlib import Path
from typing import Any

from pydantic import Field

from app.tool.base import BaseTool, ToolResult


class SkillScriptTool(BaseTool):
    """Execute only one manifest-declared ``run(**kwargs)`` Skill entry point."""

    script_path: Path = Field(exclude=True)
    skill_id: str = Field(exclude=True)

    async def execute(self, **kwargs: Any) -> ToolResult:
        module_name = f"beesync_skill_{self.skill_id.replace('.', '_').replace('-', '_')}_{self.name}"
        try:
            spec = importlib.util.spec_from_file_location(module_name, self.script_path)
            if spec is None or spec.loader is None:
                raise RuntimeError("Unable to load declared Skill script")
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            runner = getattr(module, "run", None)
            if not callable(runner):
                raise RuntimeError("Declared Skill script must define run(**kwargs)")
            result = runner(**kwargs)
            if inspect.isawaitable(result):
                result = await result
            if not isinstance(result, (str, dict)):
                raise RuntimeError("Skill script must return a string or JSON object")
            return self.success_response(result)
        except ModuleNotFoundError as error:
            return self.fail_response(
                f"capability_unavailable: {self.name} requires the packaged dependency '{error.name}'"
            )
        except Exception as error:
            return self.fail_response(f"Skill script failed: {error}")
