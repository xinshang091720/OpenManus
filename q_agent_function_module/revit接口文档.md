# revit接口文档

# 获取revit和cad同一轴网中心点坐标，用于计算平移量



```
curl --location --request POST 'http://localhost:5000/api/RevitApi/BatchCreateRooms  ' \
--header 'content-type: application/json' \
--data ''
```

```
{
    "code": 200,
    "msg": "B3(AR-12.800)楼层共生成0个房间\nB2(AR-9.200)楼层共生成0个房间\nB1(AR-5.600)楼层共生成0个房间\n1F(0.00)楼层共生成0个房间\n"
}
```



curl -L -X POST "http://localhost:5000//api/RevitApi/DwgRevitGridData" \

-H "Content-Type: application/json" \

-d "{

    \"dwgFilePath\":\"D:\\project\\ai_agent\\data\\room_coordinates_test\\地下室\\dwg\\地下室顶板平面图.dwg\"

}"  
返回体：

{

    "code": 200,
    
    "msg": "返回dwg轴网跟rvt轴网数据",
    
    "rvtGrid": {
    
        "AxisCode": "D2-A",
    
        "Begin_Position": [
    
            "-193761.322158341",
    
            "-27126.3684405588",
    
            "0"
    
        ],
    
        "End_Position": [
    
            "-211641.075592701",
    
            "-27126.3684405588",
    
            "0"
    
        ]
    
    },
    
    "dwgGrid": {
    
        "AxisCode": "D2-A",
    
        "Begin_Position": [
    
            "492729391.144911",
    
            "2519711460.51568",
    
            "0.0"
    
        ],
    
        "End_Position": [
    
            "492741798.26936",
    
            "2519711460.51568",
    
            "0.0"
    
        ]
    
    }

}

# 基点修改

curl -L -X POST "http://localhost:5000//api/RevitApi/BasePointSetting " \

-H "Content-Type: application/json" \

-d "{

  \"coordinates\": [

{

      \"Northsouth\": \"886340.5034867161\",
    
      \"Eastwest\": \"-170810.0484587667\"
    
    },
    
    {
    
      \"Northsouth\": \"754674.2162259516\",
    
      \"Eastwest\": \"-176570.6977297838\"
    
    },
    
    {
    
      \"Northsouth\": \"801424.2162259526\",
    
      \"Eastwest\": \"-177670.6977298023\"
    
    }

  ],

  \"Elevation\": 600.0,

  \"Angleton\": 20

}"  
返回体  


{

    "code": 200,
    
    "msg": "修改完成"

}

# revit房间值修改

curl -L -X POST "http://localhost:5000//api/RevitApi/UpdateRoomName " \

-H "Content-Type: application/json" \

-d "{

  \"RoomData\": [

    {
    
      \"floor_num\": -3,
    
      \"room_texts\": [
    
        {
    
          \"RoomName\": \"地下三层\",
    
          \"XYZ\": \"(-166084.00495165586, -29741.40102148056, -0.0121136200671661)\"
    
        },
    
        {
    
          \"RoomName\": \"战时进风机房\",
    
          \"XYZ\": \"(-108952.07803183794, -68984.52743148804, -0.012113620067166)\"
    
        },
    
        {
    
          \"RoomName\": \"平时排风机房\",
    
          \"XYZ\": \"(-109414.16250896454, -69326.98576593399, -0.012113620067166)\"
    
        }]

  },

    {
    
      \"floor_num\": -2,
    
      \"room_texts\": [
    
        {
    
          \"RoomName\": \"地下二层\",
    
          \"XYZ\": \"(-166084.00495165586, -29741.40102148056, -0.0121136200671661)\"
    
        },
    
        {
    
          \"RoomName\": \"战时进风机房\",
    
          \"XYZ\": \"(-108952.07803183794, -68984.52743148804, -0.012113620067166)\"
    
        },
    
        {
    
          \"RoomName\": \"平时排风机房\",
    
          \"XYZ\": \"(-109414.16250896454, -69326.98576593399, -0.012113620067166)\"
    
        }]

  },

    {
    
      \"floor_num\": -1,
    
      \"room_texts\": [
    
        {
    
          \"RoomName\": \"地下一层\",
    
          \"XYZ\": \"(-166084.00495165586, -29741.40102148056, -0.0121136200671661)\"
    
        },
    
        {
    
          \"RoomName\": \"战时进风机房\",
    
          \"XYZ\": \"(-108952.07803183794, -68984.52743148804, -0.012113620067166)\"
    
        },
    
        {
    
          \"RoomName\": \"平时排风机房\",
    
          \"XYZ\": \"(-109414.16250896454, -69326.98576593399, -0.012113620067166)\"
    
        }]

  }

 ]

}  
返回体：  


{

    "code": 200,
    
    "msg": "已全部修改完成 "

}

# 生成ifc文件

curl -L -X POST "http://localhost:5000//api/RevitApi/ExportIFC" \

-H "Content-Type: application/json" \

-d "{\"IfcFilePath\":\"D:\\project\\ai_agent\\data\\room_coordinates_test\\dwg_z\\建筑_地下室\\16-地下室图纸_t3.dwg\"}"  
返回体：  


{

    "code": 200,
    
    "msg": "导出成功 "

}
