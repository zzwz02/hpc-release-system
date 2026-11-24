"""The website matrix governs MCP discovery, calls and outstanding tickets."""
import json
import re
import sqlite3
from contextlib import closing

from app.config import settings
from app.db.connection import connect
from app.integrations import jira
from app.repositories import mcp_repo, users_repo
from app.services.mcp_tools import registered_tools
from tests.test_mcp import ISSUE, call, confirmable_call, connect_client, connect_verified, rpc
from tests.test_mcp import site as site
from tests.test_mcp_guest import download_path
from tests.test_mcp_jenkins_access import listed, membership, set_identity

PATH = "/api/mcp/permissions"


def save(client, revision, grants, **extra):
    return client.post(PATH, json={"revision": revision, "grants": grants, **extra})


def test_permission_matrix_defaults_validation_and_conflict(site):
    client, current, _, _ = site
    for role in ("Owner", "Guest"):
        current.update(role=role)
        assert client.get(PATH).status_code == 403
        assert save(client, 0, {"skill_load": []}).status_code == 403

    current.update(username="rm", role="RM")
    initial = client.get(PATH).json()
    assert initial["revision"] == 0
    assert initial["guests"] == [
        {"username": "guest", "display_name": "Guest", "jira_write_all": False},
    ]
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
    session = connect_verified(client, token)
    monkeypatch.setattr(jira, "get_issue", lambda key: {**ISSUE, "key": key, "assignee": {"name": "guest"}})
    monkeypatch.setattr(jira, "account_can_browse",
                        lambda username, project_key="", issue_key="": project_key != "PHS")
    uploads = []
    monkeypatch.setattr(jira, "upload_agent_attachment", lambda *args: uploads.append(args) or "900")
    current.update(username="rm", role="RM")
    assert save(client, 0, {"jira_upload_attachment": ["Guest"]}).status_code == 200
    denied = call(client, token, session, "jira_upload_attachment", {"issue_key": "PHS-1", "file_path": "test.txt"})
    assert denied["isError"] and "无权浏览 JIRA 项目 PHS" in denied["content"][0]["text"]
    upload = call(client, token, session, "jira_upload_attachment", {"issue_key": "MC3-1", "file_path": "test.txt"})
    assert not upload["isError"], upload
    upload_path = re.search(r"(/api/mcp/uploads/[\w-]+)", upload["content"][0]["text"]).group(1)
    pull = call(client, token, session, "jira_pull_issue", {"issue_key": "MC3-1", "save_to_local": True})
    assert save(client, 1, {"jira_upload_attachment": [], "jira_pull_issue": []}).status_code == 200
    assert client.put(upload_path, content=b"x").status_code == 403
    assert client.get(download_path(pull)).status_code == 403
    assert uploads == []


def test_rm_guest_jira_write_grants_are_validated_and_audited(site):
    client, current, _, _ = site
    flags = {"guest": True}
    for role in ("Guest", "Owner", "Admin"):
        current.update(role=role)
        assert save(client, 0, {}, guest_jira_write_all=flags).status_code == 403
    current.update(username="rm", role="RM")
    assert save(client, 0, {}, guest_jira_write=flags).status_code == 422
    assert save(client, 0, {}, guest_jira_write_all={"guest": "true"}).status_code == 422
    for username in ("alice", "unknown"):
        invalid = save(client, 0, {"skill_load": []}, guest_jira_write_all={**flags, username: True})
        assert invalid.status_code == 400
        assert save(client, 0, {}, guest_jira_write_all={username: True}).status_code == 400
    matrix = client.get(PATH).json()
    assert matrix["revision"] == 0
    assert matrix["guests"][0]["jira_write_all"] is False
    skill_load = next(tool for tool in matrix["tools"] if tool["name"] == "skill_load")
    assert "Guest" in skill_load["grants"]
    updated = save(client, 0, {}, guest_jira_write_all=flags)
    assert updated.status_code == 200
    assert updated.json()["revision"] == 1
    assert updated.json()["guests"][0]["jira_write_all"] is True
    assert save(client, 1, {}, guest_jira_write_all=flags).json() == updated.json()
    assert save(client, 0, {}, guest_jira_write_all={"guest": False}).status_code == 409
    with closing(connect(settings.db_path)) as conn:
        audit = conn.execute(
            "SELECT user, detail FROM audit WHERE event = 'mcp_tool_permissions'"
        ).fetchall()
        assert len(audit) == 1 and audit[0]["user"] == "rm"
        assert json.loads(audit[0]["detail"]) == [{
            "username": "guest", "permission": "guest_jira_write_all", "before": False, "after": True,
        }]
        users_repo.update_user_role(conn, "guest", role="QA")
        users_repo.update_user_role(conn, "guest", role="Guest")
        conn.commit()
    assert client.get(PATH).json()["guests"][0]["jira_write_all"] is False
    assert save(client, 1, {}, guest_jira_write_all=flags).status_code == 200
    with closing(connect(settings.db_path)) as conn:
        users_repo.delete_user(conn, "guest")
        users_repo.insert_user(conn, username="guest", password_hash="", role="Guest")
        conn.commit()
    assert client.get(PATH).json()["guests"][0]["jira_write_all"] is False


def test_guest_write_all_is_individual_requires_verification_and_rechecks_grants(site, monkeypatch):
    client, current, token_for, calls = site
    with closing(connect(settings.db_path)) as conn:
        users_repo.insert_user(conn, username="other_guest", password_hash="", role="Guest")
        conn.commit()
    token = token_for("guest", "Guest")
    session = connect_verified(client, token)
    unverified_session, _ = connect_client(client, token)
    other_token = token_for("other_guest", "Guest")
    other_session, _ = connect_client(client, other_token)
    original_tools = listed(client, token, session)
    writes = {"jira_add_comment", "jira_upload_attachment"}
    assert not original_tools & writes
    uploads = []
    monkeypatch.setattr(jira, "upload_agent_attachment", lambda *args: uploads.append(args) or "900")
    monkeypatch.setattr(jira, "account_can_browse", lambda username, project_key="", issue_key="":
                        username == "guest" and (project_key or issue_key.split("-")[0]) in {"MC3", "PHS"})

    current.update(username="rm", role="RM")
    enabled = save(client, 0, {}, guest_jira_write_all={"guest": True})
    assert enabled.status_code == 200
    assert enabled.json()["guests"][0]["jira_write_all"] is True
    assert listed(client, token, session) == original_tools | writes
    assert listed(client, other_token, other_session) == original_tools
    current.update(username="guest", role="Guest")
    info = client.get("/api/mcp/info").json()
    assert info["guest_jira_write_all"] is True and info["default_jira_projects"] == ["MC3"]
    assert info["can_view_all"] is False
    assert not call(client, token, session, "jira_pull_issue", {"issue_key": "PHS-1"})["isError"]
    assert call(client, token, unverified_session, "jira_pull_issue", {"issue_key": "PHS-1"})["isError"]

    def upload(key, session_id=session):
        return call(client, token, session_id, "jira_upload_attachment",
                    {"issue_key": key, "file_path": "test.txt"})

    assert upload("PHS-1", unverified_session)["structuredContent"]["status"] == "identity_required"
    inaccessible = upload("SECRET-1")
    assert inaccessible["isError"] and "无权浏览 JIRA 项目 SECRET" in inaccessible["content"][0]["text"]
    comments = confirmable_call(client, token, session, "jira_add_comment",
                                {"issue_key": "PHS-1", "body": "guest comment"},
                                {"result": {"action": "accept", "content": {"confirm": True}}})
    assert not comments[-1]["result"]["isError"]
    assert [key for key, _ in calls["comments"]] == ["PHS-1"]

    paths = []
    for key in ("PHS-1", "MC3-2"):
        issued = upload(key)
        assert not issued["isError"]
        paths.append(re.search(r"(/api/mcp/uploads/[\w-]+)", issued["content"][0]["text"]).group(1))
    assert client.put(paths[0], content=b"text").status_code == 200
    current.update(username="rm", role="RM")
    assert save(client, 1, {}, guest_jira_write_all={"guest": False}).status_code == 200
    assert listed(client, token, session) == original_tools
    assert client.put(paths[1], content=b"text").status_code == 403
    assert uploads == [("PHS-1", "test.txt", b"text")]
    with closing(connect(settings.db_path)) as conn:
        last = conn.execute(
            "SELECT detail FROM audit WHERE event = 'mcp_tool_permissions' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        assert json.loads(last["detail"]) == [{
            "username": "guest", "permission": "guest_jira_write_all", "before": True, "after": False,
        }]


def test_existing_guest_grants_preserve_all_access_without_promoting_assigned_access(tmp_path):
    db = tmp_path / "previous.db"
    with closing(sqlite3.connect(db)) as conn:
        conn.executescript("""
            CREATE TABLE users (
                username TEXT PRIMARY KEY, password_hash TEXT NOT NULL DEFAULT '',
                role TEXT NOT NULL, auth_source TEXT NOT NULL DEFAULT 'local',
                display_name TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE mcp_guest_jira_writers (
                username TEXT NOT NULL PRIMARY KEY REFERENCES users(username) ON DELETE CASCADE,
                jira_write INTEGER NOT NULL DEFAULT 1,
                jira_write_all INTEGER NOT NULL DEFAULT 0
            );
            INSERT INTO users(username, role) VALUES ('assigned', 'Guest'), ('all', 'Guest'), ('reader', 'Guest');
            INSERT INTO mcp_guest_jira_writers VALUES ('assigned', 1, 0), ('all', 0, 1);
        """)
    with closing(connect(db)) as conn:
        grants = {guest["username"]: guest["jira_write_all"] for guest in mcp_repo.list_guest_jira_access(conn)}
        assert grants == {"assigned": False, "all": True, "reader": False, "guest": False}
        assert not mcp_repo.guest_can_write_all_jira(conn, "assigned")
        assert mcp_repo.guest_can_write_all_jira(conn, "all")
