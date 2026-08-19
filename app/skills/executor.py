"""Safe sequential execution for declarative skills."""

import re
from copy import deepcopy
from typing import TYPE_CHECKING, Any, Dict

from app.skills.models import SkillDefinition, SkillExecutionResult, SkillStepResult

if TYPE_CHECKING:
    from app.tool.tool_collection import ToolCollection


_BINDING_PATTERN = re.compile(
    r"^\{\{\s*(inputs|steps)\.([a-zA-Z0-9_-]+)(?:\.output)?\s*\}\}$"
)


class SkillExecutor:
    """Runs declared steps against a supplied, already-authorized tool collection."""

    async def execute(
        self,
        skill: SkillDefinition,
        inputs: Dict[str, Any],
        tools: "ToolCollection",
        approved: bool = False,
    ) -> SkillExecutionResult:
        validation_error = self.validate_definition(skill, tools)
        if validation_error:
            return SkillExecutionResult(
                skill_id=skill.id,
                status="failed",
                error=validation_error,
            )

        validation_error = self._validate_inputs(skill, inputs)
        if validation_error:
            return SkillExecutionResult(
                skill_id=skill.id,
                status="failed",
                error=validation_error,
            )

        if skill.policy.requires_approval and not approved:
            return SkillExecutionResult(skill_id=skill.id, status="approval_required")

        outputs: Dict[str, Any] = {}
        step_results = []
        for step in skill.steps:
            try:
                arguments = self._bind(deepcopy(step.arguments), inputs, outputs)
            except ValueError as error:
                step_results.append(
                    SkillStepResult(
                        step_id=step.id,
                        tool=step.tool,
                        status="failed",
                        error=str(error),
                    )
                )
                return SkillExecutionResult(
                    skill_id=skill.id,
                    status="failed",
                    steps=step_results,
                    outputs=outputs,
                    error=str(error),
                )

            result = await tools.execute(name=step.tool, tool_input=arguments)
            if result.error:
                step_results.append(
                    SkillStepResult(
                        step_id=step.id,
                        tool=step.tool,
                        status="failed",
                        error=result.error,
                    )
                )
                return SkillExecutionResult(
                    skill_id=skill.id,
                    status="failed",
                    steps=step_results,
                    outputs=outputs,
                    error=result.error,
                )

            outputs[step.id] = result.output
            step_results.append(
                SkillStepResult(
                    step_id=step.id,
                    tool=step.tool,
                    status="completed",
                    output=result.output,
                )
            )

        return SkillExecutionResult(
            skill_id=skill.id,
            status="completed",
            steps=step_results,
            outputs=outputs,
        )

    @staticmethod
    def validate_definition(
        skill: SkillDefinition, tools: "ToolCollection"
    ) -> str | None:
        for step in skill.steps:
            if step.tool not in tools.tool_map:
                return f"Skill step '{step.id}' references unknown tool '{step.tool}'"
        return None

    @staticmethod
    def _validate_inputs(skill: SkillDefinition, inputs: Dict[str, Any]) -> str | None:
        required_inputs = skill.input_schema.get("required", [])
        missing_inputs = [name for name in required_inputs if name not in inputs]
        if missing_inputs:
            return f"Missing required skill inputs: {', '.join(missing_inputs)}"
        return None

    def _bind(self, value: Any, inputs: Dict[str, Any], outputs: Dict[str, Any]) -> Any:
        if isinstance(value, dict):
            return {
                key: self._bind(item, inputs, outputs) for key, item in value.items()
            }
        if isinstance(value, list):
            return [self._bind(item, inputs, outputs) for item in value]
        if not isinstance(value, str):
            return value

        match = _BINDING_PATTERN.match(value)
        if not match:
            return value

        source, key = match.groups()
        values = inputs if source == "inputs" else outputs
        if key not in values:
            raise ValueError(f"Binding '{value}' cannot be resolved")
        return values[key]
