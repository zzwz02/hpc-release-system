"""Reviewed publication through real HTTP and the fake Codex WebSocket server."""
import io
from zipfile import ZipFile

from app.config import settings
from app.db.connection import transaction
from app.integrations import jira
from app.repositories import jira_agent_repo as repo
from app.services import jira_agent_runner as execution
from tests.test_jira_agent import env, _handover, _wait, _done, _latest, _detail


def _draft_body(env, turn):
    return env.client.get(f"/api/jira-agent/turns/{turn['id']}/comment").json()["body"]


def _send_comment(env, turn, body=None, file_ids=None):
    url = f"/api/jira-agent/turns/{turn['id']}/comment"
    current = env.client.get(url).json()
    response = env.client.post(url + "/send", json={"body": body or current["body"],
        "file_ids": file_ids or [], "revision": current["revision"]})
    assert response.status_code == 200, response.text
    return response.json()["turn"]


def completed(env, **kwargs):
    cid = _handover(env, **kwargs)["conversation"]["id"]
    detail = _wait(env, cid, _done)
    return detail, _latest(detail)


def test_edited_comment_and_only_selected_artifacts_are_sent_once(env, monkeypatch):
    detail, turn = completed(env)
    artifact = next(f for f in detail["files"] if f["source"] == "artifact")
    calls = []
    monkeypatch.setattr(jira, "upload_agent_attachment", lambda key, name, data: calls.append((key, name, data)) or "att-1")
    _draft_body(env, turn)
    sent = _send_comment(env, turn, "人工修改后的结论。", [artifact["id"]])
    assert sent["comment_status"] == "posted"
    assert env.jira.comments[0][1].startswith("人工修改后的结论。")
    assert len(calls) == 1 and calls[0][1].endswith("fix.patch")
    url = f"/api/jira-agent/turns/{turn['id']}/comment"
    state = env.client.get(url).json()
    assert state["frozen"] and state["uploads"][0]["state"] == "posted"
    for _ in range(2):
        result = env.client.post(url + "/send", json={"body": state["body"], "file_ids": [artifact["id"]], "revision": state["revision"]})
        assert result.status_code == 409
    assert len(calls) == len(env.jira.comments) == 1
    assert env.client.post(url + "/send", json={"body": "different", "file_ids": [], "revision": 1}).status_code == 409
    env.as_user("carol")
    assert env.client.delete(f"/api/jira-agent/conversations/{turn['conversation_id']}").status_code == 200
    with execution.open_db() as conn:
        for table in ("jira_agent_comment_drafts", "jira_agent_comment_revisions", "jira_agent_comment_uploads"):
            assert conn.execute(f"SELECT count(*) FROM {table} WHERE turn_id=?", (turn['id'],)).fetchone()[0] == 0


def test_upload_lost_response_reconciles_before_comment(env, monkeypatch):
    detail, turn = completed(env)
    artifact = next(f for f in detail["files"] if f["source"] == "artifact")
    uploads = []
    def lost(key, name, data):
        uploads.append(name)
        raise TimeoutError("lost response")
    monkeypatch.setattr(jira, "upload_agent_attachment", lost)
    monkeypatch.setattr(jira, "find_agent_attachment", lambda *args: "")
    sent = _send_comment(env, turn, file_ids=[artifact["id"]])
    assert sent["comment_status"] == "failed" and not env.jira.comments
    retry = f"/api/jira-agent/turns/{turn['id']}/comment/retry"
    assert env.client.post(retry).json()["turn"]["comment_status"] == "failed"
    assert len(uploads) == 1 and not env.jira.comments
    env.stop_site(); env.start_site()
    assert not env.jira.comments
    monkeypatch.setattr(jira, "find_agent_attachment", lambda *args: "att-found")
    assert env.client.post(retry).json()["turn"]["comment_status"] == "posted"
    assert len(uploads) == len(env.jira.comments) == 1


def test_permissions_current_assignee_and_foreign_files(env):
    detail, turn = completed(env)
    _draft_body(env, turn)
    url = f"/api/jira-agent/turns/{turn['id']}/comment"
    foreign = next(f for f in detail["files"] if f["source"] == "jira")
    assert env.client.post(url + "/send", json={"body": "text", "file_ids": [foreign["id"]], "revision": 1}).status_code == 400
    env.jira.assignee = "bob"
    assert env.client.post(url + "/generate").status_code == 405
    assert env.client.post(url + "/send", json={"body": "text", "file_ids": [], "revision": 1}).status_code == 403
    env.as_user("carol")
    assert env.client.post(url + "/send", json={"body": "RM reviewed", "file_ids": [], "revision": 0}).status_code == 200
    assert len(env.jira.comments) == 1


def test_historical_posted_turn_cannot_generate_or_send_again(env):
    _, turn = completed(env)
    with execution.open_db() as conn, transaction(conn):
        repo.update_turn(conn, turn["id"], comment_status="posted", comment_id="old-1", comment_body="历史已发布正文", post_comment=0)
    url = f"/api/jira-agent/turns/{turn['id']}/comment"
    current = env.client.get(url).json()
    assert current["frozen"] and current["body"] == "历史已发布正文"
    assert env.client.post(url + "/generate").status_code == 405
    response = env.client.post(url + "/send", json={"body": "new", "file_ids": [], "revision": 1})
    assert response.status_code == 409 and "不能再次发送" in response.text
    assert not env.jira.comments and env.fake.turns == 1


def test_draft_edit_history_version_conflict_and_send_lock(env, monkeypatch):
    detail, turn = completed(env)
    assert turn["comment_body"] and not env.jira.comments
    artifact = next(f for f in detail["files"] if f["source"] == "artifact")
    url = f"/api/jira-agent/turns/{turn['id']}/comment"
    first = env.client.get(url).json()
    history = env.client.get(url + "/history").json()["items"]
    assert [(r["revision"], r["source"], r["editor"]) for r in history] == []
    edit = {"body": "审核后的评论", "file_ids": [artifact["id"]], "revision": first["revision"]}
    saved = env.client.post(url + "/draft", json=edit)
    assert saved.status_code == 200 and saved.json()["revision"] == 1
    assert not env.jira.comments
    stale = env.client.post(url + "/draft", json={**edit, "body": "另一窗口的过期修改"})
    assert stale.status_code == 409
    uploads = []
    monkeypatch.setattr(jira, "upload_agent_attachment", lambda *args: uploads.append(args) or "att-1")
    assert env.client.post(url + "/send", json=edit).status_code == 409
    assert not uploads and not env.jira.comments
    sent = env.client.post(url + "/send", json={**edit, "revision": 1})
    assert sent.status_code == 200 and sent.json()["turn"]["comment_status"] == "posted"
    history = env.client.get(url + "/history").json()["items"]
    assert [(r["revision"], r["source"]) for r in history] == [(2, "sent"), (1, "edited"), (0, "generated")]
    assert history[1]["editor"] == "alice" and history[1]["body"] == edit["body"]
    assert history[1]["file_ids"] == edit["file_ids"] and history[1]["created_at"]
    assert env.client.post(url + "/draft", json={**edit, "revision": 3}).status_code == 409
    assert env.client.post(url + "/send", json={**edit, "revision": 3}).status_code == 409
    env.as_user("bob")
    assert env.client.get(url + "/history").status_code == 404


def test_artifact_preview_zip_and_access_control(env):
    detail, turn = completed(env)
    cid = turn["conversation_id"]
    samples = {"report.md": b"# Result\nPASS", "kernel.cu": b"__global__ void kernel() {}",
               "test.cpp": b"int main() {}", "log.txt": b"PASS\n", "data.bin": b"\x00\x01\xff"}
    records = []
    with execution.open_db() as conn, transaction(conn):
        for name, data in samples.items():
            path = settings.jira_agent_data_dir / cid / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            records.append(repo.add_file(conn, conversation_id=cid, turn_id=turn["id"], direction="output",
                source="artifact", name=name, local_path=str(path), remote_path="artifacts/" + name, size=len(data)))
    for file, kind in zip(records, ["markdown", "code", "code", "text", "binary"]):
        url = f"/api/jira-agent/conversations/{cid}/files/{file['id']}/preview"
        response = env.client.get(url)
        assert response.status_code == 200 and response.json()["kind"] == kind
    zip_url = f"/api/jira-agent/turns/{turn['id']}/artifacts.zip"
    response = env.client.get(zip_url)
    assert response.status_code == 200
    with ZipFile(io.BytesIO(response.content)) as archive:
        for name, data in samples.items():
            assert archive.read("artifacts/" + name) == data
    env.as_user("bob")
    assert env.client.get(zip_url).status_code == 404
    assert env.client.get(url).status_code == 404


def test_old_round_and_input_attachments_cannot_be_selected(env, monkeypatch):
    detail, turn = completed(env, post_comment=True)
    assert not env.jira.comments and turn['post_comment'] is False
    old_file = next(f for f in detail['files'] if f['source'] == 'artifact')
    response = env.client.post(f"/api/jira-agent/conversations/{turn['conversation_id']}/messages",
                               json={'text': '继续分析', 'post_comment': True})
    assert response.status_code == 200
    latest = _latest(_wait(env, turn['conversation_id'], _done))
    assert latest['id'] != turn['id'] and not env.jira.comments
    uploads = []
    monkeypatch.setattr(jira, 'upload_agent_attachment', lambda *args: uploads.append(args) or 'unexpected')
    url = f"/api/jira-agent/turns/{latest['id']}/comment"
    for endpoint in ('/draft', '/send'):
        response = env.client.post(url + endpoint,
            json={'body': '本轮评论', 'file_ids': [old_file['id']], 'revision': 0})
        assert response.status_code == 400
    assert not uploads and not env.jira.comments
    saved = env.client.post(url + '/draft', json={'body': '', 'file_ids': [], 'revision': 0})
    assert saved.status_code == 200
    assert env.client.get(url).json()['body'] == ''
    assert env.client.post(url + '/send', json={'body': '', 'file_ids': [], 'revision': 1}).status_code == 400
    assert env.client.post(url + '/draft', json={'body': '恢复正文', 'file_ids': [], 'revision': 1}).status_code == 200
