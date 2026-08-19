"""Collect the MCP runtime without its optional command-line application."""

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules



def _is_runtime_module(module_name: str) -> bool:
    return module_name != "mcp.cli" and not module_name.startswith("mcp.cli.")


datas = collect_data_files("mcp")
binaries = collect_dynamic_libs("mcp")
hiddenimports = collect_submodules("mcp", filter=_is_runtime_module)
