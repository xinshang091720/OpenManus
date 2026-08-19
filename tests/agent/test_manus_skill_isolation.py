from app.agent.manus import Manus


def test_skill_packages_do_not_replace_general_tool_collection():
    """A task-specific Skill may guide the agent, but must not hide base tools."""
    agent = Manus()

    tool_names_before = {tool.name for tool in agent.available_tools}
    assert agent.skill_package_registry.get("revit-ifc-assignment") is not None
    tool_names_after = {tool.name for tool in agent.available_tools}

    assert "browser_use" in tool_names_before
    assert tool_names_after == tool_names_before
