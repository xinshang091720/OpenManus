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
