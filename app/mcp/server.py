import logging
import sys
import argparse
import asyncio
import json
from inspect import Parameter, Signature
from typing import Any, Dict, Optional, Union

from mcp.server.fastmcp import FastMCP

from app.logger import logger
from app.mcp.revit_lock import RevitProcessLock
from app.mcp.sz_ifc_lock import SzIfcProcessLock
from app.revit import RevitApiClient, RevitApiError
from app.tool.base import BaseTool
from app.tool.revit import (
    RevitApplyAssignment,
    RevitGetIfcIdentifiers,
    RevitGetLevelIfcIdentifiers,
    RevitOpenFile,
    RevitPrepareAssignment,
    RevitAssignIfcIdentifiers,
    RevitSaveAs,
)
from app.tool.revit_rooms import RevitApplyRoomSync, RevitPreviewRoomSync
from app.tool.revit_delivery import (
    RevitExportIfc,
    RevitInspectIfc,
    RevitSetBasePoint,
)
from app.tool.revit_room_creation import RevitCreateAndNameArRooms
from app.tool.windows_app import RevitLaunchApplication, RevitLaunchVersionedModel
from app.tool.desktop_bim import (
    EnsureAutocadRunning,
    RevitOpenProjectModel,
    SzIfcOpenModel,
)


logging.basicConfig(level=logging.INFO, handlers=[logging.StreamHandler(sys.stderr)])


class MCPServer:
    """MCP Server implementation with tool registration and management."""

    def __init__(self, name: str = "openmanus"):
        self.server = FastMCP(name)
        self.tools: Dict[str, BaseTool] = {}
        # Revit's external-event API is single-threaded.  The lock is owned by
        # the bridge, so concurrent MCP clients cannot overlap plugin calls.
        self.revit_api_lock = asyncio.Lock()
        self.revit_process_lock = RevitProcessLock()
        self.sz_ifc_lock = SzIfcProcessLock()

        # This server is a Revit-plugin bridge.  Do not expose unrelated shell,
        # browser, or filesystem tools to an agent connected for a Revit task.
        self.tools["revit_open_file"] = RevitOpenFile()
        self.tools["revit_launch_application"] = RevitLaunchApplication()
        self.tools["revit_launch_versioned_model"] = RevitLaunchVersionedModel()
        self.tools["revit_open_project_model"] = RevitOpenProjectModel()
        self.tools["ensure_autocad_running"] = EnsureAutocadRunning()
        self.tools["sz_ifc_open_model"] = SzIfcOpenModel()
        self.tools["revit_prepare_assignment"] = RevitPrepareAssignment()
        self.tools["revit_get_level_ifc_identifiers"] = RevitGetLevelIfcIdentifiers()
        self.tools["revit_get_ifc_identifiers"] = RevitGetIfcIdentifiers()
        self.tools["revit_apply_assignment"] = RevitApplyAssignment()
        self.tools["revit_save_as"] = RevitSaveAs()
        self.tools["revit_assign_ifc_identifiers"] = RevitAssignIfcIdentifiers()
        self.tools["revit_preview_room_sync"] = RevitPreviewRoomSync()
        self.tools["revit_apply_room_sync"] = RevitApplyRoomSync()
        self.tools["revit_create_and_name_ar_rooms"] = RevitCreateAndNameArRooms()
        self.tools["revit_set_base_point"] = RevitSetBasePoint()
        self.tools["revit_export_ifc"] = RevitExportIfc()
        self.tools["revit_inspect_ifc"] = RevitInspectIfc()

    async def _execute_revit_tool(
        self, tool_name: str, tool: BaseTool, kwargs: Dict[str, Any]
    ) -> Any:
        """Run one Revit operation at a time for the whole MCP server."""
        if tool_name in {"sz_ifc_open_model", "revit_inspect_ifc"}:
            logger.info(f"Waiting for SZ-IFC desktop slot: {tool_name}")
            async with self.sz_ifc_lock.hold():
                return await tool.execute(**kwargs)
        if tool_name == "ensure_autocad_running":
            return await tool.execute(**kwargs)
        logger.info(f"Waiting for Revit API slot: {tool_name}")
        async with self.revit_api_lock:
            async with self.revit_process_lock.hold():
                client = getattr(tool, "client", None)
                # The project opener must be allowed to start Revit when the
                # plugin is not online yet.  It performs its own plugin call
                # only when an existing compatible instance is discovered.
                if isinstance(client, RevitApiClient) and tool_name != "revit_open_project_model":
                    try:
                        await client.ensure_plugin_ready()
                    except RevitApiError as error:
                        return tool.fail_response(str(error))
                logger.info(f"Executing Revit tool: {tool_name}")
                return await tool.execute(**kwargs)

    def register_tool(self, tool: BaseTool, method_name: Optional[str] = None) -> None:
        """Register a tool with parameter validation and documentation."""
        tool_name = method_name or tool.name
        tool_param = tool.to_param()
        tool_function = tool_param["function"]

        # Define the async function to be registered
        async def tool_method(**kwargs):
            result = await self._execute_revit_tool(tool_name, tool, kwargs)

            logger.info(
                f"Completed {tool_name}: {'failed' if result.error else 'succeeded'}"
            )

            if result.error:
                # FastMCP converts raised exceptions into an MCP result with
                # ``isError=true``.  Returning a JSON string here made the
                # Runtime and Agent treat failed desktop operations as success.
                raise RuntimeError(result.error)
            if hasattr(result, "output"):
                return result.output or "No output returned."
            if isinstance(result, dict):
                return json.dumps(result)
            return result

        # Set method metadata
        tool_method.__name__ = tool_name
        tool_method.__doc__ = self._build_docstring(tool_function)
        tool_method.__signature__ = self._build_signature(tool_function)

        # Store parameter schema (important for tools that access it programmatically)
        param_props = tool_function.get("parameters", {}).get("properties", {})
        required_params = tool_function.get("parameters", {}).get("required", [])
        tool_method._parameter_schema = {
            param_name: {
                "description": param_details.get("description", ""),
                "type": param_details.get("type", "any"),
                "required": param_name in required_params,
            }
            for param_name, param_details in param_props.items()
        }

        # Register with server
        self.server.tool()(tool_method)
        logger.info(f"Registered tool: {tool_name}")

    def _build_docstring(self, tool_function: dict) -> str:
        """Build a formatted docstring from tool function metadata."""
        description = tool_function.get("description", "")
        param_props = tool_function.get("parameters", {}).get("properties", {})
        required_params = tool_function.get("parameters", {}).get("required", [])

        # Build docstring (match original format)
        docstring = description
        if param_props:
            docstring += "\n\nParameters:\n"
            for param_name, param_details in param_props.items():
                required_str = (
                    "(required)" if param_name in required_params else "(optional)"
                )
                param_type = param_details.get("type", "any")
                param_desc = param_details.get("description", "")
                docstring += (
                    f"    {param_name} ({param_type}) {required_str}: {param_desc}\n"
                )

        return docstring

    def _build_signature(self, tool_function: dict) -> Signature:
        """Build a function signature from tool function metadata."""
        param_props = tool_function.get("parameters", {}).get("properties", {})
        required_params = tool_function.get("parameters", {}).get("required", [])

        parameters = []

        # Follow original type mapping
        for param_name, param_details in param_props.items():
            param_type = param_details.get("type", "")
            is_required = param_name in required_params
            default = (
                Parameter.empty
                if is_required
                else param_details.get("default", None)
            )

            # Map JSON Schema types to Python types
            base_type = Any
            if param_type == "string":
                base_type = Union[str, int, float]
            elif param_type in ("integer", "number"):
                base_type = Union[int, float, str]
            elif param_type == "boolean":
                base_type = Union[bool, str, int]
            elif param_type == "object":
                base_type = Union[dict, str]
            elif param_type == "array":
                base_type = Union[list, str]
            elif isinstance(param_type, list):
                base_type = Union[str, int, float]

            annotation = base_type if is_required else Optional[base_type]

            # Create parameter with same structure as original
            param = Parameter(
                name=param_name,
                kind=Parameter.KEYWORD_ONLY,
                default=default,
                annotation=annotation,
            )
            parameters.append(param)

        return Signature(parameters=parameters)

    async def cleanup(self) -> None:
        """Clean up server resources."""
        logger.info("Cleaning up resources")
        # Follow original cleanup logic - only clean browser tool
        if "browser" in self.tools and hasattr(self.tools["browser"], "cleanup"):
            await self.tools["browser"].cleanup()
        self.revit_process_lock.close()
        self.sz_ifc_lock.close()

    def register_all_tools(self) -> None:
        """Register all tools with the server."""
        for tool in self.tools.values():
            self.register_tool(tool)

    def run(self, transport: str = "stdio") -> None:
        """Run the MCP server."""
        # Register all tools
        self.register_all_tools()

        # Start server (with same logging as original)
        logger.info(f"Starting OpenManus server ({transport} mode)")
        self.server.run(transport=transport)


def parse_args() -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(description="OpenManus MCP Server")
    parser.add_argument(
        "--transport",
        choices=["stdio", "sse"],
        default="stdio",
        help="Communication method: stdio or SSE (default: stdio)",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()

    # Create and run server (maintaining original flow)
    server = MCPServer()
    server.run(transport=args.transport)
