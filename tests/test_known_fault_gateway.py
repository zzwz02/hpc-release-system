from unittest.mock import Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app.api.errors import register_error_handlers
from app.api.routers import cicd_agent
from app.deps import require_login
from app.domain.permissions import ALL_ROLES


@pytest.mark.parametrize("role", ALL_ROLES)
@pytest.mark.parametrize("method,suffix", [("GET", ""), ("POST", ""), ("PUT", "/1"),
    ("GET", "/1/history"), ("POST", "/1/preview"), ("POST", "/1/apply")])
def test_rule_gateway_uses_rm_session_not_browser_headers(monkeypatch, role, method, suffix):
    app = FastAPI(); register_error_handlers(app); app.include_router(cicd_agent.router)
    app.dependency_overrides[require_login] = lambda: {"username": "trusted", "role": role}
    upstream = Mock(return_value=(200, {"items": []}))
    monkeypatch.setattr(cicd_agent, "_request_agent_payload", upstream)
    with TestClient(app) as client:
        response = client.request(method, "/api/cicd-agent/known-fault-rules" + suffix,
            **({"json": {"config": {}}} if method != "GET" else {}),
            headers={"X-Actor": "forged", "X-Actor-Role": "RM"})
    assert response.status_code == (200 if role == "RM" else 403)
    if role != "RM":
        upstream.assert_not_called()
    else:
        assert upstream.call_args.kwargs["actor"] == {"username": "trusted", "role": "RM"}
        assert upstream.call_args.args[1] == "/api/v1/known-fault-rules" + suffix
