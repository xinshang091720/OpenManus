---
name: revit-project-delivery
description: 本地 Revit 与 SZ-IFC 项目交付：打开模型、设置基点（支持从 DWG 图纸自动提取或用户指定坐标）、导出 IFC、SZ-IFC 报建自检（质检）。当用户要求设置基点、根据图纸设置基点、打开模型、导出 IFC、报建自检/质检，或回复“已加载/继续”等确认时，必须优先激活本 Skill 并调用对应工具。
---

# Revit 项目交付

所有操作通过运行内置的 Revit 业务工具完成，完全通过 Revit API 执行，不依赖、不调用、也不启动 AutoCAD / 天正。

## 核心业务工具

- `revit_open_project_model`：按版本打开指定 RVT 模型。
- `revit_set_base_point`：设置 Revit 模型基点。支持从指定 DWG 图纸自动提取坐标（传入 `dwg_path`），也支持直接写入用户提供的坐标数值（`north_south`, `east_west`, `elevation`, `angle_to_north`）。**严禁自行写 Python 脚本调用 AutoCAD 或转 DXF**。
- `revit_export_ifc`：导出 IFC 及配套 Excel 清单文件。
- `sz_ifc_open_model`：只读确认 SZ-IFC 已加载 IFC 模型。
- `revit_inspect_ifc`：接管 SZ-IFC 软件，自动选择规则、执行模型检查并导出 DOCX 质检报告。

## 安全边界与禁令（硬约束）

- **严禁编写任何 Python 脚本去启动 AutoCAD、调用 AutoCAD COM 或执行 `accoreconsole` 转换 DXF**。所有 DWG 提取完全由 Revit 插件内部 API 执行。
- 设置基点或提取图纸信息时，**必须且仅能通过 `revit_set_base_point(dwg_path=...)` 执行**。
- Revit 调用保持串行、单实例；IFC 导出要求恰好一个 Revit 进程。
- 房间创建完全在 Revit 内完成，**不启动 AutoCAD / 天正**。

## 复合交付流程与连续执行（避免无意义中断）

当用户提交了包含多个环节的复合任务（例如“打开模型、创建房间、进行IFC标识和自检”）：
1. **端到端自主执行**：各步骤依序执行（打开模型 → 房间创建 → IFC赋值 → IFC导出 → SZ-IFC自检）。严禁在第一步停下来要求用户确认是否对即将打开的模型赋值或确认专业（用户指定了文件夹或建筑模型时，直接打开建筑 AR 模型并自主连续执行各阶段），步骤之间严禁调用 `ask_human` 或提前中断向用户询问。
2. **SZ-IFC 加载中断与续办**：导出 IFC 后，若调用 `revit_inspect_ifc` 发现 SZ-IFC 未运行或未加载目标 IFC，工具会返回提示用户手动加载的操作说明。用户回复“已经打开/已加载/继续”后，直接调用 `revit_inspect_ifc`，严禁重新打开模型、重复建房、重复赋值或重复导出。
3. **模型接续**：房间创建完成后模型另存为 `result` 目录下结果模型，后续的 IFC 赋值与导出必须使用该结果模型，严禁重新打开初始源模型。
