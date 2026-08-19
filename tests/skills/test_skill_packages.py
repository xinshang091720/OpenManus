import asyncio
from pathlib import Path

from app.agent.manus import Manus
from app.config import MCPServerConfig
from app.skills.packages import SkillPackageRegistry
from app.tool.skill_script import SkillScriptTool
from app.tool.tool_collection import ToolCollection


def test_revit_mcp_does_not_expose_standalone_parameter_clear():
    from app.mcp.server import MCPServer

    server = MCPServer()
    try:
        assert "revit_clear_parameters" not in server.tools
    finally:
        server.revit_process_lock.close()
        server.sz_ifc_lock.close()


def test_catalog_discovers_metadata_without_keyword_router():
    root = Path(__file__).resolve().parents[2] / "skills"
    registry = SkillPackageRegistry(root)
    catalog = registry.discover()

    assert {item.name for item in catalog} >= {"revit-ifc-assignment", "pdf"}
    assert not hasattr(registry, "match")
    assert registry.get("revit-ifc-assignment").description


def test_revit_manifest_exposes_only_high_level_agent_tools():
    root = Path(__file__).resolve().parents[2] / "skills"
    package = SkillPackageRegistry(root).discover()[0]
    package = SkillPackageRegistry(root)
    package.discover()
    revit = package.get("revit-ifc-assignment")

    assert revit.runtime_tools == ("revit_run_ifc_assignment",)
    assert set(revit.mcp_bindings[0].tools) == {
        "revit_launch_application",
        "revit_launch_versioned_model",
        "revit_open_project_model",
        "revit_open_file",
        "revit_save_as",
        "revit_export_ifc",
        "sz_ifc_open_model",
        "revit_inspect_ifc",
    }


def test_project_delivery_skill_exposes_the_complete_local_revit_chain():
    root = Path(__file__).resolve().parents[2] / "skills"
    registry = SkillPackageRegistry(root)
    registry.discover()
    delivery = registry.get("revit-project-delivery")

    assert delivery.runtime_tools == ("revit_run_ifc_assignment",)
    assert {
        "revit_set_base_point",
        "revit_create_and_name_ar_rooms",
        "revit_export_ifc",
        "revit_open_project_model",
        "ensure_autocad_running",
        "sz_ifc_open_model",
        "revit_inspect_ifc",
        "revit_save_as",
    } <= set(delivery.mcp_bindings[0].tools)
    assert "revit_clear_parameters" not in delivery.mcp_bindings[0].tools

    instructions = (root / "revit-project-delivery" / "SKILL.md").read_text(encoding="utf-8")
    assert "ask_human" in instructions
    assert "当前收到“已准备好”：直接自检" in instructions
    assert "不因“质检”自动执行 IFC 标识赋值" in instructions
    assert "Skill 每次激活只是加载工具说明" in instructions
    assert "每一次 IFC 交付或质量检查" not in instructions


def test_agent_can_activate_project_delivery_with_one_scoped_revit_bridge(monkeypatch):
    async def scenario():
        agent = Manus()
        connected = []

        async def fake_connect(*args, **kwargs):
            connected.append(kwargs["allowed_tools"])

        monkeypatch.setattr(agent, "connect_mcp_server", fake_connect)
        agent.mcp_server_configs = {
            "revit_local": MCPServerConfig(
                type="stdio", command="fake", load_mode="skill_scoped", skill="revit-ifc-assignment"
            )
        }
        result = await agent.activate_skill("revit-project-delivery")
        return connected, result

    connected, result = asyncio.run(scenario())
    assert len(connected) == 1
    assert {
        "revit_set_base_point",
        "revit_create_and_name_ar_rooms",
        "revit_export_ifc",
        "sz_ifc_open_model",
        "revit_inspect_ifc",
    } <= connected[0]
    assert "revit_run_ifc_assignment" in result["active_tools"]


def test_agent_supports_three_active_skills_and_merges_shared_mcp_tools(monkeypatch):
    async def scenario():
        agent = Manus()
        connections = []
        disconnections = []

        async def fake_connect(*args, **kwargs):
            server_id = kwargs.get("server_id", args[1])
            connections.append((server_id, set(kwargs["allowed_tools"])))
            agent.connected_servers[server_id] = "fake"

        async def fake_disconnect(server_id=""):
            disconnections.append(server_id)
            agent.connected_servers.pop(server_id, None)

        monkeypatch.setattr(agent, "connect_mcp_server", fake_connect)
        monkeypatch.setattr(agent, "disconnect_mcp_server", fake_disconnect)
        agent.mcp_server_configs = {
            "revit_local": MCPServerConfig(
                type="stdio", command="fake", load_mode="skill_scoped", skill="revit-ifc-assignment"
            )
        }
        first = await agent.activate_skill("revit-room-sync")
        second = await agent.activate_skill("revit-project-delivery")
        third = await agent.activate_skill("revit-ifc-assignment")
        fourth_error = None
        try:
            await agent.activate_skill("pdf")
        except RuntimeError as error:
            fourth_error = str(error)
        removed = await agent.deactivate_skill("revit-room-sync")
        return connections, disconnections, first, second, third, fourth_error, removed

    connections, disconnections, first, second, third, fourth_error, removed = asyncio.run(scenario())
    assert first["active_skill_ids"] == ["revit-room-sync"]
    assert second["active_skill_ids"] == ["revit-room-sync", "revit-project-delivery"]
    assert third["active_skill_ids"] == [
        "revit-room-sync", "revit-project-delivery", "revit-ifc-assignment"
    ]
    assert "At most 3 Skills" in fourth_error
    assert removed["active_skill_ids"] == ["revit-project-delivery", "revit-ifc-assignment"]
    assert disconnections == ["revit_local", "revit_local", "revit_local"]
    final_tools = connections[-1][1]
    assert "revit_create_and_name_ar_rooms" in final_tools
    assert "revit_run_ifc_assignment" not in final_tools  # Runtime tool, not an MCP binding.
    assert "revit_preview_room_sync" not in final_tools




def test_skill_package_does_not_duplicate_an_identical_user_directory(tmp_path):
    root = tmp_path / "skills"
    package = root / "sample"
    package.mkdir(parents=True)
    (package / "SKILL.md").write_text(
        "---\nname: sample\ndescription: sample skill\n---\nInstructions",
        encoding="utf-8",
    )

    packages = SkillPackageRegistry(root, root).discover()

    assert [item.name for item in packages] == ["sample"]


def test_agent_hides_revit_tools_until_model_activates_skill(monkeypatch):
    async def scenario():
        agent = Manus()
        assert not any("revit" in tool.name for tool in agent.available_tools)
        connected = []

        async def fake_connect(*args, **kwargs):
            connected.append(kwargs["allowed_tools"])

        monkeypatch.setattr(agent, "connect_mcp_server", fake_connect)
        agent.mcp_server_configs = {
            "revit_local": MCPServerConfig(
                type="stdio",
                command="fake-revit-mcp",
                load_mode="skill_scoped",
                skill="revit-ifc-assignment",
            )
        }
        result = await agent.activate_skill("revit-ifc-assignment")
        return agent, connected, result

    agent, connected, result = asyncio.run(scenario())
    assert connected
    assert "revit_run_ifc_assignment" in result["active_tools"]
    assert "revit_run_ifc_assignment" in [tool.name for tool in agent.available_tools]


def test_skill_activation_emits_a_structured_event(monkeypatch):
    async def scenario():
        events = []

        async def sink(event, data):
            events.append((event, data))

        agent = Manus(skill_event_sink=sink)

        async def fake_connect(*args, **kwargs):
            return None

        monkeypatch.setattr(agent, "connect_mcp_server", fake_connect)
        agent.mcp_server_configs = {
            "revit_local": MCPServerConfig(
                type="stdio", command="fake", load_mode="skill_scoped", skill="revit-ifc-assignment"
            )
        }
        await agent.activate_skill("revit-ifc-assignment")
        return events

    events = asyncio.run(scenario())
    assert events[0][0] == "skill_activated"
    assert events[0][1]["skill_id"] == "revit-ifc-assignment"


def test_active_skill_restores_its_mcp_binding_after_turn_cleanup(monkeypatch):
    async def scenario():
        agent = Manus()
        connected = []

        async def fake_connect(*args, **kwargs):
            connected.append(kwargs["allowed_tools"])

        monkeypatch.setattr(agent, "connect_mcp_server", fake_connect)
        agent.mcp_server_configs = {
            "revit_local": MCPServerConfig(
                type="stdio", command="fake", load_mode="skill_scoped", skill="revit-ifc-assignment"
            )
        }
        await agent.activate_skill("revit-ifc-assignment")
        # Simulate ToolCallAgent cleanup: scoped MCP tools are gone but the
        # interactive conversation still has its explicitly activated Skill.
        agent.available_tools = ToolCollection()
        package = agent.skill_package_registry.get("revit-ifc-assignment")
        restored = await agent._restore_skill_mcp_binding_for_tool(
            package, "revit_export_ifc"
        )
        return restored, connected

    restored, connected = asyncio.run(scenario())
    assert restored is True
    assert len(connected) == 2
    assert "revit_export_ifc" in connected[-1]
    assert "revit_clear_parameters" not in connected[-1]


def test_declared_skill_script_is_loaded_without_shell_or_pip(tmp_path):
    script = tmp_path / "script.py"
    script.write_text("def run(value):\n    return {'value': value}\n", encoding="utf-8")
    tool = SkillScriptTool(
        name="sample_script",
        description="sample",
        parameters={"type": "object", "properties": {"value": {"type": "string"}}},
        script_path=script,
        skill_id="sample",
    )

    result = asyncio.run(tool.execute(value="ok"))

    assert result.error is None
    assert '"value": "ok"' in result.output


def test_pdf_skill_registers_packaged_pdf_tools(tmp_path):
    from pypdf import PdfWriter

    async def scenario():
        source = tmp_path / "sample.pdf"
        writer = PdfWriter()
        writer.add_blank_page(width=100, height=100)
        with source.open("wb") as stream:
            writer.write(stream)
        agent = Manus()
        await agent.activate_skill("pdf")
        result = await agent.available_tools.get_tool("pdf_verify").execute(input_path=str(source))
        return [tool.name for tool in agent.available_tools], result

    tool_names, result = asyncio.run(scenario())
    assert "pdf_extract_text" in tool_names
    assert "pdf_verify" in tool_names
    assert result.error is None
    assert '"valid": true' in result.output
