"""Resolve Jira visibility for one MCP request using the session's identity."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from app.api.errors import ApiError
from app.domain.mcp_access import DEFAULT_JIRA_PROJECT, MCP_VERIFY_IDENTITY_TOOL, project_key
from app.integrations import jira

# Bound the number of permission requests to Jira during a cross-project search.
PROJECT_CHECK_WORKERS = 8


class JiraReadAccess:
    def __init__(self, username: str, identity_verified: bool):
        self.username = username
        self.verified = identity_verified
        self._project_allowed: dict[str, bool] = {}
        self._issue_allowed: set[str] = set()

    def _browse(self, *, project: str = "", issue: str = "") -> bool:
        try:
            return jira.account_can_browse(self.username, project_key=project, issue_key=issue)
        except (RuntimeError, ValueError, OSError) as exc:
            raise ApiError(
                502,
                "暂时无法核验域账号的 JIRA 浏览权限，请联系管理员检查 JIRA 接入",
            ) from exc

    def projects(self) -> tuple[str, ...]:
        if not self.verified:
            return (DEFAULT_JIRA_PROJECT,)
        try:
            candidates = jira.visible_project_keys()
        except (RuntimeError, ValueError, OSError) as exc:
            raise ApiError(502, "暂时无法读取 JIRA 项目列表") from exc
        if candidates:
            with ThreadPoolExecutor(max_workers=PROJECT_CHECK_WORKERS) as workers:
                allowed = workers.map(self._can_browse_project, candidates)
                self._project_allowed.update(zip(candidates, allowed, strict=True))
        return tuple(key for key in candidates if self._project_allowed[key])

    def _can_browse_project(self, key: str) -> bool:
        return self._browse(project=key)

    def require_issue(self, key: str) -> None:
        project = project_key(key)
        if not self.verified:
            if project != DEFAULT_JIRA_PROJECT:
                raise ApiError(
                    403,
                    f"默认权限仅允许读取 {DEFAULT_JIRA_PROJECT} 项目的 JIRA。"
                    f"请先运行 {MCP_VERIFY_IDENTITY_TOOL} 验证域账号。",
                )
            return
        if project not in self._project_allowed:
            self._project_allowed[project] = self._can_browse_project(project)
        if not self._project_allowed[project]:
            raise ApiError(403, f"当前域账号无权浏览 JIRA 项目 {project}")
        if key not in self._issue_allowed:
            if not self._browse(issue=key):
                raise ApiError(403, f"JIRA 工单 {key} 不存在或当前域账号无权浏览")
            self._issue_allowed.add(key)
