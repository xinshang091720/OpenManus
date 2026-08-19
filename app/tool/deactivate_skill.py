"""Explicitly remove an active Skill and its scoped capabilities."""

from typing import Any, Awaitable, Callable

from pydantic import Field

from app.tool.base import BaseTool, ToolResult


DeactivationCallback = Callable[[str], Awaitable[dict[str, Any]]]


class DeactivateSkill(BaseTool):
    """Remove one active Skill without affecting unrelated active Skills."""

    name: str = "deactivate_skill"
    description: str = (
        "Deactivates one currently active Skill when its domain tools are no longer needed. "
        "Use this before activating a fourth Skill or to reduce the available tool scope."
    )
    parameters: dict = {
        "type": "object",
        "properties": {
            "skill_id": {
                "type": "string",
                "description": "Exact id of a currently active Skill.",
            }
        },
        "required": ["skill_id"],
        "additionalProperties": False,
    }
    deactivator: Any = Field(default=None, exclude=True)

    def bind(self, deactivator: DeactivationCallback) -> None:
        self.deactivator = deactivator

    async def execute(self, skill_id: str) -> ToolResult:
        if self.deactivator is None:
            return self.fail_response("Skill deactivation is not available in this Agent session")
        try:
            return self.success_response(await self.deactivator(skill_id))
        except (KeyError, ValueError, RuntimeError) as error:
            return self.fail_response(str(error))
