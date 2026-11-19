"""Platform credentials, deliberately separate from MCP tokens and sessions."""
import sqlite3

PUBLIC_COLUMNS = "id, username, name, token_hint, created_at, last_used_at, last_operation, revoked_at"


def list_tokens(conn: sqlite3.Connection, username: str) -> list[dict]:
    return [dict(row) for row in conn.execute(
        f"SELECT {PUBLIC_COLUMNS} FROM api_tokens WHERE username=? ORDER BY (revoked_at='') DESC, id DESC LIMIT 50",
        (username,),
    )]


def get_token(conn: sqlite3.Connection, token_id: int, username: str) -> dict | None:
    row = conn.execute(f"SELECT {PUBLIC_COLUMNS} FROM api_tokens WHERE id=? AND username=?", (token_id, username)).fetchone()
    return dict(row) if row else None


def active_count(conn: sqlite3.Connection, username: str) -> int:
    return conn.execute("SELECT COUNT(*) FROM api_tokens WHERE username=? AND revoked_at=''", (username,)).fetchone()[0]


def insert(conn: sqlite3.Connection, username: str, name: str, digest: str, hint: str, now: str) -> int:
    return conn.execute(
        "INSERT INTO api_tokens(username,name,token_hash,token_hint,created_at) VALUES (?,?,?,?,?)",
        (username, name, digest, hint, now),
    ).lastrowid


def revoke(conn: sqlite3.Connection, token_id: int, username: str, now: str) -> None:
    conn.execute("UPDATE api_tokens SET revoked_at=? WHERE id=? AND username=? AND revoked_at=''", (now, token_id, username))


def delete_revoked(conn: sqlite3.Connection, token_id: int, username: str) -> None:
    conn.execute("DELETE FROM api_tokens WHERE id=? AND username=? AND revoked_at<>''", (token_id, username))


def authenticate(conn: sqlite3.Connection, digest: str) -> dict | None:
    row = conn.execute(
        "SELECT t.id AS token_id, u.username, u.role FROM api_tokens t "
        "JOIN users u ON u.username=t.username WHERE t.token_hash=? AND t.revoked_at=''", (digest,),
    ).fetchone()
    return dict(row) if row else None


def touch(conn: sqlite3.Connection, token_id: int, now: str, operation: str) -> None:
    conn.execute("UPDATE api_tokens SET last_used_at=?, last_operation=? WHERE id=?", (now, operation, token_id))
