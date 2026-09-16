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
from websockets.exceptions import ConnectionClosed

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
KEY_PATH = "/home/agent/.ssh/id_ed25519"
# ssh-keygen -l prints: 256 SHA256:lrNsVsx6+k+Hy/d+CeYkXjLtXLZxRxAKqM9wH/H/KuI hpc-jira-agent@B (ED25519)
PUBKEY = b"ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIDbFODJI3urCdOwD3QXJpRYlnRjEo08FkDvSkoHHZI9U hpc-jira-agent@B\n"
FINGERPRINT = "SHA256:lrNsVsx6+k+Hy/d+CeYkXjLtXLZxRxAKqM9wH/H/KuI"
SYSTEM_MACHINE = "hpc@10.2.118.75"

RESULT = {
    "conclusion": "fixed_pending_review",
    "issue_category": "结果错误",
    "ownership": {"belongs_to_us": "yes", "target_group": "", "reasoning": "saxpy.cu 的线程块数量向下取整"},
    "summary": "修复尾部元素未计算",
    "root_cause": "blocks = n / 256 向下取整",
    "machine": SYSTEM_MACHINE,
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
    """Minimal codex app-server: threads, turns, steer/interrupt, fs.

    Like the real one, a turn keeps running when its connection closes, the
    connection that last started/resumed a thread receives its notifications,
    and turn/start on a thread with a running turn joins that turn.
    """

    def __init__(self, token: str = TOKEN) -> None:
        self.token = token
        self.requests: list[tuple[str, dict]] = []
        self.files: dict[str, bytes] = {}
        self.thread_cwd: dict[str, str] = {}
        self.plans: list[str] = []
        self.turn_start_delay = 0.0
        self.resume_delay = 0.0
        self.threads = 0
        self.turns = 0
        self.turn_state: dict[str, dict] = {}  # turn id -> Turn as thread/turns/list returns it
        self.thread_turns: dict[str, list[str]] = {}
        # command/exec: tty processes ask for a password (ssh-copy-id); other
        # commands (the BatchMode check) exit with verify_exit.
        self.ssh_password = "s3cret"
        self.verify_exit = 0
        self.processes: dict[str, dict] = {}
        self._subscriber: dict[str, object] = {}  # thread id -> connection receiving its notifications
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

    def release(self) -> None:
        """Let "gate" turns finish."""
        self._loop.call_soon_threadsafe(self._release.set)

    def _run(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_until_complete(self._serve())

    async def _serve(self) -> None:
        self._stop = asyncio.Event()
        self._release = asyncio.Event()
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
            if method == "thread/resume" and self.resume_delay:
                await asyncio.sleep(self.resume_delay)
            if method == "command/exec" and params.get("tty"):
                # answered when the process exits, like the real app-server,
                # which kills it with exit 124 once timeoutMs has passed
                process_id = params["processId"]
                process = self.processes[process_id] = {"ws": ws, "id": message["id"], "input": b""}
                await self._process_output(process_id, b"tester@host's password: ")
                asyncio.get_running_loop().call_later(
                    params["timeoutMs"] / 1000,
                    lambda: asyncio.ensure_future(self._process_timeout(process_id, process)),
                )
                continue
            result, error = self._result(ws, method, params)
            reply = {"id": message["id"], **({"error": error} if error else {"result": result})}
            try:
                await ws.send(json.dumps(reply))
            except ConnectionClosed:  # the client went away; the work carries on
                return

    def _running_turn(self, thread_id: str) -> str:
        return next((t for t in self.thread_turns.get(thread_id, []) if self.turn_state[t]["status"] == "inProgress"), "")

    def _result(self, ws, method: str, params: dict):
        if method == "thread/start":
            self.threads += 1
            thread_id = f"thr_{self.threads}"
            self.thread_cwd[thread_id] = params["cwd"]
            self.thread_turns[thread_id] = []
            self._subscriber[thread_id] = ws
            return {"thread": {"id": thread_id, "cwd": params["cwd"]}}, None
        if method == "thread/resume":
            thread_id = params["threadId"]
            self._subscriber[thread_id] = ws
            status = "active" if self._running_turn(thread_id) else "idle"
            return {"thread": {"id": thread_id, "status": {"type": status, **({"activeFlags": []} if status == "active" else {})}}}, None
        if method == "thread/turns/list":
            ids = list(reversed(self.thread_turns.get(params["threadId"], [])))[: params.get("limit") or 25]
            data = [dict(self.turn_state[t], items=list(self.turn_state[t]["items"])) for t in ids]
            if params.get("itemsView") == "notLoaded":
                data = [dict(turn, items=[]) for turn in data]
            return {"data": data}, None
        if method == "fs/writeFile":
            self.files[params["path"]] = base64.b64decode(params["dataBase64"])
            return {}, None
        if method == "fs/readFile":
            if params["path"] not in self.files:
                return None, {"code": -32000, "message": "No such file"}
            return {"dataBase64": base64.b64encode(self.files[params["path"]]).decode()}, None
        if method == "turn/start":
            running = self._running_turn(params["threadId"])
            if running:
                return {"turn": {"id": running, "status": "inProgress", "items": []}}, None
            self.turns += 1
            turn_id = f"turn_{self.turns}"
            self._interrupts[turn_id] = asyncio.Event()
            self.turn_state[turn_id] = {"id": turn_id, "status": "inProgress", "items": [], "error": None, "startedAt": int(time.time())}
            self.thread_turns.setdefault(params["threadId"], []).append(turn_id)
            plan = self.plans.pop(0) if self.plans else "complete"
            asyncio.get_running_loop().call_later(0.05, lambda: asyncio.ensure_future(self._play(params, turn_id, plan)))
            return {"turn": {"id": turn_id, "status": "inProgress", "items": []}}, None
        if method == "turn/interrupt":
            self._interrupts.setdefault(params["turnId"], asyncio.Event()).set()
            return {}, None
        if method == "turn/steer":
            return {"turnId": params["expectedTurnId"]}, None
        if method == "command/exec":  # one-shot: the BatchMode key-login check
            stderr = "" if self.verify_exit == 0 else "Permission denied (publickey)."
            return {"exitCode": self.verify_exit, "stdout": "", "stderr": stderr}, None
        if method in ("command/exec/write", "command/exec/terminate"):
            process_id = params["processId"]
            process = self.processes.get(process_id)
            if process is None:
                return None, {"code": -32000, "message": "no such process"}
            if method == "command/exec/terminate":
                asyncio.ensure_future(self._process_exit(process_id, 1))
            else:
                process["input"] += base64.b64decode(params.get("deltaBase64") or "")
                if b"\n" in process["input"]:
                    typed = process["input"].split(b"\n")[0].decode()
                    asyncio.ensure_future(self._process_finish(process_id, typed))
            return {}, None
        return {}, None  # initialize, fs/createDirectory, thread/archive

    async def _ws_send(self, ws, payload: dict) -> None:
        try:
            await ws.send(json.dumps(payload))
        except ConnectionClosed:
            pass

    async def _process_output(self, process_id: str, data: bytes) -> None:
        process = self.processes[process_id]
        await self._ws_send(process["ws"], {"method": "command/exec/outputDelta", "params": {
            "processId": process_id, "stream": "stdout", "deltaBase64": base64.b64encode(data).decode(), "capReached": False,
        }})

    async def _process_finish(self, process_id: str, typed: str) -> None:
        if typed == self.ssh_password:
            await self._process_output(process_id, b"\r\nNumber of key(s) added: 1\r\n")
            await self._process_exit(process_id, 0)
        else:
            await self._process_output(process_id, b"\r\nPermission denied, please try again.\r\n")
            await self._process_exit(process_id, 1)

    async def _process_timeout(self, process_id: str, process: dict) -> None:
        if self.processes.get(process_id) is process:  # still this (unfinished) process
            await self._process_exit(process_id, 124)

    async def _process_exit(self, process_id: str, code: int) -> None:
        process = self.processes.pop(process_id, None)
        if process is not None:
            await self._ws_send(process["ws"], {"id": process["id"], "result": {"exitCode": code, "stdout": "", "stderr": ""}})

    async def _send(self, thread_id: str, payload: dict) -> None:
        ws = self._subscriber.get(thread_id)
        if ws is None:
            return
        try:
            await ws.send(json.dumps(payload))
        except ConnectionClosed:
            pass

    async def _released(self, turn_id: str) -> bool:
        """Wait for release() or turn/interrupt; True when released."""
        interrupt = asyncio.ensure_future(self._interrupts[turn_id].wait())
        release = asyncio.ensure_future(self._release.wait())
        done, pending = await asyncio.wait({interrupt, release}, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
        return interrupt not in done

    async def _play(self, params: dict, turn_id: str, plan: str) -> None:
        thread_id = params["threadId"]
        state = self.turn_state[turn_id]

        async def notify(method: str, extra: dict) -> None:
            item = extra.get("item")
            if item is not None:
                state["items"] = [i for i in state["items"] if i["id"] != item["id"]] + [item]
            await self._send(thread_id, {"method": method, "params": {"threadId": thread_id, "turnId": turn_id, **extra}})

        command = {
            "type": "commandExecution", "id": f"cmd_{turn_id}",
            "command": f"ssh {SYSTEM_MACHINE} 'make && ./saxpy 16777217'",
            "cwd": "/b", "status": "inProgress", "commandActions": [], "exitCode": None,
            "aggregatedOutput": None, "durationMs": None,
        }
        await notify("item/started", {"item": command, "startedAtMs": 1})
        if plan == "hold":
            await self._interrupts[turn_id].wait()
            status = "interrupted"
        elif plan == "gate" and not await self._released(turn_id):
            status = "interrupted"
        else:
            await notify("item/completed", {"item": {**command, "status": "completed", "exitCode": 0, "aggregatedOutput": "PASS\n", "durationMs": 12}, "completedAtMs": 2})
            await notify("item/completed", {"item": {"type": "agentMessage", "id": f"note_{turn_id}", "text": "开始复现", "phase": "commentary"}, "completedAtMs": 3})
            self.files[f"{self.thread_cwd.get(thread_id, '/b')}/artifacts/fix.patch"] = PATCH
            await notify("item/completed", {"item": {"type": "agentMessage", "id": f"final_{turn_id}", "text": json.dumps(RESULT, ensure_ascii=False), "phase": "final_answer"}, "completedAtMs": 4})
            status = "completed"
        state["status"] = status
        await self._send(thread_id, {"method": "turn/completed", "params": {"threadId": thread_id, "turn": {"id": turn_id, "status": status, "items": [], "error": None}}})


class FakeJira:
    def __init__(self) -> None:
        self.assignee = "alice"
        self.comments: list[tuple[str, str]] = []
        self.searches: list[str] = []
        self.validate_flags: list[bool] = []
        self.gone: set[str] = set()
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

    def search_issues(self, jql: str, *, max_results: int = 50, validate: bool = True) -> dict:
        self.searches.append(jql)
        self.validate_flags.append(validate)
        if "bad" in jql:
            raise jira.JiraQueryError("Error in JQL Query: bad")
        fields = ("key", "url", "summary", "issue_type", "status", "priority", "assignee", "components", "updated")
        if jql.startswith("key in ("):
            keys = [key for key in jql[len("key in ("):-1].split(", ") if key not in self.gone]
            issues = [{**{k: self.get_issue(key)[k] for k in fields}, "status": "Closed"} for key in keys]
            return {"total": len(issues), "issues": issues}
        issue = self.get_issue("MC3-7672")
        return {"total": 1, "issues": [{k: issue[k] for k in fields}]}

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
        f"SSH_KEY_PATH = {KEY_PATH}\n"
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

    fake.files[f"{KEY_PATH}.pub"] = PUBKEY
    conn = connect_jira_agent(settings.jira_agent_database_url)
    try:
        with transaction(conn):
            repo.create_machine(
                conn, agent_group="HPC", ssh_target=SYSTEM_MACHINE, description="1×A100 40GB | CUDA 12",
                created_by="carol",
            )
    finally:
        conn.close()

    current = {"user": USERS["alice"]}
    app = create_app()
    app.dependency_overrides[require_login] = lambda: current["user"]

    def as_user(name: str) -> None:
        current["user"] = USERS[name]

    ns = SimpleNamespace(client=None, fake=fake, jira=fake_jira, conf=conf, tmp=tmp_path, as_user=as_user)

    def start_site() -> None:
        ns.client = TestClient(app, raise_server_exceptions=False)
        ns.client.__enter__()

    def stop_site() -> None:
        """Stop the website like a service stop: the lifespan shutdown runs."""
        client, ns.client = ns.client, None
        if client is not None:
            client.__exit__(None, None, None)

    ns.start_site, ns.stop_site = start_site, stop_site
    start_site()
    try:
        yield ns
    finally:
        stop_site()
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
    several = env.client.get("/api/jira-agent/issues", params={"q": "mc3-1, MC3-2"})
    assert several.status_code == 400
    assert "一次只能查询一个 JIRA 编号" in several.json()["error"]

    missing = env.client.get("/api/jira-agent/issues", params={"q": "NOPE-1"}).json()
    assert (missing["mode"], missing["issues"], missing["missing"]) == ("key", [], ["NOPE-1"])
    assert env.client.get("/api/jira-agent/issues", params={"q": "mc3-1"}).json()["issues"][0]["open_conversation"] is None

    keys = env.client.get("/api/jira-agent/issues", params={"q": " mc3-2 "}).json()
    assert keys["mode"] == "key"
    assert keys["jql"] == ""
    assert [issue["key"] for issue in keys["issues"]] == ["MC3-2"]
    assert keys["missing"] == []
    open_conversation = keys["issues"][0]["open_conversation"]
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


def test_rm_lists_every_handled_issue_including_closed(env) -> None:
    first = _handover(env, "MC3-1")["conversation"]
    _wait(env, first["id"], lambda d: _latest(d)["status"] == "completed")
    second = _handover(env, "MC3-1", new_conversation=True)["conversation"]
    _handover(env, "MC3-2")

    assert env.client.get("/api/jira-agent/issues", params={"scope": "handled"}).status_code == 403

    env.as_user("carol")
    env.jira.gone.add("MC3-2")
    handled = env.client.get("/api/jira-agent/issues", params={"scope": "handled"}).json()
    assert (handled["mode"], handled["total"], handled["missing"]) == ("handled", 2, ["MC3-2"])
    assert env.jira.searches[-1] in {"key in (MC3-1, MC3-2)", "key in (MC3-2, MC3-1)"}
    assert env.jira.validate_flags[-1] is False
    [item] = handled["issues"]
    assert (item["key"], item["status"]) == ("MC3-1", "Closed")
    assert item["open_conversation"]["id"] == second["id"]
    assert item["agent"]["conversation_count"] == 2
    latest = item["agent"]["latest_conversation"]
    assert (latest["id"], latest["owner"]) == (second["id"], "alice")


def test_parse_issue_query() -> None:
    assert domain.parse_issue_query("  ") == ("mine", "")
    assert domain.parse_issue_query("mc3-7672") == ("key", "MC3-7672")
    assert domain.parse_issue_query("PDE_HPC-12，") == ("key", "PDE_HPC-12")
    assert domain.parse_issue_query("MC3-1 mc3-1") == ("key", "MC3-1")
    with pytest.raises(ValueError, match="一次只能查询一个 JIRA 编号"):
        domain.parse_issue_query("PDE_HPC-12，MC3-1")
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
# Website restart recovery
# ─────────────────────────────────────────────────────────────

def _db_turn(turn_id: str) -> dict:
    conn = connect_jira_agent(settings.jira_agent_database_url)
    try:
        return repo.get_turn(conn, turn_id)
    finally:
        conn.close()


def _until(predicate, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError("condition not reached")
        time.sleep(0.05)


def _status_texts(detail: dict) -> list[str]:
    return [event["payload"].get("text", "") for event in detail["events"] if event["kind"] == "status"]


def _phase(name: str):
    return lambda detail: detail["conversation"]["phase"] == name


def test_site_restart_reattaches_turn_still_running_on_codex(env) -> None:
    env.fake.plans = ["gate"]
    conversation = _handover(env)["conversation"]
    turn_id = _latest(_wait(env, conversation["id"], _phase("running")))["id"]

    env.stop_site()
    assert _db_turn(turn_id)["status"] == "running"  # left for the next start, not interrupted
    env.start_site()
    _wait(env, conversation["id"], _phase("running"))
    env.fake.release()

    detail = _wait(env, conversation["id"], _done)
    turn = _latest(detail)
    assert (turn["status"], turn["comment_status"]) == ("completed", "posted")
    assert env.fake.turns == 1
    assert len(env.jira.comments) == 1
    assert [params["threadId"] for params in env.fake.params("thread/resume")] == ["thr_1"]
    assert "已重新接管 Codex 上仍在运行的本轮" in _status_texts(detail)
    assert [e["payload"]["status"] for e in detail["events"] if e["kind"] == "command"] == ["completed"]


def test_site_restart_collects_turn_finished_while_site_was_down(env) -> None:
    env.fake.plans = ["gate"]
    conversation = _handover(env)["conversation"]
    _wait(env, conversation["id"], _phase("running"))

    env.stop_site()
    env.fake.release()
    _until(lambda: env.fake.turn_state["turn_1"]["status"] == "completed")
    env.start_site()

    detail = _wait(env, conversation["id"], _done)
    turn = _latest(detail)
    assert (turn["status"], turn["conclusion"], turn["comment_status"]) == (
        "completed", "fixed_pending_review", "posted",
    )
    assert "本轮已在网站重启期间结束，正在收尾" in _status_texts(detail)
    # the command finished while the site was down: backfilled from thread/turns/list
    assert [e["payload"]["status"] for e in detail["events"] if e["kind"] == "command"] == ["completed"]
    assert [f["name"] for f in detail["files"] if f["source"] == "artifact"] == ["fix.patch"]
    assert env.fake.turns == 1
    assert len(env.jira.comments) == 1


def test_site_stopped_while_turn_start_in_flight_adopts_the_started_turn(env) -> None:
    env.fake.plans = ["gate"]
    env.fake.turn_start_delay = 1.0
    conversation = _handover(env)["conversation"]
    turn_id = _latest(_wait(env, conversation["id"], _phase("starting")))["id"]

    env.stop_site()
    _until(lambda: env.fake.turns == 1)  # turn/start still reached the app-server
    assert _db_turn(turn_id)["codex_turn_id"] == ""
    env.fake.turn_start_delay = 0
    env.start_site()

    _wait(env, conversation["id"], _phase("running"))
    assert _db_turn(turn_id)["codex_turn_id"] == "turn_1"
    env.fake.release()
    assert _latest(_wait(env, conversation["id"], _done))["status"] == "completed"
    assert env.fake.turns == 1


def test_unreachable_codex_at_restart_then_followup_stops_leftover_turn(env) -> None:
    env.fake.plans = ["gate", "complete"]
    conversation = _handover(env)["conversation"]
    _wait(env, conversation["id"], _phase("running"))

    env.stop_site()
    _write_conf(env.conf, env.fake.url, token="wrong")
    env.start_site()
    turn = _latest(_wait(env, conversation["id"], _done))
    assert turn["status"] == "interrupted"
    assert "未能重新接管" in turn["error"]

    # turn_1 is still running on the app-server; a new turn/start would join it
    _write_conf(env.conf, env.fake.url)
    sent = env.client.post(f"/api/jira-agent/conversations/{conversation['id']}/messages", json={"text": "继续"})
    assert sent.json()["mode"] == "queued"
    detail = _wait(env, conversation["id"], lambda d: _latest(d)["seq"] == 2 and _done(d))
    assert _latest(detail)["status"] == "completed"
    assert env.fake.params("turn/interrupt") == [{"threadId": "thr_1", "turnId": "turn_1"}]
    assert env.fake.turns == 2
    assert "Codex 上本对话还有未结束的上一轮，先中断它" in _status_texts(detail)


def test_startup_requeues_unstarted_turn_and_posts_pending_comment(env) -> None:
    env.stop_site()
    conn = connect_jira_agent(settings.jira_agent_database_url)
    try:
        with transaction(conn):
            for cid, key in (("jac_new", "MC3-1"), ("jac_done", "MC3-2")):
                repo.create_conversation(
                    conn, conversation_id=cid, issue_key=key, issue_summary="", agent_group="HPC",
                    owner="alice", created_by="alice", workspace=f"/b/workspaces/{cid}",
                )
            unstarted = repo.create_turn(conn, conversation_id="jac_new", trigger="handover", created_by="alice", input_text="")
            assert repo.claim_turn(conn, unstarted["id"])  # stopped before a thread existed
            finished = repo.create_turn(conn, conversation_id="jac_done", trigger="handover", created_by="alice", input_text="")
            repo.update_turn(conn, finished["id"], status="completed", comment_status="pending", comment_body="结论")
    finally:
        conn.close()
    env.start_site()

    assert _latest(_wait(env, "jac_new", _done))["status"] == "completed"
    assert env.fake.turns == 1
    _until(lambda: _db_turn(finished["id"])["comment_status"] == "posted")
    assert env.jira.comments.count(("MC3-2", "结论")) == 1


def test_actions_are_refused_while_restarted_site_reattaches(env) -> None:
    env.fake.plans = ["gate"]
    cid = _handover(env)["conversation"]["id"]
    _wait(env, cid, _phase("running"))

    env.stop_site()
    env.fake.resume_delay = 1.5  # keep the re-attach in progress
    env.start_site()
    _wait(env, cid, _phase("recovering"))
    refused = [
        env.client.post(f"/api/jira-agent/conversations/{cid}/cancel"),
        env.client.post(f"/api/jira-agent/conversations/{cid}/messages", json={"text": "补充"}),
        env.client.post("/api/jira-agent/conversations", json={"issue_key": "MC3-7672", "new_conversation": True}),
    ]
    for response in refused:
        assert response.status_code == 409, response.text
        assert "正在重新接管" in response.text
    assert _detail(env, cid)["conversation"]["status"] == "open"

    _wait(env, cid, _phase("running"))  # once re-attached the actions work again
    assert env.client.post(f"/api/jira-agent/conversations/{cid}/cancel").status_code == 200
    turn = _latest(_wait(env, cid, _done))
    assert turn["status"] == "cancelled"
    assert env.fake.params("turn/interrupt") == [{"threadId": "thr_1", "turnId": "turn_1"}]
    assert env.jira.comments == []


def test_restart_does_not_run_turns_of_closed_conversations(env) -> None:
    env.stop_site()
    conn = connect_jira_agent(settings.jira_agent_database_url)
    turns = {}
    try:
        with transaction(conn):
            for cid, key in (("jac_a", "MC3-1"), ("jac_b", "MC3-2"), ("jac_c", "MC3-3")):
                repo.create_conversation(
                    conn, conversation_id=cid, issue_key=key, issue_summary="", agent_group="HPC",
                    owner="alice", created_by="alice", workspace=f"/b/workspaces/{cid}",
                )
                turns[cid] = repo.create_turn(conn, conversation_id=cid, trigger="handover", created_by="alice", input_text="")["id"]
            # a: closed, then the site crashed while turn/start had not reached the app-server
            repo.set_thread_id(conn, "jac_a", "thr_gone")
            assert repo.claim_turn(conn, turns["jac_a"])
            # b: closed and crashed before a thread existed; c: closed with a turn still queued
            assert repo.claim_turn(conn, turns["jac_b"])
            for cid in turns:
                repo.close_conversation(conn, cid, "superseded")
    finally:
        conn.close()
    env.start_site()

    for turn_id in turns.values():
        _until(lambda turn_id=turn_id: _db_turn(turn_id)["status"] == "cancelled")
    assert env.fake.params("turn/start") == []
    assert env.jira.comments == []


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


def test_ssh_target_and_public_key_fingerprint() -> None:
    assert domain.parse_ssh_target(" hpc@10.2.118.75 ") == "hpc@10.2.118.75"
    for bad in ("-oProxyCommand=x@h", "hpc@-oProxyCommand=x", "hpc@10.0.0.1:22", "a b@h", "hpc", "", "hpc@h;rm"):
        with pytest.raises(ValueError):
            domain.parse_ssh_target(bad)
    assert domain.ssh_public_key_info(PUBKEY.decode()) == {
        "type": "ssh-ed25519", "fingerprint": FINGERPRINT, "comment": "hpc-jira-agent@B",
    }
    with pytest.raises(ValueError):
        domain.ssh_public_key_info("ssh-ed25519 not-base64!")


# ─────────────────────────────────────────────────────────────
# Execution machines
# ─────────────────────────────────────────────────────────────

def test_auto_machine_prompt_lists_system_machines_and_comment_names_used_machine(env) -> None:
    conversation = _handover(env)["conversation"]
    assert conversation["machine"] == ""
    _wait(env, conversation["id"], _done)
    turn_start = env.fake.params("turn/start")[0]
    prompt = turn_start["input"][0]["text"]
    assert "按工单需要从下列系统机器中选择一台" in prompt
    assert f"| `{SYSTEM_MACHINE}` | 1×A100 40GB \\| CUDA 12 |" in prompt
    assert "machine" in turn_start["outputSchema"]["required"]
    [(_, body)] = env.jira.comments
    assert f"*执行机器*：{SYSTEM_MACHINE}" in body
    assert _detail(env, conversation["id"])["conversation"]["machine_used"] == SYSTEM_MACHINE

    sent = env.client.post(f"/api/jira-agent/conversations/{conversation['id']}/messages", json={"text": "继续"})
    assert sent.status_code == 200, sent.text
    _wait(env, conversation["id"], lambda d: _latest(d)["seq"] == 2 and _done(d))
    assert SYSTEM_MACHINE in env.fake.params("turn/start")[1]["input"][0]["text"]


def test_machine_shows_up_while_the_turn_is_still_running(env) -> None:
    env.fake.plans = ["hold"]
    conversation = _handover(env)["conversation"]
    # the first command logs in to a system machine: no need to wait for the result
    detail = _wait(env, conversation["id"], lambda d: d["conversation"]["machine_used"] == SYSTEM_MACHINE)
    assert _latest(detail)["status"] == "running"
    assert env.client.post(f"/api/jira-agent/conversations/{conversation['id']}/cancel").status_code == 200
    assert _latest(_wait(env, conversation["id"], _done))["status"] == "cancelled"


def test_rm_maintains_system_machines(env) -> None:
    machines = env.client.get("/api/jira-agent/machines").json()
    assert machines["can_manage"] is False
    assert [m["ssh_target"] for m in machines["machines"]] == [SYSTEM_MACHINE]
    assert machines["groups"] == [{"name": "HPC", "display_name": "HPC 数字员工"}]
    body = {"agent_group": "HPC", "ssh_target": "hpc2@10.2.118.76", "description": "2×A100"}
    assert env.client.post("/api/jira-agent/machines", json=body).status_code == 403

    env.as_user("carol")
    created = env.client.post("/api/jira-agent/machines", json=body)
    assert created.status_code == 200, created.text
    machine_id = created.json()["machine"]["id"]
    assert env.client.post("/api/jira-agent/machines", json=body).status_code == 409
    for bad in ("-oProxyCommand=x@h", "hpc@10.0.0.1:22", "hpc@h ost", "nohost"):
        assert env.client.post("/api/jira-agent/machines", json={**body, "ssh_target": bad}).status_code == 400, bad
    assert env.client.post("/api/jira-agent/machines", json={**body, "agent_group": "NOPE"}).status_code == 400
    updated = env.client.put(
        f"/api/jira-agent/machines/{machine_id}", json={"ssh_target": "hpc3@10.2.118.77", "description": "改"},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["machine"]["ssh_target"] == "hpc3@10.2.118.77"

    for machine in env.client.get("/api/jira-agent/machines").json()["machines"]:
        assert env.client.delete(f"/api/jira-agent/machines/{machine['id']}").status_code == 200
    assert env.client.delete(f"/api/jira-agent/machines/{machine_id}").status_code == 404

    env.as_user("alice")  # the automatic choice needs a system machine
    empty = env.client.post("/api/jira-agent/conversations", json={"issue_key": "MC3-1"})
    assert empty.status_code == 409
    assert "系统机器列表为空" in empty.json()["error"]


def _key_session(env, session_id: str, predicate, timeout: float = 10.0) -> dict:
    deadline = time.monotonic() + timeout
    while True:
        response = env.client.get(f"/api/jira-agent/ssh-key-sessions/{session_id}")
        assert response.status_code == 200, response.text
        session = response.json()
        if predicate(session):
            return session
        if time.monotonic() > deadline:
            raise AssertionError(f"key session condition not reached: {session}")
        time.sleep(0.05)


def _finished(session: dict) -> bool:
    return session["status"] not in ("running", "verifying")


def _start_key_session(env, target: str) -> str:
    response = env.client.post("/api/jira-agent/ssh-key-sessions", json={"agent_group": "HPC", "target": target})
    assert response.status_code == 200, response.text
    return response.json()["id"]


def test_user_given_machine_needs_key_uploaded_from_b_and_verified(env) -> None:
    target = "tester@10.0.0.9"
    refused = env.client.post("/api/jira-agent/conversations", json={"issue_key": "MC3-1", "machine": target})
    assert refused.status_code == 409
    assert "上传到 tester@10.0.0.9" in refused.json()["error"]
    bad = env.client.post("/api/jira-agent/conversations", json={"issue_key": "MC3-1", "machine": "-oProxyCommand=sh@x"})
    assert bad.status_code == 400

    info = env.client.get("/api/jira-agent/ssh-key-info", params={"group": "HPC"}).json()
    assert (info["fingerprint"], info["comment"], info["verified_targets"]) == (FINGERPRINT, "hpc-jira-agent@B", [])

    session_id = _start_key_session(env, target)
    _key_session(env, session_id, lambda s: "password:" in s["output"])
    [pty] = [params for params in env.fake.params("command/exec") if params.get("tty")]
    assert pty["command"] == [
        "ssh-copy-id", "-i", f"{KEY_PATH}.pub", "-o", "StrictHostKeyChecking=accept-new",
        "-o", "ConnectTimeout=10", target,
    ]
    # the app-server enforces the limit; its default (10 s) is too short to type a password
    assert pty["timeoutMs"] == 60_000
    assert "disableTimeout" not in pty

    env.as_user("bob")  # only the user who started it
    assert env.client.get(f"/api/jira-agent/ssh-key-sessions/{session_id}").status_code == 404
    assert env.client.post(
        f"/api/jira-agent/ssh-key-sessions/{session_id}/input", json={"data": "x\n"},
    ).status_code == 404
    env.as_user("alice")
    again = env.client.post("/api/jira-agent/ssh-key-sessions", json={"agent_group": "HPC", "target": target})
    assert again.status_code == 409  # one live session per user

    sent = env.client.post(f"/api/jira-agent/ssh-key-sessions/{session_id}/input", json={"data": "s3cret\n"})
    assert sent.status_code == 200, sent.text
    done = _key_session(env, session_id, _finished)
    assert (done["status"], done["exit_code"]) == ("succeeded", 0), done
    assert "Number of key(s) added" in done["output"]
    [check] = [params for params in env.fake.params("command/exec") if not params.get("tty")]
    assert check["command"][:3] == ["ssh", "-o", "BatchMode=yes"]
    assert check["command"][-2:] == [target, "true"]
    assert env.client.get("/api/jira-agent/ssh-key-info", params={"group": "HPC"}).json()["verified_targets"] == [target]

    conversation = _handover(env, "MC3-1", machine=target)["conversation"]
    assert conversation["machine"] == target
    _wait(env, conversation["id"], _done)
    prompt = env.fake.params("turn/start")[0]["input"][0]["text"]
    assert f"用户指定的机器 `ssh {target}`" in prompt
    assert SYSTEM_MACHINE not in prompt

    # what was typed is only forwarded: not in any file the website wrote
    stored = b"".join(path.read_bytes() for path in env.tmp.rglob("*") if path.is_file())
    assert b"s3cret" not in stored

    env.as_user("carol")  # the upload record belongs to the user who did it
    other = env.client.post("/api/jira-agent/conversations", json={"issue_key": "MC3-2", "machine": target})
    assert other.status_code == 409


def test_key_upload_failures_and_timeouts_record_nothing(env, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services import jira_agent_ssh_key

    target = "tester@10.0.0.9"
    env.fake.verify_exit = 255  # key copied, but server B still cannot log in with it
    session_id = _start_key_session(env, target)
    _key_session(env, session_id, lambda s: "password:" in s["output"])
    env.client.post(f"/api/jira-agent/ssh-key-sessions/{session_id}/input", json={"data": "s3cret\n"})
    failed = _key_session(env, session_id, _finished)
    assert failed["status"] == "failed"
    assert "免密登录" in failed["message"] and "Permission denied" in failed["message"]

    env.fake.verify_exit = 0
    session_id = _start_key_session(env, target)
    _key_session(env, session_id, lambda s: "password:" in s["output"])
    env.client.post(f"/api/jira-agent/ssh-key-sessions/{session_id}/input", json={"data": "wrong\n"})
    wrong = _key_session(env, session_id, _finished)
    assert (wrong["status"], wrong["exit_code"]) == ("failed", 1)
    assert "退出码 1" in wrong["message"]

    monkeypatch.setattr(jira_agent_ssh_key, "PROCESS_TIMEOUT_MS", 300)  # nobody types in time
    session_id = _start_key_session(env, target)
    timed_out = _key_session(env, session_id, _finished)
    assert (timed_out["status"], timed_out["exit_code"]) == ("failed", 124)
    assert "秒未完成" in timed_out["message"]
    assert not env.fake.params("command/exec/terminate")  # killed by the app-server, not the website

    assert env.client.get("/api/jira-agent/ssh-key-info", params={"group": "HPC"}).json()["verified_targets"] == []
    assert len([params for params in env.fake.params("command/exec") if not params.get("tty")]) == 1
