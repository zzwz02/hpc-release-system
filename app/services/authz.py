"""Authorization helpers that need DB data.

Functions here require DB access so they cannot live in domain/permissions.py.
Used by service methods (not as FastAPI Depends — they need conn + context args).

Faithful port of server.py:1356-1381 (require_owner_or_rm,
require_app_audit_access).
"""
from __future__ import annotations

import sqlite3

from app.api.errors import AuthzError


def require_owner_or_rm(
    conn: sqlite3.Connection,
    app_id: str,
    username: str,
    role: str,
) -> None:
    """Dead stub — this signature cannot work without an owners list.

    Use require_owner_or_rm_with_owners(owners, username, role) instead;
    callers already have the snapshot in hand.
    """
    raise NotImplementedError(
        "require_owner_or_rm needs an owners list — use require_owner_or_rm_with_owners"
    )


def require_owner_or_rm_with_owners(
    owners: list[str] | None,
    username: str,
    role: str,
) -> None:
    """Raise AuthzError if user is neither the app owner nor RM.

    Mirrors server.py:require_owner_or_rm:
      - RM: always allowed
      - Owner: allowed iff username in owners
      - else: denied
    """
    if role == "RM":
        return
    if role == "Owner" and username in (owners or []):
        return
    raise AuthzError("Owner permission required")


def require_app_audit_access(
    conn: sqlite3.Connection,
    app_id: str,
    username: str,
    role: str,
    release_id: str = "",
) -> None:
    """Raise AuthzError if user cannot view app audit log.

    Mirrors server.py:1363-1381:
      - RM / Admin / QA: always allowed
      - Owner: allowed iff username in owners on any (or specified) release snapshot
      - else: denied
    """
    if role in {"RM", "Admin", "QA"}:
        return
    if role != "Owner":
        raise AuthzError("App audit access denied")

    # Owner: check snapshot ownership
    from release_system import core as _core

    if release_id:
        release = _core.get_release(conn, release_id)
        if release:
            snapshot = release["snapshots"].get(app_id)
            if snapshot and username in (snapshot.get("owners") or []):
                return
        raise AuthzError("App audit access denied")

    for release in _core.list_releases(conn):
        release_data = _core.get_release(conn, release["id"])
        if release_data:
            snapshot = release_data["snapshots"].get(app_id)
            if snapshot and username in (snapshot.get("owners") or []):
                return
    raise AuthzError("App audit access denied")
