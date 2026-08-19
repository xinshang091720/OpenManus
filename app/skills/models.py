"""Data contracts for user-configurable, declarative skills."""

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, field_validator


class SkillPolicy(BaseModel):
    """Execution rules enforced by the skill runtime, not by the LLM."""

    requires_approval: bool = False
    stop_on_error: bool = True


class SkillStep(BaseModel):
    """One declared tool invocation in a skill."""

    id: str = Field(..., min_length=1)
    tool: str = Field(..., min_length=1)
    arguments: Dict[str, Any] = Field(default_factory=dict)


class SkillDefinition(BaseModel):
    """A deterministic workflow composed from registered tools."""

    id: str = Field(..., min_length=1)
    name: str = Field(..., min_length=1)
    description: str = ""
    input_schema: Dict[str, Any] = Field(default_factory=dict)
    steps: List[SkillStep] = Field(..., min_length=1)
    policy: SkillPolicy = Field(default_factory=SkillPolicy)

    @field_validator("steps")
    @classmethod
    def validate_unique_step_ids(cls, steps: List[SkillStep]) -> List[SkillStep]:
        step_ids = [step.id for step in steps]
        if len(step_ids) != len(set(step_ids)):
            raise ValueError("Skill step IDs must be unique")
        return steps


class SkillStepResult(BaseModel):
    """Trace entry emitted for every declared skill step."""

    step_id: str
    tool: str
    status: Literal["completed", "failed", "skipped"]
    output: Any = None
    error: Optional[str] = None


class SkillExecutionResult(BaseModel):
    """Structured result returned to the hosting PC application."""

    skill_id: str
    status: Literal["completed", "failed", "approval_required"]
    steps: List[SkillStepResult] = Field(default_factory=list)
    outputs: Dict[str, Any] = Field(default_factory=dict)
    error: Optional[str] = None
