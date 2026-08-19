from types import SimpleNamespace

import pytest
from mcp.types import TextContent

from app.tool.mcp import MCPClientTool


class ErrorSession:
    async def call_tool(self, name, arguments):
        return SimpleNamespace(
            isError=True,
            content=[TextContent(type="text", text="Revit plugin is unavailable")],
        )


@pytest.mark.asyncio
async def test_mcp_proxy_marks_remote_business_errors_as_tool_errors():
    tool = MCPClientTool.model_construct(
        name="mcp_revit_local_revit_prepare_assignment",
        description="test",
        parameters={},
        session=ErrorSession(),
        server_id="revit_local",
        original_name="revit_prepare_assignment",
    )

    result = await tool.execute(standard_id=109003, marjor_name="建筑")

    assert result.error == "Revit plugin is unavailable"
