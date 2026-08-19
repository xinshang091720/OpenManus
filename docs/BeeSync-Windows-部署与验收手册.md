# BeeSync Windows 桌面端部署与验收手册

适用对象：BeeSync 桌面端、Agent Runtime、Revit 插件的开发、测试和交付人员。

本文以“C# 桌面端主程序 + BeeSync Agent Runtime 侧车进程 + Revit 插件”的本机架构为准。Runtime 不保存聊天记录；C# 负责界面、业务数据、聊天记录、密钥和进程托管。

## 1. 架构与端口

```text
BeeSync.Desktop.exe (C#)
  └─ HTTP + SSE ──> BeeSync.AgentRuntime.exe (127.0.0.1:18765)
                           ├─ stdio ──> 内部 Revit MCP（不占用 8000 端口）
                           └─ HTTP ──> Revit 插件（localhost:5000）
```

| 服务 | 地址 | 说明 |
| --- | --- | --- |
| Agent Runtime | `127.0.0.1:18765` | BeeSync 对 C# 的本机 API；必须使用 Bearer Token。 |
| Revit 插件 | `http://localhost:5000//api/RevitApi` | 当前测试插件地址。`localhost` 和双斜杠 `//api` 都不能改成 `127.0.0.1` 或单斜杠。 |
| 内部 Revit MCP | stdio | Runtime 自动启动；不要手动运行 `run_mcp_server.py --transport sse`，也不需要 8000 端口。 |

Runtime 只能监听 `127.0.0.1`，不可改为 `0.0.0.0`。若 `18765` 被占用，应由 C# 选择安装配置中的备用端口并重启 Runtime，不能静默换端口。

## 2. 发布包与前置条件

发布包采用 PyInstaller `--onedir` 结构，例如：

```text
BeeSync.AgentRuntime\
├─ BeeSync.AgentRuntime.exe
└─ _internal\                 # Python 运行时、依赖、内置 Skills 等资源
```

目标电脑不需要安装 Python、Conda 或项目源码。运行 Revit 能力前需满足：

1. 安装并启动匹配版本的 Revit。
2. 已安装、加载 BeeSync/Revit 插件，且插件的本机 HTTP 服务可用。
3. 目标模型可由当前用户读取；执行需要专业信息的工具前，由用户确认 `AR`、`ST`、`AC`、`PD`、`EL` 或对应中文专业。文件名不作硬性要求。
4. Runtime 与 Revit 插件所用端口未被其他程序占用。

当前首期仅登记深圳报建标准，标准 ID 固定为 `109003`。Agent 应自动使用该标准，不能要求客户填写或确认 Standard ID。后续新增标准时，维护 Skill 中的 `references/standard-mapping.yaml`，并随新 Runtime 版本发布。

## 3. 构建发布包

在开发机项目根目录执行。每次正式发布使用新的版本号，不覆盖已交付版本目录。

```powershell
cd C:\Users\jly23\Desktop\OpenManus-main
conda activate open_manus

.\scripts\build_runtime.ps1 `
  -Version 1.1.0 `
  -Python D:\an\envs\open_manus\python.exe
```

浏览器自动化属于可选能力。只有在目标 Windows 上完成单独验收后，才使用 `-IncludeBrowser` 打包。当前已知部分浏览器依赖在 PyInstaller 环境可能触发原生模块崩溃；发布验证阶段应保持浏览器关闭。

## 4. 安装目录建议

```text
C:\Program Files\Anbi\BeeSync\
  ├─ BeeSync.Desktop.exe
  └─ runtime\<version>\BeeSync.AgentRuntime\

%ProgramData%\Anbi\BeeSync\config\
  └─ revit-plugin.json          # 非敏感的插件地址配置（可选）

%LocalAppData%\Anbi\BeeSync\
  ├─ skills\                    # 当前用户扩展 Skill
  ├─ mcp-servers\               # 当前用户 MCP 配置
  ├─ workspace\                 # 临时任务文件
  └─ logs\                      # Runtime 日志
```

官方内置 Skill 位于发布包 `_internal\skills`，升级时由 Runtime 包替换；用户自定义 Skill 位于 `%LocalAppData%`，升级不得覆盖。

## 5. 配置原则与密钥

Runtime 使用下列环境变量。生产环境由 C# 在启动子进程时注入，禁止写入命令行、代码、`config.toml`、日志或安装包。

| 变量 | 必填 | 作用 |
| --- | --- | --- |
| `OPENMANUS_RUNTIME_TOKEN` | 是 | Runtime API 的本机 Bearer Token。每次 C# 启动时生成随机值。 |
| `BEESYNC_LLM_API_KEY` | 是 | 调用模型服务的 API Key。不是 Runtime HTTP Token。 |
| `BEESYNC_REVIT_API_BASE_URL` | 当前测试必填 | Revit 插件 API 地址；当前值为 `http://localhost:5000//api/RevitApi`。 |
| `BEESYNC_ENABLE_BROWSER` | 建议 | 当前稳定验证设置为 `0`，避免浏览器可选依赖影响 Revit/聊天主链路。 |

`BEESYNC_HOST_MUST_INJECT_API_KEY` 仅是示例配置中的占位文字，不是环境变量，也不是第二把 Key。真实模型 Key 只通过 `BEESYNC_LLM_API_KEY` 注入。

如使用配置文件管理 Revit 地址，创建：

```text
%ProgramData%\Anbi\BeeSync\config\revit-plugin.json
```

内容如下：

```json
{
  "apiBaseUrl": "http://localhost:5000//api/RevitApi"
}
```

环境变量优先级高于该配置文件，便于测试与部署覆盖。

## 6. 手工验收：启动与健康检查

以下命令仅用于测试。将占位值替换为实际值，且不要将真实 Key 粘贴到聊天、文档或日志中。

### 6.1 进入发布包并检查

```powershell
cd "C:\Program Files\Anbi\BeeSync\runtime\1.1.0\BeeSync.AgentRuntime"
.\BeeSync.AgentRuntime.exe --doctor
```

`--doctor` 应输出 JSON，且 `status` 为 `ok`。若当前 Revit 插件使用 5000 端口，请先设置 `BEESYNC_REVIT_API_BASE_URL` 再运行 `--doctor`，确认输出的 `revit_api_base_url` 完全等于：

```text
http://localhost:5000//api/RevitApi
```

### 6.2 启动 Runtime

```powershell
$env:OPENMANUS_RUNTIME_TOKEN = "<随机本机Token>"
$env:BEESYNC_LLM_API_KEY = "<模型服务API-Key>"
$env:BEESYNC_REVIT_API_BASE_URL = "http://localhost:5000//api/RevitApi"
$env:BEESYNC_ENABLE_BROWSER = "0"

.\BeeSync.AgentRuntime.exe --host 127.0.0.1 --port 18765
```

保持该窗口运行。出现以下日志代表服务就绪：

```text
Application startup complete.
```

### 6.3 健康检查

在第二个 PowerShell 窗口中执行：

```powershell
$headers = @{ Authorization = "Bearer <随机本机Token>" }

Invoke-RestMethod `
  "http://127.0.0.1:18765/api/v1/health" `
  -Headers $headers
```

预期 `status` 为 `ok`。`revit_plugin.status` 若为 `unavailable`，先检查 Revit 是否启动、插件是否加载、5000 端口是否可访问。

## 7. 手工验收：创建任务与订阅 SSE

### 7.1 创建 Run

PowerShell 对中文 JSON 可能使用错误编码，因此始终将 JSON 转为 UTF-8 字节后发送：

```powershell
$headers = @{
  Authorization = "Bearer <随机本机Token>"
  "Content-Type" = "application/json"
}

$body = @{
  request_id = "manual-test-001"
  conversation_id = "manual-conversation-001"
  user_message = "清空已有 IFC 参数。处理 C:\Models\项目_B1-AR.rvt，进行核验，并保存到 C:\Output。"
  history = @()
  attachments = @()
} | ConvertTo-Json -Depth 5

$utf8Body = [System.Text.Encoding]::UTF8.GetBytes($body)

$run = Invoke-RestMethod `
  "http://127.0.0.1:18765/api/v1/runs" `
  -Method Post `
  -Headers $headers `
  -ContentType "application/json; charset=utf-8" `
  -Body $utf8Body

$run
```

预期响应：

```json
{
  "run_id": "run_xxx",
  "status": "queued"
}
```

`attachments` 是可选字段。对于模型路径已直接写在 `user_message` 中的测试，可传空数组；C# 若以附件形式提供本机模型路径，可传入：

```json
[
  { "path": "C:\\Models\\项目_B1-AR.rvt", "type": "revit_model" }
]
```

它不上传文件，`type` 仅用于让 Runtime 知道该路径是 Revit 模型附件。

### 7.2 订阅流式事件

将 `run_xxx` 替换为创建任务返回的 `run_id`：

```powershell
curl.exe -N `
  -H "Authorization: Bearer <随机本机Token>" `
  "http://127.0.0.1:18765/api/v1/runs/run_xxx/events"
```

常见事件顺序：

```text
run_queued → run_started → assistant_message / tool_started / tool_progress / tool_completed → run_completed
```

失败时最后事件为 `run_failed`；取消成功后为 `run_cancelled`。SSE 只返回用户可读摘要和必要文件路径，不应向 UI 推送模型 Key、完整 IFC 候选集或完整内部日志。

## 8. C# 桌面端对接要求

1. 从 Windows Credential Manager 或 DPAPI 读取模型 Key。
2. 每次启动生成随机 Runtime Token，通过子进程环境变量传入；不要出现在命令行、日志或数据库中。
3. 启动 Runtime 后轮询 `/api/v1/health`；成功后才允许创建 Run。
4. 从业务数据库取出经过筛选的 `user`、`assistant` 历史消息，传入 `history`；不要传旧工具调用记录、完整候选数据或内部审计 JSON。
5. 调用 `POST /api/v1/runs` 后订阅 SSE；`tool_progress` 只更新当前步骤状态，不保存为聊天消息；保存最终回复、状态和 artifact 路径。
6. 同一 `conversation_id` 已由 Runtime 串行执行；不同会话可并行，但所有 Revit 调用由跨进程锁串行化。
7. C# 主程序退出时应停止 Runtime 子进程；Runtime 崩溃时将未完成 Run 标记为 `runtime_lost`，提示用户重新执行，不能自动重放可能造成赋参副作用的任务。

## 9. Revit 任务规则

- IFC 标识赋值只在用户明确要求赋值、重建或重新赋值时执行。高层工具把它视为一次重建：清参、识别、赋值和另存各一次；任一阶段失败或状态未知后当前 Run 结束，不再次清参。
- 使用 `revit_open_project_model` 打开明确 RVT 或仅在用户指定文件夹内选择唯一模型；它等待准确模型窗口和 Revit 插件可继续执行后才返回 `ready`，返回后 Agent 直接继续下一工具，不要求用户再确认“已打开”。不扫描 Desktop、Downloads 或其他磁盘。
- `ensure_autocad_running` 默认只复用或从 AutoCAD 2020 卸载注册表和 `AutoCAD.Application.23.1` 定位安装；不使用 `.dwg` 默认关联，不选择 2026，不遍历磁盘。需要启动时，它优先复用开始菜单中目标与该安装一致的官方 AutoCAD 2020 快捷方式参数和工作目录（例如 `/product ACAD /language zh-CN` 与 `UserDataCache`），快捷方式缺失时才从同一安装目录构造回退命令。启动后等待主窗口、版本化 COM 与 PID 一致才返回。2026 已运行时保留它；没有 2020 时要求人工打开，不回退其他版本，也不单独启动或操作天正加载过程。
- 当前默认标准为深圳 `109003`。用户明确专业时优先使用；唯一独立代码可以自动选择，冲突、`G` 或规则不唯一时再询问。不得因文件名拦截、复制或重命名模型。
- Revit 插件为单线程 API，不能并发调用多个 Revit 功能接口。
- `revit_export_ifc` 只提交一次并最长等待两小时，必须在目标 Revit PID 内识别本次出现或由进度窗口复用而来的“导出已完成→确认”，并且 IFC 与 XLSX 稳定、完整后才成功；文件已生成但缺少最终确认时不得启动 SZ-IFC，超时不重启或重试。
- “质检”只表示 SZ-IFC 报建自检。已有 IFC 但历史尚未确认加载时，前端应向用户显示 IFC 绝对路径，请用户在 SZ-IFC 中手动打开；用户确认后直接调用 `revit_inspect_ifc`。
- `sz_ifc_open_model` 仅作为兼容的只读确认工具：检查准确文件名是否已显示且“模型检查”是否启用。它不会启动 SZ-IFC、不修改 `.ifc` 默认应用，也不在后台等待用户操作。
- 报告导出通过本次点击后新出现的 Windows 通用保存窗口 HWND 接管；保存窗口必须具有准确标题、`#32770` 类、文件名输入框，并与目标 SZ-IFC 进程或窗口所有者关联。保存后再验证“导出成功”和 DOCX ZIP 结构。

模型配置支持 `base_url + api_key`。优先读取 `BEESYNC_LLM_BASE_URL` / `BEESYNC_LLM_API_KEY`，其次读取显式 `BEESYNC_CONFIG_FILE` 的 `llm` 节，Base URL 缺省时使用发布包默认地址。冻结包不会让旧 ProgramData `config.toml` 静默覆盖发布默认值。`BEESYNC_HOST_MUST_INJECT_API_KEY` 只是占位符，`--doctor` 会把它报告为未配置，且 Runtime 不会把它发送到模型服务。

## 10. 常见故障排查

| 现象 | 原因与处理 |
| --- | --- |
| `unauthorized` | C# 请求未携带 `Authorization: Bearer <Token>`，或 Token 与 Runtime 启动时不一致。 |
| 无法连接 `18765` | Runtime 未启动、已崩溃或端口冲突。检查 Runtime 窗口和 `Test-NetConnection 127.0.0.1 -Port 18765`。 |
| Revit 返回 `Bad Request - Invalid Hostname` | 使用了 `127.0.0.1` 或漏掉了 `//api`。改为 `http://localhost:5000//api/RevitApi`。 |
| 模型服务返回 `401 invalid_api_key` | 检查 `BEESYNC_LLM_API_KEY` 或配置文件 `llm.api_key` 是否为真实 Key；`BEESYNC_HOST_MUST_INJECT_API_KEY` 无效。`--doctor` 会显示 Base URL、模型名和 Key 是否配置，但不输出 Key。内部 Revit MCP 继承相同配置。 |
| Runtime 在创建 Run 后直接退出 | 当前发布包的浏览器依赖可能有原生兼容性问题。设置 `BEESYNC_ENABLE_BROWSER=0` 后重试，并保留 Windows 事件查看器中的 `Application Error` 记录。 |
| 中文消息变成 `?` | PowerShell 测试请求必须按本文示例转为 UTF-8 字节；C# 使用 UTF-8 JSON 编码。 |
| Revit 接口 `code: 500` 或空引用 | 检查 Revit 已启动、插件已加载、目标模型已真正激活；不要同时打开多个可能抢占插件上下文的 Revit 实例。必要时由 Revit 插件同事检查 `ActiveUIDocument` 与 ExternalEvent 调用上下文。 |
| `--doctor` 显示 `39521` | 这是未注入 Revit 地址时的默认发布配置。当前测试插件在 5000 时，设置 `BEESYNC_REVIT_API_BASE_URL` 或部署 `revit-plugin.json`。 |

## 11. 升级与回滚

1. 每个 Runtime 版本安装到独立目录，例如 `runtime\1.1.0`、`runtime\1.1.1`。
2. C# 下载或安装完成后执行 `--doctor` 与 `/api/v1/health` 自检。
3. 自检通过才切换当前版本指针；失败则恢复上一稳定版本。
4. 活跃 Run 不迁移、不重放。升级前等待 Run 结束，或由 C# 明确标记为中断。
5. 官方 Skill 随 Runtime 升级；用户 Skill/MCP 位于用户数据目录，升级不得覆盖。

## 12. 发布前检查清单

- [ ] 在未安装 Python、Conda 和源码的干净 Windows 电脑上完成启动。
- [ ] `--doctor` 返回 `status: ok`。
- [ ] `/api/v1/health` 使用正确 Token 返回 `status: ok`。
- [ ] 中文消息以 UTF-8 创建 Run，SSE 能收到最终事件。
- [ ] Revit 插件地址使用 `localhost:5000//api/RevitApi` 且能够执行打开、清参、赋参、核验、保存。
- [ ] IFC 任务自动使用深圳标准 `109003`，不会询问客户 Standard ID。
- [ ] 同时提交两个 Revit 任务时，插件调用没有重叠。
- [ ] 日志、安装包、配置文件中不含生产模型 Key 或 Runtime Token。
- [ ] 浏览器能力未通过目标机验收前，保持 `BEESYNC_ENABLE_BROWSER=0`。
