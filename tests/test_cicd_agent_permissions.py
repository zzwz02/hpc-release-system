from __future__ import annotations

import pytest
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from app.api.routers import cicd_agent
from app.deps import require_login
from app.domain.tab_permissions import ALL_ROLES, roles_for_tab
from app.main import create_app


def _client_for_role(monkeypatch: pytest.MonkeyPatch, role: str) -> TestClient:
    app = create_app()
    app.dependency_overrides[require_login] = lambda: {
        "username": role.lower(),
        "display_name": role,
        "role": role,
    }
    monkeypatch.setattr(
        cicd_agent,
        "_request_agent",
        lambda *_args, **_kwargs: JSONResponse({"ok": True}),
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
def test_cicd_assistant_api_role_gate(
    monkeypatch: pytest.MonkeyPatch,
    role: str,
) -> None:
    expected_status = 200 if role in roles_for_tab("cicd-assistant") else 403
    with _client_for_role(monkeypatch, role) as client:
        response = client.post(
            "/api/cicd-agent/failure-chat",
            json={"message": "why did this build fail?"},
        )
    assert response.status_code == expected_status
