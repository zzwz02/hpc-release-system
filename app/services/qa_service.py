"""QA service — status updates and log uploads.

# TODO Phase 2 — implement
"""
from __future__ import annotations

import sqlite3


def set_qa_status(
    conn: sqlite3.Connection,
    release_id: str,
    app_id: str,
    *,
    status: str,
    user: str,
) -> dict:
    """Set the QA status for an app in a release.

    # TODO Phase 2
    """
    raise NotImplementedError


def upload_qa_log(
    conn: sqlite3.Connection,
    release_id: str,
    *,
    filename: str,
    storage_path: str,
    user: str,
) -> dict:
    """Record a QA log upload.

    # TODO Phase 2
    """
    raise NotImplementedError
