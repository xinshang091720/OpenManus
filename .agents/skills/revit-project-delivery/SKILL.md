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
