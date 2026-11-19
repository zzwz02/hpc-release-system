"""Resolve live LDAP membership for one MCP request, without trusting login audits."""
from __future__ import annotations

import sqlite3

from app.domain.permissions import has_capability
from app.integrations import ldap


def jenkins_groups(conn: sqlite3.Connection, user: dict) -> tuple[tuple[str, ...], str]:
    # RM has every Jenkins site grant, including when LDAP is unavailable.
    if has_capability(user["role"], "mcp.jenkins.all"):
        return (), ""
    # A local account must never inherit the domain account with the same name.
    row = conn.execute("SELECT auth_source FROM users WHERE username = ?", (user["username"],)).fetchone()
    if not row or row["auth_source"] != "ldap":
        return (), ""
    try:
        groups = ldap.groups_for_user(user["username"], ldap_config=ldap.current_ldap_config())
        return tuple(groups), ""
    except Exception:
        # LDAP exception text can include bind details; do not return or log it.
        return (), "无法查询 LDAP 域组，依赖域组的 Jenkins 权限暂不可用；请检查服务器 LDAP 配置及连接。"
