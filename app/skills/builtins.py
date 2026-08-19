"""Compatibility hook for declarative skills.

Revit IFC work is intentionally implemented as a filesystem skill package and one
deterministic workflow, not as the legacy step-by-step JSON skills.
"""

from app.skills.registry import SkillRegistry

REVIT_MCP_SERVER_ID = "revit_local"


def register_builtin_skills(registry: SkillRegistry) -> None:
    """Leave extension registration available without registering legacy skills."""
    return None
