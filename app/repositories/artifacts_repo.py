"""Artifacts repository — artifacts table CRUD.

# TODO Phase 1 — implement
"""
from __future__ import annotations

import sqlite3


def get_artifact(
    conn: sqlite3.Connection,
    release_id: str,
    kind: str,
) -> dict | None:
    """Return an artifact row or None.

    # TODO Phase 1
    """
    raise NotImplementedError


def upsert_artifact(
    conn: sqlite3.Connection,
    release_id: str,
    kind: str,
    *,
    name: str,
    content: str,
    generated_at: str,
    final: int = 0,
) -> None:
    """Insert or replace an artifact row.

    # TODO Phase 1
    """
    raise NotImplementedError
