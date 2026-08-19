from app.agent.manus import Manus
from app.skills import REVIT_MCP_SERVER_ID


def test_manus_discovers_filesystem_revit_skill_without_legacy_builtins():
    agent = Manus()
    skills = {skill.id: skill for skill in agent.skill_registry.list()}

    assert skills == {}
    assert agent.skill_package_registry.get("revit-ifc-assignment") is not None
    direct_revit_tools = {
        tool.name for tool in agent.available_tools if tool.name.startswith("revit_")
    }
    assert not direct_revit_tools
