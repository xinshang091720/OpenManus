---
name: revit-room-sync
description: 为建筑（AR）Revit 模型根据 DWG 图纸批量创建并命名房间，以及图纸坐标对齐。完全通过 Revit API 在后台执行，不需要启动 AutoCAD / 天正。用户要求房间创建、DWG 房间名同步时使用。
---

# Revit 房间创建与同步

面向建筑（AR）模型：
1. 通过 Revit 插件 `/DwgRevitGridData` 读取 Revit 与指定文件夹内各 DWG 的轴网坐标并自动对齐。
2. 通过 Revit 插件 `/GetDwgText` 提取 DWG 中的房间文字与坐标。
3. 批量创建房间、写入纯中文房间名，并安全另存模型。
4. **完全通过 Revit API 执行，不依赖、不调用、也不启动 AutoCAD / 天正。**

## 核心工具

- `revit_create_and_name_ar_rooms`：批量创建并命名建筑房间。

## 楼层判定与自主仲裁

1. 楼层解析以规范 DWG 文件名为主，结合图纸内部文字相互印证。
2. 若图纸内部仅存在局部弱标注（如室外机说明、示意图、做法说明等），系统优先采纳规范文件名自动对齐，避免误报。
3. 若遇到边缘情况导致工具返回 `selection_required`，Agent 应首先根据候选文字与图纸文件名进行语义常识判断（区分局部设备说明与整图图名）。若能确信判断，直接附带 `floor_overrides` 参数继续执行；若确实无法确定，再以通俗自然的工程师语言向用户提问。
