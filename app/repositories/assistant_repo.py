"""CICD assistant conversation repository."""
from __future__ import annotations

import sqlite3
from typing import Any

from app.repositories.base import dumps_json, loads_json, new_id, row_to_dict


def _conversation_row(row: sqlite3.Row) -> dict[str, Any]:
    data = row_to_dict(row)
    data["message_count"] = int(data.get("message_count") or 0)
    return data


def _message_row(row: sqlite3.Row) -> dict[str, Any]:
    data = row_to_dict(row)
    data["sequence"] = int(data.get("sequence") or 0)
    data["metadata"] = loads_json(data.pop("metadata_json", "{}"), {})
    return data


def _state_row(row: sqlite3.Row | None, conversation_id: str) -> dict[str, Any]:
    if not row:
        return {
            "conversation_id": conversation_id,
            "rolling_summary": "",
            "slots": {},
            "summarized_until_sequence": 0,
            "updated_at": "",
        }
    data = row_to_dict(row)
    data["slots"] = loads_json(data.pop("slots_json", "{}"), {})
    data["summarized_until_sequence"] = int(data.get("summarized_until_sequence") or 0)
    return data


def create_conversation(
    conn: sqlite3.Connection,
    *,
    user_id: str,
    title: str,
    created_at: str,
    conversation_id: str | None = None,
) -> dict[str, Any]:
    conv_id = conversation_id or new_id("asst_conv")
    clean_title = title.strip() or "新会话"
    conn.execute(
        """
        INSERT INTO assistant_conversations(id, user_id, title, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (conv_id, user_id, clean_title, created_at, created_at),
    )
    conn.execute(
        """
        INSERT INTO assistant_conversation_state(
            conversation_id, rolling_summary, slots_json, summarized_until_sequence, updated_at
        )
        VALUES (?, '', '{}', 0, ?)
        """,
        (conv_id, created_at),
    )
    row = conn.execute(
        """
        SELECT id, user_id, title, created_at, updated_at, deleted_at, message_count
        FROM assistant_conversations
        WHERE id = ?
        """,
        (conv_id,),
    ).fetchone()
    return _conversation_row(row)


def list_conversations(
    conn: sqlite3.Connection,
    *,
    user_id: str,
    limit: int = 100,
) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT id, user_id, title, created_at, updated_at, deleted_at, message_count
        FROM assistant_conversations
        WHERE user_id = ? AND deleted_at = ''
        ORDER BY updated_at DESC, created_at DESC
        LIMIT ?
        """,
        (user_id, limit),
    ).fetchall()
    return [_conversation_row(row) for row in rows]


def get_conversation(
    conn: sqlite3.Connection,
    *,
    conversation_id: str,
    user_id: str,
    include_deleted: bool = False,
) -> dict[str, Any] | None:
    deleted_clause = "" if include_deleted else "AND deleted_at = ''"
    row = conn.execute(
        f"""
        SELECT id, user_id, title, created_at, updated_at, deleted_at, message_count
        FROM assistant_conversations
        WHERE id = ? AND user_id = ? {deleted_clause}
        """,
        (conversation_id, user_id),
    ).fetchone()
    return _conversation_row(row) if row else None


def update_title(
    conn: sqlite3.Connection,
    *,
    conversation_id: str,
    user_id: str,
    title: str,
    updated_at: str,
) -> None:
    conn.execute(
        """
        UPDATE assistant_conversations
        SET title = ?, updated_at = ?
        WHERE id = ? AND user_id = ? AND deleted_at = ''
        """,
        (title.strip() or "新会话", updated_at, conversation_id, user_id),
    )


def soft_delete_conversation(
    conn: sqlite3.Connection,
    *,
    conversation_id: str,
    user_id: str,
    deleted_at: str,
) -> bool:
    cursor = conn.execute(
        """
        UPDATE assistant_conversations
        SET deleted_at = ?, updated_at = ?
        WHERE id = ? AND user_id = ? AND deleted_at = ''
        """,
        (deleted_at, deleted_at, conversation_id, user_id),
    )
    return cursor.rowcount > 0


def add_message(
    conn: sqlite3.Connection,
    *,
    conversation_id: str,
    role: str,
    content: str,
    created_at: str,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    row = conn.execute(
        "SELECT COALESCE(MAX(sequence), 0) + 1 AS next_sequence "
        "FROM assistant_messages WHERE conversation_id = ?",
        (conversation_id,),
    ).fetchone()
    sequence = int(row["next_sequence"])
    message_id = new_id("asst_msg")
    conn.execute(
        """
        INSERT INTO assistant_messages(
            id, conversation_id, sequence, role, content, metadata_json, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            message_id,
            conversation_id,
            sequence,
            role,
            content,
            dumps_json(metadata or {}),
            created_at,
        ),
    )
    conn.execute(
        """
        UPDATE assistant_conversations
        SET updated_at = ?, message_count = message_count + 1
        WHERE id = ?
        """,
        (created_at, conversation_id),
    )
    message = conn.execute(
        """
        SELECT id, conversation_id, sequence, role, content, metadata_json, created_at
        FROM assistant_messages
        WHERE id = ?
        """,
        (message_id,),
    ).fetchone()
    return _message_row(message)


def list_messages(
    conn: sqlite3.Connection,
    *,
    conversation_id: str,
) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT id, conversation_id, sequence, role, content, metadata_json, created_at
        FROM assistant_messages
        WHERE conversation_id = ?
        ORDER BY sequence ASC
        """,
        (conversation_id,),
    ).fetchall()
    return [_message_row(row) for row in rows]


def get_message(
    conn: sqlite3.Connection,
    *,
    conversation_id: str,
    message_id: str,
) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT id, conversation_id, sequence, role, content, metadata_json, created_at
        FROM assistant_messages
        WHERE conversation_id = ? AND id = ?
        """,
        (conversation_id, message_id),
    ).fetchone()
    return _message_row(row) if row else None


def previous_user_message(
    conn: sqlite3.Connection,
    *,
    conversation_id: str,
    before_sequence: int,
) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT id, conversation_id, sequence, role, content, metadata_json, created_at
        FROM assistant_messages
        WHERE conversation_id = ? AND sequence < ? AND role = 'user'
        ORDER BY sequence DESC
        LIMIT 1
        """,
        (conversation_id, before_sequence),
    ).fetchone()
    return _message_row(row) if row else None


def recent_messages(
    conn: sqlite3.Connection,
    *,
    conversation_id: str,
    limit: int,
) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT id, conversation_id, sequence, role, content, metadata_json, created_at
        FROM assistant_messages
        WHERE conversation_id = ?
        ORDER BY sequence DESC
        LIMIT ?
        """,
        (conversation_id, limit),
    ).fetchall()
    return [_message_row(row) for row in reversed(rows)]


def recent_messages_before_sequence(
    conn: sqlite3.Connection,
    *,
    conversation_id: str,
    before_sequence: int,
    limit: int,
) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT id, conversation_id, sequence, role, content, metadata_json, created_at
        FROM assistant_messages
        WHERE conversation_id = ? AND sequence < ?
        ORDER BY sequence DESC
        LIMIT ?
        """,
        (conversation_id, before_sequence, limit),
    ).fetchall()
    return [_message_row(row) for row in reversed(rows)]


def get_state(
    conn: sqlite3.Connection,
    *,
    conversation_id: str,
) -> dict[str, Any]:
    row = conn.execute(
        """
        SELECT conversation_id, rolling_summary, slots_json,
               summarized_until_sequence, updated_at
        FROM assistant_conversation_state
        WHERE conversation_id = ?
        """,
        (conversation_id,),
    ).fetchone()
    return _state_row(row, conversation_id)


def update_state(
    conn: sqlite3.Connection,
    *,
    conversation_id: str,
    rolling_summary: str,
    slots: dict[str, Any],
    summarized_until_sequence: int,
    updated_at: str,
) -> dict[str, Any]:
    conn.execute(
        """
        INSERT INTO assistant_conversation_state(
            conversation_id, rolling_summary, slots_json, summarized_until_sequence, updated_at
        )
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(conversation_id) DO UPDATE SET
            rolling_summary = excluded.rolling_summary,
            slots_json = excluded.slots_json,
            summarized_until_sequence = excluded.summarized_until_sequence,
            updated_at = excluded.updated_at
        """,
        (
            conversation_id,
            rolling_summary,
            dumps_json(slots),
            summarized_until_sequence,
            updated_at,
        ),
    )
    return get_state(conn, conversation_id=conversation_id)


def messages_in_sequence_range(
    conn: sqlite3.Connection,
    *,
    conversation_id: str,
    after_sequence: int,
    through_sequence: int,
) -> list[dict[str, Any]]:
    if through_sequence <= after_sequence:
        return []
    rows = conn.execute(
        """
        SELECT id, conversation_id, sequence, role, content, metadata_json, created_at
        FROM assistant_messages
        WHERE conversation_id = ?
          AND sequence > ?
          AND sequence <= ?
        ORDER BY sequence ASC
        """,
        (conversation_id, after_sequence, through_sequence),
    ).fetchall()
    return [_message_row(row) for row in rows]
