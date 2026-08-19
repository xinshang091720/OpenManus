import os
import sys
from importlib import import_module
from typing import Any, Dict, List, Optional

from pydantic import Field, model_validator

from app.agent.browser import BrowserContextHelper
from app.agent.toolcall import ToolCallAgent
from app.config import MCPServerConfig, config
from app.logger import logger
from app.prompt.manus import NEXT_STEP_PROMPT, SYSTEM_PROMPT
from app.skills.registry import SkillRegistry
from app.skills.packages import SkillPackageRegistry
from app.tool.activate_skill import ActivateSkill
from app.tool.deactivate_skill import DeactivateSkill
from app.tool.ask_human import AskHuman
from app.tool.mcp import MCPClients, MCPClientTool
from app.tool.python_execute import PythonExecute
from app.tool.skill_script import SkillScriptTool
from app.tool.str_replace_editor import StrReplaceEditor
from app.tool.terminate import Terminate
from app.tool.tool_collection import ToolCollection
from app.tool.windows_app import WindowsOpenApplication
from app.workflows import RevitRunIfcAssignment


def _default_tools() -> ToolCollection:
    """Create the standard tools without forcing browser native libraries at boot."""
    tools = [
        PythonExecute(),
        StrReplaceEditor(),
        WindowsOpenApplication(),
        AskHuman(),
        ActivateSkill(),
        DeactivateSkill(),
        Terminate(),
    ]
    # BrowserUse imports Playwright and optional native dependencies. Keep it
    # available in source development, but require explicit opt-in in a frozen
    # desktop Runtime until its browser distribution is installed and tested.
    configured = os.environ.get("BEESYNC_ENABLE_BROWSER")
    if configured is None:
        # A frozen Runtime must remain usable even when optional browser native
        # dependencies are not compatible with the target Windows machine.
        # Development from source keeps the convenient historical default.
        enable_browser = not getattr(sys, "frozen", False)
    else:
        enable_browser = configured.strip().lower() in {"1", "true", "yes", "on"}
    if enable_browser:
        try:
            browser_tool_type = import_module("app.tool.browser_use_tool").BrowserUseTool
            crawler_tool_type = import_module("app.tool.crawl4ai").Crawl4aiTool
            tools.insert(1, browser_tool_type())
            tools.insert(2, crawler_tool_type())
        except Exception as error:
            # A minimal Runtime package deliberately omits browser modules.
            # Keep chat/Revit usable and make the missing capability visible.
            logger.warning(f"Browser tools are unavailable in this Runtime: {error}")
    return ToolCollection(*tools)


class Manus(ToolCallAgent):
    """A versatile general-purpose agent with support for both local and MCP tools."""

    name: str = "Manus"
    description: str = (
        "A versatile agent that can solve various tasks using multiple tools including MCP-based tools"
    )

    system_prompt: str = SYSTEM_PROMPT.format(directory=config.workspace_root)
    next_step_prompt: str = NEXT_STEP_PROMPT

    max_observe: int = 10000
    max_steps: int = 20

    # MCP clients for remote tool access
    mcp_clients: MCPClients = Field(default_factory=MCPClients)
    # Compatibility-only registry for the inactive Capability API.  It is not
    # exposed as an Agent tool; directory Skill packages are the normal path.
    skill_registry: SkillRegistry = Field(default_factory=SkillRegistry, exclude=True)
    skill_package_registry: SkillPackageRegistry = Field(
        default_factory=lambda: SkillPackageRegistry(
            config.vendor_skills_root, config.user_skills_root
        ),
        exclude=True,
    )

    # Add general-purpose tools to the tool collection
    available_tools: ToolCollection = Field(
        default_factory=_default_tools
    )

    special_tool_names: list[str] = Field(default_factory=lambda: [Terminate().name])
    browser_context_helper: Optional[BrowserContextHelper] = None
    skill_event_sink: Any = Field(default=None, exclude=True)
    mcp_server_configs: Dict[str, MCPServerConfig] = Field(default_factory=dict, exclude=True)
    active_skill_ids: list[str] = Field(default_factory=list, exclude=True)
    max_active_skills: int = Field(
        default_factory=lambda: max(1, int(os.environ.get("BEESYNC_MAX_ACTIVE_SKILLS", "3"))),
        exclude=True,
    )
    skill_mcp_server_ids: set[str] = Field(default_factory=set, exclude=True)

    # Track connected MCP servers
    connected_servers: Dict[str, str] = Field(
        default_factory=dict
    )  # server_id -> url/command
    _initialized: bool = False

    @model_validator(mode="after")
    def initialize_helper(self) -> "Manus":
        """Initialize basic components synchronously."""
        self.browser_context_helper = BrowserContextHelper(self)
        self.skill_package_registry.discover()
        self._refresh_skill_catalog_prompt()
        skill_tool = self.available_tools.get_tool("activate_skill")
        if isinstance(skill_tool, ActivateSkill):
            skill_tool.bind(self.activate_skill)
        deactivate_tool = self.available_tools.get_tool("deactivate_skill")
        if isinstance(deactivate_tool, DeactivateSkill):
            deactivate_tool.bind(self.deactivate_skill)
        return self

    @classmethod
    async def create(
        cls,
        *,
        mcp_server_configs: Optional[Dict[str, MCPServerConfig]] = None,
        **kwargs,
    ) -> "Manus":
        """Factory method to create and properly initialize a Manus instance."""
        instance = cls(**kwargs)
        instance.mcp_server_configs = (
            mcp_server_configs if mcp_server_configs is not None else config.mcp_config.servers
        )
        await instance.initialize_mcp_servers(mcp_server_configs)
        instance._initialized = True
        return instance

    async def initialize_mcp_servers(
        self, mcp_server_configs: Optional[Dict[str, MCPServerConfig]] = None
    ) -> None:
        """Initialize connections to configured MCP servers."""
        server_configs = (
            mcp_server_configs
            if mcp_server_configs is not None
            else config.mcp_config.servers
        )
        self.mcp_server_configs = server_configs
        for server_id, server_config in server_configs.items():
            if server_config.load_mode == "skill_scoped":
                continue
            try:
                if server_config.type == "sse":
                    if server_config.url:
                        await self.connect_mcp_server(server_config.url, server_id)
                        logger.info(
                            f"Connected to MCP server {server_id} at {server_config.url}"
                        )
                elif server_config.type == "stdio":
                    if server_config.command:
                        await self.connect_mcp_server(
                            server_config.command,
                            server_id,
                            use_stdio=True,
                            stdio_args=server_config.args,
                            stdio_env=server_config.env,
                        )
                        logger.info(
                            f"Connected to MCP server {server_id} using command {server_config.command}"
                        )
            except Exception as e:
                logger.error(f"Failed to connect to MCP server {server_id}: {e}")

    async def connect_mcp_server(
        self,
        server_url: str,
        server_id: str = "",
        use_stdio: bool = False,
        stdio_args: List[str] = None,
        stdio_env: Optional[Dict[str, str]] = None,
        allowed_tools: Optional[set[str]] = None,
        expose_original_names: bool = False,
    ) -> None:
        """Connect to an MCP server and add its tools."""
        if use_stdio:
            await self.mcp_clients.connect_stdio(
                server_url, stdio_args or [], server_id, env=stdio_env,
                allowed_tools=allowed_tools, expose_original_names=expose_original_names
            )
            self.connected_servers[server_id or server_url] = server_url
        else:
            await self.mcp_clients.connect_sse(
                server_url, server_id, allowed_tools=allowed_tools,
                expose_original_names=expose_original_names
            )
            self.connected_servers[server_id or server_url] = server_url

        # Update available tools with only the new tools from this server
        new_tools = [
            tool for tool in self.mcp_clients.tools if tool.server_id == server_id
        ]
        self.available_tools.add_tools(*new_tools)

    def _refresh_skill_catalog_prompt(self) -> None:
        catalog = self.skill_package_registry.catalog()
        if not catalog:
            return
        entries = "\n".join(f"- `{item['id']}`: {item['description']}" for item in catalog)
        self.system_prompt = (
            f"{SYSTEM_PROMPT.format(directory=config.workspace_root)}\n\n"
            "Available Skills (metadata only):\n"
            f"{entries}\n"
            "When a task needs one of these domains, call activate_skill with its exact id before "
            "using domain-specific tools. Do not claim a Skill is active until activation succeeds. "
            f"At most {self.max_active_skills} Skills may be active at once; call deactivate_skill "
            "for an unrelated active Skill before activating another one at the limit."
        )

    async def _emit_skill_event(self, event: str, data: dict) -> None:
        if self.skill_event_sink:
            await self.skill_event_sink(event, data)

    async def activate_skill(self, skill_id: str) -> dict:
        package = self.skill_package_registry.get(skill_id)
        if package.name not in self.active_skill_ids:
            if len(self.active_skill_ids) >= self.max_active_skills:
                raise RuntimeError(
                    f"At most {self.max_active_skills} Skills may be active. "
                    "Deactivate an unrelated Skill before activating another one."
                )
            self._validate_skill_package(package)
            self.active_skill_ids.append(package.name)
            try:
                await self._sync_active_skill_mcp_bindings()
                self._rebuild_active_skill_local_tools()
            except Exception as error:
                self.active_skill_ids.remove(package.name)
                await self._sync_active_skill_mcp_bindings()
                self._rebuild_active_skill_local_tools()
                await self._emit_skill_event(
                    "skill_activation_failed",
                    {"skill_id": package.name, "version": package.package_version, "error": str(error)},
                )
                raise
            await self._emit_skill_event(
                "skill_activated",
                {
                    "skill_id": package.name,
                    "version": package.package_version,
                    "source": package.source,
                    "summary": f"Activated {package.name} domain capabilities.",
                },
            )
        else:
            # Interactive CLI sessions keep their conversation and active Skill
            # scope between turns. ToolCallAgent cleans up MCP connections after
            # each turn, so restore only the manifest-declared bridge when the
            # model reuses a tool from an already active Skill.
            await self._sync_active_skill_mcp_bindings()
            self._rebuild_active_skill_local_tools()
        return self._skill_state_response(package)

    async def deactivate_skill(self, skill_id: str) -> dict:
        package = self.skill_package_registry.get(skill_id)
        if package.name not in self.active_skill_ids:
            raise ValueError(f"Skill '{package.name}' is not active")
        self.active_skill_ids.remove(package.name)
        try:
            await self._sync_active_skill_mcp_bindings()
            self._rebuild_active_skill_local_tools()
        except Exception:
            self.active_skill_ids.append(package.name)
            await self._sync_active_skill_mcp_bindings()
            self._rebuild_active_skill_local_tools()
            raise
        await self._emit_skill_event(
            "skill_deactivated",
            {
                "skill_id": package.name,
                "version": package.package_version,
                "active_skill_ids": list(self.active_skill_ids),
            },
        )
        return self._skill_state_response(package, deactivated=True)

    def _validate_skill_package(self, package) -> None:
        for binding in package.mcp_bindings:
            if binding.server_id not in self.mcp_server_configs:
                raise RuntimeError(
                    f"Skill '{package.name}' requires unavailable MCP server '{binding.server_id}'"
                )
        unknown = set(package.runtime_tools) - {"revit_run_ifc_assignment"}
        if unknown:
            raise RuntimeError(f"Skill '{package.name}' declares unknown Runtime tool '{sorted(unknown)[0]}'")

    def _skill_state_response(self, package, *, deactivated: bool = False) -> dict:
        return {
            "skill_id": package.name,
            "version": package.package_version,
            "instruction": "" if deactivated else package.instruction(),
            "active_skill_ids": list(self.active_skill_ids),
            "max_active_skills": self.max_active_skills,
            "active_tools": [tool.name for tool in self.available_tools],
        }

    def _rebuild_active_skill_local_tools(self) -> None:
        """Keep base tools, then recreate local capabilities owned by active Skills."""
        base_tools = [
            tool
            for tool in self.available_tools.tools
            if not isinstance(tool, (MCPClientTool, SkillScriptTool, RevitRunIfcAssignment))
        ]
        self.available_tools = ToolCollection(*base_tools)
        for skill_id in self.active_skill_ids:
            package = self.skill_package_registry.get(skill_id)
            for runtime_tool in package.runtime_tools:
                if runtime_tool == "revit_run_ifc_assignment":
                    self.available_tools.add_tool(RevitRunIfcAssignment(event_sink=self.skill_event_sink))
            for definition in package.script_tools:
                self.available_tools.add_tool(
                    SkillScriptTool(
                        name=definition.name,
                        description=definition.description,
                        parameters=definition.parameters,
                        script_path=definition.script,
                        skill_id=package.name,
                    )
                )
        self.available_tools.add_tools(*self.mcp_clients.tools)

    async def _sync_active_skill_mcp_bindings(self) -> None:
        """Reconnect each Skill-scoped MCP server once with the union of active tools."""
        requested: dict[str, set[str]] = {}
        for skill_id in self.active_skill_ids:
            package = self.skill_package_registry.get(skill_id)
            for binding in package.mcp_bindings:
                requested.setdefault(binding.server_id, set()).update(binding.tools)

        managed_servers = self.skill_mcp_server_ids | set(requested)
        for server_id in sorted(managed_servers):
            if server_id in self.connected_servers:
                await self.disconnect_mcp_server(server_id)
        self.skill_mcp_server_ids = set()

        for server_id, allowed_tools in requested.items():
            server = self.mcp_server_configs.get(server_id)
            if server is None:
                raise RuntimeError(f"Active Skill requires unavailable MCP server '{server_id}'")
            await self.connect_mcp_server(
                server.url or server.command or "",
                server_id,
                use_stdio=server.type == "stdio",
                stdio_args=server.args,
                stdio_env=server.env,
                allowed_tools=allowed_tools,
                expose_original_names=True,
            )
            self.skill_mcp_server_ids.add(server_id)

    async def _restore_skill_mcp_binding_for_tool(
        self, package, tool_name: Optional[str]
    ) -> bool:
        """Reconnect a declared Skill-scoped MCP bridge after per-turn cleanup.

        ``tool_name`` is checked against the manifest rather than task text, so
        this never becomes a keyword-based Skill router.  ``None`` restores all
        bindings for an explicit repeat activation.
        """
        owns_requested_tool = tool_name is None or any(
            tool_name in binding.tools for binding in package.mcp_bindings
        )
        if not owns_requested_tool:
            return False
        await self._sync_active_skill_mcp_bindings()
        self._rebuild_active_skill_local_tools()
        return bool(package.mcp_bindings)

    async def execute_tool(self, command) -> str:
        """Restore an already active Skill's MCP bridge just before tool use."""
        tool_name = getattr(getattr(command, "function", None), "name", None)
        if tool_name and tool_name not in self.available_tools.tool_map:
            for skill_id in self.active_skill_ids:
                package = self.skill_package_registry.get(skill_id)
                if await self._restore_skill_mcp_binding_for_tool(package, tool_name):
                    break
        return await super().execute_tool(command)

    async def disconnect_mcp_server(self, server_id: str = "") -> None:
        """Disconnect from an MCP server and remove its tools."""
        await self.mcp_clients.disconnect(server_id)
        if server_id:
            self.connected_servers.pop(server_id, None)
        else:
            self.connected_servers.clear()

        # Rebuild available tools without the disconnected server's tools
        base_tools = [
            tool
            for tool in self.available_tools.tools
            if not isinstance(tool, MCPClientTool)
        ]
        self.available_tools = ToolCollection(*base_tools)
        self.available_tools.add_tools(*self.mcp_clients.tools)

    async def cleanup(self):
        """Clean up Manus agent resources."""
        if self.browser_context_helper:
            await self.browser_context_helper.cleanup_browser()
        # Disconnect from all MCP servers only if we were initialized
        if self._initialized:
            await self.disconnect_mcp_server()
            self._initialized = False

    async def think(self) -> bool:
        """Process current state and decide next actions with appropriate context."""
        if not self._initialized:
            await self.initialize_mcp_servers()
            self._initialized = True

        original_prompt = self.next_step_prompt
        recent_messages = self.memory.messages[-3:] if self.memory.messages else []
        browser_in_use = any(
            tc.function.name == "browser_use"
            for msg in recent_messages
            if msg.tool_calls
            for tc in msg.tool_calls
        )

        if browser_in_use:
            self.next_step_prompt = (
                await self.browser_context_helper.format_next_step_prompt()
            )

        result = await super().think()

        # Restore original prompt
        self.next_step_prompt = original_prompt

        return result
