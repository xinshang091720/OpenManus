# 房间功能_接口文档（room_coordinates）

# 1.主接口（`dwg_room_extractor_run.py`）

**功能描述**：`run_pipeline_and_update` 是 DWG 图纸房间名称自动提取与 Revit 模型更新的核心控制入口函数。该函数按顺序串联了 DWG 文件校验、批量提取房间与楼层数据、数据清洗（过滤非纯中文）以及调用 Revit 接口（http://localhost:5000//api/RevitApi/UpdateRoomName） 更新模型房间名称的完整业务逻辑。

### **1. 获取 DWG 轴网跟 RVT 轴网数据接口**

**接口说明**：模型楼层从低到高排序传入房间数据，判断房间名称点位是否在模型房间当中，如果符合要求进行修改模型房间名称

#### 请求示例：

```plaintext
curl --request POST \
  --url http://localhost:5000//api/RevitApi/UpdateRoomName \
  --header 'content-type: application/json' \
  --data '{
  "RoomData": [
    {
      "floor_num": 1000.5,
      "room_texts": [
        {
          "RoomName": "教师办公",
          "XYZ": "(94973.4497126556, 50615.58057917096, 0.0)"
        }
      ]
    },
    {
      "floor_num": 1000.5,
      "room_texts": [
        {
          "RoomName": "榫卯工坊",
          "XYZ": "(-98155.96216383521, 217.39966472471133, 0.0)"
        }
        {
          "RoomName": "参",
          "XYZ": "(27700.47478828515, -5998.609686776996, 0.0)"
        }
      ]
    },
    {
      "floor_num": 1000.5,
      "room_texts": [
        {
          "RoomName": "园艺辅助",
          "XYZ": "(38533.556326183985, 72818.33801674983, 0.0)"
        }
        {
          "RoomName": "室外安全区域",
          "XYZ": "(30457.408856632275, 26067.996915524825, 0.0)"
        }
      ]
    }
  ]
}'
```

#### 返回示例：

```plaintext
{
	"code": 200,
	"msg": "已全部修改完成 B3(AR-13.200)楼层共修改0个房间名称\nB2(AR-9.500)楼层共修改0个房间名称\n"
}
```

### 2.**Python 函数规范**

#### **流程逻辑与决策分支**

**步骤 1**：校验传入的 DWG 文件路径列表，过滤掉不存在或非 `.dwg` 后缀的无效文件。若无效则终止流程。
**步骤 2**：调用 `batch_process_dwg_files()`​ 批量提取房间与楼层数据（内部包含请求 Revit 接口获取文字/轴网、AI 识别房间和楼层、坐标平移等）。若触发 `eFileSharingViolation`（图纸被占用），则直接终止整个流程。
**步骤 3**：数据清洗。遍历提取到的房间数据，过滤掉 `RoomName` 中包含数字或特殊符号的记录，仅保留纯中文房间名。
**步骤 4**：调用 `send_update_room_name_request()`​ 将清洗后的数据推送到 Revit 服务端 `UpdateRoomName` 接口，完成模型房间名称的批量修改。

```text
                             [ 开始: run_pipeline_and_update(dwg_paths) ]
                                              │
                                  [ 步骤 1: 校验 DWG 路径列表 ]
                                              │
                                   ┌──────────┴──────────┐
                              存在有效文件?              │
                                   │                     │
                                 (是)                   (否)
                                   │                     │
       [ 步骤 2: batch_process_dwg_files ]       [ 返回: 记录错误并终止 ]
                                   │
                        ┌──────────┴──────────┐
                    提取是否被中断?             │
                   (如: 图纸被占用)            │
                        │                     │
                      (是)                   (否)
                        │                     │
         [ 返回: 记录错误并终止 ]     [ 步骤 3: 数据清洗 (过滤非纯中文) ]
                                                  │
                                       [ 步骤 4: 推送至 Revit UpdateRoomName ]
                                                  │
                                       ┌──────────┴──────────┐
                                   推送是否成功?             │
                                       │                     │
                                     (是)                   (否)
                                       │                     │
                  [ 返回: 记录成功日志 ]             [ 返回: 记录失败日志 ]
```

#### **函数签名**

```python
async def run_pipeline_and_update(dwg_paths: List[str])
```

#### **输入参数**

|参数名|类型|必填|说明|
| ------| ----| ----| -------------------------------|
|​`dwg_paths`|​`List[str]`|是|本地 DWG 图纸文件的绝对路径列表|

#### **返回值结构**

无直接返回值（`None`​）。流程执行结果通过控制台日志（`logger`）及 Revit 接口响应状态体现。

---

# 2.单独功能点

## 1.获得房间名称和对应坐标（`dwg_room_extractor.py`）

**功能描述**：此功能用于从 Revit 接口 `GetDwgText` 返回的 DWG 全图文字列表中，精准提取建筑房间名称及其对应的三维坐标。内部采用“正则表达式初筛 + Qwen 大模型（LLM）二次判定”的双重策略，确保房间名称识别的高准确率。

### Python 函数规范

#### **函数签名**

```python
def process_room_extraction_from_texts(text_items: list, max_workers: int = 10) -> dict
```

#### **输入参数**

|参数名|类型|必填|默认值|说明|
| ------| ----| ----| ------| ----------------------------------------------------|
|​`text_items`|​`list`|是|-|Revit 接口 `GetDwgText`​ 返回的 `data`​ 列表，包含 `Text`​, `Text_coordinates`​, `Text_layer` 等字段|
|​`max_workers`|​`int`|否|10|调用 Qwen 大模型进行房间判定的最大线程并发数|

#### **返回值**

数据类型: `dict`

|字段名|数据类型|说明|
| ------| --------| -------------------------------------------------------------------------------|
|​`room_texts`|​`List[Dict[str, str]]`|提取出的房间列表。每个字典包含 `RoomName`​ (清洗后的房间名) 和 `XYZ` (字符串格式的三维坐标)|

#### **返回示例**

```json
{
    "room_texts": [
        {
            "RoomName": "物业管理用房",
            "XYZ": "(507552.329, 2506033.104, 0.0)"
        },
        {
            "RoomName": "教师办公",
            "XYZ": "(94973.449, 50615.580, 0.0)"
        }
    ]
}
```

## 2.获得层号（`git_cad_floor.py`）

**功能描述**：此功能用于基于图纸的全部文字内容列表及图纸文件名，智能判断当前图纸所属的楼层区间。内部复用正则初筛提取包含楼层关键字的候选文本，结合 Qwen 大模型进行全局语义分析，输出楼层的最低值、最高值及匹配的关键文本。

### **Python 函数规范**

#### **函数签名**

```python
def judge_floor_from_texts(raw_texts: List[str], dwg_filename: str = None) -> Dict[str, Any]
```

#### **输入参数**

|参数名|类型|必填|默认值|说明|
| ------| ----| ----| ------| ------------------------------------------------------------------|
|​`raw_texts`|​`List[str]`|是|-|图纸中所有的原始文字内容列表|
|​`dwg_filename`|​`str`|否|​`None`|图纸的文件名（如 "综合楼 二层平面图.dwg"），作为最高优先级参考信息|

#### **返回值** 数据类型: `Dict[str, Any]`

|字段名|数据类型|说明|
| ------| --------| ------------------------------------------------------------------------|
|​`matched_text`|​`str`|匹配到的最主要的原本文本（或文件名）|
|​`min_floor`|​`float`|最低楼层数值（如普通层为 1.0，区间层如 6~29 层则为 6.0，屋顶为 29.8 等）|
|​`max_floor`|​`float`|最高楼层数值（规则同上）|

#### **返回示例**

```json
{
    "matched_text": "六～二十九层平面图",
    "min_floor": 6.0,
    "max_floor": 29.0
}
```

---

## 3.拼接房间赋值数据（`dwg_room_extractor_main.py`）

**功能描述**：此模块是批量处理 DWG 文件的核心调度中心。负责串行请求 Revit 接口获取轴网与文字数据（`http://localhost:5000/api/RevitApi/DwgRevitGridData`​，`http://localhost:5000/api/RevitApi/GetDwgText`），并行处理本地数据（房间抽取、楼层判断、坐标平移转换、楼层展开），最终拼接并返回按楼层排序的房间赋值数据结构。

本模块内部串行调用了以下两个 Revit 接口：

### **1. 获取 DWG 轴网跟 RVT 轴网数据接口**

**接口说明**：获取 dwg 图纸轴网数据跟模型轴网数据。从 dwg 图纸获取其中一条轴网数据与模型轴网数据相交的线，返回给 AI 进行计算二根线中点之间的偏移量

#### 请求示例：

```plaintext
curl --request POST \
  --url http://localhost:5000//api/RevitApi/DwgRevitGridData \
  --header 'content-type: application/json' \
  --data '{
    "dwgFilePath": "C:\Users\wang1\Desktop\测试AC\地下一层平面图.dwg"
}'
```

#### 返回示例：

```plaintext
{
	"code": 200,
	"msg": "返回dwg轴网跟rvt轴网数据",
	"rvtGrid": {
		"AxisCode": null,
		"Begin_Position": null,
		"End_Position": null
	},
	"dwgGrid": {
		"AxisCode": null,
		"Begin_Position": null,
		"End_Position": null
	}
}
```

### **2. 获取 DWG 图纸文字接口**

接口说明：获取 DWG 图纸所有的文字，文字坐标，文字图层

#### 请求参数：

|**参数名**|**数据类型**|**是否必填**|**默认值**|**示例值**|**说明**|
| ----| ----| -| -| ----| -------------------------------------------|
|​`dwgFilePath`|​`String`|**是**|-|​`D:\project\data\test.dwg`|目标 DWG 文件的绝对路径（包含文件名及后缀）|

#### 请求示例

```
curl -L -X POST "http://localhost:5000/api/RevitApi/GetDwgText" \
-H "Content-Type: application/json" \
-d "{
    \"dwgFilePath\": \"D:\\project\\ai_agent\\data\\test\\雪象全专业(1)\\满京华下雪总平面图.dwg\" 
}"
```

#### 返回示例

JSON

```
{
    "code": 200,
    "msg": "成功",
    "data": [
        {
            "Text": "物业管理用房",
            "Text_coordinates": "(507552.329647414,2506033.1049056,0.000105220004823627)",
            "Text_layer": "半地下室平面图"
        },
        {
            "Text": "1C-A",
            "Text_coordinates": "(507571.008658181,2506080.76761651,0)",
            "Text_layer": "AXIS_TEXT"
        }
    ]
}
```

#### 返回参数：

|**字段名**|**数据类型**|**说明**|
| ----| ----| ------------------------------------------------------|
|​`Text`|​`String`|提取出的文本内容（可能包含 CAD 控制符/多行文本控制码）|
|​`Text_coordinates`|​`String`|文本节点在 DWG 中的三维坐标，格式为 `"(东坐标, 北坐标, 高程/Z)"`|
|​`Text_layer`|​`String`|该文本节点所在的 CAD 图层名称|

### 3.**Python 函数规范**

#### **函数签名**

```python
async def batch_process_dwg_files(dwg_paths: List[str]) -> dict
```

#### **输入参数**

|参数名|类型|必填|说明|
| ------| ----| ----| -------------------------------|
|​`dwg_paths`|​`List[str]`|是|本地 DWG 图纸文件的绝对路径列表|

#### **返回值**

数据类型: `dict`​ 或 `str` (异常终止时)

|字段名|数据类型|说明|
| ------| --------| ---------------------------------------------------------------------------------|
|​`RoomData`|​`List[Dict[str, Any]]`|按楼层从小到大排序的房间数据列表。每个元素包含 `floor_num`​ (楼层号) 和 `room_texts` (该层的房间列表)|

#### **异常处理机制**

|触发条件|日志记录|函数返回值|
| ----------------------------------| ---------------------------------| ------------------------------------------------|
|DWG 文件被 AutoCAD 等程序占用 (`eFileSharingViolation`)|记录 Error 日志，取消所有后台任务|返回错误提示字符串 (如 `"关闭任何方式打开的图纸"`)，上层主流程捕获后终止|
|接口请求超时或网络异常 (重试耗尽)|记录 Error 日志，跳过该文件|正常返回其他成功文件的数据，失败文件不计入 `RoomData`|

#### **返回示例**

```json
{
    "RoomData": [
        {
            "floor_num": -1.0,
            "room_texts": [
                {
                    "RoomName": "水泵房",
                    "XYZ": "(100.5, 200.3, 0.0)"
                }
            ]
        },
        {
            "floor_num": 1.0,
            "room_texts": [
                {
                    "RoomName": "物业管理用房",
                    "XYZ": "(507552.3, 2506033.1, 0.0)"
                },
                {
                    "RoomName": "教师办公",
                    "XYZ": "(94973.4, 50615.5, 0.0)"
                }
            ]
        }
    ]
}
```
