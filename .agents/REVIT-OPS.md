# Revit / CAD / SZ-IFC 交付操作手册

本文档是给在本工作区里替我"亲手操作" Revit 交付流程的 Agent 用的速查手册。
来源：`app/revit/*`、`app/workflows/revit_ifc_assignment.py`、`app/tool/*.py`、
`q_agent_function_module/.../ifc_function/ifc_SZ-IFC_to_docx.py`、`skills/*/SKILL.md`。

## 0. 环境与调用方式（已实测可用）

- Python 3.13.9，依赖（httpx / pydantic / requests / pywinauto / win32）已安装。
- 从仓库根目录运行，`sys.path.insert(0, ".")` 后即可 import `app.*`。
- 插件地址解析顺序：环境变量 `BEESYNC_REVIT_API_BASE_URL` → `config/revit-plugin.json` 的
  `apiBaseUrl` → 未打包时 `http://localhost:5000//api/RevitApi`，打包后 `http://127.0.0.1:39521/api/RevitApi`。
- 长操作（最长 2 小时）务必用后台任务跑并轮询，不要同步阻塞。

## 1. 业务工具 → Python 入口速查

| 业务工具名 | 直接入口（类/方法） | 是否写模型 | 前置条件 |
| --- | --- | --- | --- |
| `revit_open_project_model` | `app/tool/desktop_bim.py: RevitOpenProjectModel.execute` | 否 | 单 Revit 实例、版本匹配 |
| `sz_ifc_open_model` | `app/tool/desktop_bim.py`（只读确认 SZ-IFC 已加载） | 否 | 用户在 SZ-IFC 手动加载 IFC |
| `revit_set_base_point` | `RevitProjectDelivery.set_base_point`（`app/revit/project_delivery.py`） | 是 | 支持用户指定坐标或从 DWG 提取 |
| `revit_create_and_name_ar_rooms` | `ArRoomCreationWorkflow.run`（`app/revit/room_creation.py`） | 是 | 仅 AR/建筑；DWG 文件夹（无需 AutoCAD） |
| `revit_run_ifc_assignment` | `RevitWorkflow.run`（`app/workflows/revit_ifc_assignment.py`）包装 `RevitIfcAssignmentWorkflow.run` | 是 | 活动模型；专业已确认；含 preflight + 保存到 `ifc-assigned` |
| `revit_export_ifc` | `RevitProjectDelivery.export_ifc` | 是 | 恰好 1 个 Revit 实例 |
| `revit_inspect_ifc` | `RevitProjectDelivery.inspect_ifc` → `run_sz_ifc_full_inspection` | 否(Revit) | SZ-IFC 已加载该 IFC |
| `revit_preview_room_sync` / `revit_apply_room_sync` | `RoomSyncWorkflow.preview/apply`（`app/revit/room_sync.py`） | 预览否/应用是 | 旧版两步流程 |
| 底层 API | `RevitApiClient`（`app/revit/client.py`） | — | 见方法列表 |

`RevitApiClient` 方法：`open_revit_file`、`base_point_setting`、`base_point_setting_is_correct`、`get_dwg_text`、`export_ifc`、`save_as`、
`batch_create_rooms`、`update_room_name`、`dwg_revit_grid_data`、`one_click_identifier`、
`get_ifc_ident`、`get_level_ifc_ident`、`clear_parameters`、`one_click_assignment`、`open_delivery`。

关键基础设施：
- `call_revit_operation`（`app/revit/operations.py`）：统一两小时上限 + 30s 心跳，超时抛 `RevitOperationUnknown`，**不自动重试**。
- `RevitProcessLock`（`app/mcp/revit_lock.py`）：Windows 命名互斥锁，跨进程串行，同请求内可重入；跨进程共用 `Local\OpenManusRevitPluginApiLock`。
- `resolve_fresh_saved_model_path` / `snapshot_folder_files`（`app/revit/save_result.py`）：SaveAs 后按"原文件名 / 去后缀名"验证新产物。

## 2. 续办状态机（最容易出错，务必遵守）

- 本项目的"质检/自检"只指 SZ-IFC 报建自检（`revit_inspect_ifc`），不是 IFC 赋值。
- 已有 IFC 绝对路径但未确认加载 → 告知该绝对路径，请用户在 SZ-IFC 手动加载；可 `sz_ifc_open_model` 只读确认一次（未加载立即返回 `user_action_required`，不要后台等待）。
- 若 `revit_inspect_ifc` 发现 SZ-IFC 软件未打开或未加载模型，会立即返回 `user_action_required`，直接按返回提示用户在 SZ-IFC 中手动加载 IFC。
- 历史已确认加载，或收到"已加载/已准备好/继续/已经打开/ok/弄好了" → **直接 `revit_inspect_ifc`**，严禁重新开模型、建房、赋值或导出。
- 只有用户**明确**要求"赋值/重新赋值/补充/重建"时才调用 `revit_run_ifc_assignment`；"质检"绝不触发赋值。
- 房间成功返回 `saved_model_path` 后，后续导出直接用该结果模型，不要重新打开源 RVT（会丢房间）。
- 专业代码：`AR/A`=建筑、`ST/S/FS/SS`=结构、`AC/M`=通风空调、`PD/P`=给排水、`EL/E/T`=电气；`G` 有歧义（总图/燃气）必须问。

## 3. 安全边界（硬约束）

- **严禁编写任何 Python 脚本去启动 AutoCAD、调用 AutoCAD COM 或执行 `accoreconsole` 转换 DXF**。所有 DWG 提取完全由 Revit 插件内部 API 执行。
- 所有 Revit 调用保持**串行 + 单实例**；不启动第二个 Revit；IFC 导出要求恰好一个 Revit 进程。
- 不覆盖已有交付物（自动 `result` / `result-N` / `名称(1)` 后缀）。
- 写入结果未知（超时）时**不自动重试**，向用户说明状态未知。
- 不扫描磁盘、不复制/改名/替换 RVT；只用用户明确提供或指定文件夹内的路径。
- **基点设置支持用户直接决策指定坐标数值，也支持从 DWG 图纸中自动提取**。直接通过 `revit_set_base_point` 执行。

## 4. 已知代码缺陷（操作时注意，见之前 review）

- `OpenDelivery` 有两套实现：`RevitApiClient.open_delivery`（httpx，未被生产调用）与
  `q_agent...call_open_delivery_api`（requests，实际使用且失败被吞）。失败会被误报成成功。
- `RevitProjectDelivery.inspect_ifc` 的 `delivery_status` 文案硬编码"已自动打开…"，未经验证。
- 报告解析失败（`extract_failed_element_ids_from_docx` 抛异常被 `except: pass` 吞掉）会被误报成"100% 通过"。

→ 操作质检流程后，若出现"未通过构件"结果，应交叉核对 `report_path` 与实际 OpenDelivery 返回，不要轻信文案。
