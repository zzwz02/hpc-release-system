"""Weekly report check — runs one check and serves its stored results.

Ported from the local weekly_report_jira_check.py (its main()).  A check takes
the parsed workbook (app/domain/weekly_report.py), asks the LLM to split each
person's update into work items, looks the referenced JIRA issues up
read-only, optionally asks the LLM whether the JIRA updates match the report,
and stores one version per ISO week.

Uploads and background jobs live in app/services/weekly_report_jobs.py.
"""
from __future__ import annotations

import concurrent.futures
import functools
import csv
import datetime as dt
import hashlib
import io
import re
import sqlite3
import threading
import time
import urllib.error
from collections.abc import Callable
from typing import Any

from app.db.connection import transaction
from app.domain import weekly_report as wr
from app.domain import weekly_report_prompts as prompts
from app.integrations import jira, llm
from app.repositories import users_repo, weekly_report_repo
from app.repositories.audit_repo import log_audit
from app.repositories.base import dumps_json
from app.timeutil import BEIJING_TZ, beijing_timestamp

# JIRA update window offered for each analysis: 8 x 24h, a full week plus the
# day the reports are collected.  RM can change it in the upload dialog.
DEFAULT_ACTIVE_HOURS = 192
MAX_ACTIVE_HOURS = 24 * 60
# Which account a sheet belongs to, and which sheets are not checked, comes
# only from the manual table (weekly_report_members), matched by sheet name
# ignoring case.  Sheets that are not a person (Project, Template, ...) and
# the leads' sheets are rows there marked "不检查".
# People checked at the same time; each costs one LLM call per work item at
# most plus one JIRA request per issue.
PERSON_WORKERS = 4
# A failed or unparseable LLM reply is asked for once more (llm.ask_json).
LLM_ATTEMPTS = 2

CSV_COLUMNS = (
    ("person", "姓名"),
    ("sheet_date", "周报日期"),
    ("problem", "问题"),
    ("item", "工作项"),
    ("jira_key", "JIRA"),
    ("jira_status", "JIRA状态"),
    ("assignee", "assignee"),
    ("last_updated", "最后更新时间"),
    ("last_updater", "最后更新人"),
    ("note", "说明"),
)

class CheckCancelled(llm.LLMCancelled):
    """The check was stopped on request; nothing about the week was stored.

    A kind of LLMCancelled, so llm.ask_json never retries a call it stopped.
    """


LlmCall = Callable[[str, str], str]
FetchActivity = Callable[[str, dt.datetime], dict[str, Any]]
Progress = Callable[[int, int, str], None]


# ---------------------------------------------------------------------------
# Settings and members
# ---------------------------------------------------------------------------

def validate_active_hours(value: object, *, source: str = "更新窗口") -> int:
    try:
        hours = int(str(value).strip())
    except ValueError as exc:
        raise ValueError(f"{source} 必须是整数小时：{value!r}") from exc
    if not 1 <= hours <= MAX_ACTIVE_HOURS:
        raise ValueError(f"{source} 应在 1 到 {MAX_ACTIVE_HOURS} 小时之间：{hours}")
    return hours


def _members_version(members: list[dict[str, Any]]) -> str:
    return hashlib.sha256(dumps_json(members).encode("utf-8")).hexdigest()[:16]


def get_members(conn: sqlite3.Connection) -> dict[str, Any]:
    members = weekly_report_repo.list_members(conn)
    return {"members": members, "version": _members_version(members)}


def save_members(
    conn: sqlite3.Connection,
    members: object,
    *,
    version: str,
    user: str,
    role: str,
) -> dict[str, Any]:
    """Replace the member table; refuses when it changed since *version* was read."""
    if not isinstance(members, list):
        raise ValueError("members 必须是列表")
    cleaned: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in members:
        if not isinstance(raw, dict):
            raise ValueError("成员格式不正确")
        sheet = str(raw.get("sheet") or "").strip()
        if not sheet:
            raise ValueError("周报页名不能为空")
        if sheet.casefold() in seen:
            raise ValueError(f"周报页名重复：{sheet}")
        seen.add(sheet.casefold())
        cleaned.append({
            "sheet": sheet,
            "jira_username": str(raw.get("jira_username") or "").strip(),
            "skip": bool(raw.get("skip")),
            "note": str(raw.get("note") or "").strip(),
        })
    with transaction(conn):
        current = weekly_report_repo.list_members(conn)
        if _members_version(current) != version:
            raise ValueError("成员表已被其他人修改，请刷新后重新编辑")
        weekly_report_repo.replace_members(conn, cleaned)
        log_audit(
            conn, f"更新周报手工对应（{len(cleaned)} 行）",
            ts=beijing_timestamp(), user=user, role=role, event="weekly_report_members",
            detail={"before": current, "after": cleaned},
        )
    return get_members(conn)


UNMAPPED_REASON = "找不到对应的用户"


def select_people(
    reports: list[wr.SheetReport],
    manual: list[dict[str, Any]],
    usernames: dict[str, str],
    undated: tuple[str, ...] = (),
) -> dict[str, Any]:
    """Decide, sheet by sheet in name order, who is checked and under which JIRA username.

    The manual table is the only source; its sheet names match ignoring case.
    A row marked skip is left out.  A sheet without a row (or with an empty one) is still checked, but only for
    missing JIRA ids.  *usernames* maps accounts to the site's display names,
    to show who an account is.  ``mapping`` records every sheet's outcome.
    """
    by_sheet = {member["sheet"].casefold(): member for member in manual}
    people: list[dict[str, Any]] = []
    mapping: list[dict[str, str]] = []
    for report in sorted(reports, key=lambda item: item.sheet.lower()):
        row = {"sheet": report.sheet, "username": "", "display_name": "", "status": "check", "reason": ""}
        member = by_sheet.get(report.sheet.casefold())
        if member and member["skip"]:
            row.update(username=member["jira_username"], status="skip", reason="手工对应里设置为不检查")
        elif member and member["jira_username"]:
            row.update(username=member["jira_username"])
        else:
            row.update(reason=UNMAPPED_REASON)
        row["display_name"] = usernames.get(row["username"], "")
        mapping.append(row)
        if row["status"] == "check":
            people.append({"report": report, "username": row["username"], "display_name": row["display_name"]})
    skipped = {member["sheet"].casefold() for member in manual if member["skip"]}
    return {
        "people": people,
        "mapping": mapping,
        "unmapped": [row["sheet"] for row in mapping if row["status"] == "check" and not row["username"]],
        "not_checked": sorted(
            [row["sheet"] for row in mapping if row["status"] == "skip"]
            + [name for name in undated if name.casefold() in skipped],
            key=str.lower,
        ),
        "undated": [name for name in undated if name.casefold() not in skipped],
    }


def _display_names(conn: sqlite3.Connection) -> dict[str, str]:
    """Account → person name ("Zhao Wu (zhawu)" → "Zhao Wu") from the site's users."""
    return {
        username: re.split(r"\s*[（(]", name, maxsplit=1)[0].strip()
        for username, name in users_repo.display_name_map(conn).items()
    }


def _select(conn: sqlite3.Connection, reports: list[wr.SheetReport], undated: tuple[str, ...]) -> dict[str, Any]:
    return select_people(
        reports, weekly_report_repo.list_members(conn), _display_names(conn), undated,
    )


def preview(
    conn: sqlite3.Connection,
    reports: list[wr.SheetReport],
    undated: list[str],
) -> dict[str, Any]:
    """What an upload contains under the current manual table."""
    selection = _select(conn, reports, tuple(undated))
    report_date = wr.default_report_date([person["report"] for person in selection["people"]])
    week = wr.week_of(report_date) if report_date else None
    return {
        "report_date": report_date.isoformat() if report_date else "",
        "week": week,
        "week_exists": bool(week and weekly_report_repo.get_week(conn, week["week_key"])),
        "people_count": len(selection["people"]),
        "mapping": selection["mapping"],
        "unmapped_sheets": selection["unmapped"],
        "undated_sheets": selection["undated"],
        "active_hours": DEFAULT_ACTIVE_HOURS,
    }


# ---------------------------------------------------------------------------
# One person
# ---------------------------------------------------------------------------

def _ask(llm_call: LlmCall, prompt: str) -> dict[str, Any]:
    return llm.ask_json(prompts.SYSTEM, prompt, attempts=LLM_ATTEMPTS, call=llm_call)


def extract_items(llm_call: LlmCall, text: str) -> list[dict[str, Any]]:
    items = _ask(llm_call, prompts.LLM_PROMPT % text).get("items", [])
    return [item for item in items if isinstance(item, dict)] if isinstance(items, list) else []


def check_person(
    report: wr.SheetReport,
    username: str,
    *,
    report_date: dt.date,
    today: dt.date,
    since: dt.datetime,
    consistency: bool,
    cached_items: list[dict[str, Any]] | None,
    llm_call: LlmCall,
    fetch_issue: Callable[[str], dict[str, Any] | str],
) -> dict[str, Any]:
    """Check one sheet: its problems, work items, a remark, and the split to cache."""
    problems = wr.date_problems(report, report_date=report_date, today=today)
    result: dict[str, Any] = {"problems": problems, "items": [], "note": "", "to_cache": None}
    if not report.text:
        return result
    if cached_items is None:
        try:
            items = extract_items(llm_call, report.text)
        except llm.LLMConfigError:
            raise
        except Exception as exc:
            raise RuntimeError(f"{report.sheet}：LLM 拆分工作项失败：{exc}") from exc
        result["to_cache"] = items
    else:
        items = cached_items
    result["items"] = items

    notes: list[str] = []
    issues: dict[str, dict[str, Any] | str] = {}
    if username:
        for item in items:
            for key in wr.item_keys(item):
                if wr.JIRA_KEY_RE.match(key) and key not in issues:
                    issues[key] = fetch_issue(key)
    else:
        notes.append(f"{UNMAPPED_REASON}，只检查是否写了 JIRA ID")

    for item in items:
        found, updated = wr.item_problems(item, username=username, issues=issues, since=since)
        problems.extend(found)
        if not updated or not consistency:
            continue
        title = str(item.get("title") or "")
        jira_text = "\n\n".join(wr.jira_update_text(activity, username, since) for activity in updated)
        try:
            verdict = _ask(llm_call, prompts.CONSIST_PROMPT % (title, item.get("summary") or title, jira_text))
        except llm.LLMConfigError:
            raise
        except Exception as exc:
            # A failed consistency check leaves the other checks standing.
            notes.append(f"一致性分析失败（{title}）：{exc}")
            continue
        kind = {
            prompts.VERDICT_MISMATCH: wr.T_MISMATCH,
            prompts.VERDICT_PARTIAL: wr.T_PARTIAL,
        }.get(verdict.get("verdict", ""))
        if kind:
            problems.append(wr.problem(
                kind, title, ", ".join(activity["key"] for activity in updated),
                note=str(verdict.get("reason") or ""),
            ))
    result["note"] = "；".join(notes)
    return result


# ---------------------------------------------------------------------------
# One check
# ---------------------------------------------------------------------------

def _issue_fetcher(fetch_activity: FetchActivity, since: dt.datetime) -> Callable[[str], dict[str, Any] | str]:
    """Fetch each issue once per check; 404 / 403 become their problem type.

    Any other failure ends the check: reporting it per issue would only bury
    an unreachable JIRA or a rejected token.
    """
    cache: dict[str, dict[str, Any] | str] = {}
    lock = threading.Lock()

    def fetch(key: str) -> dict[str, Any] | str:
        with lock:
            if key in cache:
                return cache[key]
        try:
            value: dict[str, Any] | str = fetch_activity(key, since)
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                value = wr.T_NOT_FOUND
            elif exc.code == 403:
                value = wr.T_FORBIDDEN
            else:
                raise RuntimeError(f"查询 JIRA {key} 失败：HTTP {exc.code}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise RuntimeError(f"无法访问 JIRA（查询 {key} 时）：{exc}") from exc
        with lock:
            cache[key] = value
        return value

    return fetch


def run_check(
    conn: sqlite3.Connection,
    reports: list[wr.SheetReport],
    *,
    report_date: dt.date | None = None,
    active_hours: int | None = None,
    consistency: bool = True,
    undated: tuple[str, ...] = (),
    now: dt.datetime | None = None,
    llm_call: LlmCall | None = None,
    fetch_activity: FetchActivity | None = None,
    progress: Progress | None = None,
    should_stop: Callable[[], bool] = lambda: False,
    meter: llm.LlmMeter | None = None,
    cancel: llm.CancelToken | None = None,
) -> dict[str, Any]:
    """Check every person in *reports*; returns the week, summary and findings.

    Nothing about the week is stored here (see store_week); only the work-item
    split cache is written, so a failed check does not repeat finished splits.
    Raises CheckCancelled soon after *should_stop* turns true, without
    waiting for the calls under way; cancelling *cancel* as well cuts their
    LLM connections at once.  *meter* collects LLM call counts and tokens
    while the check runs.
    """
    started = time.monotonic()
    meter = meter or llm.LlmMeter()
    hours = DEFAULT_ACTIVE_HOURS if active_hours is None else validate_active_hours(active_hours)
    selection = _select(conn, reports, undated)
    people = selection["people"]
    if not people:
        raise ValueError("周报里没有需要检查的人员页")
    if report_date is None:
        report_date = wr.default_report_date([person["report"] for person in people])
    if report_date is None:
        raise ValueError("各页的日期都不正常，无法确定报告日期，请手工指定")

    if llm_call is None:
        llm.require_config()
        llm_call = meter.wrap(functools.partial(llm.chat_json, cancel=cancel), streaming=True)
    else:
        llm_call = meter.wrap(llm_call, streaming=False)
    if fetch_activity is None:
        if jira.load_config() is None:
            raise RuntimeError("未配置 JIRA：release_system.conf 的 [jira] 需要 JIRA_BASE_URL 和 JIRA_TOKEN")
        fetch_activity = jira.get_issue_activity

    def stoppable(call):
        # A stop request ends a person at their next LLM or JIRA call; an LLM
        # call under way is cut through *cancel*.
        def wrapper(*args, **kwargs):
            if should_stop():
                raise CheckCancelled("分析已中止")
            return call(*args, **kwargs)
        return wrapper

    now = now or dt.datetime.now(BEIJING_TZ)
    since = now - dt.timedelta(hours=hours)
    llm_call = stoppable(llm_call)
    fetch_issue = stoppable(_issue_fetcher(fetch_activity, since))
    total = len(people)
    if progress:
        progress(0, total, f"开始分析 {total} 人")

    results: dict[str, dict[str, Any]] = {}
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=PERSON_WORKERS, thread_name_prefix="weekly-report")
    try:
        futures = {}
        for person in people:
            report = person["report"]
            cached = weekly_report_repo.get_cached_items(conn, report.sheet, wr.text_sha256(report.text))
            future = executor.submit(
                check_person, report, person["username"],
                report_date=report_date, today=now.date(), since=since, consistency=consistency,
                cached_items=cached, llm_call=llm_call, fetch_issue=fetch_issue,
            )
            futures[future] = report
        pending = set(futures)
        while pending:
            done, pending = concurrent.futures.wait(
                pending, timeout=0.2, return_when=concurrent.futures.FIRST_COMPLETED,
            )
            # Checked before the results: a person cut short by the stop fails
            # with an error that is not worth reporting.
            if should_stop():
                raise CheckCancelled("分析已中止")
            for future in done:
                report = futures[future]
                result = future.result()
                results[report.sheet] = result
                if result["to_cache"] is not None:
                    weekly_report_repo.put_cached_items(
                        conn, report.sheet, wr.text_sha256(report.text), result["to_cache"],
                    )
                    conn.commit()
                if progress:
                    progress(len(results), total, f"已完成 {report.sheet}")
    finally:
        # People not started yet are dropped.  After a stop the workers are
        # not waited for: their LLM connections are cut, and a JIRA request
        # under way (seconds at most) ends on its own; they touch no database.
        executor.shutdown(wait=not should_stop(), cancel_futures=True)

    findings = [
        {"person": person["report"].sheet, "sheet_date": person["report"].date.isoformat(), **found}
        for person in people
        for found in results[person["report"].sheet]["problems"]
    ]
    summary = wr.summarize(findings)
    summary.update(
        elapsed_seconds=round(time.monotonic() - started),
        llm=meter.snapshot(),
        people_count=total,
        consistency=consistency,
        window_start=since.strftime("%Y-%m-%d %H:%M"),
        not_checked=selection["not_checked"],
        unmapped_sheets=selection["unmapped"],
        people=[
            {
                "sheet": person["report"].sheet,
                "date": person["report"].date.isoformat(),
                "item_count": len(results[person["report"].sheet]["items"]),
                "note": results[person["report"].sheet]["note"],
                "username": person["username"],
                "display_name": person["display_name"],
            }
            for person in people
        ],
    )
    return {
        "week": wr.week_of(report_date),
        "report_date": report_date.isoformat(),
        "active_hours": hours,
        "summary": summary,
        "findings": findings,
    }


def store_week(
    conn: sqlite3.Connection,
    result: dict[str, Any],
    *,
    xlsx_name: str,
    xlsx_size: int,
    uploaded_by: str,
    uploaded_at: str,
    analyzed_by: str,
    role: str,
) -> dict[str, Any]:
    """Store a finished check as its week's only version, replacing any earlier one."""
    week = {
        "week_key": result["week"]["week_key"],
        "week_start": result["week"]["week_start"],
        "week_end": result["week"]["week_end"],
        "report_date": result["report_date"],
        "xlsx_name": xlsx_name,
        "xlsx_size": xlsx_size,
        "uploaded_by": uploaded_by,
        "uploaded_at": uploaded_at,
        "analyzed_by": analyzed_by,
        "analyzed_at": beijing_timestamp(),
        "active_hours": result["active_hours"],
        "summary": result["summary"],
    }
    with transaction(conn):
        replaced = weekly_report_repo.get_week(conn, week["week_key"])
        weekly_report_repo.replace_week(conn, week, result["findings"])
        log_audit(
            conn,
            f"周报检查 {week['week_key']}：{result['summary']['total']} 条问题"
            + ("（覆盖上一版）" if replaced else ""),
            ts=week["analyzed_at"], user=analyzed_by, role=role, event="weekly_report_analyze",
            detail={
                key: week[key]
                for key in ("week_key", "report_date", "xlsx_name", "xlsx_size", "uploaded_by", "uploaded_at", "active_hours")
            },
        )
    return week


# ---------------------------------------------------------------------------
# Reads and export
# ---------------------------------------------------------------------------

def _week_view(week: dict[str, Any]) -> dict[str, Any]:
    label = wr.week_of(dt.date.fromisoformat(week["report_date"]))["label"]
    return {**week, "label": label, "finding_count": week["summary"].get("total", 0)}


def list_weeks(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    return [
        {key: view[key] for key in ("week_key", "label", "week_start", "week_end", "finding_count")}
        for view in map(_week_view, weekly_report_repo.list_weeks(conn))
    ]


def get_week(conn: sqlite3.Connection, week_key: str) -> dict[str, Any] | None:
    week = weekly_report_repo.get_week(conn, week_key)
    if week is None:
        return None
    return {**_week_view(week), "jira_browse_url": jira.browse_url(), "problem_types": list(wr.PROBLEM_TYPES)}


def list_findings(
    conn: sqlite3.Connection,
    week_key: str,
    *,
    person: str = "",
    problem: str = "",
) -> list[dict[str, Any]] | None:
    if weekly_report_repo.get_week(conn, week_key) is None:
        return None
    return weekly_report_repo.list_findings(conn, week_key, person=person, problem=problem)


def export_csv(findings: list[dict[str, Any]]) -> str:
    """Problem details, one row each; UTF-8 BOM so Excel reads the Chinese text."""
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow([title for _key, title in CSV_COLUMNS])
    for finding in findings:
        writer.writerow([finding[key] for key, _title in CSV_COLUMNS])
    return "\ufeff" + buffer.getvalue()


def _markdown_line(finding: dict[str, Any]) -> str:
    kind, key, item = finding["problem"], finding["jira_key"], finding["item"]
    if kind in (wr.T_MISMATCH, wr.T_PARTIAL):
        return f"- {kind}：{key}（{item}）：{finding['note']}"
    if kind == wr.T_NOT_UPDATED:
        return (
            f"- {kind}：{key}（{item}，{finding['jira_status']}；"
            f"最后更新 {finding['last_updated'] or '无'} by {finding['last_updater'] or '-'}）"
        )
    if key:
        return f"- {kind}：{key}（{item}）"
    return f"- {kind}：{item}"


def export_markdown(week: dict[str, Any], findings: list[dict[str, Any]]) -> str:
    """Each person's result, then the summary table."""
    summary = week["summary"]
    by_person: dict[str, list[dict[str, Any]]] = {}
    for finding in findings:
        by_person.setdefault(finding["person"], []).append(finding)
    lines = [
        f"# 周报 JIRA 检查 {week['report_date']}",
        "",
        f"本期周报日期 {week['report_date']}；JIRA 更新窗口 {summary.get('window_start', '')} 起"
        f"（{week['active_hours']} 小时）；不检查：{'、'.join(summary.get('not_checked', []))}。",
        "",
        "## 每人明细",
    ]
    people = summary.get("people", [])
    for person in people:
        lines += ["", f"### {person['sheet']}"]
        if person["note"]:
            lines.append(f"- 备注：{person['note']}")
        rows = by_person.get(person["sheet"], [])
        if not rows:
            lines.append("- 没有发现问题")
        lines += [_markdown_line(row) for row in rows]
    lines += [
        "",
        "## 汇总",
        "",
        f"| 姓名 | 周报日期 | 工作项 | {' | '.join(wr.PROBLEM_TYPES)} |",
        "|---|---|---|" + "---|" * len(wr.PROBLEM_TYPES),
    ]
    for person in people:
        rows = by_person.get(person["sheet"], [])
        counts = [sum(1 for row in rows if row["problem"] == kind) for kind in wr.PROBLEM_TYPES]
        lines.append(
            f"| {person['sheet']} | {person['date']} | {person['item_count']} | "
            f"{' | '.join(str(count) if count else '' for count in counts)} |"
        )
    lines.append("")
    return "\n".join(lines) + "\n"


def export_filename(week: dict[str, Any], fmt: str) -> str:
    return f"weekly_jira_check_{week['report_date']}.{fmt}"
