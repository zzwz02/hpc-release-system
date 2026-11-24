"""MCP tool grants and project scope, composed with the shared role matrix."""
from __future__ import annotations

import re

from app.api.errors import ApiError
from app.domain.permissions import CAPABILITY_PERMISSIONS, LDAP_GROUP_GRANTS, has_capability, roles_for_tab

# Default JIRA scope for an MCP session without domain identity confirmation.
DEFAULT_JIRA_PROJECT = "MC3"
MCP_VERIFY_IDENTITY_TOOL = "mcp_verify_identity"
GUEST_JIRA_WRITE_TOOLS = frozenset({"jira_add_comment", "jira_upload_attachment"})
MCP_ROLES = roles_for_tab("mcp")
MCP_LDAP_PATTERNS = tuple(dict.fromkeys(
    pattern for capability, patterns in LDAP_GROUP_GRANTS.items()
    if capability.startswith("mcp.") for pattern in patterns
))
_ORDER_BY = re.compile(r"\border\s+by\b", re.I)
_ORDER_FIELD = r'''(?:[A-Za-z_][A-Za-z0-9_.]*|cf\[\d+\]|"(?:[^"\\]|\\.)+"|'(?:[^'\\]|\\.)+')'''
_ORDER_ITEM = rf"{_ORDER_FIELD}(?:\s+(?:ASC|DESC))?"
_ORDER_FIELDS = re.compile(rf"{_ORDER_ITEM}(?:\s*,\s*{_ORDER_ITEM})*", re.I)


def can_use_tool(role: str, name: str) -> bool:
    """Explicit grants for limited users; future tools are denied by default."""
    return (has_capability(role, "mcp.tools.all")
            or role in CAPABILITY_PERMISSIONS.get(f"mcp.tool.{name}", ()))


def can_use_jenkins(role: str, site: str) -> bool:
    """Default role grants for the two Jenkins sites."""
    capability = f"mcp.jenkins.{site}"
    return role in MCP_ROLES and (
        has_capability(role, "mcp.jenkins.all")
        or role in CAPABILITY_PERMISSIONS.get(capability, ())
    )


def project_key(issue_key: str) -> str:
    return issue_key.upper().rsplit("-", 1)[0]


def scope_jql(jql: str, projects: tuple[str, ...]) -> str:
    """Intersect a whole expression with allowed projects, preserving a terminal ORDER BY.

    JQL is still parsed by JIRA. This lexical pass only establishes a safe
    grouping boundary: quotes/parentheses must balance, and the sort suffix
    cannot contain another condition. See Atlassian's JQL keywords reference:
    https://confluence.atlassian.com/jiracoreserver/advanced-searching-keywords-reference-939937720.html
    """
    if not projects or any(not re.fullmatch(r"[A-Z][A-Z0-9_]*", key) for key in projects):
        raise ApiError(403, "没有可查询的 JIRA 项目")
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
    condition = f'project in ({", ".join(f"{key!r}" for key in projects)})'
    if len(projects) == 1:
        condition = f"project = {projects[0]}"
    return condition + (f" AND ({expression})" if expression else "") + sort
