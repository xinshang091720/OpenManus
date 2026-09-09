---
name: autocad-operator
description: AutoCAD 通用代码解释器与自主操作：在本地活动图纸中动态执行 Python-COM 脚本或 AutoLISP 命令，完成图层管控、文字检索替换、属性块数据提取、几何绘图、深度清理（Purge/Audit）与无弹窗批量出图 (PDF)。当用户要求对 AutoCAD 软件或 DWG 图纸进行任意查询、统计、修改、绘制或批处理时使用。
---

# AutoCAD 通用代码解释器与自主操作指南 (autocad-operator)

本 Skill 将 Agent 转变为一名熟练的 AutoCAD 自动化工程师。通过调用 `cad_run_code` 工具，Agent 可以直接在本地运行中的 AutoCAD 实例及活动图纸中执行 Python-COM 脚本或 AutoLISP 命令。

---

## 核心执行工具：`cad_run_code`

- **默认语言**：`python`（直接面向对象模型，推荐首选）。
- **备选语言**：`command` / `lisp`（用于发送纯 AutoCAD 命令行指令或 AutoLISP 表达式）。
- **预注入全局变量**（Python 模式下直接可用）：
  - `acad` / `app`: AutoCAD Application 根对象。
  - `doc`: 当前活动文档（`acad.ActiveDocument`）。
  - `model_space` / `mspace`: 模型空间（`doc.ModelSpace`）。
  - `paper_space` / `pspace`: 布局/图纸空间（`doc.PaperSpace`）。
  - `layers`: 图层集合（`doc.Layers`）。
  - `blocks`: 块定义集合（`doc.Blocks`）。
  - `APoint(x, y, z=0.0)`: 快速生成 AutoCAD COM 所需的 3D 双精度坐标点。
  - `send_command(cmd)`: 发送单行命令。
- **重要规则**：环境通过捕获 `sys.stdout` 获取执行反馈。**必须在脚本中使用 `print(...)` 打印处理结果或统计数据**，否则无法获知执行详情。

---

## 工程师作业四步法 (Core Mental Model)

在为用户编写执行脚本前，严格遵循以下思维逻辑：

### 第一步：状态探查 (Inspection)
- 严禁盲目假设图层、图块已经存在。
- 修改图层前先遍历或用 `try...except` 检查图层；修改实体前先确认其 `ObjectName`。

### 第二步：命令静默防死锁 (Silent Execution)
- 若使用命令行或 `doc.SendCommand`，**严禁触发 GUI 交互弹窗**（否则 AutoCAD 会因弹窗等待人工点击而导致 Agent 彻底超时死锁）。
- 所有命令必须使用前缀横杠 `-`（静默模式）：
  - 导出/打印：使用 `-PLOT` 或 `-EXPORT`，严禁使用 `PLOT` 或 `EXPORT`。
  - 图层管理：使用 `-LAYER`，严禁使用 `LAYER`。
  - 清理：使用 `-PURGE`，严禁使用 `PURGE`。
- 执行器已自动在后台开启 `FILEDIA=0` 和 `CMDDIA=0` 保护。

### 第三步：精确坐标与捕捉安全 (OSMODE)
- 在通过命令绘制线条或插入图块前，AutoCAD 的对象捕捉可能会把坐标强行吸附到相邻点。
- 如需通过命令行绘图，临时记录并设置 `doc.SetVariable("OSMODE", 0)`，绘图完毕后恢复。

### 第四步：统计与结果回显 (Verification)
- 脚本必须具备统计意识：处理了几个实体、修改了几个图层、导出了哪个文件，均用 `print()` 明确输出，以便直接向用户汇报。

---

## 常用对象模型与实用代码配方 (Code Recipes)

### 1. 图层操作 (Layers)

```python
# 检查并创建新图层，设置颜色与线宽
layer_name = "AI_ANNOTATION"
try:
    target_layer = layers.Item(layer_name)
except Exception:
    target_layer = layers.Add(layer_name)

# 颜色索引：1=红, 2=黄, 3=绿, 4=青, 5=蓝, 6=洋红, 7=白/黑
target_layer.Color = 1  
target_layer.Freeze = False
target_layer.Lock = False
print(f"图层 {layer_name} 准备就绪，颜色已设为红色。")
```

### 2. 文本检索与批量替换/修改 (Text & MText)

```python
# 遍历模型空间中所有单行与多行文字，查找包含特定关键词的内容并修改
search_kw = "未定"
replace_kw = "已确认"
count = 0

for entity in model_space:
    if entity.ObjectName in ["AcDbText", "AcDbMText"]:
        text_val = entity.TextString
        if search_kw in text_val:
            entity.TextString = text_val.replace(search_kw, replace_kw)
            count += 1

print(f"共完成 {count} 处文字替换：'{search_kw}' -> '{replace_kw}'。")
```

### 3. 图框属性块提取 (Block Attributes to Data)

```python
# 提取图纸中图框块的属性（如工程名称、图号、图名、日期）
records = []
for entity in model_space:
    if entity.ObjectName == "AcDbBlockReference":
        # 获取块的有效名称
        blk_name = getattr(entity, "EffectiveName", entity.Name)
        if "图框" in blk_name or "TITLE" in blk_name.upper():
            attrs = entity.GetAttributes()
            attr_dict = {attr.TagString: attr.TextString for attr in attrs}
            records.append({"block": blk_name, "attributes": attr_dict})

print(f"找到 {len(records)} 个图框块，提取属性如下:")
for r in records:
    print(r)
```

### 4. 几何绘制 (Geometry)

```python
# 使用 APoint 辅助工具创建直线和圆
p1 = APoint(0, 0, 0)
p2 = APoint(5000, 3000, 0)
line = model_space.AddLine(p1, p2)
line.Layer = "0"

center = APoint(2500, 1500, 0)
circle = model_space.AddCircle(center, 800)
circle.Color = 3  # 绿色

print(f"成功绘制直线 (Handle: {line.Handle}) 与圆 (Handle: {circle.Handle})。")
```

### 5. 深度图纸清理与修复 (Purge All & Audit)

```python
# 通过静默命令行彻底清理无用块、图层、线型并修复错误
send_command("-PURGE A * N\n")
send_command("AUDIT Y\n")
print("已成功触发全部对象清理 (-PURGE All) 与图纸修复 (AUDIT)。")
```

### 6. 无弹窗导出 PDF (-PLOT)

```python
# 静默配置当前布局并打印输出为 PDF
import os
output_pdf = os.path.abspath("output.pdf")
# 发送静默打印命令
cmd = f'-PLOT Y  DWG To PDF.pc3 "ISO full bleed A1 (841.00 x 594.00 MM)" M L N W 0,0 84100,59400 1:1 C Y monochrome.ctb Y N N N "{output_pdf}" N Y\n'
send_command(cmd)
print(f"正在后台导出 PDF 至: {output_pdf}")
```

---

## 自省与自愈套路 (Self-Healing Loop)

当 `cad_run_code` 返回报错时，按以下规则自主反思并重试：

1. **`未检测到运行中的 AutoCAD 实例`**：
   - 提示用户启动 AutoCAD 软件并打开图纸，或检查是否有权限阻止了 COM 通信。
2. **`图层已被锁定或冻结 (0x80010105 / eLayerLocked)`**：
   - 在修改图元属性前，先将图元的对应图层属性 `layer.Lock = False` 和 `layer.Freeze = False`，再进行属性设置。
3. **`参数类型不匹配 (Type mismatch / Invalid index)`**：
   - AutoCAD COM 的点坐标必须传入包含 3 个浮点数的数组/VARIANT，使用 `APoint(x, y, z)` 包装，严禁直接传入 Python tuple `(x, y)`。
4. **`AutoCAD 处于忙碌状态 (Call was rejected by callee)`**：
   - 用户可能正在 AutoCAD 界面中进行命令交互或选点，短暂等待 1-2 秒后再重试执行。
