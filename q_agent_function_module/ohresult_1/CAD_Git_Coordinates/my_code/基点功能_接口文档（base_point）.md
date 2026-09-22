# 基点功能_接口文档（base_point）

# 1. 主接口（main\_pipeline.py）

功能描述：`run_pipeline` 是 Revit 模型基点自动修复的核心控制入口函数。该函数按顺序串联了模型基点校验、DWG 图纸提取以及 Revit API 基点修改的完整业务逻辑

## 流程逻辑与决策分支

1. **步骤 1：**  调用 `check_base_point()` 验证原始模型基点字段完整性。若完全正常则跳过修复，直接返回成功。
2. **步骤 2：**  若原始基点存在缺失，调用 `get_base_point_from_dwg()` 从 DWG 图纸中提取坐标、标高与旋转角。
3. **步骤 3：**  调用 `update_base_point()` 提交数据至 Revit 服务端更新基点。
4. **步骤 4：**  调用 `get_missing_fields()` 对更新后的数据进行二次字段完整性校验，缺失字段将自动转换为中文提示。

```
                             [ 开始: run_pipeline(dwg_path) ]
                                             │
                                 [ 步骤 1: check_base_point ]
                                             │
                                  ┌──────────┴──────────┐
                             基点是否完整?              │
                                  │                     │
                                (是)                   (否)
                                  │                     │
      [ 返回 SUCCESS: 原始模型基点完整无误 ]     [ 步骤 2: get_base_point_from_dwg ]
                                                        │
                                           [ 步骤 3: update_base_point ]
                                                        │
                                             ┌──────────┴──────────┐
                                         API调用成功?              │
                                             │                     │
                                           (是)                   (否)
                                             │                     │
                                 [ 步骤 4: get_missing_fields ]  [ 返回 FAILED: 调用修改基点接口失败 ]
                                             │
                                  ┌──────────┴──────────┐
                              有无缺失字段?             │
                                  │                     │
                                (有)                   (无)
                                  │                     │
     [ 返回 ERROR_INCOMPLETE: 修改错误，缺少... ]   [ 返回 SUCCESS: 基点修改成功 ]
```

## 函数签名

```
def run_pipeline(dwg_path: str) -> Dict[str, Any]
```

## 输入参数 

|**参数名**|**类型**|**必填**|**说明**|
| ------| ------| --| -------------------------------------------|
|​`dwg_path`|​`str`|**是**|本地或网络共享盘中 DWG 图纸文件的绝对路径|

## 返回值结构 

- ​**数据类型**​: `Dict[str, Any]`

|**字典 Key**|**类型**|**是否必返**|**说明**|
| ------| ------| ----------| ----------------------------------------------------------------------------|
|​`status`|​`str`|**是**|流程处理状态码（`SUCCESS`​/`ERROR_INCOMPLETE`​/`FAILED`）|
|​`msg`|​`str`|**是**|人类可读的状态说明或错误提示文本|
|​`data`|​`Dict[str, Any]`|条件返回|模型基点数据字典，包含更新后或原始的模型基点字段。仅在调用失败时可能为空。|

## ​​`status` 状态说明表

|**status 状态码**|**msg 示例**|**说明与触发场景**|
| ------| ----------| -------------------------------------------------------------------------------------------------------------------------|
|​**​`SUCCESS`​**|​`"原始模型基点完整无误"`​或`"基点修改成功"`|**处理成功**：<br />1. 步骤 1 校验原始模型基点已完整存在，无需修改； <br />2. 经过 DWG 解析并修改后，四个核心字段均已被成功补充并完整返回。|
|​**​`ERROR_INCOMPLETE`​**|​`"修改错误，缺少：调整项目高程、修改正北角度"`|**数据不完整**：<br />修改接口已被正常调用，但修改结果数据中仍然存在为空或缺失的关键字段。`msg`会拼接具体的中文字段名称提示。|
|​**​`FAILED`​**|​`"调用修改基点接口失败"`|**请求失败**：<br />调用修改基点 API 失败或抛出网络/系统异常，且未返回有效数据。|

## 响应 JSON 示例

### 场景一：原始基点完整 (步骤 1 直接通过)

```
{
    "status": "SUCCESS",
    "msg": "原始模型基点完整无误",
    "data": {
        "Northsouth": "2506045399.00",
        "Eastwest": "507491908.00",
        "Elevation": "73900.00",
        "Angleton": "120"
    }
}
```

### 场景二：完整执行并通过 DWG 修复成功

```
{
    "status": "SUCCESS",
    "msg": "基点修改成功",
    "data": {
        "Northsouth": "2506045399.00",
        "Eastwest": "507491908.00",
        "Elevation": "73900.00",
        "Angleton": "120"
    }
}
```

### 场景三：修复后数据仍缺失关键字段

```
{
    "status": "ERROR_INCOMPLETE",
    "msg": "修改错误，缺少：调整项目高程、修改正北角度",
    "data": {
        "Northsouth": "2506045399.00",
        "Eastwest": "507491908.00",
        "Elevation": null,
        "Angleton": ""
    }
}
```

### 场景四：调用修改接口失败

```
{
    "status": "FAILED",
    "msg": "调用修改基点接口失败"
}
```

‍

# 2. 单独功能点

## 1. 验证原始模型基点数据（check_base_point.py）

**功能描述：** 此函数用于调用外部 Revit 接口（`verify_datum_mark: "http://localhost:5000/api/RevitApi/BasePointSettingIsCorrect"`）以获取原始模型基点数据，并验证返回的数据字段（南北坐标、东西坐标、高程、角度）是否完整。

### 1. 外部 HTTP 接口要求 (Revit API)

**接口说明**：验证模型项目基点是否符合标准，值不为空的代表符合标准，返回的值为毫米单位

- ​**请求方式**​: `POST`
- **请求 URL**: 该地址由项目根目录下 `config.yaml`​ 文件中的 `revit.verify_datum_mark` 配置项决定。
- **请求超时**: 15 秒
- **请求参数**: 无请求体

#### 请求示例：

```plaintext
curl --request POST \
  --url http://localhost:5000//api/RevitApi/BasePointSettingIsCorrect
```

#### 返回示例：

```plaintext
{
	"code": 200,
	"msg": "基点北南达到标准,基点东西达到标准,高程达到标准",
	"data": {
		"Northsouth": "492766431",
		"Eastwest": "2519189704",
		"Elevation": "25200",
		"Angleton": ""
	}
}
```

#### 返回参数：

|**字段**|**类型**|**说明**|
| :----------: | :------: | :----: |
|Northsouth|string|北南|
|Eastwest|string|东西|
|Elevation|string|高程|
|Angleton|string|角度|

### 2. Python 函数规范

#### 函数签名

```json
def check_base_point()
```

#### **返回值**:

- ​**数据类型**​: `Tuple[bool, Dict[str, Any]]`
- ​**结构**​: `(is_complete, data_dict)`

|**变量名**|**类型**|**说明**|
| ------| ------| -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
|​`is_complete`|​`bool`|**数据完整性校验标识**：<br />•**​`True`​**​：HTTP 状态码为 200、业务`code`​为 200，且`data`​中的`Northsouth`​、`Eastwest`​、`Elevation`​、`Angleton`​四个字段均存在、不为`None`且去除首尾空格后不为空字符串。 <br />•**​`False`​**​：网络请求失败、状态码非 200、业务`code`异常，或上述任一字段缺失/为空。|
|​`data_dict`|​`dict`|**接口响应数据**：<br />• 返回接口响应中的`data`对象字典。 <br />• 若在请求或解析过程中发生任何异常（如 HTTP 错误、业务`code`​异常或抛出 Exception），则返回空字典`{}`。|

#### 异常处理机制

|**触发条件**|**日志记录**|**函数返回值**|
| ----------------------------| ----------| --------|
|HTTP 状态码不等于 200|记录**Error**日志|​`False, {}`<br />|
|返回 JSON 中的`code`不等于 200|记录**Warning**日志|​`False, {}`<br />|
|其他代码层面异常 (`Exception`)|记录**Error**日志|​`False, {}`<br />|

---

## 2. 修改原始模型基点数据（update_base_point.py）

**功能描述：** 此功能用于调用 Revit 修改基点接口 （`update_datum_mark: "http://localhost:5000/api/RevitApi/BasePointSetting"`）完成对模型基点的修改并返回修改后的基点数据字段（南北坐标、东西坐标、高程、角度）。

### 1. 外部 HTTP 接口请求规范 (Revit API)

**接口说明**：修改项目基点 ，二种情况  一种是总图纸获取的数据  一种是上一个模型达到标准的数据 

- **请求方式**: `POST`
- **请求 URL**: 该地址由项目根目录下 `config.yaml`​ 文件中的 `revit.update_datum_mark` 配置项决定。
- **请求超时**: 15 秒
- **请求格式**: `application/json`​（以 JSON 结构传递 `payload`）。

#### 请求参数：

|**参数名**|**类型**|**必填**|**说明**|
| ------------| --------| ----| ------------------------------|
|Northsouth|string|是|北南|
|Eastwest|string|是|东西|
|Elevation|string|是|高程|
|Angleton|string|是|角度|
|IsGeneral|int|是|0 总图数据  1 上一个专业数据|

#### 请求示例：

```plaintext
curl --request POST \
  --url http://localhost:5000//api/RevitApi/BasePointSetting \
  --header 'content-type: application/json' \
  --data '{
  "Coordinates": [
    {
      "Northsouth": "2506045399.00",
      "Eastwest": "507491908.00",
      "Elevation": "73900.00",
      "Angleton":"120",
      "IsGeneral":1
    },
    {
     "Northsouth": "2000",
      "Eastwest": -27126.368,
      "Elevation": "",
      "Angleton":"",
      "IsGeneral":0
    }
  ]
}'
```

#### 返回示例：

```plaintext
{
	"code": 200,
	"msg": "调整基点坐标北南,调整基点坐标东西，调整项目高程修改正北角度",
	"data": {
		"Northsouth": "2506045399.00",
		"Eastwest": "507491908.00",
		"Elevation": "73900.00",
		"Angleton": "120"
	}
}
```

### 2. Python 函数规范

####  函数签名

Python

```
def update_base_point(
    step1_data: Dict[str, Any],
    step2_data: Optional[Dict[str, Any]] = None
) -> Tuple[bool, Dict[str, Any]]
```

#### 输入参数 (Input Parameters)

|**参数名**|**类型**|**必填**|**默认值**|**说明**|
| ------| ------| ----| ------| -----------------------------------------------------|
|​`step1_data`|​`Dict[str, Any]`|**是**|-|步骤 1 获取的原始模型基点，需包含`Northsouth`​,`Eastwest`​,`Elevation`​,`Angleton`<br />|
|​`step2_data`|​`Dict[str, Any]`|否|​`None`|步骤 2 提取的 DWG 数据，需包含`coordinates`​数组及外层的`Elevation`​,`Angleton`<br />|

#### 返回值 (Return Values)

- ​**数据类型**​: `Tuple[bool, Dict[str, Any]]`

|**元组索引**|**变量名**|**类型**|**说明**|
| ------| ------| ------| ----------------------------------------------------------------------------------------------------|
|​`[0]`|​`is_success`|​`bool`|**调用结果**：HTTP 状态码及业务`code`​均等于 200 时为`True`​，其余情况（请求失败、业务 code 异常、运行时抛异常）均为`False`。|
|​`[1]`|​`response_data`|​`dict`|**响应数据**：成功或业务 code 异常时返回接口响应中的`data`​对象；发生网络/代码异常时返回空字典`{}`。|

---

## 3. 提取 DWG总图 中的基点数据（get_base_point.py）

功能描述：该功能通过调用 Revit 接口（`get_dwg_text_coordinates_layer: "http://localhost:5000/api/RevitApi/GetDwgText"`）获取 DWG 图纸中的全图文字数据，对文字中的 CAD 格式控制符进行清洗，再利用正则解析、双向最近邻坐标配对以及 MAD 动态降噪算法提取基点坐标（南北/东西）、标高和旋转角度。

### 1. 外部 HTTP 接口请求规范 (Revit API)

接口说明：获取 DWG 图纸所有的文字，文字坐标，文字图层

#### 请求参数：

|**参数名**|**数据类型**|**是否必填**|**默认值**|**示例值**|**说明**|
| ------| ------| --| ---| ------| ---------------------------------------------|
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
| ------| ------| --------------------------------------------------------|
|​`Text`|​`String`|提取出的文本内容（可能包含 CAD 控制符/多行文本控制码）|
|​`Text_coordinates`|​`String`|文本节点在 DWG 中的三维坐标，格式为`"(东坐标, 北坐标, 高程/Z)"`|
|​`Text_layer`|​`String`|该文本节点所在的 CAD 图层名称|

### 2. Python 函数规范

####  函数签名

Python

```
def get_base_point_from_dwg(dwg_path: str) -> Dict[str, Any]
```

#### 输入参数 (Input Parameters)

|**参数名**|**类型**|**必填**|**说明**|
| ------| ------| --| -------------------------------------------|
|​`dwg_path`|​`str`|**是**|本地或网络共享盘中的 DWG 图纸文件绝对路径|

#### 返回值说明 (Return Value)

- ​**数据类型**​: `Dict[str, Any]`
- ​**字典字段结构**：

|**字段名**|**数据类型**|**说明**|
| ------| ------| ------------------------------------------------------------------|
|​`coordinates`|​`List[Dict[str, str]]`|**坐标列表**：包含提取出的坐标对。单组对象结构为`{"Northsouth": "X标注值", "Eastwest": "Y标注值"}`​。若未解析到则为空列表`[]`。|
|​`Elevation`|​`Optional[str]`|**绝对标高**：解析到的标高字符串（如`"73.9"`​）；若未找到或提取失败则为`None`。|
|​`Angleton`|​`Optional[str]`|**旋转角度**：解析到的角度字符串（如`"120"`​）；若未找到或提取失败则为`None`。|

#### 异常处理机制

|**触发条件**|**日志记录**|**函数返回值**|
| ---------------------------------------| ---------------------------| ----------------------------------------|
|​`GetDwgText`接口请求失败或业务 code 非 200|记录 Warning / Error 日志|返回`{}`（空字典）|
|接口成功返回但未检索到任何文字数据|记录 Warning 日志|返回`{}`（空字典）|
|文本解析或数据处理过程中抛出异常 (`Exception`)|记录 Error 日志|返回`{}`（空字典，拦截异常不阻塞主流程）|

####  返回示例

JSON

```
{
    "coordinates": [
        {
            "Northsouth": "2506045399.00",
            "Eastwest": "507491908.00"
        },
	    {
            "Northsouth": "2506045398.00",
            "Eastwest": "507491907.00"
        } 
    ],
    "Elevation": "73.9",
    "Angleton": "120.0"
}
```

‍
