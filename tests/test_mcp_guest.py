"""Guest MCP allowlist, MC3 read scope, and identity changes at ticket use."""
from __future__ import annotations

from copy import deepcopy
import json
import re

import pytest

from app.config import settings
from app.db.connection import connect
from app.integrations import jira
from app.services import mcp_tools
from tests.test_mcp import ISSUE, call, connect_client, connect_verified, rpc, site  # noqa: F401

GUEST_TOOLS = {"jira_search", "jira_pull_issue", "jira_read_attachment", "skill_load"}


def change_role(username, role):
    conn = connect(settings.db_path)
    conn.execute("UPDATE users SET role=? WHERE username=?", (role, username))
    conn.commit()
    conn.close()


def download_path(result):
    return re.search(r"http://testserver(/api/mcp/downloads/[^\s\x27]+)", result["content"][0]["text"]).group(1)


def test_guest_can_connect_with_default_read_tools_and_identity_verification(site):
    client, _, token_for, _ = site
    token = token_for("guest", "Guest")
    session, initialized = connect_client(client, token)
    tools = rpc(client, token, "tools/list", session=session).json()["result"]["tools"]
    assert {tool["name"] for tool in tools} == GUEST_TOOLS | {"mcp_verify_identity"}
    assert all(tool["annotations"]["readOnlyHint"] for tool in tools)
    assert "MC3" in initialized["instructions"] and "默认只开放" in initialized["instructions"]
    info = client.get("/api/mcp/info").json()
    assert {tool["name"] for tool in info["tools"]} == GUEST_TOOLS
    assert info["default_jira_projects"] == ["MC3"] and not info["can_view_all"]
    skill = call(client, token, session, "skill_load", {"skill": "jira-format"})
    assert not skill["isError"] and skill["content"]


@pytest.mark.parametrize("name", ["jira_add_comment", "jira_upload_attachment", "jenkins_search_failures",
                                 "jenkins_get_failure", "jenkins_get_build_errors", "jenkins_list_jobs", "jenkins_list_builds"])
def test_guest_cannot_call_other_tools_by_name(site, monkeypatch, name):
    client, _, token_for, calls = site
    token = token_for("guest", "Guest")
    session, _ = connect_client(client, token)
    monkeypatch.setattr(jira, "get_issue", lambda *_a: pytest.fail("forbidden tool must not execute"))
    response = rpc(client, token, "tools/call", {"name": name, "arguments": {"issue_key": "MC3-1", "body": "x", "file_path": "x"}}, session=session)
    assert response.json()["error"]["code"] == -32602
    assert calls["comments"] == []


def test_future_tools_are_not_implicitly_granted_to_guest():
    tool = mcp_tools.Tool(name="future_tool", category="test", title="Future", description="", input_schema={}, handler=lambda *_: None)
    assert not tool.allowed("Guest")
    assert tool.allowed("Owner")


@pytest.mark.parametrize("key", ["PHS-1", "CCPM-1", "MC30-1"])
@pytest.mark.parametrize("name", ["jira_search", "jira_pull_issue", "jira_read_attachment"])
def test_guest_other_project_reads_are_blocked_before_jira(site, monkeypatch, name, key):
    client, _, token_for, _ = site
    token = token_for("guest", "Guest")
    session, _ = connect_client(client, token)
    def forbidden(*_a, **_kw):
        pytest.fail("must reject outside MC3 before any upstream call")
    for function in ("search_issues", "get_issue", "get_issue_snapshot", "download_attachment"):
        monkeypatch.setattr(jira, function, forbidden)
    result = call(client, token, session, name, {"query": key, "issue_key": key, "attachment_id": "77"} if name != "jira_read_attachment" else {"issue_key": key, "attachment_id": "77"})
    assert result["isError"] and "仅允许读取 MC3" in result["content"][0]["text"]


def test_guest_can_read_mc3_details_and_text_attachment(site):
    client, _, token_for, _ = site
    token = token_for("guest", "Guest")
    session, _ = connect_client(client, token)
    result = call(client, token, session, "jira_pull_issue", {"issue_key": "mc3-1"})
    assert not result["isError"] and "GPU hang" in result["content"][1]["text"]
    attachment = call(client, token, session, "jira_read_attachment", {"issue_key": "mc3-1", "attachment_id": "77"})
    assert not attachment["isError"]
    assert json.loads(attachment["content"][0]["text"])["content"] == "abc"


@pytest.mark.parametrize("query,expected", [
    ("MC3-1", "project = MC3 AND (key = MC3-1)"),
    ("status = Open OR project = PHS ORDER BY updated DESC", "project = MC3 AND (status = Open OR project = PHS) ORDER BY updated DESC"),
    ('summary ~ "ORDER BY token" ORDER BY key ASC', 'project = MC3 AND (summary ~ "ORDER BY token") ORDER BY key ASC'),
    ('(status = Open OR status = Closed) AND text ~ "a\\\"b"', 'project = MC3 AND ((status = Open OR status = Closed) AND text ~ "a\\\"b")'),
    ('ORDER BY "Target end" DESC, key ASC', 'project = MC3 ORDER BY "Target end" DESC, key ASC'),
])
def test_guest_search_scopes_complete_expression_and_preserves_sort(site, monkeypatch, query, expected):
    client, _, token_for, _ = site
    token = token_for("guest", "Guest")
    session, _ = connect_client(client, token)
    captured = []
    def search(jql, **_kw):
        captured.append(jql)
        return {"issues": [deepcopy(ISSUE)], "total": 1}
    monkeypatch.setattr(jira, "search_issues", search)
    result = call(client, token, session, "jira_search", {"query": query})
    assert not result["isError"] and "MC3-1" in result["content"][0]["text"]
    assert captured == [expected]


def test_guest_empty_search_retains_my_open_issues_filter_inside_mc3(site, monkeypatch):
    client, _, token_for, _ = site
    token = token_for("guest", "Guest")
    session, _ = connect_client(client, token)
    def search(jql, **_kw):
        assert jql.startswith('project = MC3 AND (assignee = "guest" AND status != Closed) ORDER BY updated DESC')
        return {"issues": [], "total": 0}
    monkeypatch.setattr(jira, "search_issues", search)
    assert not call(client, token, session, "jira_search", {})["isError"]


@pytest.mark.parametrize("query", [
    "status = Open) OR project = PHS", "(status = Open", 'summary ~ "not closed',
    "status = Open ORDER BY key DESC OR project = PHS", "status = Open ORDER BY key ORDER BY updated",
    "status = Open -- comment", "status = Open /* comment */", "status = Open; project = PHS",
])
def test_guest_malformed_jql_cannot_escape_project_scope(site, monkeypatch, query):
    client, _, token_for, _ = site
    token = token_for("guest", "Guest")
    session, _ = connect_client(client, token)
    monkeypatch.setattr(jira, "search_issues", lambda *_a, **_kw: pytest.fail("unsafe query must not reach JIRA"))
    assert call(client, token, session, "jira_search", {"query": query})["isError"]


@pytest.mark.parametrize("name", ["jira_search", "jira_pull_issue", "jira_read_attachment"])
def test_guest_rejects_moved_or_out_of_scope_upstream_results(site, monkeypatch, name):
    client, _, token_for, _ = site
    token = token_for("guest", "Guest")
    session, _ = connect_client(client, token)
    outside = {**deepcopy(ISSUE), "key": "PHS-1", "summary": "must-not-leak", "project": "PHS"}
    monkeypatch.setattr(jira, "search_issues", lambda *_a, **_kw: {"issues": [outside], "total": 1})
    monkeypatch.setattr(jira, "get_issue_snapshot", lambda *_a, **_kw: outside)
    monkeypatch.setattr(jira, "get_issue", lambda *_a, **_kw: outside)
    monkeypatch.setattr(jira, "download_attachment", lambda *_a, **_kw: pytest.fail("no outside downloads"))
    args = {"query": "MC3-1"} if name == "jira_search" else {"issue_key": "MC3-1"}
    if name == "jira_read_attachment":
        args["attachment_id"] = "77"
    result = call(client, token, session, name, args)
    assert result["isError"] and "must-not-leak" not in json.dumps(result)


def test_guest_cannot_use_another_issues_attachment_id(site, monkeypatch):
    client, _, token_for, _ = site
    token = token_for("guest", "Guest")
    session, _ = connect_client(client, token)
    monkeypatch.setattr(jira, "download_attachment", lambda *_a, **_kw: pytest.fail("attachment must belong to the allowed issue"))
    assert call(client, token, session, "jira_read_attachment", {"issue_key": "MC3-1", "attachment_id": "other-issue-file"})["isError"]


def test_guest_can_download_mc3_but_cannot_reuse_a_ticket_after_jira_access_revocation(site, monkeypatch):
    client, _, token_for, _ = site
    token = token_for("guest", "Guest")
    session, _ = connect_client(client, token)
    pull = call(client, token, session, "jira_pull_issue", {"issue_key": "MC3-1", "save_to_local": True})
    assert client.get(download_path(pull)).content == b"abc"
    owner = token_for("alice", "Owner")
    owner_session = connect_verified(client, owner)
    foreign = call(client, owner, owner_session, "jira_pull_issue", {"issue_key": "PHS-1", "save_to_local": True})
    path = download_path(foreign)
    change_role("alice", "Guest")
    monkeypatch.setattr(jira, "account_can_browse", lambda username, **_: False)
    monkeypatch.setattr(jira, "download_attachment", lambda *_a, **_kw: pytest.fail("ticket must recheck project scope"))
    assert client.get(path).status_code == 403
    listed = rpc(client, owner, "tools/list", session=owner_session).json()["result"]["tools"]
    assert {tool["name"] for tool in listed} == GUEST_TOOLS | {"mcp_verify_identity"}


def test_guest_assignee_cannot_reuse_previously_issued_upload_ticket(site, monkeypatch):
    client, _, token_for, _ = site
    owner = token_for("alice", "Owner")
    session = connect_verified(client, owner)
    upload = call(client, owner, session, "jira_upload_attachment", {"issue_key": "MC3-1", "file_path": "x.log"})
    path = re.search(r"http://testserver(/api/mcp/uploads/[^\s\x27]+)", upload["content"][0]["text"]).group(1)
    change_role("alice", "Guest")
    monkeypatch.setattr(jira, "get_issue", lambda *_a: pytest.fail("guest writes must be rejected even if assignee"))
    monkeypatch.setattr(jira, "upload_agent_attachment", lambda *_a: pytest.fail("guest cannot upload"))
    assert client.put(path, content=b"x").status_code == 403


@pytest.mark.parametrize("moved", [True, False])
def test_guest_download_ticket_rechecks_issue_moves_and_attachment_membership(site, monkeypatch, moved):
    client, _, token_for, _ = site
    token = token_for("guest", "Guest")
    session, _ = connect_client(client, token)
    pull = call(client, token, session, "jira_pull_issue", {"issue_key": "MC3-1", "save_to_local": True})
    path = download_path(pull)
    issue = {**deepcopy(ISSUE), "key": "PHS-1"} if moved else {**deepcopy(ISSUE), "attachments": []}
    monkeypatch.setattr(jira, "get_issue", lambda *_a: issue)
    monkeypatch.setattr(jira, "download_attachment", lambda *_a, **_kw: pytest.fail("ticket no longer authorizes this file"))
    assert client.get(path).status_code == 403


def test_guest_statistics_and_logs_remain_personal(site):
    client, current, token_for, _ = site
    owner = token_for("alice", "Owner")
    session, _ = connect_client(client, owner)
    call(client, owner, session, "jira_pull_issue", {"issue_key": "PHS-1"})
    guest = token_for("guest", "Guest")
    guest_session, _ = connect_client(client, guest)
    call(client, guest, guest_session, "jira_pull_issue", {"issue_key": "MC3-1"})
    assert current["role"] == "Guest"
    stats = client.get("/api/mcp/stats", params={"username": "alice"}).json()
    assert stats["scope"] == "mine" and {row["username"] for row in stats["by_user"]} == {"guest"}
    entries = client.get("/api/mcp/logs", params={"username": "alice"}).json()["items"]
    assert entries and {row["username"] for row in entries} == {"guest"}
