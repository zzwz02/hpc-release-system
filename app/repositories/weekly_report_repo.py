"""Weekly report check repository — weeks, findings, members, item cache.

Convention: pure functions fn(conn, ...), SQL only, no business rules.
"""
from __future__ import annotations

import sqlite3
from typing import Any

from app.repositories.base import dumps_json, loads_json, row_to_dict

_FINDING_COLUMNS = (
    "person", "sheet_date", "problem", "item", "jira_key", "jira_status",
    "assignee", "last_updated", "last_updater", "note",
)


# ---------------------------------------------------------------------------
# Weeks
# ---------------------------------------------------------------------------

def _week(row: sqlite3.Row) -> dict[str, Any]:
    week = row_to_dict(row)
    week["summary"] = loads_json(week.pop("summary_json"), {})
    return week


def list_weeks(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """All analysed weeks, newest first."""
    rows = conn.execute("SELECT * FROM weekly_report_weeks ORDER BY week_key DESC")
    return [_week(row) for row in rows]


def get_week(conn: sqlite3.Connection, week_key: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM weekly_report_weeks WHERE week_key = ?", (week_key,)).fetchone()
    return _week(row) if row else None


def replace_week(
    conn: sqlite3.Connection,
    week: dict[str, Any],
    findings: list[dict[str, str]],
) -> None:
    """Overwrite one week's row and findings.  Call inside transaction()."""
    conn.execute("DELETE FROM weekly_report_findings WHERE week_key = ?", (week["week_key"],))
    conn.execute(
        """
        INSERT INTO weekly_report_weeks(
            week_key, week_start, week_end, report_date, xlsx_name, xlsx_size,
            uploaded_by, uploaded_at, analyzed_by, analyzed_at, active_hours, summary_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(week_key) DO UPDATE SET
            week_start = excluded.week_start,
            week_end = excluded.week_end,
            report_date = excluded.report_date,
            xlsx_name = excluded.xlsx_name,
            xlsx_size = excluded.xlsx_size,
            uploaded_by = excluded.uploaded_by,
            uploaded_at = excluded.uploaded_at,
            analyzed_by = excluded.analyzed_by,
            analyzed_at = excluded.analyzed_at,
            active_hours = excluded.active_hours,
            summary_json = excluded.summary_json
        """,
        (
            week["week_key"], week["week_start"], week["week_end"], week["report_date"],
            week["xlsx_name"], week["xlsx_size"], week["uploaded_by"], week["uploaded_at"],
            week["analyzed_by"], week["analyzed_at"], week["active_hours"],
            dumps_json(week["summary"]),
        ),
    )
    conn.executemany(
        f"INSERT INTO weekly_report_findings(week_key, {', '.join(_FINDING_COLUMNS)}) "
        f"VALUES (?, {', '.join('?' for _ in _FINDING_COLUMNS)})",
        [(week["week_key"], *(finding[column] for column in _FINDING_COLUMNS)) for finding in findings],
    )


def list_findings(
    conn: sqlite3.Connection,
    week_key: str,
    *,
    person: str = "",
    problem: str = "",
) -> list[dict[str, Any]]:
    """Findings of one week in analysis order, optionally filtered."""
    sql = f"SELECT id, {', '.join(_FINDING_COLUMNS)} FROM weekly_report_findings WHERE week_key = ?"
    params: list[Any] = [week_key]
    if person:
        sql += " AND person = ?"
        params.append(person)
    if problem:
        sql += " AND problem = ?"
        params.append(problem)
    return [row_to_dict(row) for row in conn.execute(sql + " ORDER BY id", params)]


# ---------------------------------------------------------------------------
# Members
# ---------------------------------------------------------------------------

def list_members(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT sheet, jira_username, skip, note FROM weekly_report_members ORDER BY sheet COLLATE NOCASE"
    )
    return [{**row_to_dict(row), "skip": bool(row["skip"])} for row in rows]


def replace_members(conn: sqlite3.Connection, members: list[dict[str, Any]]) -> None:
    """Replace the whole member table.  Call inside transaction()."""
    conn.execute("DELETE FROM weekly_report_members")
    conn.executemany(
        "INSERT INTO weekly_report_members(sheet, jira_username, skip, note) VALUES (?, ?, ?, ?)",
        [
            (member["sheet"], member["jira_username"], 1 if member["skip"] else 0, member["note"])
            for member in members
        ],
    )


# ---------------------------------------------------------------------------
# Work-item split cache
# ---------------------------------------------------------------------------

def get_cached_items(conn: sqlite3.Connection, sheet: str, text_sha256: str) -> list[dict[str, Any]] | None:
    row = conn.execute(
        "SELECT items_json FROM weekly_report_items WHERE sheet = ? AND text_sha256 = ?",
        (sheet, text_sha256),
    ).fetchone()
    return loads_json(row["items_json"], []) if row else None


def put_cached_items(
    conn: sqlite3.Connection,
    sheet: str,
    text_sha256: str,
    items: list[dict[str, Any]],
) -> None:
    """Keep only the newest split per sheet: older report texts never recur."""
    conn.execute("DELETE FROM weekly_report_items WHERE sheet = ?", (sheet,))
    conn.execute(
        "INSERT INTO weekly_report_items(sheet, text_sha256, items_json) VALUES (?, ?, ?)",
        (sheet, text_sha256, dumps_json(items)),
    )
