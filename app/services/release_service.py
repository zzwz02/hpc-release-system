"""Release timeline (dashboard schedule entries).

Monthly cycles themselves live in cycle_service.
"""
from __future__ import annotations

import sqlite3

from app.db.connection import transaction
from app.domain.audit_diff import field_diff
from app.repositories import schedule_repo
from app.repositories.audit_repo import log_audit
from app.repositories.base import new_id
from app.timeutil import beijing_timestamp

def _normalize_schedule_date(value: str | None) -> str:
    """Normalize a schedule date string to ``YYYY-MM-DD``.

    Schedule entries store calendar dates only (no time-of-day); strip any
    trailing time component so the table renders cleanly.
    """
    import datetime as dt

    text = (value or "").strip()
    if not text:
        return ""
    text = text.replace("T", " ").split(" ")[0]
    try:
        return dt.datetime.strptime(text, "%Y-%m-%d").strftime("%Y-%m-%d")
    except ValueError as exc:
        raise ValueError(f"Invalid date: {value!r}; expected YYYY-MM-DD") from exc


def upsert_schedule_entry(
    conn: sqlite3.Connection,
    *,
    entry_id: str | None,
    version: str,
    branch_cut_at: str,
    release_at: str,
    note: str,
    user: str,
    role: str,
) -> dict:
    """Create or update a release schedule entry.

    Returns {"entry": <entry dict>}.
    Mirrors server.py:995-1009 and core.py:upsert_release_schedule.
    """
    version = (version or "").strip()
    if not version:
        raise ValueError("版本号不能为空")
    branch_cut = _normalize_schedule_date(branch_cut_at)
    release_date = _normalize_schedule_date(release_at)
    if branch_cut and release_date and branch_cut > release_date:
        raise ValueError("拉 branch 时间不能晚于发布时间")
    note = (note or "").strip()
    ts = beijing_timestamp()
    with transaction(conn):
        existing = schedule_repo.get_schedule_entry(conn, entry_id) if entry_id else None
        if existing:
            schedule_repo.update_schedule_entry(
                conn,
                entry_id,
                version=version,
                branch_cut_at=branch_cut,
                release_at=release_date,
                note=note,
                updated_at=ts,
                updated_by=user,
            )
            log_audit(
                conn,
                f"更新发布时间线：{version}",
                ts=ts,
                user=user,
                role=role,
                event="update_release_schedule",
                detail=field_diff(
                    existing,
                    {"version": version, "branch_cut_at": branch_cut, "release_at": release_date, "note": note},
                    {"version": "版本号", "branch_cut_at": "拉 branch 时间", "release_at": "发布时间", "note": "备注"},
                ),
            )
            final_id = entry_id
        else:
            final_id = entry_id or new_id("sched")
            schedule_repo.insert_schedule_entry(
                conn,
                entry_id=final_id,
                version=version,
                branch_cut_at=branch_cut,
                release_at=release_date,
                note=note,
                created_at=ts,
                created_by=user,
            )
            log_audit(
                conn,
                f"新增发布时间线：{version}",
                ts=ts,
                user=user,
                role=role,
                event="create_release_schedule",
                detail={"version": version, "branch_cut_at": branch_cut, "release_at": release_date, "note": note},
            )
        entry = schedule_repo.get_schedule_entry(conn, final_id)
    return {"entry": entry}


def delete_schedule_entry(
    conn: sqlite3.Connection,
    *,
    entry_id: str,
    user: str,
    role: str,
) -> dict:
    """Delete a release schedule entry.

    Returns {"ok": True} on success; raises RuntimeError if not found.
    Mirrors server.py:1010-1017 and core.py:delete_release_schedule.
    """
    if not entry_id:
        raise ValueError("id is required")
    with transaction(conn):
        existing = schedule_repo.get_schedule_entry(conn, entry_id)
        if not existing:
            raise RuntimeError("entry not found")
        schedule_repo.delete_schedule_entry(conn, entry_id)
        log_audit(
            conn,
            f"删除发布时间线：{existing['version']}",
            ts=beijing_timestamp(),
            user=user,
            role=role,
            event="delete_release_schedule",
            detail={"id": entry_id, "version": existing["version"]},
        )
    return {"ok": True}
