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
