# BeeSync Windows Runtime 对接说明

## 进程职责

C# 桌面端是唯一的 Runtime 宿主：生成 Token、从 Windows Credential Manager / DPAPI 读取模型密钥、启动和停止 `BeeSync.AgentRuntime.exe`、保存聊天记录并订阅 SSE。Python Runtime 不保存聊天历史，也不自行更新。

Runtime 内部通过 `--mcp-stdio` 启动 Revit MCP。桌面端不要启动 `run_mcp_server.py`，也不要部署 8000 端口。

## 启动命令

```text
BeeSync.AgentRuntime.exe --host 127.0.0.1 --port 18765 \
  --config-dir "%ProgramData%\\Anbi\\BeeSync\\config" \
  --data-dir "%LocalAppData%\\Anbi\\BeeSync" \
  --skills-dir "<runtime-version-dir>\\skills" \
  --user-skills-dir "%LocalAppData%\\Anbi\\BeeSync\\skills" \
  --revit-api-base-url "http://127.0.0.1:39521/api/RevitApi" \
  --runtime-version "<runtime-version>"
```

C# 通过子进程环境变量提供以下值，绝不写入命令行或文件：

```text
OPENMANUS_RUNTIME_TOKEN=<每次启动随机 Token>
BEESYNC_LLM_API_KEY=<从 Credential Manager / DPAPI 读取的密钥>
```

可选环境变量 `BEESYNC_LLM_MODEL` 与 `BEESYNC_LLM_BASE_URL` 可覆盖非敏感 TOML 配置。没有 `OPENMANUS_RUNTIME_TOKEN` 或 `BEESYNC_LLM_API_KEY` 时，Runtime 必须拒绝启动。

## Runtime 接口

既有 `/api/v1/health`、`/api/v1/runs`、`/api/v1/runs/{run_id}/events` 和取消接口保持不变。health 新增但不移除字段：

```json
{
  "status": "ok",
  "runtime_version": "1.0.0",
  "api_version": "1.0",
  "revit_mcp_mode": "stdio",
  "revit_mcp_status": "on_demand",
  "revit_plugin": {
    "status": "ready",
    "plugin_version": "1.0.0",
    "revit_connected": true,
    "ready_for_requests": true,
    "message": "Revit plugin is ready"
  }
}
```

桌面端在 health 成功、`api_version` 兼容且模型密钥已注入后才允许创建 Run。Runtime 崩溃时，桌面端将执行中的 Run 标记为 `runtime_lost`，不得自动重放可能修改 Revit 模型的任务。

## 会话上下文与任务标识

- Python Runtime 保持无状态。C# 每次创建 Run 时，把当前消息放在 `user_message`，把此前完整的 `user/assistant` 对话放在 `history`；当前消息不能再放入 `history`。
- 直接使用业务层 `sessionGroupId` 作为 `conversation_id`。新建聊天生成新值，切换历史聊天恢复其原值，同一聊天后续消息保持该值。Runtime 只用它串行化同会话 Run。
- `request_id` 是可选诊断兼容字段，不承担上下文或去重。需要发送时使用完整 GUID，不得截断。创建成功后使用返回的 `run_id` 查询任务和订阅 SSE。
- 创建 Run 前只记录 conversation ID、history 数量、角色顺序、最后一条 Assistant 和当前消息的有限预览；不要输出完整上下文或敏感内容。

`tool_progress` 事件包含 `tool`、`summary`、`elapsed_seconds`。桌面端应查找该工具当前未完成的步骤并更新其标题，例如“正在批量写入 IFC 标识，已运行 18 分钟”。该事件不创建聊天气泡，也不进入下一轮 `history`。

Revit 工具最长运行两小时，因此 SSE 客户端不能保留原先的十分钟总超时。使用无限 HTTP 超时配合当前 Run 的 CancellationToken；两小时上限由 Runtime 控制。

## Revit 插件契约

Revit 插件只监听 `127.0.0.1:<configured-port>`。机器级文件 `%ProgramData%\\Anbi\\BeeSync\\config\\revit-plugin.json`：

```json
{"apiBaseUrl":"http://127.0.0.1:39521/api/RevitApi"}
```

Runtime 启动参数/环境变量优先于该文件。C# 在启动 Revit 前检查已配置端口；冲突时提示用户修改配置并重启 Revit，不能静默换端口。

插件需新增：`GET /api/RevitApi/Health`

```json
{
  "code": 200,
  "msg": "Revit plugin is ready",
  "pluginVersion": "1.0.0",
  "revitConnected": true,
  "readyForRequests": true
}
```

在插件 health 发布前，Runtime 可兼容现有插件；发布后由 C# 设置 `BEESYNC_REVIT_REQUIRE_HEALTH=true` 强制预检。

## 扩展与升级

- 官方 Skill：`<runtime-version-dir>\\skills`，只随 Runtime 版本升级。
- 用户 Skill：`%LocalAppData%\\Anbi\\BeeSync\\skills`，以 `user.` 命名空间加载，不能覆盖官方同名 Skill。
- 用户 MCP：`%LocalAppData%\\Anbi\\BeeSync\\mcp-servers\\*.json`，格式见 `config/mcp-user.example.json`；每个配置必须在 C# UI 中展示命令、参数、来源和启用状态，并由用户确认后启用。
- C# 下载签名并校验 SHA-256 的 Runtime 包，安装到 `runtime\\<version>`，health 自检成功后切换版本；失败时回滚上一版本。
