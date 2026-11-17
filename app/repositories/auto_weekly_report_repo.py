"""Automatic weekly report persistence, separate from Excel check results."""
import sqlite3

from app.repositories.base import dumps_json, loads_json


COLUMNS = "id, jira_group, since, until, report_date, created_by, created_at, revision"


def list_reports(conn: sqlite3.Connection) -> list[dict]:
    return [dict(r) for r in conn.execute(f"SELECT {COLUMNS} FROM auto_weekly_reports ORDER BY report_date DESC, created_at DESC, rowid DESC")]


def get(conn: sqlite3.Connection, report_id: str) -> dict | None:
    row = conn.execute("SELECT * FROM auto_weekly_reports WHERE id=?", (report_id,)).fetchone()
    if not row:
        return None
    result = dict(row)
    result["evidence"] = loads_json(result.pop("evidence_json"), {})
    result["llm"] = loads_json(result.pop("llm_json"), {})
    return result


def previous(conn: sqlite3.Connection, group: str, report_date: str) -> dict | None:
    row = conn.execute("SELECT id FROM auto_weekly_reports WHERE jira_group=? AND report_date<? ORDER BY report_date DESC, created_at DESC, rowid DESC LIMIT 1", (group, report_date)).fetchone()
    return get(conn, row["id"]) if row else None


def replace(conn: sqlite3.Connection, report: dict) -> str:
    """Save a completed report, replacing its week atomically; keep its ID.

    report_date is the week's Friday. Call inside transaction(). Incrementing
    revision also rejects editor drafts opened before a regeneration.
    """
    conn.execute("""INSERT INTO auto_weekly_reports
        (id,jira_group,since,until,report_date,created_by,created_at,markdown,notes,evidence_json,llm_json,revision)
        VALUES (:id,:group,:since,:until,:report_date,:created_by,:created_at,:markdown,:notes,:evidence,:llm,1)
        ON CONFLICT(report_date) DO UPDATE SET
            jira_group=excluded.jira_group,
            since=excluded.since,
            until=excluded.until,
            created_by=excluded.created_by,
            created_at=excluded.created_at,
            markdown=excluded.markdown,
            notes=excluded.notes,
            evidence_json=excluded.evidence_json,
            llm_json=excluded.llm_json,
            revision=auto_weekly_reports.revision+1""",
        {**report, "evidence": dumps_json(report["evidence"]), "llm": dumps_json(report["llm"])})
    return conn.execute("SELECT id FROM auto_weekly_reports WHERE report_date=?", (report["report_date"],)).fetchone()["id"]


def update(conn: sqlite3.Connection, report_id: str, markdown: str, notes: str, revision: int) -> bool:
    return conn.execute("UPDATE auto_weekly_reports SET markdown=?,notes=?,revision=revision+1 WHERE id=? AND revision=?",
                        (markdown, notes, report_id, revision)).rowcount == 1
