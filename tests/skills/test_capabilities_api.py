from fastapi.testclient import TestClient

from app.api.capabilities import CapabilityManager, create_app


def test_revit_status_compatibility_endpoint_is_available():
    client = TestClient(create_app(CapabilityManager()))

    response = client.get("/api/v1/revit/status")

    assert response.status_code == 200
    assert response.json() == {
        "status": "available",
        "service": "capability_api",
        "revit_mcp_configured": True,
        "revit_plugin_checked": False,
    }


def test_skill_crud_and_execution_are_scoped_to_one_session():
    client = TestClient(create_app(CapabilityManager()))
    skill = {
        "id": "approved-workflow",
        "name": "Approved workflow",
        "steps": [
            {
                "id": "request-approval",
                "tool": "ask_human",
                "arguments": {"question": "Continue?"},
            }
        ],
        "policy": {"requires_approval": True},
    }

    response = client.post("/sessions/one/skills", json=skill)
    assert response.status_code == 201

    invalid_skill = {
        "id": "unknown-tool",
        "name": "Unknown tool",
        "steps": [{"id": "missing", "tool": "does_not_exist"}],
    }
    response = client.post("/sessions/one/skills/validate", json=invalid_skill)
    assert response.status_code == 400
    response = client.post("/sessions/one/skills", json=invalid_skill)
    assert response.status_code == 400

    response = client.post("/sessions/one/skills/approved-workflow/execute", json={})
    assert response.status_code == 200
    assert response.json()["status"] == "approval_required"

    second_session_skill_ids = {
        item["id"] for item in client.get("/sessions/two/skills").json()
    }
    assert second_session_skill_ids == set()
    assert "approved-workflow" not in second_session_skill_ids
    assert client.get("/sessions/one/tools").status_code == 200

    response = client.delete("/sessions/one/skills/approved-workflow")
    assert response.status_code == 204
    assert {
        item["id"] for item in client.get("/sessions/one/skills").json()
    } == second_session_skill_ids
