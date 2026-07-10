"""Shared release read-model helpers.

Composes releases_repo + snapshots_repo + phase derivation into the full
release dict shape used across services (mirrors core.py:get_release /
list_releases): row columns plus ``snapshots`` (app_id → snapshot dict),
``released_locked`` (bool) and ``phase``.
"""
from __future__ import annotations

import sqlite3
from typing import Any

from app.domain.phases import current_phase
from app.repositories import releases_repo, snapshots_repo


def get_release(conn: sqlite3.Connection, release_id: str) -> dict[str, Any]:
    """Return the full release dict (with snapshots + phase).

    Raises KeyError for an unknown release id.
    """
    release = releases_repo.get_release_row(conn, release_id)
    if not release:
        raise KeyError(f"Unknown release: {release_id}")
    release["snapshots"] = snapshots_repo.get_all_for_release(conn, release_id)
    release["phase"] = current_phase(release)
    return release


def list_releases(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Return all release rows (no snapshots) with ``phase`` attached."""
    releases = releases_repo.list_release_rows(conn)
    for release in releases:
        release["phase"] = current_phase(release)
    return releases
