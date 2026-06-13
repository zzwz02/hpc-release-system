"""FastAPI dependency providers.

Key dependencies:
  - get_db: yields one ManagedConnection per request, closes in finally (no pooling)
  - require_login: extracts session from cookie, raises 401 if missing
  - require_roles(...): role gate, raises 403 if role not in allowed set
"""
from __future__ import annotations

import sqlite3
from collections.abc import Generator

from fastapi import Cookie, Depends

from app.api.errors import AuthzError
from app.config import settings
from app.db.connection import connect
from app.repositories import sessions_repo


def get_db() -> Generator[sqlite3.Connection, None, None]:
    """Yield one SQLite connection per request; always close in finally.

    No connection pooling — one connection per request as in the original
    server.py.  The connection is closed even if the request handler raises.

    check_same_thread=False is required because FastAPI's async event loop
    may resolve Depends() in a different OS thread from where the endpoint
    coroutine runs.  SQLite itself is thread-safe in WAL mode; we ensure
    correctness by never sharing a connection across concurrent requests.
    """
    conn = connect(settings.db_path)
    try:
        yield conn
    finally:
        conn.close()


def require_login(
    hpc_session: str | None = Cookie(default=None),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    """Return the session user dict or raise 401.

    Mirrors server.py current_user(required=True): reads cookie →
    sessions_repo.get_session → raises PermissionError (→ 401) if no match.
    """
    if hpc_session:
        user = sessions_repo.get_session(conn, hpc_session)
        if user:
            return user
    raise PermissionError("Login required")


def require_roles(*roles: str):
    """Return a dependency that raises 403 if the user's role is not in *roles*.

    Usage:
        Depends(require_roles("RM", "Admin"))
    """
    def _check(user: dict = Depends(require_login)) -> dict:
        if user.get("role") not in roles:
            raise AuthzError(f"Role required: {', '.join(roles)}")
        return user
    return _check
