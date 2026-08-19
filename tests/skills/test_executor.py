import pytest

from app.skills import SkillDefinition, SkillExecutor, SkillRegistry
from app.tool.base import BaseTool, ToolResult
from app.tool.skill import ExecuteSkill
from app.tool.tool_collection import ToolCollection


class EchoTool(BaseTool):
    name: str = "echo"
    description: str = "Returns the supplied value."
    parameters: dict = {"type": "object"}

    async def execute(self, value: str) -> ToolResult:
        return ToolResult(output=value)


@pytest.mark.asyncio
async def test_skill_executes_declared_tools_with_bound_outputs():
    skill = SkillDefinition(
        id="copy-value",
        name="Copy value",
        input_schema={"required": ["value"]},
        steps=[
            {
                "id": "first",
                "tool": "echo",
                "arguments": {"value": "{{ inputs.value }}"},
            },
            {
                "id": "second",
                "tool": "echo",
                "arguments": {"value": "{{ steps.first.output }}"},
            },
        ],
    )

    result = await SkillExecutor().execute(
        skill, {"value": "revit"}, ToolCollection(EchoTool())
    )

    assert result.status == "completed"
    assert result.outputs == {"first": "revit", "second": "revit"}


@pytest.mark.asyncio
async def test_skill_requires_approval_before_any_tool_execution():
    skill = SkillDefinition(
        id="approved-only",
        name="Approved only",
        steps=[{"id": "first", "tool": "echo", "arguments": {"value": "x"}}],
        policy={"requires_approval": True},
    )

    result = await SkillExecutor().execute(skill, {}, ToolCollection(EchoTool()))

    assert result.status == "approval_required"
    assert result.steps == []


@pytest.mark.asyncio
async def test_skill_rejects_an_unknown_tool_before_execution():
    skill = SkillDefinition(
        id="invalid-tool",
        name="Invalid tool",
        steps=[{"id": "first", "tool": "missing", "arguments": {}}],
    )

    result = await SkillExecutor().execute(skill, {}, ToolCollection(EchoTool()))

    assert result.status == "failed"
    assert "unknown tool" in result.error


@pytest.mark.asyncio
async def test_execute_skill_tool_uses_the_bound_session_registry():
    registry = SkillRegistry()
    registry.register(
        SkillDefinition(
            id="echo-skill",
            name="Echo",
            steps=[
                {
                    "id": "echo",
                    "tool": "echo",
                    "arguments": {"value": "{{ inputs.value }}"},
                }
            ],
        )
    )
    skill_tool = ExecuteSkill()
    tools = ToolCollection(EchoTool(), skill_tool)
    skill_tool.bind(registry, tools)

    result = await tools.execute(
        name="execute_skill",
        tool_input={"skill_id": "echo-skill", "inputs": {"value": "bound"}},
    )

    assert result.error is None
    assert '"status": "completed"' in result.output
