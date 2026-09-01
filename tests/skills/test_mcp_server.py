import asyncio
import sys

from app.mcp.server import MCPServer, parse_args
from app.tool.mcp import MCPClients
from app.tool.revit_delivery import RevitExportAndInspectIfc, RevitExportIfc


def test_mcp_server_owns_revit_workflow_tools_without_standard_database_tool():
    server = MCPServer()

    assert "revit_prepare_assignment" in server.tools
    assert "revit_open_file" in server.tools
    assert "revit_launch_application" in server.tools
    assert "revit_launch_versioned_model" in server.tools
    assert "revit_open_project_model" in server.tools
    assert "ensure_autocad_running" in server.tools
    assert "sz_ifc_open_model" in server.tools
    assert "revit_save_as" in server.tools
    assert "revit_assign_ifc_identifiers" in server.tools
    assert all("quality" not in tool_name for tool_name in server.tools)
    assert "revit_export_and_inspect_ifc" not in server.tools
    assert "lookup_standard_objects" not in server.tools
    assert "bash" not in server.tools
    assert "browser" not in server.tools
    assert "editor" not in server.tools


def test_mcp_server_accepts_sse_transport(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["run_mcp_server.py", "--transport", "sse"])

    assert parse_args().transport == "sse"


def test_mcp_server_serializes_revit_plugin_calls():
    class BlockingTool:
        def __init__(self):
            self.running = 0
            self.max_running = 0

        async def execute(self, **kwargs):
            self.running += 1
            self.max_running = max(self.max_running, self.running)
            await asyncio.sleep(0)
            self.running -= 1
            return kwargs["value"]

    async def run_concurrently():
        server = MCPServer()
        tool = BlockingTool()
        return await asyncio.gather(
            server._execute_revit_tool("first", tool, {"value": 1}),
            server._execute_revit_tool("second", tool, {"value": 2}),
        ), tool.max_running

    results, max_running = asyncio.run(run_concurrently())

    assert results == [1, 2]
    assert max_running == 1


def test_mcp_client_connections_are_not_shared_between_agents():
    first = MCPClients()
    second = MCPClients()
    first.sessions["first"] = object()

    assert "first" not in second.sessions


def test_mcp_signature_preserves_optional_schema_defaults():
    tool = RevitExportIfc()
    signature = MCPServer()._build_signature(tool.to_param()["function"])

    assert signature.parameters["timeout_seconds"].default == 7200


def test_combined_ifc_delivery_tool_exposes_a_long_wait_default():
    tool = RevitExportAndInspectIfc()
    signature = MCPServer()._build_signature(tool.to_param()["function"])

    assert signature.parameters["timeout_seconds"].default == 7200


def test_mcp_signature_for_revit_set_base_point_allows_optional_parameters():
    from app.tool.revit_delivery import RevitSetBasePoint

    tool = RevitSetBasePoint()
    server = MCPServer()
    server.register_tool(tool)

    signature = server._build_signature(tool.to_param()["function"])
    assert signature.parameters["north_south"].default is None
    assert signature.parameters["dwg_path"].default is None
    registered = server.server._tool_manager.get_tool("revit_set_base_point")
    assert registered is not None

    # Test validating float and string inputs via FastMCP's generated arg_model
    model = registered.fn_metadata.arg_model
    parsed_nums = model.model_validate({
        "north_south": 2506045399.0,
        "east_west": 507491908.0,
        "elevation": 73900.0,
        "angle_to_north": 216,
    })
    assert parsed_nums.north_south == 2506045399.0
    assert parsed_nums.dwg_path is None

    parsed_strs = model.model_validate({
        "north_south": "2506045399.000",
        "east_west": "507491908.000",
        "elevation": "73900.000",
        "angle_to_north": "216",
    })
    assert parsed_strs.north_south == "2506045399.000"
    assert parsed_strs.dwg_path is None
