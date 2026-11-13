"""MCP server: token auth, JSON-RPC protocol, JIRA tools and usage logging."""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.db.connection import connect
from app.deps import require_login
from app.integrations import jira
from app.main import create_app
from app.services import mcp_protocol, mcp_tools
from app.services.mcp_tools import Tool, ToolLog, ToolResult

ISSUE = {
    "key": "MC3-1", "url": "https://jira.example/browse/MC3-1", "summary": "GPU hang",
    "issue_type": "Bug", "status": "Open", "priority": "P1", "project": "MC3",
    "assignee": {"name": "alice", "display_name": "Alice"}, "reporter": {"name": "bob", "display_name": "Bob"},
    "components": ["HPC"], "labels": [], "description": "steps", "created": "2026-11-01", "updated": "2026-11-02",
    "comments": [{"id": "9", "author": "Bob", "created": "2026-11-02", "body": "log attached"}],
    "attachments": [{"id": "77", "filename": "run/../x.log", "size": 3, "mime_type": "text/plain",
                     "content_url": "https://jira.example/secure/attachment/77/x.log"}],
    "custom_fields": {"customfield_1": {"name": "Chip", "value": {"value": "C500"}}},
    "acquisition": {"status": "complete"},
}


@pytest.fixture()
def site(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "db_path", tmp_path / "main.db")
    conn = connect(settings.db_path)
    for username, role in (("alice", "Owner"), ("carol", "Owner"), ("rm", "RM"), ("guest", "Guest")):
        conn.execute("INSERT OR REPLACE INTO users(username, password_hash, role, display_name) VALUES (?, '', ?, ?)",
                     (username, role, username.title()))
    conn.commit()
    conn.close()
    current = {"username": "alice", "role": "Owner"}
    app = create_app()
    app.dependency_overrides[require_login] = lambda: {**current, "display_name": current["username"].title()}
    client = TestClient(app, raise_server_exceptions=False)
    calls: dict[str, list] = {"comments": []}
    monkeypatch.setattr(jira, "get_issue_snapshot", lambda key, **_: {**ISSUE, "key": key})
    monkeypatch.setattr(jira, "get_issue", lambda key: {**ISSUE, "key": key})
    monkeypatch.setattr(jira, "download_attachment", lambda url, **_: b"abc")

    def add_comment(key, body):
        calls["comments"].append((key, body))
        return "1001"

    monkeypatch.setattr(jira, "add_comment", add_comment)

    def token_for(username: str, role: str) -> str:
        current.update(username=username, role=role)
        return client.post("/api/mcp/tokens", json={"name": "t"}).json()["token"]["token"]

    return client, current, token_for, calls


def rpc(client, token, method, params=None, *, msg_id=1, session=""):
    headers = {"Authorization": f"Bearer {token}"}
    if session:
        headers["Mcp-Session-Id"] = session
    body = {"jsonrpc": "2.0", "method": method, "params": params or {}}
    if msg_id is not None:
        body["id"] = msg_id
    return client.post("/mcp", json=body, headers=headers)


def connect_client(client, token, name="claude-code"):
    response = rpc(client, token, "initialize", {
        "protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": name, "version": "2.0"},
    })
    assert response.status_code == 200
    return response.headers["mcp-session-id"], response.json()["result"]


def call(client, token, session, name, arguments):
    response = rpc(client, token, "tools/call", {"name": name, "arguments": arguments}, session=session)
    assert response.status_code == 200
    return response.json()["result"]


def logs(client, **params):
    return client.get("/api/mcp/logs", params=params).json()["items"]


def test_requires_valid_token_and_role(site):
    client, current, token_for, _ = site
    assert client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"}).status_code == 401
    assert rpc(client, "rsmcp_wrong", "ping").status_code == 401

    token = token_for("alice", "Owner")
    assert rpc(client, token, "ping").json()["result"] == {}

    # A role change applies to existing tokens immediately.
    conn = connect(settings.db_path)
    conn.execute("UPDATE users SET role = 'Guest' WHERE username = 'alice'")
    conn.commit()
    conn.close()
    assert rpc(client, token, "ping").status_code == 403

    # Guests cannot even create a token.
    current.update(username="guest", role="Guest")
    assert client.post("/api/mcp/tokens", json={"name": "x"}).status_code == 403


def test_revoked_token_and_foreign_revoke(site):
    client, current, token_for, _ = site
    token = token_for("alice", "Owner")
    token_id = client.get("/api/mcp/tokens").json()["tokens"][0]["id"]
    assert "token" not in client.get("/api/mcp/tokens").json()["tokens"][0]

    current.update(username="carol", role="Owner")
    assert client.post(f"/api/mcp/tokens/{token_id}/revoke").status_code == 404

    current.update(username="alice", role="Owner")
    assert client.post(f"/api/mcp/tokens/{token_id}/revoke").json()["token"]["revoked_at"]
    assert rpc(client, token, "ping").status_code == 401


def test_initialize_session_and_tool_list(site):
    client, _, token_for, _ = site
    token = token_for("alice", "Owner")
    session, result = connect_client(client, token)
    assert result["protocolVersion"] == "2025-06-18"
    assert result["capabilities"] == {"tools": {"listChanged": False}}
    assert rpc(client, token, "notifications/initialized", msg_id=None, session=session).status_code == 202

    tools = rpc(client, token, "tools/list", session=session).json()["result"]["tools"]
    assert {tool["name"] for tool in tools} == {"jira_search", "jira_pull_issue", "jira_add_comment",
                                                "jira_upload_attachment", "skill_load",
                                                "jenkins_search_failures", "jenkins_get_failure", "jenkins_get_build_errors", "jenkins_list_jobs", "jenkins_list_builds", "jira_read_attachment"}
    assert rpc(client, token, "nope", session=session).json()["error"]["code"] == -32601

    [entry] = logs(client)
    assert (entry["kind"], entry["tool"], entry["client_name"]) == ("connection", "initialize", "claude-code")


def test_pull_issue_returns_markdown_and_working_download_links(site):
    client, _, token_for, _ = site
    token = token_for("alice", "Owner")
    session, _ = connect_client(client, token, "codex-mcp-client")
    result = call(client, token, session, "jira_pull_issue", {"issue_key": "mc3-1"})
    assert result["isError"] is False
    guide, markdown = (block["text"] for block in result["content"][:2])
    assert "jira/MC3-1/issue.md" in guide
    assert markdown.startswith("# MC3-1 GPU hang") and "log attached" in markdown and "Chip：C500" in markdown
    assert "[run/../x.log|https://jira.example/secure/attachment/77/run%2F..%2Fx.log]" in markdown
    # The attachment name cannot escape the target directory.
    assert "jira/MC3-1/attachments/run_.._x.log" in guide
    url = guide.split("http://testserver", 1)[1].split()[0].strip("'")
    download = client.get(url)
    assert download.status_code == 200 and download.content == b"abc"
    assert client.get("/api/mcp/downloads/forged").status_code == 404

    pull, fetched = logs(client, kind="tool")[0], logs(client, kind="download")[0]
    assert (pull["category"], pull["target_type"], pull["target"], pull["status"]) == ("jira", "jira_issue", "MC3-1", "ok")
    assert json.loads(pull["extra_json"])["attachments"] == 1
    assert (fetched["target"], fetched["client_name"]) == ("MC3-1", "codex-mcp-client")


def test_comment_limited_to_assignee_or_rm_and_attributed(site):
    client, _, token_for, calls = site
    carol = token_for("carol", "Owner")
    session = connect_confirming(client, carol)
    denied = confirmable_call(client, carol, session, "jira_add_comment", {"issue_key": "MC3-1", "body": "hi"},
                              {"result": {"action": "accept", "content": {"username": "carol"}}})[-1]["result"]
    assert denied["isError"] and "assignee" in denied["content"][0]["text"]
    assert calls["comments"] == []

    alice = token_for("alice", "Owner")
    session = connect_confirming(client, alice, "claude-code")
    posted = confirmable_call(client, alice, session, "jira_add_comment", {"issue_key": "MC3-1", "body": "root cause: X"},
                              {"result": {"action": "accept", "content": {"username": "alice"}}})[-1]["result"]
    assert not posted["isError"] and "focusedCommentId=1001" in posted["content"][0]["text"]
    [(key, body)] = calls["comments"]
    assert key == "MC3-1" and body.startswith("root cause: X") and "Alice（alice）" in body and "claude-code" in body

    rm = token_for("rm", "RM")
    session = connect_confirming(client, rm)
    assert not confirmable_call(client, rm, session, "jira_add_comment", {"issue_key": "MC3-1", "body": "rm"},
                              {"result": {"action": "accept", "content": {"username": "rm"}}})[-1]["result"]["isError"]


def test_tool_error_is_logged_with_target(site):
    client, _, token_for, _ = site
    token = token_for("alice", "Owner")
    session, _ = connect_client(client, token)
    bad = call(client, token, session, "jira_pull_issue", {"issue_key": "not a key"})
    assert bad["isError"]
    assert rpc(client, token, "tools/call", {"name": "missing", "arguments": {}}, session=session).json()["error"]
    entry = logs(client, status="error")[0]
    assert entry["tool"] == "jira_pull_issue" and entry["error"]


def test_skill_load_serves_the_method_and_its_pages_to_every_role(site):
    client, _, token_for, _ = site
    assert all(mcp_tools.get_tool("skill_load").allowed(role) for role in ("RM", "Owner", "QA", "SPD"))
    for username, role in (("alice", "Owner"), ("rm", "RM")):
        token = token_for(username, role)
        session, _ = connect_client(client, token)
        main = call(client, token, session, "skill_load", {"skill": "cu-bridge"})
        assert not main["isError"] and "cu-bridge" in main["content"][0]["text"]

    page = call(client, token, session, "skill_load", {"skill": "cu-bridge", "page": "diagnosis"})
    assert not page["isError"] and "references/diagnosis.md" in page["content"][0]["text"]
    for bad in ({"skill": "no-such-skill"}, {"skill": "cu-bridge", "page": "../../../etc/passwd"}):
        assert call(client, token, session, "skill_load", bad)["isError"]

    entry = logs(client, category="skill", status="ok")[0]
    assert (entry["target_type"], entry["target"]) == ("skill", "cu-bridge/diagnosis")
    assert json.loads(entry["extra_json"])["page"] == "diagnosis"
    # a rejected page is still recorded, with what was asked for
    refused = logs(client, category="skill", status="error")[0]
    assert refused["target"] == "cu-bridge/../../../etc/passwd" and refused["error"]


def test_stats_scope_mine_vs_rm(site):
    client, current, token_for, _ = site
    for username, role in (("alice", "Owner"), ("carol", "Owner")):
        token = token_for(username, role)
        session, _ = connect_client(client, token)
        call(client, token, session, "jira_search", {"query": "MC3-1"})

    current.update(username="alice", role="Owner")
    mine = client.get("/api/mcp/stats", params={"username": "carol"}).json()
    assert mine["scope"] == "mine" and [row["username"] for row in mine["by_user"]] == ["alice"]
    assert {row["username"] for row in logs(client, username="carol")} == {"alice"}

    current.update(username="rm", role="RM")
    everyone = client.get("/api/mcp/stats").json()
    assert everyone["scope"] == "all" and everyone["totals"]["users"] == 2
    assert {(row["category"], row["tool"]) for row in everyone["by_tool"]} >= {("jira", "jira_search")}
    assert client.get("/api/mcp/stats", params={"username": "carol"}).json()["totals"]["users"] == 1


def test_new_tool_gets_logging_without_schema_change(site, monkeypatch):
    """The extension point: a registered tool is listed, called and logged generically."""
    client, _, token_for, _ = site

    def handler(ctx, args):
        return ToolResult.text("done", log=ToolLog(target_type="app", target="lammps",
                                                   summary="1 app", extra={"rows": 1}))

    tool = Tool(name="demo_tool", category="release", title="Demo", description="d",
                input_schema={"type": "object"}, handler=handler, roles=("RM",),
                log_args=lambda args: {**args, "secret": "***"})
    monkeypatch.setitem(mcp_tools._REGISTRY, "demo_tool", tool)

    owner = token_for("alice", "Owner")
    session, _ = connect_client(client, owner)
    names = [t["name"] for t in rpc(client, owner, "tools/list", session=session).json()["result"]["tools"]]
    assert "demo_tool" not in names  # role-limited tool

    rm = token_for("rm", "RM")
    session, _ = connect_client(client, rm)
    assert call(client, rm, session, "demo_tool", {"secret": "pw", "long": "x" * 900})["content"][0]["text"] == "done"
    entry = logs(client, category="release")[0]
    args = json.loads(entry["args_json"])
    assert args["secret"] == "***" and len(args["long"]) == mcp_tools.LOG_ARG_MAX_CHARS + 1
    assert (entry["target_type"], entry["target"], json.loads(entry["extra_json"])) == ("app", "lammps", {"rows": 1})


def test_download_links_use_the_address_the_client_connected_to(site, tmp_path, monkeypatch):
    """PUBLIC_BASE_URL may name another instance; the ticket only exists in this one."""
    client, _, token_for, _ = site
    conf = tmp_path / "release_system.conf"
    conf.write_text("[site]\nPUBLIC_BASE_URL = https://public.example\n", encoding="utf-8")
    monkeypatch.setattr(settings, "runtime_conf_path", conf)
    token = token_for("alice", "Owner")
    session, _ = connect_client(client, token)
    guide = call(client, token, session, "jira_pull_issue", {"issue_key": "MC3-1"})["content"][0]["text"]
    assert "http://testserver/api/mcp/downloads/" in guide and "public.example" not in guide


def test_stats_and_logs_break_down_by_token(site):
    client, current, token_for, _ = site
    laptop = token_for("alice", "Owner")
    session, _ = connect_client(client, laptop, "claude-code")
    call(client, laptop, session, "jira_search", {"query": "MC3-1"})
    current.update(username="alice", role="Owner")
    server = client.post("/api/mcp/tokens", json={"name": "server"}).json()["token"]["token"]
    for _ in range(2):
        session, _ = connect_client(client, server, "codex-mcp-client")
        call(client, server, session, "jira_search", {"query": "MC3-1"})

    stats = client.get("/api/mcp/stats").json()
    by_name = {row["token_name"]: row for row in stats["by_token"]}
    assert by_name["t"]["calls"] == 2 and by_name["t"]["revoked_at"]
    assert by_name["server"]["calls"] == 4 and not by_name["server"]["revoked_at"]
    assert by_name["server"]["sessions"] == 2 and by_name["server"]["clients"] == "codex-mcp-client"
    assert by_name["server"]["token_prefix"].startswith("rsmcp_") and "token" not in by_name["server"]
    server_id = by_name["server"]["token_id"]

    assert client.get("/api/mcp/stats", params={"token_id": server_id}).json()["totals"]["calls"] == 4
    entries = logs(client, token_id=server_id)
    assert len(entries) == 4 and {entry["token_name"] for entry in entries} == {"server"}

    # Another user's token id filters to nothing for a non-RM viewer.
    current.update(username="carol", role="Owner")
    assert client.get("/api/mcp/stats", params={"token_id": server_id}).json()["totals"]["calls"] == 0


def test_one_active_token_per_user(site):
    client, current, token_for, _ = site
    first = token_for("alice", "Owner")
    second = token_for("alice", "Owner")
    assert rpc(client, first, "ping").status_code == 401
    assert rpc(client, second, "ping").status_code == 200
    listing = client.get("/api/mcp/tokens").json()
    assert listing["active"]["token_prefix"] == second[:12]
    assert sum(1 for token in listing["tokens"] if not token["revoked_at"]) == 1

    # An older database may still hold several unrevoked rows: only the newest works.
    conn = connect(settings.db_path)
    conn.execute("UPDATE mcp_tokens SET revoked_at = ''")
    conn.commit()
    conn.close()
    assert rpc(client, first, "ping").status_code == 401
    assert rpc(client, second, "ping").status_code == 200


def test_browser_get_on_endpoint_redirects_to_page(site):
    client, _, _, _ = site
    page = client.get("/mcp", headers={"Accept": "text/html,application/xhtml+xml"}, follow_redirects=False)
    assert page.status_code == 307 and page.headers["location"] == "/mcp-console"
    assert client.get("/mcp", headers={"Accept": "text/event-stream"}).status_code == 405


def _set_password(username: str, password: str) -> None:
    from app.domain import authn
    conn = connect(settings.db_path)
    conn.execute("UPDATE users SET password_hash = ? WHERE username = ?", (authn.hash_password(password), username))
    conn.commit()
    conn.close()


def account(client, action, password="pw-alice", *, plain=False, username="alice"):
    headers = {"Accept": "text/plain"} if plain else {}
    return client.post("/api/mcp/account", headers=headers,
                       data={"username": username, "password": password, "action": action, "name": "box1"})


def test_account_status_and_regenerate_without_website_login(site):
    client, current, _, _ = site
    _set_password("alice", "pw-alice")
    client.app.dependency_overrides.clear()  # no website session at all

    assert account(client, "status").json()["active"] is None
    assert account(client, "status", plain=True).text == "none\n"

    created = client.post("/api/mcp/account", json={"username": "alice", "password": "pw-alice",
                                                     "action": "regenerate", "name": "box1"}).json()
    token = created["token"]
    assert token.startswith("rsmcp_") and created["endpoint"] == "http://testserver/mcp"
    entry = created["config"]["claude_json"]["mcpServers"]["release-system"]
    assert entry == {"type": "http", "url": "http://testserver/mcp", "headers": {"Authorization": f"Bearer {token}"}}
    assert f'Bearer {token}' in created["config"]["codex_toml"]
    session, _ = connect_client(client, token, "codex-mcp-client")

    status = account(client, "status", plain=True).text.rstrip("\n").split("\t")
    assert status[:3] == ["active", token[:12], "box1"] and status[5] == "codex-mcp-client"
    assert token not in account(client, "status").text  # status never returns the secret

    again = account(client, "regenerate", plain=True)
    assert again.status_code == 200 and again.text.strip().startswith("rsmcp_")
    assert rpc(client, token, "ping").status_code == 401
    assert rpc(client, again.text.strip(), "ping").status_code == 200
    assert account(client, "explode").status_code == 400

    current.update(username="alice", role="Owner")
    client.app.dependency_overrides[require_login] = lambda: {**current, "display_name": "Alice"}
    assert [entry["tool"] for entry in logs(client, kind="token")] == ["regenerate_token", "regenerate_token"]


def test_account_rejects_bad_password_role_and_throttles(site):
    client, _, _, _ = site
    _set_password("alice", "pw-alice")
    _set_password("guest", "pw-guest")
    client.app.dependency_overrides.clear()
    guest = account(client, "regenerate", "pw-guest", plain=True, username="guest")
    assert guest.status_code == 403 and "无权使用 MCP" in guest.text

    for _ in range(5):
        assert account(client, "status", "bad").status_code == 401
    assert account(client, "status").status_code == 429


def test_setup_script_points_at_this_site_and_is_valid_bash(site, tmp_path):
    import subprocess
    client, _, _, _ = site
    response = client.get("/api/mcp/setup.sh")
    assert response.status_code == 200 and "__BASE_URL__" not in response.text
    assert 'BASE_URL="http://testserver"' in response.text
    harnesses = json.loads((Path(__file__).resolve().parents[1] / "shared" / "mcp_harnesses.json")
                           .read_text(encoding="utf-8"))["harnesses"]
    assert harnesses and all(item["url"] in response.text for item in harnesses)
    assert "__HARNESS_LINKS__" not in response.text
    script = tmp_path / "setup.sh"
    script.write_text(response.text, encoding="utf-8")
    assert subprocess.run(["bash", "-n", str(script)], capture_output=True).returncode == 0
    from app.api.routers import mcp as mcp_router
    assert mcp_router._SAFE_BASE_URL.match("https://hpc-release.swlab.metax-tech.com")
    assert mcp_router._SAFE_BASE_URL.match("http://127.0.0.1:3577")
    for bad in ('http://x";rm -rf ~;"', "http://x$(id)", "http://x`id`", "ftp://x"):
        assert not mcp_router._SAFE_BASE_URL.match(bad)


def _upload_url(text: str) -> str:
    return text.split("http://testserver", 1)[1].split()[0].strip("'")


def test_upload_attachment_via_one_time_link(site, monkeypatch):
    from app.services import mcp_service
    client, _, token_for, _ = site
    uploads: list = []
    outcomes = iter([OSError("network down"), "5001"])

    def upload(key, filename, data):
        outcome = next(outcomes)
        if isinstance(outcome, Exception):
            raise outcome
        uploads.append((key, filename, data))
        return outcome

    monkeypatch.setattr(jira, "upload_agent_attachment", upload)
    monkeypatch.setattr(jira, "browse_url", lambda: "https://jira.example/browse/")
    token = token_for("alice", "Owner")
    session, _ = connect_client(client, token)
    result = call(client, token, session, "jira_upload_attachment",
                  {"issue_key": "MC3-1", "file_path": "/home/a/run dir/fix.patch"})
    assert not result["isError"]
    text = result["content"][0]["text"]
    assert "--data-binary @'/home/a/run dir/fix.patch'" in text
    url = _upload_url(text)

    failed = client.put(url, content=b"diff --git")
    assert failed.status_code == 502 and "network down" in failed.text  # ticket survives a failure
    done = client.put(url, content=b"diff --git")
    assert done.status_code == 200 and "附件 ID 5001" in done.text
    assert "[fix.patch|https://jira.example/secure/attachment/5001/fix.patch]" in done.text
    assert "!fix.patch!" not in done.text  # embed hint only for images
    assert uploads == [("MC3-1", "fix.patch", b"diff --git")]
    assert client.put(url, content=b"again").status_code == 404  # one use

    entries = logs(client, kind="upload")
    assert [entry["status"] for entry in entries] == ["ok", "error"]
    assert json.loads(entries[0]["extra_json"]) == {"attachment_id": "5001", "bytes": 10, "filename": "fix.patch"}

    monkeypatch.setattr(mcp_service, "UPLOAD_MAX_BYTES", 4)
    result = call(client, token, session, "jira_upload_attachment", {"issue_key": "MC3-1", "file_path": "big.log"})
    assert client.put(_upload_url(result["content"][0]["text"]), content=b"12345").status_code == 413


def test_upload_attachment_requires_assignee_at_link_and_upload_time(site, monkeypatch):
    client, _, token_for, _ = site
    monkeypatch.setattr(jira, "upload_agent_attachment", lambda *args: pytest.fail("must not upload"))
    carol = token_for("carol", "Owner")
    session, _ = connect_client(client, carol)
    denied = call(client, carol, session, "jira_upload_attachment", {"issue_key": "MC3-1", "file_path": "a.log"})
    assert denied["isError"] and "上传附件" in denied["content"][0]["text"]

    alice = token_for("alice", "Owner")
    session, _ = connect_client(client, alice)
    url = _upload_url(call(client, alice, session, "jira_upload_attachment",
                           {"issue_key": "MC3-1", "file_path": "a.log"})["content"][0]["text"])
    reassigned = {**ISSUE, "assignee": {"name": "carol", "display_name": "Carol"}}
    monkeypatch.setattr(jira, "get_issue", lambda key: {**reassigned, "key": key})
    rejected = client.put(url, content=b"x")
    assert rejected.status_code == 403 and "assignee 是 carol" in rejected.text


def test_image_attachment_reference_offers_embed():
    from app.services.mcp_jira_tools import attachment_reference
    text = attachment_reference("shot [1].png", "9", "https://jira.example")
    assert "[shot (1).png|https://jira.example/secure/attachment/9/shot%20%5B1%5D.png]" in text
    assert "!shot [1].png!" in text


@pytest.mark.parametrize('change,expected', [('revoke', 401), ('rotate', 401), ('delete', 401),
                                             ('guest', 403), ('owner', 403)])
def test_attachment_tickets_recheck_current_identity(site, monkeypatch, change, expected):
    client, _, token_for, _ = site
    token = token_for('rm', 'RM')
    session, _ = connect_client(client, token)
    upload = call(client, token, session, 'jira_upload_attachment',
                  {'issue_key': 'MC3-1', 'file_path': 'a.log'})
    upload_url = _upload_url(upload['content'][0]['text'])
    pull = call(client, token, session, 'jira_pull_issue', {'issue_key': 'MC3-1'})
    import re
    download_url = re.search(r'http://testserver(/api/mcp/downloads/[^\s\x27]+)',
                             pull['content'][0]['text']).group(1)
    monkeypatch.setattr(jira, 'upload_agent_attachment', lambda *a: pytest.fail('must not upload'))
    conn = connect(settings.db_path)
    if change == 'rotate':
        token_for('rm', 'RM')
    elif change == 'revoke':
        conn.execute("UPDATE mcp_tokens SET revoked_at='revoked' WHERE username='rm'")
    elif change == 'delete':
        conn.execute("DELETE FROM users WHERE username='rm'")
    else:
        conn.execute('UPDATE users SET role=? WHERE username=?', (change.title(), 'rm'))
    conn.commit()
    conn.close()
    assert client.put(upload_url, content=b'x').status_code == expected
    # An Owner may still read, but loses the RM exemption for others' issues.
    if change != 'owner':
        monkeypatch.setattr(jira, 'download_attachment', lambda *a: pytest.fail('must not download'))
    assert client.get(download_url).status_code == (200 if change == 'owner' else expected)


@pytest.mark.parametrize('session_kind', ['missing', 'unknown', 'foreign', 'ended'])
@pytest.mark.parametrize('tool,args', [
    ('jira_add_comment', {'issue_key': 'MC3-1', 'body': 'no'}),
    ('jira_upload_attachment', {'issue_key': 'MC3-1', 'file_path': 'a.log'}),
])
def test_write_requires_valid_session(site, session_kind, tool, args):
    client, _, token_for, calls = site
    token = token_for('alice', 'Owner')
    session = connect_confirming(client, token)
    if session_kind == 'missing':
        session = ''
    elif session_kind == 'unknown':
        session = 'unknown-session'
    elif session_kind == 'foreign':
        other = token_for('carol', 'Owner')
        session = connect_confirming(client, other)
    else:
        assert client.delete('/mcp', headers={'Authorization': f'Bearer {token}',
                                             'Mcp-Session-Id': session}).status_code == 204
    result = rpc(client, token, 'tools/call', {'name': tool, 'arguments': args}, session=session)
    assert result.json()['error']['code'] == mcp_protocol.INVALID_PARAMS
    assert calls['comments'] == []


@pytest.mark.parametrize('confirming', [True, False])
def test_batch_cannot_downgrade_write_confirmation(site, confirming):
    client, _, token_for, calls = site
    token = token_for('alice', 'Owner')
    session = connect_confirming(client, token) if confirming else connect_client(client, token)[0]
    response = client.post('/mcp', headers={'Authorization': f'Bearer {token}', 'Mcp-Session-Id': session},
                           json=[{'jsonrpc': '2.0', 'id': i, 'method': 'tools/call',
                                  'params': {'name': name, 'arguments': args}} for i, (name, args) in enumerate([
                                      ('jira_add_comment', {'issue_key': 'MC3-1', 'body': 'no'}),
                                      ('jira_upload_attachment', {'issue_key': 'MC3-1', 'file_path': 'a.log'}),
                                      ('skill_load', {'skill': 'cu-bridge'})])])
    results = response.json()
    assert all(r['error']['code'] == mcp_protocol.INVALID_PARAMS for r in results[:2])
    assert not results[2]['result']['isError']
    assert calls['comments'] == []




def connect_confirming(client, token, name="claude-code"):
    response = rpc(client, token, "initialize", {
        "protocolVersion": "2025-06-18", "capabilities": {"elicitation": {"form": {}}},
        "clientInfo": {"name": name, "version": "2.1"},
    })
    assert response.status_code == 200
    return response.headers["mcp-session-id"]


def sse_messages(response):
    return [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]


def answer(client, token, session, request_id, payload):
    reply = client.post("/mcp", json={"jsonrpc": "2.0", "id": request_id, **payload},
                        headers={"Authorization": f"Bearer {token}", "Mcp-Session-Id": session})
    assert reply.status_code == 202


def confirmable_call(site_client, token, session, name, arguments, payload):
    import threading
    answered = threading.Event()
    box: dict = {}

    def respond():
        for _ in range(100):
            time.sleep(0.05)
            pending = [m for m in box.get("seen", []) if m.get("method") == "elicitation/create"]
            if pending:
                answer(site_client, token, session, pending[0]["id"], payload)
                answered.set()
                return
        answered.set()

    original = mcp_protocol._Stream.events

    def watched(self):
        for chunk in original(self):
            if chunk.startswith(b"event: message\n"):
                box.setdefault("seen", []).append(json.loads(chunk.decode().split("data: ", 1)[1]))
            yield chunk

    mcp_protocol._Stream.events = watched
    worker = threading.Thread(target=respond, daemon=True)
    worker.start()
    try:
        response = site_client.post("/mcp", json={
            "jsonrpc": "2.0", "id": 90, "method": "tools/call",
            "params": {"name": name, "arguments": arguments}},
            headers={"Authorization": f"Bearer {token}", "Mcp-Session-Id": session})
    finally:
        mcp_protocol._Stream.events = original
        answered.wait(10)
    assert response.status_code == 200 and response.headers["content-type"].startswith("text/event-stream")
    return sse_messages(response)


def test_comment_asks_the_user_to_confirm_and_posts_on_accept(site):
    client, _, token_for, calls = site
    token = token_for("alice", "Owner")
    session = connect_confirming(client, token)
    messages = confirmable_call(client, token, session, "jira_add_comment",
                               {"issue_key": "MC3-1", "body": "root cause: X"},
                               {"result": {"action": "accept", "content": {"username": "alice"}}})
    ask, result = messages
    assert ask["method"] == "elicitation/create"
    assert "MC3-1" in ask["params"]["message"] and "root cause: X" in ask["params"]["message"]
    assert "alice" not in json.dumps(ask["params"], ensure_ascii=False).casefold()
    assert ask["params"]["requestedSchema"]["required"] == ["username"]
    assert result["id"] == 90 and result["result"]["isError"] is False
    assert [key for key, _ in calls["comments"]] == ["MC3-1"]
    assert json.loads(logs(client, tool="jira_add_comment")[0]["extra_json"])["confirm"] == "username_matched"


@pytest.mark.parametrize(("payload", "outcome"), [
    ({"result": {"action": "decline"}}, "decline"),
    ({"result": {"action": "cancel"}}, "cancel"),
    ({"result": {"action": "accept", "content": {"confirm": True}}}, "username_mismatch"),
    ({"result": {"action": "accept", "content": {"username": ""}}}, "username_mismatch"),
    ({"result": {"action": "accept", "content": {"username": "carol"}}}, "username_mismatch"),
    ({"result": {"action": "accept", "content": {"username": ["alice"]}}}, "username_mismatch"),
    ({"result": {"action": "accept", "content": {"username": "alice-other"}}}, "username_mismatch"),
    ({"error": {"code": -1, "message": "no"}}, "no_answer"),
])
def test_comment_is_not_posted_when_the_user_does_not_confirm(site, payload, outcome):
    client, _, token_for, calls = site
    token = token_for("alice", "Owner")
    session = connect_confirming(client, token)
    _, result = confirmable_call(client, token, session, "jira_add_comment",
                                 {"issue_key": "MC3-1", "body": "x"}, payload)
    assert result["result"]["isError"] is True
    assert "未" in result["result"]["content"][0]["text"]
    assert result["result"]["structuredContent"] == {"status": "not_executed", "reason": outcome, "retryable": False}
    assert calls["comments"] == []
    entry = logs(client, tool="jira_add_comment")[0]
    assert json.loads(entry["extra_json"])["confirm"] == outcome
    assert (entry["target_type"], entry["target"]) == ("jira_issue", "MC3-1")


@pytest.mark.parametrize("confirming", [True, False])
def test_upload_does_not_confirm_even_on_elicitation_client(site, monkeypatch, confirming):
    client, _, token_for, _ = site
    uploaded = []
    monkeypatch.setattr(jira, "upload_agent_attachment", lambda *args: uploaded.append(args) or "5001")
    monkeypatch.setattr(jira, "browse_url", lambda: "https://jira.example/browse/")
    token = token_for("alice", "Owner")
    session = connect_confirming(client, token) if confirming else connect_client(client, token)[0]
    response = rpc(client, token, "tools/call", {"name": "jira_upload_attachment", "arguments": {
        "issue_key": "MC3-1", "file_path": "/tmp/build.log"}}, session=session)
    assert response.headers["content-type"].startswith("application/json")
    result = response.json()["result"]
    assert not result["isError"]
    url = _upload_url(result["content"][0]["text"])
    assert client.put(url, content=b"build log").status_code == 200
    assert uploaded == [("MC3-1", "build.log", b"build log")]
    assert "confirm" not in json.loads(logs(client, tool="jira_upload_attachment")[0]["extra_json"])
    plain = rpc(client, token, "tools/call", {"name": "jira_search", "arguments": {"query": "MC3-1"}},
                session=session)
    assert plain.headers["content-type"].startswith("application/json")


def test_client_without_elicitation_cannot_publish_comment(site):
    client, _, token_for, calls = site
    token = token_for("alice", "Owner")
    session, _ = connect_client(client, token, "deepseek-harness")
    result = call(client, token, session, "jira_add_comment", {"issue_key": "MC3-1", "body": "直接发布"})
    assert result["isError"] and calls["comments"] == []
    assert "不支持" in result["content"][0]["text"]
    entry = logs(client, tool="jira_add_comment")[0]
    assert json.loads(entry["extra_json"])["confirm"] == "client_cannot_confirm"
    connection = logs(client, kind="connection")[0]
    assert json.loads(connection["extra_json"])["can_confirm"] is False


def test_initialize_records_that_the_client_can_confirm(site):
    client, _, token_for, _ = site
    token = token_for("alice", "Owner")
    connect_confirming(client, token, "codex-mcp-client")
    extra = json.loads(logs(client, kind="connection")[0]["extra_json"])
    assert extra["can_confirm"] is True and extra["capabilities"] == ["elicitation"]


def test_confirmation_times_out_without_posting(site, monkeypatch):
    client, _, token_for, calls = site
    monkeypatch.setattr(mcp_protocol, "CONFIRM_TIMEOUT_SECONDS", 0.2)
    token = token_for("alice", "Owner")
    session = connect_confirming(client, token)
    response = client.post("/mcp", json={"jsonrpc": "2.0", "id": 91, "method": "tools/call",
                                         "params": {"name": "jira_add_comment",
                                                    "arguments": {"issue_key": "MC3-1", "body": "x"}}},
                           headers={"Authorization": f"Bearer {token}", "Mcp-Session-Id": session})
    _, result = sse_messages(response)
    assert result["result"]["isError"] is True and "超时" in result["result"]["content"][0]["text"]
    assert calls["comments"] == []
    assert json.loads(logs(client, tool="jira_add_comment")[0]["extra_json"])["confirm"] == "timeout"


@pytest.mark.parametrize('change', ['revoke', 'demote'])
def test_confirmation_revalidates_before_publish(site, change):
    from app.services import mcp_service
    client, _, token_for, calls = site
    token = token_for('rm', 'RM')
    session = connect_confirming(client, token)
    conn = connect(settings.db_path)
    try:
        caller = mcp_service.authenticate(conn, f'Bearer {token}')
        stream, _ = mcp_protocol.handle_payload(conn, caller, session, 'http://testserver', {
            'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
            'params': {'name': 'jira_add_comment', 'arguments': {'issue_key': 'MC3-1', 'body': 'no'}}})
        events = stream.exchange.call_with_confirmation(stream.msg_id, stream.tool, stream.arguments)
        ask = next(events)
        if change == 'revoke':
            conn.execute("UPDATE mcp_tokens SET revoked_at='revoked' WHERE username='rm'")
        else:
            conn.execute("UPDATE users SET role='Owner' WHERE username='rm'")
        conn.commit()
        assert mcp_protocol._settle({'id': ask['id'], 'result': {'action': 'accept', 'content': {'username': 'rm'}}},
                                    caller, {'session_id': session})
        assert next(events)['result']['isError']
        assert calls['comments'] == []
    finally:
        conn.close()


def test_confirmation_response_is_bound_to_token_and_session(site):
    from app.services import mcp_service
    client, _, token_for, calls = site
    token = token_for('alice', 'Owner')
    session = connect_confirming(client, token)
    other_token = token_for('carol', 'Owner')
    conn = connect(settings.db_path)
    try:
        caller = mcp_service.authenticate(conn, f'Bearer {token}')
        other = mcp_service.authenticate(conn, f'Bearer {other_token}')
        exchange = mcp_protocol.Exchange(conn, caller, session, 'http://testserver')
        events = exchange.call_with_confirmation(1, mcp_tools.get_tool('jira_add_comment'),
                                                 {'issue_key': 'MC3-1', 'body': 'approved'})
        ask = next(events)
        reply = {'id': ask['id'], 'result': {'action': 'accept', 'content': {'username': 'alice'}}}
        assert not mcp_protocol._settle(reply, other, {'session_id': session})
        assert not mcp_protocol._settle(reply, caller, {'session_id': 'another-session'})
        assert calls['comments'] == []
        assert mcp_protocol._settle(reply, caller, {'session_id': session})
        assert not next(events)['result']['isError']
        assert len(calls['comments']) == 1
    finally:
        conn.close()


def test_comment_username_normalizes_case_and_whitespace(site):
    client, _, token_for, calls = site
    token = token_for("alice", "Owner")
    session = connect_confirming(client, token)
    ask, result = confirmable_call(client, token, session, "jira_add_comment",
                                  {"issue_key": "MC3-1", "body": "test"},
                                  {"result": {"action": "accept", "content": {"username": "  ALICE  "}}})
    field = ask["params"]["requestedSchema"]["properties"]["username"]
    assert field["type"] == "string" and "default" not in field
    assert not result["result"]["isError"] and len(calls["comments"]) == 1


def test_url_only_elicitation_cannot_bypass_username_form(site):
    client, _, token_for, calls = site
    token = token_for('alice', 'Owner')
    response = rpc(client, token, 'initialize', {
        'protocolVersion': '2025-06-18', 'capabilities': {'elicitation': {'url': {}}},
        'clientInfo': {'name': 'url-only', 'version': '1'},
    })
    session = response.headers['mcp-session-id']
    result = call(client, token, session, 'jira_add_comment', {'issue_key': 'MC3-1', 'body': 'no'})
    assert result['isError'] and calls['comments'] == []


@pytest.mark.parametrize('read_only,destructive', [(True, True), (True, False), (False, True), (False, False)])
@pytest.mark.parametrize('custom_preview', [True, False])
def test_confirmation_policy_comes_from_tool_flags(site, monkeypatch, read_only, destructive, custom_preview):
    client, _, token_for, _ = site
    executed = []
    previews = []
    def preview(ctx, args):
        previews.append(args)
        return 'Custom preview'
    def handler(ctx, args):
        executed.append(args)
        return ToolResult.text('executed')
    tool = Tool(name='policy_test', category='test', title='Generic action', description='test',
                input_schema={'type': 'object'}, handler=handler, read_only=read_only,
                destructive=destructive, confirm=preview if custom_preview else None)
    monkeypatch.setitem(mcp_tools._REGISTRY, tool.name, tool)
    token = token_for('alice', 'Owner')
    session = connect_confirming(client, token)
    args = {'target': 'example'}
    if not read_only and destructive:
        # Even without a custom preview a destructive write must never bypass confirmation.
        _, refused = confirmable_call(client, token, session, tool.name, args,
                                      {'result': {'action': 'accept', 'content': {'username': 'carol'}}})
        assert refused['result']['isError'] and executed == []
        ask, result = confirmable_call(client, token, session, tool.name, args,
                                       {'result': {'action': 'accept', 'content': {'username': 'alice'}}})
        assert ask['params']['requestedSchema']['required'] == ['username']
        if custom_preview:
            assert ask['params']['message'] == 'Custom preview'
        else:
            assert all(text in ask['params']['message'] for text in ('Generic action', 'example'))
            assert 'alice' not in json.dumps(ask['params'], ensure_ascii=False).casefold()
        assert not result['result']['isError']
        unconfirmed_session, _ = connect_client(client, token)
        blocked = call(client, token, unconfirmed_session, tool.name, args)
        assert blocked['isError']
    else:
        response = rpc(client, token, 'tools/call', {'name': tool.name, 'arguments': args}, session=session)
        assert response.headers['content-type'].startswith('application/json')
        assert not response.json()['result']['isError']
        assert previews == []  # A preview callback alone must not enable confirmation.
    assert executed == [args]


def test_setup_endpoint_follows_request_origin_not_public_base(site, tmp_path, monkeypatch):
    client, _, _, _ = site
    conf = tmp_path / 'site.conf'
    conf.write_text('[site]\nPUBLIC_BASE_URL = https://different.example\n')
    monkeypatch.setattr(settings, 'runtime_conf_path', conf)
    origin = 'http://127.0.0.1:3577'
    info = client.get(origin + '/api/mcp/info')
    assert info.json()['endpoint'] == origin + '/mcp'
    script = client.get(origin + '/api/mcp/setup.sh').text
    assert f'BASE_URL="{origin}"' in script
    assert 'different.example' not in script


@pytest.mark.parametrize(('origin', 'forwarded_proto', 'expected'), [
    ('http://127.0.0.1:3577', '', 'http://127.0.0.1:3577'),
    ('http://release.example', 'https', 'https://release.example'),
    ('http://release.example:8443', 'https', 'https://release.example:8443'),
    ('https://release.example', 'http', 'https://release.example'),
    ('http://release.example', 'ftp', 'http://release.example'),
    ('http://release.example', 'https, http', 'http://release.example'),
])
def test_setup_preserves_https_behind_proxy(site, origin, forwarded_proto, expected):
    client, _, _, _ = site
    headers = {'X-Forwarded-Proto': forwarded_proto, 'X-Forwarded-Host': 'other.example'}
    response = client.get(origin + '/api/mcp/setup.sh', headers=headers)
    assert response.status_code == 200
    assert f'BASE_URL="{expected}"' in response.text
    info = client.get(origin + '/api/mcp/info', headers=headers)
    assert info.status_code == 200
    assert info.json()['endpoint'] == expected + '/mcp'


def test_proxy_https_used_for_account_config_and_attachment_commands(site, monkeypatch):
    from app.services import mcp_service
    monkeypatch.setattr(mcp_service, '_SETUP_FAILURES', {})
    client, _, _, _ = site
    client.headers.update({'Host': 'release.example:8443', 'X-Forwarded-Proto': 'https'})
    _set_password('alice', 'pw-alice')
    response = account(client, 'regenerate')
    assert response.status_code == 200
    created = response.json()
    origin = 'https://release.example:8443'
    assert created['endpoint'] == origin + '/mcp'
    assert created['config']['claude_json']['mcpServers']['release-system']['url'] == origin + '/mcp'
    assert f'url = "{origin}/mcp"' in created['config']['codex_toml']
    session, _ = connect_client(client, created['token'])
    for name, args, path in (
        ('jira_pull_issue', {'issue_key': 'MC3-1'}, 'downloads'),
        ('jira_upload_attachment', {'issue_key': 'MC3-1', 'file_path': 'test.log'}, 'uploads'),
    ):
        result = call(client, created['token'], session, name, args)
        assert not result.get('isError')
        guide = result['content'][0]['text']
        assert f'{origin}/api/mcp/{path}/' in guide
        assert 'http://release.example' not in guide


@pytest.fixture()
def jenkins_backend(monkeypatch):
    from unittest.mock import Mock
    from app.integrations import cicd_agent

    monkeypatch.setattr(cicd_agent.runtime_config, "section", lambda _: {
        "BASE_URL": "http://agent.invalid", "TIMEOUT_SECONDS": "5",
        "REPRO_GATEWAY_TOKEN": "test-gateway-token",
    })
    response = Mock(status=200)
    response.__enter__ = Mock(return_value=response)
    response.__exit__ = Mock(return_value=False)
    opener = Mock()
    opener.open.return_value = response
    monkeypatch.setattr(cicd_agent.urllib.request, "build_opener", lambda *_: opener)
    return opener, response


@pytest.mark.parametrize("role", ["Owner", "RM", "QA", "SPD"])
def test_jenkins_search_returns_reasons_and_pagination(site, jenkins_backend, role):
    from urllib.parse import parse_qs, urlsplit
    client, _, token_for, _ = site
    conn = connect(settings.db_path)
    conn.execute("UPDATE users SET role=? WHERE username='alice'", (role,))
    conn.commit()
    conn.close()
    token = token_for("alice", role)
    session, _ = connect_client(client, token)
    opener, response = jenkins_backend
    payload = {"total": 41, "page": 2, "page_size": 20, "total_pages": 3, "records": [{
        "id": 101, "reason_summary": "编译失败", "key_error_snippet": "undefined reference",
        "action_suggestion": "检查链接库", "build_url": "https://jenkins.example/job/test/1/",
    }]}
    response.read.return_value = json.dumps(payload).encode()
    result = call(client, token, session, "jenkins_search_failures", {
        "keyword": "a+b & c", "date_from": "2026-11-01", "date_to": "2026-11-11",
        "page": 2, "pending_rm_review": False,
    })
    assert not result["isError"]
    decoded = json.loads(result["content"][0]["text"])
    assert "correlations" in decoded
    assert {k: v for k, v in decoded.items() if k != "correlations"} == payload
    request = opener.open.call_args.args[0]
    assert request.method == "GET"
    assert urlsplit(request.full_url).path == "/api/v1/failures"
    query = parse_qs(urlsplit(request.full_url).query)
    assert query == {"keyword": ["a+b & c"], "date_from": ["2026-11-01"], "date_to": ["2026-11-11"],
                     "page": ["2"], "page_size": ["20"], "pending_rm_review": ["false"],
                     "record_state": ["active"], "sort_by": ["created_at"], "sort_order": ["desc"]}
    entry = logs(client, tool="jenkins_search_failures")[0]
    assert entry["category"] == "jenkins" and entry["status"] == "ok"


@pytest.mark.parametrize("state,role,allowed", [("active", "Owner", True), ("trashed", "Owner", False),
                                                ("trashed", "RM", True)])
def test_jenkins_detail_preserves_diagnostics_and_trash_permissions(site, jenkins_backend, state, role, allowed):
    client, _, token_for, _ = site
    opener, response = jenkins_backend
    payload = {"id": 101, "reason_summary": "compile failed", "key_error_snippet": "missing.h",
               "action_suggestion": "install dependency", "responsibility_events": [{"actor": "owner"}],
               "test_summary": {"total": 3}, "effective_owner_account": "alice"}
    response.read.return_value = json.dumps(payload).encode()
    token = token_for("rm" if role == "RM" else "alice", role)
    session, _ = connect_client(client, token)
    result = call(client, token, session, "jenkins_get_failure", {"record_id": 101, "record_state": state})
    assert result["isError"] is not allowed
    if not allowed:
        opener.open.assert_not_called()
        return
    decoded = json.loads(result["content"][0]["text"])
    assert "correlations" in decoded
    assert {k: v for k, v in decoded.items() if k != "correlations"} == payload
    request = opener.open.call_args.args[0]
    assert request.full_url == f"http://agent.invalid/api/v1/failures/101?record_state={state}"
    headers = {key.lower(): value for key, value in request.header_items()}
    assert headers["authorization"] == "Bearer test-gateway-token"
    if state == "trashed":
        assert headers["x-actor"] == "rm" and headers["x-actor-role"] == "RM"


@pytest.mark.parametrize("tool,args", [
    ("jenkins_search_failures", {"record_state": "trashed"}),
    ("jenkins_search_failures", {"record_state": "deleted"}),
    ("jenkins_search_failures", {"page_size": 101}),
    ("jenkins_search_failures", {"date_from": "2026-11-12", "date_to": "2026-11-01"}),
    ("jenkins_search_failures", {"actor_role": "RM"}),
    ("jenkins_get_failure", {"record_id": "../summary"}),
    ("jenkins_get_failure", {"record_id": True}),
])
def test_jenkins_invalid_or_unauthorized_queries_do_not_reach_backend(site, jenkins_backend, tool, args):
    client, _, token_for, _ = site
    opener, _ = jenkins_backend
    token = token_for("alice", "Owner")
    session, _ = connect_client(client, token)
    assert call(client, token, session, tool, args)["isError"]
    opener.open.assert_not_called()


@pytest.mark.parametrize("status,payload", [(404, {"detail": "记录不存在"}),
                                           (503, {"error": "服务不可用"}),
                                           (200, {"ok": False, "error": "查询失败"})])
def test_jenkins_upstream_errors_are_not_reported_as_empty_results(site, monkeypatch, status, payload):
    from app.integrations import cicd_agent
    monkeypatch.setattr(cicd_agent, "_request_agent_payload", lambda *a, **kw: (status, payload))
    client, _, token_for, _ = site
    token = token_for("alice", "Owner")
    session, _ = connect_client(client, token)
    result = call(client, token, session, "jenkins_get_failure", {"record_id": 101})
    assert result["isError"]
    assert (payload.get("error") or payload["detail"]) in result["content"][0]["text"]


def test_jira_format_skill_reuses_reference_and_reloads(site, monkeypatch, tmp_path):
    from app.services import mcp_skill_tools
    client, _, token_for, _ = site
    token = token_for('alice', 'Owner')
    session, _ = connect_client(client, token)
    result = call(client, token, session, 'skill_load', {'skill': 'jira-format'})
    reference = mcp_skill_tools.SKILLS['jira-format']
    expected = (reference['folder'] / reference['main']).read_text()
    assert not result['isError'] and expected in result['content'][0]['text']
    assert 'page=' not in result['content'][0]['text']
    assert call(client, token, session, 'skill_load', {'skill': 'jira-format', 'page': 'diagnosis'})['isError']
    (tmp_path / 'format.md').write_text('shared format version one')
    monkeypatch.setitem(mcp_skill_tools.SKILLS, 'jira-format', {'folder': tmp_path, 'main': 'format.md', 'pages': {}})
    assert 'version one' in call(client, token, session, 'skill_load', {'skill': 'jira-format'})['content'][0]['text']
    (tmp_path / 'format.md').write_text('shared format version two')
    assert 'version two' in call(client, token, session, 'skill_load', {'skill': 'jira-format'})['content'][0]['text']


@pytest.mark.parametrize('repetitions', [1, 600])
def test_comment_preview_and_published_body_share_formatting(site, repetitions):
    from app.domain.jira_format import format_jira_comment
    client, _, token_for, calls = site
    token = token_for('alice', 'Owner')
    session = connect_confirming(client, token)
    body = '设置{{VALUE}}后重试。\n{noformat}\necho 中{{raw}}文\n{noformat}\n' + '说明。' * repetitions + '\n结束{{END}}标记'
    expected = format_jira_comment(body)
    ask, result = confirmable_call(client, token, session, 'jira_add_comment',
                                  {'issue_key': 'MC3-1', 'body': body},
                                  {'result': {'action': 'accept', 'content': {'username': 'alice'}}})
    assert not result['result']['isError']
    message = ask['params']['message']
    if repetitions == 1:
        preview = message.split('\n\n', 1)[1]
        assert preview == expected
    else:
        preview = message.split('评论摘要：\n', 1)[1].split('\n…', 1)[0]
        assert expected.startswith(preview)
        assert '摘要已截断' in message and '结束 {{END}} 标记' not in message
    published = calls['comments'][0][1].split('\n\n----\n', 1)[0]
    assert published == expected
    assert 'echo 中{{raw}}文' in preview
    assert '结束 {{END}} 标记' in published
    assert 'alice' not in ask['params']['message'].casefold()
    assert format_jira_comment(expected) == expected


@pytest.mark.parametrize('body', ['长评论' * 10000, '逐行说明\n' * 1000, '首行\n' + '\n' * 1000 + '结束'])
@pytest.mark.parametrize('username', ['alice', 'carol'])
def test_long_comment_confirmation_is_compact_without_truncating_publication(site, body, username):
    client, _, token_for, calls = site
    token = token_for('alice', 'Owner')
    session = connect_confirming(client, token)
    ask, result = confirmable_call(client, token, session, 'jira_add_comment',
                                  {'issue_key': 'MC3-1', 'body': body},
                                  {'result': {'action': 'accept', 'content': {'username': username}}})
    message = ask['params']['message']
    assert len(message) <= 500 and len(message.splitlines()) <= 13
    assert 'MC3-1' in message and '摘要已截断' in message
    assert '完整正文请在对话中核对' in message
    assert message.endswith('请输入本人域账号用户名：')
    field = ask['params']['requestedSchema']['properties']['username']
    assert field['title'] == '请输入本人域账号用户名' and 'default' not in field
    assert ask['params']['requestedSchema']['required'] == ['username']
    if username == 'alice':
        assert not result['result']['isError']
        assert calls['comments'][0][1].split('\n\n----\n', 1)[0] == body.strip()
    else:
        assert result['result']['isError'] and calls['comments'] == []


def test_attachment_read_paginates_long_lines_and_checks_identity(site, monkeypatch):
    from app.services.mcp_jira_attachments import MAX_PAGE_CHARS
    client, _, token_for, _ = site
    token = token_for('alice', 'Owner')
    session, _ = connect_client(client, token)
    original = '源' * (MAX_PAGE_CHARS+20) + '\nsecond\nthird\n'
    downloads = []
    def download(url, **kwargs):
        downloads.append(kwargs)
        return original.encode()
    monkeypatch.setattr(jira, 'download_attachment', download)
    args = {'issue_key': 'MC3-1', 'attachment_id': '77', 'max_lines': 2}
    texts = []
    for _ in range(5):
        result = call(client, token, session, 'jira_read_attachment', args)
        assert not result['isError']
        page = json.loads(result['content'][0]['text'])
        texts.append(page['content'])
        if not page['has_more']:
            break
        args.update(start_line=page['next_start_line'], start_column=page['next_start_column'], expected_sha256=page['sha256'])
    assert ''.join(texts) == original
    assert all(x['max_bytes'] == 2*1024*1024 for x in downloads)
    before = len(downloads)
    assert call(client, token, session, 'jira_read_attachment', {'issue_key':'MC3-1','attachment_id':'999'})['isError']
    assert len(downloads) == before
    assert call(client, token, session, 'jira_read_attachment', {**args,'expected_sha256':'0'*64})['isError']


@pytest.mark.parametrize('data', [b'\x00\x01binary', b'\xff\x00\xaa', b'x'*(2*1024*1024+1)])
def test_attachment_rejects_binary_or_oversized_content(site, monkeypatch, data):
    client, _, token_for, _ = site
    token = token_for('alice','Owner')
    session, _ = connect_client(client, token)
    monkeypatch.setattr(jira, 'download_attachment', lambda *a, **kw: data)
    assert call(client,token,session,'jira_read_attachment',{'issue_key':'MC3-1','attachment_id':'77'})['isError']
