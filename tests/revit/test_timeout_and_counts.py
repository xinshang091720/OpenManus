import asyncio
import os
import pytest

from app.revit.client import RevitApiClient
from app.revit.operations import call_revit_operation
from app.revit.workflow import RevitIfcAssignmentWorkflow


class CountingClient:
    def __init__(self):
        self.calls = []

    async def clear_parameters(self):
        return {"code": 200}

    async def one_click_identifier(self, standard_id, major):
        return {
            "code": 200,
            "data": [
                {"ComPonentId": "1", "ComTypeName": "c1", "ComCategoryName": "墙", "IdentID": None},
                {"ComPonentId": "2", "ComTypeName": "c2", "ComCategoryName": "墙", "IdentID": None},
            ],
        }

    async def get_level_ifc_ident(self, standard_id):
        return {"code": 200, "data": []}

    async def get_ifc_ident(self, standard_id, major):
        return {
            "code": 200,
            "data": [
                {"IdentName": "wall", "IdentId": "W1", "MarjorId": "112011", "ParentID": "0", "Grouping": "component"}
            ],
        }

    async def one_click_assignment(self, standard_id, data):
        # Return plugin message with total 34596 completed 34596
        return {"code": 200, "msg": "一键赋值操作完成，合计34596个完成34596个。"}


def test_call_revit_operation_unlimited_timeout_does_not_abort():
    async def sample_op():
        await asyncio.sleep(0.05)
        return {"code": 200, "data": "ok"}

    client = object()
    result = asyncio.run(
        call_revit_operation(
            client,
            "SampleOp",
            sample_op,
            timeout_seconds=0,
            heartbeat_interval_seconds=0.01,
        )
    )
    assert result["data"] == "ok"


def test_workflow_reports_total_completed_elements_when_plugin_returns_summary():
    client = CountingClient()

    async def reviewer(payloads):
        return {
            payload["group_key"]: {
                "identifier_id": payload["candidates"][0]["identifier_id"],
                "reason": "auto",
            }
            for payload in payloads
        }

    report = asyncio.run(
        RevitIfcAssignmentWorkflow(client, reviewer).run(
            r"C:\models\test.rvt", discipline="AR", clear_existing=True
        )
    )
    # Total assigned count should reflect the 34596 completed elements
    assert report["assigned_count"] == 34596
    assert report["total_elements"] == 34596
    assert report["already_matched_count"] == 34596 - 2
    # Supplementary assigned count by LLM/algorithm is 2
    assert report["supplementary_assigned_count"] == 2
    assert report["returned_unmatched_count"] == 2
