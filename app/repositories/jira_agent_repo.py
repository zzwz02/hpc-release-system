"""Persistence for JIRA agent conversations, turns, timeline events and files.

Module-level functions fn(conn, ...); callers wrap writes in transaction().
The queued rows of jira_agent_turns are the persistent FIFO queue (rowid order).
"""
from __future__ import annotations

import sqlite3
from typing import Any

from app.repositories.base import dumps_json, loads_json, new_id, row_to_dict
from app.timeutil import beijing_timestamp

ACTIVE_TURN_STATUSES = ("queued", "running")

_TURN_UPDATABLE = {
    "status",
    "codex_turn_id",
    "result_json",
    "conclusion",
    "comment_status",
    "post_comment",
    "comment_id",
    "comment_body",
    "comment_error",
    "error",
    "started_at",
    "finished_at",
}


def _one(conn: sqlite3.Connection, sql: str, params: tuple) -> dict | None:
    row = conn.execute(sql, params).fetchone()
    return row_to_dict(row) if row else None


def _all(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> list[dict]:
    return [row_to_dict(row) for row in conn.execute(sql, params).fetchall()]


# ─────────────────────────────────────────────────────────────
# Conversations
# ─────────────────────────────────────────────────────────────

def create_conversation(
    conn: sqlite3.Connection,
    *,
    conversation_id: str,
    issue_key: str,
    issue_summary: str,
    agent_group: str,
    owner: str,
    created_by: str,
    workspace: str,
    machine: str = "",
) -> dict:
    now = beijing_timestamp()
    conn.execute(
        """
        INSERT INTO jira_agent_conversations
            (id, issue_key, issue_summary, agent_group, owner, created_by,
             workspace, machine, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (conversation_id, issue_key, issue_summary, agent_group, owner,
         created_by, workspace, machine, now, now),
    )
    return get_conversation(conn, conversation_id)  # type: ignore[return-value]


def get_conversation(conn: sqlite3.Connection, conversation_id: str) -> dict | None:
    return _one(conn, "SELECT * FROM jira_agent_conversations WHERE id = ?", (conversation_id,))


def get_open_conversation(conn: sqlite3.Connection, issue_key: str) -> dict | None:
    return _one(
        conn,
        "SELECT * FROM jira_agent_conversations WHERE issue_key = ? AND status = 'open'",
        (issue_key,),
    )


def open_conversations_by_issue(conn: sqlite3.Connection, issue_keys: list[str]) -> dict[str, dict]:
    if not issue_keys:
        return {}
    marks = ",".join("?" * len(issue_keys))
    rows = _all(
        conn,
        f"SELECT * FROM jira_agent_conversations WHERE status = 'open' AND issue_key IN ({marks})",
        tuple(issue_keys),
    )
    return {row["issue_key"]: row for row in rows}


def list_conversations(
    conn: sqlite3.Connection,
    *,
    username: str | None = None,
    issue_key: str | None = None,
    limit: int = 200,
) -> list[dict]:
    clauses: list[str] = []
    params: list[Any] = []
    if username is not None:
        clauses.append("(owner = ? COLLATE NOCASE OR created_by = ? COLLATE NOCASE)")
        params += [username, username]
    if issue_key is not None:
        clauses.append("issue_key = ?")
        params.append(issue_key)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    return _all(
        conn,
        f"""
        SELECT * FROM jira_agent_conversations {where}
        ORDER BY updated_at DESC, rowid DESC
        LIMIT ?
        """,
        (*params, limit),
    )


def handled_issues(conn: sqlite3.Connection, *, limit: int) -> tuple[int, list[dict]]:
    """Issues ever handed to the agent, most recent agent activity first.

    Returns (total issue count, rows of issue_key / conversation_count /
    last_activity for the first `limit` issues).
    """
    total = conn.execute(
        "SELECT COUNT(DISTINCT issue_key) FROM jira_agent_conversations"
    ).fetchone()[0]
    rows = _all(
        conn,
        """
        SELECT issue_key, COUNT(*) AS conversation_count, MAX(updated_at) AS last_activity
        FROM jira_agent_conversations
        GROUP BY issue_key
        ORDER BY last_activity DESC, MAX(rowid) DESC
        LIMIT ?
        """,
        (limit,),
    )
    return int(total), rows


def touch_conversation(conn: sqlite3.Connection, conversation_id: str) -> None:
    conn.execute(
        "UPDATE jira_agent_conversations SET updated_at = ? WHERE id = ?",
        (beijing_timestamp(), conversation_id),
    )


def set_thread_id(conn: sqlite3.Connection, conversation_id: str, thread_id: str) -> None:
    conn.execute(
        "UPDATE jira_agent_conversations SET thread_id = ?, updated_at = ? WHERE id = ?",
        (thread_id, beijing_timestamp(), conversation_id),
    )


def close_conversation(conn: sqlite3.Connection, conversation_id: str, reason: str) -> bool:
    now = beijing_timestamp()
    cur = conn.execute(
        """
        UPDATE jira_agent_conversations
        SET status = 'closed', close_reason = ?, closed_at = ?, updated_at = ?
        WHERE id = ? AND status = 'open'
        """,
        (reason, now, now, conversation_id),
    )
    return cur.rowcount == 1


def mark_thread_archived(conn: sqlite3.Connection, conversation_id: str) -> None:
    conn.execute(
        "UPDATE jira_agent_conversations SET thread_archived = 1 WHERE id = ?",
        (conversation_id,),
    )


# ─────────────────────────────────────────────────────────────
# Turns
# ─────────────────────────────────────────────────────────────

def create_turn(
    conn: sqlite3.Connection,
    *,
    conversation_id: str,
    trigger: str,
    created_by: str,
    input_text: str,
    post_comment: bool = True,
) -> dict:
    seq = conn.execute(
        "SELECT COALESCE(MAX(seq), 0) + 1 FROM jira_agent_turns WHERE conversation_id = ?",
        (conversation_id,),
    ).fetchone()[0]
    turn_id = new_id("jat")
    conn.execute(
        """
        INSERT INTO jira_agent_turns
            (id, conversation_id, seq, trigger, created_by, input_text, post_comment, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            turn_id, conversation_id, seq, trigger, created_by, input_text,
            int(post_comment), beijing_timestamp(),
        ),
    )
    touch_conversation(conn, conversation_id)
    return get_turn(conn, turn_id)  # type: ignore[return-value]


def get_turn(conn: sqlite3.Connection, turn_id: str) -> dict | None:
    return _one(conn, "SELECT * FROM jira_agent_turns WHERE id = ?", (turn_id,))


def list_turns(conn: sqlite3.Connection, conversation_id: str) -> list[dict]:
    return _all(
        conn,
        "SELECT * FROM jira_agent_turns WHERE conversation_id = ? ORDER BY seq",
        (conversation_id,),
    )


def latest_turn(conn: sqlite3.Connection, conversation_id: str) -> dict | None:
    return _one(
        conn,
        "SELECT * FROM jira_agent_turns WHERE conversation_id = ? ORDER BY seq DESC LIMIT 1",
        (conversation_id,),
    )


def active_turn(conn: sqlite3.Connection, conversation_id: str) -> dict | None:
    return _one(
        conn,
        """
        SELECT * FROM jira_agent_turns
        WHERE conversation_id = ? AND status IN ('queued', 'running')
        """,
        (conversation_id,),
    )


def append_turn_input(conn: sqlite3.Connection, turn_id: str, text: str) -> None:
    if not text:
        return
    conn.execute(
        """
        UPDATE jira_agent_turns
        SET input_text = CASE WHEN input_text = '' THEN ?
                              ELSE input_text || char(10) || char(10) || ? END
        WHERE id = ?
        """,
        (text, text, turn_id),
    )


def claim_turn(conn: sqlite3.Connection, turn_id: str) -> bool:
    cur = conn.execute(
        """
        UPDATE jira_agent_turns SET status = 'running', started_at = ?
        WHERE id = ? AND status = 'queued'
        """,
        (beijing_timestamp(), turn_id),
    )
    return cur.rowcount == 1


def cancel_queued_turn(conn: sqlite3.Connection, turn_id: str, error: str) -> bool:
    cur = conn.execute(
        """
        UPDATE jira_agent_turns SET status = 'cancelled', error = ?, finished_at = ?
        WHERE id = ? AND status = 'queued'
        """,
        (error, beijing_timestamp(), turn_id),
    )
    return cur.rowcount == 1


def update_turn(conn: sqlite3.Connection, turn_id: str, **fields: Any) -> None:
    unknown = set(fields) - _TURN_UPDATABLE
    if unknown:
        raise ValueError(f"unknown turn fields: {sorted(unknown)}")
    if not fields:
        return
    assignments = ", ".join(f"{name} = ?" for name in fields)
    conn.execute(
        f"UPDATE jira_agent_turns SET {assignments} WHERE id = ?",
        (*fields.values(), turn_id),
    )


def queued_groups(conn: sqlite3.Connection) -> list[str]:
    return [
        row[0]
        for row in conn.execute(
            """
            SELECT DISTINCT c.agent_group
            FROM jira_agent_turns t JOIN jira_agent_conversations c ON c.id = t.conversation_id
            WHERE t.status = 'queued'
            """
        ).fetchall()
    ]


def queued_turns(conn: sqlite3.Connection, agent_group: str) -> list[dict]:
    return _all(
        conn,
        """
        SELECT t.* FROM jira_agent_turns t
        JOIN jira_agent_conversations c ON c.id = t.conversation_id
        WHERE t.status = 'queued' AND c.agent_group = ?
        ORDER BY t.rowid
        """,
        (agent_group,),
    )


def queue_position(conn: sqlite3.Connection, turn_id: str) -> int:
    """Turns ahead of a queued turn in its group: running ones plus earlier queued."""
    row = conn.execute(
        """
        SELECT
          (SELECT COUNT(*) FROM jira_agent_turns t2
             JOIN jira_agent_conversations c2 ON c2.id = t2.conversation_id
            WHERE c2.agent_group = c.agent_group AND t2.status = 'running')
          +
          (SELECT COUNT(*) FROM jira_agent_turns t3
             JOIN jira_agent_conversations c3 ON c3.id = t3.conversation_id
            WHERE c3.agent_group = c.agent_group AND t3.status = 'queued'
              AND t3.rowid < t.rowid)
        FROM jira_agent_turns t JOIN jira_agent_conversations c ON c.id = t.conversation_id
        WHERE t.id = ?
        """,
        (turn_id,),
    ).fetchone()
    return int(row[0]) if row else 0


def set_machine_used(conn: sqlite3.Connection, conversation_id: str, machine: str) -> None:
    """Record the machine the agent works on, as soon as one of its commands shows it."""
    conn.execute(
        "UPDATE jira_agent_conversations SET machine_used = ? WHERE id = ?",
        (machine, conversation_id),
    )


def running_turns(conn: sqlite3.Connection) -> list[dict]:
    """Startup recovery: turns a previous process left running on the app-server."""
    return _all(conn, "SELECT * FROM jira_agent_turns WHERE status = 'running' ORDER BY rowid")


def requeue_turn(conn: sqlite3.Connection, turn_id: str) -> bool:
    """Put a turn that never reached the app-server back at its queue position."""
    cur = conn.execute(
        """
        UPDATE jira_agent_turns SET status = 'queued', started_at = ''
        WHERE id = ? AND status = 'running'
        """,
        (turn_id,),
    )
    return cur.rowcount == 1


def pending_comment_turns(conn: sqlite3.Connection) -> list[dict]:
    return _all(
        conn,
        """
        SELECT * FROM jira_agent_turns
        WHERE status = 'completed' AND comment_status = 'pending'
        ORDER BY rowid
        """,
    )


# ─────────────────────────────────────────────────────────────
# Timeline events
# ─────────────────────────────────────────────────────────────

def _event_view(row: dict) -> dict:
    row["payload"] = loads_json(row.pop("payload_json"), {})
    return row


def add_event(
    conn: sqlite3.Connection,
    *,
    conversation_id: str,
    kind: str,
    payload: dict,
    turn_id: str = "",
    item_id: str = "",
) -> dict:
    """Insert an event, or update the one with the same item_id in place."""
    now = beijing_timestamp()
    rev = conn.execute(
        "SELECT COALESCE(MAX(rev), 0) + 1 FROM jira_agent_events WHERE conversation_id = ?",
        (conversation_id,),
    ).fetchone()[0]
    existing = None
    if item_id:
        existing = _one(
            conn,
            "SELECT id FROM jira_agent_events WHERE conversation_id = ? AND item_id = ?",
            (conversation_id, item_id),
        )
    if existing:
        conn.execute(
            """
            UPDATE jira_agent_events SET kind = ?, payload_json = ?, rev = ?, updated_at = ?
            WHERE id = ?
            """,
            (kind, dumps_json(payload), rev, now, existing["id"]),
        )
        event_id = existing["id"]
    else:
        seq = conn.execute(
            "SELECT COALESCE(MAX(seq), 0) + 1 FROM jira_agent_events WHERE conversation_id = ?",
            (conversation_id,),
        ).fetchone()[0]
        event_id = new_id("jae")
        conn.execute(
            """
            INSERT INTO jira_agent_events
                (id, conversation_id, turn_id, seq, rev, item_id, kind, payload_json,
                 created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (event_id, conversation_id, turn_id, seq, rev, item_id, kind,
             dumps_json(payload), now, now),
        )
    touch_conversation(conn, conversation_id)
    return _event_view(_one(conn, "SELECT * FROM jira_agent_events WHERE id = ?", (event_id,)))  # type: ignore[arg-type]


def list_events(conn: sqlite3.Connection, conversation_id: str, *, after_rev: int = 0) -> list[dict]:
    return [
        _event_view(row)
        for row in _all(
            conn,
            """
            SELECT * FROM jira_agent_events
            WHERE conversation_id = ? AND rev > ?
            ORDER BY seq
            """,
            (conversation_id, after_rev),
        )
    ]


def max_event_rev(conn: sqlite3.Connection, conversation_id: str) -> int:
    return int(
        conn.execute(
            "SELECT COALESCE(MAX(rev), 0) FROM jira_agent_events WHERE conversation_id = ?",
            (conversation_id,),
        ).fetchone()[0]
    )


# ─────────────────────────────────────────────────────────────
# Files
# ─────────────────────────────────────────────────────────────

def add_file(
    conn: sqlite3.Connection,
    *,
    conversation_id: str,
    turn_id: str,
    direction: str,
    source: str,
    name: str,
    size: int,
    sha256: str = "",
    source_ref: str = "",
    local_path: str = "",
    remote_path: str = "",
) -> dict:
    file_id = new_id("jaf")
    conn.execute(
        """
        INSERT INTO jira_agent_files
            (id, conversation_id, turn_id, direction, source, source_ref, name, size,
             sha256, local_path, remote_path, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (file_id, conversation_id, turn_id, direction, source, source_ref, name, size,
         sha256, local_path, remote_path, beijing_timestamp()),
    )
    return get_file(conn, file_id)  # type: ignore[return-value]


def get_file(conn: sqlite3.Connection, file_id: str) -> dict | None:
    return _one(conn, "SELECT * FROM jira_agent_files WHERE id = ?", (file_id,))


def list_files(conn: sqlite3.Connection, conversation_id: str) -> list[dict]:
    return _all(
        conn,
        "SELECT * FROM jira_agent_files WHERE conversation_id = ? ORDER BY rowid",
        (conversation_id,),
    )


def jira_attachment_paths(conn: sqlite3.Connection, conversation_id: str) -> dict[str, str]:
    """Already-synced JIRA attachments: attachment id → workspace-relative path."""
    return {
        row["source_ref"]: row["remote_path"]
        for row in _all(
            conn,
            """
            SELECT source_ref, remote_path FROM jira_agent_files
            WHERE conversation_id = ? AND source = 'jira' AND remote_path != ''
            """,
            (conversation_id,),
        )
    }


def remote_paths(conn: sqlite3.Connection, conversation_id: str) -> set[str]:
    return {
        row["remote_path"]
        for row in _all(
            conn,
            "SELECT remote_path FROM jira_agent_files WHERE conversation_id = ? AND remote_path != ''",
            (conversation_id,),
        )
    }


def pending_uploads(conn: sqlite3.Connection, conversation_id: str) -> list[dict]:
    return _all(
        conn,
        """
        SELECT * FROM jira_agent_files
        WHERE conversation_id = ? AND source = 'upload' AND remote_path = ''
        ORDER BY rowid
        """,
        (conversation_id,),
    )


def set_file_remote_path(conn: sqlite3.Connection, file_id: str, remote_path: str) -> None:
    conn.execute(
        "UPDATE jira_agent_files SET remote_path = ? WHERE id = ?",
        (remote_path, file_id),
    )


# ─────────────────────────────────────────────────────────────
# Execution machines
# ─────────────────────────────────────────────────────────────

def list_machines(conn: sqlite3.Connection, agent_group: str | None = None) -> list[dict]:
    if agent_group is None:
        return _all(conn, "SELECT * FROM jira_agent_machines ORDER BY agent_group, ssh_target")
    return _all(
        conn,
        "SELECT * FROM jira_agent_machines WHERE agent_group = ? ORDER BY ssh_target",
        (agent_group,),
    )


def get_machine(conn: sqlite3.Connection, machine_id: str) -> dict | None:
    return _one(conn, "SELECT * FROM jira_agent_machines WHERE id = ?", (machine_id,))


def create_machine(
    conn: sqlite3.Connection, *, agent_group: str, ssh_target: str, description: str, created_by: str,
) -> dict:
    machine_id = new_id("jam")
    now = beijing_timestamp()
    conn.execute(
        """
        INSERT INTO jira_agent_machines
            (id, agent_group, ssh_target, description, created_by, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (machine_id, agent_group, ssh_target, description, created_by, now, now),
    )
    return get_machine(conn, machine_id)  # type: ignore[return-value]


def update_machine(conn: sqlite3.Connection, machine_id: str, *, ssh_target: str, description: str) -> bool:
    cur = conn.execute(
        "UPDATE jira_agent_machines SET ssh_target = ?, description = ?, updated_at = ? WHERE id = ?",
        (ssh_target, description, beijing_timestamp(), machine_id),
    )
    return cur.rowcount == 1


def delete_machine(conn: sqlite3.Connection, machine_id: str) -> bool:
    cur = conn.execute("DELETE FROM jira_agent_machines WHERE id = ?", (machine_id,))
    return cur.rowcount == 1


def record_ssh_key(
    conn: sqlite3.Connection, *, username: str, agent_group: str, ssh_target: str, key_fingerprint: str,
) -> None:
    conn.execute(
        """
        INSERT INTO jira_agent_ssh_keys
            (id, username, agent_group, ssh_target, key_fingerprint, verified_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT (username, agent_group, ssh_target, key_fingerprint)
        DO UPDATE SET verified_at = excluded.verified_at
        """,
        (new_id("jak"), username, agent_group, ssh_target, key_fingerprint, beijing_timestamp()),
    )


def list_ssh_keys(
    conn: sqlite3.Connection, *, username: str, agent_group: str, key_fingerprint: str,
) -> list[dict]:
    return _all(
        conn,
        """
        SELECT * FROM jira_agent_ssh_keys
        WHERE username = ? COLLATE NOCASE AND agent_group = ? AND key_fingerprint = ?
        ORDER BY verified_at DESC
        """,
        (username, agent_group, key_fingerprint),
    )


def has_ssh_key(
    conn: sqlite3.Connection, *, username: str, agent_group: str, ssh_target: str, key_fingerprint: str,
) -> bool:
    return any(
        row["ssh_target"] == ssh_target
        for row in list_ssh_keys(conn, username=username, agent_group=agent_group, key_fingerprint=key_fingerprint)
    )
