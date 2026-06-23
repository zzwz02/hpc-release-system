"""Authentication service — login, logout, session management.

Faithful port of release_system/core.py:authenticate, logout_session,
ldap_login_or_create, and session_user (server.py:636-703).

Services take conn: sqlite3.Connection first, are pure (no HTTP), and own
transaction boundaries.
"""
from __future__ import annotations

import hashlib
import os
import secrets
import sqlite3
from pathlib import Path

from app.repositories import sessions_repo, users_repo
from app.timeutil import beijing_timestamp

# ---------------------------------------------------------------------------
# Password hashing — mirrors core.py:hash_password / verify_password
# ---------------------------------------------------------------------------

def _hash_password(password: str, salt: str | None = None) -> str:
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt.encode("utf-8"), 120_000
    ).hex()
    return f"{salt}${digest}"


def _verify_password(password: str, encoded: str) -> bool:
    salt, expected = encoded.split("$", 1)
    return secrets.compare_digest(
        _hash_password(password, salt).split("$", 1)[1], expected
    )


def ensure_admin_user(
    conn: sqlite3.Connection,
    *,
    password_file: Path,
) -> str:
    """Ensure the local admin account exists.

    Mirrors the legacy startup behavior without touching ``server.py``:
    HPC_ADMIN_PASSWORD > admin_password.local > generated password file.
    Returns the source used, or ``"existing"`` when admin already exists.
    """
    if users_repo.get_user(conn, "admin"):
        return "existing"
    password = os.environ.get("HPC_ADMIN_PASSWORD", "")
    source = "HPC_ADMIN_PASSWORD"
    if not password:
        if password_file.exists():
            raw = password_file.read_text(encoding="utf-8")
            for line in raw.splitlines():
                if line.startswith("password="):
                    password = line.split("=", 1)[1].strip()
                    break
            source = "admin_password.local"
        else:
            password = secrets.token_urlsafe(24)
            password_file.write_text(
                f"username=admin\npassword={password}\n",
                encoding="utf-8",
            )
            source = "generated"
    users_repo.insert_user(
        conn,
        username="admin",
        password_hash=_hash_password(password),
        role="Admin",
        auth_source="local",
        display_name="Admin",
    )
    conn.commit()
    return source


# ---------------------------------------------------------------------------
# Public service functions
# ---------------------------------------------------------------------------

def login(conn: sqlite3.Connection, username: str, password: str) -> dict:
    """Verify local credentials and create a session.

    Returns {"ok": True, "token": <token>} on success.
    Returns {"ok": False} if credentials are wrong (caller raises PermissionError).

    Mirrors core.py:authenticate (server.py:636-645).
    """
    row = conn.execute(
        "SELECT username, password_hash FROM users WHERE username = ?", (username,)
    ).fetchone()
    if not row or not _verify_password(password, row["password_hash"]):
        return {"ok": False}
    token = secrets.token_urlsafe(32)
    sessions_repo.create_session(
        conn,
        token=token,
        username=username,
        created_at=beijing_timestamp(),
    )
    conn.commit()
    return {"ok": True, "token": token}


def login_ldap(
    conn: sqlite3.Connection,
    username: str,
    display_name: str,
    groups: list[str],
) -> str:
    """Ensure an LDAP-authenticated user exists, then return a session token.

    First-time LDAP logins are auto-provisioned from LDAP groups.  Subsequent
    logins update display_name if it changed; the stored role is always
    preserved so Admin manual changes are not overwritten by LDAP.

    Mirrors core.py:ldap_login_or_create (server.py:663-703).
    """
    from release_system import core as _core

    token = _core.ldap_login_or_create(conn, username, display_name, groups)
    return token


def logout(conn: sqlite3.Connection, token: str) -> None:
    """Invalidate a session token.

    Mirrors core.py:logout_session (server.py:657-662).
    """
    if token:
        sessions_repo.delete_session(conn, token)
        conn.commit()


def whoami(conn: sqlite3.Connection, token: str | None) -> dict | None:
    """Return user info for a session token, or None if no valid session.

    Returns {username, role, display_name} or None.
    Mirrors core.py:session_user (server.py:646-655).
    """
    if not token:
        return None
    return sessions_repo.get_session(conn, token)


def change_password(
    conn: sqlite3.Connection,
    username: str,
    old_password: str,
    new_password: str,
) -> None:
    """Change a user's local password.

    Raises PermissionError if old_password is wrong.
    Raises ValueError if new_password is empty.
    """
    if not new_password:
        raise ValueError("新密码不能为空")
    row = conn.execute(
        "SELECT password_hash FROM users WHERE username = ?", (username,)
    ).fetchone()
    if not row or not _verify_password(old_password, row["password_hash"]):
        raise PermissionError("原密码不正确")
    users_repo.update_password_hash(conn, username, password_hash=_hash_password(new_password))
    conn.commit()
