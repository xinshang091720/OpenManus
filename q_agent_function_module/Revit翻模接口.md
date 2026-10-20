# Revit翻模接口

## 1.通用说明

### 请求头

| Header | 说明 |
| --- | --- |
| Content-Type | application/json（POST 请求必填） |

### 统一响应结构

所有接口采用统一包装：

json { "code": 200, "msg": "success", "data": null }

| **字段** | **类型** | **说明** |
| --- | --- | --- |
| code | int | 状态编码 |
| msg | string | 提示文本 |

### 状态码约定

| **code** | **说明** |
| --- | --- |
| 200 | 成功 |
| 400 | 未打开Revit文档 |
| 405 | 接口不存在 |
| 500 | Revit操作异常 |

## 2.创建rvt文件

**接口说明**：根据用户传入的模型名称与专业和保存目录新建revit模型文件。

### 2.1请求示例：

```plaintext
curl --request POST \
  --url http://localhost:5000/api/RevitApi/CreateModel \
  --header 'Accept: */*' \
  --header 'Accept-Encoding: gzip, deflate, br' \
  --header 'Connection: keep-alive' \
  --header 'Content-Type: application/json' \
  --header 'User-Agent: PostmanRuntime-ApipostRuntime/1.1.0' \
  --data '{ 
   "modelName":"测试",
   "marjorName":"结构",
   "modelSavePath":"C:\Users\wang1\Desktop\手册"
}'
```

### 2.2请求参数：

| **参数名** | **类型** | **必填** | **说明** |
| --- | --- | --- | --- |
| modelName | string | 是 | 模型名称 |
| marjorName | string | 是 | 专业名称 |
| modelSavePath | string | 是 | 保存目录 |

### 2.3返回示例：

```plaintext
{
	"code": 200,
	"msg": "创建成功"
}
```

## 3.创建标高

**接口说明**：AI从立面图读取楼层信息进行创建对应的标高

### 请求示例：

```plaintext
curl --request POST \
    --url http://localhost:5000/api/RevitApi/CreateLeveView \
  --header 'Accept: */*' \
  --header 'Accept-Encoding: gzip, deflate, br' \
  --header 'Connection: keep-alive' \
  --header 'Content-Type: application/json' \
  --header 'User-Agent: PostmanRuntime-ApipostRuntime/1.1.0' \
  --data '{
 "leveDatas":
 [
     {
        "floor":"1F",   //标高名称
         "elevation":0,  //标高高度
         "dwgsFilePaths": [
        "C:/Project/Drawings/1F.dwg",
        "C:/Project/Drawings/1F_ref.dwg"  //每层对应的图纸集合
      ]
     },
     {
        "floor":"2F",
         "elevation":3.5,
         "dwgsFilePaths": [
        "C:/Project/Drawings/1F.dwg",
        "C:/Project/Drawings/1F_ref.dwg"
      ]
     },
     {
        "floor":"3F",
         "elevation":6.8,
         "dwgsFilePaths": [
        "C:/Project/Drawings/1F.dwg",
        "C:/Project/Drawings/1F_ref.dwg"
      ]
      
     },
     {
        "floor":"4F",
         "elevation":10,
         "dwgsFilePaths": [
        "C:/Project/Drawings/1F.dwg",
        "C:/Project/Drawings/1F_ref.dwg"
      ]
     }
 ],
 "marjorName":"结构"
}'
```

### 请求参数：

| **参数名** | **类型** | **必填** | **说明** |
| --- | --- | --- | --- |
| leveDatas | 集合 | 是 | 标高信息集合 |
| marjorName | string | 是 | 专业名称 |

### 返回示例：

```plaintext
{
	"code": 200,
	"msg": "创建完成！"
}
```

## 4.批量创建构件

**接口说明**：从本地读取json数据，根据指定专业进行各层图纸对应构件的批量生成

### 请求参数：

| 字段名 | 类型 | 说明 | 示例 |
| :--- | :--- | :--- | :--- |
| `marjorName` | string | 专业名称（建筑、结构、电气、给排水、暖通） | `"结构"` |

### 请求示例：

```bash
curl --request POST \
  --url http://localhost:5000/api/RevitApi/CraeteComponentApi \
  --header 'Content-Type: application/json' \
  --header 'Accept: */*' \
  --data '{
    "marjorName": "结构"
  }'
```

### 返回示例：

```plaintext
{
	"code": 200,
	"msg": "1F楼层共生成180个轴网，1F楼层共生成0个墙，门窗：转化失败,当前标高没有墙，无法转化门窗!，1F楼层共生成0个房间，1F楼层共生成0个承台，1F楼层共生成43个柱，"
}
```