"""Tool adapter that exposes registered declarative skills to an agent."""

from typing import Any, Dict, Optional

from pydantic import Field

from app.skills.executor import SkillExecutor
from app.skills.registry import SkillRegistry
from app.tool.base import BaseTool, ToolResult
from app.tool.tool_collection import ToolCollection


class ExecuteSkill(BaseTool):
    """Execute an approved, registered skill against the current tool collection."""

    name: str = "execute_skill"
    description: str = (
        "Executes a registered declarative skill. Skills run only their declared "
        "steps and cannot invoke tools outside the current authorized tool collection."
    )
    parameters: dict = {
        "type": "object",
        "properties": {
            "skill_id": {
                "type": "string",
                "description": "ID of the registered skill to execute.",
            },
            "inputs": {
                "type": "object",
                "description": "Values for the skill input schema.",
                "default": {},
            },
            "approved": {
                "type": "boolean",
                "description": "Whether the business client approved a protected skill.",
                "default": False,
            },
        },
        "required": ["skill_id"],
        "additionalProperties": False,
    }

    registry: SkillRegistry = Field(default_factory=SkillRegistry, exclude=True)
    executor: SkillExecutor = Field(default_factory=SkillExecutor, exclude=True)
    tool_collection: Optional[ToolCollection] = Field(default=None, exclude=True)

    def bind(self, registry: SkillRegistry, tool_collection: ToolCollection) -> None:
        """Bind this tool to one agent/session after its tools are initialized."""
        self.registry = registry
        self.tool_collection = tool_collection

    async def execute(
        self,
        skill_id: str,
        inputs: Optional[Dict[str, Any]] = None,
        approved: bool = False,
    ) -> ToolResult:
        if self.tool_collection is None:
            return self.fail_response("Skill tool is not bound to an agent session")

        try:
            skill = self.registry.get(skill_id)
        except KeyError as error:
            return self.fail_response(str(error))

        result = await self.executor.execute(
            skill=skill,
            inputs=inputs or {},
            tools=self.tool_collection,
            approved=approved,
        )
        if result.status == "failed":
            return self.fail_response(result.error or "Skill execution failed")

        return self.success_response(result.model_dump())
