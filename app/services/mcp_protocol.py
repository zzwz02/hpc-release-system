"""MCP JSON-RPC handling for the Streamable HTTP transport.

Implements the server side Claude Code, Codex and OpenCode need: initialize,
ping, tools/list, tools/call and notifications. Almost every request is
answered with one JSON body; a tool that needs the user to confirm answers
with an SSE stream containing elicitation/create before acting.

Destructive writes require typed usernames through elicitation. Read-only and
non-destructive tools do not request confirmation; permissions still apply.
Confirmation tools require a valid session and cannot be called in batches.
The pending-confirmation registry lives in this process (single worker).
"""
from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from collections.abc import Iterator
from typing import Any

from app.api.errors import ApiError
from app.services import mcp_service
from app.services.mcp_service import McpCaller
from app.services.mcp_tools import ToolContext, ToolError, ToolResult, get_tool, tools_for

logger = logging.getLogger(__name__)

SUPPORTED_PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")
SERVER_NAME = "release-system"
SERVER_VERSION = "1.0.0"
INSTRUCTIONS = (
    "HPC 发布协作系统的 MCP。可以搜索 JIRA、把工单（字段、评论、附件）拉取到本地目录、"
    "给工单追加评论、上传附件。评论和附件以发布系统的 JIRA 账号提交并注明你的名字；"
    "发评论时必须通过 elicitation 输入本人域账号用户名，与 token 所属账号匹配才发送；"
    "客户端不支持时不能发评论。附件上传不需要确认，但仍检查权限。"
    "起草或发送 JIRA 评论前先调用 skill_load(skill=\"jira-format\") 读取格式规范；评论正文先统一处理格式，再弹窗确认。"
    "可用 jenkins_search_failures 筛选 Jenkins 失败记录，再用 jenkins_get_failure 查询错误原因、错误片段和处理建议等详情；需要原始测试 case 报错或日志时调用 jenkins_get_build_errors。"
    "另外提供 MetaX HPC 专家方法：处理 cu-bridge、CUDA 迁移到 MACA 的任务前先调用 skill_load，"
    "按它返回的方法作答，不要凭记忆或公网文档回答这类问题。"
)
PARSE_ERROR, INVALID_REQUEST, METHOD_NOT_FOUND, INVALID_PARAMS = -32700, -32600, -32601, -32602

CONFIRM_TIMEOUT_SECONDS = 300
_PENDING: dict[str, dict[str, Any]] = {}
_PENDING_LOCK = threading.Lock()


class _Stream:
    """A tools/call answered as SSE because the user is asked to confirm."""

    def __init__(self, exchange: Exchange, msg_id: Any, tool, arguments: dict) -> None:
        self.exchange, self.msg_id, self.tool, self.arguments = exchange, msg_id, tool, arguments

    def events(self) -> Iterator[bytes]:
        for message in self.exchange.call_with_confirmation(self.msg_id, self.tool, self.arguments):
            yield f"event: message\ndata: {json.dumps(message, ensure_ascii=False)}\n\n".encode()


def _settle(message: dict, caller: McpCaller, session: dict) -> bool:
    """Only the originating token and session can answer a confirmation."""
    with _PENDING_LOCK:
        waiter = _PENDING.get(str(message.get("id")))
        if (not waiter or waiter["token_id"] != caller.token_id
                or waiter["session_id"] != session.get("session_id") or waiter["reply"] is not None):
            return False
        waiter["reply"] = message
        waiter["event"].set()
    return True


def _error(msg_id: Any, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}


def _result(msg_id: Any, result: dict) -> dict:
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


class Exchange:
    """One HTTP request: the caller, its session, and a session created by initialize."""

    def __init__(self, conn, caller: McpCaller, session_id: str, base_url: str,
                 allow_stream: bool = True) -> None:
        self.conn = conn
        self.caller = caller
        self.base_url = base_url
        # Batches cannot carry SSE; confirmation tools must be sent individually.
        self.allow_stream = allow_stream
        self.session = mcp_service.session_client(conn, caller, session_id)
        self.new_session_id = ""

    def handle(self, message: Any) -> dict | _Stream | None:
        if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
            return _error(None, INVALID_REQUEST, "Invalid JSON-RPC message")
        method = message.get("method")
        msg_id = message.get("id")
        if not isinstance(method, str):
            _settle(message, self.caller, self.session)
            return None
        if "id" not in message:
            return None
        params = message.get("params") or {}
        if not isinstance(params, dict):
            return _error(msg_id, INVALID_PARAMS, "params must be an object")
        if method == "initialize":
            return _result(msg_id, self._initialize(params))
        if method == "ping":
            return _result(msg_id, {})
        if method == "tools/list":
            return _result(msg_id, {"tools": [t.definition() for t in tools_for(self.caller.user["role"])]})
        if method == "tools/call":
            return self._call(msg_id, params)
        if method in ("resources/list", "prompts/list"):
            return _result(msg_id, {method.split("/")[0]: []})
        return _error(msg_id, METHOD_NOT_FOUND, f"Method not found: {method}")

    def _initialize(self, params: dict) -> dict:
        requested = str(params.get("protocolVersion") or "")
        version = requested if requested in SUPPORTED_PROTOCOL_VERSIONS else SUPPORTED_PROTOCOL_VERSIONS[0]
        client = params.get("clientInfo") if isinstance(params.get("clientInfo"), dict) else {}
        capabilities = params.get("capabilities") if isinstance(params.get("capabilities"), dict) else {}
        self.new_session_id = mcp_service.start_session(self.conn, self.caller, client, version, capabilities)
        self.session = mcp_service.session_client(self.conn, self.caller, self.new_session_id)
        mcp_service.record(caller=self.caller, session=self.session, kind="connection", status="ok",
                           tool="initialize", summary=f"protocol {version}",
                           extra={"can_confirm": self.session["can_confirm"],
                                  "capabilities": sorted(capabilities)})
        return {
            "protocolVersion": version,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": SERVER_NAME, "title": "HPC 发布协作系统", "version": SERVER_VERSION},
            "instructions": INSTRUCTIONS,
        }

    def _call(self, msg_id: Any, params: dict) -> dict | _Stream:
        name = params.get("name")
        arguments = params.get("arguments") or {}
        tool = get_tool(name) if isinstance(name, str) else None
        if not tool or not tool.allowed(self.caller.user["role"]):
            return _error(msg_id, INVALID_PARAMS, f"Unknown tool: {name}")
        if not isinstance(arguments, dict):
            return _error(msg_id, INVALID_PARAMS, "arguments must be an object")
        if not tool.read_only and not self.session.get("session_id"):
            return _error(msg_id, INVALID_PARAMS, "写操作需要有效的 MCP 会话，请重新 initialize")
        if not tool.read_only and not self.allow_stream:
            return _error(msg_id, INVALID_PARAMS, "写操作不支持批量请求，请单独调用")
        if tool.requires_confirmation and not self.session.get("can_confirm"):
            ctx = ToolContext(self.caller, self.session, self.base_url)
            message = f"执行「{tool.title}」需要通过 elicitation 输入用户名；当前客户端不支持，未执行。"
            result = ToolResult.text(message)
            result.is_error = True
            self._record(tool, arguments, result, ctx, error=message,
                         confirmed="client_cannot_confirm", started=time.monotonic())
            return _result(msg_id, {"content": result.content, "isError": True})
        if tool.requires_confirmation and self.session.get("can_confirm") and self.allow_stream:
            return _Stream(self, msg_id, tool, arguments)
        return self._run(msg_id, tool, arguments, confirmed="")

    def _run(self, msg_id: Any, tool, arguments: dict, *, confirmed: str) -> dict:
        ctx = ToolContext(caller=self.caller, session=self.session, base_url=self.base_url)
        started = time.monotonic()
        error = ""
        try:
            if not tool.read_only:
                self.caller = mcp_service.refresh_caller(self.caller)
                ctx.caller = self.caller
                if not tool.allowed(ctx.user["role"]):
                    raise ToolError("当前角色无权调用该工具")
            result = tool.handler(ctx, arguments)
        except (ToolError, ApiError) as exc:
            error = str(exc)
        except Exception as exc:
            logger.exception("MCP tool %s failed", tool.name)
            error = f"内部错误：{type(exc).__name__}"
        if error:
            result = ToolResult.text(error, log=ctx.log)
            result.is_error = True
        self._record(tool, arguments, result, ctx, error=error, confirmed=confirmed, started=started)
        return _result(msg_id, {"content": result.content, "isError": result.is_error})

    def _record(self, tool, arguments: dict, result: ToolResult, ctx: ToolContext, *, error: str,
                confirmed: str, started: float) -> None:
        log = result.log
        extra = dict(log.extra or ctx.log.extra or {})
        if confirmed:
            extra["confirm"] = confirmed
        mcp_service.record(
            caller=self.caller, session=self.session, kind="tool", category=tool.category, tool=tool.name,
            status="error" if result.is_error else "ok", target_type=log.target_type or ctx.log.target_type,
            target=log.target or ctx.log.target, args_json=tool.logged_args(arguments),
            extra=extra, error=error, summary=log.summary,
            duration_ms=int((time.monotonic() - started) * 1000),
        )

    def call_with_confirmation(self, msg_id: Any, tool, arguments: dict) -> Iterator[dict]:
        ctx = ToolContext(caller=self.caller, session=self.session, base_url=self.base_url)
        started = time.monotonic()
        try:
            if tool.prepare:
                arguments = tool.prepare(ctx, dict(arguments))
            message = tool.confirmation_message(ctx, arguments)
        except ToolError as exc:
            result = ToolResult.text(str(exc), log=ctx.log)
            result.is_error = True
            self._record(tool, arguments, result, ctx, error=str(exc), confirmed="not_reached", started=started)
            yield _result(msg_id, {"content": result.content, "isError": True})
            return
        request_id = f"confirm-{uuid.uuid4().hex}"
        waiter: dict[str, Any] = {"event": threading.Event(), "reply": None,
                                  "token_id": self.caller.token_id,
                                  "session_id": self.session["session_id"]}
        with _PENDING_LOCK:
            _PENDING[request_id] = waiter
        schema = {"type": "object", "properties": {"username": {
            "type": "string", "title": "域账号用户名",
            "description": "请输入你本人的域账号用户名，匹配 token 所属账号后执行"}},
            "required": ["username"]}
        try:
            yield {"jsonrpc": "2.0", "id": request_id, "method": "elicitation/create", "params": {
                "message": message,
                "requestedSchema": schema,
            }}
            answered = waiter["event"].wait(CONFIRM_TIMEOUT_SECONDS)
            reply = waiter["reply"] or {}
        finally:
            with _PENDING_LOCK:
                _PENDING.pop(request_id, None)
        answer = (reply.get("result") or {}) if isinstance(reply.get("result"), dict) else {}
        action = str(answer.get("action") or "")
        content = answer.get("content") if isinstance(answer.get("content"), dict) else {}
        if not answered:
            outcome, text = "timeout", "等待用户确认超时，未执行。"
        elif action != "accept":
            outcome, text = action or "no_answer", "用户未确认，未执行。"
        elif (
            not isinstance(content.get("username"), str)
            or content["username"].strip().casefold() != self.caller.user["username"].casefold()
        ):
            outcome, text = "username_mismatch", "用户名为空或与 token 所属账号不匹配，未执行。"
        else:
            yield self._run(msg_id, tool, arguments,
                            confirmed="username_matched")
            return
        refused = ToolResult.text(text, log=ctx.log)
        refused.is_error = True
        self._record(tool, arguments, refused, ctx, error=text, confirmed=outcome, started=started)
        yield _result(msg_id, {"content": refused.content, "isError": True})


def handle_payload(conn, caller: McpCaller, session_id: str, base_url: str, payload: Any) -> tuple[Any, str]:
    exchange = Exchange(conn, caller, session_id, base_url)
    if isinstance(payload, list):
        if not payload:
            return _error(None, INVALID_REQUEST, "Empty batch"), ""
        exchange.allow_stream = False
        replies = [reply for reply in (exchange.handle(item) for item in payload) if reply is not None]
        return (replies or None), exchange.new_session_id
    return exchange.handle(payload), exchange.new_session_id
