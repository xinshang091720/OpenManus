"""AR room creation and DWG room-name synchronisation as one business operation."""

from __future__ import annotations

import asyncio
import json
import math
import os
import re
import time
import uuid
from pathlib import Path
from typing import Any

from app.logger import logger
from app.mcp.revit_lock import RevitProcessLock
from app.revit.client import RevitApiClient
from app.revit.operations import call_revit_operation
from app.revit.project_delivery import prepare_save_folder
from app.revit.save_result import resolve_fresh_saved_model_path, snapshot_folder_files
from app.revit.room_sync import (
    _extract_rooms_and_floor_from_texts,
    _floor_from_path,
    _points,
    close_autocad_document_if_open,
)
from app.runtime_paths import runtime_paths
from app.tool_progress import set_tool_progress


_PROGRESS_TOOL = "revit_create_and_name_ar_rooms"
PURE_CHINESE_PATTERN = re.compile(r"^[\u4e00-\u9fa5]+$")


class ArRoomCreationWorkflow:
    """Create closed-area rooms, then name them from every project DWG in one folder."""

    def __init__(self, client: RevitApiClient | None = None) -> None:
        self.client = client or RevitApiClient()
        self.lock = RevitProcessLock()

    @staticmethod
    def _all_drawings(folder: Path) -> list[Path]:
        """Return every drawing supplied with the project, without name filtering."""
        return sorted(
            (
                path
                for path in folder.iterdir()
                if path.is_file() and path.suffix.casefold() == ".dwg"
            ),
            key=lambda item: item.name.casefold(),
        )

    @staticmethod
    def _room_data_sort_key(item: dict[str, Any]) -> tuple[bool, float]:
        """Keep the legacy unresolved/zero level at the end of the batch."""
        try:
            value = float(item.get("floor_num", 0))
        except (TypeError, ValueError):
            value = 0.0
        return value == 0.0, value

    @staticmethod
    def _write_audit(payload: dict[str, Any]) -> str:
        folder = runtime_paths.workspace_dir / "revit-room-runs"
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{uuid.uuid4().hex}.json"
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return str(path)

    @staticmethod
    def _path_key(path: str | Path) -> str:
        """Create a case-insensitive absolute key for one Windows file path."""
        return os.path.normcase(os.path.abspath(os.fspath(path)))

    @classmethod
    def _normalise_floor_overrides(
        cls,
        floor_overrides: list[dict[str, Any]] | None,
        drawings: list[Path],
    ) -> dict[str, list[int | float]]:
        """Validate explicit user decisions without guessing from file names."""
        if floor_overrides is None:
            return {}
        if not isinstance(floor_overrides, list):
            raise ValueError("floor_overrides must be a list of DWG floor mappings")

        available = {cls._path_key(path): path for path in drawings}
        overrides: dict[str, list[int | float]] = {}
        for item in floor_overrides:
            if not isinstance(item, dict):
                raise ValueError("Each floor_overrides item must be an object")
            raw_path = item.get("dwg_path")
            raw_numbers = item.get("floor_numbers")
            if not isinstance(raw_path, str) or not raw_path.strip():
                raise ValueError("floor_overrides.dwg_path must be an absolute DWG path")
            candidate_path = Path(raw_path)
            if not candidate_path.is_absolute():
                raise ValueError("floor_overrides.dwg_path must be an absolute DWG path")
            key = cls._path_key(candidate_path)
            if key not in available:
                raise ValueError("floor_overrides.dwg_path is not a DWG in dwg_folder_path")
            if key in overrides:
                raise ValueError("Duplicate floor_overrides mapping for the same DWG")
            if not isinstance(raw_numbers, list) or not raw_numbers:
                raise ValueError("floor_overrides.floor_numbers must be a non-empty list")

            numbers: list[int | float] = []
            for raw_number in raw_numbers:
                if isinstance(raw_number, bool):
                    raise ValueError("floor_overrides.floor_numbers must contain numbers")
                try:
                    numeric = float(raw_number)
                except (TypeError, ValueError) as error:
                    raise ValueError("floor_overrides.floor_numbers must contain numbers") from error
                if not math.isfinite(numeric):
                    raise ValueError("floor_overrides.floor_numbers must contain finite numbers")
                number: int | float = int(numeric) if numeric.is_integer() else numeric
                if number not in numbers:
                    numbers.append(number)
            overrides[key] = numbers
        return overrides

    @staticmethod
    def _assert_sources_unchanged(source_stats: dict[str, tuple[int, int]]) -> None:
        """Guard the invariant that supplied DWGs are always read-only inputs."""
        for path_text, original in source_stats.items():
            current = Path(path_text).stat()
            if (current.st_size, current.st_mtime_ns) != original:
                raise RuntimeError(f"Source DWG was unexpectedly modified: {path_text}")

    async def run(
        self,
        rvt_file_path: str,
        dwg_folder_path: str,
        discipline: str,
        allow_length_mismatch: bool = True,
        save_folder_path: str | None = None,
        floor_overrides: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        model = Path(rvt_file_path)
        folder = Path(dwg_folder_path)
        if model.suffix.lower() != ".rvt" or not model.is_file():
            raise ValueError("rvt_file_path 必须是存在的 .rvt 文件")
        if not folder.is_dir():
            raise ValueError("dwg_folder_path 必须是存在的文件夹")
        if discipline.strip().upper() not in {"AR", "建筑"}:
            return {
                "status": "not_applicable",
                "reason": "房间创建与命名仅适用于用户确认的建筑（AR）模型",
                "model_path": str(model),
            }

        drawings = self._all_drawings(folder)
        if not drawings:
            return {
                "status": "skipped",
                "reason": "文件夹内没有 DWG 图纸",
                "model_path": str(model),
                "skipped_drawing_count": 0,
            }
        source_stats = {
            str(path): (path.stat().st_size, path.stat().st_mtime_ns)
            for path in drawings
        }
        confirmed_floor_overrides = self._normalise_floor_overrides(
            floor_overrides,
            drawings,
        )
        started = time.monotonic()
        room_data: list[dict[str, Any]] = []
        per_floor_counts: dict[str, int] = {}
        drawing_floor_detection: list[dict[str, Any]] = []
        unresolved_drawings: list[dict[str, Any]] = []

        async with self.lock.hold():
            set_tool_progress(_PROGRESS_TOOL, "正在打开并确认目标 Revit 模型")
            await call_revit_operation(
                self.client, "OpenRevitFile", self.client.open_revit_file, str(model)
            )
            from q_agent_function_module.ohresult.CAD_Git_Coordinates.my_code.room_coordinates.coordinate_transformation import (
                calculate_segment_translation_vector,
                transform_room_texts,
                validate_translation_alignment,
            )

            total_drawings = len(drawings)
            skipped_drawings: list[dict[str, Any]] = []
            for index, dwg_path in enumerate(drawings, start=1):
                set_tool_progress(
                    _PROGRESS_TOOL,
                    f"正在读取图纸 [{index}/{total_drawings}] {dwg_path.name} 轴网数据",
                    drawing_index=index,
                    drawing_count=total_drawings,
                )
                grid = await call_revit_operation(
                    self.client, "DwgRevitGridData", self.client.dwg_revit_grid_data, str(dwg_path)
                )
                rvt_grid, dwg_grid = grid.get("rvtGrid", {}), grid.get("dwgGrid", {})
                try:
                    rvt_line, dwg_line = _points(rvt_grid), _points(dwg_grid)
                    alignment = validate_translation_alignment(
                        rvt_line, dwg_line, allow_length_mismatch=allow_length_mismatch
                    )
                    if not alignment["valid"]:
                        reason = alignment.get("reason", "轴网未对齐")
                        logger.warning(f"跳过图纸 {dwg_path.name}：{reason}")
                        skipped_drawings.append({"path": str(dwg_path), "reason": reason})
                        continue
                except (ValueError, TypeError) as e:
                    logger.warning(f"跳过无有效楼层轴网的图纸 {dwg_path.name}：{e}")
                    skipped_drawings.append({"path": str(dwg_path), "reason": f"无有效楼层轴网: {e}"})
                    continue

                set_tool_progress(
                    _PROGRESS_TOOL,
                    f"正在提取图纸 [{index}/{total_drawings}] {dwg_path.name} 房间文字与标高",
                    drawing_index=index,
                    drawing_count=total_drawings,
                )
                text_res = await call_revit_operation(
                    self.client, "GetDwgText", self.client.get_dwg_text, str(dwg_path)
                )
                text_items = text_res.get("data", []) if isinstance(text_res, dict) else []

                floor_hint = _floor_from_path(str(dwg_path))
                extracted, floor_entries, floor_detection = await asyncio.to_thread(
                    _extract_rooms_and_floor_from_texts,
                    text_items,
                    dwg_path.name,
                    fallback_floor=floor_hint,
                )
                # Keep compatibility with older helper implementations that
                # returned a source-only detection object together with an
                # already resolved floor list.
                if floor_entries and floor_detection.get("status") is None:
                    floor_detection = {
                        **floor_detection,
                        "status": "resolved",
                        "confidence": floor_detection.get("confidence", "legacy"),
                    }
                override_numbers = confirmed_floor_overrides.get(self._path_key(dwg_path))
                if override_numbers is not None:
                    floor_entries = override_numbers
                    floor_detection = {
                        "status": "resolved",
                        "source": "user_override",
                        "confidence": "confirmed",
                        "matched_text": "",
                        "floor_entries": floor_entries,
                        "filename_hint": floor_hint,
                        "candidates": [],
                    }
                if floor_detection.get("status") != "resolved" or not floor_entries:
                    drawing_floor_detection.append(
                        {
                            "path": str(dwg_path),
                            "floor_entries": floor_entries,
                            "detection": floor_detection,
                        }
                    )
                    unresolved_drawings.append(
                        {
                            "dwg_path": str(dwg_path),
                            "floor_candidates": list(floor_detection.get("candidates") or []),
                        }
                    )
                    continue
                set_tool_progress(_PROGRESS_TOOL, "正在读取并转换房间文字")
                transformed = transform_room_texts(
                    extracted, calculate_segment_translation_vector(rvt_line, dwg_line)
                )
                rooms = [
                    item
                    for item in transformed["room_texts"]
                    if item.get("RoomName") and PURE_CHINESE_PATTERN.match(item["RoomName"].strip())
                ]
                for floor in floor_entries:
                    room_data.append({"floor_num": floor, "room_texts": rooms})
                    per_floor_counts[str(floor)] = per_floor_counts.get(str(floor), 0) + len(rooms)
                drawing_floor_detection.append(
                    {
                        "path": str(dwg_path),
                        "floor_entries": floor_entries,
                        "detection": floor_detection,
                    }
                )

            # If all drawings were skipped or produced no room data
            if not room_data and not unresolved_drawings:
                self._assert_sources_unchanged(source_stats)
                return {
                    "status": "skipped",
                    "reason": "所提供的 DWG 图纸均未检测到与当前模型匹配的有效楼层轴网或房间信息",
                    "model_path": str(model),
                    "dwg_folder_path": str(folder),
                    "processed_drawing_count": total_drawings,
                    "skipped_drawing_count": len(skipped_drawings),
                    "skipped_drawings": skipped_drawings,
                    "duration_seconds": round(time.monotonic() - started, 1),
                }

            # A floor decision is required before the first Revit write.  We
            # preflight every submitted DWG so the user receives one complete
            # question instead of an edit/retry loop.
            if unresolved_drawings:
                self._assert_sources_unchanged(source_stats)
                return {
                    "status": "selection_required",
                    "message": (
                        "以下 DWG 图纸中检测到多个不同的楼层标记，无法自动确认对应楼层；尚未创建、命名或另存任何 Revit 房间。"
                        "请确认对应楼层后继续（例如回复“第1个是5层”或“该图为 6 至 30 层”）："
                    ),
                    "model_path": str(model),
                    "dwg_folder_path": str(folder),
                    "processed_drawing_count": total_drawings,
                    "candidates": unresolved_drawings,
                    "drawing_floor_detection": drawing_floor_detection,
                    "skipped_drawing_count": len(skipped_drawings),
                    "skipped_drawings": skipped_drawings,
                    "duration_seconds": round(time.monotonic() - started, 1),
                }

            # All project drawings have now completed their CAD/DXF pass.
            # Sorting applies to the extracted room payload, never to the
            # input-file eligibility decision.
            room_data.sort(key=self._room_data_sort_key)

            # Preflight all CAD extraction before mutating Revit.  A failed
            # conversion must not leave newly created, unnamed rooms behind.
            set_tool_progress(_PROGRESS_TOOL, f"正在 Revit 中批量创建房间（共提取 {len(room_data)} 个房间）")
            created = await call_revit_operation(
                self.client, "BatchCreateRooms", self.client.batch_create_rooms
            )
            set_tool_progress(_PROGRESS_TOOL, f"正在向 Revit 房间写入名称与编号（共 {len(room_data)} 个）")
            update = await call_revit_operation(
                self.client, "UpdateRoomName", self.client.update_room_name, room_data
            )

        for path_text, original in source_stats.items():
            current = Path(path_text).stat()
            if (current.st_size, current.st_mtime_ns) != original:
                raise RuntimeError(f"源 DWG 被意外修改：{path_text}")

        # Room creation changes the model.  Always preserve a result model
        # before downstream IFC delivery; never leave those changes only in a
        # live Revit session.
        requested_save_folder = save_folder_path or str(model.parent / "result")
        target = prepare_save_folder(requested_save_folder, str(model))
        set_tool_progress(_PROGRESS_TOOL, f"正在将修改后的模型另存到目录：{target.name}")
        save_before = snapshot_folder_files(target)
        async with self.lock.hold():
            await call_revit_operation(self.client, "SaveAs", self.client.save_as, str(target))
        saved_to = str(target)
        saved_model = resolve_fresh_saved_model_path(target, str(model), save_before)
        saved_model_path = str(saved_model) if saved_model else None
        saved_model_path_status = "verified" if saved_model else "unverified"

        audit_path = self._write_audit(
            {
                "model_path": str(model),
                "dwg_folder_path": str(folder),
                "batch_create_message": created.get("msg"),
                "update_message": update.get("msg"),
                "room_data": room_data,
                "drawing_floor_detection": drawing_floor_detection,
                "skipped_drawings": skipped_drawings,
                "saved_to": saved_to,
                "saved_model_path": saved_model_path,
                "saved_model_path_status": saved_model_path_status,
            }
        )
        return {
            "status": "completed",
            "model_path": str(model),
            "batch_create_summary": created.get("msg", "房间创建完成"),
            "processed_drawing_count": total_drawings,
            "skipped_drawing_count": len(skipped_drawings),
            "skipped_drawings": skipped_drawings,
            "named_room_count": sum(per_floor_counts.values()),
            "per_floor_named_count": per_floor_counts,
            "message": update.get("msg", "房间命名完成"),
            "saved_to": saved_to,
            "saved_model_path": saved_model_path,
            "saved_model_path_status": saved_model_path_status,
            "audit_path": audit_path,
            "duration_seconds": round(time.monotonic() - started, 1),
        }

    def close(self) -> None:
        self.lock.close()
