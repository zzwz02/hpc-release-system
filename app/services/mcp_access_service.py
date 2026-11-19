"""Manage the MCP tool matrix and resolve live access for one request."""
from __future__ import annotations

import sqlite3
from contextlib import closing

from app.api.errors import ApiError
from app.config import settings
from app.db.connection import connect, transaction
from app.domain.mcp_access import MCP_LDAP_PATTERNS, MCP_ROLES
from app.integrations import ldap
from app.repositories import audit_repo, mcp_repo
from app.services.mcp_tools import Tool, get_tool, registered_tools
from app.timeutil import beijing_timestamp


def _ldap_groups(conn: sqlite3.Connection, user: dict) -> tuple[tuple[str, ...], str]:
    # A local account must never inherit the domain account with the same name.
    row = conn.execute("SELECT auth_source FROM users WHERE username = ?", (user["username"],)).fetchone()
    if not row or row["auth_source"] != "ldap":
        return (), ""
    try:
        groups = ldap.groups_for_user(user["username"], ldap_config=ldap.current_ldap_config())
        return tuple(groups), ""
    except Exception:
        # LDAP exception text can include bind details; do not return or log it.
        return (), "无法查询 LDAP 域组，依赖域组授权的 MCP 权限暂不可用。请检查服务器 LDAP 配置及连接。"


class ToolAccess:
    def __init__(self, conn: sqlite3.Connection, user: dict):
        self.conn = conn
        self.user = user
        _, self.grants = mcp_repo.read_tool_permissions(conn)
        self._membership: tuple[tuple[str, ...], str] | None = None

    def check(self, tool: Tool) -> tuple[bool, str]:
        role = self.user["role"]
        if role not in MCP_ROLES or not tool.enabled:
            return False, ""
        selected = self.grants.get(tool.name, tool.default_grants())
        if role in selected:
            return True, ""
        if not any(pattern in selected for pattern in MCP_LDAP_PATTERNS):
            return False, ""
        if self._membership is None:
            self._membership = _ldap_groups(self.conn, self.user)
        groups, warning = self._membership
        return tool.allowed(role, groups=groups, grants=selected), warning

    def list_tools(self) -> tuple[list[Tool], str]:
        tools = []
        warning = ""
        for tool in registered_tools():
            allowed, lookup_warning = self.check(tool)
            if allowed:
                tools.append(tool)
            warning = warning or lookup_warning
        return tools, warning


def require_tool_access(user: dict, name: str) -> None:
    """Recheck access when a confirmation or attachment ticket is used."""
    tool = get_tool(name)
    if tool:
        with closing(connect(settings.db_path)) as conn:
            allowed, warning = ToolAccess(conn, user).check(tool)
        if allowed:
            return
        if warning:
            raise ApiError(403, warning)
    raise ApiError(403, f"当前账号无权调用 MCP 工具 {name}")


def _permission_matrix(revision: int, grants: dict[str, list[str]]) -> dict:
    return {
        "revision": revision,
        "columns": [
            *({"id": role, "label": role, "kind": "role"} for role in MCP_ROLES),
            *({"id": pattern, "label": pattern.removesuffix("*"), "kind": "group"}
              for pattern in MCP_LDAP_PATTERNS),
        ],
        "tools": [
            {
                "name": tool.name, "title": tool.title, "category": tool.category,
                "jenkins_site": tool.jenkins_site,
                "read_only": tool.read_only, "enabled": tool.enabled,
                "grants": list(grants.get(tool.name, tool.default_grants())),
            }
            for tool in sorted(
                registered_tools(), key=lambda item: (item.category, item.jenkins_site != "pde", item.jenkins_site),
            )
        ],
    }


def permission_matrix(conn: sqlite3.Connection) -> dict:
    return _permission_matrix(*mcp_repo.read_tool_permissions(conn))


def save_permissions(conn: sqlite3.Connection, user: dict, revision: int, changes: dict[str, list[str]]) -> dict:
    principals = MCP_ROLES + MCP_LDAP_PATTERNS
    tools = {tool.name: tool for tool in registered_tools()}
    for name, selected in changes.items():
        if name not in tools:
            raise ApiError(400, f"未知 MCP 工具 {name}")
        if len(set(selected)) != len(selected) or not set(selected).issubset(principals):
            raise ApiError(400, f"MCP 工具 {name} 包含无效或重复的授权对象")

    with transaction(conn, immediate=True):
        current_revision, grants = mcp_repo.read_tool_permissions(conn)
        if revision != current_revision:
            raise ApiError(409, "权限配置已被其他人更新，请重新读取后再保存")
        audit_changes = []
        for name, selected in changes.items():
            before = list(grants.get(name, tools[name].default_grants()))
            if set(selected) != set(before):
                after = [principal for principal in principals if principal in selected]
                grants[name] = after
                audit_changes.append({"tool": name, "before": before, "after": after})
        if audit_changes:
            current_revision += 1
            mcp_repo.write_tool_permissions(conn, current_revision, grants)
            audit_repo.log_audit(
                conn, "更新 MCP 工具权限", ts=beijing_timestamp(), user=user["username"],
                role=user["role"], event="mcp_tool_permissions", detail=audit_changes,
            )
    return _permission_matrix(current_revision, grants)
