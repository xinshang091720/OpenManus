"""Regression coverage for DWG floor resolution before Revit room writes.

The DWG name is deliberately treated as a weak candidate.  The drawing title
is the authoritative source when it is available; when neither source can
identify a floor, the workflow must ask for a user decision before mutating
the Revit model.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

import pytest

from app.revit.room_creation import ArRoomCreationWorkflow
from app.revit.room_sync import _extract_rooms_and_floor_from_dwg, _floor_from_path


class _NoopRevitLock:
    @asynccontextmanager
    async def hold(self):
        yield

    def close(self):
        pass


@pytest.fixture(autouse=True)
def no_live_revit_mutex(monkeypatch):
    """Unit tests must not wait for a user's live desktop Revit task."""
    monkeypatch.setattr("app.revit.room_creation.RevitProcessLock", _NoopRevitLock)


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

    async def get_dwg_text(self, path, *, wait_forever=False):
        self.calls.append(("get_dwg_text", path, wait_forever))
        return {
            "code": 200,
            "data": [
                {"Text": "办公室", "Text_coordinates": "(1, 2, 0)", "Text_layer": "0"},
            ],
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


def test_drawing_title_overrides_filename_hint_and_expands_standard_floor_range():
    """A filename candidate cannot bypass an explicit title inside the drawing."""
    from app.revit.room_sync import _extract_rooms_and_floor_from_texts

    text_items = [
        {"Text": "六~三十层平面图", "Text_coordinates": "(0, 0, 0)", "Text_layer": "TITLE"},
        {"Text": "办公室", "Text_coordinates": "(10, 20, 0)", "Text_layer": "ROOM"},
    ]

    _rooms, floors, detection = _extract_rooms_and_floor_from_texts(
        text_items,
        dwg_filename="三层平面图.dwg",
        fallback_floor=3,
    )

    assert floors == list(range(6, 31))
    assert detection["status"] == "resolved"
    assert detection["source"] == "drawing_text"
    assert detection["matched_text"] == "六~三十层平面图"
    assert detection["filename_hint"] == 3


def test_unambiguous_filename_resolves_when_drawing_only_has_incidental_weak_texts():
    """Incidental equipment/diagram text must not override an unambiguous filename."""
    from app.revit.room_sync import _extract_rooms_and_floor_from_texts

    text_items = [
        {"Text": "三层工坊分体空调室外机", "Text_coordinates": "(0, 0, 0)"},
        {"Text": "四层小展厅分体空调室外机", "Text_coordinates": "(1, 1, 0)"},
        {"Text": "五层防火分区示意图", "Text_coordinates": "(2, 2, 0)"},
        {"Text": "走廊做法参一~四层做法6", "Text_coordinates": "(3, 3, 0)"},
        {"Text": "办公室", "Text_coordinates": "(10, 20, 0)"},
    ]

    _rooms, floors, detection = _extract_rooms_and_floor_from_texts(
        text_items,
        dwg_filename="综合楼 五层平面图.dwg",
        fallback_floor=5,
    )

    assert floors == [5]
    assert detection["status"] == "resolved"
    assert detection["source"] == "filename"
    assert detection["floor_entries"] == [5]


def test_range_filename_resolves_standard_floors():
    """A standard range filename resolves cleanly when drawing text has no strong conflict."""
    from app.revit.room_sync import _extract_rooms_and_floor_from_texts

    text_items = [
        {"Text": "办公室", "Text_coordinates": "(10, 20, 0)"},
    ]

    _rooms, floors, detection = _extract_rooms_and_floor_from_texts(
        text_items,
        dwg_filename="综合楼 六~三十层平面图.dwg",
    )

    assert floors == list(range(6, 31))
    assert detection["status"] == "resolved"
    assert detection["source"] == "filename"


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

    monkeypatch.setattr(
        "app.revit.room_creation._extract_rooms_and_floor_from_texts",
        lambda _items, _name, *, fallback_floor=None: (
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
    assert [call[0] for call in client.calls] == ["open", "grid", "get_dwg_text"]
    assert client.updates == []


def test_user_floor_override_resolves_ambiguous_drawing_before_revit_write(tmp_path, monkeypatch):
    """A single user-confirmed mapping becomes the baseline for the next run."""
    model = tmp_path / "project_AR.rvt"
    model.write_bytes(b"rvt")
    drawings = tmp_path / "dwg"
    drawings.mkdir()
    drawing = drawings / "六~三十层标准层图.dwg"
    drawing.write_bytes(b"dwg")

    monkeypatch.setattr(
        "app.revit.room_creation._extract_rooms_and_floor_from_texts",
        lambda _items, _name, *, fallback_floor=None: (
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
    assert [call[0] for call in client.calls] == ["open", "grid", "get_dwg_text", "create", "update", "save"]


def test_standard_floor_plan_disambiguated_by_filename_match():
    """When drawing text has both a range title and a single-floor note, matching filename resolves it."""
    from app.revit.room_sync import _resolve_floor_from_local_texts, _floor_entries_from_title_text

    raw_texts = ["六~二十九层平面图", "二十九层平面图"]
    dwg_filename = "1栋二单元 六~二十九层 平面图.dwg"
    filename_floors = _floor_entries_from_title_text(dwg_filename)
    detection = _resolve_floor_from_local_texts(
        raw_texts,
        filename_hint=None,
        filename_floors=filename_floors,
    )
    assert detection is not None
    assert detection["status"] == "resolved"
    assert detection["floor_entries"] == list(range(6, 30))
    assert detection["matched_text"] == "六~二十九层平面图"


def test_standard_floor_plan_range_candidate_encloses_subset_candidate():
    """Even without filename match, an inclusive range covering all other candidates wins."""
    from app.revit.room_sync import _resolve_floor_from_local_texts

    raw_texts = ["六~二十九层平面图", "二十九层平面图"]
    detection = _resolve_floor_from_local_texts(
        raw_texts,
        filename_hint=None,
        filename_floors=None,
    )
    assert detection is not None
    assert detection["status"] == "resolved"
    assert detection["floor_entries"] == list(range(6, 30))
    assert detection["matched_text"] == "六~二十九层平面图"


def test_floor_overrides_flexible_formats(tmp_path):
    """floor_overrides accepts range strings, Chinese text, and filename-only matching."""
    drawing = tmp_path / "1栋二单元 六~二十九层 平面图.dwg"
    drawing.write_bytes(b"dwg")
    drawings = [drawing]

    # String range "6-29"
    res1 = ArRoomCreationWorkflow._normalise_floor_overrides(
        [{"dwg_path": str(drawing), "floor_numbers": "6-29"}],
        drawings,
    )
    assert list(res1.values())[0] == list(range(6, 30))

    # String range with Chinese "6至29层"
    res2 = ArRoomCreationWorkflow._normalise_floor_overrides(
        [{"dwg_path": str(drawing), "floor_numbers": "6至29层"}],
        drawings,
    )
    assert list(res2.values())[0] == list(range(6, 30))

    # List containing a range string ["6~29"]
    res3 = ArRoomCreationWorkflow._normalise_floor_overrides(
        [{"dwg_path": str(drawing), "floor_numbers": ["6~29"]}],
        drawings,
    )
    assert list(res3.values())[0] == list(range(6, 30))

    # Filename-only matching with range string
    res4 = ArRoomCreationWorkflow._normalise_floor_overrides(
        [{"dwg_path": "1栋二单元 六~二十九层 平面图.dwg", "floor_numbers": "6至29层"}],
        drawings,
    )
    assert list(res4.values())[0] == list(range(6, 30))

