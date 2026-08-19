"""Session-scoped capability API with no server-side persistence."""

from dataclasses import dataclass
from typing import Any, Dict, List, Literal, Optional

from fastapi import FastAPI, HTTPException, status
from pydantic import BaseModel, Field, model_validator

from app.agent.manus import Manus
from app.skills import SkillDefinition, SkillExecutor, SkillRegistry


class MCPServerRequest(BaseModel):
    """One MCP server configuration supplied by the host application."""

    server_id: str = Field(..., min_length=1)
    type: Literal["sse", "stdio"]
    url: Optional[str] = None
    command: Optional[str] = None
    args: List[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_connection(self) -> "MCPServerRequest":
        if self.type == "sse" and not self.url:
            raise ValueError("url is required for an SSE MCP server")
        if self.type == "stdio" and not self.command:
            raise ValueError("command is required for a stdio MCP server")
        return self


class SkillExecutionRequest(BaseModel):
    inputs: Dict[str, Any] = Field(default_factory=dict)
    approved: bool = False


@dataclass
class SessionCapabilities:
    """Runtime state owned by one business-client session."""

    agent: Manus
    skill_registry: SkillRegistry
    mcp_servers: Dict[str, MCPServerRequest]


class CapabilityManager:
    """Provides session-local tools and skills without writing configuration to disk."""

    def __init__(self) -> None:
        self._sessions: Dict[str, SessionCapabilities] = {}
        self._executor = SkillExecutor()

    def get_session(self, session_id: str) -> SessionCapabilities:
        if session_id not in self._sessions:
            agent = Manus()
            self._sessions[session_id] = SessionCapabilities(
                agent=agent,
                skill_registry=agent.skill_registry,
                mcp_servers={},
            )
        return self._sessions[session_id]

    def list_tools(self, session_id: str) -> List[Dict[str, Any]]:
        session = self.get_session(session_id)
        return [
            {
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.parameters,
            }
            for tool in session.agent.available_tools
        ]

    def list_skills(self, session_id: str) -> List[SkillDefinition]:
        return self.get_session(session_id).skill_registry.list()

    def register_skill(
        self, session_id: str, skill: SkillDefinition
    ) -> SkillDefinition:
        session = self.get_session(session_id)
        self._validate_skill(skill, session)
        return session.skill_registry.register(skill)

    def replace_skill(
        self, session_id: str, skill_id: str, skill: SkillDefinition
    ) -> SkillDefinition:
        if skill.id != skill_id:
            raise ValueError("Skill ID in the request body must match the path")
        session = self.get_session(session_id)
        self._validate_skill(skill, session)
        registry = session.skill_registry
        registry.remove(skill_id)
        return registry.register(skill)

    def remove_skill(self, session_id: str, skill_id: str) -> None:
        self.get_session(session_id).skill_registry.remove(skill_id)

    async def execute_skill(
        self, session_id: str, skill_id: str, request: SkillExecutionRequest
    ):
        session = self.get_session(session_id)
        skill = session.skill_registry.get(skill_id)
        return await self._executor.execute(
            skill=skill,
            inputs=request.inputs,
            tools=session.agent.available_tools,
            approved=request.approved,
        )

    async def add_mcp_server(
        self, session_id: str, server: MCPServerRequest
    ) -> MCPServerRequest:
        session = self.get_session(session_id)
        if server.server_id in session.mcp_servers:
            raise ValueError(f"MCP server '{server.server_id}' is already configured")
        await self._connect(session, server)
        session.mcp_servers[server.server_id] = server
        return server

    async def replace_mcp_server(
        self, session_id: str, server_id: str, server: MCPServerRequest
    ) -> MCPServerRequest:
        if server.server_id != server_id:
            raise ValueError("MCP server ID in the request body must match the path")
        session = self.get_session(session_id)
        if server_id in session.mcp_servers:
            await session.agent.disconnect_mcp_server(server_id)
            session.mcp_servers.pop(server_id)
        await self._connect(session, server)
        session.mcp_servers[server_id] = server
        return server

    async def remove_mcp_server(self, session_id: str, server_id: str) -> None:
        session = self.get_session(session_id)
        if server_id not in session.mcp_servers:
            raise KeyError(f"MCP server '{server_id}' is not configured")
        await session.agent.disconnect_mcp_server(server_id)
        del session.mcp_servers[server_id]

    @staticmethod
    async def _connect(session: SessionCapabilities, server: MCPServerRequest) -> None:
        if server.type == "sse":
            await session.agent.connect_mcp_server(server.url or "", server.server_id)
            return
        await session.agent.connect_mcp_server(
            server.command or "",
            server.server_id,
            use_stdio=True,
            stdio_args=server.args,
        )

    def _validate_skill(
        self, skill: SkillDefinition, session: SessionCapabilities
    ) -> None:
        validation_error = self._executor.validate_definition(
            skill, session.agent.available_tools
        )
        if validation_error:
            raise ValueError(validation_error)


def create_app(manager: Optional[CapabilityManager] = None) -> FastAPI:
    """Create an API instance. Passing a manager makes tests and embedding explicit."""
    capability_manager = manager or CapabilityManager()
    api = FastAPI(title="OpenManus Capability API", version="0.1.0")

    @api.get("/api/v1/revit/status")
    async def revit_status():
        return {
            "status": "available",
            "service": "capability_api",
            "revit_mcp_configured": True,
            "revit_plugin_checked": False,
        }

    def get_skill_or_404(session_id: str, skill_id: str) -> SkillDefinition:
        try:
            return capability_manager.get_session(session_id).skill_registry.get(
                skill_id
            )
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    @api.get("/sessions/{session_id}/tools")
    async def list_tools(session_id: str):
        return capability_manager.list_tools(session_id)

    @api.get("/sessions/{session_id}/skills")
    async def list_skills(session_id: str):
        return capability_manager.list_skills(session_id)

    @api.post("/sessions/{session_id}/skills", status_code=status.HTTP_201_CREATED)
    async def create_skill(session_id: str, skill: SkillDefinition):
        try:
            return capability_manager.register_skill(session_id, skill)
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error

    @api.post("/sessions/{session_id}/skills/validate")
    async def validate_skill(session_id: str, skill: SkillDefinition):
        try:
            capability_manager._validate_skill(
                skill, capability_manager.get_session(session_id)
            )
            return {"valid": True}
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error

    @api.get("/sessions/{session_id}/skills/{skill_id}")
    async def get_skill(session_id: str, skill_id: str):
        return get_skill_or_404(session_id, skill_id)

    @api.put("/sessions/{session_id}/skills/{skill_id}")
    async def update_skill(session_id: str, skill_id: str, skill: SkillDefinition):
        try:
            return capability_manager.replace_skill(session_id, skill_id, skill)
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error

    @api.delete(
        "/sessions/{session_id}/skills/{skill_id}",
        status_code=status.HTTP_204_NO_CONTENT,
    )
    async def delete_skill(session_id: str, skill_id: str):
        try:
            capability_manager.remove_skill(session_id, skill_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    @api.post("/sessions/{session_id}/skills/{skill_id}/execute")
    async def execute_skill(
        session_id: str, skill_id: str, request: SkillExecutionRequest
    ):
        get_skill_or_404(session_id, skill_id)
        return await capability_manager.execute_skill(session_id, skill_id, request)

    @api.get("/sessions/{session_id}/mcp-servers")
    async def list_mcp_servers(session_id: str):
        return list(capability_manager.get_session(session_id).mcp_servers.values())

    @api.post("/sessions/{session_id}/mcp-servers", status_code=status.HTTP_201_CREATED)
    async def create_mcp_server(session_id: str, server: MCPServerRequest):
        try:
            return await capability_manager.add_mcp_server(session_id, server)
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    @api.put("/sessions/{session_id}/mcp-servers/{server_id}")
    async def update_mcp_server(
        session_id: str, server_id: str, server: MCPServerRequest
    ):
        try:
            return await capability_manager.replace_mcp_server(
                session_id, server_id, server
            )
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error

    @api.delete(
        "/sessions/{session_id}/mcp-servers/{server_id}",
        status_code=status.HTTP_204_NO_CONTENT,
    )
    async def delete_mcp_server(session_id: str, server_id: str):
        try:
            await capability_manager.remove_mcp_server(session_id, server_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    return api


app = create_app()
