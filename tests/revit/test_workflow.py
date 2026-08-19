import asyncio

import pytest

from app.revit.client import (
    REVIT_OPERATION_TIMEOUT_SECONDS,
    RevitApiClient,
    RevitApiError,
)
from app.revit.matching import group_unmatched_elements, rank_candidates, route_for_element
from app.revit.workflow import (
    RevitIfcAssignmentWorkflow,
    WorkflowError,
    resolve_major,
)


WALL = "\u5899"
LEVEL = "\u6807\u9ad8"
BUILDING = "\u5efa\u7b51"
ELECTRICAL = "\u7535\u6c14"


class RecordingClient:
    def __init__(self):
        self.calls, self.assignments = [], []

    async def open_revit_file(self, path): self.calls.append(("open", path)); return {"code": 200}
    async def clear_parameters(self): self.calls.append(("clear",)); return {"code": 200}
    async def one_click_identifier(self, standard_id, major):
        self.calls.append(("identify", standard_id, major))
        return {"code": 200, "data": [
            {"ComPonentId": "1", "ComTypeName": "brick", "ComFamlyName": "basic", "ComCategoryName": WALL, "IdentID": None},
            {"ComPonentId": "2", "ComTypeName": "brick", "ComFamlyName": "basic", "ComCategoryName": WALL, "IdentID": None},
            {"ComPonentId": "3", "ComTypeName": "standard", "ComFamlyName": None, "ComCategoryName": LEVEL, "IdentID": None},
            {"ComPonentId": "4", "ComTypeName": "door", "ComFamlyName": "door", "ComCategoryName": "door", "IdentID": "old"},
        ]}
    async def get_level_ifc_ident(self, standard_id):
        self.calls.append(("level", standard_id)); return {"code": 200, "data": [{"IdentName": "standard", "IdentId": "L1", "MarjorId": "112011", "ParentID": "0", "Grouping": "space"}]}
    async def get_ifc_ident(self, standard_id, major):
        self.calls.append(("major", standard_id, major)); return {"code": 200, "data": [{"IdentName": "wall", "IdentId": "W1", "MarjorId": "112011", "ParentID": "0", "Grouping": "component"}]}
    async def one_click_assignment(self, standard_id, data):
        self.calls.append(("assign", standard_id)); self.assignments = data; return {"code": 200, "msg": "assigned"}
    async def save_as(self, folder): self.calls.append(("save", folder)); return {"code": 200, "msg": "saved"}


def test_end_to_end_workflow_assigns_groups_with_explicit_discipline(tmp_path):
    client = RecordingClient()

    async def reviewer(payloads):
        return {payload["group_key"]: {"identifier_id": payload["candidates"][0]["identifier_id"], "reason": "reviewed"} for payload in payloads}

    report = asyncio.run(RevitIfcAssignmentWorkflow(client, reviewer).run(
        r"C:\models\project_without_code.rvt", discipline="AR", clear_existing=True
    ))
    assert report["marjor_name"] == BUILDING
    assert report["assigned_count"] == 3
    assert report["clear_existing"] is True
    assert report["identify_attempts"] == 1
    assert "audit_report_path" not in report
    assert len(report["groups"][0]["candidates"]) == 1
    assert [item[0] for item in client.calls] == ["clear", "identify", "level", "major", "assign"]
    assert {item["ComPonentId"] for item in client.assignments} == {"1", "2", "3"}
    assert all(item["IdentID"] and item["IdentName"] and item["MarjorID"] for item in client.assignments)


def test_ifc_assignment_clears_existing_identifiers_even_if_legacy_flag_is_false():
    client = RecordingClient()

    async def reviewer(payloads):
        return {
            payload["group_key"]: {
                "identifier_id": payload["candidates"][0]["identifier_id"],
                "reason": "reviewed",
            }
            for payload in payloads
        }

    report = asyncio.run(
        RevitIfcAssignmentWorkflow(client, reviewer).run(
            r"C:\models\project.rvt", discipline="建筑", clear_existing=False
        )
    )
    assert report["clear_existing"] is True
    assert client.calls[0] == ("clear",)


def test_identification_failure_is_not_retried(monkeypatch, tmp_path):
    class EventuallyReadyClient(RecordingClient):
        def __init__(self):
            super().__init__()
            self.identify_calls = 0

        async def one_click_identifier(self, standard_id, major):
            self.identify_calls += 1
            if self.identify_calls == 1:
                raise RevitApiError("object reference not set", endpoint="/OneClickIdentifier", plugin_code=500)
            return await super().one_click_identifier(standard_id, major)

    client = EventuallyReadyClient()

    async def reviewer(payloads):
        return {payload["group_key"]: {"identifier_id": payload["candidates"][0]["identifier_id"], "reason": "reviewed"} for payload in payloads}

    with pytest.raises(WorkflowError, match="identify"):
        asyncio.run(RevitIfcAssignmentWorkflow(client, reviewer).run(
            r"C:\models\project.rvt", discipline="AR"
        ))
    assert client.identify_calls == 1
    assert [call[0] for call in client.calls] == ["clear"]


def test_identification_chinese_null_reference_is_not_retried(monkeypatch, tmp_path):
    class EventuallyReadyClient(RecordingClient):
        def __init__(self):
            super().__init__()
            self.identify_calls = 0

        async def one_click_identifier(self, standard_id, major):
            self.identify_calls += 1
            if self.identify_calls == 1:
                raise RevitApiError("执行异常：未将对象引用设置到对象的实例", endpoint="/OneClickIdentifier", plugin_code=500)
            return await super().one_click_identifier(standard_id, major)

    client = EventuallyReadyClient()

    async def reviewer(payloads):
        return {payload["group_key"]: {"identifier_id": payload["candidates"][0]["identifier_id"], "reason": "reviewed"} for payload in payloads}

    with pytest.raises(WorkflowError, match="identify"):
        asyncio.run(RevitIfcAssignmentWorkflow(client, reviewer).run(
            r"C:\models\project.rvt", discipline="AR"
        ))
    assert client.identify_calls == 1
    assert [call[0] for call in client.calls] == ["clear"]


def test_grouping_routing_and_low_score_fallback_are_deterministic():
    elements = [{"ComPonentId": "1", "ComTypeName": "", "ComFamlyName": "basic", "ComCategoryName": WALL, "IdentID": None}, {"ComPonentId": "2", "ComTypeName": "standard", "ComFamlyName": None, "ComCategoryName": LEVEL, "IdentID": None}, {"ComPonentId": "3", "ComCategoryName": WALL, "IdentID": "existing"}]
    assert set(group_unmatched_elements(elements)) == {f"basic > {WALL}", f"standard > {LEVEL}"}
    assert route_for_element(elements[1]) == "level"
    ranked = rank_candidates(elements[0], [{"IdentName": "unrelated", "IdentId": "1", "MarjorId": "x", "ParentID": "0", "Grouping": "component"}])
    assert (ranked[0]["identifier_id"], ranked[0]["score"]) == ("1", 0)


@pytest.mark.parametrize(
    ("category", "keyword"),
    [
        ("模型", "建筑"), ("房间", "空间"), ("面积", "空间"),
        ("管道系统", "系统"), ("风管系统", "系统"), (WALL, "构件"),
    ],
)
def test_grouping_filter_uses_documented_category_mapping(category, keyword):
    from app.revit.matching import filter_candidates_by_grouping

    candidates = [
        {"IdentId": "wanted", "Grouping": f"深圳{keyword}标识"},
        {"IdentId": "other", "Grouping": "深圳其他标识"},
    ]
    filtered, returned_keyword, used_fallback = filter_candidates_by_grouping(
        {"ComCategoryName": category}, candidates
    )
    assert returned_keyword == keyword
    assert used_fallback is False
    assert [item["IdentId"] for item in filtered] == ["wanted"]


def test_major_uses_explicit_discipline_instead_of_filename():
    assert resolve_major("EL") == ELECTRICAL
    assert resolve_major("建筑") == BUILDING
    with pytest.raises(WorkflowError, match="请先由用户确认"): resolve_major("")


def test_workflow_normalizes_missing_standard_id_to_shenzhen_default(tmp_path):
    client = RecordingClient()

    async def reviewer(payloads):
        return {payload["group_key"]: {"identifier_id": payload["candidates"][0]["identifier_id"], "reason": "reviewed"} for payload in payloads}

    report = asyncio.run(
        RevitIfcAssignmentWorkflow(client, reviewer).run(
            r"C:\models\project.rvt", discipline="AR", standard_id=None
        )
    )
    assert report["standard_id"] == 109003
    assert ("identify", 109003, BUILDING) in client.calls


def test_workflow_reviews_multiple_groups_in_one_bounded_batch(tmp_path):
    client = RecordingClient()
    seen_batches = []

    async def reviewer(payloads):
        seen_batches.append([payload["group_key"] for payload in payloads])
        return {
            payload["group_key"]: {
                "identifier_id": payload["candidates"][0]["identifier_id"],
                "reason": "batch reviewed",
            }
            for payload in payloads
        }

    report = asyncio.run(
        RevitIfcAssignmentWorkflow(client, reviewer, review_batch_size=3).run(
            r"C:\models\project.rvt", discipline="AR"
        )
    )
    assert len(seen_batches) == 1
    assert len(seen_batches[0]) == 2
    assert report["review_batch_size"] == 3
    assert report["review_batch_count"] == 1


def test_client_uses_all_documented_paths_and_fields():
    class CapturingClient(RevitApiClient):
        async def _post(self, path, payload=None, **kwargs): return {"path": path, "payload": payload, "code": 200}

    async def verify():
        client = CapturingClient()
        assert (await client.open_revit_file("a.rvt"))["payload"] == {"rvtFilePath": "a.rvt"}
        assert (await client.one_click_identifier(109003, BUILDING))["path"] == "/OneClickIdentifier"
        assert (await client.get_level_ifc_ident(109003))["path"] == "/GetLevelIfcIdent"
        assert (await client.get_ifc_ident(109003, BUILDING))["path"] == "/GetIfcIdent"
        assert (await client.one_click_assignment(109003, [{"ComPonentId": "1", "IdentID": "2"}]))["path"] == "/OneClickAssignment"
        assert (await client.save_as("C:/out"))["payload"] == {"folderPath": "C:/out"}
        assert (await client.batch_create_rooms())["path"] == "/BatchCreateRooms"

    asyncio.run(verify())


def test_client_bounds_confirmed_long_operations_at_two_hours():
    class CapturingClient(RevitApiClient):
        def __init__(self):
            super().__init__()
            self.timeout_values = []

        async def _request(self, method, path, payload=None, **kwargs):
            self.timeout_values.append(kwargs.get("timeout_seconds"))
            return {"code": 200}

    async def verify():
        client = CapturingClient()
        await client.batch_create_rooms(wait_forever=True)
        await client.update_room_name([], wait_forever=True)
        assert client.timeout_values == [
            REVIT_OPERATION_TIMEOUT_SECONDS,
            REVIT_OPERATION_TIMEOUT_SECONDS,
        ]

    asyncio.run(verify())


def test_save_as_creates_result_folder_when_target_has_same_model_name(tmp_path):
    from app.tool.revit import RevitSaveAs

    source = tmp_path / "project-AR.rvt"
    source.write_text("source", encoding="utf-8")
    (tmp_path / source.name).write_text("existing", encoding="utf-8")
    selected = RevitSaveAs._prepare_target_folder(str(tmp_path), str(source))

    assert selected == tmp_path / "result"
    assert selected.is_dir()


def test_save_as_creates_result_folder_when_legacy_output_has_no_extension(tmp_path):
    from app.revit.project_delivery import prepare_save_folder

    source = tmp_path / "project-AR.rvt"
    source.write_text("source", encoding="utf-8")
    (tmp_path / source.stem).write_text("legacy result", encoding="utf-8")

    selected = prepare_save_folder(str(tmp_path), str(source))

    assert selected == tmp_path / "result"
    assert selected.is_dir()


def test_assignment_tool_does_not_require_or_return_audit_path():
    from app.tool.revit import RevitAssignIfcIdentifiers

    schema = RevitAssignIfcIdentifiers().parameters
    assert "audit_folder_path" not in schema["properties"]
    assert schema["required"] == ["rvt_file_path", "discipline"]


def test_end_to_end_tool_returns_compact_report_only():
    from app.tool.revit import RevitAssignIfcIdentifiers

    report = {
        "standard_id": 109003,
        "groups": [{
            "group_key": "wall", "count": 2, "route": "major",
            "selected": {"identifier_id": "1"}, "review_status": "accepted",
            "program_low_confidence": False, "grouping_keyword": "构件",
            "grouping_filter_fallback": False,
            "candidates": [{"identifier_id": str(index)} for index in range(20)],
        }],
    }
    summary = RevitAssignIfcIdentifiers._agent_summary(report)
    assert summary["details_in_audit_report"] is False
    assert "candidates" not in summary["groups"][0]


def test_workflow_reports_the_actual_failing_stage_and_endpoint():
    class FailingClient(RecordingClient):
        async def one_click_identifier(self, standard_id, major):
            raise RevitApiError("bad request", endpoint="/OneClickIdentifier", http_status=500, plugin_code=400)

    with pytest.raises(WorkflowError, match="identify") as error:
        asyncio.run(RevitIfcAssignmentWorkflow(FailingClient()).run(
            r"C:\models\project.rvt", discipline="AR"
        ))

    assert "endpoint=/OneClickIdentifier" in str(error.value)
