"""MCP JSON-RPC handling for the Streamable HTTP transport.

Implements the server side Claude Code, Codex and OpenCode need: initialize,
ping, tools/list, tools/call and notifications. Almost every request is
answered with one JSON body; a tool that needs the user to confirm answers
with an SSE stream containing elicitation/create before acting.

Destructive writes require explicit approval through elicitation. Read-only and
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
from collections.abc import Generator, Iterator
from typing import Any

from app.api.errors import ApiError
from app.domain.mcp_access import DEFAULT_JIRA_PROJECT, MCP_VERIFY_IDENTITY_TOOL
from app.services import mcp_access_service, mcp_service
from app.services.mcp_service import McpCaller
from app.services.mcp_tools import ToolContext, ToolError, ToolResult, get_tool

logger = logging.getLogger(__name__)

SUPPORTED_PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")
SERVER_NAME = "release-system"
SERVER_VERSION = "1.0.0"
INSTRUCTIONS = (
    "HPC 发布协作系统的 MCP。可以搜索或查询 JIRA、按用户要求把工单（字段、评论、附件）下载到本地目录、"
    "给工单追加评论、上传附件。评论和附件以发布系统的 JIRA 账号提交并注明你的名字；"
    "用户只问 JIRA 的信息、状态、进展或要求查询时，调用 jira_search 或 jira_pull_issue 查询后直接回答，"
    "不要创建本地目录、写 issue.md 或下载附件。"
    "jira_search 每次返回一页，最多 50 条。有剩余时向用户说明已返回条数和总数。"
    "用户需要更多结果时，沿用原查询和 max_results，使用结果提示的 start_at 查下一页。"
    "jira_pull_issue 默认 save_to_local=false；"
    "仅在用户明确要求下载或保存到本地时设为 true，保存目录遵循用户指定。"
    "评论和上传附件均不要求先调用 jira_pull_issue。已知工单编号且评论所需信息齐全时，直接调用 jira_add_comment；"
    "已知工单编号和本地文件路径时，直接调用 jira_upload_attachment 并执行返回的上传命令。"
    "不要为了验证工单存在或操作权限而先拉取工单，服务端会实时检查；仅在任务需要补充工单上下文、历史评论或查找已有附件时才拉取。"
    f"连接时通过个人 token 识别账号。会话默认只开放矩阵允许的只读工具，JIRA 只可读取 {DEFAULT_JIRA_PROJECT}。"
    f"需要写入、查询其他 JIRA 项目或用户要求验证时，调用 {MCP_VERIFY_IDENTITY_TOOL}，由用户填写本人域账号。"
    "每个 MCP 会话只需成功验证一次。未验证、失败或客户端不支持表单时保留默认只读权限。"
    "不要自动重试或替用户填写域账号。验证后仍适用工具权限矩阵及域账号的 JIRA 浏览权限。"
    "发评论前由用户在 elicitation 表单中勾选「确认执行」并提交。"
    "未勾选、拒绝、取消或确认超时均表示未获确认，应告知用户原因，不要自动重试或替用户确认。"
    "客户端返回拒绝或取消不能证明用户看到了弹窗；也可能是客户端审批策略自动拒绝。"
    "域账号验证或评论确认失败时，向用户原样展示工具返回的提示。不要另行建议修改配置文件。"
    "客户端不支持表单时只能使用默认权限。附件上传在验证后直接检查上传权限。"
    "MCP 评论和附件上传实时检查工单经办人或 RM 权限。"
    "被 RM 授予「允许写入所有 JIRA」的 Guest 可向本人有浏览权限的 JIRA 工单评论和上传附件。"
    "这两项操作不受 JIRA agent 的 cybersecurity risk 锁限制。"
    "起草或发送 JIRA 评论前先调用 skill_load(skill=\"jira-format\") 读取格式规范；评论正文先统一处理格式，再弹窗确认。"
    "调用 jira_add_comment 前，先在对话中向用户展示拟发送的完整评论；长评论在确认弹窗中只展示摘要，发送时保留全文。"
    "可用 jira_read_attachment 按附件 ID 在内存中读取文本或图片，不必执行 curl 或创建附件文件。"
    "图片以原生 image 内容块返回完整原始字节，每次一张；服务端不缩放或压缩，仍受单件读取上限限制。"
    "客户端和模型可能自行缩放或拒绝图片，客户端仍可能把图片保存在会话记录中。"
    "其他二进制附件若返回 needs_user_action，先说明尚未读取正文，向用户展示 suggested_question；"
    "取得明确同意后才下载到本地解析。不要自动下载，不要把原始二进制 Base64 当文本塞进模型上下文。"
    "可用 jenkins_list_jobs 和 jenkins_list_builds 直接定位任务及构建，日期按北京时间；不依赖失败记录后端。"
    "jenkins_* 工具用于 PDE Jenkins；cicd_jenkins_list_jobs、cicd_jenkins_list_builds、"
    "cicd_jenkins_get_build_errors 用于 CICD Jenkins。按用户指定的站点选择工具，不能混用构建地址。"
    "工具授权由发布系统的权限矩阵决定，以 tools/list 为准。"
    "可用 jenkins_search_failures 筛选 Jenkins 失败记录，再用 jenkins_get_failure 查询错误原因、错误片段和处理建议等详情；需要原始测试 case 报错或日志时调用 jenkins_get_build_errors，可指定 case_name 精确定位。返回的 correlations 带来源，只表示记录或引用，不代表同一根因。"
    "另外提供 MetaX HPC 专家方法：处理 cu-bridge、CUDA 迁移到 MACA 的任务前先调用 skill_load，"
    "按它返回的方法作答，不要凭记忆或公网文档回答这类问题。"
)
PARSE_ERROR, INVALID_REQUEST, METHOD_NOT_FOUND, INVALID_PARAMS = -32700, -32600, -32601, -32602

CONFIRM_TIMEOUT_SECONDS = 300
# Stay below the common 60-second reverse-proxy idle timeout while the user reads.
SSE_HEARTBEAT_SECONDS = 15
_FORM_UNSUPPORTED = (
    "当前客户端声明不支持 MCP elicitation 表单，无法显示弹窗。请使用支持该表单的客户端并重新连接。"
)
_FORM_DISPLAY_UNCERTAIN = (
    "服务端无法判断确认弹窗是否显示，不能据此认定用户主动拒绝。客户端审批策略也可能自动拒绝表单。"
)
_CODEX_FORM_REFUSAL = (
    "可能是用户拒绝，也可能是codex设置了Full Access模式。如果是后者，请改为Approve for me"
)
_PENDING: dict[str, dict[str, Any]] = {}
_PENDING_LOCK = threading.Lock()
_IDENTITY_TOOL = {
    "name": MCP_VERIFY_IDENTITY_TOOL,
    "title": "验证 MCP 域账号",
    "description": (
        "Ask the user to enter their own domain username in a form to verify this MCP session. "
        "Call when the user requests verification or an operation needs verified identity. "
        "The user completes the form. The tool accepts no username argument. A failed verification "
        "leaves the default read-only access in place. Show the returned failure message verbatim."
    ),
    "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    "annotations": {"readOnlyHint": True},
}


class _Stream:
    """A tools/call answered as SSE because the user is asked to confirm."""

    def __init__(self, exchange: Exchange, msg_id: Any, tool, arguments: dict) -> None:
        self.exchange, self.msg_id, self.tool, self.arguments = exchange, msg_id, tool, arguments

    def events(self) -> Iterator[bytes]:
        messages = (self.exchange.call_with_confirmation(self.msg_id, self.tool, self.arguments)
                    if self.tool else self.exchange.verify_identity(self.msg_id))
        for message in messages:
            if message is None:
                yield b": keep-alive\n\n"
            else:
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
        self.access = mcp_access_service.ToolAccess(conn, caller.user)

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
            tools, warning = self.access.list_tools()
            verified = self.session["identity_verified"]
            definitions = [_IDENTITY_TOOL]
            for tool in tools:
                definition = tool.definition()
                if not tool.read_only and not verified:
                    definition["description"] += (
                        f" Locked until this MCP session is verified via {MCP_VERIFY_IDENTITY_TOOL}."
                    )
                definitions.append(definition)
            result = {"tools": definitions, "_meta": {"identity_verified": verified}}
            if warning:
                result["_meta"]["access_warnings"] = [warning]
            return _result(msg_id, result)
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
        arguments = params.get("arguments", {})
        if name == MCP_VERIFY_IDENTITY_TOOL:
            if arguments or not isinstance(arguments, dict):
                return _error(msg_id, INVALID_PARAMS, "验证工具不接受参数，请在弹窗内输入域账号")
            return self._start_identity_verification(msg_id)
        tool = get_tool(name) if isinstance(name, str) else None
        allowed, warning = self.access.check(tool) if tool else (False, "")
        if not allowed:
            if warning:
                return _result(msg_id, {"content": [{"type": "text", "text": warning}], "isError": True})
            return _error(msg_id, INVALID_PARAMS, f"Unknown tool: {name}")
        if not isinstance(arguments, dict):
            return _error(msg_id, INVALID_PARAMS, "arguments must be an object")
        if not tool.read_only and not self.session.get("session_id"):
            return _error(msg_id, INVALID_PARAMS, "写操作需要有效的 MCP 会话，请重新 initialize")
        if not tool.read_only and not self.allow_stream:
            return _error(msg_id, INVALID_PARAMS, "写操作不支持批量请求，请单独调用")
        if not tool.read_only and not self.session["identity_verified"]:
            return _result(msg_id, {
                "content": [{"type": "text", "text": f"请先调用 {MCP_VERIFY_IDENTITY_TOOL} 验证域账号。"}],
                "isError": True,
                "structuredContent": {"status": "identity_required"},
            })
        if tool.requires_confirmation and not self.session.get("can_confirm"):
            ctx = ToolContext(self.caller, self.session, self.base_url)
            message = f"执行「{tool.title}」需要表单确认，未执行。{_FORM_UNSUPPORTED}"
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
                self.caller, ctx.session = mcp_service.refresh_session(self.caller, self.session["session_id"])
                ctx.caller = self.caller
                ctx.jira_write_all = mcp_access_service.require_tool_access(
                    ctx.user, tool.name, identity_verified=ctx.session["identity_verified"],
                )
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

    def call_with_confirmation(self, msg_id: Any, tool, arguments: dict) -> Iterator[dict | None]:
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
        schema = {
            "type": "object",
            "properties": {"confirm": {"type": "boolean", "title": "确认执行", "default": False}},
            "required": ["confirm"],
        }
        action, content = yield from self._ask(message, schema)
        if action == "timeout":
            outcome, text = "timeout", "等待用户确认超时，未执行。"
        elif action != "accept":
            outcome = action or "no_answer"
            text = ("客户端未返回有效的确认，未执行。" if not action else
                    "客户端返回拒绝或取消，未执行。")
            if action in ("decline", "cancel") and self._is_codex_client():
                text = f"客户端拒绝或取消，未执行。{_CODEX_FORM_REFUSAL}，或手动评论。"
            else:
                text += _FORM_DISPLAY_UNCERTAIN
        elif content.get("confirm") is not True:
            outcome, text = "not_confirmed", "没有收到明确的执行确认，未执行。"
        else:
            yield self._run(msg_id, tool, arguments, confirmed="approved")
            return
        refused = ToolResult.text(
            text + "请向用户说明原因，不要自动重试或替用户确认。仅在用户明确要求重新确认后再调用。",
            log=ctx.log,
        )
        refused.is_error = True
        self._record(tool, arguments, refused, ctx, error=text, confirmed=outcome, started=started)
        yield _result(msg_id, {"content": refused.content, "isError": True,
                              "structuredContent": {"status": "not_executed", "reason": outcome,
                                                    "retryable": False}})

    def _start_identity_verification(self, msg_id: Any) -> dict | _Stream:
        if not self.session["session_id"] or not self.allow_stream:
            return _error(msg_id, INVALID_PARAMS, "域账号验证需要有效的 MCP 会话，且须单独调用")
        if self.session["identity_verified"]:
            return self._identity_result(msg_id, "verified", "本 MCP 会话已验证域账号")
        if self.caller.user.get("auth_source") != "ldap":
            return self._identity_result(msg_id, "domain_account_required", "请用域账号配置个人 token")
        if not self.session["can_confirm"]:
            return self._identity_result(
                msg_id, "client_cannot_confirm", f"域账号验证需要表单确认，未执行。{_FORM_UNSUPPORTED}",
            )
        return _Stream(self, msg_id, None, {})

    def _is_codex_client(self) -> bool:
        return "codex" in self.session["client_name"].casefold()

    def verify_identity(self, msg_id: Any) -> Iterator[dict | None]:
        schema = {
            "type": "object", "required": ["username"],
            "properties": {"username": {"type": "string", "title": "本人域账号用户名"}},
        }
        action, content = yield from self._ask("请输入本人域账号用户名，验证当前 MCP 会话。", schema)
        if action != "accept":
            if action == "timeout":
                text = "等待域账号验证超时"
            elif action in ("decline", "cancel"):
                response = "拒绝" if action == "decline" else "取消"
                guidance = (
                    f"{_CODEX_FORM_REFUSAL}，再重新发起验证。"
                    if self._is_codex_client() else _FORM_DISPLAY_UNCERTAIN
                )
                text = f"客户端返回{response}，域账号验证未完成。{guidance}"
            else:
                text = f"客户端未返回有效的域账号验证答复。{_FORM_DISPLAY_UNCERTAIN}"
            yield self._identity_result(msg_id, action or "no_answer", text)
            return
        username = content.get("username")
        if not isinstance(username, str) or username.strip().casefold() != self.caller.user["username"].casefold():
            yield self._identity_result(msg_id, "username_mismatch", "用户名为空或与个人 token 所属账号不匹配")
            return
        try:
            mcp_service.verify_session_identity(self.caller, self.session["session_id"])
        except ApiError as exc:
            yield self._identity_result(msg_id, "session_invalid", str(exc))
            return
        yield self._identity_result(msg_id, "verified", "当前 MCP 会话已验证域账号，工具和资源权限仍按实时授权执行")

    def _identity_result(self, msg_id: Any, outcome: str, text: str) -> dict:
        verified = outcome == "verified"
        mcp_service.record(caller=self.caller, session=self.session, kind="tool", tool=MCP_VERIFY_IDENTITY_TOOL,
                           category="account", status="ok" if verified else "error", extra={"outcome": outcome})
        if not verified:
            text = f"{text.removesuffix('。')}。可继续使用默认只读权限，JIRA 限 {DEFAULT_JIRA_PROJECT}。"
        return _result(msg_id, {"content": [{"type": "text", "text": text}], "isError": not verified,
                                "structuredContent": {"identity_verified": verified, "reason": outcome}})

    def _ask(self, message: str, schema: dict) -> Generator[dict | None, None, tuple[str, dict]]:
        request_id = f"confirm-{uuid.uuid4().hex}"
        waiter = {"event": threading.Event(), "reply": None, "token_id": self.caller.token_id,
                  "session_id": self.session["session_id"]}
        with _PENDING_LOCK:
            _PENDING[request_id] = waiter
        try:
            yield {"jsonrpc": "2.0", "id": request_id, "method": "elicitation/create",
                   "params": {"message": message, "requestedSchema": schema}}
            deadline = time.monotonic() + CONFIRM_TIMEOUT_SECONDS
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return "timeout", {}
                if waiter["event"].wait(min(SSE_HEARTBEAT_SECONDS, remaining)):
                    break
                if time.monotonic() < deadline:
                    yield None
            reply = waiter["reply"].get("result")
        finally:
            with _PENDING_LOCK:
                _PENDING.pop(request_id, None)
        if not isinstance(reply, dict):
            return "", {}
        action = reply.get("action")
        content = reply.get("content")
        return action if action in ("accept", "decline", "cancel") else "", content if isinstance(content, dict) else {}


def handle_payload(conn, caller: McpCaller, session_id: str, base_url: str, payload: Any) -> tuple[Any, str]:
    exchange = Exchange(conn, caller, session_id, base_url)
    if isinstance(payload, list):
        if not payload:
            return _error(None, INVALID_REQUEST, "Empty batch"), ""
        exchange.allow_stream = False
        replies = [reply for reply in (exchange.handle(item) for item in payload) if reply is not None]
        return (replies or None), exchange.new_session_id
    return exchange.handle(payload), exchange.new_session_id
