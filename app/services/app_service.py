"""App workbench service — app CRUD and snapshot update with CICD decision sync.

Key method: update_snapshot — saves snapshot then calls sync_decision_to_cicd
inside the same transaction when release_decision changes (plan §3.5 b).

# TODO Phase 2 — implement
"""
from __future__ import annotations

import sqlite3


def add_new_app(conn: sqlite3.Connection, *, release_id: str, user: str, **payload) -> dict:
    """Add a new app to a release (find-or-create with git identity dedup).

    # TODO Phase 2
    """
    raise NotImplementedError


def update_snapshot(
    conn: sqlite3.Connection,
    release_id: str,
    app_id: str,
    *,
    user: str,
    role: str,
    fields: dict,
) -> dict:
    """Save snapshot fields; sync CICD status when release_decision changes.

    # TODO Phase 2 — decision change triggers sync_decision_to_cicd inside tx
    """
    raise NotImplementedError


def delete_app(conn: sqlite3.Connection, app_id: str, *, user: str, role: str) -> None:
    """Delete an app globally (Admin-only system maintenance).

    # TODO Phase 2
    """
    raise NotImplementedError


def transfer_owner(
    conn: sqlite3.Connection,
    app_id: str,
    release_id: str,
    *,
    new_owner: str,
    user: str,
) -> None:
    """Transfer app ownership within a release snapshot.

    # TODO Phase 2
    """
    raise NotImplementedError
