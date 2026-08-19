# Windows 桌面端 Agent Runtime API（第一期）

## 1. 定位与边界

Runtime 是本机运行的 Python Agent 服务。C# 桌面端负责界面、业务数据、聊天记录和审计落库；Runtime 负责在本机调用 Manus、Skill、MCP 与 Revit。

- 地址：`http://127.0.0.1:18765`（可使用 `--port` 覆盖）
- Swagger UI：`http://127.0.0.1:18765/docs`
- OpenAPI JSON：`http://127.0.0.1:18765/openapi.json`
- 不保存聊天记录、附件或业务数据库数据。
- Revit MCP 由 Runtime 用 stdio 启动；不会监听 8000。现有 CLI 的 MCP 配置不变。

在 Swagger 的右上角点击 **Authorize** 后，只粘贴 Token 原文；Swagger 会自动添加 `Bearer ` 前缀。

## 2. 启动与鉴权

C# 启动 Runtime 前生成高强度随机 Token，并同时：

1. 写入子进程环境变量 `OPENMANUS_RUNTIME_TOKEN`；
2. 为每个 HTTP/SSE 请求添加 `Authorization: Bearer <同一个Token>`。

本地手工测试示例（两个终端使用完全相同的测试 Token）：

```powershell
$env:OPENMANUS_RUNTIME_TOKEN = "dev-test-token-20260722"
python run_agent_runtime.py --port 18765
```

```powershell
$headers = @{ Authorization = "Bearer dev-test-token-20260722" }
Invoke-RestMethod http://127.0.0.1:18765/api/v1/health -Headers $headers
```

端口已被占用时，进程输出 `port_in_use: 127.0.0.1:18765: ...` 并以退出码 `3` 退出。Token 缺失时退出码为 `2`。

## 3. 接口契约

所有接口均需要 Bearer Token。未提供或不正确时返回：

```json
{"detail":"unauthorized"}
```

### 3.1 健康检查

`GET /api/v1/health`

这是 Runtime 真实返回的字段结构：

```json
{
  "status": "ok",
  "runtime_version": "0.1.0",
  "revit_mcp_mode": "stdio",
  "revit_mcp_status": "on_demand"
}
```

`revit_mcp_status` 在 Run 正在建立/使用 Revit MCP 时为 `running`，其他时间为 `on_demand`。

### 3.2 创建 Run

`POST /api/v1/runs`

请求体：

```json
{
  "request_id": "req-20260722-0001",
  "conversation_id": "conv-project-42",
  "user_message": "请总结上面的模型处理要求。",
  "history": [
    {"role": "user", "content": "我们需要处理 Revit IFC 标识。"},
    {"role": "assistant", "content": "我会按 Skill 和 MCP 工具执行。"}
  ],
  "attachments": []
}
```

真实响应结构：

```json
{
  "run_id": "run_02dc3f6bd5334d5b9fdab5beab81ea01",
  "status": "queued"
}
```

说明：

- `history` 只允许 `user`、`assistant`；不能传旧工具调用、工具结果、系统提示或 IFC 候选数据。
- `history` 不包含本次 `user_message`，避免上下文重复。
- `request_id` 是可选兼容字段，不参与上下文、排队、查询或去重。若 C# 发送它，应使用完整 GUID；任务查询和 SSE 始终使用服务端返回的 `run_id`。
- 相同 `conversation_id` 的 Run 在 Runtime 内存队列中严格串行；不同会话可同时运行。
- `conversation_id` 不保存上下文。C# 应直接传业务会话的 `sessionGroupId`：新建会话时生成新值，恢复历史会话时恢复原值，同一聊天后续消息保持不变。
- `attachments` 不上传文件，仅引用当前 Windows 主机的真实绝对路径。路径不存在或为相对路径会返回 `422`。
- Revit 示例：`{"path":"C:\\Models\\B1-AR.rvt","type":"revit_model"}`。此路径仅为字段格式示例，调用时必须替换成用户电脑上已存在的实际文件。

### 3.3 SSE 事件流

`GET /api/v1/runs/{run_id}/events`

响应类型为 `text/event-stream`。C# 应持续读取每一条 `event:` / `data:`，并把最终回复、进度和 artifact 路径写入业务数据库。

固定事件类型及真实字段：

| event | data 字段 | 含义 |
| --- | --- | --- |
| `run_queued` | `run_id`, `status` | 已进入队列 |
| `run_started` | `run_id`, `summary` | 开始执行，并说明当前在分析任务 |
| `assistant_message` | `phase: "reasoning"`, `content` | 模型选择工具前生成的简短执行说明（最多 1,500 字符）；不是完整内部思维链 |
| `assistant_message` | `phase: "plan"`, `content` | 模型未提供执行说明时的系统生成计划 |
| `tool_started` | `tool`, `summary` | 工具开始；`summary` 是中文业务步骤说明 |
| `tool_progress` | `tool`, `summary`, `elapsed_seconds` | Revit 工具每 30 秒报告运行状态；只更新现有工具步骤，不创建 Assistant 气泡、不写入聊天历史 |
| `tool_completed` | `tool`, `success`, `summary` | 工具结束；不发送完整 IFC 候选或密钥 |
| `assistant_message` | `phase: "final"`, `content` | 最终助手文本 |
| `run_completed` | `run_id`, `final_answer`, `artifacts` | 成功终态 |
| `run_failed` | `run_id`, `error` | 异常终态 |
| `run_cancelled` | `run_id` | 取消终态 |

完成事件的实际线路格式：

```text
event: run_completed
data: {"run_id":"run_02dc3f6bd5334d5b9fdab5beab81ea01","final_answer":"任务已完成。","artifacts":[]}

```

Revit IFC 工作流生成审计报告时，`artifacts` 中会出现：

```json
[
  {"type":"ifc_audit","path":"C:\\...\\ifc-assignment-audit.json"}
]
```

### 3.4 取消 Run

`POST /api/v1/runs/{run_id}/cancel`

排队中的 Run 立即返回并推送 `run_cancelled`。执行中的 Run 返回：

```json
{
  "run_id": "run_02dc3f6bd5334d5b9fdab5beab81ea01",
  "status": "cancelling"
}
```

Runtime 收到取消请求后立即进入 `cancelling`。取消在通用框架节点生效：排队前、工具启动前、当前工具返回后和下一 Agent 步骤前。已经发往 Revit 原生插件的单次 HTTP 调用无法撤回。`sz_ifc_open_model` 只做人工加载状态确认，可立即取消。`revit_inspect_ifc` 每秒检查一次取消状态；当前已经发出的单次点击不会撤回，但保存窗口、成功提示或文件稳定等待会停止后续动作，后台线程退出并释放 SZ-IFC 锁。两者都不会关闭用户的 SZ-IFC 进程。

### Skill-scoped capabilities

The Runtime starts without Revit MCP tools in the Agent context.  The model
receives the Skill Catalog, calls `activate_skill("revit-ifc-assignment")` for
a Revit task, and only then starts the internal stdio Revit MCP child.  The
following additive SSE events may be rendered by new desktop clients and safely
ignored by older clients: `skill_activated`, `skill_activation_failed`,
`workflow_stage_started`, `workflow_stage_completed`, and
`workflow_stage_failed`. Revit calls also emit `tool_progress` every 30 seconds.

Revit 工具调用最长等待两小时。达到上限后 Runtime 返回“最终 Revit 状态未知”，且不会自动重试写入操作。同一 Run 中任一 Revit 写工具失败或状态未知后，Runtime 会结束该 Run 并阻止同一模型响应中的后续 Revit 写调用。桌面端的 SSE HTTP 请求不得设置短于该时限的总超时；推荐使用调用方 CancellationToken 管理无限 SSE 读取。

## 4. C# 最小调用顺序

```text
生成 Token
  -> 以 Token 环境变量启动 Runtime
  -> GET /health 成功
  -> 从业务库筛选 user/assistant history
  -> POST /runs
  -> GET /runs/{run_id}/events（SSE）
  -> 保存 final_answer、状态、事件与 artifact 路径
```

创建 Run 前，C# 可记录有限诊断字段：`conversation_id`、历史数量、角色顺序、最后一条 Assistant 的短预览和当前用户消息的短预览。不要记录完整长上下文、工具结果或敏感内容。

首次不需要实现 MCP/Skill 的用户管理界面。C# 不应尝试直连 Runtime 的内部 Revit stdio MCP。
