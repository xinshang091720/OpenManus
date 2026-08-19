import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from app.workflows.revit_ifc_assignment import RevitWorkflow


class _NoopLock:
    @asynccontextmanager
    async def hold(self):
        yield

    def close(self):
        pass


class _Client:
    async def ensure_plugin_ready(self, *, wait_forever=False):
        return {"code": 200}

    async def save_as(self, folder, *, wait_forever=False):
        # Simulate the legacy endpoint accepting a folder but preserving no
        # extension in the generated filename.
        Path(folder, "project_AR").write_bytes(b"saved")
        return {"code": 200, "msg": "saved"}


def test_ifc_assignment_reports_verified_extensionless_saveas_path(tmp_path, monkeypatch):
    source = tmp_path / "project_AR.rvt"
    source.write_bytes(b"source")

    class _CoreWorkflow:
        def __init__(self, client):
            self.client = client

        async def run(self, **_kwargs):
            return {"status": "completed", "assigned_count": 1}

    monkeypatch.setattr("app.workflows.revit_ifc_assignment.RevitProcessLock", _NoopLock)
    monkeypatch.setattr("app.workflows.revit_ifc_assignment.CoreWorkflow", _CoreWorkflow)
    workflow = RevitWorkflow(client=_Client())
    result = asyncio.run(workflow.run(str(source), "AR"))

    expected = tmp_path / "ifc-assigned" / source.stem
    assert result["saved_to"] == str(expected.parent)
    assert result["saved_model_path"] == str(expected)
    assert result["saved_model_path_status"] == "verified"


def test_ifc_assignment_does_not_invent_saveas_path_when_no_file_appears(tmp_path, monkeypatch):
    source = tmp_path / "project_AR.rvt"
    source.write_bytes(b"source")

    class _CoreWorkflow:
        def __init__(self, client):
            self.client = client

        async def run(self, **_kwargs):
            return {"status": "completed", "assigned_count": 1}

    class _NoOutputClient(_Client):
        async def save_as(self, folder, *, wait_forever=False):
            return {"code": 200, "msg": "saved"}

    monkeypatch.setattr("app.workflows.revit_ifc_assignment.RevitProcessLock", _NoopLock)
    monkeypatch.setattr("app.workflows.revit_ifc_assignment.CoreWorkflow", _CoreWorkflow)
    result = asyncio.run(RevitWorkflow(client=_NoOutputClient()).run(str(source), "AR"))

    assert result["saved_to"] == str(tmp_path / "ifc-assigned")
    assert result["saved_model_path"] is None
    assert result["saved_model_path_status"] == "unverified"
