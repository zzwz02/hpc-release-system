"""MCP router — the MCP endpoint for Claude Code / Codex plus the AI助手 → MCP page.

  POST   /mcp                         — MCP Streamable HTTP (Bearer personal token)
  GET    /mcp                         — 405 for MCP clients; browsers redirect to /mcp-console
  DELETE /mcp                         — end the Mcp-Session-Id session
  GET    /api/mcp/downloads/{ticket}  — attachment link handed out by jira_pull_issue
  PUT    /api/mcp/uploads/{ticket}    — raw file body for jira_upload_attachment (one use)
  GET    /api/mcp/setup.sh            — setup script (configure / status / regenerate / print / uninstall)
  POST   /api/mcp/account             — username + password + action=status|regenerate
                                        (JSON or form; Accept: text/plain → tab-separated for shell)

  GET    /api/mcp/tokens              — my tokens, the active one first (never the secret)
  POST   /api/mcp/tokens              — (re)generate; revokes the active one; secret returned once
  POST   /api/mcp/tokens/{id}/revoke
  GET    /api/mcp/stats?days=&username=&token_id=
  GET    /api/mcp/logs?days=&username=&category=&tool=&status=&kind=&target=&token_id=&limit=&offset=

Statistics and logs: RM (mcp.logs.view.all) sees everyone, others only themselves.
"""
from __future__ import annotations

import json
import re
import sqlite3
import time
import urllib.error
from pathlib import Path
from urllib.parse import parse_qs, quote

from fastapi import APIRouter, Depends, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse, PlainTextResponse, RedirectResponse, StreamingResponse
from pydantic import BaseModel

from app.api.errors import ApiError, AuthzError
from app.config import settings
from app.db.connection import connect
from app.deps import get_db, require_tab_access
from app.integrations import jira
from app.services import mcp_protocol, mcp_service

router = APIRouter(tags=["mcp"])
require_view = require_tab_access("mcp", message="无权访问 MCP")
MAX_BODY_BYTES = 4 * 1024 * 1024
# The website page (AI助手 → MCP). It must not be /mcp: that is the MCP
# endpoint, and a browser refresh there would hit it instead of the SPA.
PAGE_PATH = "/mcp-console"
SETUP_SCRIPT = Path(__file__).resolve().parents[2] / "resources" / "mcp" / "setup.sh"
HARNESSES_FILE = Path(__file__).resolve().parents[3] / "shared" / "mcp_harnesses.json"
# The base URL is pasted into a shell script; anything outside this set is refused.
_SAFE_BASE_URL = re.compile(r"^https?://[A-Za-z0-9.\-]+(:\d+)?(/[A-Za-z0-9._\-/]*)?$")


def _rpc_error(status: int, message: str, code: int = -32001) -> JSONResponse:
    headers = {"WWW-Authenticate": 'Bearer realm="release-system-mcp"'} if status == 401 else None
    return JSONResponse(status_code=status, headers=headers,
                        content={"jsonrpc": "2.0", "id": None, "error": {"code": code, "message": message}})


def _authenticated(fn):
    """Open a connection, authenticate the bearer token, run fn(conn, caller)."""
    def run(authorization: str):
        conn = connect(settings.db_path)
        try:
            caller = mcp_service.authenticate(conn, authorization)
            return fn(conn, caller)
        finally:
            conn.close()
    return run


@router.post("/mcp")
async def mcp_post(request: Request) -> Response:
    body = await request.body()
    if len(body) > MAX_BODY_BYTES:
        return _rpc_error(413, "Request too large", -32600)
    try:
        payload = json.loads(body or b"null")
    except ValueError:
        return _rpc_error(400, "Parse error", mcp_protocol.PARSE_ERROR)
    session_id = request.headers.get("mcp-session-id", "")
    # Download links go back to the address this client reached us at, not
    # PUBLIC_BASE_URL: the ticket lives in this process, and the configured
    # public site may be another instance (e.g. a dev copy on another port).
    base_url = str(request.base_url).rstrip("/")

    def handle(conn, caller):
        return mcp_protocol.handle_payload(conn, caller, session_id, base_url, payload)

    try:
        reply, new_session = await run_in_threadpool(_authenticated(handle), request.headers.get("authorization", ""))
    except PermissionError as exc:
        return _rpc_error(401, str(exc))
    except AuthzError as exc:
        return _rpc_error(403, str(exc))
    headers = {"Mcp-Session-Id": new_session} if new_session else {}
    if reply is None:
        return Response(status_code=202, headers=headers)
    # A tool that asks the user to confirm streams: the elicitation request
    # first, the tool result once the client answers.
    if isinstance(reply, mcp_protocol._Stream):
        return StreamingResponse(reply.events(), media_type="text/event-stream",
                                 headers={**headers, "Cache-Control": "no-cache"})
    return JSONResponse(reply, headers=headers)


@router.get("/mcp")
def mcp_get(request: Request) -> Response:
    """No server-initiated stream (MCP clients get 405); a browser goes to the page."""
    if "text/html" in request.headers.get("accept", ""):
        return RedirectResponse(PAGE_PATH, status_code=307)
    return Response(status_code=405, headers={"Allow": "POST, DELETE"})


@router.delete("/mcp")
async def mcp_delete(request: Request) -> Response:
    session_id = request.headers.get("mcp-session-id", "")

    def end(conn, caller):
        if session_id:
            mcp_service.end_session(conn, caller, session_id)

    try:
        await run_in_threadpool(_authenticated(end), request.headers.get("authorization", ""))
    except PermissionError as exc:
        return _rpc_error(401, str(exc))
    except AuthzError as exc:
        return _rpc_error(403, str(exc))
    return Response(status_code=204)


@router.get("/api/mcp/downloads/{ticket}")
def mcp_download(ticket: str) -> Response:
    entry = mcp_service.redeem_ticket(ticket)
    caller = mcp_service.refresh_caller(entry.caller)
    started = time.monotonic()
    log = dict(caller=caller, session=entry.session, kind="download", category="jira",
               tool="jira_attachment", target_type="jira_issue", target=entry.issue_key,
               extra={"filename": entry.filename})
    try:
        data = jira.download_attachment(entry.content_url)
    except (OSError, ValueError, RuntimeError) as exc:
        message = f"HTTP {exc.code}" if isinstance(exc, urllib.error.HTTPError) else str(exc)
        mcp_service.record(**log, status="error", error=message,
                           duration_ms=int((time.monotonic() - started) * 1000))
        raise ApiError(502, f"下载 JIRA 附件失败：{message}") from exc
    mcp_service.record(**log, status="ok", summary=f"{len(data)} bytes",
                       duration_ms=int((time.monotonic() - started) * 1000))
    return Response(
        content=data, media_type="application/octet-stream",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(entry.filename)}"},
    )


@router.api_route("/api/mcp/uploads/{ticket}", methods=["PUT", "POST"])
async def mcp_upload(ticket: str, request: Request) -> Response:
    """Receive the local file and attach it to JIRA. Plain-text replies: curl prints them."""
    from app.services.mcp_jira_tools import ToolError, check_can_write

    limit = mcp_service.UPLOAD_MAX_BYTES
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > limit:
        return PlainTextResponse(f"文件超过 {limit // (1024 * 1024)} MB\n", status_code=413)
    try:
        entry = mcp_service.claim_upload(ticket)
    except ApiError as exc:
        return PlainTextResponse(f"{exc}\n", status_code=exc.status_code)
    started = time.monotonic()
    log = dict(caller=entry.caller, session=entry.session, kind="upload", category="jira",
               tool="jira_attachment_upload", target_type="jira_issue", target=entry.issue_key)
    succeeded = False
    try:
        caller = await run_in_threadpool(mcp_service.refresh_caller, entry.caller)
        log["caller"] = caller
        chunks, size = [], 0
        async for chunk in request.stream():
            size += len(chunk)
            if size > limit:
                raise ValueError(f"文件超过 {limit // (1024 * 1024)} MB")
            chunks.append(chunk)
        if not size:
            raise ValueError("文件为空")
        data = b"".join(chunks)
        # Permission again: the assignee may have changed since the link was issued.
        caller = await run_in_threadpool(mcp_service.refresh_caller, entry.caller)
        log["caller"] = caller
        await run_in_threadpool(check_can_write, caller.user, entry.issue_key, "上传附件")
        attachment_id = await run_in_threadpool(jira.upload_agent_attachment, entry.issue_key, entry.filename, data)
        succeeded = True
    except (ApiError, ToolError, ValueError, OSError, RuntimeError) as exc:
        message = f"HTTP {exc.code}" if isinstance(exc, urllib.error.HTTPError) else str(exc)
        status = (exc.status_code if isinstance(exc, ApiError) else 403 if isinstance(exc, ToolError)
                  else 413 if "超过" in message else 400 if isinstance(exc, ValueError) else 502)
        mcp_service.record(**log, status="error", error=message, extra={"filename": entry.filename},
                           duration_ms=int((time.monotonic() - started) * 1000))
        return PlainTextResponse(f"上传失败：{message}\n", status_code=status)
    finally:
        mcp_service.finish_upload(ticket, succeeded=succeeded)
    mcp_service.record(**log, status="ok", summary=f"{entry.filename} {size} bytes",
                       extra={"filename": entry.filename, "bytes": size, "attachment_id": attachment_id},
                       duration_ms=int((time.monotonic() - started) * 1000))
    from app.services.mcp_jira_tools import attachment_reference
    return PlainTextResponse(
        f"已上传到 {entry.issue_key}：{entry.filename}（{size} bytes，附件 ID {attachment_id}）\n"
        f"{attachment_reference(entry.filename, attachment_id)}\n"
    )


# ─────────────────────────────────────────────────────────────
# One-step setup without the website (username + password)
# ─────────────────────────────────────────────────────────────

def _request_base(request: Request) -> str:
    """The address the caller reached us at (setup runs on the caller's machine)."""
    base = str(request.base_url).rstrip("/")
    if not _SAFE_BASE_URL.match(base):
        raise ApiError(400, "无法识别网站地址")
    return base


def setup_config(endpoint: str, token: str) -> dict:
    """Config file snippets; they work for both the CLIs and the VS Code extensions."""
    entry = {"type": "http", "url": endpoint, "headers": {"Authorization": f"Bearer {token}"}}
    return {
        "claude_json": {"mcpServers": {mcp_protocol.SERVER_NAME: entry}},
        "codex_toml": (
            f"[mcp_servers.{mcp_protocol.SERVER_NAME}]\n"
            f'url = "{endpoint}"\n'
            f'http_headers = {{ "Authorization" = "Bearer {token}" }}\n'
            "enabled = true\nrequired = true\nstartup_timeout_sec = 30\n"
        ),
    }


def _plain_status(active: dict | None) -> str:
    if not active:
        return "none"
    return "\t".join(["active", active["token_prefix"], active["name"], active["created_at"],
                      active["last_used_at"] or "-", active.get("last_client") or "-"])


@router.get("/api/mcp/setup.sh")
def setup_script(request: Request) -> Response:
    marks = {"form": "", "none": "（不推荐：不支持用户名确认，无法发评论；附件会直接提交）",
             "unknown": "（谨慎使用：未核实是否支持发布前确认）"}
    harnesses = json.loads(HARNESSES_FILE.read_text(encoding="utf-8"))["harnesses"]
    links = "\n".join(f"#   - {item['name']}：{item['url']}{marks.get(item.get('confirm'), '')}"
                      for item in harnesses)
    script = (SETUP_SCRIPT.read_text(encoding="utf-8")
              .replace("__BASE_URL__", _request_base(request)).replace("__HARNESS_LINKS__", links))
    return Response(content=script, media_type="text/x-shellscript; charset=utf-8")


@router.post("/api/mcp/account")
async def account(request: Request) -> Response:
    plain = "text/plain" in request.headers.get("accept", "")
    raw = await request.body()
    if len(raw) > 64 * 1024:
        raise ApiError(413, "请求过大")
    if request.headers.get("content-type", "").startswith("application/json"):
        try:
            body = json.loads(raw or b"{}")
        except ValueError:
            body = None
        if not isinstance(body, dict):
            raise ApiError(400, "请求体必须是 JSON 对象")
    else:
        body = {key: values[0] for key, values in parse_qs(raw.decode("utf-8", "replace")).items()}

    def run():
        conn = connect(settings.db_path)
        try:
            return mcp_service.account_action(
                conn, username=str(body.get("username") or ""), password=str(body.get("password") or ""),
                action=str(body.get("action") or "status"), name=str(body.get("name") or ""),
            )
        finally:
            conn.close()

    try:
        result = await run_in_threadpool(run)
    except (PermissionError, AuthzError, ApiError, ValueError, RuntimeError) as exc:
        status = (401 if isinstance(exc, PermissionError) else 403 if isinstance(exc, AuthzError)
                  else exc.status_code if isinstance(exc, ApiError) else 400)
        if plain:
            return PlainTextResponse(str(exc), status_code=status)
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=status)
    # Plain text for the shell script: the token (regenerate) or one status line.
    if plain:
        return PlainTextResponse((result.get("token") or _plain_status(result["active"])) + "\n")
    endpoint = f"{_request_base(request)}/mcp"
    if result.get("token"):
        result["endpoint"] = endpoint
        result["config"] = setup_config(endpoint, result["token"])
    return JSONResponse(result)


# ─────────────────────────────────────────────────────────────
# Website page (session cookie)
# ─────────────────────────────────────────────────────────────

class TokenBody(BaseModel):
    name: str = ""


@router.get("/api/mcp/tokens")
def list_tokens(user: dict = Depends(require_view), conn: sqlite3.Connection = Depends(get_db)) -> dict:
    return {"tokens": mcp_service.list_tokens(conn, user),
            "active": mcp_service.active_token(conn, user["username"])}


@router.post("/api/mcp/tokens")
def create_token(body: TokenBody, user: dict = Depends(require_view),
                 conn: sqlite3.Connection = Depends(get_db)) -> dict:
    return {"token": mcp_service.create_token(conn, user, body.name.strip() or "网站生成")}


@router.post("/api/mcp/tokens/{token_id}/revoke")
def revoke_token(token_id: int, user: dict = Depends(require_view),
                 conn: sqlite3.Connection = Depends(get_db)) -> dict:
    return {"token": mcp_service.revoke_token(conn, user, token_id)}


@router.get("/api/mcp/info")
def info(request: Request, user: dict = Depends(require_view)) -> dict:
    from app.services.mcp_tools import tools_for
    return {
        "endpoint": f"{str(request.base_url).rstrip('/')}/mcp",
        "server_name": mcp_protocol.SERVER_NAME,
        "tools": [{"name": t.name, "title": t.title, "category": t.category, "read_only": t.read_only,
                   "description": t.description} for t in tools_for(user["role"])],
        "can_view_all": mcp_service.can_view_all(user["role"]),
    }


@router.get("/api/mcp/stats")
def stats(days: int = 30, username: str = "", token_id: int | None = None,
          user: dict = Depends(require_view), conn: sqlite3.Connection = Depends(get_db)) -> dict:
    return mcp_service.stats(conn, user, days=days, username=username, token_id=str(token_id or ""))


@router.get("/api/mcp/logs")
def logs(days: int = 30, username: str = "", category: str = "", tool: str = "", status: str = "",
         kind: str = "", target: str = "", token_id: int | None = None, limit: int = 50, offset: int = 0,
         user: dict = Depends(require_view), conn: sqlite3.Connection = Depends(get_db)) -> dict:
    return mcp_service.logs(conn, user, days=days, username=username, category=category, tool=tool,
                            status=status, kind=kind, target=target, token_id=str(token_id or ""),
                            limit=limit, offset=offset)
