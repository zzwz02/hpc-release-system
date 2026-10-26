"""Jira integration — issue creation for CICD dispatch (stdlib only).

Called after transaction commit (in thread pool, failure does not roll back).
Used when RM approves with approval_mode='dispatch_spd' and jira_auto_created=1.

Config is the [jira] section of release_system.conf (app/runtime_config.py).
"""
from __future__ import annotations

import json
import logging
import sqlite3
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone
from urllib.parse import quote

from app import runtime_config
from app.config import settings
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
_CICD_FIRST_ACTION_FIELD = "_cicd_first_action"
_CICD_FIRST_ACTION_CREATED = "created"
_CICD_FIRST_ACTION_ASSOCIATED = "associated"


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


def owner_qa_reporter_group() -> str:
    return str(_read_config().get("JIRA_AGENT_OWNER_QA_REPORTER_GROUP") or "").strip() or "dl.sw_qa"


def browse_url() -> str:
    """Return the safe, browser-facing Jira issue prefix (never a token)."""
    base_url = str(_read_config().get("JIRA_BASE_URL") or "").strip()
    return f"{base_url}/browse/" if base_url else ""


# ─────────────────────────────────────────────────────────────
# HTTP
# ─────────────────────────────────────────────────────────────

def _request(base_url: str, token: str, method: str, path: str,
             body: dict | None = None, *, timeout: float = 20,
             max_bytes: int | None = None) -> dict:
    url = base_url + path
    data = json.dumps(body, ensure_ascii=False).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Content-Type", "application/json; charset=utf-8")
    req.add_header("Accept", "application/json")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read(max_bytes + 1) if max_bytes is not None else resp.read()
        if max_bytes is not None and len(raw) > max_bytes:
            raise ValueError("Jira response exceeds snapshot size budget")
        return json.loads(raw) if raw else {}


# ─────────────────────────────────────────────────────────────
# Issue title helper
# ─────────────────────────────────────────────────────────────

def _change_side(payload: dict, field: str, side: str) -> str:
    value = payload.get(field)
    if not isinstance(value, dict):
        return ""
    return str(value.get(side) or "").strip()


def _is_stop_modify_request(request_type: str, payload: dict) -> bool:
    return (
        request_type == "modify"
        and _change_side(payload, "status", "old") == "Running"
        and _change_side(payload, "status", "new") == "Stopped"
    )


def _legacy_task_exists_for_app(conn: sqlite3.Connection, app_name: str) -> bool:
    if not app_name:
        return False
    return conn.execute(
        "SELECT 1 FROM cicd_tasks WHERE app_name=?",
        (app_name,),
    ).fetchone() is not None


def _is_append_create(
    conn: sqlite3.Connection,
    payload: dict,
    app_name: str,
    task_context: dict | None,
) -> bool:
    first_action = str(payload.get(_CICD_FIRST_ACTION_FIELD) or "").strip()
    if first_action == _CICD_FIRST_ACTION_ASSOCIATED:
        return True
    if first_action == _CICD_FIRST_ACTION_CREATED:
        return False

    context_name = str((task_context or {}).get("app_name") or "").strip()
    if context_name and (not app_name or context_name == app_name):
        return True

    return _legacy_task_exists_for_app(conn, app_name)


def compute_title(
    conn: sqlite3.Connection,
    request_type: str,
    payload: dict,
    task_id: str | None,
    task_context: dict | None = None,
) -> str:
    """Compute the Jira issue title for a CICD request.

    Kept aligned with the CICD approval dialog preview.
    """
    app_name = payload.get("app_name", "") or (task_context or {}).get("app_name", "")
    if not app_name and task_id:
        row = conn.execute("SELECT app_name FROM cicd_tasks WHERE id=?", (task_id,)).fetchone()
        if row:
            app_name = row[0]
    app_name = str(app_name or "").strip()
    if _is_stop_modify_request(request_type, payload):
        branch = str((task_context or {}).get("branch") or "").strip()
        if branch:
            return f"[Stop] {app_name} 【停止发布 {branch} 分支】"
        return f"[Stop] {app_name} 【停止发布】"
    if request_type == "create":
        if _is_append_create(conn, payload, app_name, task_context):
            return f"[Append] {app_name} 【追加发布新版本】"
        return f"[New] {app_name} 【新发布项目】"
    return f"[Change] {app_name} 【修改项目】"


# ─────────────────────────────────────────────────────────────
# Description builder (Jira wiki markup)
# ─────────────────────────────────────────────────────────────

_FIELD_LABEL: dict[str, str] = {
    "app_name":        "应用名称",
    "app_version":     "应用版本",
    "repo_url":        "代码仓库",
    "repo_name":       "仓库名",
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


def _stringify_value(value: object) -> str:
    if isinstance(value, list):
        return ", ".join(str(item) for item in value)
    return str(value or "")


def _payload_context_value(payload: dict, key: str) -> str:
    value = payload.get(key)
    if isinstance(value, dict):
        return _stringify_value(value.get("old") or value.get("new") or "")
    return _stringify_value(value)


def _strip_known_prefix(value: str, prefix: str) -> str | None:
    prefix = prefix.rstrip("/")
    if value == prefix:
        return ""
    with_slash = f"{prefix}/"
    if value.startswith(with_slash):
        return value[len(with_slash):]
    return None


def _format_repo_path(repo_name: str, repo_type: str = "") -> str:
    value = repo_name.strip()
    if not value:
        return ""
    hpc_project = settings.gerrit_hpc_project.strip("/")
    manifest_project = settings.gerrit_manifest_project.strip("/")
    ssh_base = settings.gerrit_ssh_base_url.rstrip("/")
    hpc_base = f"{ssh_base}/{hpc_project}"
    manifest_repo_url = f"{hpc_base}/{manifest_project}"

    manifest_relative = _strip_known_prefix(value, manifest_repo_url)
    if manifest_relative is not None:
        return manifest_relative.lstrip("/")

    marker = f"/{hpc_project}/"
    index = value.find(marker)
    hpc_path = value[index + len(marker):] if index >= 0 else value
    manifest_prefix = f"{manifest_project}/"
    if (
        (repo_type == "repo" or hpc_path.endswith(".xml"))
        and hpc_path.startswith(manifest_prefix)
    ):
        return hpc_path[len(manifest_prefix):]
    return hpc_path


def _basic_task_context(payload: dict, task_context: dict | None) -> dict[str, str]:
    context: dict[str, str] = {}
    repo_type = (
        _stringify_value((task_context or {}).get("repo_type", ""))
        or _payload_context_value(payload, "repo_type")
    )
    for key in ("app_name", "app_version", "repo_name", "branch"):
        value = _stringify_value((task_context or {}).get(key, ""))
        if not value:
            value = _payload_context_value(payload, key)
        if key == "repo_name":
            value = _format_repo_path(value, repo_type)
        if value:
            context[key] = value
    return context


def _ordered_change_items(payload: dict) -> list[tuple[str, object]]:
    priority = ("repo_name", "branch")
    ordered: list[tuple[str, object]] = []
    seen: set[str] = set()
    for key in priority:
        if key in payload:
            ordered.append((key, payload[key]))
            seen.add(key)
    ordered.extend((key, value) for key, value in payload.items() if key not in seen)
    return ordered


def _field_value(field: str, value: object, repo_type: str = "") -> str:
    text = _stringify_value(value)
    if field == "repo_name":
        return _format_repo_path(text, repo_type)
    return text


def build_description(
    *,
    request_id: int | None,
    request_type: str,
    payload: dict,
    task_id: str | None,
    submitter: str,
    title: str,
    review_note: str = "",
    task_context: dict | None = None,
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
    context = _basic_task_context(payload, task_context)
    if context.get("app_name"):
        lines.append(f"|项目名称|{context['app_name']}|")
    if context.get("app_version"):
        lines.append(f"|版本|{context['app_version']}|")
    if context.get("repo_name"):
        lines.append(f"|仓库名|{context['repo_name']}|")
    if context.get("branch"):
        lines.append(f"|分支|{context['branch']}|")
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
            val = _stringify_value(v)
            lines.append(f"|{label}|{val}|")
    elif payload:
        lines.append("||字段||原值||新值||")
        repo_type = (
            _stringify_value((task_context or {}).get("repo_type", ""))
            or _payload_context_value(payload, "repo_type")
        )
        for k, ch in _ordered_change_items(payload):
            if not isinstance(ch, dict):
                continue
            label = _FIELD_LABEL.get(k, k)
            old_v = _field_value(k, ch.get("old"), repo_type)
            new_v = _field_value(k, ch.get("new"), repo_type)
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
    return _issue_data(raw, cfg["JIRA_BASE_URL"], issue_key)


def _issue_data(raw: dict, base_url: str, issue_key: str) -> dict:
    fields = raw.get("fields") or {}
    key = raw.get("key") or issue_key
    comments = (fields.get("comment") or {}).get("comments") or []
    return {
        "key": key,
        "url": f"{base_url}/browse/{key}",
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


def get_issue_snapshot(issue_key: str, *, timeout_seconds: float = 120) -> dict:
    """Collect visible fields and all comment pages for one agent turn.

    Unlike get_issue (also used for permission checks), this performs bounded
    collection and reports partial coverage instead of silently dropping data.
    """
    cfg = _require_config()
    started = datetime.now(timezone.utc).isoformat()
    deadline = time.monotonic() + min(120, max(0, timeout_seconds))

    def fetch(path: str):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("Snapshot collection deadline exceeded")
        return _request(cfg["JIRA_BASE_URL"], cfg["JIRA_TOKEN"], "GET", path,
                        timeout=min(20, remaining), max_bytes=8 * 1024 * 1024)

    class Incomplete(ValueError):
        pass

    def failure(exc: Exception) -> str:
        if isinstance(exc, Incomplete):
            return str(exc)
        if isinstance(exc, urllib.error.HTTPError):
            return f"http_{exc.code}"
        if isinstance(exc, TimeoutError):
            return "collection_timeout"
        return "invalid_response" if isinstance(exc, (ValueError, TypeError)) else "request_failed"

    raw = fetch(f"/rest/api/2/issue/{quote(issue_key, safe='')}")
    if not isinstance(raw, dict) or raw.get("key") != issue_key or not isinstance(raw.get("fields"), dict):
        raise ValueError("Jira snapshot identity or fields are invalid")
    fields = raw["fields"]
    parts = {"issue": {"status": "complete"},
             "description": {"status": "complete" if "description" in fields else "unavailable"},
             "custom_fields": {"status": "complete"},
             "attachment_metadata": {"status": "complete" if isinstance(fields.get("attachment"), list) else "unavailable"}}
    definitions = []
    try:
        definitions = fetch("/rest/api/2/field")
        if not isinstance(definitions, list) or any(not isinstance(item, dict) or not isinstance(item.get("id"), str) for item in definitions):
            raise Incomplete("invalid_field_definitions")
        parts["field_definitions"] = {"status": "complete"}
    except (OSError, ValueError, TypeError) as exc:
        definitions = []
        parts["field_definitions"] = {"status": "unavailable", "error_code": failure(exc)}
    names = {item["id"]: item for item in definitions}
    custom = {
        key: {"name": names.get(key, {}).get("name") or key, "value": value,
              "raw": value, "schema": names.get(key, {}).get("schema")}
        for key, value in fields.items() if key.startswith("customfield_")
    }
    parts["custom_fields"]["collected"] = len(custom)
    comments = []
    seen: set[str] = set()
    pages = []
    total = None
    size = 0
    try:
        for _ in range(100):
            offset = len(comments)
            page = fetch(f"/rest/api/2/issue/{quote(issue_key, safe='')}/comment?startAt={offset}&maxResults=100")
            if not isinstance(page, dict) or not isinstance(page.get("comments"), list):
                raise Incomplete("invalid_comment_page")
            items = page["comments"]
            reported = page.get("total")
            if type(reported) is not int or type(page.get("startAt")) is not int or page.get("startAt") != offset or reported < offset + len(items):
                raise Incomplete("invalid_comment_offset_or_total")
            if total is not None and reported != total:
                raise Incomplete("comment_total_changed")
            total = reported
            ids = [str(item["id"]) for item in items if isinstance(item, dict)
                   and type(item.get("id")) in (str, int) and str(item["id"])]
            if len(ids) != len(items) or len(set(ids)) != len(ids) or seen.intersection(ids):
                raise Incomplete("comment_ids_missing_or_repeated")
            size += len(json.dumps(items, ensure_ascii=False).encode("utf-8"))
            if size > 8 * 1024 * 1024:
                raise Incomplete("comment_size_budget_exceeded")
            seen.update(ids)
            comments.extend(items)
            pages.append({"startAt": offset, "count": len(items), "total": total})
            if len(comments) == total:
                parts["comments"] = {"status": "complete"}
                break
            if not items:
                raise Incomplete("comment_page_ended_early")
        else:
            raise Incomplete("comment_page_budget_exceeded")
    except (OSError, ValueError, TypeError) as exc:
        parts["comments"] = {"status": "partial" if comments else "unavailable", "error_code": failure(exc)}
        # An embedded page is useful evidence, but does not establish coverage.
        embedded = fields.get("comment") or {}
        fallback = embedded.get("comments") if isinstance(embedded, dict) else None
        if not comments and isinstance(fallback, list):
            for item in fallback:
                if isinstance(item, dict) and type(item.get("id")) in (str, int) and str(item["id"]) and str(item["id"]) not in seen:
                    seen.add(str(item["id"]))
                    comments.append(item)
            if comments:
                parts["comments"].update(status="partial", fallback="issue_embedded_page")
            if total is None and type(embedded.get("total")) is int:
                total = embedded["total"]
    parts["comments"].update(pages=pages, collected=len(comments), reported_total=total)
    collected = {**raw, "fields": {**fields, "comment": {"comments": comments}}}
    issue = _issue_data(collected, cfg["JIRA_BASE_URL"], issue_key)
    issue["comments"] = [
        {**comment, "updated": original.get("updated"), "visibility": original.get("visibility"),
         "author_name": (_person(original.get("author")) or {}).get("name", ""),
         "body_raw": original.get("body")}
        for comment, original in zip(issue["comments"], comments)
    ]
    issue["comments"].sort(key=lambda item: (item.get("created") or "", item["id"]))
    issue.update(raw_fields=fields, custom_fields=custom, field_definitions=definitions)
    issue["acquisition"] = {
        "status": "complete" if all(part["status"] == "complete" for part in parts.values()) else "partial",
        "source": "website_jira_api", "scope": "currently_visible_api_data_not_an_atomic_snapshot",
        "started_at": started, "finished_at": datetime.now(timezone.utc).isoformat(),
        "parts": parts, "conflicts": [],
    }
    return issue


class JiraQueryError(ValueError):
    """JQL rejected by JIRA (HTTP 400); message carries JIRA's explanation."""


_SEARCH_FIELDS = ["summary", "status", "assignee", "reporter", "priority", "issuetype", "components", "created", "updated"]


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
            "reporter": _person(fields.get("reporter")),
            "components": [c.get("name", "") for c in fields.get("components") or []],
            "created": fields.get("created") or "",
            "updated": fields.get("updated") or "",
        })
    return {"total": int(raw.get("total") or 0), "issues": issues}


# ─────────────────────────────────────────────────────────────
# Issue activity (weekly report check) — read only
# ─────────────────────────────────────────────────────────────

_ACTIVITY_FIELDS = "summary,status,assignee,reporter,created,updated,comment,worklog"


def _activity_time(value: object) -> datetime | None:
    """Parse a JIRA timestamp such as 2026-10-09T15:23:11.000+0800."""
    for fmt in ("%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%dT%H:%M:%S%z"):
        try:
            return datetime.strptime(value, fmt)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            continue
    return None


def _username(value: dict | None) -> str:
    return (value or {}).get("name") or (value or {}).get("key") or ""


def get_issue_activity(issue_key: str, since: datetime) -> dict:
    """Return one issue's status, assignee and who touched it when.

    ``events`` lists every creation, field change, comment (and comment edit)
    and worklog of the whole history as {user, display, time}.  ``changes``,
    ``comments`` and ``worklogs`` carry the content of those at or after
    *since* (an aware datetime).  Only GET requests.  Raises
    urllib.error.HTTPError (404 missing, 403 no browse permission) or URLError.
    """
    cfg = _require_config()
    path = f"/rest/api/2/issue/{quote(issue_key, safe='')}"
    raw = _request(
        cfg["JIRA_BASE_URL"], cfg["JIRA_TOKEN"], "GET",
        f"{path}?fields={_ACTIVITY_FIELDS}&expand=changelog",
    )
    fields = raw.get("fields") or {}
    embedded = fields.get("worklog") or {}
    raw_worklogs = embedded.get("worklogs") or []
    # The issue embeds only the first page of worklogs.
    if int(embedded.get("total") or 0) > len(raw_worklogs):
        raw_worklogs = _request(cfg["JIRA_BASE_URL"], cfg["JIRA_TOKEN"], "GET", f"{path}/worklog").get("worklogs") or []

    events: list[dict] = []

    def touch(who: dict | None, when: object) -> datetime | None:
        moment = _activity_time(when)
        user = _username(who)
        if user and moment:
            events.append({"user": user, "display": (who or {}).get("displayName") or "", "time": moment})
        return moment

    touch(fields.get("reporter"), fields.get("created"))
    changes: list[dict] = []
    for history in (raw.get("changelog") or {}).get("histories") or []:
        moment = touch(history.get("author"), history.get("created"))
        if moment and moment >= since:
            changes.append({
                "user": _username(history.get("author")),
                "time": moment,
                "items": [
                    {"field": item.get("field") or "", "from": item.get("fromString") or "", "to": item.get("toString") or ""}
                    for item in history.get("items") or []
                ],
            })
    comments: list[dict] = []
    for comment in (fields.get("comment") or {}).get("comments") or []:
        touch(comment.get("author"), comment.get("created"))
        if comment.get("updated") != comment.get("created"):
            touch(comment.get("updateAuthor"), comment.get("updated"))
        moment = _activity_time(comment.get("updated") or comment.get("created"))
        if moment and moment >= since:
            comments.append({
                "author": _username(comment.get("author")),
                "update_author": _username(comment.get("updateAuthor")),
                "time": moment,
                "body": comment.get("body") or "",
            })
    worklogs: list[dict] = []
    for worklog in raw_worklogs:
        moment = touch(worklog.get("author"), worklog.get("created"))
        if moment and moment >= since:
            worklogs.append({
                "author": _username(worklog.get("author")),
                "time": moment,
                "time_spent": worklog.get("timeSpent") or "",
                "comment": worklog.get("comment") or "",
            })
    return {
        "key": raw.get("key") or issue_key,
        "summary": fields.get("summary") or "",
        "status": (fields.get("status") or {}).get("name", ""),
        "assignee": _person(fields.get("assignee")),
        "events": events,
        "changes": changes,
        "comments": comments,
        "worklogs": worklogs,
    }


def download_attachment(
    content_url: str,
    *,
    max_bytes: int = 50 * 1024 * 1024,
    timeout_seconds: float = 120,
) -> bytes:
    """Download attachment bytes; only URLs under the configured JIRA base."""
    cfg = _require_config()
    if not content_url.startswith(cfg["JIRA_BASE_URL"] + "/"):
        raise ValueError("附件地址不属于配置的 JIRA")
    req = urllib.request.Request(content_url, method="GET")
    req.add_header("Authorization", f"Bearer {cfg['JIRA_TOKEN']}")
    with urllib.request.urlopen(req, timeout=timeout_seconds) as resp:
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
