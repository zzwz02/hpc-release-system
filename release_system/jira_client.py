"""release_system/jira_client.py — Jira REST API client (stdlib only).

Used by server.py for auto-creating Jira issues when RM approves with
approval_mode='dispatch_spd' and jira_auto_created=1.

Config is read from <project_root>/jira.conf.
"""
from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from datetime import date, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)

# Default path: project root / jira.conf
_DEFAULT_CONF = Path(__file__).parent.parent / "jira.conf"


# ─────────────────────────────────────────────────────────────
# Config
# ─────────────────────────────────────────────────────────────

def load_config(conf_path: str | Path | None = None) -> dict | None:
    """Load jira.conf.  Returns None if file missing or required keys absent."""
    p = Path(conf_path) if conf_path else _DEFAULT_CONF
    if not p.exists():
        return None
    cfg: dict = {}
    with open(p, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            cfg[key.strip()] = val.strip()
    if not cfg.get("JIRA_BASE_URL") or not cfg.get("JIRA_TOKEN"):
        return None
    cfg["JIRA_BASE_URL"] = cfg["JIRA_BASE_URL"].rstrip("/")
    return cfg


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
# Field discovery
# ─────────────────────────────────────────────────────────────

def discover_eta_fields(cfg: dict) -> dict[str, str]:
    """Return {'expected_eta': 'customfield_XXXX', 'estimated_eta': 'customfield_YYYY'}.

    First checks config overrides (JIRA_FIELD_EXPECTED_ETA / JIRA_FIELD_ESTIMATED_ETA),
    then falls back to GET /rest/api/2/field discovery by name.
    Returns empty dict if discovery fails.
    """
    result: dict[str, str] = {}
    if cfg.get("JIRA_FIELD_EXPECTED_ETA"):
        result["expected_eta"] = cfg["JIRA_FIELD_EXPECTED_ETA"]
    if cfg.get("JIRA_FIELD_ESTIMATED_ETA"):
        result["estimated_eta"] = cfg["JIRA_FIELD_ESTIMATED_ETA"]
    if len(result) == 2:
        return result

    try:
        fields = _request(cfg["JIRA_BASE_URL"], cfg["JIRA_TOKEN"], "GET", "/rest/api/2/field")
        for f in fields:
            name = (f.get("name") or "").lower()
            fid = f.get("id", "")
            if "expected_eta" not in result and "expected" in name and "eta" in name:
                result["expected_eta"] = fid
            elif "estimated_eta" not in result and "estimated" in name and "eta" in name:
                result["estimated_eta"] = fid
    except Exception as e:
        logger.warning("Jira ETA field discovery failed: %s", e)

    return result


def _pick_type(types: dict[str, str], candidates: list[str]) -> str | None:
    for c in candidates:
        if c.lower() in types:
            return types[c.lower()]
    for kw in candidates:
        for k, v in types.items():
            if kw.lower() in k:
                return v
    return None


# ─────────────────────────────────────────────────────────────
# Issue title helper (mirrors JS cicdJiraTitle)
# ─────────────────────────────────────────────────────────────

def compute_title(conn, request_type: str, payload: dict, task_id: str | None) -> str:
    """Compute the Jira issue title for a CICD request.

    Mirrors the JS function cicdJiraTitle() in index.html.
    """
    import sqlite3  # local import to keep module importable without sqlite3
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
    "branch":          "分支",
    "repo_type":       "仓库类型",
    "pipeline_url":    "流水线地址",
    "build_cmd":       "构建命令",
    "deploy_cmd":      "部署命令",
    "rollback_cmd":    "回滚命令",
    "test_cmd":        "测试命令",
    "env":             "环境",
    "owner_username":  "负责人",
    "qa_username":     "QA负责人",
    "description":     "描述",
    "jira_id":         "Jira",
}

_REQ_TYPE_LABEL: dict[str, str] = {
    "create":         "新建任务",
    "modify":         "修改任务",
    "owner_transfer": "负责人变更",
}


def build_description(
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
            if v is None or v == "":
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

    lines.append(f"----")
    lines.append(f"摘要：{title}")
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────
# Create issue
# ─────────────────────────────────────────────────────────────

def create_issue(cfg: dict, title: str, description: str | None = None) -> str:
    """Create a Jira issue and return its key (e.g. 'SPD-456').

    Sets component to JIRA_COMPONENT (default 'SPD_CICD') and both ETA
    fields to today + 2 days.

    Raises urllib.error.HTTPError or RuntimeError on failure.
    """
    project   = cfg.get("JIRA_PROJECT", "SPD")
    parent_key = cfg.get("JIRA_PARENT_ISSUE", "").strip()
    component  = cfg.get("JIRA_COMPONENT", "SPD_CICD")
    eta        = (date.today() + timedelta(days=2)).strftime("%Y-%m-%d")

    eta_fields = discover_eta_fields(cfg)
    exp_field  = eta_fields.get("expected_eta")
    est_field  = eta_fields.get("estimated_eta")

    # Fetch project issue types
    proj = _request(cfg["JIRA_BASE_URL"], cfg["JIRA_TOKEN"],
                    "GET", f"/rest/api/2/project/{project}")
    types = {it["name"].lower(): it["name"] for it in proj.get("issueTypes", [])}

    if parent_key:
        type_name = (
            _pick_type(types, ["子任务", "subtask", "sub-task", "sub task", "子问题"])
            or next((v for k, v in types.items() if "task" in k), "Sub-task")
        )
        if cfg.get("JIRA_ISSUE_TYPE_SUBTASK"):
            type_name = cfg["JIRA_ISSUE_TYPE_SUBTASK"]
        fields: dict = {
            "project":    {"key": project},
            "summary":    title,
            "issuetype":  {"name": type_name},
            "parent":     {"key": parent_key},
            "components": [{"name": component}],
        }
    else:
        type_name = (
            _pick_type(types, ["task", "任务", "story", "故事"])
            or next(iter(types.values()), "Task")
        )
        if cfg.get("JIRA_ISSUE_TYPE_TASK"):
            type_name = cfg["JIRA_ISSUE_TYPE_TASK"]
        fields = {
            "project":    {"key": project},
            "summary":    title,
            "issuetype":  {"name": type_name},
            "components": [{"name": component}],
        }

    if cfg.get("JIRA_ASSIGNEE"):
        fields["assignee"] = {"name": cfg["JIRA_ASSIGNEE"]}
    if exp_field:
        fields[exp_field] = eta
    if est_field:
        fields[est_field] = eta
    fields["description"] = description or f"由 CICD 发布系统自动创建（审批模式：下发给 SPD 执行交付）。\n\n摘要：{title}"

    result = _request(cfg["JIRA_BASE_URL"], cfg["JIRA_TOKEN"],
                      "POST", "/rest/api/2/issue", {"fields": fields})
    key = result.get("key") or ""
    if not key:
        raise RuntimeError(f"Jira 建单成功但未返回 key: {result}")
    logger.info("Jira issue created: %s — %s", key, title)
    return key
