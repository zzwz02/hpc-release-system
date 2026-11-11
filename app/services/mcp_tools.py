"""MCP tool registry — the extension point for new MCP features.

Adding a feature = one module that calls ``register(Tool(...))`` and is
imported in ``load_tools()``.  The protocol layer (mcp_protocol.py) lists and
calls tools from this registry and writes one ``mcp_call_logs`` row per call,
so a new tool gets connection auth, permission checks, logging and the
statistics page without touching them:

  * ``category``  groups the tool on the statistics page (jira, ...)
  * ``roles``     extra role limit on top of the MCP tab permission
  * ``handler``   returns ToolResult; ``ToolResult.log`` says what the call
                  acted on (target) and any tool-specific fields (extra)
  * ``log_args``  optional redaction of arguments before they are stored
  * ``read_only`` / ``destructive`` determine confirmation policy: only a
                  destructive write requires a matching typed username.
  * ``confirm``   optional custom preview; omitting it uses the generic preview
                  and never disables confirmation for a destructive write.
  * ``prepare``   optional pre-confirmation normalization; its returned arguments
                  are used by both the confirmation preview and the handler.

Handlers are synchronous and run in a worker thread.  Raise ToolError for a
user-facing failure; it becomes an ``isError`` result and an error log row.
"""
from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

# Stored argument values are cut to this many characters each, so a pasted
# log or long comment cannot bloat the log table.
LOG_ARG_MAX_CHARS = 500


class ToolError(Exception):
    """A failure the calling agent should read and may act on."""


@dataclass
class ToolLog:
    """What a call acted on, for the log row and the statistics page."""

    target_type: str = ""
    target: str = ""
    summary: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolResult:
    content: list[dict[str, Any]]
    log: ToolLog = field(default_factory=ToolLog)
    is_error: bool = False

    @classmethod
    def text(cls, *texts: str, log: ToolLog | None = None) -> ToolResult:
        return cls([{"type": "text", "text": text} for text in texts], log or ToolLog())


@dataclass
class ToolContext:
    """Who is calling and from where; handed to every handler."""

    caller: Any  # mcp_service.McpCaller (token id + user)
    session: dict[str, Any]  # session_id / client_name / client_version
    base_url: str
    # Filled by the handler when it fails after identifying its target, so the
    # error row still records what the call was about.
    log: ToolLog = field(default_factory=ToolLog)

    @property
    def user(self) -> dict[str, Any]:
        return self.caller.user

    @property
    def client_name(self) -> str:
        return self.session.get("client_name") or "MCP 客户端"

    @property
    def actor(self) -> str:
        name = self.user.get("display_name") or self.user.get("username", "")
        return f"{name}（{self.user.get('username', '')}）" if name != self.user.get("username") else name


@dataclass(frozen=True)
class Tool:
    name: str
    category: str
    title: str
    description: str
    input_schema: dict[str, Any]
    handler: Callable[[ToolContext, dict[str, Any]], ToolResult]
    roles: tuple[str, ...] = ()
    read_only: bool = True
    destructive: bool = True          # only meaningful for write tools
    log_args: Callable[[dict[str, Any]], dict[str, Any]] | None = None
    confirm: Callable[[ToolContext, dict[str, Any]], str] | None = None
    prepare: Callable[[ToolContext, dict[str, Any]], dict[str, Any]] | None = None

    @property
    def requires_confirmation(self) -> bool:
        return not self.read_only and self.destructive

    def confirmation_message(self, ctx: ToolContext, arguments: dict[str, Any]) -> str:
        if self.confirm:
            return self.confirm(ctx, arguments)
        return (f"即将执行「{self.title}」（{self.name}）。"
                "请输入你本人的域账号用户名，匹配账号后执行。\n\n"
                f"操作参数：\n{json.dumps(arguments, ensure_ascii=False, indent=2)}")

    def definition(self) -> dict[str, Any]:
        annotations: dict[str, Any] = {"title": self.title, "readOnlyHint": self.read_only}
        if not self.read_only:
            annotations["destructiveHint"] = self.destructive
        return {
            "name": self.name,
            "title": self.title,
            "description": self.description,
            "inputSchema": self.input_schema,
            "annotations": annotations,
        }

    def allowed(self, role: str) -> bool:
        return not self.roles or role in self.roles

    def logged_args(self, arguments: dict[str, Any]) -> str:
        values = self.log_args(arguments) if self.log_args else arguments
        trimmed = {
            key: (value[:LOG_ARG_MAX_CHARS] + "…" if isinstance(value, str) and len(value) > LOG_ARG_MAX_CHARS else value)
            for key, value in values.items()
        }
        return json.dumps(trimmed, ensure_ascii=False, sort_keys=True)


_REGISTRY: dict[str, Tool] = {}


def register(tool: Tool) -> Tool:
    if tool.name in _REGISTRY and _REGISTRY[tool.name] is not tool:
        raise RuntimeError(f"MCP tool {tool.name} registered twice")
    _REGISTRY[tool.name] = tool
    return tool


def load_tools() -> None:
    """Import every feature module so its tools register (idempotent)."""
    from app.services import mcp_jenkins_tools, mcp_jira_tools, mcp_skill_tools  # noqa: F401


def tools_for(role: str) -> list[Tool]:
    load_tools()
    return [tool for tool in _REGISTRY.values() if tool.allowed(role)]


def get_tool(name: str) -> Tool | None:
    load_tools()
    return _REGISTRY.get(name)
