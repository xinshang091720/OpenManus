import asyncio
import json
from types import SimpleNamespace

from app.api.runtime import RuntimeManager
from app.config import (
    Config,
    DEFAULT_LLM_BASE_URL,
    MCPSettings,
    llm_api_key_is_configured,
)
from app.revit.client import RevitApiClient, configured_revit_api_base_url
from app.runtime_paths import RuntimePaths
from app.skills.packages import SkillPackageRegistry


def test_runtime_child_uses_the_same_entrypoint_in_source_mode():
    command, args = RuntimeManager._internal_mcp_command()
    assert command
    assert args[-1] == "--mcp-stdio"
    assert any("run_agent_runtime.py" in item for item in args)


def test_runtime_child_inherits_llm_endpoint_model_and_key(monkeypatch):
    monkeypatch.setenv("BEESYNC_LLM_BASE_URL", "https://example.invalid/v1")
    monkeypatch.setenv("BEESYNC_LLM_MODEL", "third-party-model")
    monkeypatch.setenv("BEESYNC_LLM_API_KEY", "secret-value")
    child = RuntimeManager._revit_stdio_config()["revit_local"].env
    assert child["BEESYNC_LLM_BASE_URL"] == "https://example.invalid/v1"
    assert child["BEESYNC_LLM_MODEL"] == "third-party-model"
    assert child["BEESYNC_LLM_API_KEY"] == "secret-value"


def test_revit_child_preserves_only_explicit_desktop_and_licensing_environment(monkeypatch):
    monkeypatch.setenv("PROGRAMDATA", r"C:\ProgramData")
    monkeypatch.setenv("COMMONPROGRAMFILES", r"C:\Program Files\Common Files")
    monkeypatch.setenv("COMSPEC", r"C:\Windows\System32\cmd.exe")
    monkeypatch.setenv("SESSIONNAME", "Console")
    monkeypatch.setenv("ADSK_LICENSE_FILE", "27000@licenses.example.invalid")
    monkeypatch.setenv("OPENMANUS_RUNTIME_TOKEN", "must-not-reach-mcp")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "must-not-reach-mcp")

    child = RuntimeManager._revit_stdio_config()["revit_local"].env

    assert child["PROGRAMDATA"] == r"C:\ProgramData"
    assert child["COMMONPROGRAMFILES"] == r"C:\Program Files\Common Files"
    assert child["COMSPEC"] == r"C:\Windows\System32\cmd.exe"
    assert child["SESSIONNAME"] == "Console"
    assert child["ADSK_LICENSE_FILE"] == "27000@licenses.example.invalid"
    assert "OPENMANUS_RUNTIME_TOKEN" not in child
    assert "ANTHROPIC_API_KEY" not in child


def test_llm_placeholder_is_not_a_credential_and_default_url_is_stable():
    assert not llm_api_key_is_configured("BEESYNC_HOST_MUST_INJECT_API_KEY")
    assert llm_api_key_is_configured("real-key")
    assert DEFAULT_LLM_BASE_URL.endswith("/compatible-mode/v1")


def test_frozen_runtime_prefers_bundled_default_over_stale_programdata(monkeypatch, tmp_path):
    resource = tmp_path / "release" / "_internal"
    machine = tmp_path / "ProgramData" / "config"
    (resource / "config").mkdir(parents=True)
    machine.mkdir(parents=True)
    bundled = resource / "config" / "config.toml"
    bundled.write_text("[llm]\nbase_url='published'", encoding="utf-8")
    (machine / "config.toml").write_text("[llm]\nbase_url='stale'", encoding="utf-8")
    monkeypatch.delenv("BEESYNC_CONFIG_FILE", raising=False)
    monkeypatch.setattr("app.config.sys.frozen", True, raising=False)
    monkeypatch.setattr(
        "app.config.runtime_paths",
        SimpleNamespace(resource_root=resource, config_dir=machine),
    )
    assert Config._get_config_path() == bundled


def test_runtime_paths_accept_csharp_host_overrides(monkeypatch, tmp_path):
    config_dir = tmp_path / "machine-config"
    data_dir = tmp_path / "user-data"
    monkeypatch.setenv("BEESYNC_CONFIG_DIR", str(config_dir))
    monkeypatch.setenv("BEESYNC_DATA_DIR", str(data_dir))

    paths = RuntimePaths.discover()
    paths.ensure_user_directories()

    assert paths.config_dir == config_dir
    assert paths.workspace_dir.is_dir()
    assert paths.log_dir.is_dir()


def test_revit_endpoint_environment_has_priority(monkeypatch):
    monkeypatch.setenv("BEESYNC_REVIT_API_BASE_URL", "http://127.0.0.1:40123/api/RevitApi/")
    assert configured_revit_api_base_url() == "http://127.0.0.1:40123/api/RevitApi"


def test_revit_legacy_endpoint_preserves_localhost_and_route_slashes(monkeypatch):
    monkeypatch.delenv("BEESYNC_REVIT_API_BASE_URL", raising=False)
    assert configured_revit_api_base_url() == "http://localhost:5000//api/RevitApi"


def test_revit_health_status_is_structured_without_a_live_plugin():
    class HealthClient(RevitApiClient):
        async def _request(self, method, path, payload=None, **kwargs):
            assert (method, path) == ("GET", "/Health")
            return {
                "code": 200,
                "pluginVersion": "1.2.3",
                "revitConnected": True,
                "readyForRequests": True,
            }

    status = asyncio.run(HealthClient("http://127.0.0.1:39521/api/RevitApi").plugin_status())
    assert status["status"] == "ready"
    assert status["plugin_version"] == "1.2.3"


def test_user_skill_is_namespaced_and_cannot_replace_vendor_skill(tmp_path):
    vendor_root = tmp_path / "vendor"
    user_root = tmp_path / "user"
    for root in (vendor_root, user_root):
        package = root / "same-name"
        package.mkdir(parents=True)
        (package / "SKILL.md").write_text(
            "---\nname: same-name\ndescription: test skill\n---\nInstructions",
            encoding="utf-8",
        )

    (user_root / "enabled.json").write_text('{"enabled": ["same-name"]}', encoding="utf-8")

    packages = SkillPackageRegistry(vendor_root, user_root).discover()
    assert {package.name for package in packages} == {"same-name", "user.same-name"}


def test_user_skill_is_disabled_until_enabled_registry_lists_it(tmp_path):
    user_root = tmp_path / "user"
    package = user_root / "sample"
    package.mkdir(parents=True)
    (package / "SKILL.md").write_text(
        "---\nname: sample\ndescription: sample\n---\nInstructions", encoding="utf-8"
    )

    assert SkillPackageRegistry(tmp_path / "vendor", user_root).discover() == []


def test_user_mcp_config_is_namespaced_and_disabled_entries_are_skipped(tmp_path):
    config_file = tmp_path / "user-tools.json"
    config_file.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "enabled": {"type": "sse", "url": "http://127.0.0.1:39001/sse"},
                    "disabled": {"type": "stdio", "command": "tool.exe", "enabled": False},
                }
            }
        ),
        encoding="utf-8",
    )
    servers = {}
    MCPSettings._load_file(config_file, servers, prefix="user_user-tools_")
    assert list(servers) == ["user_user-tools_enabled"]
