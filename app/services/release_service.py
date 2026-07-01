"""Release lifecycle service — import, create, lock, deadlines, schedule.

Port of the release-related handlers in server.py (lines 669-722, 995-1018)
and the schedule handlers.  Most business logic delegates to release_system.core;
final lock goes through artifact_service so current FastAPI artifact rules apply.

Services take conn: sqlite3.Connection first, pure (no HTTP), own transactions.
"""
from __future__ import annotations

import sqlite3

from release_system import core as _core

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _serialize_release(release: dict) -> dict:
    """Add `released_locked` (bool) and `phase` to a release dict.

    Mirrors server.py:_serialize_release (server.py:1384-1388).
    """
    out = dict(release)
    out["released_locked"] = bool(out.get("released_locked"))
    out["phase"] = _core.current_phase(out)
    return out


# ---------------------------------------------------------------------------
# Import / create
# ---------------------------------------------------------------------------

def import_initial(
    conn: sqlite3.Connection,
    *,
    csv: str,
    release_name: str | None,
    maca_version: str | None,
    app_freeze_deadline: str,
    doc_deadline: str,
) -> dict:
    """Import a CSV to bootstrap the first release.

    Returns {"release_id": ...}.
    Mirrors server.py:669-682.
    """
    release_id = _core.import_initial_rows(
        conn,
        _core.parse_csv_text(csv),
        release_name=release_name or None,
        maca_version=maca_version or None,
        app_freeze_deadline=app_freeze_deadline,
        doc_deadline=doc_deadline,
    )
    return {"release_id": release_id}


def create_release(
    conn: sqlite3.Connection,
    *,
    name: str,
    maca_version: str,
    app_freeze_deadline: str,
    doc_deadline: str,
    user: str,
    role: str,
) -> dict:
    """Clone the most recent release into a new one.

    Returns {"release_id": ...}.
    Mirrors server.py:682-695.
    """
    release_id = _core.create_release_from_previous(
        conn,
        name,
        maca_version=maca_version,
        app_freeze_deadline=app_freeze_deadline,
        doc_deadline=doc_deadline,
        user=user,
        role=role,
    )
    return {"release_id": release_id}


# ---------------------------------------------------------------------------
# Deadline update
# ---------------------------------------------------------------------------

def update_deadlines(
    conn: sqlite3.Connection,
    *,
    release_id: str,
    name: str | None,
    app_freeze_deadline: str | None,
    doc_deadline: str | None,
    user: str,
    role: str,
) -> dict:
    """Update name and/or deadline fields on a release.

    Returns {"release": <serialized release>}.
    Mirrors server.py:696-709.
    """
    release = _core.update_release_deadlines(
        conn,
        release_id,
        name=name,
        app_freeze_deadline=app_freeze_deadline,
        doc_deadline=doc_deadline,
        user=user,
        role=role,
    )
    return {"release": _serialize_release(release)}


# ---------------------------------------------------------------------------
# Lock / unlock
# ---------------------------------------------------------------------------

def final_lock(
    conn: sqlite3.Connection,
    *,
    release_id: str,
    user: str,
    role: str,
) -> dict:
    """Final-lock a release and generate final artifacts.

    Returns {"artifacts": [...]}.
    Mirrors server.py:710-715 except for current FastAPI artifact rules.
    """
    from app.services import artifact_service

    artifacts = artifact_service.final_lock_release(
        conn,
        release_id,
        user=user,
        role=role,
    )
    return {"artifacts": list(artifacts)}


def final_unlock(
    conn: sqlite3.Connection,
    *,
    release_id: str,
    user: str,
    role: str,
) -> dict:
    """Reverse a final lock.

    Returns {"ok": True}.
    Mirrors server.py:716-721.
    """
    _core.final_unlock_release(conn, release_id, user=user, role=role)
    return {"ok": True}


# ---------------------------------------------------------------------------
# Release schedule
# ---------------------------------------------------------------------------

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
    Mirrors server.py:995-1009.
    """
    entry = _core.upsert_release_schedule(
        conn,
        entry_id=entry_id,
        version=version,
        branch_cut_at=branch_cut_at,
        release_at=release_at,
        note=note,
        user=user,
        role=role,
    )
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
    Mirrors server.py:1010-1017.
    """
    if not entry_id:
        raise ValueError("id is required")
    ok = _core.delete_release_schedule(conn, entry_id, user=user, role=role)
    if not ok:
        raise RuntimeError("entry not found")
    return {"ok": True}
