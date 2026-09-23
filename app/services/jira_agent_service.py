"""JIRA agent use cases: hand-over, follow-up messages, cancel and views.

Rules:
  - only the current JIRA assignee or RM may hand an issue to the agent or
    talk to it; checked against live JIRA on every such write
  - a conversation (one Codex thread + one workspace) belongs to the assignee
    at hand-over time; an assignee change, or an explicit "new conversation",
    closes the old one read-only — even if the issue later returns to them
  - the agent only comments on JIRA; people decide what happens next
"""
from __future__ import annotations

import asyncio
import base64
import binascii
import re
import sqlite3
import urllib.error
from pathlib import Path

from app.api.errors import ApiError, AuthzError
from app.config import settings
from app.db.connection import transaction
from app.domain import jira_agent as domain
from app.integrations import jira
from app.integrations.codex_app_server import CodexAppServerError
from app.repositories import jira_agent_repo as repo
from app.repositories.base import loads_json, new_id
from app.services import jira_agent_runner as runner_module
from app.services import jira_agent_ssh_key
from app.services.jira_agent_runner import open_db, runner

MAX_UPLOAD_FILES = 10
MAX_UPLOAD_BYTES = 20 * 1024 * 1024
MAX_SEARCH_RESULTS = 50
MAX_HANDLED_RESULTS = 200
HANDLED_JQL_CHUNK = 100
_ISSUE_KEY_RE = re.compile(r"^[A-Z][A-Z0-9_]*-\d+$")

_CLOSE_TEXT = {
    "superseded": "JIRA assignee 已变更，本对话已结束（只读）",
    "new_conversation": "已新建对话，本对话已结束（只读）",
}


# ─────────────────────────────────────────────────────────────
# JIRA and permissions
# ─────────────────────────────────────────────────────────────

def normalize_issue_key(issue_key: str) -> str:
    key = (issue_key or "").strip().upper()
    if not _ISSUE_KEY_RE.match(key):
        raise ValueError("请输入有效的 JIRA 编号，例如 MC3-7672")
    return key


async def fetch_issue(issue_key: str) -> dict:
    key = normalize_issue_key(issue_key)
    try:
        return await asyncio.to_thread(jira.get_issue, key)
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403, 404):
            raise ApiError(404, f"JIRA 工单 {key} 不存在或无权访问") from exc
        raise ApiError(502, f"读取 JIRA 失败：HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise ApiError(502, f"无法连接 JIRA：{exc.reason}") from exc


def _same_user(a: str, b: str) -> bool:
    return bool(a) and bool(b) and a.casefold() == b.casefold()


def _is_rm(user: dict) -> bool:
    return user.get("role") == "RM"


def assignee_name(issue: dict) -> str:
    return (issue.get("assignee") or {}).get("name", "")


def can_act(user: dict, issue: dict) -> bool:
    return _is_rm(user) or _same_user(user.get("username", ""), assignee_name(issue))


def check_actor(user: dict, issue: dict) -> None:
    if not can_act(user, issue):
        raise AuthzError("只有当前 JIRA assignee 或 RM 可以把工单交给 agent 或与 agent 对话")


def can_view(user: dict, conversation: dict) -> bool:
    username = user.get("username", "")
    return (
        _is_rm(user)
        or _same_user(username, conversation["owner"])
        or _same_user(username, conversation["created_by"])
    )


def _get_visible(conn: sqlite3.Connection, user: dict, conversation_id: str) -> dict:
    conversation = repo.get_conversation(conn, conversation_id)
    if conversation is None or not can_view(user, conversation):
        raise ApiError(404, "对话不存在或无权访问")
    return conversation


# ─────────────────────────────────────────────────────────────
# Uploads
# ─────────────────────────────────────────────────────────────

def _decode_uploads(files: list[dict] | None) -> list[tuple[str, bytes]]:
    files = files or []
    if len(files) > MAX_UPLOAD_FILES:
        raise ValueError(f"一次最多上传 {MAX_UPLOAD_FILES} 个文件")
    decoded = []
    for item in files:
        name = domain.safe_filename(item.get("filename") or "")
        try:
            data = base64.b64decode(item.get("content_base64") or "", validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValueError(f"文件 {name} 内容不是有效的 base64") from exc
        if len(data) > MAX_UPLOAD_BYTES:
            raise ValueError(f"文件 {name} 超过 {MAX_UPLOAD_BYTES // (1024 * 1024)}MB")
        decoded.append((name, data))
    return decoded


def _store_upload(
    conn: sqlite3.Connection, conversation_id: str, turn_id: str, name: str, data: bytes,
    *, remote_path: str = "",
) -> dict:
    folder = Path(settings.jira_agent_data_dir) / conversation_id / "uploads"
    folder.mkdir(parents=True, exist_ok=True)
    local = folder / name
    index = 1
    while local.exists():
        local = folder / f"{index}-{name}"
        index += 1
    local.write_bytes(data)
    return repo.add_file(
        conn, conversation_id=conversation_id, turn_id=turn_id, direction="input",
        source="upload", name=name, size=len(data), local_path=str(local),
        remote_path=remote_path,
    )


# ─────────────────────────────────────────────────────────────
# Views
# ─────────────────────────────────────────────────────────────

def _turn_view(conn: sqlite3.Connection, turn: dict | None) -> dict | None:
    if turn is None:
        return None
    view = dict(turn)
    view["result"] = loads_json(view.pop("result_json"), None)
    view["post_comment"] = bool(view["post_comment"])
    view["queue_position"] = (
        repo.queue_position(conn, turn["id"]) if turn["status"] == "queued" else None
    )
    return view


def _state(conversation: dict, latest: dict | None) -> str:
    if conversation["status"] != "open":
        return "closed"
    if latest is None:
        return "idle"
    if latest["status"] == "completed":
        return "waiting_review"
    return latest["status"]


def conversation_view(conn: sqlite3.Connection, user: dict, conversation: dict) -> dict:
    latest = repo.latest_turn(conn, conversation["id"])
    active = runner.active_for_conversation(conversation["id"])
    return {
        **conversation,
        "state": _state(conversation, latest),
        "phase": active.phase if active else "",
        "latest_turn": _turn_view(conn, latest),
        "read_only": conversation["status"] != "open",
        "can_write": conversation["status"] == "open"
        and (_is_rm(user) or _same_user(user.get("username", ""), conversation["owner"])),
        "browse_url": jira.browse_url() + conversation["issue_key"] if jira.browse_url() else "",
    }


def _file_view(record: dict) -> dict:
    view = {k: v for k, v in record.items() if k != "local_path"}
    view["downloadable"] = bool(record["local_path"])
    return view


def list_conversations(user: dict) -> dict:
    conn = open_db()
    try:
        rows = repo.list_conversations(conn, username=None if _is_rm(user) else user["username"])
        return {"conversations": [conversation_view(conn, user, row) for row in rows]}
    finally:
        conn.close()


def get_conversation_detail(user: dict, conversation_id: str) -> dict:
    conn = open_db()
    try:
        conversation = _get_visible(conn, user, conversation_id)
        history = [
            {"id": row["id"], "owner": row["owner"], "status": row["status"],
             "close_reason": row["close_reason"], "created_at": row["created_at"]}
            for row in repo.list_conversations(conn, issue_key=conversation["issue_key"])
            if can_view(user, row)
        ]
        return {
            "conversation": conversation_view(conn, user, conversation),
            "turns": [_turn_view(conn, turn) for turn in repo.list_turns(conn, conversation_id)],
            "files": [_file_view(record) for record in repo.list_files(conn, conversation_id)],
            "events": repo.list_events(conn, conversation_id),
            "rev": repo.max_event_rev(conn, conversation_id),
            "issue_conversations": history,
        }
    finally:
        conn.close()


def list_events(user: dict, conversation_id: str, after_rev: int) -> dict:
    conn = open_db()
    try:
        conversation = _get_visible(conn, user, conversation_id)
        return {
            "conversation": conversation_view(conn, user, conversation),
            "events": repo.list_events(conn, conversation_id, after_rev=after_rev),
            "rev": repo.max_event_rev(conn, conversation_id),
        }
    finally:
        conn.close()


def file_for_download(user: dict, conversation_id: str, file_id: str) -> tuple[Path, str]:
    conn = open_db()
    try:
        _get_visible(conn, user, conversation_id)
        record = repo.get_file(conn, file_id)
    finally:
        conn.close()
    if record is None or record["conversation_id"] != conversation_id or not record["local_path"]:
        raise ApiError(404, "文件不存在或不可下载")
    path = Path(record["local_path"])
    if not path.is_file():
        raise ApiError(404, "文件已不存在")
    return path, record["name"]


# ─────────────────────────────────────────────────────────────
# Use cases
# ─────────────────────────────────────────────────────────────

async def preview_issue(user: dict, issue_key: str) -> dict:
    issue = await fetch_issue(issue_key)
    conn = open_db()
    try:
        open_conversation = repo.get_open_conversation(conn, issue["key"])
        history = [
            conversation_view(conn, user, row)
            for row in repo.list_conversations(conn, issue_key=issue["key"])
            if can_view(user, row)
        ]
    finally:
        conn.close()
    assignee = assignee_name(issue)
    try:
        agent_group = domain.group_for_issue(runner_module.load_groups(), issue["components"]).name
    except RuntimeError:  # no digital employee configured
        agent_group = ""
    return {
        "agent_group": agent_group,
        "issue": {
            "key": issue["key"],
            "url": issue["url"],
            "summary": issue["summary"],
            "issue_type": issue["issue_type"],
            "status": issue["status"],
            "priority": issue["priority"],
            "assignee": issue["assignee"],
            "components": issue["components"],
            "attachment_count": len(issue["attachments"]),
            "comment_count": len(issue["comments"]),
        },
        "can_handover": can_act(user, issue) and bool(assignee),
        "open_conversation": {
            "id": open_conversation["id"],
            "owner": open_conversation["owner"],
            "owner_is_assignee": _same_user(open_conversation["owner"], assignee),
            "recovering": _is_recovering(open_conversation["id"]),
        } if open_conversation else None,
        "conversations": history,
    }


_SEARCH_ISSUE_FIELDS = (
    "key", "url", "summary", "issue_type", "status", "priority", "assignee", "components", "updated",
)


async def _jql_search(jql: str, *, max_results: int, validate: bool = True) -> dict:
    try:
        return await asyncio.to_thread(
            jira.search_issues, jql, max_results=max_results, validate=validate,
        )
    except urllib.error.HTTPError as exc:
        raise ApiError(502, f"JIRA 查询失败：HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise ApiError(502, f"无法连接 JIRA：{exc.reason}") from exc


def _search_item(conn: sqlite3.Connection, user: dict, issue: dict, conversation: dict | None) -> dict:
    assignee = assignee_name(issue)
    return {
        **{field: issue.get(field) for field in _SEARCH_ISSUE_FIELDS},
        "can_handover": can_act(user, issue) and bool(assignee),
        "open_conversation": {
            "id": conversation["id"] if can_view(user, conversation) else "",
            "owner": conversation["owner"],
            "owner_is_assignee": _same_user(conversation["owner"], assignee),
            "state": _state(conversation, repo.latest_turn(conn, conversation["id"])),
        } if conversation else None,
    }


async def search_issues(user: dict, query: str) -> dict:
    """Search box: empty → default list, one issue key → that issue, else JQL.

    The default list is the user's not-closed issues; RM sees the issues of
    the configured JIRA member groups.  Searches run with the JIRA account in
    release_system.conf [jira]; hand-over is still limited to the assignee or RM.
    """
    mode, value = domain.parse_issue_query(query)
    missing: list[str] = []
    jql = ""
    if mode == "key":
        issues = []
        try:
            issues.append(await fetch_issue(value))
        except ApiError as exc:
            if exc.status_code != 404:
                raise
            missing.append(value)
        total = len(issues)
    else:
        jql = value if mode == "jql" else domain.default_issue_jql(
            username=user["username"], is_rm=_is_rm(user), groups=runner_module.load_groups(),
        )
        found = await _jql_search(jql, max_results=MAX_SEARCH_RESULTS)
        issues, total = found["issues"], found["total"]

    conn = open_db()
    try:
        open_by_key = repo.open_conversations_by_issue(conn, [issue["key"] for issue in issues])
        results = [_search_item(conn, user, issue, open_by_key.get(issue["key"])) for issue in issues]
    finally:
        conn.close()
    return {"mode": mode, "jql": jql, "total": total, "missing": missing, "issues": results}


async def list_handled_issues(user: dict) -> dict:
    """RM only: every issue ever handed to the agent, JIRA-closed ones included.

    Current status/assignee come from JIRA; keys JIRA no longer returns
    (deleted, moved, no permission) are listed in `missing`.
    """
    if not _is_rm(user):
        raise AuthzError("只有 RM 可以查看 agent 处理过的全部工单")
    conn = open_db()
    try:
        total, rows = repo.handled_issues(conn, limit=MAX_HANDLED_RESULTS)
    finally:
        conn.close()

    keys = [row["issue_key"] for row in rows]
    jira_by_key: dict[str, dict] = {}
    for start in range(0, len(keys), HANDLED_JQL_CHUNK):
        chunk = keys[start:start + HANDLED_JQL_CHUNK]
        # Keys were validated at hand-over, so they are safe inside JQL.
        found = await _jql_search(f"key in ({', '.join(chunk)})", max_results=len(chunk), validate=False)
        jira_by_key.update({issue["key"]: issue for issue in found["issues"]})

    conn = open_db()
    try:
        open_by_key = repo.open_conversations_by_issue(conn, keys)
        results = []
        missing = []
        for row in rows:
            key = row["issue_key"]
            issue = jira_by_key.get(key)
            if issue is None:
                missing.append(key)
                continue
            latest = repo.list_conversations(conn, issue_key=key, limit=1)[0]
            latest_turn = repo.latest_turn(conn, latest["id"])
            results.append({
                **_search_item(conn, user, issue, open_by_key.get(key)),
                "agent": {
                    "conversation_count": row["conversation_count"],
                    "last_activity": row["last_activity"],
                    "latest_conversation": {
                        "id": latest["id"],
                        "owner": latest["owner"],
                        "state": _state(latest, latest_turn),
                        "conclusion": latest_turn["conclusion"] if latest_turn else "",
                    },
                },
            })
    finally:
        conn.close()
    return {"mode": "handled", "jql": "", "total": total, "missing": missing, "issues": results}


_RECOVERING_TEXT = "网站刚重启，正在重新接管本轮，请几秒后再试"


def _is_recovering(conversation_id: str) -> bool:
    active = runner.active_for_conversation(conversation_id)
    return active is not None and active.phase == "recovering"


def _check_not_recovering(conversation_id: str) -> None:
    """While a restarted site re-attaches to a turn, its outcome is not known yet."""
    if _is_recovering(conversation_id):
        raise ApiError(409, _RECOVERING_TEXT)


async def close_conversation(conn: sqlite3.Connection, conversation: dict, reason: str) -> None:
    """Close read-only, cancel/interrupt its active turn, archive its thread."""
    _check_not_recovering(conversation["id"])
    running_turn_id = ""
    with transaction(conn):
        if not repo.close_conversation(conn, conversation["id"], reason):
            return
        turn = repo.active_turn(conn, conversation["id"])
        if turn is not None and not repo.cancel_queued_turn(conn, turn["id"], _CLOSE_TEXT[reason]):
            running_turn_id = turn["id"]
        repo.add_event(
            conn, conversation_id=conversation["id"],
            kind="status", payload={"status": "closed", "text": _CLOSE_TEXT[reason]},
        )
    if running_turn_id and not await runner.stop_turn(running_turn_id, "closed"):
        with transaction(conn):
            repo.update_turn(conn, running_turn_id, status="interrupted", error=_CLOSE_TEXT[reason])
    runner.schedule_archive(conversation["id"])


async def handover(user: dict, body: dict) -> dict:
    issue = await fetch_issue(body.get("issue_key", ""))
    check_actor(user, issue)
    assignee = assignee_name(issue)
    if not assignee:
        raise ApiError(409, "工单没有 assignee，请先在 JIRA 指派负责人")
    note = (body.get("note") or "").strip()
    uploads = _decode_uploads(body.get("files"))
    post_comment = bool(body.get("post_comment", True))
    group = domain.group_for_issue(runner_module.load_groups(), issue["components"])

    conn = open_db()
    try:
        machine = _check_machine(
            conn, user, group, body.get("machine") or "", body.get("ssh_key_session_id") or "",
        )
        existing = repo.get_open_conversation(conn, issue["key"])
        if existing is not None:
            same_owner = _same_user(existing["owner"], assignee)
            if same_owner and not body.get("new_conversation"):
                return {"created": False, "conversation": conversation_view(conn, user, existing)}
            await close_conversation(conn, existing, "new_conversation" if same_owner else "superseded")

        conversation_id = new_id("jac")
        try:
            with transaction(conn):
                conversation = repo.create_conversation(
                    conn,
                    conversation_id=conversation_id,
                    issue_key=issue["key"],
                    issue_summary=issue["summary"],
                    agent_group=group.name,
                    owner=assignee,
                    created_by=user["username"],
                    workspace=domain.workspace_path(group, issue["key"], conversation_id),
                    machine=machine,
                )
                turn = repo.create_turn(
                    conn, conversation_id=conversation_id, trigger="handover",
                    created_by=user["username"], input_text=note, post_comment=post_comment,
                )
                for name, data in uploads:
                    _store_upload(conn, conversation_id, turn["id"], name, data)
                repo.add_event(
                    conn, conversation_id=conversation_id, turn_id=turn["id"], kind="user_message",
                    payload={
                        "author": user["username"],
                        "text": note or f"把 {issue['key']} 交给 {group.display_name} 处理",
                        "files": [name for name, _ in uploads],
                        "mode": "handover",
                        "post_comment": post_comment,
                    },
                )
        except sqlite3.IntegrityError as exc:
            raise ApiError(409, "该工单刚刚已被交给 agent，请刷新后查看") from exc
        runner.wake()
        return {"created": True, "conversation": conversation_view(conn, user, conversation)}
    finally:
        conn.close()


async def send_message(user: dict, conversation_id: str, body: dict) -> dict:
    text = (body.get("text") or "").strip()
    uploads = _decode_uploads(body.get("files"))
    post_comment = bool(body.get("post_comment", True))
    if not text and not uploads:
        raise ValueError("请输入补充信息或上传文件")

    conn = open_db()
    try:
        conversation = _get_visible(conn, user, conversation_id)
        if conversation["status"] != "open":
            raise ApiError(409, "该对话已结束（只读），请重新把工单交给 agent 新建对话")
        _check_not_recovering(conversation_id)
        issue = await fetch_issue(conversation["issue_key"])
        assignee = assignee_name(issue)
        if not _same_user(assignee, conversation["owner"]):
            await close_conversation(conn, conversation, "superseded")
            raise ApiError(
                409,
                f"JIRA assignee 已变更为 {assignee or '无'}，本对话已结束；请由当前 assignee 交给 agent 新建对话",
            )
        check_actor(user, issue)

        names = [name for name, _ in uploads]
        active = runner.active_for_conversation(conversation_id)
        if active is not None and active.phase == "running":
            return await _steer(conn, user, conversation, active, text, uploads, post_comment)
        if active is not None and active.phase != "preparing":
            raise ApiError(409, "agent 正在启动或收尾本轮，请几秒后再发送")

        with transaction(conn):
            turn = repo.active_turn(conn, conversation_id)
            if turn is not None:
                repo.append_turn_input(conn, turn["id"], text)
                repo.update_turn(conn, turn["id"], post_comment=int(post_comment))
                mode = "merged"
            else:
                turn = repo.create_turn(
                    conn, conversation_id=conversation_id, trigger="followup",
                    created_by=user["username"], input_text=text, post_comment=post_comment,
                )
                mode = "queued"
            for name, data in uploads:
                _store_upload(conn, conversation_id, turn["id"], name, data)
            repo.add_event(
                conn, conversation_id=conversation_id, turn_id=turn["id"], kind="user_message",
                payload={
                    "author": user["username"], "text": text, "files": names,
                    "mode": mode, "post_comment": post_comment,
                },
            )
        runner.wake()
        return {"mode": mode, "conversation": conversation_view(conn, user, conversation)}
    finally:
        conn.close()


async def _steer(
    conn, user: dict, conversation: dict, active, text: str, uploads, post_comment: bool,
) -> dict:
    remote = []
    try:
        for name, data in uploads:
            rel = f"uploads/{new_id('up')}-{name}"
            await runner.upload_to_active(active, f"{conversation['workspace']}/{rel}", data)
            with transaction(conn):
                _store_upload(conn, conversation["id"], active.turn_id, name, data, remote_path=rel)
            remote.append(rel)
        steer_text = text or "用户上传了补充文件，请查看。"
        if remote:
            steer_text += "\n新上传文件：\n" + "\n".join(f"- {rel}" for rel in remote)
        await runner.steer(active, steer_text)
    except CodexAppServerError as exc:
        raise ApiError(409, f"补充信息未送达 agent（{exc}），请稍后重试") from exc
    with transaction(conn):
        repo.update_turn(conn, active.turn_id, post_comment=int(post_comment))
        repo.add_event(
            conn, conversation_id=conversation["id"], turn_id=active.turn_id, kind="user_message",
            payload={
                "author": user["username"], "text": text, "files": [n for n, _ in uploads],
                "mode": "steer", "post_comment": post_comment,
            },
        )
    return {"mode": "steer", "conversation": conversation_view(conn, user, conversation)}


async def cancel(user: dict, conversation_id: str) -> dict:
    conn = open_db()
    try:
        conversation = _get_visible(conn, user, conversation_id)
        if not (_is_rm(user) or _same_user(user["username"], conversation["owner"])):
            raise AuthzError("只有对话 owner 或 RM 可以取消")
        _check_not_recovering(conversation_id)
        turn = repo.active_turn(conn, conversation_id)
        if turn is None:
            raise ApiError(409, "当前没有排队或运行中的轮次")
        with transaction(conn):
            cancelled = repo.cancel_queued_turn(conn, turn["id"], "已由用户取消排队")
            if cancelled:
                repo.add_event(
                    conn, conversation_id=conversation_id, turn_id=turn["id"],
                    kind="status", payload={"status": "cancelled", "text": "已由用户取消排队"},
                )
        if not cancelled and not await runner.stop_turn(turn["id"], "user"):
            with transaction(conn):
                repo.update_turn(conn, turn["id"], status="interrupted", error="已由用户中断")
        return {"conversation": conversation_view(conn, user, conversation)}
    finally:
        conn.close()


async def retry_comment(user: dict, turn_id: str) -> dict:
    conn = open_db()
    try:
        turn = repo.get_turn(conn, turn_id)
        if turn is None:
            raise ApiError(404, "轮次不存在")
        conversation = _get_visible(conn, user, turn["conversation_id"])
        if not (_is_rm(user) or _same_user(user["username"], conversation["owner"])):
            raise AuthzError("只有对话 owner 或 RM 可以重试发布评论")
        if turn["comment_status"] != "failed":
            raise ApiError(409, "该轮评论不需要重试")
        return {"turn": _turn_view(conn, await runner_module.post_turn_comment(conn, turn_id))}
    finally:
        conn.close()


# ─────────────────────────────────────────────────────────────
# Execution machines
# ─────────────────────────────────────────────────────────────

MAX_MACHINE_DESCRIPTION = 500


def _check_machine(
    conn: sqlite3.Connection, user: dict, group: domain.AgentGroup, machine: str, key_session_id: str,
) -> str:
    """Validate the hand-over machine choice; returns what the conversation stores.

    "" lets the agent pick from the group's system machine list; a user-given
    user@host needs this user's key upload to it that just passed, spent here.
    """
    if not machine.strip():
        if not repo.list_machines(conn, group.name):
            raise ApiError(409, "系统机器列表为空，请联系 RM 添加机器，或自填 user@host")
        return ""
    target = domain.parse_ssh_target(machine)
    jira_agent_ssh_key.sessions.claim(user, key_session_id, group, target)
    return target


def _check_rm(user: dict) -> None:
    if not _is_rm(user):
        raise AuthzError("只有 RM 可以维护系统机器列表")


def _machine_fields(body: dict) -> tuple[str, str]:
    target = domain.parse_ssh_target(body.get("ssh_target") or "")
    description = (body.get("description") or "").strip()
    if len(description) > MAX_MACHINE_DESCRIPTION:
        raise ValueError(f"说明不能超过 {MAX_MACHINE_DESCRIPTION} 字")
    return target, description


def list_machines(user: dict, group: str = "") -> dict:
    groups = runner_module.load_groups()
    conn = open_db()
    try:
        machines = repo.list_machines(conn, group or None)
    finally:
        conn.close()
    return {
        "groups": [{"name": g.name, "display_name": g.display_name} for g in groups.values()],
        "machines": machines,
        "can_manage": _is_rm(user),
    }


def create_machine(user: dict, body: dict) -> dict:
    _check_rm(user)
    group = (body.get("agent_group") or "").strip()
    if group not in runner_module.load_groups():
        raise ValueError("请选择已配置的数字员工组")
    target, description = _machine_fields(body)
    conn = open_db()
    try:
        try:
            with transaction(conn):
                machine = repo.create_machine(
                    conn, agent_group=group, ssh_target=target, description=description,
                    created_by=user["username"],
                )
        except sqlite3.IntegrityError as exc:
            raise ApiError(409, f"{group} 已有机器 {target}") from exc
        return {"machine": machine}
    finally:
        conn.close()


def update_machine(user: dict, machine_id: str, body: dict) -> dict:
    _check_rm(user)
    target, description = _machine_fields(body)
    conn = open_db()
    try:
        try:
            with transaction(conn):
                if not repo.update_machine(conn, machine_id, ssh_target=target, description=description):
                    raise ApiError(404, "机器不存在")
        except sqlite3.IntegrityError as exc:
            raise ApiError(409, f"已有机器 {target}") from exc
        return {"machine": repo.get_machine(conn, machine_id)}
    finally:
        conn.close()


def delete_machine(user: dict, machine_id: str) -> dict:
    _check_rm(user)
    conn = open_db()
    try:
        with transaction(conn):
            if not repo.delete_machine(conn, machine_id):
                raise ApiError(404, "机器不存在")
        return {"ok": True}
    finally:
        conn.close()
