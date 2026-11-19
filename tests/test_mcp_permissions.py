"""The website matrix governs MCP discovery, calls and outstanding tickets."""
from contextlib import closing
import json
import re

from app.config import settings
from app.db.connection import connect
from app.integrations import jira
from app.services.mcp_tools import registered_tools
from tests.test_mcp import ISSUE, call, connect_client, rpc, site  # noqa: F401
from tests.test_mcp_guest import download_path
from tests.test_mcp_jenkins_access import listed, membership, set_identity

PATH = "/api/mcp/permissions"


def save(client, revision, grants):
    return client.post(PATH, json={"revision": revision, "grants": grants})


def test_permission_matrix_defaults_validation_and_conflict(site):
    client, current, _, _ = site
    for role in ("Owner", "Guest"):
        current.update(role=role)
        assert client.get(PATH).status_code == 403
        assert save(client, 0, {"skill_load": []}).status_code == 403

    current.update(username="rm", role="RM")
    initial = client.get(PATH).json()
    assert initial["revision"] == 0
    assert {col["id"] for col in initial["columns"]} == {"RM", "Owner", "Guest", "QA", "SPD", "dl.pde_*", "dl.sw_*"}
    tools = {tool["name"]: tool for tool in initial["tools"]}
    assert set(tools) == {tool.name for tool in registered_tools()}
    assert tools["wiki_search"]["enabled"] is False
    assert set(tools["wiki_search"]["grants"]) == {"RM", "Owner", "dl.pde_*", "dl.sw_*"}
    assert set(tools["cicd_jenkins_list_jobs"]["grants"]) == {"RM", "dl.sw_*"}
    sites = [tool["jenkins_site"] for tool in initial["tools"] if tool["category"] == "jenkins"]
    assert sites == ["pde"] * 5 + ["cicd"] * 3

    for invalid in ({"unknown_tool": []}, {"skill_load": ["Admin"]}, {"skill_load": ["Guest", "Guest"]}):
        assert save(client, 0, invalid).status_code == 400
    changed = save(client, 0, {"skill_load": []})
    assert changed.status_code == 200 and changed.json()["revision"] == 1
    assert save(client, 0, {"skill_load": ["Guest"]}).status_code == 409
    assert client.get(PATH).json() == changed.json()
    with closing(connect(settings.db_path)) as conn:
        audit = conn.execute("SELECT user, detail FROM audit WHERE event = 'mcp_tool_permissions'").fetchone()
        assert audit["user"] == "rm"
        assert json.loads(audit["detail"])[0]["after"] == []


def test_roles_and_groups_apply_to_an_existing_mcp_token(site, monkeypatch):
    client, current, token_for, _ = site
    token = token_for("guest", "Guest")
    session, _ = connect_client(client, token)
    set_identity("guest", "Guest")
    groups = ["CN=dl.sw_runtime,OU=Groups,DC=example"]
    membership(monkeypatch, groups)
    assert "skill_load" in listed(client, token, session)

    current.update(username="rm", role="RM")
    assert save(client, 0, {"skill_load": ["dl.pde_*", "Owner"]}).status_code == 200
    assert "skill_load" not in listed(client, token, session)
    groups[:] = ["CN=DL.PDE_HPC,OU=Groups,DC=example"]
    assert "skill_load" in listed(client, token, session)
    assert not call(client, token, session, "skill_load", {"skill": "jira-format"})["isError"]
    current.update(username="guest", role="Guest")
    assert "skill_load" in {tool["name"] for tool in client.get("/api/mcp/info").json()["tools"]}

    current.update(username="rm", role="RM")
    assert save(client, 1, {"skill_load": ["Guest", "dl.sw_*"]}).status_code == 200
    groups.clear()
    assert "skill_load" in listed(client, token, session)
    assert save(client, 2, {"skill_load": []}).status_code == 200
    assert "skill_load" not in listed(client, token, session)
    assert rpc(client, token, "tools/call", {"name": "skill_load"}, session=session).json()["error"]["code"] == -32602


def test_matrix_can_grant_a_write_and_revoke_outstanding_tickets(site, monkeypatch):
    client, current, token_for, _ = site
    token = token_for("guest", "Guest")
    session, _ = connect_client(client, token)
    monkeypatch.setattr(jira, "get_issue", lambda key: {**ISSUE, "key": key, "assignee": {"name": "guest"}})
    uploads = []
    monkeypatch.setattr(jira, "upload_agent_attachment", lambda *args: uploads.append(args) or "900")
    current.update(username="rm", role="RM")
    assert save(client, 0, {"jira_upload_attachment": ["Guest"]}).status_code == 200
    denied = call(client, token, session, "jira_upload_attachment", {"issue_key": "PHS-1", "file_path": "test.txt"})
    assert denied["isError"] and "MC3" in denied["content"][0]["text"]
    upload = call(client, token, session, "jira_upload_attachment", {"issue_key": "MC3-1", "file_path": "test.txt"})
    assert not upload["isError"], upload
    upload_path = re.search(r"(/api/mcp/uploads/[\w-]+)", upload["content"][0]["text"]).group(1)
    pull = call(client, token, session, "jira_pull_issue", {"issue_key": "MC3-1", "save_to_local": True})
    assert save(client, 1, {"jira_upload_attachment": [], "jira_pull_issue": []}).status_code == 200
    assert client.put(upload_path, content=b"x").status_code == 403
    assert client.get(download_path(pull)).status_code == 403
    assert uploads == []
