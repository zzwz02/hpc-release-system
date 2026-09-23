"""Jira integration — issue creation for CICD dispatch (stdlib only).

Called after transaction commit (in thread pool, failure does not roll back).
Used when RM approves with approval_mode='dispatch_spd' and jira_auto_created=1.

Config is the [jira] section of release_system.conf (app/runtime_config.py).
"""
from __future__ import annotations

import json
import logging
import sqlite3
import urllib.error
import urllib.request
from datetime import date, timedelta
from urllib.parse import quote

from app import runtime_config
from app.domain.cicd_config import CICD_PAYLOAD_CONFIG_LABELS

logger = logging.getLogger(__name__)

# CICD dispatch tickets always go to this project and component on
# jira.metax-tech.com; issue types and ETA field ids are that instance's.
DISPATCH_PROJECT = "SPD"
DISPATCH_COMPONENT = "SPD_CICD"
DISPATCH_ISSUE_TYPE = "Task"
DISPATCH_SUBTASK_TYPE = "Sub-task"
EXPECTED_ETA_FIELD = "customfield_10115"
ESTIMATED_ETA_FIELD = "customfield_10116"


# ─────────────────────────────────────────────────────────────
# Config
# ─────────────────────────────────────────────────────────────

def _read_config() -> dict:
    """Read non-secret and secret Jira settings without validating use cases."""
    cfg = runtime_config.section("jira")
    if cfg.get("JIRA_BASE_URL"):
        cfg["JIRA_BASE_URL"] = cfg["JIRA_BASE_URL"].rstrip("/")
    return cfg


def load_config() -> dict | None:
    """Load the [jira] section.  Returns None if required keys are absent."""
    cfg = _read_config()
    if not cfg.get("JIRA_BASE_URL") or not cfg.get("JIRA_TOKEN"):
        return None
    return cfg


def browse_url() -> str:
    """Return the safe, browser-facing Jira issue prefix (never a token)."""
    base_url = str(_read_config().get("JIRA_BASE_URL") or "").strip()
    return f"{base_url}/browse/" if base_url else ""


# ─────────────────────────────────────────────────────────────
# HTTP
# ─────────────────────────────────────────────────────────────

def _request(base_url: str, token: str, method: str, path: str,
             body: dict | None = None) -> dict:
    url = base_url + path
    data = json.dumps(body, ensure_ascii=False).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Content-Type", "application/json; charset=utf-8")
    req.add_header("Accept", "application/json")
    with urllib.request.urlopen(req, timeout=20) as resp:
        raw = resp.read()
        return json.loads(raw) if raw else {}


# ─────────────────────────────────────────────────────────────
# Issue title helper (mirrors JS cicdJiraTitle)
# ─────────────────────────────────────────────────────────────

def compute_title(
    conn: sqlite3.Connection,
    request_type: str,
    payload: dict,
    task_id: str | None,
) -> str:
    """Compute the Jira issue title for a CICD request.

    Mirrors the JS function cicdJiraTitle() in index.html.
    """
    app_name = payload.get("app_name", "")
    if not app_name and task_id:
        row = conn.execute("SELECT app_name FROM cicd_tasks WHERE id=?", (task_id,)).fetchone()
        if row:
            app_name = row[0]
    if request_type == "create":
        exists = conn.execute(
            "SELECT 1 FROM cicd_tasks WHERE app_name=?", (app_name,)
        ).fetchone()
        return (
            f"[Append] {app_name} 【追加发布新版本】" if exists
            else f"[New] {app_name} 【新发布项目】"
        )
    return f"[Change] {app_name} 【修改项目】"


# ─────────────────────────────────────────────────────────────
# Description builder (Jira wiki markup)
# ─────────────────────────────────────────────────────────────

_FIELD_LABEL: dict[str, str] = {
    "app_name":        "应用名称",
    "app_version":     "应用版本",
    "repo_url":        "代码仓库",
    "repo_name":       "Gerrit 路径",
    "branch":          "分支",
    "release_decision": "Release 决策",
    "build_product":   "构建产物",
    "pipeline_url":    "流水线地址",
    "build_cmd":       "构建命令",
    "deploy_cmd":      "部署命令",
    "rollback_cmd":    "回滚命令",
    "test_cmd":        "测试命令",
    "env":             "环境",
    "owner_username":  "负责人",
    "qa_username":     "QA负责人",
    "status":          "状态",
    "description":     "描述",
    "jira_id":         "Jira",
    **CICD_PAYLOAD_CONFIG_LABELS,
}

_REQ_TYPE_LABEL: dict[str, str] = {
    "create":         "新建任务",
    "modify":         "修改任务",
    "owner_transfer": "负责人变更",
}


def build_description(
    *,
    request_id: int | None,
    request_type: str,
    payload: dict,
    task_id: str | None,
    submitter: str,
    title: str,
    review_note: str = "",
) -> str:
    """Build a Jira wiki-markup description string from a CICD request.

    Compatible with Jira Server / DC wiki markup (also renders reasonably
    on Cloud as plain text fallback).
    """
    lines: list[str] = []
    lines.append("由 CICD 发布系统自动创建（审批模式：下发给 SPD 执行交付）。")
    lines.append("")

    # ── 基本信息 ──
    lines.append("h3. 基本信息")
    lines.append("||字段||值||")
    if request_id is not None:
        lines.append(f"|申请 ID|#{request_id}|")
    lines.append(f"|类型|{_REQ_TYPE_LABEL.get(request_type, request_type)}|")
    lines.append(f"|申请人|{submitter}|")
    if task_id:
        lines.append(f"|任务 ID|{task_id}|")
    lines.append("")

    # ── 变更详情 ──
    lines.append("h3. 变更详情")
    if request_type == "create":
        lines.append("||字段||值||")
        for k, v in payload.items():
            if k == "app_id" or k.startswith("_"):
                continue
            if v is None or v == "" or v == []:
                continue
            label = _FIELD_LABEL.get(k, k)
            val = ", ".join(v) if isinstance(v, list) else str(v)
            lines.append(f"|{label}|{val}|")
    elif payload:
        lines.append("||字段||原值||新值||")
        for k, ch in payload.items():
            if not isinstance(ch, dict):
                continue
            label = _FIELD_LABEL.get(k, k)
            old_v = ", ".join(ch["old"]) if isinstance(ch.get("old"), list) else str(ch.get("old", ""))
            new_v = ", ".join(ch["new"]) if isinstance(ch.get("new"), list) else str(ch.get("new", ""))
            lines.append(f"|{label}|{old_v}|{new_v}|")
    else:
        lines.append("（无变更详情）")
    lines.append("")

    if review_note and review_note.strip():
        lines.append(f"*审批备注：* {review_note.strip()}")
        lines.append("")

    lines.append("----")
    lines.append(f"摘要：{title}")
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────
# Create issue
# ─────────────────────────────────────────────────────────────

def create_issue(title: str, description: str | None = None, *, jira_config: dict) -> str:
    """Create a Jira issue and return its key (e.g. 'SPD-456').

    Files a DISPATCH_ISSUE_TYPE under DISPATCH_PROJECT / DISPATCH_COMPONENT, or
    a DISPATCH_SUBTASK_TYPE when JIRA_PARENT_ISSUE is set, with both ETA
    fields at today + 2 days.

    Raises urllib.error.HTTPError or RuntimeError on failure — callers must
    catch and log; do NOT let Jira errors roll back a DB transaction.
    """
    cfg = jira_config
    parent_key = cfg.get("JIRA_PARENT_ISSUE", "")
    eta        = (date.today() + timedelta(days=2)).strftime("%Y-%m-%d")

    fields: dict = {
        "project":    {"key": DISPATCH_PROJECT},
        "summary":    title,
        "issuetype":  {"name": DISPATCH_SUBTASK_TYPE if parent_key else DISPATCH_ISSUE_TYPE},
        "components": [{"name": DISPATCH_COMPONENT}],
        EXPECTED_ETA_FIELD:  eta,
        ESTIMATED_ETA_FIELD: eta,
    }
    if parent_key:
        fields["parent"] = {"key": parent_key}
    if cfg.get("JIRA_ASSIGNEE"):
        fields["assignee"] = {"name": cfg["JIRA_ASSIGNEE"]}
    fields["description"] = description or f"由 CICD 发布系统自动创建（审批模式：下发给 SPD 执行交付）。\n\n摘要：{title}"

    result = _request(cfg["JIRA_BASE_URL"], cfg["JIRA_TOKEN"],
                      "POST", "/rest/api/2/issue", {"fields": fields})
    key = result.get("key") or ""
    if not key:
        raise RuntimeError(f"Jira 建单成功但未返回 key: {result}")
    logger.info("Jira issue created: %s — %s", key, title)
    return key


# ─────────────────────────────────────────────────────────────
# Issue snapshot / attachments / comments (JIRA agent)
# ─────────────────────────────────────────────────────────────

_ISSUE_FIELDS = (
    "summary,issuetype,status,priority,project,assignee,reporter,components,"
    "labels,description,attachment,comment,created,updated"
)


def _require_config() -> dict:
    cfg = load_config()
    if cfg is None:
        raise RuntimeError("未配置 JIRA：release_system.conf 的 [jira] 需要 JIRA_BASE_URL 和 JIRA_TOKEN")
    return cfg


def _person(value: dict | None) -> dict | None:
    if not value:
        return None
    name = value.get("name") or value.get("key") or ""
    return {"name": name, "display_name": value.get("displayName") or name}


def get_issue(issue_key: str) -> dict:
    """Return a normalised snapshot of one issue.

    Raises urllib.error.HTTPError (e.g. 404) or URLError on failure.
    """
    cfg = _require_config()
    raw = _request(
        cfg["JIRA_BASE_URL"], cfg["JIRA_TOKEN"], "GET",
        f"/rest/api/2/issue/{quote(issue_key)}?fields={_ISSUE_FIELDS}",
    )
    fields = raw.get("fields") or {}
    key = raw.get("key") or issue_key
    comments = (fields.get("comment") or {}).get("comments") or []
    return {
        "key": key,
        "url": f"{cfg['JIRA_BASE_URL']}/browse/{key}",
        "summary": fields.get("summary") or "",
        "issue_type": (fields.get("issuetype") or {}).get("name", ""),
        "status": (fields.get("status") or {}).get("name", ""),
        "priority": (fields.get("priority") or {}).get("name", ""),
        "project": (fields.get("project") or {}).get("name", ""),
        "assignee": _person(fields.get("assignee")),
        "reporter": _person(fields.get("reporter")),
        "components": [c.get("name", "") for c in fields.get("components") or []],
        "labels": list(fields.get("labels") or []),
        "description": fields.get("description") or "",
        "created": fields.get("created") or "",
        "updated": fields.get("updated") or "",
        "comments": [
            {
                "id": str(c.get("id", "")),
                "author": (_person(c.get("author")) or {}).get("display_name", ""),
                "created": c.get("created") or "",
                "body": c.get("body") or "",
            }
            for c in comments
        ],
        "attachments": [
            {
                "id": str(a.get("id", "")),
                "filename": a.get("filename") or "",
                "size": int(a.get("size") or 0),
                "mime_type": a.get("mimeType") or "",
                "content_url": a.get("content") or "",
            }
            for a in fields.get("attachment") or []
        ],
    }


class JiraQueryError(ValueError):
    """JQL rejected by JIRA (HTTP 400); message carries JIRA's explanation."""


_SEARCH_FIELDS = ["summary", "status", "assignee", "priority", "issuetype", "components", "updated"]


def search_issues(
    jql: str,
    *,
    max_results: int = 50,
    validate: bool = True,
) -> dict:
    """Run a JQL search; returns {"total", "issues": [summary dicts]}.

    validate=False lets `key in (...)` skip keys that no longer exist instead
    of failing the whole query.
    """
    cfg = _require_config()
    try:
        raw = _request(
            cfg["JIRA_BASE_URL"], cfg["JIRA_TOKEN"], "POST", "/rest/api/2/search",
            {"jql": jql, "maxResults": max_results, "fields": _SEARCH_FIELDS, "validateQuery": validate},
        )
    except urllib.error.HTTPError as exc:
        if exc.code != 400:
            raise
        try:
            messages = json.loads(exc.read() or b"{}").get("errorMessages") or []
        except ValueError:
            messages = []
        raise JiraQueryError("；".join(messages) or "JQL 查询无效") from exc
    issues = []
    for item in raw.get("issues") or []:
        fields = item.get("fields") or {}
        issues.append({
            "key": item.get("key") or "",
            "url": f"{cfg['JIRA_BASE_URL']}/browse/{item.get('key') or ''}",
            "summary": fields.get("summary") or "",
            "issue_type": (fields.get("issuetype") or {}).get("name", ""),
            "status": (fields.get("status") or {}).get("name", ""),
            "priority": (fields.get("priority") or {}).get("name", ""),
            "assignee": _person(fields.get("assignee")),
            "components": [c.get("name", "") for c in fields.get("components") or []],
            "updated": fields.get("updated") or "",
        })
    return {"total": int(raw.get("total") or 0), "issues": issues}


def download_attachment(
    content_url: str,
    *,
    max_bytes: int = 50 * 1024 * 1024,
) -> bytes:
    """Download attachment bytes; only URLs under the configured JIRA base."""
    cfg = _require_config()
    if not content_url.startswith(cfg["JIRA_BASE_URL"] + "/"):
        raise ValueError("附件地址不属于配置的 JIRA")
    req = urllib.request.Request(content_url, method="GET")
    req.add_header("Authorization", f"Bearer {cfg['JIRA_TOKEN']}")
    with urllib.request.urlopen(req, timeout=120) as resp:
        data = resp.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise ValueError(f"附件超过 {max_bytes // (1024 * 1024)}MB，未下载")
    return data


def add_comment(issue_key: str, body: str) -> str:
    """Append a comment (wiki markup) and return its id.  Never edits others."""
    cfg = _require_config()
    result = _request(
        cfg["JIRA_BASE_URL"], cfg["JIRA_TOKEN"], "POST",
        f"/rest/api/2/issue/{quote(issue_key)}/comment", {"body": body},
    )
    return str(result.get("id") or "")
