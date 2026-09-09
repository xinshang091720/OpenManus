"""Tool exports with lazy imports for optional desktop capabilities."""

from importlib import import_module
from typing import Any

_EXPORTS = {
    "BaseTool": ("app.tool.base", "BaseTool"),
    "ActivateSkill": ("app.tool.activate_skill", "ActivateSkill"),
    "DeactivateSkill": ("app.tool.deactivate_skill", "DeactivateSkill"),
    "Bash": ("app.tool.bash", "Bash"),
    "BrowserUseTool": ("app.tool.browser_use_tool", "BrowserUseTool"),
    "Crawl4aiTool": ("app.tool.crawl4ai", "Crawl4aiTool"),
    "CreateChatCompletion": ("app.tool.create_chat_completion", "CreateChatCompletion"),
    "ExecuteSkill": ("app.tool.skill", "ExecuteSkill"),
    "SkillScriptTool": ("app.tool.skill_script", "SkillScriptTool"),
    "PlanningTool": ("app.tool.planning", "PlanningTool"),
    "RevitApplyAssignment": ("app.tool.revit", "RevitApplyAssignment"),
    "RevitAssignIfcIdentifiers": ("app.tool.revit", "RevitAssignIfcIdentifiers"),
    "RevitClearParameters": ("app.tool.revit", "RevitClearParameters"),
    "RevitGetIfcIdentifiers": ("app.tool.revit", "RevitGetIfcIdentifiers"),
    "RevitGetLevelIfcIdentifiers": ("app.tool.revit", "RevitGetLevelIfcIdentifiers"),
    "RevitOpenFile": ("app.tool.revit", "RevitOpenFile"),
    "RevitPrepareAssignment": ("app.tool.revit", "RevitPrepareAssignment"),
    "RevitSaveAs": ("app.tool.revit", "RevitSaveAs"),
    "StrReplaceEditor": ("app.tool.str_replace_editor", "StrReplaceEditor"),
    "Terminate": ("app.tool.terminate", "Terminate"),
    "ToolCollection": ("app.tool.tool_collection", "ToolCollection"),
    "WebSearch": ("app.tool.web_search", "WebSearch"),
    "WindowsOpenApplication": ("app.tool.windows_app", "WindowsOpenApplication"),
    "RevitOpenProjectModel": ("app.tool.desktop_bim", "RevitOpenProjectModel"),
    "EnsureAutocadRunning": ("app.tool.desktop_bim", "EnsureAutocadRunning"),
    "SzIfcOpenModel": ("app.tool.desktop_bim", "SzIfcOpenModel"),
    "CadRunCode": ("app.tool.cad_execute", "CadRunCode"),
}

__all__ = list(_EXPORTS)


def __getattr__(name: str) -> Any:
    try:
        module_name, attribute = _EXPORTS[name]
    except KeyError as error:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from error
    return getattr(import_module(module_name), attribute)
