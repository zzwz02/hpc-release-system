"""FastAPI dependency providers.

Key dependencies:
  - get_db: yields one ManagedConnection per request, closes in finally (no pooling)
  - require_login: extracts session from cookie, raises 401 if missing
  - require_roles(...): role gate, raises 403 if role not in allowed set

# TODO Phase 2 — implement fully
"""
from __future__ import annotations

import sqlite3
from collections.abc import Generator

from fastapi import Cookie, Depends


def get_db() -> Generator[sqlite3.Connection, None, None]:
    """Yield one SQLite connection per request; always close in finally.

    No connection pooling — one connection per request as in the original
    server.py.  The connection is closed even if the request handler raises.

    # TODO Phase 2 — wire to app.db.connection.connect() with settings.db_path
    """
    # TODO Phase 2 — replace with real implementation
    raise NotImplementedError("get_db not yet implemented (Phase 2)")
    yield  # type: ignore[misc]  # pragma: no cover


def require_login(
    hpc_session: str | None = Cookie(default=None),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    """Return the session user dict or raise 401.

    # TODO Phase 2 — implement session lookup via sessions_repo
    """
    raise NotImplementedError("require_login not yet implemented (Phase 2)")


def require_roles(*roles: str):
    """Return a dependency that raises 403 if the user's role is not in *roles*.

    # TODO Phase 2 — implement
    """
    def _check(user: dict = Depends(require_login)) -> dict:
        raise NotImplementedError("require_roles not yet implemented (Phase 2)")
    return _check
