"""JIRA agent: hand-over, Codex app-server protocol flow, isolation and queue.

A fake Codex app-server (real websockets, token auth) plays scripted turns;
JIRA calls are replaced by an in-memory fake.
"""
from __future__ import annotations

import asyncio
import base64
import json
import threading
import time
import urllib.error
from http import HTTPStatus
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from websockets.asyncio.server import serve

from app.config import settings
from app.db.connection import transaction
from app.db.jira_agent_connection import connect_jira_agent, reset_jira_agent_init_state
from app.deps import require_login
from app.domain import jira_agent as domain
from app.integrations import jira
from app.main import create_app
from app.repositories import jira_agent_repo as repo
from app.services import jira_agent_runner

TOKEN = "secret-token"

RESULT = {
    "conclusion": "fixed_pending_review",
    "issue_category": "结果错误",
    "ownership": {"belongs_to_us": "yes", "target_group": "", "reasoning": "saxpy.cu 的线程块数量向下取整"},
    "summary": "修复尾部元素未计算",
    "root_cause": "blocks = n / 256 向下取整",
    "reproduction": {"reproduced": True, "environment": "A100", "steps": ["make", "./saxpy 16777217"]},
    "evidence": [{"description": "修复后验证", "command": "./saxpy 16777217", "result": "PASS exit=0"}],
    "fix": {"description": "改为向上取整"},
    "artifacts": ["artifacts/fix.patch", "../../etc/passwd"],
    "next_steps": ["审阅补丁后 resolve"],
    "skills_used": ["hpc-bug-repro"],
}
PATCH = b"--- original/saxpy.cu\n+++ work/saxpy.cu\n-blocks = n / 256;\n+blocks = (n + 255) / 256;\n"


# ─────────────────────────────────────────────────────────────
# Fakes
# ─────────────────────────────────────────────────────────────

class FakeCodex:
    """Minimal codex app-server: threads, turns, steer/interrupt, fs."""

    def __init__(self, token: str = TOKEN) -> None:
        self.token = token
        self.requests: list[tuple[str, dict]] = []
        self.files: dict[str, bytes] = {}
        self.thread_cwd: dict[str, str] = {}
        self.plans: list[str] = []
        self.turn_start_delay = 0.0
        self.threads = 0
        self.turns = 0
        self._interrupts: dict[str, asyncio.Event] = {}
        self._ready = threading.Event()
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run, daemon=True)

    @property
    def url(self) -> str:
        return f"ws://127.0.0.1:{self.port}"

    def start(self) -> FakeCodex:
        self._thread.start()
        assert self._ready.wait(5)
        return self

    def stop(self) -> None:
        self._loop.call_soon_threadsafe(self._stop.set)
        self._thread.join(5)

    def methods(self) -> list[str]:
        return [method for method, _ in self.requests]

    def params(self, method: str) -> list[dict]:
        return [params for name, params in self.requests if name == method]

    def _run(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_until_complete(self._serve())

    async def _serve(self) -> None:
        self._stop = asyncio.Event()
        async with serve(self._handler, "127.0.0.1", 0, process_request=self._auth) as server:
            self.port = next(iter(server.sockets)).getsockname()[1]
            self._ready.set()
            await self._stop.wait()

    def _auth(self, connection, request):
        if request.headers.get("Authorization") != f"Bearer {self.token}":
            return connection.respond(HTTPStatus.UNAUTHORIZED, "unauthorized\n")
        return None

    async def _handler(self, ws) -> None:
        async for raw in ws:
            message = json.loads(raw)
            if "id" not in message:
                continue
            method, params = message["method"], message.get("params") or {}
            self.requests.append((method, params))
            if method == "turn/start" and self.turn_start_delay:
                await asyncio.sleep(self.turn_start_delay)
            result, error = self._result(ws, method, params)
            reply = {"id": message["id"], **({"error": error} if error else {"result": result})}
            await ws.send(json.dumps(reply))

    def _result(self, ws, method: str, params: dict):
        if method == "thread/start":
            self.threads += 1
            thread_id = f"thr_{self.threads}"
            self.thread_cwd[thread_id] = params["cwd"]
            return {"thread": {"id": thread_id, "cwd": params["cwd"]}}, None
        if method == "thread/resume":
            return {"thread": {"id": params["threadId"]}}, None
        if method == "fs/writeFile":
            self.files[params["path"]] = base64.b64decode(params["dataBase64"])
            return {}, None
        if method == "fs/readFile":
            if params["path"] not in self.files:
                return None, {"code": -32000, "message": "No such file"}
            return {"dataBase64": base64.b64encode(self.files[params["path"]]).decode()}, None
        if method == "turn/start":
            self.turns += 1
            turn_id = f"turn_{self.turns}"
            self._interrupts[turn_id] = asyncio.Event()
            plan = self.plans.pop(0) if self.plans else "complete"
            asyncio.get_running_loop().call_later(0.05, lambda: asyncio.ensure_future(self._play(ws, params, turn_id, plan)))
            return {"turn": {"id": turn_id, "status": "inProgress", "items": []}}, None
        if method == "turn/interrupt":
            self._interrupts.setdefault(params["turnId"], asyncio.Event()).set()
            return {}, None
        if method == "turn/steer":
            return {"turnId": params["expectedTurnId"]}, None
        return {}, None  # initialize, fs/createDirectory, thread/archive

    async def _play(self, ws, params: dict, turn_id: str, plan: str) -> None:
        thread_id = params["threadId"]

        async def notify(method: str, extra: dict) -> None:
            await ws.send(json.dumps({"method": method, "params": {"threadId": thread_id, "turnId": turn_id, **extra}}))

        command = {
            "type": "commandExecution", "id": f"cmd_{turn_id}", "command": "./saxpy 16777217",
            "cwd": "/b", "status": "inProgress", "commandActions": [], "exitCode": None,
            "aggregatedOutput": None, "durationMs": None,
        }
        await notify("item/started", {"item": command, "startedAtMs": 1})
        if plan == "hold":
            await self._interrupts[turn_id].wait()
            status = "interrupted"
        else:
            await notify("item/completed", {"item": {**command, "status": "completed", "exitCode": 0, "aggregatedOutput": "PASS\n", "durationMs": 12}, "completedAtMs": 2})
            await notify("item/completed", {"item": {"type": "agentMessage", "id": f"note_{turn_id}", "text": "开始复现", "phase": "commentary"}, "completedAtMs": 3})
            self.files[f"{self.thread_cwd.get(thread_id, '/b')}/artifacts/fix.patch"] = PATCH
            await notify("item/completed", {"item": {"type": "agentMessage", "id": f"final_{turn_id}", "text": json.dumps(RESULT, ensure_ascii=False), "phase": "final_answer"}, "completedAtMs": 4})
            status = "completed"
        await ws.send(json.dumps({"method": "turn/completed", "params": {"threadId": thread_id, "turn": {"id": turn_id, "status": status, "items": [], "error": None}}}))


class FakeJira:
    def __init__(self) -> None:
        self.assignee = "alice"
        self.comments: list[tuple[str, str]] = []
        self.searches: list[str] = []
        self.fail_comment = False

    def get_issue(self, key: str) -> dict:
        if key == "NOPE-1":
            raise urllib.error.HTTPError("http://jira", 404, "Not Found", {}, None)
        return {
            "key": key, "url": f"http://jira/browse/{key}", "summary": "saxpy 尾部元素错误",
            "issue_type": "Bug", "status": "Open", "priority": "High", "project": "MACA 3.0",
            "assignee": {"name": self.assignee, "display_name": self.assignee} if self.assignee else None,
            "reporter": {"name": "rep", "display_name": "rep"}, "components": ["PDE_HPC"], "labels": [],
            "description": "./saxpy 16777217 应输出 PASS", "created": "", "updated": "",
            "comments": [{"id": "1", "author": "rep", "created": "", "body": "可以使用 10.2.118.75"}],
            "attachments": [{"id": "10", "filename": "saxpy.cu", "size": 12, "mime_type": "text/plain", "content_url": "http://jira/secure/attachment/10/saxpy.cu"}],
        }

    def search_issues(self, jql: str, *, max_results: int = 50) -> dict:
        self.searches.append(jql)
        if "bad" in jql:
            raise jira.JiraQueryError("Error in JQL Query: bad")
        issue = self.get_issue("MC3-7672")
        return {"total": 1, "issues": [{k: issue[k] for k in ("key", "url", "summary", "issue_type", "status", "priority", "assignee", "components", "updated")}]}

    def download_attachment(self, url: str) -> bytes:
        return b"int main(){}"

    def add_comment(self, key: str, body: str) -> str:
        if self.fail_comment:
            raise RuntimeError("issue is closed")
        self.comments.append((key, body))
        return str(len(self.comments))


USERS = {
    "alice": {"username": "alice", "display_name": "Alice", "role": "Owner"},
    "bob": {"username": "bob", "display_name": "Bob", "role": "Owner"},
    "carol": {"username": "carol", "display_name": "Carol", "role": "RM"},
}


def _write_conf(path: Path, url: str, *, token: str = TOKEN, max_concurrent: int = 1, timeout: int = 600) -> None:
    path.write_text(
        f"[HPC]\nDISPLAY_NAME = HPC 数字员工\nCODEX_WS_URL = {url}\nCODEX_WS_TOKEN = {token}\n"
        f"WORKSPACE_ROOT = /b/workspaces\nCOMPONENTS = PDE_HPC\nJIRA_MEMBERS_GROUP = pde_hpc\n"
        f"MAX_CONCURRENT = {max_concurrent}\n"
        f"TURN_TIMEOUT_SECONDS = {timeout}\n",
        encoding="utf-8",
    )


@pytest.fixture()
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    fake = FakeCodex().start()
    conf = tmp_path / "jira_agent.conf"
    _write_conf(conf, fake.url)
    reset_jira_agent_init_state()
    monkeypatch.setattr(settings, "db_path", tmp_path / "main.db")
    monkeypatch.setattr(settings, "admin_password_file", tmp_path / "admin.local")
    monkeypatch.setattr(settings, "jira_agent_conf_path", conf)
    monkeypatch.setattr(settings, "jira_agent_database_url", f"sqlite:///{(tmp_path / 'jira_agent.db').as_posix()}")
    monkeypatch.setattr(settings, "jira_agent_data_dir", tmp_path / "data")
    monkeypatch.setattr(settings, "jira_agent_public_base_url", "http://site")
    monkeypatch.setattr(jira_agent_runner, "_SCAN_SECONDS", 0.1)
    fake_jira = FakeJira()
    monkeypatch.setattr(jira, "get_issue", fake_jira.get_issue)
    monkeypatch.setattr(jira, "download_attachment", fake_jira.download_attachment)
    monkeypatch.setattr(jira, "search_issues", fake_jira.search_issues)
    monkeypatch.setattr(jira, "add_comment", fake_jira.add_comment)

    current = {"user": USERS["alice"]}
    app = create_app()
    app.dependency_overrides[require_login] = lambda: current["user"]

    def as_user(name: str) -> None:
        current["user"] = USERS[name]

    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            yield SimpleNamespace(client=client, fake=fake, jira=fake_jira, conf=conf, tmp=tmp_path, as_user=as_user)
    finally:
        fake.stop()
        reset_jira_agent_init_state()


def _detail(env, conversation_id: str) -> dict:
    response = env.client.get(f"/api/jira-agent/conversations/{conversation_id}")
    assert response.status_code == 200, response.text
    return response.json()


def _wait(env, conversation_id: str, predicate, timeout: float = 15.0) -> dict:
    deadline = time.monotonic() + timeout
    while True:
        detail = _detail(env, conversation_id)
        if predicate(detail):
            return detail
        if time.monotonic() > deadline:
            raise AssertionError(f"condition not reached: {json.dumps(detail, ensure_ascii=False)[:3000]}")
        time.sleep(0.05)


def _latest(detail: dict) -> dict:
    return detail["conversation"]["latest_turn"]


def _done(detail: dict) -> bool:
    turn = _latest(detail)
    return turn["status"] not in ("queued", "running") and turn["comment_status"] != "pending"


def _handover(env, key: str = "MC3-7672", **extra) -> dict:
    response = env.client.post("/api/jira-agent/conversations", json={"issue_key": key, **extra})
    assert response.status_code == 200, response.text
    return response.json()


# ─────────────────────────────────────────────────────────────
# Full flow
# ─────────────────────────────────────────────────────────────

def test_handover_runs_codex_turn_records_timeline_and_comments(env) -> None:
    upload = {"filename": "notes.txt", "content_base64": base64.b64encode(b"use A100").decode()}
    created = _handover(env, "mc3-7672", note="请修复", files=[upload])
    assert created["created"] is True
    conversation = created["conversation"]
    assert conversation["owner"] == "alice"
    assert conversation["workspace"] == f"/b/workspaces/MC3-7672-{conversation['id']}"

    detail = _wait(env, conversation["id"], _done)
    turn = _latest(detail)
    assert turn["status"] == "completed"
    assert turn["conclusion"] == "fixed_pending_review"
    assert turn["comment_status"] == "posted"
    assert detail["conversation"]["state"] == "waiting_review"

    fake = env.fake
    assert fake.methods()[0] == "initialize"
    workspace = conversation["workspace"]
    assert fake.params("thread/start")[0]["cwd"] == workspace
    assert "sandbox" not in fake.params("thread/start")[0]  # policy belongs to server B
    turn_start = fake.params("turn/start")[0]
    assert turn_start["outputSchema"] == domain.RESULT_SCHEMA
    assert "请修复" in turn_start["input"][0]["text"]
    assert "uploads/notes.txt" in turn_start["input"][0]["text"]
    assert fake.files[f"{workspace}/attachments/saxpy.cu"] == b"int main(){}"
    assert fake.files[f"{workspace}/uploads/notes.txt"] == b"use A100"
    assert "attachments/saxpy.cu" in fake.files[f"{workspace}/issue.md"].decode()
    assert detail["conversation"]["thread_id"] == "thr_1"

    kinds = [event["kind"] for event in detail["events"]]
    for kind in ("user_message", "command", "agent_message", "result", "jira_comment"):
        assert kind in kinds
    command = next(event for event in detail["events"] if event["kind"] == "command")
    assert command["payload"]["status"] == "completed"
    assert command["payload"]["exit_code"] == 0
    assert "PASS" in command["payload"]["output"]
    assert any("不在工作目录内" in e["payload"].get("text", "") for e in detail["events"] if e["kind"] == "error")

    [(key, body)] = env.jira.comments
    assert key == "MC3-7672"
    assert "结论：已修复，方案请审批" in body
    assert "blocks = (n + 255) / 256;" in body
    assert f"http://site/jira-agent?conversation={conversation['id']}" in body

    patch = next(f for f in detail["files"] if f["source"] == "artifact")
    download = env.client.get(f"/api/jira-agent/conversations/{conversation['id']}/files/{patch['id']}")
    assert download.status_code == 200
    assert download.content == PATCH


def test_only_current_assignee_or_rm_can_hand_over(env) -> None:
    env.as_user("bob")
    response = env.client.post("/api/jira-agent/conversations", json={"issue_key": "MC3-1"})
    assert response.status_code == 403

    env.as_user("carol")  # RM acts on behalf; owner stays the assignee
    conversation = _handover(env, "MC3-1")["conversation"]
    assert (conversation["owner"], conversation["created_by"]) == ("alice", "carol")

    env.as_user("bob")
    assert env.client.get(f"/api/jira-agent/conversations/{conversation['id']}").status_code == 404
    assert env.client.get("/api/jira-agent/conversations").json()["conversations"] == []

    env.as_user("alice")
    assert env.client.get("/api/jira-agent/issues/NOPE-1").status_code == 404
    preview = env.client.get("/api/jira-agent/issues/MC3-1").json()
    assert preview["can_handover"] is True
    assert preview["open_conversation"]["id"] == conversation["id"]


def test_issue_search_default_list_keys_and_jql(env) -> None:
    mine = env.client.get("/api/jira-agent/issues", params={"q": ""}).json()
    assert mine["mode"] == "mine"
    assert mine["jql"] == 'assignee = "alice" AND status != Closed ORDER BY updated DESC'
    assert mine["issues"][0]["can_handover"] is True

    env.as_user("carol")  # RM: the configured HPC JIRA group
    rm = env.client.get("/api/jira-agent/issues", params={"q": ""}).json()
    assert rm["jql"] == 'assignee in membersOf("pde_hpc") AND status != Closed ORDER BY updated DESC'

    env.as_user("alice")
    conversation = _handover(env, "MC3-2")["conversation"]
    keys = env.client.get(
        "/api/jira-agent/issues", params={"q": "mc3-1, MC3-2 NOPE-1"}
    ).json()
    assert keys["mode"] == "keys"
    assert keys["jql"] == ""
    assert [issue["key"] for issue in keys["issues"]] == ["MC3-1", "MC3-2"]
    assert keys["missing"] == ["NOPE-1"]
    assert keys["issues"][0]["open_conversation"] is None
    open_conversation = keys["issues"][1]["open_conversation"]
    assert {k: open_conversation[k] for k in ("id", "owner", "owner_is_assignee")} == {
        "id": conversation["id"], "owner": "alice", "owner_is_assignee": True,
    }
    assert open_conversation["state"] in {"queued", "running", "waiting_review"}

    env.as_user("bob")  # not the owner: sees that a conversation exists, not its id
    hidden = env.client.get("/api/jira-agent/issues", params={"q": "MC3-2"}).json()
    assert hidden["issues"][0]["open_conversation"]["id"] == ""
    assert hidden["issues"][0]["can_handover"] is False

    jql = env.client.get("/api/jira-agent/issues", params={"q": "project = MC3 AND status = Open"}).json()
    assert jql["mode"] == "jql"
    assert env.jira.searches[-1] == "project = MC3 AND status = Open"

    bad = env.client.get("/api/jira-agent/issues", params={"q": "bad jql"})
    assert bad.status_code == 400
    assert "Error in JQL Query" in bad.json()["error"]


def test_parse_issue_query() -> None:
    assert domain.parse_issue_query("  ") == ("mine", "")
    assert domain.parse_issue_query("mc3-7672") == ("keys", ["MC3-7672"])
    assert domain.parse_issue_query("PDE_HPC-12，MC3-1 MC3-1") == ("keys", ["PDE_HPC-12", "MC3-1"])
    # browse URLs are JQL input (business JIRA runs at other addresses)
    assert domain.parse_issue_query("http://jira:8080/browse/SPD-9") == ("jql", "http://jira:8080/browse/SPD-9")
    assert domain.parse_issue_query("MC3") == ("jql", "MC3")
    assert domain.parse_issue_query("key = MC3-1") == ("jql", "key = MC3-1")
    assert domain.parse_issue_query("MC3-1 OR MC3-2") == ("jql", "MC3-1 OR MC3-2")
    assert domain.default_issue_jql(username='a"b', is_rm=False, groups={}) == (
        'assignee = "a\\"b" AND status != Closed ORDER BY updated DESC'
    )


def test_followup_resumes_the_same_thread_and_comments_again(env) -> None:
    conversation = _handover(env)["conversation"]
    _wait(env, conversation["id"], _done)

    response = env.client.post(
        f"/api/jira-agent/conversations/{conversation['id']}/messages",
        json={"text": "补充 N=257 验证"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["mode"] == "queued"

    detail = _wait(env, conversation["id"], lambda d: _latest(d)["seq"] == 2 and _done(d))
    assert _latest(detail)["status"] == "completed"
    assert env.fake.threads == 1
    assert env.fake.params("thread/resume")[0]["threadId"] == "thr_1"
    assert "补充 N=257 验证" in env.fake.params("turn/start")[1]["input"][0]["text"]
    assert len(env.jira.comments) == 2


# ─────────────────────────────────────────────────────────────
# Conversation isolation
# ─────────────────────────────────────────────────────────────

def test_new_conversation_for_same_assignee_and_assignee_changes(env) -> None:
    first = _handover(env)["conversation"]
    _wait(env, first["id"], _done)

    again = _handover(env)
    assert again["created"] is False
    assert again["conversation"]["id"] == first["id"]

    second = _handover(env, new_conversation=True)["conversation"]
    assert second["id"] != first["id"]
    assert second["workspace"] != first["workspace"]
    old = _detail(env, first["id"])["conversation"]
    assert (old["status"], old["close_reason"], old["read_only"]) == ("closed", "new_conversation", True)
    _wait(env, second["id"], _done)
    assert env.fake.threads == 2
    deadline = time.monotonic() + 5
    while {"threadId": "thr_1"} not in env.fake.params("thread/archive"):
        assert time.monotonic() < deadline, "old thread was not archived"
        time.sleep(0.05)

    closed = env.client.post(f"/api/jira-agent/conversations/{first['id']}/messages", json={"text": "继续"})
    assert closed.status_code == 409

    # alice hands the issue to bob: alice's conversation ends read-only.
    env.jira.assignee = "bob"
    stale = env.client.post(f"/api/jira-agent/conversations/{second['id']}/messages", json={"text": "继续"})
    assert stale.status_code == 409
    assert "assignee 已变更" in stale.json()["error"]
    assert _detail(env, second["id"])["conversation"]["close_reason"] == "superseded"

    env.as_user("bob")
    third = _handover(env)["conversation"]
    assert third["owner"] == "bob"
    _wait(env, third["id"], _done)

    # back to alice: still a brand-new conversation, nothing revived.
    env.jira.assignee = "alice"
    env.as_user("alice")
    fourth = _handover(env)["conversation"]
    assert fourth["id"] not in {first["id"], second["id"], third["id"]}
    assert fourth["owner"] == "alice"
    assert env.client.get(f"/api/jira-agent/conversations/{third['id']}").status_code == 404  # bob's
    env.as_user("carol")
    assert _detail(env, third["id"])["conversation"]["close_reason"] == "superseded"

    conn = connect_jira_agent(settings.jira_agent_database_url)
    try:
        open_rows = conn.execute(
            "SELECT COUNT(*) FROM jira_agent_conversations WHERE issue_key = 'MC3-7672' AND status = 'open'"
        ).fetchone()[0]
    finally:
        conn.close()
    assert open_rows == 1


# ─────────────────────────────────────────────────────────────
# Queue and control
# ─────────────────────────────────────────────────────────────

def test_queue_limit_merge_steer_and_cancel(env) -> None:
    env.fake.plans = ["hold", "complete"]
    running = _handover(env, "MC3-1")["conversation"]
    _wait(env, running["id"], lambda d: d["conversation"]["phase"] == "running")

    queued = _handover(env, "MC3-2", note="第一条")["conversation"]
    third = _handover(env, "MC3-3")["conversation"]
    detail = _detail(env, queued["id"])
    assert _latest(detail)["status"] == "queued"
    assert _latest(detail)["queue_position"] == 1
    assert _latest(_detail(env, third["id"]))["queue_position"] == 2

    merged = env.client.post(f"/api/jira-agent/conversations/{queued['id']}/messages", json={"text": "第二条"})
    assert merged.json()["mode"] == "merged"
    assert len(_detail(env, queued["id"])["turns"]) == 1

    steer = env.client.post(f"/api/jira-agent/conversations/{running['id']}/messages", json={"text": "换 GPU0"})
    assert steer.status_code == 200, steer.text
    assert steer.json()["mode"] == "steer"
    assert env.fake.params("turn/steer")[0]["input"][0]["text"] == "换 GPU0"

    assert env.client.post(f"/api/jira-agent/conversations/{third['id']}/cancel").status_code == 200
    assert _latest(_detail(env, third["id"]))["status"] == "cancelled"

    assert env.client.post(f"/api/jira-agent/conversations/{running['id']}/cancel").status_code == 200
    stopped = _wait(env, running["id"], _done)
    assert _latest(stopped)["status"] == "cancelled"
    assert env.fake.params("turn/interrupt")[0]["turnId"] == "turn_1"

    finished = _wait(env, queued["id"], _done)
    assert _latest(finished)["status"] == "completed"
    prompt = env.fake.params("turn/start")[1]["input"][0]["text"]
    assert "第一条" in prompt and "第二条" in prompt
    assert env.fake.turns == 2  # the cancelled queued turn never started


def test_cancel_while_turn_start_is_in_flight_interrupts_the_turn(env) -> None:
    # turn/start has reached the app-server but its response (the turn id)
    # has not come back yet: the turn must still be interrupted on server B.
    env.fake.plans = ["hold"]
    env.fake.turn_start_delay = 1.5
    conversation = _handover(env)["conversation"]
    _wait(env, conversation["id"], lambda d: d["conversation"]["phase"] == "starting")
    assert "turn/start" in env.fake.methods()

    assert env.client.post(f"/api/jira-agent/conversations/{conversation['id']}/cancel").status_code == 200
    detail = _wait(env, conversation["id"], _done)
    assert _latest(detail)["status"] == "cancelled"
    assert env.fake.params("turn/interrupt") == [{"threadId": "thr_1", "turnId": "turn_1"}]


def test_turn_timeout_is_counted_from_dequeue(env) -> None:
    _write_conf(env.conf, env.fake.url, timeout=1)
    env.fake.plans = ["hold"]
    conversation = _handover(env)["conversation"]
    detail = _wait(env, conversation["id"], _done)
    assert _latest(detail)["status"] == "failed"
    assert "限时" in _latest(detail)["error"]
    assert env.fake.params("turn/interrupt")


def test_comment_failure_can_be_retried_without_rerunning(env) -> None:
    env.jira.fail_comment = True
    conversation = _handover(env)["conversation"]
    turn = _latest(_wait(env, conversation["id"], _done))
    assert (turn["status"], turn["comment_status"]) == ("completed", "failed")

    env.jira.fail_comment = False
    retried = env.client.post(f"/api/jira-agent/turns/{turn['id']}/comment/retry")
    assert retried.status_code == 200, retried.text
    assert retried.json()["turn"]["comment_status"] == "posted"
    assert env.fake.turns == 1
    assert len(env.jira.comments) == 1


def test_health_reports_bad_token(env) -> None:
    [group] = env.client.get("/api/jira-agent/health").json()["groups"]
    assert group["ok"] is True
    _write_conf(env.conf, env.fake.url, token="wrong")
    [group] = env.client.get("/api/jira-agent/health").json()["groups"]
    assert group["ok"] is False
    assert "401" in group["error"]


def test_startup_marks_leftover_running_turns_interrupted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    reset_jira_agent_init_state()
    url = f"sqlite:///{(tmp_path / 'jira_agent.db').as_posix()}"
    monkeypatch.setattr(settings, "jira_agent_database_url", url)
    monkeypatch.setattr(settings, "jira_agent_conf_path", tmp_path / "missing.conf")
    conn = connect_jira_agent(url)
    with transaction(conn):
        repo.create_conversation(
            conn, conversation_id="jac_1", issue_key="MC3-1", issue_summary="", agent_group="HPC",
            owner="alice", created_by="alice", workspace="/b/w",
        )
        turn = repo.create_turn(conn, conversation_id="jac_1", trigger="handover", created_by="alice", input_text="")
        queued_conv = repo.create_conversation(
            conn, conversation_id="jac_2", issue_key="MC3-2", issue_summary="", agent_group="NOPE",
            owner="alice", created_by="alice", workspace="/b/w2",
        )
        repo.create_turn(conn, conversation_id=queued_conv["id"], trigger="handover", created_by="alice", input_text="")
        assert repo.claim_turn(conn, turn["id"])

    runner = jira_agent_runner.JiraAgentRunner()

    async def cycle() -> None:
        await runner.start()
        await asyncio.sleep(0.2)
        await runner.stop()

    asyncio.run(cycle())
    assert repo.get_turn(conn, turn["id"])["status"] == "interrupted"
    other = repo.latest_turn(conn, "jac_2")
    assert other["status"] == "failed"  # queued turn of an unconfigured group
    conn.close()
    reset_jira_agent_init_state()


# ─────────────────────────────────────────────────────────────
# Pure helpers
# ─────────────────────────────────────────────────────────────

def test_workspace_paths_cannot_escape() -> None:
    root = "/b/workspaces/MC3-1-jac_1"
    assert domain.resolve_workspace_path(root, "artifacts/fix.patch") == f"{root}/artifacts/fix.patch"
    assert domain.resolve_workspace_path(root, f"{root}/artifacts/a.log") == f"{root}/artifacts/a.log"
    assert domain.resolve_workspace_path(root, "../MC3-1-jac_2/x") is None
    assert domain.resolve_workspace_path(root, "/etc/passwd") is None
    assert domain.safe_filename("../../etc/passwd") == "passwd"


def test_group_selection_and_comment_rendering(tmp_path: Path) -> None:
    conf = tmp_path / "jira_agent.conf"
    conf.write_text(
        "[HPC]\nCODEX_WS_URL = ws://b:1\nWORKSPACE_ROOT = /w\nCOMPONENTS = PDE_HPC\n"
        "[PYTORCH]\nCODEX_WS_URL = ws://p:1\nWORKSPACE_ROOT = /p\nCOMPONENTS = PDE_PYTORCH\n",
        encoding="utf-8",
    )
    groups = domain.load_groups(conf)
    assert domain.group_for_issue(groups, ["pde_pytorch"]).name == "PYTORCH"
    assert domain.group_for_issue(groups, ["OTHER"]).name == "HPC"

    body = domain.render_jira_comment(
        group=groups["HPC"],
        result={**RESULT, "conclusion": "not_our_group",
                "ownership": {"belongs_to_us": "no", "target_group": "PyTorch 组", "reasoning": "算子报错"}},
        turn_seq=1, owner="alice", conversation_url="", patches=[],
    )
    assert "结论：经分析需其他组负责" in body
    assert "不属于本组（建议由 PyTorch 组 负责）" in body
    assert "不会修改 assignee" in body
