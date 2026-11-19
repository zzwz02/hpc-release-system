import hashlib
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.errors import AuthzError, register_error_handlers
from app.api.routers import api_tokens, apps, auth, mcp, release_gate, jira_agent
from app.db.connection import init_db
from app.deps import get_db
from app.repositories import api_tokens_repo, sessions_repo, users_repo
from app.services import api_token_service as tokens, mcp_service
from tests.conftest import seed_app


@pytest.fixture()
def client(temp_db):
    app = FastAPI()
    register_error_handlers(app)
    for router in (api_tokens.router, apps.router, auth.router, mcp.router, release_gate.router, jira_agent.router):
        app.include_router(router)
    app.dependency_overrides[get_db] = lambda: temp_db
    sessions_repo.create_session(temp_db, token="owner-cookie", username="owner_test", created_at="2026-11-18")
    sessions_repo.create_session(temp_db, token="rm-cookie", username="rm", created_at="2026-11-18")
    temp_db.commit()
    with TestClient(app) as result:
        result.cookies.set("hpc_session", "owner-cookie")
        yield result


def issue(client, name="Jenkins"):
    response = client.post("/api/account/tokens", json={"name": name})
    assert response.status_code == 200, response.text
    return response.json()["token"]


def bearer(token):
    return {"Authorization": "Bearer " + token}


def test_one_time_secret_hash_and_metadata_only(client, temp_db):
    created = issue(client)
    assert created["token"].startswith("rsapi_")
    raw = temp_db.execute("SELECT * FROM api_tokens").fetchone()
    assert raw["token_hash"] == hashlib.sha256(created["token"].encode()).hexdigest()
    assert created["token"] not in str(dict(raw))
    response = client.get("/api/account/tokens")
    assert response.headers["cache-control"] == "no-store"
    assert created["token"] not in response.text and "token_hash" not in response.text
    assert response.json()["ci_allowed"] is True
    client.cookies.clear()
    assert client.post("/api/ci/authenticate", headers=bearer(created["token"]), json={"operation": "failure_report"}).status_code == 200
    public = api_tokens_repo.list_tokens(temp_db, "owner_test")[0]
    assert public["last_used_at"] and public["last_operation"] == "failure_report"


def test_own_management_only_and_bearer_cannot_manage(client):
    created = issue(client)
    client.cookies.set("hpc_session", "rm-cookie")
    assert client.get("/api/account/tokens").json()["tokens"] == []
    assert client.post(f'/api/account/tokens/{created["id"]}/revoke').status_code == 404
    client.cookies.clear()
    assert client.get("/api/account/tokens", headers=bearer(created["token"])).status_code == 401
    assert client.post("/api/account/tokens", json={"name": "stolen"}, headers=bearer(created["token"])).status_code == 401


def test_rotation_overlap_revocation_and_password_independence(client, temp_db):
    first, second = issue(client), issue(client, "replacement")
    users_repo.update_password_hash(temp_db, "owner_test", password_hash="changed")
    temp_db.commit()
    for token in (first, second):
        assert tokens.authenticate(temp_db, "Bearer " + token["token"], "test_results")["username"] == "owner_test"
    assert client.post(f'/api/account/tokens/{first["id"]}/revoke').status_code == 200
    assert client.post("/api/ci/authenticate", headers=bearer(first["token"]), json={"operation": "test_results"}).status_code == 401
    assert client.post("/api/ci/authenticate", headers=bearer(second["token"]), json={"operation": "test_results"}).status_code == 200


@pytest.mark.parametrize("role,status", [("Owner", 200), ("SPD", 200), ("RM", 200), ("QA", 403), ("Guest", 403), ("Admin", 403)])
def test_current_role_controls_automation_not_username(client, temp_db, role, status):
    created = issue(client)
    users_repo.update_user_role(temp_db, "owner_test", role=role)
    temp_db.commit()
    assert client.post("/api/ci/authenticate", headers=bearer(created["token"]), json={"operation": "test_results"}).status_code == status


def test_deleted_account_and_recreated_name_cannot_reactivate(client, temp_db):
    created = issue(client)
    users_repo.delete_user(temp_db, "owner_test")
    users_repo.insert_user(temp_db, username="owner_test", password_hash="", role="Owner")
    temp_db.commit()
    assert client.post("/api/ci/authenticate", headers=bearer(created["token"]), json={"operation": "failure_report"}).status_code == 401


def test_unrelated_mcp_sessions_and_jira_unchanged(client, temp_db):
    user = users_repo.get_user(temp_db, "owner_test")
    original = mcp_service.create_token(temp_db, user, "existing MCP")
    created = issue(client)
    client.post(f'/api/account/tokens/{created["id"]}/revoke')
    assert mcp_service.authenticate(temp_db, "Bearer " + original["token"]).user["username"] == "owner_test"
    assert client.get("/api/me").status_code == 200
    created = issue(client)
    client.cookies.clear()
    # Platform tokens do not replace browser sessions on unrelated modules.
    assert client.get("/api/me", headers=bearer(created["token"])).json() == {"user": None}
    for path in ("/api/apps/detail?app_id=missing", "/api/mcp/tokens", "/api/cicd/release-gate/versions", "/api/jira-agent/conversations"):
        assert client.get(path, headers=bearer(created["token"])).status_code == 401
    assert client.post("/mcp", headers=bearer(created["token"]), json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}).status_code == 401
    with pytest.raises(PermissionError):
        tokens.authenticate(temp_db, "Bearer " + original["token"], "owner_lookup")


def test_lookup_all_apps_and_check_only_not_configuration_write(client, temp_db):
    seed_app(temp_db, git_url="hpc_example", owners=["another-owner"])
    created = issue(client)
    client.cookies.clear()
    headers = bearer(created["token"])
    response = client.get("/api/apps/owner-lookup", params={"git_url": "hpc_example", "branch": "maca"}, headers=headers)
    assert response.status_code == 200, response.text
    assert response.json()["matches"][0]["owners"] == ["another-owner"]
    checked = client.post("/api/cicd/release-gate/check", json={"build_type": "release", "maca_version": "3.10.2.11", "git_url": "hpc_example", "git_branch": "maca"}, headers=headers)
    assert checked.status_code == 200, checked.text
    assert checked.json()["reason"] == "version_unauthorized"
    assert client.post("/api/cicd/release-gate/versions", json={"maca_version": "3.10.2.11", "release_id": "fake"}, headers=headers).status_code == 401
    assert client.post("/api/cicd/release-gate/check", json={"build_type": "release", "maca_version": "3.10.2.11", "git_url": "hpc_example", "git_branch": "maca"}, headers=bearer("legacy")).status_code == 401


def test_bearer_errors_do_not_fallback_to_browser_cookie(client):
    assert client.get("/api/apps/owner-lookup?git_url=a&branch=b", headers=bearer("rsapi_invalid")).status_code == 401
    assert client.get("/api/apps/owner-lookup?git_url=a&branch=b").status_code == 200


def test_management_csrf_limits_validation_and_schema_upgrade(client, temp_db):
    assert client.post("/api/account/tokens", headers={"Origin": "https://evil.example"}, json={"name": "bad"}).status_code == 403
    for body in ({"name": ""}, {"name": "a" * 61}, {"name": "x", "username": "rm"}):
        assert client.post("/api/account/tokens", json=body).status_code == 422
    for i in range(5):
        issue(client, str(i))
    assert client.post("/api/account/tokens", json={"name": "six"}).status_code == 409
    init_db(temp_db)
    assert len(api_tokens_repo.list_tokens(temp_db, "owner_test")) == 5
    token = api_tokens_repo.list_tokens(temp_db, "owner_test")[0]
    assert client.post(f'/api/account/tokens/{token["id"]}/revoke').status_code == 200
    issue(client, "replacement")
    with pytest.raises(AuthzError):
        tokens.authenticate(temp_db, "Bearer anything", "admin.manage")


def test_delete_requires_revocation_and_preserves_safe_audit(client, temp_db):
    created = issue(client)
    path = f'/api/account/tokens/{created["id"]}'
    assert client.delete(path).status_code == 409
    assert tokens.authenticate(temp_db, "Bearer " + created["token"], "owner_lookup")
    assert client.post(path + "/revoke").status_code == 200
    response = client.delete(path)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert client.get("/api/account/tokens").json()["tokens"] == []
    assert temp_db.execute("SELECT COUNT(*) FROM api_tokens").fetchone()[0] == 0
    audit = [dict(row) for row in temp_db.execute("SELECT * FROM audit WHERE event LIKE 'api_token.%'")]
    assert {row["event"] for row in audit} == {"api_token.create", "api_token.revoke", "api_token.delete"}
    details = json.loads(next(row["detail"] for row in audit if row["event"] == "api_token.delete"))
    assert details["name"] == "Jenkins" and details["token_id"] == created["id"]
    assert details["last_operation"] == "owner_lookup" and details["revoked_at"]
    assert created["token"] not in str(audit) and "token_hash" not in str(audit)
    assert client.delete(path).status_code == 404
    replacement = issue(client)
    assert replacement["id"] > created["id"]
    with pytest.raises(PermissionError):
        tokens.authenticate(temp_db, "Bearer " + created["token"], "owner_lookup")


def test_delete_is_own_cookie_only_and_checks_origin(client):
    created = issue(client)
    path = f'/api/account/tokens/{created["id"]}'
    client.post(path + "/revoke")
    assert client.delete(path, headers={"Origin": "https://evil.example"}).status_code == 403
    client.cookies.set("hpc_session", "rm-cookie")
    assert client.delete(path).status_code == 404
    client.cookies.clear()
    assert client.delete(path, headers=bearer(created["token"])).status_code == 401
    client.cookies.set("hpc_session", "owner-cookie")
    assert len(client.get("/api/account/tokens").json()["tokens"]) == 1
