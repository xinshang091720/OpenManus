"""BIM modeling workflow: CAD classification, elevation extraction, and Revit model/component generation."""

from __future__ import annotations

import asyncio
import os
import re
import shutil
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.logger import logger
from app.mcp.revit_lock import RevitProcessLock
from app.revit.client import RevitApiClient
from app.revit.operations import call_revit_operation
from app.tool_progress import set_tool_progress

# 动态引入乔轩凯提交的 CAD 分类与标高提取算法模块
_MY_CODE_DIR = Path(__file__).resolve().parents[2] / "q_agent_function_module" / "ohresult_1" / "CAD_Git_Coordinates" / "my_code"
if not _MY_CODE_DIR.exists():
    _candidates = [
        getattr(sys, "_MEIPASS", None),
        Path(sys.executable).parent / "_internal",
        Path(sys.executable).parent,
    ]
    for _base in _candidates:
        if _base:
            _cand = Path(_base) / "q_agent_function_module" / "ohresult_1" / "CAD_Git_Coordinates" / "my_code"
            if _cand.exists():
                _MY_CODE_DIR = _cand
                break
if str(_MY_CODE_DIR) not in sys.path:
    sys.path.insert(0, str(_MY_CODE_DIR))
_ELEV_DIR = _MY_CODE_DIR / "Elevation_Marking"
if str(_ELEV_DIR) not in sys.path:
    sys.path.insert(0, str(_ELEV_DIR))

try:
    from drawing_classifier import batch_classify_drawings
    from pipeline import extract_elevation_batch, extract_elevation_from_dwg
    from elevation_plan_matcher import bind_elevations_and_plans
except ImportError as error:
    logger.warning("Failed to import Elevation_Marking / drawing_classifier: %s", error)
    batch_classify_drawings = None
    extract_elevation_batch = None
    extract_elevation_from_dwg = None
    bind_elevations_and_plans = None

_PROGRESS_TOOL = "revit_bim_modeling"


def _normalise_major(raw: str | None) -> str:
    """Normalize an explicitly requested BIM discipline without guessing."""
    value = (raw or "").strip()
    aliases = {
        "建筑": "建筑", "建施": "建筑", "ARCH": "建筑", "AR": "建筑", "A": "建筑",
        "结构": "结构", "结施": "结构", "STRUCTURE": "结构", "STRUCTURAL": "结构", "ST": "结构", "S": "结构",
        "电气": "电气", "电施": "电气", "ELECTRICAL": "电气", "ELEC": "电气", "EL": "电气", "E": "电气",
        "给排水": "给排水", "水施": "给排水", "PLUMBING": "给排水", "PLUMB": "给排水", "PD": "给排水", "P": "给排水",
        "暖通": "暖通", "暖施": "暖通", "HVAC": "暖通", "MECHANICAL": "暖通", "AC": "暖通", "M": "暖通",
        "立面剖面": "立面剖面", "立面_剖面": "立面剖面", "ELEVATION_SECTION": "立面剖面",
    }
    major = aliases.get(value.upper()) if value.isascii() else aliases.get(value)
    if major is None:
        supported = "建筑、结构、电气、给排水、暖通、立面剖面"
        raise ValueError(f"无法识别专业 {raw!r}；请明确指定：{supported}")
    return major


def _find_seed_rvt() -> Optional[Path]:
    """Find a lightweight seed template to activate Revit document session."""
    bundled_seed = Path(__file__).resolve().parent / "templates" / "seed.rvt"
    if bundled_seed.exists():
        return bundled_seed

    # 兼容打包环境下的 _internal 路径
    for candidate in [
        Path(sys.executable).parent / "_internal" / "app" / "revit" / "templates" / "seed.rvt",
        Path(sys.executable).parent / "app" / "revit" / "templates" / "seed.rvt",
    ]:
        if candidate.exists():
            return candidate

    for base in [Path("C:/ProgramData/Autodesk"), Path("C:/Program Files/Autodesk"), Path("D:/Reivt2020"), Path("D:/Revit2018")]:
        if base.exists():
            for rvt in base.rglob("*.rvt"):
                if rvt.is_file() and rvt.stat().st_size > 0:
                    return rvt
    return None


class RevitBimModelingWorkflow:
    """Orchestrates CAD drawing classification, elevation derivation, and Revit component modeling."""

    def __init__(self, client: RevitApiClient | None = None) -> None:
        self.client = client or RevitApiClient()
        self.lock = RevitProcessLock()

    async def run(
        self,
        cad_folder: str,
        model_name: Optional[str] = None,
        major_name: Optional[str] = None,
        model_save_path: Optional[str] = None,
        rvt_file_path: Optional[str] = None,
        reuse_existing_model: bool = False,
        revit_version: Optional[int] = None,
        skip_components: bool = False,
        floor_overrides: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """Execute the end-to-end CAD-to-BIM modeling pipeline."""
        if reuse_existing_model and not rvt_file_path:
            raise ValueError("reuse_existing_model=true 时必须同时提供 rvt_file_path")
        folder_path = Path(cad_folder).resolve()

        # 0. 路径容错与别名纠错（如把“建筑”误写为“建施”，“结构”误写为“结施”）
        if not folder_path.exists():
            parent = folder_path.parent
            name = folder_path.name
            alias_map = {"建施": "建筑", "结施": "结构", "水施": "给排水", "暖施": "暖通", "电施": "电气"}
            if name in alias_map and (parent / alias_map[name]).exists():
                folder_path = (parent / alias_map[name]).resolve()
            elif parent.exists():
                candidates = [p for p in parent.iterdir() if p.is_dir() and (name[:2] in p.name or p.name[:2] in name)]
                if candidates:
                    folder_path = candidates[0].resolve()

        if not folder_path.exists():
            raise FileNotFoundError(f"CAD 图纸目录不存在: {cad_folder}")

        major = _normalise_major(major_name)

        # 1. 确保 Revit 进程已启动且插件已就绪（避免用户需手动打开 Revit）
        if sys.platform == "win32":
            try:
                from app.tool.windows_app import (
                    running_revit_processes,
                    discover_revit_installations,
                    _launch_revit,
                    read_revit_model_version,
                )
                plugin_check = await self.client.plugin_status(timeout_seconds=1.0)
                if plugin_check.get("status") not in {"ready", "unsupported"}:
                    installations = discover_revit_installations()
                    if not installations:
                        return {
                            "status": "failed",
                            "message": "Revit 插件未连接，且未发现可启动的 Revit 安装。请先确认 Revit 安装和本地插件服务。",
                        }
                    if installations:
                        requested_version = revit_version
                        if rvt_file_path and reuse_existing_model:
                            requested_version = read_revit_model_version(rvt_file_path)
                        if requested_version is not None:
                            target_inst = next((i for i in installations if i.version == requested_version), None)
                            if target_inst is None:
                                available = ", ".join(str(i.version) for i in installations)
                                return {
                                    "status": "failed",
                                    "message": f"指定模型需要 Revit {requested_version}，但本机可用版本为：{available}。未启动其他版本。",
                                    "rvt_file_path": rvt_file_path,
                                }
                        elif len(installations) == 1:
                            target_inst = installations[0]
                        else:
                            return {
                                "status": "selection_required",
                                "message": "检测到多个 Revit 版本；为避免用错版本，请指定 revit_version 或选择一个已有 RVT 文件。",
                                "revit_versions": [i.version for i in installations],
                            }
                        set_tool_progress(_PROGRESS_TOOL, f"正在自动启动 Autodesk Revit {target_inst.version}...")
                        logger.info("Auto-launching Revit {}: {}", target_inst.version, target_inst.executable)
                        _launch_revit(target_inst)
                        for _ in range(60):
                            await asyncio.sleep(1.0)
                            status = await self.client.plugin_status(timeout_seconds=1.0)
                            if status.get("status") in {"ready", "unsupported"}:
                                logger.info("Revit plugin is now ready on %s", status.get("endpoint"))
                                break
            except Exception as e:
                logger.exception("Revit startup/preflight failed")
                return {
                    "status": "failed",
                    "message": f"Revit 启动或插件预检查失败：{e}",
                    "rvt_file_path": rvt_file_path,
                    "revit_version": revit_version,
                }



        # 若传入的是某个专业子目录（如 清华院/结构 或 清华院/建筑），立面图通常在父目录或兄弟目录（如 清华院/立面_剖面）
        # 此时以项目总根目录优先执行分类扫描
        scan_target = folder_path
        if folder_path.is_dir() and folder_path.parent != folder_path:
            is_discipline_subdir = any(k in folder_path.name for k in ("建筑", "结构", "暖通", "电气", "给排水"))
            if is_discipline_subdir:
                scan_target = folder_path.parent
                logger.info("Detected discipline subdirectory '%s', expanding scan to project root '%s'", folder_path.name, scan_target)

        # Reusing an existing document must be explicit. An automatically
        # inferred stale path is treated as the desired new filename below.
        if rvt_file_path and reuse_existing_model:
            requested_model = Path(rvt_file_path).expanduser().resolve()
            if requested_model.suffix.lower() != ".rvt" or not requested_model.is_file():
                raise ValueError(f"指定的 Revit 模型不存在或不是 .rvt 文件：{rvt_file_path}")
            if model_save_path and Path(model_save_path).resolve() != requested_model.parent:
                raise ValueError("同时指定 rvt_file_path 和 model_save_path 时，model_save_path 必须是该 RVT 文件所在目录")
            save_dir = requested_model.parent
            model_name = requested_model.name
        elif rvt_file_path:
            requested_model = Path(rvt_file_path).expanduser().resolve()
            if requested_model.suffix.lower() != ".rvt":
                raise ValueError(f"模型文件名必须以 .rvt 结尾：{rvt_file_path}")
            if model_save_path and Path(model_save_path).resolve() != requested_model.parent:
                raise ValueError("同时指定 rvt_file_path 和 model_save_path 时，model_save_path 必须是该 RVT 文件所在目录")
            save_dir = requested_model.parent
            model_name = requested_model.name
        # 统一存放在项目的建模文件夹下（例如 清华院/BIM模型）
        elif model_save_path:
            save_dir = Path(model_save_path).resolve()
        else:
            save_dir = scan_target / "BIM模型"
        save_dir.mkdir(parents=True, exist_ok=True)

        resolved_model_name = (model_name or f"{scan_target.name}_{major}").strip()
        if not resolved_model_name.lower().endswith(".rvt"):
            resolved_model_name += ".rvt"

        clean_model_basename = resolved_model_name[:-4]
        target_model_file = save_dir / resolved_model_name

        # A new modeling request must not silently reopen or overwrite an older
        # same-named model. Keep legacy root-level models intact and allocate a
        # fresh filename; an explicit rvt_file_path above remains the opt-in
        # path for continuing an existing document.
        if not reuse_existing_model:
            model_stem = clean_model_basename
            suffix = 2
            while (
                (save_dir / resolved_model_name).exists()
                or (scan_target / resolved_model_name).exists()
            ):
                resolved_model_name = f"{model_stem}_{suffix}.rvt"
                target_model_file = save_dir / resolved_model_name
                clean_model_basename = model_stem + f"_{suffix}"
                suffix += 1
            if clean_model_basename != model_stem:
                logger.info(
                    "Requested model filename already exists; creating a new model at {}",
                    str(target_model_file),
                )

        async with self.lock.hold():
            # 2. 扫描与分类 CAD 图纸
            set_tool_progress(_PROGRESS_TOOL, "正在扫描与分类 CAD 图纸", cad_folder=str(scan_target))
            logger.info("Scanning and classifying CAD drawings in %s", str(scan_target))

            classified = {}
            if batch_classify_drawings is not None:
                classified = await asyncio.to_thread(batch_classify_drawings, str(scan_target))

            elevation_dwgs = list(classified.get("ELEVATION_SECTION") or [])
            # 兜底：如果分类器未直接列出，则从本目录或递归子目录中匹配含“立面”或“剖面”的 DWG
            if not elevation_dwgs and scan_target.is_dir():
                for dwg in scan_target.rglob("*.dwg"):
                    if any(kw in dwg.name for kw in ("立面", "剖面")):
                        elevation_dwgs.append(str(dwg))

            # 兜底：若传入单张 DWG
            if not elevation_dwgs and folder_path.is_file() and folder_path.suffix.lower() == ".dwg":
                elevation_dwgs.append(str(folder_path))

            if not elevation_dwgs:
                return {
                    "status": "failed",
                    "message": f"未在目录 '{scan_target}' 及其子目录中识别到立面/剖面 DWG 图纸，无法提取标高系统。请确保传入的路径包含立剖面图纸（或整个项目总文件夹）。",
                    "cad_folder": str(scan_target),
                }

            logger.info("Found %d elevation drawings: %s", len(elevation_dwgs), elevation_dwgs)

            # 3. 准备项目模型文档：不同专业建立独立的 RVT 模型，禁止跨专业复用同一个模型
            is_target_model_active = False
            try:
                bp_check = await self.client.base_point_setting_is_correct()
                if bp_check.get("code") == 200:
                    data_obj = bp_check.get("data") or {}
                    active_title = str(data_obj.get("DocumentTitle") or data_obj.get("Title") or "")
                    active_path = str(data_obj.get("DocumentPath") or "")
                    target_path = str(target_model_file.resolve()).casefold()
                    active_path_normalized = str(Path(active_path).resolve()).casefold() if active_path else ""
                    same_document = active_path_normalized == target_path if active_path_normalized else (
                        Path(active_title).name.casefold() == target_model_file.name.casefold()
                    )
                    if same_document:
                        is_target_model_active = True
            except Exception:
                is_target_model_active = False

            if is_target_model_active:
                logger.info("Revit already has active document matching target '%s', continuing directly", clean_model_basename)
                set_tool_progress(_PROGRESS_TOOL, f"检测到 Revit 中当前已打开 {target_model_file.name}，直接在此模型下继续翻模建模")
            elif target_model_file.exists():
                set_tool_progress(_PROGRESS_TOOL, f"正在打开已有 {major} 专业模型: {target_model_file.name}")
                try:
                    create_model_res = await call_revit_operation(
                        self.client,
                        "OpenRevitFile",
                        self.client.open_revit_file,
                        str(target_model_file),
                        timeout_seconds=60.0,
                    )
                    logger.info("Revit OpenRevitFile (existing model) response: {}", create_model_res)
                except Exception as e:
                    logger.warning("OpenRevitFile encountered error or timeout (%s), proceeding to check session: %s", target_model_file, e)
            else:
                set_tool_progress(_PROGRESS_TOOL, f"正在新建独立的 {major} 专业 RVT 模型: {target_model_file.name}")
                try:
                    create_model_res = await call_revit_operation(
                        self.client,
                        "CreateModel",
                        self.client.create_model,
                        clean_model_basename,
                        major,
                        str(save_dir),
                    )
                except Exception as e:
                    # 若 Revit 当前处于未打开文档状态，C# 插件会拦截非 OpenRevitFile 接口并返回 400 未打开Revit文档
                    # 此时通过无前置文档限制的 OpenRevitFile 先激活种子文档，再重试 CreateModel
                    if "未打开" in str(e) or "Revit文档" in str(e):
                        seed_rvt = _find_seed_rvt()
                        if seed_rvt:
                            logger.info("Revit has no active document, activating seed file first: %s", seed_rvt)
                            set_tool_progress(_PROGRESS_TOOL, "正在初始化 Revit 文档环境...")
                            await call_revit_operation(
                                self.client,
                                "OpenRevitFile",
                                self.client.open_revit_file,
                                str(seed_rvt),
                                timeout_seconds=60.0,
                            )
                            create_model_res = await call_revit_operation(
                                self.client,
                                "CreateModel",
                                self.client.create_model,
                                clean_model_basename,
                                major,
                                str(save_dir),
                            )
                        else:
                            raise
                    elif "Saving is not allowed" in str(e) or "File has been opened" in str(e):
                        logger.info("Target model is already open in Revit: %s", e)
                        create_model_res = {"code": 200, "msg": "模型已在 Revit 中打开"}
                    else:
                        raise
                logger.info("Revit CreateModel response: {}", create_model_res)

            # 4. 提取立剖面图全局标高系统（此时模型已激活，GetDwgText 不会报 400 缺少文档错误）
            set_tool_progress(
                _PROGRESS_TOOL,
                f"正在从立剖面图提取标高系统（共 {len(elevation_dwgs)} 张图纸）",
            )
            if extract_elevation_batch is not None:
                elevation_result = await asyncio.to_thread(extract_elevation_batch, elevation_dwgs)
            else:
                elevation_result = {"data": []}

            elev_data = elevation_result.get("data") or []
            if not elev_data:
                return {
                    "status": "failed",
                    "message": "未能从立面图纸中提取到有效标高数据",
                    "elevation_drawings": elevation_dwgs,
                }

            logger.info("Extracted %d elevation levels", len(elev_data))

            # 5. 标高与目标专业平面图对齐绑定
            set_tool_progress(_PROGRESS_TOOL, f"正在将标高与 {major} 平面图对齐绑定")
            leve_datas = []
            if bind_elevations_and_plans is not None:
                aligned_result = await asyncio.to_thread(
                    bind_elevations_and_plans,
                    elevation_result,
                    classified,
                    marjorName=major,
                    floor_overrides=floor_overrides,
                )
                if isinstance(aligned_result, dict):
                    leve_datas = aligned_result.get("leveDatas", aligned_result.get("data", []))
                elif isinstance(aligned_result, list):
                    leve_datas = aligned_result
                else:
                    leve_datas = []

            # 若未匹配到，以提取的标高为底装配基础数据
            if not leve_datas:
                classified_majors = {
                    str(key): len(value) for key, value in classified.items()
                    if isinstance(value, (list, tuple))
                }
                return {
                    "status": "failed",
                    "message": f"所选专业“{major}”没有匹配到可绑定楼层的平面图；已停止，未调用 CreateLeveView 或构件生成接口。",
                    "major": major,
                    "classified_drawing_counts": classified_majors,
                    "elevation_drawings": elevation_dwgs,
                    "levels_detected": len(elev_data),
                }

            # 5. 单图纸多楼层复用：每层索引直接指向原始 DWG 路径，无需在磁盘上复制副本（杜绝图纸无限繁殖裂变）
            for item in leve_datas:
                dwgs = item.get("dwgsFilePaths") or []
                item["dwgsFilePaths"] = list(dict.fromkeys(dwgs))

            # 5. 在 Revit 中生成标高与对应视图
            set_tool_progress(_PROGRESS_TOOL, f"正在 Revit 中生成 {len(leve_datas)} 个标高与对应视图")
            logger.info(
                "CreateLeveView request: major={}, floors={}, drawings_per_floor={}",
                major,
                [item.get("floor") for item in leve_datas],
                {item.get("floor"): len(item.get("dwgsFilePaths") or []) for item in leve_datas},
            )
            create_level_res = await call_revit_operation(
                self.client,
                "CreateLeveView",
                self.client.create_level_view,
                leve_datas,
                major,
            )
            logger.info("Revit CreateLeveView response: {}", create_level_res)

            # 6. 批量翻模构件
            comp_res = {}
            component_generation_majors = {"建筑", "结构", "电气", "给排水", "暖通"}
            if not skip_components and major in component_generation_majors:
                set_tool_progress(_PROGRESS_TOOL, f"正在根据图纸在 Revit 中批量翻模生成 {major} 专业构件")
                comp_res = await call_revit_operation(
                    self.client,
                    "CraeteComponentApi",
                    self.client.create_components,
                    major,
                )
                logger.info("Revit CraeteComponentApi response: {}", comp_res)
            elif not skip_components:
                comp_res = {
                    "msg": f"未调用通用构件生成接口：当前接口会从图层名称推断轴网、墙、柱、门窗等通用构件，尚无 {major} 专业构件过滤与生成规则。"
                }

            target_model_file = save_dir / resolved_model_name
            components_generated = not skip_components and major in component_generation_majors
            if components_generated:
                completion_message = f"成功完成 {major} 专业 BIM 模型创建与构件生成"
                completion_status = "success"
                component_summary = comp_res.get("msg", "构件生成接口已完成")
            elif skip_components:
                completion_message = f"已完成 {major} 专业楼层视图与图纸绑定；构件生成按请求跳过"
                completion_status = "partial"
                component_summary = "按请求跳过构件生成"
            else:
                completion_message = f"已完成 {major} 专业楼层视图与图纸绑定；当前插件尚未支持该专业的构件生成"
                completion_status = "partial"
                component_summary = comp_res.get("msg", "构件生成未执行")
            set_tool_progress(_PROGRESS_TOOL, completion_message, status="completed")

            return {
                "status": completion_status,
                "message": completion_message,
                "model_path": str(target_model_file) if target_model_file.exists() else str(save_dir / (clean_model_basename + ".rvt")),
                "major": major,
                "levels_count": len(leve_datas),
                "levels": [
                    {"floor": d.get("floor"), "elevation": d.get("elevation")}
                    for d in leve_datas
                ],
                "floor_drawings": [
                    {"floor": d.get("floor"), "dwgsFilePaths": d.get("dwgsFilePaths") or []}
                    for d in leve_datas
                ],
                "create_level_summary": create_level_res.get("msg", "标高与视图已创建"),
                "component_summary": component_summary,
                "elevation_drawings": elevation_dwgs,
            }
