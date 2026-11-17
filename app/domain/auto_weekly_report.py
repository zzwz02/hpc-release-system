"""Automatic weekly report rules, source validation and Markdown rendering.

Ported from jira-weekly-report; all timestamps here are Beijing local time.
"""
from __future__ import annotations

import datetime as dt
import re
from typing import Any

from app.timeutil import beijing_now

DEFAULT_GROUP = "pde_hpc"
# Bound a request to a month; large imports are split into LLM evidence batches.
MAX_WINDOW_DAYS = 31
TAGS = {"风险", "客户进展", "研发进展", "卡点", "需支持"}
TRIVIAL_FIELDS = {"Sprint", "Rank", "RemoteIssueLink", "Workflow", "labels", "Link", "Epic Child", "Target end"}
SECRET_PATTERNS = [
    (r"(--token[= ]+|token[:：=]\s*|Bearer\s+)\S+", r"\1[已打码]"),
    (r"((?:密码|口令|password|passwd|pwd)\s*[:：=]\s*)\S+", r"\1[已打码]"),
    (r"\b(?:ms|ghp|github_pat|hf|sk)[-_][A-Za-z0-9_-]{16,}\b", "[已打码]"),
]


def mask(text: str) -> str:
    for pattern, replacement in SECRET_PATTERNS:
        text = re.sub(pattern, replacement, text, flags=re.I)
    return text


def defaults(now: dt.datetime | None = None) -> dict:
    now = now or beijing_now()
    saturday = (now - dt.timedelta(days=now.weekday() - 5)).replace(hour=0, minute=0, second=0, microsecond=0)
    return {"group": DEFAULT_GROUP, "since": (saturday - dt.timedelta(days=7, hours=12)).strftime("%Y-%m-%dT%H:%M"),
            "until": min(saturday, now).strftime("%Y-%m-%dT%H:%M"),
            "report_date": (now.date() + dt.timedelta(days=4 - now.weekday())).isoformat()}


def options(group: str, since: str, until: str, report_date: str) -> dict:
    group = group.strip()
    if not re.fullmatch(r"[\w.@ -]{1,100}", group):
        raise ValueError("JIRA 组名无效")
    try:
        start = dt.datetime.strptime(since.replace("T", " "), "%Y-%m-%d %H:%M")
        end = dt.datetime.strptime(until.replace("T", " "), "%Y-%m-%d %H:%M")
        friday = dt.date.fromisoformat(report_date)
    except ValueError as exc:
        raise ValueError("请填写有效的统计起止时间和周报日期") from exc
    if not start < end <= beijing_now():
        raise ValueError("统计起点须早于终点，终点不能晚于当前北京时间")
    if end - start > dt.timedelta(days=MAX_WINDOW_DAYS):
        raise ValueError(f"一次最多统计 {MAX_WINDOW_DAYS} 天")
    if friday.weekday() != 4:
        raise ValueError("周报日期必须是周五")
    return {"group": group, "since": start.strftime("%Y-%m-%d %H:%M"), "until": end.strftime("%Y-%m-%d %H:%M"), "report_date": friday.isoformat()}


def local_time(value: str) -> dt.datetime:
    parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo:
        from app.timeutil import BEIJING_TZ
        parsed = parsed.astimezone(BEIJING_TZ).replace(tzinfo=None)
    return parsed


def in_window(value: str, opts: dict) -> bool:
    return bool(value) and local_time(opts["since"]) <= local_time(value) < local_time(opts["until"])


def substantive(issue: dict, opts: dict) -> bool:
    f = issue["fields"]
    return bool(in_window(f.get("created", ""), opts) or f.get("comment", {}).get("comments")
                or f.get("attachment") or any(x["field"] not in TRIVIAL_FIELDS for h in issue.get("changelog", {}).get("histories", []) for x in h["items"]))


def width(text: str) -> float:
    return sum(0.5 if ord(c) < 128 else 1 for c in text)


def render(payload: dict, opts: dict, issues: list[dict], epics: list[dict]) -> tuple[str, str]:
    """Reject invented IDs, duplicate ownership, uncovered sources and malformed output."""
    known = {i["key"] for i in issues}
    epic_names = {e["key"]: e["name"] for e in epics}
    errors: list[str] = []
    sources: list[str] = []
    assigned: dict[str, str] = {}
    project_names: set[str] = set()
    notes: list[str] = []

    def keys(value: Any, label: str) -> list[str]:
        if not isinstance(value, list) or not value or any(not isinstance(k, str) or k not in known for k in value):
            errors.append(f"{label} 缺少来源或引用了不存在的工单")
            return []
        return list(dict.fromkeys(value))

    def sentence(text: Any) -> str:
        if not isinstance(text, str) or not text.strip() or "\n" in text or width(text) > 60:
            errors.append("条目必须为一行且不超过 60 字")
            return ""
        if re.search(r"\b(?:PHS|CCPM|MC3|SPD)-\d+\b", text):
            errors.append("正文条目不应包含工单号，来源写入交付说明")
        if re.search(r"\d{4}-\d{2}-\d{2}", text):
            errors.append("正文条目不写日期")
        return mask(text.strip())

    def items(rows: list, owner: str) -> list[str]:
        out = []
        if not rows:
            errors.append(f"{owner} 为空")
        for row in rows:
            text = sentence(row.get("text"))
            ks = keys(row.get("keys"), owner)
            sources.extend(ks)
            for key in ks:
                if key in assigned and assigned[key] != owner:
                    errors.append(f"{key} 出现在多个项目")
                assigned[key] = owner
            out.append(f"- {text}")
            notes.append(f"- {owner} / {text} → {' / '.join(ks)}")
        return out

    highlights = payload.get("highlights", [])
    if not 3 <= len(highlights) <= 5:
        errors.append("本周要点必须有 3～5 条")
    seen_tags = set()
    head = [f"# 周报 {opts['report_date']}（简洁版）", "", "## 本周要点", ""]
    highlighted = []
    for row in highlights:
        tag = row.get("tag")
        if tag not in TAGS or tag in seen_tags:
            errors.append("本周要点标签无效或重复")
        seen_tags.add(tag)
        highlighted.extend(keys(row.get("keys"), "本周要点"))
        head.append(f"- {tag}：{sentence(row.get('text'))}")
        if width(f"{tag}：{row.get('text', '')}") > 60:
            errors.append("本周要点含标签后超过 60 字")
    head += ["", "## 第一部分：客户项目", ""]

    def project(block: dict, level: str) -> None:
        title = block.get("name", "")
        if not isinstance(title, str) or not title.strip() or "\n" in title or re.search(r"[#<>]|\b[A-Z]+-\d+\b", title):
            errors.append("项目名称无效")
            title = "项目"
        rows = block.get("items", [])
        if title in project_names:
            errors.append(f"{title} 重复成块")
        project_names.add(title)
        if len(rows) > (6 if title == "其他客户支持" else 5):
            errors.append(f"{title} 条目过多")
        ks = keys(block.get("keys"), title)
        if any(not k.startswith(("PHS-", "CCPM-")) for k in ks):
            errors.append("项目标题只能引用 PHS/CCPM 工单")
        row_keys = {k for row in rows for k in row.get("keys", [])}
        if not set(ks) <= row_keys:
            errors.append(f"{title} 标题工单缺少对应正文")
        head.extend([f"{level} {mask(title)}（{' / '.join(ks)}）", ""])
        if level == "###":
            head.extend([opts["report_date"], ""])
        head.extend(items(rows, title))
        head.append("")

    for block in payload.get("customers", []):
        project(block, "###")
    support = payload.get("customer_support", [])
    if support:
        if len(support) > 6:
            errors.append("其他客户支持最多 6 条")
        head.extend(["### 其他客户支持", "", opts["report_date"], "", *items(support, "其他客户支持"), ""])
    head.extend(["---", "", "## 第二部分：其他项目 Weekly Update", "", opts["report_date"], ""])
    for epic in payload.get("epics", []):
        key = epic.get("key", "")
        if key not in epic_names:
            errors.append(f"Epic {key} 不在 PHS Epic 列表")
        head.extend([f"### {epic_names.get(key, key)}（{key}）", ""])
        for block in epic.get("projects", []):
            project(block, "####")
    other = payload.get("other", [])
    if other:
        head.extend(["### 其他", "", *items(other, "其他"), ""])
    if not set(highlighted) <= set(sources):
        errors.append("本周要点缺少正文对应内容")
    omitted = payload.get("omitted", [])
    omitted_keys = set()
    notes += ["", "## 未写进周报的工单", ""]
    for row in omitted:
        ks = keys([row.get("key")], "未写入说明")
        reason = row.get("reason", "")
        if not isinstance(reason, str) or not reason.strip():
            errors.append("未写入的工单必须说明原因")
        omitted_keys.update(ks)
        notes.append(f"- {' / '.join(ks)}：{mask(str(reason))}")
    missing = known - set(sources) - omitted_keys
    if missing:
        errors.append("工单未覆盖：" + "、".join(sorted(missing)))
    if set(sources) & omitted_keys:
        errors.append("同一工单同时被写入和列为未写入")
    if errors:
        raise ValueError("；".join(errors[:12]))
    notes += ["", "## 归属、附件与待确认事项", ""]
    notes += [f"- {mask(str(note))}" for note in payload.get("notes", [])]
    return "\n".join(head).strip() + "\n", "## 条目 → 工单\n\n" + "\n".join(notes) + "\n"
