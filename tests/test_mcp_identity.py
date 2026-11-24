"""Session identity and named Jira access through the MCP HTTP endpoints."""

import json
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit

import pytest

from app.integrations import jira
from app.integrations.jira import account_can_browse, visible_project_keys
from app.services import mcp_protocol
from tests.test_mcp import ISSUE, call, confirmable_call, connect_confirming, rpc, sse_messages
from tests.test_mcp import site as site
from tests.test_mcp_guest import download_path
from tests.test_mcp_jenkins_access import CICD, PDE, listed, membership, set_identity


def verify(client, token, session, response):
    ask, result = confirmable_call(
        client, token, session, "mcp_verify_identity", {}, {"result": response}
    )
    assert ask["params"]["requestedSchema"] == {
        "type": "object",
        "required": ["username"],
        "properties": {"username": {"type": "string", "title": "本人域账号用户名"}},
    }
    return result["result"]


@pytest.mark.parametrize(
    ("response", "reason", "message"),
    [
        ({"action": "decline"}, "decline", "客户端返回拒绝"),
        ({"action": "cancel"}, "cancel", "客户端返回取消"),
        ({"action": "unexpected"}, "no_answer", "客户端未返回有效的域账号验证答复"),
        ({"action": "accept"}, "username_mismatch", "用户名为空"),
        ({"action": "accept", "content": {"username": " "}}, "username_mismatch", "用户名为空"),
        ({"action": "accept", "content": {"username": "carol"}}, "username_mismatch", "不匹配"),
    ],
)
def test_rejected_or_unmatched_identity_preserves_default_access_for_rm(
    site, monkeypatch, response, reason, message
):
    client, _, token_for, calls = site
    token = token_for("rm", "RM")
    session = connect_confirming(client, token)
    monkeypatch.setattr(
        jira, "account_can_browse", lambda *_a, **_kw: pytest.fail("unverified session")
    )
    result = verify(client, token, session, response)
    assert result["isError"] and result["structuredContent"] == {
        "identity_verified": False,
        "reason": reason,
    }
    text = result["content"][0]["text"]
    assert message in text
    assert text.endswith("可继续使用默认只读权限，JIRA 限 MC3。")
    if reason in ("decline", "cancel", "no_answer"):
        assert "客户端审批策略也可能自动拒绝表单" in text
        assert "Approve for me" not in text
    assert not call(client, token, session, "jira_pull_issue", {"issue_key": "MC3-1"})["isError"]
    assert call(client, token, session, "jira_pull_issue", {"issue_key": "PHS-1"})["isError"]
    for name in ("jira_add_comment", "jira_upload_attachment"):
        assert call(client, token, session, name, {})["structuredContent"] == {
            "status": "identity_required"
        }
    assert calls["comments"] == []


@pytest.mark.parametrize(("action", "label"), [("decline", "拒绝"), ("cancel", "取消")])
def test_codex_verification_refusal_explains_full_access_and_default_scope(site, action, label):
    client, _, token_for, _ = site
    token = token_for("alice", "Owner")
    session = connect_confirming(client, token, "codex-mcp-client")
    result = verify(client, token, session, {"action": action})
    text = result["content"][0]["text"]
    assert result["structuredContent"] == {"identity_verified": False, "reason": action}
    assert text.startswith(f"客户端返回{label}，域账号验证未完成。")
    assert "Full Access" in text and "Approve for me" in text
    assert text.endswith("可继续使用默认只读权限，JIRA 限 MC3。")


def test_verification_is_session_bound_and_preserves_jenkins_matrix(site, monkeypatch):
    client, _, token_for, _ = site
    membership(monkeypatch, ["dl.sw_runtime"])
    token = token_for("guest", "Guest")
    session = connect_confirming(client, token)
    denied = rpc(
        client,
        token,
        "tools/call",
        {
            "name": "mcp_verify_identity",
            "arguments": {"username": "guest"},
        },
        session=session,
    ).json()
    assert denied["error"]["code"] == -32602
    updated = verify(
        client, token, session, {"action": "accept", "content": {"username": "  GUEST  "}}
    )
    assert updated["structuredContent"]["identity_verified"] is True
    assert not call(client, token, session, "mcp_verify_identity", {})["isError"]
    assert listed(client, token, session) & (PDE | CICD) == CICD
    assert not call(client, token, session, "jira_pull_issue", {"issue_key": "PHS-1"})["isError"]

    second_session = connect_confirming(client, token)
    assert call(client, token, second_session, "jira_pull_issue", {"issue_key": "PHS-1"})["isError"]
    set_identity("guest", "Guest", "local")
    assert (
        call(client, token, session, "mcp_verify_identity", {})["structuredContent"]["reason"]
        == "domain_account_required"
    )
    assert call(client, token, session, "jira_pull_issue", {"issue_key": "PHS-1"})["isError"]


def test_waiting_for_identity_times_out_with_default_permissions(site, monkeypatch):
    client, _, token_for, _ = site
    token = token_for("alice", "Owner")
    session = connect_confirming(client, token)
    monkeypatch.setattr(mcp_protocol, "CONFIRM_TIMEOUT_SECONDS", 0.02)
    response = rpc(client, token, "tools/call", {"name": "mcp_verify_identity"}, session=session)
    ask, reply = sse_messages(response)
    assert ask["method"] == "elicitation/create"
    assert reply["result"]["structuredContent"] == {"identity_verified": False, "reason": "timeout"}
    assert reply["result"]["content"][0]["text"].startswith("等待域账号验证超时")
    assert not call(client, token, session, "jira_pull_issue", {"issue_key": "MC3-1"})["isError"]
    assert call(client, token, session, "jira_upload_attachment", {})["isError"]


def test_verified_search_scopes_jql_to_projects_for_exact_jira_account(site, monkeypatch):
    client, _, token_for, _ = site
    token = token_for("alice", "Owner")
    session = connect_confirming(client, token)
    assert not verify(
        client, token, session, {"action": "accept", "content": {"username": "alice"}}
    )["isError"]
    monkeypatch.setattr(jira, "account_can_browse", account_can_browse)
    monkeypatch.setattr(jira, "visible_project_keys", visible_project_keys)
    monkeypatch.setattr(
        jira,
        "_require_config",
        lambda: {"JIRA_BASE_URL": "https://jira.example", "JIRA_TOKEN": "service-token"},
    )
    checks = []
    searched = []

    def request(_base, token, method, path):
        assert token == "service-token" and method == "GET"
        url = urlsplit(path)
        if url.path == "/rest/api/2/project":
            return [{"key": key} for key in ("SECRET", "PHS", "MC3")]
        assert url.path == "/rest/api/2/user/viewissue/search"
        query = parse_qs(url.query)
        assert query["username"] == ["alice"]
        key = (query.get("projectKey") or query["issueKey"])[0]
        checks.append(key)
        if key == "SECRET":
            return [{"name": "alice-other", "active": True}, {"name": "alice", "active": False}]
        if key == "PHS-77":
            return []
        return [{"name": "ALICE", "active": True}]

    def search(jql, **page):
        searched.append((jql, page))
        return {"issues": [{**ISSUE, "key": "PHS-1"}], "total": 51}

    monkeypatch.setattr(jira, "_request", request)
    monkeypatch.setattr(jira, "search_issues", search)
    result = call(
        client,
        token,
        session,
        "jira_search",
        {
            "query": "status = Open OR project = SECRET ORDER BY key ASC",
            "start_at": 50,
        },
    )
    assert not result["isError"] and "共 51 条" in result["content"][0]["text"]
    assert set(checks) == {"MC3", "PHS", "SECRET"}
    assert searched == [
        (
            "project in ('MC3', 'PHS') AND level IS EMPTY AND "
            "(status = Open OR project = SECRET) ORDER BY key ASC",
            {"max_results": 50, "start_at": 50},
        )
    ]

    monkeypatch.setattr(
        jira,
        "get_issue_snapshot",
        lambda *_a: pytest.fail("must check visibility before reading Jira"),
    )
    for key in ("SECRET-1", "PHS-77"):
        result = call(client, token, session, "jira_pull_issue", {"issue_key": key})
        assert result["isError"] and "无权浏览" in result["content"][0]["text"]

    def forbidden(*_args):
        raise HTTPError("https://jira.example", 403, "Browse Users required", None, None)

    monkeypatch.setattr(jira, "_request", forbidden)
    result = call(client, token, session, "jira_pull_issue", {"issue_key": "PHS-1"})
    assert result["isError"] and "暂时无法核验" in result["content"][0]["text"]


def test_download_from_verified_session_stops_when_session_ends(site):
    client, _, token_for, _ = site
    token = token_for("alice", "Owner")
    session = connect_confirming(client, token)
    assert not verify(
        client, token, session, {"action": "accept", "content": {"username": "alice"}}
    )["isError"]
    pull = call(
        client, token, session, "jira_pull_issue", {"issue_key": "PHS-1", "save_to_local": True}
    )
    path = download_path(pull)
    assert client.get(path).content == b"abc"
    assert (
        client.delete(
            "/mcp", headers={"Authorization": f"Bearer {token}", "Mcp-Session-Id": session}
        ).status_code
        == 204
    )
    assert client.get(path).status_code == 403


def test_verified_jira_details_omit_restricted_comments_and_their_references(site, monkeypatch):
    client, _, token_for, _ = site
    token = token_for("alice", "Owner")
    session = connect_confirming(client, token)
    assert not verify(
        client, token, session, {"action": "accept", "content": {"username": "alice"}}
    )["isError"]
    restricted = {
        "id": "10",
        "author": "Owner",
        "created": "2026-11-02",
        "body": "Restricted https://jira.example/browse/SECRET-123",
        "visibility": {"type": "role", "value": "Developers"},
    }
    monkeypatch.setattr(
        jira,
        "get_issue_snapshot",
        lambda key: {
            **ISSUE,
            "key": key,
            "comments": [*ISSUE["comments"], restricted],
            "raw_fields": {"comment": {"comments": [restricted]}},
        },
    )
    result = call(client, token, session, "jira_pull_issue", {"issue_key": "PHS-1"})
    assert not result["isError"] and "log attached" in result["content"][1]["text"]
    assert "SECRET-123" not in json.dumps(result)
