"""MCP repository — tokens, client sessions and call logs (SQL only)."""
from __future__ import annotations

import sqlite3
from typing import Any

from app.repositories.base import row_to_dict

# ---------------------------------------------------------------------------
# Tokens
# ---------------------------------------------------------------------------

_TOKEN_COLUMNS = "id, username, name, token_prefix, created_at, last_used_at, revoked_at"


def insert_token(conn: sqlite3.Connection, *, username: str, name: str, token_hash: str,
                 token_prefix: str, created_at: str) -> int:
    cur = conn.execute(
        "INSERT INTO mcp_tokens(username, name, token_hash, token_prefix, created_at) VALUES (?, ?, ?, ?, ?)",
        (username, name, token_hash, token_prefix, created_at),
    )
    return int(cur.lastrowid)


def list_tokens(conn: sqlite3.Connection, username: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        f"SELECT {_TOKEN_COLUMNS} FROM mcp_tokens WHERE username = ? ORDER BY id DESC", (username,),
    ).fetchall()
    return [row_to_dict(row) for row in rows]


def active_token(conn: sqlite3.Connection, username: str) -> dict[str, Any] | None:
    row = conn.execute(
        f"""
        SELECT {_TOKEN_COLUMNS},
               (SELECT client_name FROM mcp_call_logs
                WHERE token_id = mcp_tokens.id AND client_name != '' ORDER BY id DESC LIMIT 1) AS last_client
        FROM mcp_tokens WHERE username = ? AND revoked_at = '' ORDER BY id DESC LIMIT 1
        """,
        (username,),
    ).fetchone()
    return row_to_dict(row) if row else None


def get_token(conn: sqlite3.Connection, token_id: int) -> dict[str, Any] | None:
    row = conn.execute(f"SELECT {_TOKEN_COLUMNS} FROM mcp_tokens WHERE id = ?", (token_id,)).fetchone()
    return row_to_dict(row) if row else None


def revoke_token(conn: sqlite3.Connection, token_id: int, revoked_at: str) -> None:
    conn.execute(
        "UPDATE mcp_tokens SET revoked_at = ? WHERE id = ? AND revoked_at = ''", (revoked_at, token_id),
    )


def revoke_active_tokens(conn: sqlite3.Connection, username: str, revoked_at: str) -> None:
    conn.execute(
        "UPDATE mcp_tokens SET revoked_at = ? WHERE username = ? AND revoked_at = ''", (revoked_at, username),
    )


def find_active_token_user(conn: sqlite3.Connection, token_hash: str | None = None,
                           *, token_id: int | None = None) -> dict[str, Any] | None:
    """Active token joined with its user's current role (None for a deleted user)."""
    row = conn.execute(
        """
        SELECT mcp_tokens.id AS token_id, users.username, users.role, users.display_name
        FROM mcp_tokens JOIN users ON users.username = mcp_tokens.username
        WHERE (mcp_tokens.token_hash = ? OR mcp_tokens.id = ?) AND mcp_tokens.revoked_at = ''
          -- One active token per user: only the newest counts, even if an
          -- older database still holds several unrevoked rows.
          AND mcp_tokens.id = (SELECT MAX(id) FROM mcp_tokens AS newest
                               WHERE newest.username = mcp_tokens.username AND newest.revoked_at = '')
        """,
        (token_hash, token_id),
    ).fetchone()
    return row_to_dict(row) if row else None


def touch_token(conn: sqlite3.Connection, token_id: int, used_at: str) -> None:
    conn.execute("UPDATE mcp_tokens SET last_used_at = ? WHERE id = ?", (used_at, token_id))


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------

def insert_session(conn: sqlite3.Connection, *, session_id: str, token_id: int, username: str,
                   client_name: str, client_version: str, protocol_version: str,
                   client_capabilities: str, created_at: str) -> None:
    conn.execute(
        """
        INSERT INTO mcp_sessions(id, token_id, username, client_name, client_version,
                                 protocol_version, client_capabilities, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (session_id, token_id, username, client_name, client_version, protocol_version,
         client_capabilities, created_at),
    )


def get_session(conn: sqlite3.Connection, session_id: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM mcp_sessions WHERE id = ?", (session_id,)).fetchone()
    return row_to_dict(row) if row else None


def end_session(conn: sqlite3.Connection, session_id: str, token_id: int, ended_at: str) -> None:
    conn.execute(
        "UPDATE mcp_sessions SET ended_at = ? WHERE id = ? AND token_id = ? AND ended_at = ''",
        (ended_at, session_id, token_id),
    )


# ---------------------------------------------------------------------------
# Call logs
# ---------------------------------------------------------------------------

def insert_log(conn: sqlite3.Connection, entry: dict[str, Any]) -> None:
    columns = ("created_at", "username", "token_id", "session_id", "client_name", "client_version",
               "kind", "category", "tool", "target_type", "target", "args_json", "extra_json",
               "status", "error", "result_summary", "duration_ms")
    conn.execute(
        f"INSERT INTO mcp_call_logs({', '.join(columns)}) VALUES ({', '.join('?' for _ in columns)})",
        tuple(entry.get(column, "") if column != "token_id" else entry.get(column) for column in columns),
    )


def _where(filters: dict[str, str]) -> tuple[str, list[Any]]:
    clauses, params = ["created_at >= ?"], [filters["since"]]
    for column in ("username", "category", "tool", "status", "kind", "target"):
        if filters.get(column):
            clauses.append(f"{column} = ?")
            params.append(filters[column])
    if filters.get("token_id"):
        clauses.append("token_id = ?")
        params.append(int(filters["token_id"]))
    return " AND ".join(clauses), params


def stats(conn: sqlite3.Connection, filters: dict[str, str]) -> dict[str, Any]:
    where, params = _where(filters)

    def rows(sql: str) -> list[dict[str, Any]]:
        return [row_to_dict(row) for row in conn.execute(sql, params).fetchall()]

    totals = conn.execute(
        f"""
        SELECT COUNT(*) AS calls,
               COALESCE(SUM(status = 'error'), 0) AS errors,
               COUNT(DISTINCT username) AS users,
               COUNT(DISTINCT NULLIF(session_id, '')) AS sessions,
               COALESCE(SUM(kind = 'tool'), 0) AS tool_calls
        FROM mcp_call_logs WHERE {where}
        """,
        params,
    ).fetchone()
    return {
        "totals": row_to_dict(totals),
        "by_tool": rows(
            f"""
            SELECT kind, category, tool, COUNT(*) AS calls, COALESCE(SUM(status = 'error'), 0) AS errors,
                   CAST(AVG(duration_ms) AS INTEGER) AS avg_ms, COUNT(DISTINCT username) AS users
            FROM mcp_call_logs WHERE {where}
            GROUP BY kind, category, tool ORDER BY calls DESC
            """
        ),
        "by_user": rows(
            f"""
            SELECT username, COUNT(*) AS calls, COALESCE(SUM(status = 'error'), 0) AS errors,
                   COALESCE(SUM(kind = 'tool'), 0) AS tool_calls, MAX(created_at) AS last_at
            FROM mcp_call_logs WHERE {where}
            GROUP BY username ORDER BY calls DESC
            """
        ),
        # Per token, so a user can tell machines / clients apart; the token's
        # name and prefix come from mcp_tokens (never the secret).
        "by_token": rows(
            f"""
            SELECT l.token_id, COALESCE(t.name, '') AS token_name,
                   COALESCE(t.token_prefix, '') AS token_prefix, COALESCE(t.revoked_at, '') AS revoked_at,
                   l.username, COUNT(*) AS calls, COALESCE(SUM(l.status = 'error'), 0) AS errors,
                   COALESCE(SUM(l.kind = 'tool'), 0) AS tool_calls,
                   COUNT(DISTINCT NULLIF(l.session_id, '')) AS sessions,
                   GROUP_CONCAT(DISTINCT NULLIF(l.client_name, '')) AS clients, MAX(l.created_at) AS last_at
            FROM (SELECT * FROM mcp_call_logs WHERE {where}) AS l
            LEFT JOIN mcp_tokens AS t ON t.id = l.token_id
            GROUP BY l.token_id ORDER BY calls DESC
            """
        ),
        "by_category": rows(
            f"""
            SELECT COALESCE(NULLIF(category, ''), kind) AS category, COUNT(*) AS calls,
                   COALESCE(SUM(status = 'error'), 0) AS errors, COUNT(DISTINCT username) AS users
            FROM mcp_call_logs WHERE {where}
            GROUP BY 1 ORDER BY calls DESC
            """
        ),
        "top_targets": rows(
            f"""
            SELECT target_type, target, COUNT(*) AS calls, COUNT(DISTINCT username) AS users,
                   MAX(created_at) AS last_at
            FROM mcp_call_logs WHERE {where} AND target != ''
            GROUP BY target_type, target ORDER BY calls DESC LIMIT 20
            """
        ),
        "by_client": rows(
            f"""
            SELECT COALESCE(NULLIF(client_name, ''), '未知') AS client, COUNT(*) AS calls,
                   COUNT(DISTINCT username) AS users
            FROM mcp_call_logs WHERE {where}
            GROUP BY client ORDER BY calls DESC
            """
        ),
        "by_day": rows(
            f"""
            SELECT substr(created_at, 1, 10) AS day, COUNT(*) AS calls,
                   COALESCE(SUM(status = 'error'), 0) AS errors, COUNT(DISTINCT username) AS users
            FROM mcp_call_logs WHERE {where}
            GROUP BY day ORDER BY day
            """
        ),
    }


def list_logs(conn: sqlite3.Connection, filters: dict[str, str], *, limit: int, offset: int) -> dict[str, Any]:
    where, params = _where(filters)
    total = int(conn.execute(f"SELECT COUNT(*) FROM mcp_call_logs WHERE {where}", params).fetchone()[0])
    items = conn.execute(
        f"""
        SELECT l.id, l.created_at, l.username, l.token_id, COALESCE(t.name, '') AS token_name,
               COALESCE(t.token_prefix, '') AS token_prefix, l.client_name, l.client_version, l.kind,
               l.category, l.tool, l.target_type, l.target, l.args_json, l.extra_json, l.status, l.error,
               l.result_summary, l.duration_ms
        FROM (SELECT * FROM mcp_call_logs WHERE {where} ORDER BY id DESC LIMIT ? OFFSET ?) AS l
        LEFT JOIN mcp_tokens AS t ON t.id = l.token_id
        ORDER BY l.id DESC
        """,
        [*params, limit, offset],
    ).fetchall()
    return {"total": total, "items": [row_to_dict(row) for row in items]}
