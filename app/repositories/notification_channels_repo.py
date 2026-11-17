"""DingTalk robot per notification kind.

Convention: pure functions fn(conn, ...), SQL only, no business rules.
"""
from __future__ import annotations

import sqlite3
from typing import Any

from app.repositories.base import dumps_json, loads_json, row_to_dict


def _channel(row: sqlite3.Row) -> dict[str, Any]:
    data = row_to_dict(row)
    data["enabled"] = bool(data["enabled"])
    data["at_user_ids"] = loads_json(data.pop("at_user_ids_json", None), [])
    return data


def get_channel(conn: sqlite3.Connection, kind: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM notification_channels WHERE kind = ?", (kind,)).fetchone()
    return _channel(row) if row else None


def list_channels(conn: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    return {
        row["kind"]: _channel(row)
        for row in conn.execute("SELECT * FROM notification_channels")
    }


def save_channel(
    conn: sqlite3.Connection,
    kind: str,
    *,
    enabled: bool,
    access_token: str,
    secret: str,
    at_user_ids: list[str],
    updated_at: str,
    updated_by: str,
) -> None:
    conn.execute(
        """
        INSERT INTO notification_channels(kind, enabled, access_token, secret,
                                          at_user_ids_json, updated_at, updated_by)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(kind) DO UPDATE SET
          enabled = excluded.enabled,
          access_token = excluded.access_token,
          secret = excluded.secret,
          at_user_ids_json = excluded.at_user_ids_json,
          updated_at = excluded.updated_at,
          updated_by = excluded.updated_by
        """,
        (kind, int(enabled), access_token, secret, dumps_json(at_user_ids), updated_at, updated_by),
    )
