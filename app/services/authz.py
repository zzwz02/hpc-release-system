"""Authorization helpers that need DB data.

Functions here require DB access so they cannot live in domain/permissions.py.
Used as FastAPI dependencies or called from service methods.

# TODO Phase 2 — implement
"""
from __future__ import annotations

import sqlite3


def require_owner_or_rm(
    conn: sqlite3.Connection,
    app_id: str,
    username: str,
    role: str,
) -> None:
    """Raise AuthzError if user is neither the app owner nor RM.

    # TODO Phase 2
    """
    raise NotImplementedError


def require_app_audit_access(
    conn: sqlite3.Connection,
    app_id: str,
    username: str,
    role: str,
) -> None:
    """Raise AuthzError if user cannot view app audit log.

    # TODO Phase 2
    """
    raise NotImplementedError
