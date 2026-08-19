"""Launch the local Agent Runtime consumed by the Windows business desktop app."""

import argparse
import importlib
import json
import os
import socket
import sys
from pathlib import Path


def bind_runtime_socket(host: str, port: int) -> socket.socket:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 0)
        sock.bind((host, port))
        sock.listen(socket.SOMAXCONN)
        return sock
    except OSError as error:
        sock.close()
        raise RuntimeError(f"port_in_use: {host}:{port}: {error}") from error


def _set_path_environment(variable: str, value: str | None) -> None:
    if value:
        os.environ[variable] = str(Path(value).expanduser().resolve())


def _configure_runtime_environment(args: argparse.Namespace) -> None:
    _set_path_environment("BEESYNC_CONFIG_DIR", args.config_dir)
    _set_path_environment("BEESYNC_DATA_DIR", args.data_dir)
    _set_path_environment("BEESYNC_SKILLS_DIR", args.skills_dir)
    _set_path_environment("BEESYNC_USER_SKILLS_DIR", args.user_skills_dir)
    _set_path_environment("BEESYNC_CONFIG_FILE", args.config_file)
    if args.revit_api_base_url:
        os.environ["BEESYNC_REVIT_API_BASE_URL"] = args.revit_api_base_url.rstrip("/")
    if args.runtime_version:
        os.environ["BEESYNC_RUNTIME_VERSION"] = args.runtime_version
    # Browser-enabled PyInstaller builds place Chromium beside Playwright.
    # Force Playwright to resolve that bundled location rather than a
    # developer-specific AppData cache.
    if getattr(sys, "frozen", False):
        os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", "0")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the local BeeSync Agent Runtime API")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=18765)
    parser.add_argument("--config-dir", help="Machine-level non-secret configuration directory.")
    parser.add_argument("--config-file", help="Explicit non-secret Runtime TOML configuration.")
    parser.add_argument("--data-dir", help="Current Windows user's writable Runtime data directory.")
    parser.add_argument("--skills-dir", help="Vendor Skill directory bundled with this Runtime version.")
    parser.add_argument("--user-skills-dir", help="Current user's custom Skill directory.")
    parser.add_argument("--revit-api-base-url", help="Configured loopback base URL for the Revit plugin API.")
    parser.add_argument("--runtime-version", help="Release version reported by /api/v1/health.")
    parser.add_argument(
        "--mcp-stdio",
        action="store_true",
        help="Internal packaged Runtime mode: serve only the Revit MCP through stdio.",
    )
    parser.add_argument(
        "--doctor",
        action="store_true",
        help="Run a local packaging/configuration self-check without starting the HTTP API.",
    )
    parser.add_argument(
        "--startup-diagnose",
        action="store_true",
        help="Print packaged Runtime startup stages for release diagnostics.",
    )
    parser.add_argument(
        "--room-extractor-diagnose",
        action="store_true",
        help="Internal release check: import the frozen DWG room-extraction dependency chain.",
    )
    parser.add_argument(
        "--dependency-diagnose",
        action="store_true",
        help="Internal release check: import protected-Runtime third-party dependencies.",
    )
    parser.add_argument(
        "--include-browser-dependencies",
        action="store_true",
        help="Internal release check: include optional browser-tool dependencies.",
    )
    return parser


def main() -> None:
    if sys.stdout and hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding='utf-8')
    if sys.stderr and hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding='utf-8')

    parser = build_parser()
    args = parser.parse_args()
    _configure_runtime_environment(args)

    def startup_stage(name: str) -> None:
        if args.startup_diagnose:
            print(f"startup-stage: {name}", flush=True)

    if args.doctor:
        # A path-only check can report success even when the packaged TOML is
        # missing. Import the actual Runtime config before reporting healthy.
        from app.config import config, llm_api_key_is_configured
        from app.revit.client import configured_revit_api_base_url
        from app.runtime_paths import runtime_paths

        runtime_paths.ensure_user_directories()
        llm_settings = config.llm["default"]
        key_configured = llm_api_key_is_configured(llm_settings.api_key)
        print(
            json.dumps(
                {
                    "status": "ok" if key_configured else "error",
                    "python": sys.version.split()[0],
                    "install_dir": str(runtime_paths.install_root),
                    "resource_dir": str(runtime_paths.resource_root),
                    "config_dir": str(runtime_paths.config_dir),
                    "data_dir": str(runtime_paths.data_dir),
                    "vendor_skills_dir": str(runtime_paths.vendor_skills_dir),
                    "user_skills_dir": str(runtime_paths.user_skills_dir),
                    "revit_api_base_url": configured_revit_api_base_url(),
                    "llm_base_url": llm_settings.base_url,
                    "llm_model": llm_settings.model,
                    "llm_api_key_configured": key_configured,
                    "llm_api_key_length": len(llm_settings.api_key) if key_configured else 0,
                    "config_loaded": True,
                },
                ensure_ascii=False,
            )
        )
        if not key_configured:
            raise SystemExit(2)
        return

    if args.dependency_diagnose:
        # app.pyd and ohresult.pyd intentionally hide their Python import
        # statements from PyInstaller. This probe makes a missing explicit
        # collection fail during the build instead of at a customer site.
        module_names = (
            "baidusearch",
            "boto3",
            "bs4",
            "dotenv",
            "duckduckgo_search",
            "ezdxf",
            "fastapi",
            "googlesearch",
            "httpx",
            "loguru",
            "mcp",
            "numpy",
            "olefile",
            "openai",
            "pandas",
            "psutil",
            "pyautogui",
            "pydantic",
            "pydantic_core",
            "pymysql",
            "pypdf",
            "pdfplumber",
            "pywinauto",
            "reportlab",
            "requests",
            "structlog",
            "tenacity",
            "tiktoken",
            "uvicorn",
            "yaml",
        )
        if args.include_browser_dependencies:
            module_names += (
                "browser_use",
                "crawl4ai",
                "markdownify",
                "playwright",
            )

        for module_name in module_names:
            importlib.import_module(module_name)

        first_party_modules = (
            "app.api.runtime",
            "app.llm",
            "app.revit.project_delivery",
            "app.tool.mcp",
            "app.tool.windows_app",
        )
        if args.include_browser_dependencies:
            first_party_modules += (
                "app.tool.browser_use_tool",
                "app.tool.crawl4ai",
            )
        for module_name in first_party_modules:
            importlib.import_module(module_name)

        print(
            json.dumps(
                {
                    "status": "ok",
                    "dependencies_checked": len(module_names),
                    "protected_modules_checked": True,
                },
                ensure_ascii=False,
            )
        )
        return

    if args.room_extractor_diagnose:
        # Keep this before MCP startup.  The actual room tool imports this
        # chain only after obtaining the Revit grid data, so a frozen-package
        # conflict used to appear late in a real user run.  This check has no
        # Revit, AutoCAD, file-conversion, or write side effects.
        import ezdxf
        import numpy
        import pythoncom  # noqa: F401
        import win32com.client  # noqa: F401
        from q_agent_function_module.ohresult.CAD_Git_Coordinates.my_code.room_coordinates import (  # noqa: F401
            dwg_room_extractor,
            dwg_room_extractor_main,
        )

        print(
            json.dumps(
                {
                    "status": "ok",
                    "numpy": numpy.__version__,
                    "numpy_path": str(Path(numpy.__file__).resolve()),
                    "ezdxf_path": str(Path(ezdxf.__file__).resolve()),
                    "room_extractor_loaded": True,
                },
                ensure_ascii=False,
            )
        )
        return

    if args.mcp_stdio:
        # Import only after path configuration so packaged Skills/configuration
        # resolve exactly like the parent Runtime process.
        from app.mcp.server import MCPServer

        MCPServer().run(transport="stdio")
        return

    token = os.environ.get("OPENMANUS_RUNTIME_TOKEN", "")
    startup_stage("credentials_checked")
    if not token:
        print("OPENMANUS_RUNTIME_TOKEN is required", file=sys.stderr)
        raise SystemExit(2)
    from app.config import config, llm_api_key_is_configured

    if not llm_api_key_is_configured(config.llm["default"].api_key):
        print(
            "A real LLM API key is required through BEESYNC_LLM_API_KEY or llm.api_key; "
            "the packaged placeholder is not a credential",
            file=sys.stderr,
        )
        raise SystemExit(2)
    try:
        sock = bind_runtime_socket(args.host, args.port)
    except RuntimeError as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(3) from error
    startup_stage("port_bound")
    import uvicorn
    startup_stage("uvicorn_imported")

    if args.startup_diagnose:
        # app.api.runtime imports Manus first; isolate that dependency so a
        # packaged native crash can be attributed without guessing.
        import app.agent.toolcall  # noqa: F401
        startup_stage("toolcall_imported")
        import app.agent.browser  # noqa: F401
        startup_stage("browser_context_imported")
        import app.tool.mcp  # noqa: F401
        startup_stage("mcp_tool_imported")
        import app.tool.windows_app  # noqa: F401
        startup_stage("windows_tool_imported")
        import app.skills  # noqa: F401
        startup_stage("skills_imported")
        from app.agent.manus import Manus  # noqa: F401

        startup_stage("manus_imported")

    from app.api.runtime import create_runtime_app
    startup_stage("runtime_api_imported")

    app = create_runtime_app(token)
    startup_stage("app_created")
    server = uvicorn.Server(uvicorn.Config(app, host=args.host, port=args.port, log_level="info"))
    startup_stage("server_created")
    server.run(sockets=[sock])


if __name__ == "__main__":
    # PyInstaller launches multiprocessing workers by re-executing this EXE
    # with internal --multiprocessing-fork arguments. Handle that protocol
    # before argparse processes normal Runtime arguments.
    import multiprocessing

    multiprocessing.freeze_support()
    main()
