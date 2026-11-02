from __future__ import annotations

import io
import json
import urllib.error
from unittest.mock import Mock
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.errors import register_error_handlers
from app.api.routers import cicd_agent
from app.deps import require_login
from app.domain.permissions import ALL_ROLES, roles_for_tab


READ_PATHS = ["", "/summary", "/filter-options", "/101"]


@pytest.fixture
def gateway(monkeypatch):
    # Router-only ASGI app: no production DB, pollers, workers, or live backend.
    app = FastAPI()
    register_error_handlers(app)
    app.include_router(cicd_agent.router)
    user = {"username": "trusted-rm", "role": "RM", "display_name": "Trusted RM"}
    app.dependency_overrides[require_login] = lambda: user
    monkeypatch.setattr(cicd_agent.runtime_config, "section", lambda _section: {
        "BASE_URL": "http://agent.invalid", "TIMEOUT_SECONDS": "5",
        "REPRO_GATEWAY_TOKEN": "gateway-test-token",
    })
    response = Mock(status=200)
    response.read.return_value = b'{"id":101,"record_state":"trashed"}'
    response.__enter__ = Mock(return_value=response)
    response.__exit__ = Mock(return_value=False)
    opener = Mock()
    opener.open.return_value = response
    monkeypatch.setattr(cicd_agent.urllib.request, "build_opener", lambda *_args: opener)
    with TestClient(app) as client:
        yield client, user, opener, response


@pytest.mark.parametrize("role", ALL_ROLES)
@pytest.mark.parametrize("suffix", READ_PATHS)
def test_trash_reads_are_rm_only_and_send_trusted_headers(gateway, role, suffix):
    client, user, opener, _ = gateway
    user["role"] = role
    response = client.get(
        f"/api/cicd-agent/failures{suffix}?record_state=trashed&date_from=2026-01-01",
        headers={"Authorization": "Bearer browser-token", "X-Actor": "forged", "X-Actor-Role": "RM"},
    )
    assert response.status_code == (200 if role == "RM" else 403)
    if role != "RM":
        opener.open.assert_not_called()
        return
    request = opener.open.call_args.args[0]
    assert request.full_url == f"http://agent.invalid/api/v1/failures{suffix}?record_state=trashed&date_from=2026-01-01"
    headers = dict((key.lower(), value) for key, value in request.header_items())
    assert headers["authorization"] == "Bearer gateway-test-token"
    assert headers["x-actor"] == "trusted-rm"
    assert headers["x-actor-role"] == "RM"
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("role", ALL_ROLES)
@pytest.mark.parametrize("state", ["", "?record_state=active"])
def test_active_reads_keep_existing_page_permissions(gateway, role, state):
    client, user, opener, _ = gateway
    user["role"] = role
    for suffix in READ_PATHS:
        response = client.get(f"/api/cicd-agent/failures{suffix}{state}")
        assert response.status_code == (200 if role in roles_for_tab("jenkins-failures") else 403)
    if role not in roles_for_tab("jenkins-failures"):
        opener.open.assert_not_called()


@pytest.mark.parametrize("suffix", READ_PATHS)
@pytest.mark.parametrize("query", [
    "record_state=deleted", "record_state=all", "record_state=",
    "record_state=trashed&record_state=active", "record_state=active&record_state=trashed",
])
def test_invalid_or_ambiguous_record_state_never_reaches_backend(gateway, suffix, query):
    client, _, opener, _ = gateway
    assert client.get(f"/api/cicd-agent/failures{suffix}?{query}").status_code == 422
    opener.open.assert_not_called()


@pytest.mark.parametrize("role", ALL_ROLES)
@pytest.mark.parametrize("action,state", [("trash", "trashed"), ("restore", "active"), ("purge", "deleted")])
def test_lifecycle_actions_enforce_rm_and_inject_session_actor(gateway, role, action, state):
    client, user, opener, upstream = gateway
    user["role"] = role
    result = {"id": 101, "record_state": state, "record_state_updated_by": "trusted-rm",
              "record_state_updated_at": "2026-11-02T12:00:00", "record_state_reason": "duplicate"}
    upstream.read.return_value = json.dumps(result).encode()
    response = client.post(f"/api/cicd-agent/failures/101/{action}", json={
        "reason": "  duplicate  ", "actor": "forged", "actor_role": "RM",
    }, headers={"X-Actor": "forged", "X-Actor-Role": "RM", "Authorization": "Bearer forged"})
    assert response.status_code == (200 if role == "RM" else 403)
    if role != "RM":
        opener.open.assert_not_called()
        return
    request = opener.open.call_args.args[0]
    assert request.method == "POST"
    assert urlsplit(request.full_url).path == f"/api/v1/failures/101/{action}"
    assert json.loads(request.data) == {"actor": "trusted-rm", "actor_role": "RM", "reason": "duplicate"}
    headers = dict((key.lower(), value) for key, value in request.header_items())
    assert headers["authorization"] == "Bearer gateway-test-token"
    assert headers["x-actor"] == "trusted-rm"
    assert headers["x-actor-role"] == "RM"
    assert response.json() == result
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("body", [{"reason": "x" * 4001}, {"reason": None}])
def test_trash_requires_bounded_string_reason(gateway, body):
    client, _, opener, _ = gateway
    assert client.post("/api/cicd-agent/failures/101/trash", json=body).status_code == 422
    opener.open.assert_not_called()


@pytest.mark.parametrize("action", ["trash", "restore", "purge"])
@pytest.mark.parametrize("body", [{}, {"reason": ""}, {"reason": " \n\t "}])
def test_actions_allow_optional_reason(gateway, action, body):
    client, _, opener, _ = gateway
    assert client.post(f"/api/cicd-agent/failures/101/{action}", json=body).status_code == 200
    assert json.loads(opener.open.call_args.args[0].data)["reason"] == ""


@pytest.mark.parametrize("role", ALL_ROLES)
@pytest.mark.parametrize("action", ["trash", "restore", "purge"])
def test_batch_rm_only_fixed_ids_and_trusted_actor(gateway, role, action):
    client, user, opener, upstream = gateway
    user["role"] = role
    result = {"results": [{"id": 101, "success": True, "status_code": 200},
                          {"id": 102, "success": False, "status_code": 409, "error": "Live repro"}],
              "succeeded": 1, "failed": 1}
    upstream.read.return_value = json.dumps(result).encode()
    response = client.post("/api/cicd-agent/failures/batch/record-state", json={
        "record_ids": [101, 102], "action": action, "actor": "forged", "actor_role": "RM",
    })
    assert response.status_code == (200 if role == "RM" else 403)
    if role != "RM":
        opener.open.assert_not_called()
        return
    request = opener.open.call_args.args[0]
    assert urlsplit(request.full_url).path == "/api/v1/failures/batch/record-state"
    assert json.loads(request.data) == {"record_ids": [101, 102], "action": action, "reason": "",
                                        "actor": "trusted-rm", "actor_role": "RM"}
    assert response.json() == result
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("changes", [
    {"record_ids": []}, {"record_ids": [1, 1]}, {"record_ids": [0]}, {"record_ids": [True]},
    {"record_ids": ["1"]}, {"record_ids": list(range(1, 102))}, {"action": "delete"}, {"reason": "x" * 4001},
])
def test_batch_validates_before_forwarding(gateway, changes):
    client, _, opener, _ = gateway
    response = client.post("/api/cicd-agent/failures/batch/record-state",
                           json={"record_ids": [101], "action": "trash", **changes})
    assert response.status_code == 422
    opener.open.assert_not_called()


@pytest.mark.parametrize("status,detail", [(409, "Live repro task"), (404, "Record not found"), (403, "Gateway required")])
@pytest.mark.parametrize("action", ["trash", "restore", "purge"])
def test_backend_errors_keep_status_and_readable_error(gateway, status, detail, action):
    client, _, opener, _ = gateway
    opener.open.side_effect = urllib.error.HTTPError(
        "http://agent.invalid", status, detail, {}, io.BytesIO(json.dumps({"detail": detail}).encode()),
    )
    response = client.post(f"/api/cicd-agent/failures/101/{action}", json={"reason": "duplicate"})
    assert response.status_code == status
    assert response.json()["error"] == detail


def test_unavailable_backend_does_not_claim_lifecycle_success(gateway):
    client, _, opener, _ = gateway
    opener.open.side_effect = urllib.error.URLError("offline")
    response = client.post("/api/cicd-agent/failures/101/trash", json={"reason": "duplicate"})
    assert response.status_code == 502
    assert "offline" in response.json()["error"]


def test_filters_and_pagination_forward_unchanged(gateway):
    client, _, opener, _ = gateway
    response = client.get("/api/cicd-agent/failures", params={
        "record_state": "trashed", "page": 2, "page_size": 20, "code_owner": "another-owner",
        "normalized_stage": "build image", "date_from": "2025-01-01", "keyword": "a+b & c",
    })
    assert response.status_code == 200
    assert parse_qs(urlsplit(opener.open.call_args.args[0].full_url).query) == {
        "record_state": ["trashed"], "page": ["2"], "page_size": ["20"], "code_owner": ["another-owner"],
        "normalized_stage": ["build image"], "date_from": ["2025-01-01"], "keyword": ["a+b & c"],
    }
