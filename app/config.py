import json
import logging
import os
import sys
import threading
import tomllib
from pathlib import Path
from typing import Dict, List, Optional

from pydantic import BaseModel, Field, model_validator

from app.runtime_paths import runtime_paths


DEFAULT_LLM_BASE_URL = (
    "https://llm-31571n47vq8n3p94.cn-beijing.maas.aliyuncs.com/compatible-mode/v1"
)
LLM_API_KEY_PLACEHOLDERS = {
    "",
    "BEESYNC_HOST_MUST_INJECT_API_KEY",
    "YOUR_API_KEY",
}


def llm_api_key_is_configured(value: str | None) -> bool:
    return bool(value and value.strip() not in LLM_API_KEY_PLACEHOLDERS)


def get_project_root() -> Path:
    """Get the project root directory"""
    return Path(__file__).resolve().parent.parent


# Kept for compatibility with existing modules.  In a packaged build these
# values resolve to the versioned Runtime installation and per-user workspace.
PROJECT_ROOT = runtime_paths.resource_root
WORKSPACE_ROOT = runtime_paths.workspace_dir


class LLMSettings(BaseModel):
    model: str = Field(..., description="Model name")
    base_url: str = Field(..., description="API base URL")
    api_key: str = Field(..., description="API key")
    max_tokens: int = Field(4096, description="Maximum number of tokens per request")
    max_input_tokens: Optional[int] = Field(
        None,
        description="Maximum input tokens to use across all requests (None for unlimited)",
    )
    temperature: float = Field(1.0, description="Sampling temperature")
    api_type: str = Field(..., description="Azure, Openai, or Ollama")
    api_version: str = Field(..., description="Azure Openai version if AzureOpenai")
    enable_thinking: Optional[bool] = Field(
        None,
        description="Enable provider thinking mode when the compatible API supports it.",
    )


class ProxySettings(BaseModel):
    server: str = Field(None, description="Proxy server address")
    username: Optional[str] = Field(None, description="Proxy username")
    password: Optional[str] = Field(None, description="Proxy password")


class SearchSettings(BaseModel):
    engine: str = Field(default="Google", description="Search engine the llm to use")
    fallback_engines: List[str] = Field(
        default_factory=lambda: ["DuckDuckGo", "Baidu", "Bing"],
        description="Fallback search engines to try if the primary engine fails",
    )
    retry_delay: int = Field(
        default=60,
        description="Seconds to wait before retrying all engines again after they all fail",
    )
    max_retries: int = Field(
        default=3,
        description="Maximum number of times to retry all engines when all fail",
    )
    lang: str = Field(
        default="en",
        description="Language code for search results (e.g., en, zh, fr)",
    )
    country: str = Field(
        default="us",
        description="Country code for search results (e.g., us, cn, uk)",
    )


class StandardDataSettings(BaseModel):
    """Connection settings for the business standard-object catalogue."""

    host: str
    port: int = Field(3306, description="MySQL port")
    database: str
    username: str
    password: str
    connect_timeout: int = Field(10, description="Database connection timeout")


class RunflowSettings(BaseModel):
    use_data_analysis_agent: bool = Field(
        default=False, description="Enable data analysis agent in run flow"
    )


class BrowserSettings(BaseModel):
    headless: bool = Field(False, description="Whether to run browser in headless mode")
    disable_security: bool = Field(
        True, description="Disable browser security features"
    )
    extra_chromium_args: List[str] = Field(
        default_factory=list, description="Extra arguments to pass to the browser"
    )
    chrome_instance_path: Optional[str] = Field(
        None, description="Path to a Chrome instance to use"
    )
    wss_url: Optional[str] = Field(
        None, description="Connect to a browser instance via WebSocket"
    )
    cdp_url: Optional[str] = Field(
        None, description="Connect to a browser instance via CDP"
    )
    proxy: Optional[ProxySettings] = Field(
        None, description="Proxy settings for the browser"
    )
    max_content_length: int = Field(
        2000, description="Maximum length for content retrieval operations"
    )


class MCPServerConfig(BaseModel):
    """Configuration for a single MCP server"""

    type: str = Field(..., description="Server connection type (sse or stdio)")
    url: Optional[str] = Field(None, description="Server URL for SSE connections")
    command: Optional[str] = Field(None, description="Command for stdio connections")
    args: List[str] = Field(
        default_factory=list, description="Arguments for stdio command"
    )
    env: Dict[str, str] = Field(
        default_factory=dict,
        description="Explicit environment variables for a stdio MCP child process",
    )
    enabled: bool = Field(True, description="Whether this server is enabled")
    load_mode: str = Field(
        "eager",
        description="eager connects at Agent startup; skill_scoped connects only after its Skill is activated",
    )
    skill: Optional[str] = Field(None, description="Owning Skill id when load_mode is skill_scoped")

    @model_validator(mode="after")
    def validate_transport(self) -> "MCPServerConfig":
        if self.type not in {"stdio", "sse"}:
            raise ValueError("MCP type must be stdio or sse")
        if self.type == "stdio" and not self.command:
            raise ValueError("stdio MCP requires command")
        if self.type == "sse" and not self.url:
            raise ValueError("sse MCP requires url")
        if self.load_mode not in {"eager", "skill_scoped"}:
            raise ValueError("MCP load_mode must be eager or skill_scoped")
        if self.load_mode == "skill_scoped" and not self.skill:
            raise ValueError("skill_scoped MCP requires skill")
        return self


class MCPSettings(BaseModel):
    """Configuration for MCP (Model Context Protocol)"""

    server_reference: str = Field(
        "app.mcp.server", description="Module reference for the MCP server"
    )
    servers: Dict[str, MCPServerConfig] = Field(
        default_factory=dict, description="MCP server configurations"
    )

    @classmethod
    def load_server_config(cls) -> Dict[str, MCPServerConfig]:
        """Load official and current-user MCP configuration without overrides."""
        servers: Dict[str, MCPServerConfig] = {}
        official_candidates = [runtime_paths.config_dir / "mcp.json"]
        bundled = runtime_paths.resource_root / "config" / "mcp.json"
        if bundled not in official_candidates:
            official_candidates.append(bundled)

        for config_path in official_candidates:
            if config_path.exists():
                cls._load_file(config_path, servers, prefix="")
                break

        # A user configuration receives a namespace derived from its filename.
        # It can add a server but can never replace a vendor-managed server.
        if runtime_paths.user_mcp_dir.exists():
            for config_path in sorted(runtime_paths.user_mcp_dir.glob("*.json")):
                cls._load_file(config_path, servers, prefix=f"user_{config_path.stem}_")
        return servers

    @staticmethod
    def _load_file(
        config_path: Path,
        servers: Dict[str, MCPServerConfig],
        *,
        prefix: str,
    ) -> None:
        try:
            with config_path.open(encoding="utf-8") as file:
                data = json.load(file)
            items = data.get("mcpServers", {})
            if not isinstance(items, dict):
                raise ValueError("mcpServers must be an object")
            for server_id, server_config in items.items():
                if not isinstance(server_id, str) or not isinstance(server_config, dict):
                    raise ValueError("MCP server entries must be named objects")
                server = MCPServerConfig(
                    type=server_config["type"],
                    url=server_config.get("url"),
                    command=server_config.get("command"),
                    args=server_config.get("args", []),
                    env=server_config.get("env", {}),
                    enabled=server_config.get("enabled", True),
                    load_mode=server_config.get("load_mode", "eager"),
                    skill=server_config.get("skill"),
                )
                if not server.enabled:
                    continue
                namespaced_id = f"{prefix}{server_id}"
                if namespaced_id in servers:
                    raise ValueError(f"duplicate server id: {namespaced_id}")
                servers[namespaced_id] = server
        except Exception as error:
            logging.getLogger(__name__).warning(
                "Ignoring invalid MCP configuration %s: %s", config_path, error
            )


class AppConfig(BaseModel):
    llm: Dict[str, LLMSettings]
    browser_config: Optional[BrowserSettings] = Field(
        None, description="Browser configuration"
    )
    search_config: Optional[SearchSettings] = Field(
        None, description="Search configuration"
    )
    standard_data: Optional[StandardDataSettings] = Field(
        None, description="Business standard-object catalogue configuration"
    )
    mcp_config: Optional[MCPSettings] = Field(None, description="MCP configuration")
    run_flow_config: Optional[RunflowSettings] = Field(
        None, description="Run flow configuration"
    )

    class Config:
        arbitrary_types_allowed = True


class Config:
    _instance = None
    _lock = threading.Lock()
    _initialized = False

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if not self._initialized:
            with self._lock:
                if not self._initialized:
                    self._config = None
                    self._load_initial_config()
                    self._initialized = True

    @staticmethod
    def _get_config_path() -> Path:
        explicit_path = os.environ.get("BEESYNC_CONFIG_FILE")
        if explicit_path:
            path = Path(explicit_path)
            if not path.exists():
                raise FileNotFoundError(f"Configured Runtime config does not exist: {path}")
            return path
        bundled = runtime_paths.resource_root / "config" / "config.toml"
        machine = runtime_paths.config_dir / "config.toml"
        # A frozen release must have a stable published default.  A stale
        # ProgramData config from an older Runtime may not silently replace the
        # endpoint; the host can opt into a file with BEESYNC_CONFIG_FILE.
        candidates = (
            [bundled, runtime_paths.resource_root / "config" / "config.example.toml"]
            if getattr(sys, "frozen", False)
            else [machine, bundled, runtime_paths.resource_root / "config" / "config.example.toml"]
        )
        for config_path in candidates:
            if config_path.exists():
                return config_path
        raise FileNotFoundError("No configuration file found in config directory")

    def _load_config(self) -> dict:
        config_path = self._get_config_path()
        with config_path.open("rb") as f:
            return tomllib.load(f)

    def _load_initial_config(self):
        raw_config = self._load_config()
        base_llm = raw_config.get("llm", {})
        llm_overrides = {
            k: v for k, v in raw_config.get("llm", {}).items() if isinstance(v, dict)
        }

        default_settings = {
            "model": base_llm.get("model"),
            "base_url": base_llm.get("base_url") or DEFAULT_LLM_BASE_URL,
            "api_key": base_llm.get("api_key"),
            "max_tokens": base_llm.get("max_tokens", 4096),
            "max_input_tokens": base_llm.get("max_input_tokens"),
            "temperature": base_llm.get("temperature", 1.0),
            "api_type": base_llm.get("api_type", ""),
            "api_version": base_llm.get("api_version", ""),
            "enable_thinking": base_llm.get("enable_thinking"),
        }
        environment_llm_overrides = {
            "model": os.environ.get("BEESYNC_LLM_MODEL"),
            "base_url": os.environ.get("BEESYNC_LLM_BASE_URL"),
            "api_key": os.environ.get("BEESYNC_LLM_API_KEY"),
        }
        default_settings.update(
            {key: value for key, value in environment_llm_overrides.items() if value}
        )

        # handle browser config.
        browser_config = raw_config.get("browser", {})
        browser_settings = None

        if browser_config:
            # handle proxy settings.
            proxy_config = browser_config.get("proxy", {})
            proxy_settings = None

            if proxy_config and proxy_config.get("server"):
                proxy_settings = ProxySettings(
                    **{
                        k: v
                        for k, v in proxy_config.items()
                        if k in ["server", "username", "password"] and v
                    }
                )

            # filter valid browser config parameters.
            valid_browser_params = {
                k: v
                for k, v in browser_config.items()
                if k in BrowserSettings.__annotations__ and v is not None
            }

            # if there is proxy settings, add it to the parameters.
            if proxy_settings:
                valid_browser_params["proxy"] = proxy_settings

            # only create BrowserSettings when there are valid parameters.
            if valid_browser_params:
                browser_settings = BrowserSettings(**valid_browser_params)

        search_config = raw_config.get("search", {})
        search_settings = None
        if search_config:
            search_settings = SearchSettings(**search_config)
        standard_data_config = raw_config.get("standard_data", {})
        standard_data_settings = (
            StandardDataSettings(**standard_data_config)
            if standard_data_config
            else None
        )
        mcp_config = raw_config.get("mcp", {})
        mcp_settings = None
        if mcp_config:
            # Load server configurations from JSON
            mcp_config["servers"] = MCPSettings.load_server_config()
            mcp_settings = MCPSettings(**mcp_config)
        else:
            mcp_settings = MCPSettings(servers=MCPSettings.load_server_config())

        run_flow_config = raw_config.get("runflow")
        if run_flow_config:
            run_flow_settings = RunflowSettings(**run_flow_config)
        else:
            run_flow_settings = RunflowSettings()
        config_dict = {
            "llm": {
                "default": default_settings,
                **{
                    name: {
                        **default_settings,
                        **{
                            key: value
                            for key, value in override_config.items()
                            if value not in (None, "")
                        },
                    }
                    for name, override_config in llm_overrides.items()
                },
            },
            "browser_config": browser_settings,
            "search_config": search_settings,
            "standard_data": standard_data_settings,
            "mcp_config": mcp_settings,
            "run_flow_config": run_flow_settings,
        }

        self._config = AppConfig(**config_dict)
        resolved_llm = self._config.llm["default"]
        key_configured = llm_api_key_is_configured(resolved_llm.api_key)
        logging.getLogger(__name__).info(
            "LLM configuration loaded: base_url=%s model=%s "
            "api_key_configured=%s api_key_length=%d",
            resolved_llm.base_url,
            resolved_llm.model,
            key_configured,
            len(resolved_llm.api_key) if key_configured else 0,
        )

    @property
    def llm(self) -> Dict[str, LLMSettings]:
        return self._config.llm

    @property
    @property
    def browser_config(self) -> Optional[BrowserSettings]:
        return self._config.browser_config

    @property
    def search_config(self) -> Optional[SearchSettings]:
        return self._config.search_config

    @property
    def standard_data(self) -> Optional[StandardDataSettings]:
        return self._config.standard_data

    @property
    def mcp_config(self) -> MCPSettings:
        """Get the MCP configuration"""
        return self._config.mcp_config

    @property
    def run_flow_config(self) -> RunflowSettings:
        """Get the Run Flow configuration"""
        return self._config.run_flow_config

    @property
    def workspace_root(self) -> Path:
        """Get the workspace root directory"""
        return WORKSPACE_ROOT

    @property
    def log_root(self) -> Path:
        """Writable per-user log directory."""
        return runtime_paths.log_dir

    @property
    def vendor_skills_root(self) -> Path:
        return runtime_paths.vendor_skills_dir

    @property
    def user_skills_root(self) -> Path:
        return runtime_paths.user_skills_dir

    @property
    def root_path(self) -> Path:
        """Get the root path of the application"""
        return PROJECT_ROOT


config = Config()
