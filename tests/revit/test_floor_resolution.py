"""Regression coverage for DWG floor resolution before Revit room writes.

The DWG name is deliberately treated as a weak candidate.  The drawing title
is the authoritative source when it is available; when neither source can
identify a floor, the workflow must ask for a user decision before mutating
the Revit model.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from app.revit.room_creation import ArRoomCreationWorkflow
from app.revit.room_sync import _extract_rooms_and_floor_from_dwg, _floor_from_path


class _RoomClient:
    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.updates: list[list[dict]] = []

    async def open_revit_file(self, path, *, wait_forever=False):
        self.calls.append(("open", path, wait_forever))
        return {"code": 200, "msg": "opened"}

    async def dwg_revit_grid_data(self, path, *, wait_forever=False):
        self.calls.append(("grid", path, wait_forever))
        return {
            "code": 200,
            "rvtGrid": {"AxisCode": "A-1", "Begin_Position": [0, 0, 0], "End_Position": [10, 0, 0]},
            "dwgGrid": {"AxisCode": "A-1", "Begin_Position": [20, 30, 0], "End_Position": [30, 30, 0]},
        }

    async def batch_create_rooms(self, *, wait_forever=False):
        self.calls.append(("create", wait_forever))
        return {"code": 200, "msg": "rooms created"}

    async def update_room_name(self, payload, *, wait_forever=False):
        self.calls.append(("update", wait_forever))
        self.updates.append(payload)
        return {"code": 200, "msg": "rooms named"}

    async def save_as(self, folder, *, wait_forever=False):
        self.calls.append(("save", folder, wait_forever))
        return {"code": 200, "msg": "saved"}


def _stub_dxf_pipeline(tmp_path, monkeypatch, floor_info):
    temporary_dxf = tmp_path / "temporary.dxf"

    def convert(_dwg_path):
        temporary_dxf.write_bytes(b"dxf")
        return str(temporary_dxf)

    monkeypatch.setattr(
        "q_agent_function_module.ohresult.CAD_Git_Coordinates.my_code.room_coordinates.dwg_room_extractor_main._convert_dwg_to_dxf_via_autocad",
        convert,
    )
    monkeypatch.setattr(
        "q_agent_function_module.ohresult.CAD_Git_Coordinates.my_code.room_coordinates.dwg_room_extractor.process_dwg_room_extraction",
        lambda _path: {"room_texts": [], "filtered_out": []},
    )
    monkeypatch.setattr(
        "q_agent_function_module.ohresult.CAD_Git_Coordinates.my_code.room_coordinates.git_cad_floor.get_cad_floor_info",
        lambda _path: floor_info,
    )
    monkeypatch.setattr("app.revit.room_sync.set_tool_progress", lambda *_args, **_kwargs: None)
    return temporary_dxf


def test_drawing_title_overrides_filename_hint_and_expands_standard_floor_range(tmp_path, monkeypatch):
    """A filename candidate cannot bypass an explicit title inside the drawing."""
    source = tmp_path / "三层平面图.dwg"
    source.write_bytes(b"dwg")
    temporary_dxf = _stub_dxf_pipeline(
        tmp_path,
        monkeypatch,
        {"matched_text": "六~三十层平面图", "min_floor": 6.0, "max_floor": 30.0},
    )

    _rooms, floors, detection = _extract_rooms_and_floor_from_dwg(
        str(source),
        fallback_floor=3,
    )

    assert floors == list(range(6, 31))
    assert detection["status"] == "resolved"
    assert detection["source"] == "drawing_text"
    assert detection["matched_text"] == "六~三十层平面图"
    assert detection["filename_hint"] == 3
    assert not temporary_dxf.exists()


def test_filename_range_is_not_forced_into_one_floor_and_does_not_raise():
    """A range-like filename must continue to drawing-title analysis safely."""
    assert _floor_from_path(r"C:\项目\六~三十层平面图.dwg") is None


def test_existing_single_floor_filename_hints_remain_available():
    """The compatibility hints used by existing projects stay intact."""
    assert _floor_from_path(r"C:\项目\地下一层平面图.dwg") == -1
    assert _floor_from_path(r"C:\项目\B03-建筑平面图.dwg") == -3
    assert _floor_from_path(r"C:\项目\1F建筑平面图.dwg") == 1


def test_unresolved_floor_returns_selection_before_any_revit_write(tmp_path, monkeypatch):
    """Ambiguity is a user decision, never a zero-floor Revit write."""
    model = tmp_path / "project_AR.rvt"
    model.write_bytes(b"rvt")
    drawings = tmp_path / "dwg"
    drawings.mkdir()
    drawing = drawings / "无明确楼层图纸.dwg"
    drawing.write_bytes(b"dwg")

    monkeypatch.setattr("app.revit.room_creation.close_autocad_document_if_open", lambda _path: False)
    monkeypatch.setattr(
        "app.revit.room_creation._extract_rooms_and_floor_from_dwg",
        lambda _path, *, fallback_floor=None: (
            {"room_texts": [{"RoomName": "设备房", "XYZ": "(1, 2, 0)"}], "filtered_out": []},
            [],
            {
                "status": "selection_required",
                "source": "unresolved",
                "confidence": "none",
                "matched_text": "",
                "candidates": [],
                "filename_hint": fallback_floor,
            },
        ),
    )

    client = _RoomClient()
    workflow = ArRoomCreationWorkflow(client)
    try:
        result = asyncio.run(workflow.run(str(model), str(drawings), "AR"))
    finally:
        workflow.close()

    assert result["status"] == "selection_required"
    assert result["candidates"] == [{"dwg_path": str(drawing), "floor_candidates": []}]
    assert [call[0] for call in client.calls] == ["open", "grid"]
    assert client.updates == []


def test_user_floor_override_resolves_ambiguous_drawing_before_revit_write(tmp_path, monkeypatch):
    """A single user-confirmed mapping becomes the baseline for the next run."""
    model = tmp_path / "project_AR.rvt"
    model.write_bytes(b"rvt")
    drawings = tmp_path / "dwg"
    drawings.mkdir()
    drawing = drawings / "六~三十层标准层图.dwg"
    drawing.write_bytes(b"dwg")

    monkeypatch.setattr("app.revit.room_creation.close_autocad_document_if_open", lambda _path: False)
    monkeypatch.setattr(
        "app.revit.room_creation._extract_rooms_and_floor_from_dwg",
        lambda _path, *, fallback_floor=None: (
            {"room_texts": [{"RoomName": "标准层房间", "XYZ": "(1, 2, 0)"}], "filtered_out": []},
            [],
            {"status": "selection_required", "source": "unresolved", "candidates": []},
        ),
    )

    client = _RoomClient()
    workflow = ArRoomCreationWorkflow(client)
    try:
        result = asyncio.run(
            workflow.run(
                str(model),
                str(drawings),
                "AR",
                floor_overrides=[
                    {
                        "dwg_path": str(drawing),
                        "floor_numbers": list(range(6, 31)),
                    }
                ],
            )
        )
    finally:
        workflow.close()

    assert result["status"] == "completed"
    assert [item["floor_num"] for item in client.updates[0]] == list(range(6, 31))
    assert [call[0] for call in client.calls] == ["open", "grid", "create", "update", "save"]
