"""Safe preview/apply workflow for synchronising DWG room labels into Revit."""

from __future__ import annotations

import asyncio
from collections import Counter
import json
import math
import os
import re
import time
import uuid
from pathlib import Path
from typing import Any

from app.mcp.revit_lock import RevitProcessLock
from app.revit.client import RevitApiClient
from app.revit.operations import call_revit_operation
from app.runtime_paths import runtime_paths
from app.tool_progress import set_tool_progress


_PREVIEWS: dict[str, dict[str, Any]] = {}
_FLOOR_WORDS = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
_CHINESE_FLOOR_DIGITS = {
    "零": 0,
    "〇": 0,
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
}
_CHINESE_FLOOR_UNITS = {"十": 10, "百": 100}
_FLOOR_NUMBER_TEXT = r"[0-9零〇一二两三四五六七八九十百]+"
_FLOOR_RANGE_SEPARATORS = r"[~～\-—–至到]"
_AUTOCAD_BUSY_MARKERS = (
    "-2147418111",  # RPC_E_CALL_REJECTED
    "rpc_e_call_rejected",
    "call was rejected by callee",
    "被呼叫方拒绝接收呼叫",
    # pywin32 can discard the original HRESULT while formatting a late COM
    # error, leaving only this Automation member name.  It is safe to retry
    # here because conversion opens the source drawing read-only and writes a
    # private temporary DXF.
    "<unknown>.open",
)


def _parse_floor_number(value: str) -> int | None:
    """Parse a positive Arabic or Chinese floor number without guessing.

    This deliberately accepts ordinary Chinese values such as ``十一`` and
    ``三十``.  It returns ``None`` for anything that is not a number, instead
    of falling through to ``int()`` and terminating the whole room workflow.
    """
    text = re.sub(r"\s+", "", str(value or "")).replace("兩", "两")
    if not text:
        return None
    if text.isdecimal():
        number = int(text)
        return number if number > 0 else None

    total = 0
    current = 0
    saw_digit = False
    for char in text:
        if char in _CHINESE_FLOOR_DIGITS:
            current = _CHINESE_FLOOR_DIGITS[char]
            saw_digit = True
            continue
        unit = _CHINESE_FLOOR_UNITS.get(char)
        if unit is None:
            return None
        total += (current or 1) * unit
        current = 0
    number = total + current
    return number if saw_digit and number > 0 else None


def _contains_floor_range(text: str) -> bool:
    """Return whether a name contains a multi-floor expression.

    A range is not a single-floor filename hint.  It must be resolved from the
    drawing title, or explicitly confirmed by the user, before room names are
    written into Revit.
    """
    token = rf"(?:地下\s*)?(?:{_FLOOR_NUMBER_TEXT})(?:\s*(?:层|[Ff]))?"
    return bool(
        re.search(
            rf"{token}\s*{_FLOOR_RANGE_SEPARATORS}\s*{token}\s*(?:层|[Ff])?",
            text,
            flags=re.IGNORECASE,
        )
    )


def _floor_from_path(dwg_path: str) -> int | None:
    """Return only an unambiguous single-floor filename candidate.

    The value is a compatibility hint, never an input-file filter or final
    decision.  Unknown syntax and ranges intentionally return ``None``.
    """
    name = Path(dwg_path).stem
    if _contains_floor_range(name):
        return None

    match = re.search(rf"地下\s*({_FLOOR_NUMBER_TEXT})\s*层", name)
    if match:
        number = _parse_floor_number(match.group(1))
        return -number if number is not None else None
    match = re.search(r"(?:^|[-_])B0*([1-9][0-9]*)", name, flags=re.IGNORECASE)
    if match:
        return -int(match.group(1))
    match = re.search(rf"({_FLOOR_NUMBER_TEXT})\s*层", name)
    if match:
        return _parse_floor_number(match.group(1))

    # A project drawing is often named ``1F建筑平面图`` or ``F01_建筑``.
    # This is only a quick ordering hint: callers must never use it to decide
    # whether a DWG participates in room creation.
    match = re.search(
        r"(?:^|[^A-Za-z0-9])(?:F0*([1-9][0-9]*)|([1-9][0-9]*)F)(?:$|[^A-Za-z0-9])",
        name,
        flags=re.IGNORECASE,
    )
    if match:
        return int(match.group(1) or match.group(2))
    return None


def _preview_file(preview_id: str) -> Path:
    folder = runtime_paths.workspace_dir / "room-sync-previews"
    folder.mkdir(parents=True, exist_ok=True)
    return folder / f"{preview_id}.json"


def _store_preview(preview_id: str, preview: dict[str, Any]) -> None:
    payload = {"preview": preview, "created_at": time.monotonic()}
    _PREVIEWS[preview_id] = payload
    _preview_file(preview_id).write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _take_preview(preview_id: str) -> dict[str, Any] | None:
    cached = _PREVIEWS.pop(preview_id, None)
    path = _preview_file(preview_id)
    if cached is None and path.is_file():
        cached = json.loads(path.read_text(encoding="utf-8"))
    if path.exists():
        path.unlink()
    return cached


def _points(node: dict[str, Any]) -> tuple[tuple[float, ...], tuple[float, ...]]:
    begin, end = node.get("Begin_Position"), node.get("End_Position")
    if not isinstance(begin, list) or not isinstance(end, list) or len(begin) < 2 or len(end) < 2:
        raise ValueError("Revit 插件返回的轴网端点不完整")
    return tuple(map(float, begin)), tuple(map(float, end))


def close_autocad_document_if_open(dwg_path: str) -> bool:
    """Close only the matching AutoCAD document without saving it."""
    try:
        import pythoncom
        import win32com.client
    except ImportError:
        return False
    pythoncom.CoInitialize()
    try:
        app = win32com.client.GetActiveObject("AutoCAD.Application.23.1")
        target = os.path.normcase(os.path.abspath(dwg_path))
        for index in range(app.Documents.Count):
            document = app.Documents.Item(index)
            if os.path.normcase(os.path.abspath(document.FullName)) == target:
                document.Close(False)
                return True
    except Exception:
        return False
    finally:
        pythoncom.CoUninitialize()
    return False


def _normalise_floor_number(value: Any, default: int | float = 0) -> int | float:
    """Compatibility wrapper for legacy callers that used a zero default."""
    number = _coerce_floor_number(value)
    return default if number is None else number


def _coerce_floor_number(value: Any) -> int | float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return int(number) if number.is_integer() else number


def _floor_entries_from_bounds(
    minimum: int | float,
    maximum: int | float,
) -> list[int | float]:
    if minimum == maximum:
        return [minimum]
    if isinstance(minimum, int) and isinstance(maximum, int):
        start, end = sorted((minimum, maximum))
        # A malformed title must never fan one room payload out to thousands of
        # Revit levels.  Real building drawings are far below this bound.
        if end - start > 200:
            return []
        return list(range(start, end + 1))
    return list(dict.fromkeys((minimum, maximum)))


def _floor_entries_from_drawing_info(floor_info: Any) -> list[int | float]:
    """Normalise a title detector result without inventing an unknown level."""
    if not isinstance(floor_info, dict):
        return []

    if "min_floor" in floor_info or "max_floor" in floor_info:
        min_floor = _coerce_floor_number(floor_info.get("min_floor"))
        max_floor = _coerce_floor_number(floor_info.get("max_floor"))
        if min_floor is None or max_floor is None:
            return []
    else:
        floor_num = _coerce_floor_number(floor_info.get("floor_num"))
        if floor_num is None:
            return []
        min_floor = max_floor = floor_num

    return _floor_entries_from_bounds(min_floor, max_floor)


def _parse_floor_reference(value: str) -> int | None:
    """Parse one floor endpoint from a drawing title, including basement form."""
    text = re.sub(r"\s+", "", str(value or ""))
    if not text:
        return None

    underground = text.startswith("地下")
    if underground:
        text = text[2:]
    match = re.fullmatch(r"[Bb]0*([1-9][0-9]*)", text)
    if match:
        return -int(match.group(1))
    match = re.fullmatch(r"[Ff]0*([1-9][0-9]*)", text)
    if match:
        return int(match.group(1))
    match = re.fullmatch(r"0*([1-9][0-9]*)[Ff]", text)
    if match:
        return int(match.group(1))

    text = re.sub(r"(?:层|[Ff])$", "", text, flags=re.IGNORECASE)
    number = _parse_floor_number(text)
    if number is None:
        return None
    return -number if underground else number


def _floor_entries_from_title_text(text: str) -> list[int | float]:
    """Extract a concrete level or range from one drawing-title string."""
    compact = re.sub(r"\s+", "", str(text or ""))
    if not compact:
        return []

    endpoint = (
        rf"(?:地下)?(?:[Bb]0*[1-9][0-9]*|[Ff]0*[1-9][0-9]*|"
        rf"0*[1-9][0-9]*[Ff]|{_FLOOR_NUMBER_TEXT})(?:层)?"
    )
    range_match = re.search(
        rf"(?P<minimum>{endpoint})\s*{_FLOOR_RANGE_SEPARATORS}\s*"
        rf"(?P<maximum>{endpoint})",
        compact,
        flags=re.IGNORECASE,
    )
    if range_match:
        minimum = _parse_floor_reference(range_match.group("minimum"))
        maximum = _parse_floor_reference(range_match.group("maximum"))
        if minimum is not None and maximum is not None:
            return _floor_entries_from_bounds(minimum, maximum)

    if "首层" in compact or "地面层" in compact:
        return [1]
    basement = re.search(rf"地下\s*({_FLOOR_NUMBER_TEXT})\s*层", compact)
    if basement:
        number = _parse_floor_number(basement.group(1))
        return [-number] if number is not None else []
    b_floor = re.search(r"(?:^|[^A-Za-z0-9])[Bb]0*([1-9][0-9]*)(?:$|[^A-Za-z0-9])", compact)
    if b_floor:
        return [-int(b_floor.group(1))]
    f_floor = re.search(r"(?:^|[^A-Za-z0-9])(?:[Ff]0*([1-9][0-9]*)|([1-9][0-9]*)[Ff])(?:$|[^A-Za-z0-9])", compact)
    if f_floor:
        return [int(f_floor.group(1) or f_floor.group(2))]
    ordinary = re.search(rf"({_FLOOR_NUMBER_TEXT})\s*层", compact)
    if ordinary:
        number = _parse_floor_number(ordinary.group(1))
        return [number] if number is not None else []
    return []


def _title_candidate_score(text: str) -> int:
    """Rank likely title-block strings over incidental drawing annotations."""
    compact = re.sub(r"\s+", "", text)
    if not compact or len(compact) > 120:
        return 0
    if any(marker in compact for marker in ("平面图", "图纸名称", "图名")):
        return 100
    if "层" in compact or re.search(r"(?:^|[^A-Za-z0-9])[BbFf]\d+", compact):
        return 20
    return 0


def _resolved_floor_detection(
    *,
    source: str,
    confidence: str,
    floor_entries: list[int | float],
    filename_hint: int | float | None,
    matched_text: str = "",
    candidates: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "status": "resolved",
        "source": source,
        "confidence": confidence,
        "matched_text": matched_text,
        "floor_entries": floor_entries,
        "filename_hint": filename_hint,
        "candidates": candidates or [],
    }


def _selection_required_floor_detection(
    *,
    filename_hint: int | float | None,
    candidates: list[dict[str, Any]] | None = None,
    source: str = "unresolved",
) -> dict[str, Any]:
    return {
        "status": "selection_required",
        "source": source,
        "confidence": "none",
        "matched_text": "",
        "floor_entries": [],
        "filename_hint": filename_hint,
        "candidates": candidates or [],
    }


def _resolve_floor_from_local_texts(
    raw_texts: list[str],
    *,
    filename_hint: int | float | None,
    filename_floors: list[int | float] | None = None,
) -> dict[str, Any] | None:
    """Use explicit DWG title text before consulting any filename hint."""
    candidates: list[tuple[int, str, list[int | float]]] = []
    for raw_text in raw_texts:
        text = re.sub(r"\\[a-zA-Z0-9]+;|\{[^{}]*\}", "", str(raw_text or "")).strip()
        score = _title_candidate_score(text)
        if not score:
            continue
        entries = _floor_entries_from_title_text(text)
        if entries:
            candidates.append((score, text, entries))
    if not candidates:
        return None

    highest_score = max(item[0] for item in candidates)
    strongest = [item for item in candidates if item[0] == highest_score]
    by_entries: dict[tuple[int | float, ...], list[str]] = {}
    for _, text, entries in strongest:
        by_entries.setdefault(tuple(entries), []).append(text)
    if len(by_entries) == 1:
        entries, texts = next(iter(by_entries.items()))
        return _resolved_floor_detection(
            source="drawing_text",
            confidence="high",
            floor_entries=list(entries),
            filename_hint=filename_hint,
            matched_text=texts[0],
        )

    # ponytail: When drawing text has multiple conflicting strong titles (>= 100), but the
    # DWG filename explicitly matches one of them (e.g. '1栋二单元 六~二十九层 平面图.dwg'
    # matches '六~二十九层平面图'), the filename confirms which drawing title is primary.
    if highest_score >= 100 and filename_floors:
        target_key = tuple(filename_floors)
        if target_key in by_entries:
            texts = by_entries[target_key]
            return _resolved_floor_detection(
                source="drawing_text",
                confidence="high",
                floor_entries=list(target_key),
                filename_hint=filename_hint,
                matched_text=texts[0],
            )

    # ponytail: When drawing text only has weak/incidental annotations (< 100)
    # that conflict (e.g. equipment annotations like '三层工坊室外机'), but the
    # DWG filename is unambiguous (e.g. '综合楼 五层平面图.dwg'), the normalized
    # filename takes precedence over weak drawing noise without hardcoding keywords.
    if highest_score < 100 and filename_floors:
        matching_cand = next((c for c in strongest if c[2] == filename_floors), None)
        matched_text = matching_cand[1] if matching_cand else ""
        return _resolved_floor_detection(
            source="filename",
            confidence="high" if matched_text else "medium",
            floor_entries=filename_floors,
            filename_hint=filename_hint,
            matched_text=matched_text,
        )

    # ponytail: For standard floor drawings, the overall sheet title is an inclusive range
    # (e.g. '六~二十九层平面图'), while sub-details/notes cite a single floor (e.g. '二十九层平面图').
    # If exactly one candidate is an inclusive multi-floor range and all other candidates are
    # subsets of it, the range represents the full sheet floor coverage.
    range_candidates = [k for k in by_entries if len(k) > 1]
    if len(range_candidates) == 1:
        superset = set(range_candidates[0])
        other_keys = [k for k in by_entries if k != range_candidates[0]]
        if all(set(k).issubset(superset) for k in other_keys):
            target_key = range_candidates[0]
            texts = by_entries[target_key]
            return _resolved_floor_detection(
                source="drawing_text",
                confidence="high",
                floor_entries=list(target_key),
                filename_hint=filename_hint,
                matched_text=texts[0],
            )

    return _selection_required_floor_detection(
        filename_hint=filename_hint,
        source="drawing_text",
        candidates=[
            {"matched_text": texts[0], "floor_entries": list(entries)}
            for entries, texts in by_entries.items()
        ],
    )


def _resolve_floor_from_local_drawing_title(
    dxf_path: str,
    *,
    filename_hint: int | float | None,
) -> dict[str, Any] | None:
    """Compatibility fallback."""
    return None


def _resolve_floor_from_legacy_title_reader(
    dxf_path: str,
    *,
    filename_hint: int | float | None,
) -> dict[str, Any] | None:
    """Compatibility fallback."""
    return None


def _extract_rooms_and_floor_from_texts(
    text_items: list[dict[str, Any]],
    dwg_filename: str = "",
    *,
    fallback_floor: int | float | None = None,
) -> tuple[dict[str, Any], list[int | float], dict[str, Any]]:
    """Process DWG text items and return rooms plus floor mapping."""
    from q_agent_function_module.ohresult.CAD_Git_Coordinates.my_code.room_coordinates.dwg_room_extractor import (
        process_room_extraction_from_texts,
    )
    from q_agent_function_module.ohresult.CAD_Git_Coordinates.my_code.room_coordinates.git_cad_floor import (
        judge_floor_from_texts,
    )

    extracted = process_room_extraction_from_texts(text_items)
    raw_texts = [str(item.get("Text") or "") for item in text_items if item.get("Text")]

    filename_floors: list[int | float] = []
    if dwg_filename:
        filename_floors = _floor_entries_from_title_text(Path(dwg_filename).stem)
    if not filename_floors and fallback_floor is not None:
        filename_floors = [_normalise_floor_number(fallback_floor)]

    local_detection = _resolve_floor_from_local_texts(
        raw_texts,
        filename_hint=fallback_floor,
        filename_floors=filename_floors,
    )
    if local_detection is not None:
        return extracted, local_detection["floor_entries"], local_detection

    if filename_floors:
        detection = _resolved_floor_detection(
            source="filename",
            confidence="medium",
            floor_entries=filename_floors,
            filename_hint=fallback_floor,
        )
        return extracted, filename_floors, detection

    floor_res = judge_floor_from_texts(raw_texts, dwg_filename)
    if isinstance(floor_res, dict):
        min_f = _coerce_floor_number(floor_res.get("min_floor"))
        max_f = _coerce_floor_number(floor_res.get("max_floor"))
        matched_text = str(floor_res.get("matched_text") or "").strip()
        if min_f is not None and max_f is not None:
            entries = _floor_entries_from_bounds(min_f, max_f)
            if entries and entries != [0] and matched_text:
                detection = _resolved_floor_detection(
                    source="drawing_text",
                    confidence="high",
                    floor_entries=entries,
                    filename_hint=fallback_floor,
                    matched_text=matched_text,
                )
                return extracted, entries, detection

    if filename_floors:
        detection = _resolved_floor_detection(
            source="filename",
            confidence="medium",
            floor_entries=filename_floors,
            filename_hint=fallback_floor,
        )
        return extracted, filename_floors, detection

    if fallback_floor is not None:
        entries = [_normalise_floor_number(fallback_floor)]
        detection = _resolved_floor_detection(
            source="filename_hint",
            confidence="medium",
            floor_entries=entries,
            filename_hint=fallback_floor,
        )
        return extracted, entries, detection

    detection = _selection_required_floor_detection(
        filename_hint=fallback_floor,
    )
    return extracted, [], detection


def _extract_rooms_and_floor_from_dwg(
    dwg_path: str,
    *,
    fallback_floor: int | float | None = None,
) -> tuple[dict[str, Any], list[int | float], dict[str, Any]]:
    """Compatibility wrapper."""
    return _extract_rooms_and_floor_from_texts(
        [],
        Path(dwg_path).name,
        fallback_floor=fallback_floor,
    )


def _extract_rooms_from_dwg(dwg_path: str) -> dict[str, Any]:
    """Backward-compatible room-only wrapper used by older call sites."""
    extracted, _, _ = _extract_rooms_and_floor_from_dwg(
        dwg_path,
        fallback_floor=_floor_from_path(dwg_path),
    )
    return extracted


class RoomSyncWorkflow:
    """Use one read-only preview before allowing a room-name update."""

    def __init__(self, client: RevitApiClient | None = None) -> None:
        self.client = client or RevitApiClient()
        self.lock = RevitProcessLock()

    async def preview(
        self,
        dwg_path: str,
        target_model_path: str,
        discipline: str,
        allow_length_mismatch: bool = True,
    ) -> dict[str, Any]:
        if Path(dwg_path).suffix.lower() != ".dwg" or not Path(dwg_path).is_file():
            raise ValueError("dwg_path 必须是存在的 .dwg 文件")
        if discipline.strip().upper() not in {"AR", "建筑"}:
            raise ValueError("房间同步仅适用于用户确认的建筑（AR）模型")
        async with self.lock.hold():
            grid = await call_revit_operation(
                self.client, "DwgRevitGridData", self.client.dwg_revit_grid_data, dwg_path
            )
            from q_agent_function_module.ohresult.CAD_Git_Coordinates.my_code.room_coordinates.coordinate_transformation import (
                calculate_segment_translation_vector,
                transform_room_texts,
                validate_translation_alignment,
            )

            rvt_grid, dwg_grid = grid.get("rvtGrid", {}), grid.get("dwgGrid", {})
            rvt_line, dwg_line = _points(rvt_grid), _points(dwg_grid)
            alignment = validate_translation_alignment(
                rvt_line, dwg_line, allow_length_mismatch=allow_length_mismatch
            )
            if not alignment["valid"]:
                return {
                    "status": "blocked",
                    "reason": alignment["reason"],
                    "grid_axis_code": {"revit": rvt_grid.get("AxisCode"), "dwg": dwg_grid.get("AxisCode")},
                    "alignment": alignment,
                    "cad_document_closed": True,
                }

            text_res = await call_revit_operation(
                self.client, "GetDwgText", self.client.get_dwg_text, dwg_path
            )
            text_items = text_res.get("data", []) if isinstance(text_res, dict) else []
            extracted, floor_entries, floor_detection = await asyncio.to_thread(
                _extract_rooms_and_floor_from_texts,
                text_items,
                Path(dwg_path).name,
                fallback_floor=_floor_from_path(dwg_path),
            )
            if floor_detection.get("status") != "resolved" or not floor_entries:
                return {
                    "status": "selection_required",
                    "reason": "Unable to uniquely determine this DWG drawing's floor.",
                    "dwg_path": dwg_path,
                    "floor_detection": floor_detection,
                    "alignment": alignment,
                    "cad_document_closed": True,
                }
            transformed = transform_room_texts(
                extracted, calculate_segment_translation_vector(rvt_line, dwg_line)
            )

        rooms = [item for item in transformed["room_texts"] if item.get("RoomName", "").strip()]
        filter_summary = Counter(
            item.get("reason", "未知") for item in extracted.get("filtered_out", [])
        )
        floor_num = floor_entries[0]
        room_data = [{"floor_num": floor, "room_texts": rooms} for floor in floor_entries]
        preview_id = uuid.uuid4().hex
        preview = {
            "status": "ready",
            "preview_id": preview_id,
            "dwg_path": dwg_path,
            "target_model_path": target_model_path,
            "floor_num": floor_num,
            "floor_numbers": floor_entries,
            "floor_detection": floor_detection,
            "room_count": len(rooms),
            "filtered_count": sum(filter_summary.values()),
            "filter_summary": dict(filter_summary),
            "room_data": room_data,
            "grid_axis_code": {"revit": rvt_grid.get("AxisCode"), "dwg": dwg_grid.get("AxisCode")},
            "alignment": alignment,
            "translation_only": True,
            "cad_document_closed": cad_document_closed,
            "warning": "插件未提供活动模型路径校验或房间创建结果；写入后仅报告插件原始响应。",
        }
        _store_preview(preview_id, preview)
        return preview

    async def apply(self, preview_id: str, confirmed: bool) -> dict[str, Any]:
        if not confirmed:
            raise ValueError("写入房间前必须明确确认预览结果")
        cached = _take_preview(preview_id)
        if not cached:
            raise ValueError("预览不存在或已使用；请重新生成预览")
        preview = cached["preview"]
        if preview["status"] != "ready" or not preview["alignment"]["valid"]:
            raise ValueError("该预览未通过坐标校验，禁止写入")
        async with self.lock.hold():
            response = await call_revit_operation(
                self.client, "UpdateRoomName", self.client.update_room_name, preview["room_data"]
            )
        return {
            "status": "update_requested",
            "preview_id": preview_id,
            "room_count": preview["room_count"],
            "plugin_message": response.get("msg", "Revit 插件未返回说明"),
            "plugin_response": response,
            "saved": False,
            "note": "C# 接口未声明是否自动创建缺失房间；结果只代表插件已接受更新请求。",
        }

    def close(self) -> None:
        self.lock.close()
