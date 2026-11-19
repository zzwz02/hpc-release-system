"""MCP tool grants and project scope, composed with the shared role matrix."""
from __future__ import annotations

import re

from app.api.errors import ApiError
from app.domain.permissions import (
    CAPABILITY_PERMISSIONS, LDAP_GROUP_GRANTS, has_capability, ldap_group_matches, roles_for_tab,
)

# Guests may read this JIRA project only. This is a fixed product permission,
# independent of which server hosts the website or the JIRA integration.
LIMITED_JIRA_PROJECT = "MC3"
_ORDER_BY = re.compile(r"\border\s+by\b", re.I)
_ORDER_FIELD = r'''(?:[A-Za-z_][A-Za-z0-9_.]*|cf\[\d+\]|"(?:[^"\\]|\\.)+"|'(?:[^'\\]|\\.)+')'''
_ORDER_ITEM = rf"{_ORDER_FIELD}(?:\s+(?:ASC|DESC))?"
_ORDER_FIELDS = re.compile(rf"{_ORDER_ITEM}(?:\s*,\s*{_ORDER_ITEM})*", re.I)


def can_use_tool(role: str, name: str) -> bool:
    """Explicit grants for limited users; future tools are denied by default."""
    return (has_capability(role, "mcp.tools.all")
            or role in CAPABILITY_PERMISSIONS.get(f"mcp.tool.{name}", ()))


def has_ldap_tool_grant(role: str, capability: str, groups: tuple[str, ...]) -> bool:
    return role in roles_for_tab("mcp") and any(
        ldap_group_matches(group, pattern)
        for group in groups for pattern in LDAP_GROUP_GRANTS.get(capability, ())
    )


def can_use_jenkins(role: str, site: str, groups: tuple[str, ...] = ()) -> bool:
    """Site grants are independent of broad MCP grants and JIRA project scope."""
    capability = f"mcp.jenkins.{site}"
    return role in roles_for_tab("mcp") and (
        has_capability(role, "mcp.jenkins.all")
        or role in CAPABILITY_PERMISSIONS.get(capability, ())
        or has_ldap_tool_grant(role, capability, groups)
    )


def limited_jira_reader(role: str) -> bool:
    return not has_capability(role, "mcp.jira.read.all_projects")


def require_jira_read(role: str, key: str) -> None:
    if limited_jira_reader(role) and not re.fullmatch(rf"{LIMITED_JIRA_PROJECT}-\d+", key):
        raise ApiError(403, f"当前账号仅允许读取 {LIMITED_JIRA_PROJECT} 项目的 JIRA")


def scope_jql(role: str, jql: str) -> str:
    """Intersect a whole expression with MC3, preserving a terminal ORDER BY.

    JQL is still parsed by JIRA. This lexical pass only establishes a safe
    grouping boundary: quotes/parentheses must balance, and the sort suffix
    cannot contain another condition. See Atlassian's JQL keywords reference:
    https://confluence.atlassian.com/jiracoreserver/advanced-searching-keywords-reference-939937720.html
    """
    if not limited_jira_reader(role):
        return jql
    quote, escaped, depth, order = "", False, 0, None
    for index, char in enumerate(jql):
        if quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = ""
            continue
        if char in {"'", '"'}:
            quote = char
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth < 0:
                raise ApiError(400, "JQL 括号不匹配")
        elif char in {";", "\\"} or jql[index:index + 2] in {"--", "/*", "//"}:
            raise ApiError(400, "JQL 包含不支持的语法")
        elif depth == 0 and (index == 0 or not (jql[index-1].isalnum() or jql[index-1] == "_")):
            match = _ORDER_BY.match(jql, index)
            if match:
                if order is not None:
                    raise ApiError(400, "JQL 只能包含一个末尾 ORDER BY")
                order = (index, match.end())
    if quote or depth:
        raise ApiError(400, "JQL 引号或括号不匹配")
    expression, sort = jql.strip(), ""
    if order is not None:
        expression, fields = jql[:order[0]].strip(), jql[order[1]:].strip()
        if not _ORDER_FIELDS.fullmatch(fields):
            raise ApiError(400, "JQL ORDER BY 只能包含排序字段和 ASC/DESC")
        sort = f" ORDER BY {fields}"
    condition = f"project = {LIMITED_JIRA_PROJECT}"
    return condition + (f" AND ({expression})" if expression else "") + sort
