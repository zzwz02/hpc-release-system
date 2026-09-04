from __future__ import annotations

import pytest
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from app.api.routers import cicd_agent
from app.deps import require_login
from app.domain.permissions import ALL_ROLES, roles_for_tab
from app.main import create_app


def _client_for_role(monkeypatch: pytest.MonkeyPatch, role: str, responder=None) -> TestClient:
    app = create_app()
    app.dependency_overrides[require_login] = lambda: {
        "username": role.lower(),
        "display_name": role,
        "role": role,
    }
    monkeypatch.setattr(
        cicd_agent,
        "_request_agent",
        responder or (lambda *_args, **_kwargs: JSONResponse({"ok": True})),
    )
    return TestClient(app, raise_server_exceptions=False)


@pytest.mark.parametrize("role", ALL_ROLES)
def test_jenkins_failure_api_role_gate(
    monkeypatch: pytest.MonkeyPatch,
    role: str,
) -> None:
    paths = [
        "/api/cicd-agent/failures",
        "/api/cicd-agent/failures/summary",
        "/api/cicd-agent/failures/filter-options",
        "/api/cicd-agent/failures/1",
    ]
    expected_status = 200 if role in roles_for_tab("jenkins-failures") else 403
    with _client_for_role(monkeypatch, role) as client:
        for path in paths:
            assert client.get(path).status_code == expected_status


@pytest.mark.parametrize("role", ALL_ROLES)
def test_jenkins_failure_feedback_role_gate_and_actor_injection(
    monkeypatch: pytest.MonkeyPatch,
    role: str,
) -> None:
    captured: dict[str, object] = {}

    def fake_request_agent(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return JSONResponse({"record": {"id": 1}})

    expected_status = 200 if role in roles_for_tab("jenkins-failures") else 403
    with _client_for_role(monkeypatch, role, responder=fake_request_agent) as client:
        response = client.post(
            "/api/cicd-agent/failures/1/responsibility-feedback",
            json={
                "feedback_type": "owner_wrong",
                "reason": "不是我的责任",
                "suggested_owner_account": "owner02",
                "suggested_owner_role": "Code Owner",
                "actor_user": "spoofed",
                "actor_role": "Admin",
            },
        )

    assert response.status_code == expected_status
    if expected_status == 200:
        assert captured["args"] == (
            "POST",
            "/api/v1/failures/1/responsibility-feedback",
        )
        assert captured["kwargs"] == {
            "body": {
                "feedback_type": "owner_wrong",
                "reason": "不是我的责任",
                "suggested_owner_account": "owner02",
                "suggested_owner_role": "Code Owner",
                "evidence": None,
                "actor_user": role.lower(),
                "actor_role": role,
            }
        }


@pytest.mark.parametrize("role", ALL_ROLES)
def test_jenkins_failure_resolution_is_limited_to_rm(
    monkeypatch: pytest.MonkeyPatch,
    role: str,
) -> None:
    captured: dict[str, object] = {}

    def fake_request_agent(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return JSONResponse({"record": {"id": 1}})

    expected_status = 200 if role == "RM" else 403
    with _client_for_role(monkeypatch, role, responder=fake_request_agent) as client:
        response = client.post(
            "/api/cicd-agent/failures/1/responsibility-resolution",
            json={
                "action": "correct",
                "final_owner_account": "owner02",
                "final_owner_role": "Code Owner",
                "note": "改派给代码负责人",
                "actor_user": "spoofed",
                "actor_role": "Admin",
            },
        )

    assert response.status_code == expected_status
    if expected_status == 200:
        assert captured["args"] == (
            "POST",
            "/api/v1/failures/1/responsibility-resolution",
        )
        assert captured["kwargs"] == {
            "body": {
                "action": "correct",
                "final_owner_account": "owner02",
                "final_owner_role": "Code Owner",
                "final_reason_summary": None,
                "final_action_suggestion": None,
                "note": "改派给代码负责人",
                "actor_user": role.lower(),
                "actor_role": role,
            }
        }


@pytest.mark.parametrize("role", ALL_ROLES)
def test_cicd_assistant_api_role_gate(
    monkeypatch: pytest.MonkeyPatch,
    role: str,
) -> None:
    captured: dict[str, object] = {}

    def fake_request_agent(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return JSONResponse({"ok": True})

    expected_status = 200 if role in roles_for_tab("cicd-assistant") else 403
    with _client_for_role(monkeypatch, role, responder=fake_request_agent) as client:
        response = client.post(
            "/api/cicd-agent/cicd-assistant",
            json={"message": "query hpcg images", "user_id": "spoofed"},
        )
    assert response.status_code == expected_status
    if expected_status == 200:
        assert captured["args"] == ("POST", "/api/v1/cicd-assistant")
        assert captured["kwargs"] == {
            "body": {
                "message": "query hpcg images",
                "user_id": role.lower(),
            }
        }
