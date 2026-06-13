"""Artifact generation service.

# TODO Phase 2 — implement
"""
from __future__ import annotations

import sqlite3


def generate_artifacts(
    conn: sqlite3.Connection,
    release_id: str,
    *,
    user: str,
    role: str,
) -> dict:
    """(Re)generate all artifacts for a release.

    # TODO Phase 2
    """
    raise NotImplementedError
