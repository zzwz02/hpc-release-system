"""MCP service — personal access tokens, call logging, statistics and
short-lived attachment download tickets.

Tokens are shown once at creation; only their SHA-256 is stored.  Every MCP
request re-reads the token's user so a role change or user deletion takes
effect immediately.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import secrets
import sqlite3
import threading
import time
from dataclasses import dataclass
from typing import Any

from app.api.errors import ApiError, AuthzError
from app.config import settings
from app.db.connection import connect, transaction
from app.domain.permissions import roles_for_capability, roles_for_tab
from app.repositories import mcp_repo, users_repo
from app.timeutil import beijing_now, beijing_timestamp

TOKEN_PREFIX = "rsmcp_"
# One active token per user: every new token revokes the previous one, so a
# person has a single credential to copy between machines and to revoke.
TOKEN_NAME_MAX = 60
# Attachment download links handed to the agent; long enough for a slow pull.
DOWNLOAD_TICKET_SECONDS = 30 * 60
# Attachment uploads: the link is used once; the size matches what the site
# downloads from JIRA (jira.download_attachment).
UPLOAD_TICKET_SECONDS = 30 * 60
UPLOAD_MAX_BYTES = 50 * 1024 * 1024
STATS_MAX_DAYS = 366
# Password → token setup: per-username failures before a cool-down, so a
# retry loop cannot lock the domain account in LDAP.
SETUP_MAX_FAILURES = 5
SETUP_LOCK_SECONDS = 10 * 60
LOG_PAGE_MAX = 200


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def can_use(role: str) -> bool:
    return role in roles_for_tab("mcp")


def can_view_all(role: str) -> bool:
    return role in roles_for_capability("mcp.logs.view.all")


# ─────────────────────────────────────────────────────────────
# Tokens
# ─────────────────────────────────────────────────────────────

def list_tokens(conn: sqlite3.Connection, user: dict) -> list[dict]:
    return mcp_repo.list_tokens(conn, user["username"])


def active_token(conn: sqlite3.Connection, username: str) -> dict | None:
    """The user's one active token (prefix only) with when and from what it was last used."""
    return mcp_repo.active_token(conn, username)


def create_token(conn: sqlite3.Connection, user: dict, name: str) -> dict:
    """(Re)generate the user's token; the previous active token stops working at once."""
    name = (name or "").strip()[:TOKEN_NAME_MAX] or "default"
    token = TOKEN_PREFIX + secrets.token_urlsafe(32)
    with transaction(conn):
        mcp_repo.revoke_active_tokens(conn, user["username"], beijing_timestamp())
        token_id = mcp_repo.insert_token(
            conn, username=user["username"], name=name, token_hash=_hash(token),
            token_prefix=token[: len(TOKEN_PREFIX) + 6], created_at=beijing_timestamp(),
        )
    return {**mcp_repo.get_token(conn, token_id), "token": token}


def revoke_token(conn: sqlite3.Connection, user: dict, token_id: int) -> dict:
    with transaction(conn):
        token = mcp_repo.get_token(conn, token_id)
        if not token or token["username"] != user["username"]:
            raise ApiError(404, "token 不存在")
        mcp_repo.revoke_token(conn, token_id, beijing_timestamp())
    return mcp_repo.get_token(conn, token_id)


_SETUP_FAILURES: dict[str, list[float]] = {}
_SETUP_LOCK = threading.Lock()


def _check_setup_throttle(username: str) -> None:
    now = time.monotonic()
    with _SETUP_LOCK:
        recent = [t for t in _SETUP_FAILURES.get(username, []) if now - t < SETUP_LOCK_SECONDS]
        _SETUP_FAILURES[username] = recent
        if len(recent) >= SETUP_MAX_FAILURES:
            wait = int(SETUP_LOCK_SECONDS - (now - recent[0])) // 60 + 1
            raise ApiError(429, f"密码错误次数过多，请 {wait} 分钟后再试")


def _record_setup_failure(username: str, failed: bool) -> None:
    with _SETUP_LOCK:
        if failed:
            _SETUP_FAILURES.setdefault(username, []).append(time.monotonic())
        else:
            _SETUP_FAILURES.pop(username, None)


SETUP_ACTIONS = ("status", "regenerate")


def verify_credentials(conn: sqlite3.Connection, *, username: str, password: str) -> dict:
    """Check website credentials without opening a website session; return the user.

    Same authentication as the login page: LDAP when it is enabled (first
    login provisions the user from LDAP groups), otherwise local accounts.
    """
    from app.integrations import ldap as ldap_integration
    from app.services import auth_service

    username = (username or "").strip()
    if not username or not password:
        raise PermissionError("用户名和密码不能为空")
    key = username.casefold()
    _check_setup_throttle(key)
    try:
        if ldap_integration.ldap_status().get("enabled"):
            uname, display, groups = ldap_integration.authenticate(
                username, password, ldap_config=ldap_integration.current_ldap_config(),
            )
            auth_service.ensure_ldap_user(conn, uname, display, groups)
        else:
            row = conn.execute("SELECT password_hash FROM users WHERE username = ?", (username,)).fetchone()
            if not row or not auth_service.verify_password(password, row["password_hash"]):
                raise PermissionError("用户名或密码不正确")
            uname = username
    except PermissionError:
        _record_setup_failure(key, True)
        raise
    _record_setup_failure(key, False)
    user = users_repo.get_user(conn, uname)
    if not user or not can_use(user["role"]):
        raise AuthzError(f"当前角色 {user['role'] if user else '-'} 无权使用 MCP，请联系管理员")
    return user


def account_action(conn: sqlite3.Connection, *, username: str, password: str, action: str, name: str) -> dict:
    """Password-authenticated token management for the setup script.

    status      — the active token's prefix and usage (never the secret)
    regenerate  — new token; the old one (on every machine) stops working
    """
    if action not in SETUP_ACTIONS:
        raise ValueError(f"未知操作：{action}")
    user = verify_credentials(conn, username=username, password=password)
    public_user = {k: user.get(k, "") for k in ("username", "role", "display_name")}
    if action == "status":
        return {"user": public_user, "active": active_token(conn, user["username"])}
    created = create_token(conn, user, name)
    record(caller=McpCaller(token_id=created["id"], user=user), session={}, kind="token", status="ok",
           tool="regenerate_token", summary=f"{created['token_prefix']}… {created['name']}")
    return {"user": public_user, "active": active_token(conn, user["username"]), "token": created["token"]}


@dataclass
class McpCaller:
    token_id: int
    user: dict[str, Any]


def authenticate(conn: sqlite3.Connection, authorization: str) -> McpCaller:
    """Resolve ``Authorization: Bearer <token>``; PermissionError → 401, AuthzError → 403."""
    scheme, _, token = (authorization or "").partition(" ")
    token = token.strip()
    if scheme.lower() != "bearer" or not token.startswith(TOKEN_PREFIX):
        raise PermissionError("缺少 MCP token：请求头需要 Authorization: Bearer rsmcp_...")
    row = mcp_repo.find_active_token_user(conn, _hash(token))
    if not row:
        raise PermissionError("MCP token 无效或已吊销")
    if not can_use(row["role"]):
        raise AuthzError(f"当前角色 {row['role']} 无权使用 MCP")
    with transaction(conn):
        mcp_repo.touch_token(conn, row["token_id"], beijing_timestamp())
    return McpCaller(
        token_id=row["token_id"],
        user={"username": row["username"], "role": row["role"], "display_name": row["display_name"]},
    )


def refresh_caller(caller: McpCaller) -> McpCaller:
    """Revalidate a deferred action against the current token and user."""
    conn = connect(settings.db_path)
    try:
        row = mcp_repo.find_active_token_user(conn, token_id=caller.token_id)
    finally:
        conn.close()
    if not row or row["username"] != caller.user["username"]:
        raise ApiError(401, "MCP token 无效或已吊销，请重新连接")
    if not can_use(row["role"]):
        raise ApiError(403, f"当前角色 {row['role']} 无权使用 MCP")
    return McpCaller(row["token_id"], {k: row[k] for k in ("username", "role", "display_name")})


# ─────────────────────────────────────────────────────────────
# Sessions and logging
# ─────────────────────────────────────────────────────────────

def start_session(conn: sqlite3.Connection, caller: McpCaller, client: dict, protocol_version: str,
                  capabilities: dict | None = None) -> str:
    session_id = secrets.token_hex(16)
    with transaction(conn):
        mcp_repo.insert_session(
            conn, session_id=session_id, token_id=caller.token_id, username=caller.user["username"],
            client_name=str(client.get("name") or "")[:100], client_version=str(client.get("version") or "")[:50],
            protocol_version=protocol_version,
            client_capabilities=json.dumps(capabilities or {}, ensure_ascii=False)[:4000],
            created_at=beijing_timestamp(),
        )
    return session_id


def session_client(conn: sqlite3.Connection, caller: McpCaller, session_id: str) -> dict:
    """The client a session belongs to; empty for an unknown or foreign session."""
    session = mcp_repo.get_session(conn, session_id) if session_id else None
    if not session or session["token_id"] != caller.token_id or session["ended_at"]:
        return {"session_id": "", "client_name": "", "client_version": "", "can_confirm": False}
    try:
        capabilities = json.loads(session["client_capabilities"] or "{}")
    except ValueError:
        capabilities = {}
    elicitation = capabilities.get("elicitation") if isinstance(capabilities, dict) else None
    return {"session_id": session_id, "client_name": session["client_name"],
            "client_version": session["client_version"],
            # Empty capabilities are the older form-only declaration; URL-only
            # clients cannot show our requestedSchema form.
            "can_confirm": isinstance(elicitation, dict) and (
                not elicitation or isinstance(elicitation.get("form"), dict))}


def end_session(conn: sqlite3.Connection, caller: McpCaller, session_id: str) -> None:
    with transaction(conn):
        mcp_repo.end_session(conn, session_id, caller.token_id, beijing_timestamp())


def record(
    *,
    caller: McpCaller,
    session: dict,
    kind: str,
    status: str,
    category: str = "",
    tool: str = "",
    target_type: str = "",
    target: str = "",
    args_json: str = "",
    extra: dict | None = None,
    error: str = "",
    summary: str = "",
    duration_ms: int = 0,
) -> None:
    """Write one log row on its own connection; logging never fails the call."""
    entry = {
        "created_at": beijing_timestamp(), "username": caller.user["username"], "token_id": caller.token_id,
        "session_id": session.get("session_id", ""), "client_name": session.get("client_name", ""),
        "client_version": session.get("client_version", ""), "kind": kind, "category": category,
        "tool": tool, "target_type": target_type, "target": target[:200], "args_json": args_json,
        "extra_json": json.dumps(extra, ensure_ascii=False, sort_keys=True) if extra else "",
        "status": status, "error": error[:1000], "result_summary": summary[:500],
        "duration_ms": int(duration_ms),
    }
    try:
        conn = connect(settings.db_path)
        try:
            with transaction(conn):
                mcp_repo.insert_log(conn, entry)
        finally:
            conn.close()
    except sqlite3.Error:
        import logging
        logging.getLogger(__name__).exception("MCP call log write failed")


# ─────────────────────────────────────────────────────────────
# Statistics (AI助手 → MCP)
# ─────────────────────────────────────────────────────────────

def _filters(user: dict, *, days: int, username: str, **extra: str) -> dict[str, str]:
    days = max(1, min(int(days or 30), STATS_MAX_DAYS))
    since = (beijing_now() - dt.timedelta(days=days - 1)).strftime("%Y-%m-%d 00:00:00")
    if not can_view_all(user["role"]):
        username = user["username"]
    return {"since": since, "username": (username or "").strip(), **{k: (v or "").strip() for k, v in extra.items()}}


def _with_names(conn: sqlite3.Connection, rows: list[dict]) -> list[dict]:
    names = users_repo.display_name_map(conn)
    return [{**row, "display_name": names.get(row["username"], "")} for row in rows]


def stats(conn: sqlite3.Connection, user: dict, *, days: int = 30, username: str = "",
          token_id: str = "") -> dict:
    filters = _filters(user, days=days, username=username, token_id=token_id)
    result = mcp_repo.stats(conn, filters)
    result["by_user"] = _with_names(conn, result["by_user"])
    result["by_token"] = _with_names(conn, result["by_token"])
    result.update(since=filters["since"], scope="all" if can_view_all(user["role"]) else "mine")
    return result


def logs(conn: sqlite3.Connection, user: dict, *, days: int = 30, username: str = "", category: str = "",
         tool: str = "", status: str = "", kind: str = "", target: str = "", token_id: str = "",
         limit: int = 50, offset: int = 0) -> dict:
    filters = _filters(user, days=days, username=username, category=category, tool=tool, status=status,
                       kind=kind, target=target, token_id=token_id)
    page = mcp_repo.list_logs(conn, filters, limit=max(1, min(limit, LOG_PAGE_MAX)), offset=max(0, offset))
    page["items"] = _with_names(conn, page["items"])
    return page


# ─────────────────────────────────────────────────────────────
# Download tickets (single uvicorn worker → in-process store)
# ─────────────────────────────────────────────────────────────

@dataclass
class DownloadTicket:
    caller: McpCaller
    session: dict
    issue_key: str
    filename: str
    content_url: str
    expires: float


_TICKETS: dict[str, DownloadTicket] = {}
_TICKETS_LOCK = threading.Lock()


def issue_ticket(caller: McpCaller, session: dict, *, issue_key: str, filename: str, content_url: str) -> str:
    ticket = secrets.token_urlsafe(24)
    now = time.monotonic()
    with _TICKETS_LOCK:
        for key in [k for k, v in _TICKETS.items() if v.expires < now]:
            del _TICKETS[key]
        _TICKETS[ticket] = DownloadTicket(caller, session, issue_key, filename, content_url,
                                          now + DOWNLOAD_TICKET_SECONDS)
    return ticket


def redeem_ticket(ticket: str) -> DownloadTicket:
    with _TICKETS_LOCK:
        entry = _TICKETS.get(ticket)
    if not entry or entry.expires < time.monotonic():
        raise ApiError(404, "下载链接无效或已过期，请重新通过 MCP 拉取")
    return entry


@dataclass
class UploadTicket:
    caller: McpCaller
    session: dict
    issue_key: str
    filename: str
    expires: float
    busy: bool = False


_UPLOADS: dict[str, UploadTicket] = {}


def issue_upload_ticket(caller: McpCaller, session: dict, *, issue_key: str, filename: str) -> str:
    ticket = secrets.token_urlsafe(24)
    now = time.monotonic()
    with _TICKETS_LOCK:
        for key in [k for k, v in _UPLOADS.items() if v.expires < now]:
            del _UPLOADS[key]
        _UPLOADS[ticket] = UploadTicket(caller, session, issue_key, filename, now + UPLOAD_TICKET_SECONDS)
    return ticket


def claim_upload(ticket: str) -> UploadTicket:
    """Reserve a ticket for one upload attempt; a second concurrent use is refused."""
    with _TICKETS_LOCK:
        entry = _UPLOADS.get(ticket)
        if not entry or entry.expires < time.monotonic():
            raise ApiError(404, "上传链接无效、已过期或已用过，请重新通过 MCP 获取")
        if entry.busy:
            raise ApiError(409, "该链接正在上传中")
        entry.busy = True
    return entry


def finish_upload(ticket: str, *, succeeded: bool) -> None:
    """A success uses the ticket up; a failure frees it for a retry."""
    with _TICKETS_LOCK:
        if succeeded:
            _UPLOADS.pop(ticket, None)
        elif ticket in _UPLOADS:
            _UPLOADS[ticket].busy = False
