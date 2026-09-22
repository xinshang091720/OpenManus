# -*- coding: utf-8 -*-
"""
elevation_pipeline_run.py
全流程调度：图纸分类 -> 标高提取 -> 平面图对齐 -> Revit模型与标高创建
"""
import os
import sys
import json
import yaml
import requests
from typing import Any, Dict, List

cur_dir = os.path.dirname(os.path.abspath(__file__))
my_code_dir = os.path.dirname(cur_dir)
for p in [my_code_dir, os.path.dirname(my_code_dir)]:
    if p and p not in sys.path:
        sys.path.insert(0, p)

try:
    from .pipeline import extract_elevation_batch
    from .elevation_plan_matcher import bind_elevations_and_plans
except (ImportError, ValueError):
    from pipeline import extract_elevation_batch
    from elevation_plan_matcher import bind_elevations_and_plans

from drawing_classifier import batch_classify_drawings

try:
    from ohresult.logger_config import setup_logger
    logger = setup_logger()
except Exception:
    import logging
    logger = logging.getLogger("elevation_pipeline")

_config_path = os.path.join(my_code_dir, "config.yaml")
with open(_config_path, "r", encoding="utf-8") as f:
    _config = yaml.safe_load(f)

CREATE_MODEL_URL = _config.get("revit", {}).get("create_model", "http://localhost:5000/api/RevitApi/CreateModel")
CREATE_LEVEL_VIEW_URL = _config.get("revit", {}).get("create_level_view", "http://localhost:5000/api/RevitApi/CreateLeveView")


def create_revit_model(modelName: str, marjorName: str, modelSavePath: str) -> dict:
    payload = {
        "modelName": modelName,
        "marjorName": marjorName,
        "modelSavePath": os.path.abspath(modelSavePath)
    }
    logger.info(f"调用 Revit 创建模型接口: {modelName}, 专业: {marjorName}")
    try:
        resp = requests.post(CREATE_MODEL_URL, json=payload, timeout=None)
        resp.raise_for_status()
        res_json = resp.json()
        logger.info("创建模型返回:\n" + json.dumps(res_json, ensure_ascii=False, indent=2))
        return res_json
    except Exception as e:
        logger.error(f"调用 Revit 创建模型接口失败: {e}")
        return {"code": 500, "msg": f"调用创建模型接口失败: {e}"}


def create_revit_level_view(leveDatas: List[Dict[str, Any]], marjorName: str) -> dict:
    payload = {
        "leveDatas": leveDatas,
        "marjorName": marjorName
    }
    logger.info(f"调用 Revit 创建标高视图接口: {len(leveDatas)} 个标高, 专业: {marjorName}")
    logger.info("传入创建标高接口的内容(对齐标高结果):\n" + json.dumps(payload, ensure_ascii=False, indent=2))
    try:
        resp = requests.post(CREATE_LEVEL_VIEW_URL, json=payload, timeout=None)
        resp.raise_for_status()
        res_json = resp.json()
        logger.info("创建标高视图返回:\n" + json.dumps(res_json, ensure_ascii=False, indent=2))
        return res_json
    except Exception as e:
        logger.error(f"调用 Revit 创建标高接口失败: {e}")
        return {"code": 500, "msg": f"调用创建标高接口失败: {e}"}


def run_pipeline(
    folder_path: str,
    marjorName: str = "结构",
    modelName: str = "测试",
    modelSavePath: str = r"C:\Users\wang1\Desktop\手册"
) -> dict:
    os.makedirs(modelSavePath, exist_ok=True)
    logger.info(f"启动全流程: {folder_path} (专业: {marjorName}, 模型: {modelName})")

    # 1. 创建 RVT 模型
    create_model_res = create_revit_model(
        modelName=modelName,
        marjorName=marjorName,
        modelSavePath=modelSavePath
    )
    if create_model_res.get("code") not in (200, "200", None):
        return {
            "msg": f"创建 RVT 文件失败: {create_model_res.get('msg')}",
            "create_model_result": create_model_res,
            "leveDatas": []
        }

    # 2. 扫描分类与提取标高
    classified = batch_classify_drawings(folder_path)
    elevation_dwgs = classified.get("ELEVATION_SECTION", [])
    if not elevation_dwgs:
        logger.warning("未识别到立剖面图纸")
        return {
            "msg": "未识别到立剖面图纸，无法提取标高系统",
            "create_model_result": create_model_res,
            "leveDatas": []
        }

    elevation_result = extract_elevation_batch(elevation_dwgs)
    if elevation_result.get("msg") != "success":
        logger.warning(f"标高提取异常: {elevation_result.get('msg')}")
        return {
            "msg": elevation_result.get("msg"),
            "create_model_result": create_model_res,
            "leveDatas": elevation_result.get("data", [])
        }

    # 3. 标高与目标专业平面图对齐
    aligned_result = bind_elevations_and_plans(
        elevation_result,
        classified,
        marjorName=marjorName
    )
    leveDatas = aligned_result.get("leveDatas", aligned_result.get("data", []))

    if aligned_result.get("msg") != "success":
        logger.warning(f"对齐图纸触发质检异常，流程终止: {aligned_result.get('msg')}")
        return {
            "msg": aligned_result.get("msg"),
            "create_model_result": create_model_res,
            "leveDatas": leveDatas
        }

    # 4. 创建标高与视图
    create_level_res = create_revit_level_view(
        leveDatas=leveDatas,
        marjorName=marjorName
    )

    return {
        "msg": aligned_result.get("msg", "success"),
        "create_model_result": create_model_res,
        "create_level_view_result": create_level_res,
        "leveDatas": leveDatas
    }


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    folder_path = r"D:\project\ai_agent\data\标高全专业测试数据\清华院"
    marjorName = "结构"
    modelName = "结构测试"
    modelSavePath = r"D:\project\ai_agent\data\标高全专业测试数据"

    res = run_pipeline(
        folder_path=folder_path,
        marjorName=marjorName,
        modelName=modelName,
        modelSavePath=modelSavePath
    )
    print(json.dumps(res, ensure_ascii=False, indent=2))
