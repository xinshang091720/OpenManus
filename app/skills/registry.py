"""In-memory registry owned by one agent or one business-client session."""

from typing import Dict, List

from app.skills.models import SkillDefinition


class SkillRegistry:
    """Stores skill definitions without introducing configuration persistence."""

    def __init__(self) -> None:
        self._skills: Dict[str, SkillDefinition] = {}

    def register(self, skill: SkillDefinition) -> SkillDefinition:
        if skill.id in self._skills:
            raise ValueError(f"Skill '{skill.id}' is already registered")
        self._skills[skill.id] = skill
        return skill

    def get(self, skill_id: str) -> SkillDefinition:
        try:
            return self._skills[skill_id]
        except KeyError as error:
            raise KeyError(f"Skill '{skill_id}' is not registered") from error

    def remove(self, skill_id: str) -> None:
        self.get(skill_id)
        del self._skills[skill_id]

    def list(self) -> List[SkillDefinition]:
        return list(self._skills.values())
