"""Agent exports, loaded only when the requested agent is used."""

from importlib import import_module
from typing import Any

_EXPORTS = {
    "BaseAgent": ("app.agent.base", "BaseAgent"),
    "BrowserAgent": ("app.agent.browser", "BrowserAgent"),
    "MCPAgent": ("app.agent.mcp", "MCPAgent"),
    "ReActAgent": ("app.agent.react", "ReActAgent"),
    "SWEAgent": ("app.agent.swe", "SWEAgent"),
    "ToolCallAgent": ("app.agent.toolcall", "ToolCallAgent"),
}

__all__ = list(_EXPORTS)


def __getattr__(name: str) -> Any:
    try:
        module_name, attribute = _EXPORTS[name]
    except KeyError as error:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from error
    return getattr(import_module(module_name), attribute)
