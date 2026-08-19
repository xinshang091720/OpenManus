import asyncio
from pathlib import Path

import pytest

from app.revit.client import RevitApiError
from app.revit.room_sync import RoomSyncWorkflow, _PREVIEWS, _extract_rooms_from_dwg
from q_agent_function_module.ohresult.CAD_Git_Coordinates.my_code.room_coordinates.coordinate_transformation import (
    validate_translation_alignment,
)


def test_translation_validation_accepts_translation_and_rejects_rotation_or_scale():
    assert validate_translation_alignment(((0, 0), (10, 0)), ((20, 30), (30, 30)))["valid"]
    assert not validate_translation_alignment(((0, 0), (10, 0)), ((0, 0), (0, 10)))["valid"]
    assert not validate_translation_alignment(((0, 0), (10, 0)), ((0, 0), (20, 0)))["valid"]
    legacy = validate_translation_alignment(
        ((0, 0), (10, 0)), ((0, 0), (20, 0)), allow_length_mismatch=True
    )
    assert legacy["valid"] and legacy["warning"]


def test_dwg_extractor_refuses_source_dwg_without_deleting_it(tmp_path):
    from q_agent_function_module.ohresult.CAD_Git_Coordinates.my_code.room_coordinates.dwg_room_extractor import (
        process_dwg_room_extraction,
    )

    source = tmp_path / "地下三层平面图.dwg"
    source.write_bytes(b"source")
    with pytest.raises(ValueError, match="converted .dxf"):
        process_dwg_room_extraction(str(source))
    assert source.read_bytes() == b"source"


def test_dwg_extraction_retries_only_autocad_busy_without_touching_source(tmp_path, monkeypatch):
    source = tmp_path / "floor-B1.dwg"
    source.write_bytes(b"source")
    temporary_dxf = tmp_path / "temporary.dxf"
    attempts = []
    waits = []

    def convert(_):
        attempts.append("convert")
        if len(attempts) == 1:
            raise RuntimeError("-2147418111: 被呼叫方拒绝接收呼叫")
        temporary_dxf.write_bytes(b"dxf")
        return str(temporary_dxf)

    monkeypatch.setattr(
        "q_agent_function_module.ohresult.CAD_Git_Coordinates.my_code.room_coordinates.dwg_room_extractor_main._convert_dwg_to_dxf_via_autocad",
        convert,
    )
    monkeypatch.setattr(
        "q_agent_function_module.ohresult.CAD_Git_Coordinates.my_code.room_coordinates.dwg_room_extractor.process_dwg_room_extraction",
        lambda _: {"room_texts": []},
    )
    monkeypatch.setattr("app.revit.room_sync.time.sleep", lambda seconds: waits.append(seconds))

    assert _extract_rooms_from_dwg(str(source)) == {"room_texts": []}
    assert attempts == ["convert", "convert"]
    assert waits == [2]
    assert source.read_bytes() == b"source"
    assert not temporary_dxf.exists()


def test_obvious_drawing_annotations_are_not_room_names():
    from q_agent_function_module.ohresult.CAD_Git_Coordinates.my_code.room_coordinates.dwg_room_extractor import (
        is_obvious_non_room_label,
    )

    assert is_obvious_non_room_label("地下三层平面图")
    assert is_obvious_non_room_label("面积：29.36m")
    assert is_obvious_non_room_label("电梯基坑标高")
    assert not is_obvious_non_room_label("101诊室")


class GridClient:
    def __init__(self, grid):
        self.grid = grid
        self.updates = []

    async def dwg_revit_grid_data(self, _):
        return self.grid

    async def update_room_name(self, payload):
        self.updates.append(payload)
        return {"code": 200, "msg": "accepted"}


def test_preview_blocks_before_extraction_when_grid_requires_rotation(tmp_path, monkeypatch):
    dwg = tmp_path / "地下三层平面图.dwg"
    dwg.write_bytes(b"dwg")
    model = tmp_path / "model_AR-B3.rvt"
    model.write_bytes(b"rvt")
    client = GridClient({
        "rvtGrid": {"AxisCode": "A-1", "Begin_Position": [0, 0, 0], "End_Position": [10, 0, 0]},
        "dwgGrid": {"AxisCode": "A-1", "Begin_Position": [0, 0, 0], "End_Position": [0, 10, 0]},
    })
    monkeypatch.setattr("app.revit.room_sync.close_autocad_document_if_open", lambda _: False)
    workflow = RoomSyncWorkflow(client)
    try:
        preview = asyncio.run(workflow.preview(str(dwg), str(model), "AR"))
    finally:
        workflow.close()
    assert preview["status"] == "blocked"
    assert "旋转" in preview["reason"]


def test_apply_uses_preview_once_and_never_saves():
    client = GridClient({})
    preview_id = "approved-preview"
    _PREVIEWS[preview_id] = {"created_at": 0, "preview": {
        "status": "ready", "alignment": {"valid": True}, "room_count": 1,
        "room_data": [{"floor_num": -3, "room_texts": [{"RoomName": "101诊室", "XYZ": "(1, 2, 0)"}]}],
    }}
    workflow = RoomSyncWorkflow(client)
    try:
        result = asyncio.run(workflow.apply(preview_id, True))
    finally:
        workflow.close()
    assert client.updates == [[{"floor_num": -3, "room_texts": [{"RoomName": "101诊室", "XYZ": "(1, 2, 0)"}]}]]
    assert result["saved"] is False
    with pytest.raises(ValueError, match="不存在或已使用"):
        asyncio.run(RoomSyncWorkflow(client).apply(preview_id, True))


def test_process_lock_is_reentrant_inside_one_revit_request():
    from app.mcp.revit_lock import RevitProcessLock

    async def acquire_twice():
        outer = RevitProcessLock()
        inner = RevitProcessLock()
        try:
            async with outer.hold():
                async with inner.hold():
                    return "ok"
        finally:
            outer.close()
            inner.close()

    assert asyncio.run(acquire_twice()) == "ok"
