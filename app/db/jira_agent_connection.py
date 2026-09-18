"""Independent SQLite storage for JIRA agent conversations, turns and events."""
from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

from app.db.connection import ManagedConnection

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_INIT_LOCK = threading.Lock()
_INITIALIZED_DBS: set[str] = set()


def _sqlite_path(database_url: str) -> str | Path:
    if database_url == "sqlite:///:memory:":
        return ":memory:"
    prefix = "sqlite:///"
    if not database_url.startswith(prefix):
        raise RuntimeError(
            "jira_agent_database_url currently supports only sqlite:/// URLs"
        )
    path = Path(database_url[len(prefix):])
    if not path.is_absolute():
        path = _PROJECT_ROOT / path
    return path


def connect_jira_agent(database_url: str) -> sqlite3.Connection:
    """Open and lazily initialise the independent JIRA agent database."""
    db_path = _sqlite_path(database_url)
    if db_path != ":memory:":
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(
        db_path,
        timeout=10.0,
        factory=ManagedConnection,
        check_same_thread=False,
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")

    key = str(Path(db_path).resolve()) if db_path != ":memory:" else str(db_path)
    with _INIT_LOCK:
        if key not in _INITIALIZED_DBS:
            try:
                conn.execute("PRAGMA journal_mode = WAL")
            except sqlite3.OperationalError:
                pass
            init_jira_agent_db(conn)
            _INITIALIZED_DBS.add(key)
    return conn


def reset_jira_agent_init_state() -> None:
    """Reset JIRA agent DB init tracking for tests."""
    with _INIT_LOCK:
        _INITIALIZED_DBS.clear()


def init_jira_agent_db(conn: sqlite3.Connection) -> None:
    """Create JIRA agent tables. Safe to run repeatedly."""
    conn.executescript(
        """
        -- One conversation = one Codex thread + one workspace on server B,
        -- owned by the JIRA assignee at hand-over time.
        CREATE TABLE IF NOT EXISTS jira_agent_conversations (
            id TEXT PRIMARY KEY,
            issue_key TEXT NOT NULL,
            issue_summary TEXT NOT NULL DEFAULT '',
            agent_group TEXT NOT NULL,
            owner TEXT NOT NULL,
            created_by TEXT NOT NULL,
            thread_id TEXT NOT NULL DEFAULT '',
            workspace TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'closed')),
            close_reason TEXT NOT NULL DEFAULT ''
                CHECK (close_reason IN ('', 'superseded', 'new_conversation')),
            thread_archived INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            closed_at TEXT NOT NULL DEFAULT ''
        );

        CREATE UNIQUE INDEX IF NOT EXISTS uq_jira_agent_conversations_open_issue
            ON jira_agent_conversations(issue_key) WHERE status = 'open';

        CREATE INDEX IF NOT EXISTS idx_jira_agent_conversations_owner
            ON jira_agent_conversations(owner, updated_at DESC);

        -- One turn = one Codex turn; queued rows are the persistent queue.
        CREATE TABLE IF NOT EXISTS jira_agent_turns (
            id TEXT PRIMARY KEY,
            conversation_id TEXT NOT NULL
                REFERENCES jira_agent_conversations(id) ON DELETE CASCADE,
            seq INTEGER NOT NULL,
            trigger TEXT NOT NULL CHECK (trigger IN ('handover', 'followup')),
            created_by TEXT NOT NULL,
            input_text TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'queued' CHECK (
                status IN ('queued', 'running', 'completed', 'failed', 'cancelled', 'interrupted')
            ),
            codex_turn_id TEXT NOT NULL DEFAULT '',
            result_json TEXT NOT NULL DEFAULT '',
            conclusion TEXT NOT NULL DEFAULT '',
            comment_status TEXT NOT NULL DEFAULT ''
                CHECK (comment_status IN ('', 'pending', 'posted', 'failed')),
            comment_id TEXT NOT NULL DEFAULT '',
            comment_body TEXT NOT NULL DEFAULT '',
            comment_error TEXT NOT NULL DEFAULT '',
            error TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            started_at TEXT NOT NULL DEFAULT '',
            finished_at TEXT NOT NULL DEFAULT '',
            UNIQUE (conversation_id, seq)
        );

        -- At most one queued-or-running turn per conversation.
        CREATE UNIQUE INDEX IF NOT EXISTS uq_jira_agent_turns_active
            ON jira_agent_turns(conversation_id) WHERE status IN ('queued', 'running');

        CREATE INDEX IF NOT EXISTS idx_jira_agent_turns_status_created
            ON jira_agent_turns(status, created_at);

        -- Timeline. seq is display order; rev increases on every insert/update
        -- so the browser can poll incrementally (commands update in place).
        CREATE TABLE IF NOT EXISTS jira_agent_events (
            id TEXT PRIMARY KEY,
            conversation_id TEXT NOT NULL
                REFERENCES jira_agent_conversations(id) ON DELETE CASCADE,
            turn_id TEXT NOT NULL DEFAULT '',
            seq INTEGER NOT NULL,
            rev INTEGER NOT NULL,
            item_id TEXT NOT NULL DEFAULT '',
            kind TEXT NOT NULL,
            payload_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE (conversation_id, seq)
        );

        CREATE UNIQUE INDEX IF NOT EXISTS uq_jira_agent_events_item
            ON jira_agent_events(conversation_id, item_id) WHERE item_id != '';

        CREATE INDEX IF NOT EXISTS idx_jira_agent_events_rev
            ON jira_agent_events(conversation_id, rev);

        CREATE TABLE IF NOT EXISTS jira_agent_files (
            id TEXT PRIMARY KEY,
            conversation_id TEXT NOT NULL
                REFERENCES jira_agent_conversations(id) ON DELETE CASCADE,
            turn_id TEXT NOT NULL DEFAULT '',
            direction TEXT NOT NULL CHECK (direction IN ('input', 'output')),
            source TEXT NOT NULL CHECK (source IN ('jira', 'upload', 'artifact')),
            source_ref TEXT NOT NULL DEFAULT '',
            name TEXT NOT NULL,
            size INTEGER NOT NULL DEFAULT 0,
            sha256 TEXT NOT NULL DEFAULT '',
            local_path TEXT NOT NULL DEFAULT '',
            remote_path TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_jira_agent_files_conversation
            ON jira_agent_files(conversation_id, created_at);

        -- System machine list per group, maintained by RM.  SSH access from
        -- server B is set up beforehand; the agent picks one per task.
        CREATE TABLE IF NOT EXISTS jira_agent_machines (
            id TEXT PRIMARY KEY,
            agent_group TEXT NOT NULL,
            ssh_target TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            created_by TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE (agent_group, ssh_target)
        );

        -- user@host a website user put server B's public key on (password
        -- terminal) with key login then verified from B.  Not system machines.
        CREATE TABLE IF NOT EXISTS jira_agent_ssh_keys (
            id TEXT PRIMARY KEY,
            username TEXT NOT NULL,
            agent_group TEXT NOT NULL,
            ssh_target TEXT NOT NULL,
            key_fingerprint TEXT NOT NULL,
            verified_at TEXT NOT NULL,
            UNIQUE (username, agent_group, ssh_target, key_fingerprint)
        );
        """
    )
    # '' = the agent picks from the system machine list; else user@host.
    _ensure_column(conn, "jira_agent_conversations", "machine", "TEXT NOT NULL DEFAULT ''")
    # what the agent is actually on: detected from its commands, then its result.
    _ensure_column(conn, "jira_agent_conversations", "machine_used", "TEXT NOT NULL DEFAULT ''")
    # 0 = keep this turn's result on the website only (no JIRA comment); the
    # choice sent with the turn's latest message wins.
    _ensure_column(conn, "jira_agent_turns", "post_comment", "INTEGER NOT NULL DEFAULT 1")
    conn.commit()


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, ddl: str) -> None:
    columns = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
