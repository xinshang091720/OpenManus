import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

import pytest

from app.revit.operations import RevitOperationUnknown, call_revit_operation
from app.revit.room_creation import ArRoomCreationWorkflow


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


class RoomClient:
    def __init__(self):
        self.calls = []
        self.updates = []

    async def open_revit_file(self, path, *, wait_forever=False):
        self.calls.append(("open", path, wait_forever))
        return {"code": 200, "msg": "opened"}

    async def batch_create_rooms(self, *, wait_forever=False):
        self.calls.append(("create", wait_forever))
        return {"code": 200, "msg": "rooms created"}

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

    async def update_room_name(self, payload, *, wait_forever=False):
        self.calls.append(("update", wait_forever))
        self.updates.append(payload)
        return {"code": 200, "msg": "rooms named"}

    async def save_as(self, folder, *, wait_forever=False):
        self.calls.append(("save", folder, wait_forever))
        target = Path(folder)
        source = next(target.parent.glob("*.rvt"), None)
        if source is not None:
            (target / source.name).write_bytes(b"saved")
        return {"code": 200, "msg": "saved"}


def test_ar_room_creation_processes_every_project_dwg_and_orders_floor_data(tmp_path, monkeypatch):
    model = tmp_path / "project_AR-B2.rvt"
    model.write_bytes(b"rvt")
    folder = tmp_path / "dwg"
    folder.mkdir()
    first = folder / "floor-B1.dwg"
    second = folder / "floor-B2.dwg"
    first_floor = folder / "1F\u5efa\u7b51\u5e73\u9762\u56fe.dwg"
    unknown = folder / "\u672a\u547d\u540d\u56fe\u7eb8.dwg"
    drawings = (first, second, first_floor, unknown)
    for path in drawings:
        path.write_bytes(b"dwg")
    original_source_stats = {
        path: (path.read_bytes(), path.stat().st_mtime_ns)
        for path in drawings
    }

    floors_by_name = {
        first.name: [-1],
        second.name: [-2],
        first_floor.name: [1],
        unknown.name: [3],
    }
    seen = []

    def extract(text_items, dwg_name, *, fallback_floor=None):
        name = dwg_name
        seen.append((name, fallback_floor))
        return (
            {"room_texts": [{"RoomName": "办公室", "XYZ": "(1, 2, 0)"}], "filtered_out": []},
            floors_by_name[name],
            {"source": "test"},
        )

    monkeypatch.setattr("app.revit.room_creation._extract_rooms_and_floor_from_texts", extract)
    client = RoomClient()
    workflow = ArRoomCreationWorkflow(client)
    try:
        result = asyncio.run(workflow.run(str(model), str(folder), "AR"))
    finally:
        workflow.close()

    assert [item[0] for item in client.calls] == [
        "open",
        "grid", "get_dwg_text",
        "grid", "get_dwg_text",
        "grid", "get_dwg_text",
        "grid", "get_dwg_text",
        "create", "update", "save",
    ]
    assert {Path(item[1]).name for item in client.calls if item[0] == "grid"} == set(floors_by_name)
    assert {name for name, _ in seen} == set(floors_by_name)
    assert dict(seen)[first_floor.name] == 1
    assert dict(seen)[unknown.name] is None
    assert [item["floor_num"] for item in client.updates[0]] == [-2, -1, 1, 3]
    assert result["processed_drawing_count"] == 4
    assert result["named_room_count"] == 4
    assert result["skipped_drawing_count"] == 0
    assert "room_data" not in result
    assert result["saved_to"] == str(tmp_path / "result")
    assert result["saved_model_path"] == str(tmp_path / "result" / model.name)
    for path, (original_bytes, original_mtime) in original_source_stats.items():
        assert path.read_bytes() == original_bytes
        assert path.stat().st_mtime_ns == original_mtime


def test_room_creation_uses_user_confirmed_discipline_not_filename(tmp_path):
    model = tmp_path / "project-B2.rvt"
    model.write_bytes(b"rvt")
    folder = tmp_path / "dwg"
    folder.mkdir()
    client = RoomClient()
    workflow = ArRoomCreationWorkflow(client)
    try:
        result = asyncio.run(workflow.run(str(model), str(folder), "ST"))
    finally:
        workflow.close()
    assert result["status"] == "not_applicable"
    assert client.calls == []


def test_room_creation_accepts_ar_model_without_ar_filename(tmp_path):
    model = tmp_path / "project-B2.rvt"
    model.write_bytes(b"rvt")
    folder = tmp_path / "dwg"
    folder.mkdir()
    workflow = ArRoomCreationWorkflow(RoomClient())
    try:
        result = asyncio.run(workflow.run(str(model), str(folder), "AR"))
    finally:
        workflow.close()
    assert result["status"] == "skipped"
    assert result["skipped_drawing_count"] == 0


def test_room_creation_processes_multiple_drawings_that_map_to_the_same_floor(tmp_path, monkeypatch):
    model = tmp_path / "project_AR-B2.rvt"
    model.write_bytes(b"rvt")
    folder = tmp_path / "dwg"
    folder.mkdir()
    (folder / "one-B1.dwg").write_bytes(b"dwg")
    (folder / "two-B1.dwg").write_bytes(b"dwg")
    monkeypatch.setattr(
        "app.revit.room_creation._extract_rooms_and_floor_from_texts",
        lambda _items, _name, *, fallback_floor=None: (
            {"room_texts": [{"RoomName": "办公室", "XYZ": "(1, 2, 0)"}]},
            [fallback_floor],
            {"source": "filename_hint"},
        ),
    )
    client = RoomClient()
    workflow = ArRoomCreationWorkflow(client)
    try:
        result = asyncio.run(workflow.run(str(model), str(folder), "AR"))
    finally:
        workflow.close()
    assert result["processed_drawing_count"] == 2
    assert [item["floor_num"] for item in client.updates[0]] == [-1, -1]


def test_room_creation_does_not_create_rooms_when_dwg_preflight_fails(tmp_path, monkeypatch):
    model = tmp_path / "project_AR-B2.rvt"
    model.write_bytes(b"rvt")
    folder = tmp_path / "dwg"
    folder.mkdir()
    (folder / "floor-B1.dwg").write_bytes(b"dwg")
    monkeypatch.setattr(
        "app.revit.room_creation._extract_rooms_and_floor_from_texts",
        lambda _items, _name, **__: (_ for _ in ()).throw(RuntimeError("Extraction failed")),
    )
    client = RoomClient()
    workflow = ArRoomCreationWorkflow(client)
    try:
        with pytest.raises(RuntimeError, match="Extraction failed"):
            asyncio.run(workflow.run(str(model), str(folder), "AR"))
    finally:
        workflow.close()
    assert [item[0] for item in client.calls] == ["open", "grid", "get_dwg_text"]


def test_long_operation_continues_when_health_is_unsupported():
    class Client:
        async def plugin_status(self):
            return {"status": "unsupported"}

    async def operation(*, wait_forever=False):
        await asyncio.sleep(0.01)
        return {"wait_forever": wait_forever}

    result = asyncio.run(call_revit_operation(Client(), "test", operation, heartbeat_interval_seconds=0.001))
    assert result == {"wait_forever": True}


def test_long_operation_stops_as_unknown_at_explicit_timeout_without_retry():
    calls = []

    async def operation(*, wait_forever=False):
        calls.append(wait_forever)
        await asyncio.Event().wait()
        return {}

    with pytest.raises(RevitOperationUnknown, match="final Revit state is unknown"):
        asyncio.run(call_revit_operation(
            object(),
            "test",
            operation,
            heartbeat_interval_seconds=0.001,
            timeout_seconds=0.005,
        ))
    assert calls == [True]


def test_room_creation_skips_dwg_without_valid_grid_and_continues(tmp_path):
    model = tmp_path / "project_AR-B2.rvt"
    model.write_bytes(b"rvt")
    folder = tmp_path / "dwg"
    folder.mkdir()
    # 1 valid floor plan + 1 site plan (no grid)
    (folder / "floor-1F.dwg").write_bytes(b"dwg")
    (folder / "site-plan.dwg").write_bytes(b"dwg")

    class MixedGridRoomClient(RoomClient):
        async def dwg_revit_grid_data(self, path, *, wait_forever=False):
            self.calls.append(("grid", path, wait_forever))
            if "site-plan" in str(path):
                return {
                    "code": 200,
                    "msg": "返回dwg轴网跟rvt轴网数据",
                    "rvtGrid": {"AxisCode": None, "Begin_Position": None, "End_Position": None},
                    "dwgGrid": {"AxisCode": None, "Begin_Position": None, "End_Position": None},
                }
            return await super().dwg_revit_grid_data(path, wait_forever=wait_forever)

    client = MixedGridRoomClient()
    workflow = ArRoomCreationWorkflow(client)
    try:
        result = asyncio.run(workflow.run(str(model), str(folder), "AR"))
    finally:
        workflow.close()

    assert result["status"] == "completed"
    assert result["processed_drawing_count"] == 2
    assert result["skipped_drawing_count"] == 1
    assert "site-plan.dwg" in result["skipped_drawings"][0]["path"]
    assert result["named_room_count"] == 1

