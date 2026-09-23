from __future__ import annotations

import asyncio

from app.api.routers import cicd as cicd_router
from app.config import settings
from app.integrations import jira
from tests.test_cicd_ruling_bc import submit_create


def _write_jira_section(tmp_path, monkeypatch, base_url: str):
    config_path = tmp_path / "release_system.conf"
    config_path.write_text(
        f"[jira]\nJIRA_BASE_URL = {base_url}\nJIRA_TOKEN = secret-token\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(settings, "runtime_conf_path", config_path)


def test_browse_url_uses_jira_section_without_exposing_token(tmp_path, monkeypatch):
    _write_jira_section(tmp_path, monkeypatch, "https://jira.example.test/")

    assert jira.browse_url() == "https://jira.example.test/browse/"


def test_cicd_public_config_returns_only_browser_safe_jira_url(tmp_path, monkeypatch):
    _write_jira_section(tmp_path, monkeypatch, "https://jira.example.test")

    result = cicd_router.get_config(user={"username": "rm", "role": "RM"})

    assert result == {"jira_browse_url": "https://jira.example.test/browse/"}
    assert "secret-token" not in repr(result)


class _JsonRequest:
    def __init__(self, body: dict):
        self._body = body

    async def json(self) -> dict:
        return self._body


def _approve_with_auto_jira(conn, request_id: int) -> dict:
    body = {"request_id": request_id, "approval_mode": "dispatch_spd", "jira_auto_created": 1}
    return asyncio.run(cicd_router.post_approve(
        request=_JsonRequest(body), user={"username": "rm", "role": "RM"}, conn=conn,
    ))


def test_approve_reports_missing_jira_config_without_blocking(temp_db):
    req = submit_create(temp_db)

    result = _approve_with_auto_jira(temp_db, req["id"])

    assert result["request"]["status"] == "approved"
    assert result["jira_error"].startswith("未配置 JIRA")


def test_approve_reports_jira_create_failure_and_files_under_fixed_project(temp_db, tmp_path, monkeypatch):
    _write_jira_section(tmp_path, monkeypatch, "http://jira")
    created: dict = {}

    def fail_create(title, desc, *, jira_config):
        created["config"] = jira_config
        raise RuntimeError("component SPD_CICD not found")

    monkeypatch.setattr(jira, "create_issue", fail_create)
    req = submit_create(temp_db)

    result = _approve_with_auto_jira(temp_db, req["id"])

    assert result["request"]["status"] == "approved"
    assert result["jira_error"] == "component SPD_CICD not found"
    assert created["config"]["JIRA_BASE_URL"] == "http://jira"
    assert (jira.DISPATCH_PROJECT, jira.DISPATCH_COMPONENT) == ("SPD", "SPD_CICD")


def test_approve_success_has_no_jira_error(temp_db, tmp_path, monkeypatch):
    _write_jira_section(tmp_path, monkeypatch, "http://jira")
    monkeypatch.setattr(jira, "create_issue", lambda title, desc, *, jira_config: "SPD-9")
    req = submit_create(temp_db)

    result = _approve_with_auto_jira(temp_db, req["id"])

    assert "jira_error" not in result
    assert result["request"]["jira_id"] == "SPD-9"


def test_create_issue_sends_fixed_project_types_and_eta_fields(monkeypatch):
    sent: list[tuple[str, str, dict]] = []

    def fake_request(base, token, method, path, body=None):
        sent.append((method, path, body))
        return {"key": "SPD-1"}

    monkeypatch.setattr(jira, "_request", fake_request)
    cfg = {"JIRA_BASE_URL": "http://jira", "JIRA_TOKEN": "t", "JIRA_ASSIGNEE": "m00930"}

    assert jira.create_issue("[New] App", "desc", jira_config=cfg) == "SPD-1"
    jira.create_issue("[New] App", "desc", jira_config={**cfg, "JIRA_PARENT_ISSUE": "SPD-1535"})

    assert [(method, path) for method, path, _ in sent] == [("POST", "/rest/api/2/issue")] * 2
    task, subtask = (body["fields"] for _, _, body in sent)
    assert task["project"] == {"key": "SPD"}
    assert task["components"] == [{"name": "SPD_CICD"}]
    assert task["issuetype"] == {"name": "Task"}
    assert "parent" not in task
    assert task["assignee"] == {"name": "m00930"}
    assert task["customfield_10115"] == task["customfield_10116"]
    assert subtask["issuetype"] == {"name": "Sub-task"}
    assert subtask["parent"] == {"key": "SPD-1535"}
