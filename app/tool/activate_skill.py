"""The single generic entry point for model-selected Skill activation."""

from typing import Any, Awaitable, Callable

from pydantic import Field

from app.tool.base import BaseTool, ToolResult


ActivationCallback = Callable[[str], Awaitable[dict[str, Any]]]


class ActivateSkill(BaseTool):
    """Load a catalogued Skill and its declared, scoped capabilities."""

    name: str = "activate_skill"
    description: str = (
        "Activates one Skill from the Skill Catalog. Use this before a task needs "
        "that Skill's instructions, domain tools, MCP connections, or script tools."
    )
    parameters: dict = {
        "type": "object",
        "properties": {
            "skill_id": {
                "type": "string",
                "description": "Exact Skill id from the Skill Catalog.",
            }
        },
        "required": ["skill_id"],
        "additionalProperties": False,
    }
    activator: Any = Field(default=None, exclude=True)

    def bind(self, activator: ActivationCallback) -> None:
        self.activator = activator

    async def execute(self, skill_id: str) -> ToolResult:
        if self.activator is None:
            return self.fail_response("Skill activation is not available in this Agent session")
        try:
            return self.success_response(await self.activator(skill_id))
        except (KeyError, ValueError, RuntimeError) as error:
            return self.fail_response(str(error))
