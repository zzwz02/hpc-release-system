"""Independent SQLite storage for CICD assistant conversations."""
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
            "assistant_database_url currently supports only sqlite:/// URLs"
        )
    raw_path = database_url[len(prefix):]
    path = Path(raw_path)
    if not path.is_absolute():
        path = _PROJECT_ROOT / path
    return path


def connect_assistant(database_url: str) -> sqlite3.Connection:
    """Open and lazily initialise the independent assistant database."""
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
            init_assistant_db(conn)
            _INITIALIZED_DBS.add(key)
    return conn


def reset_assistant_init_state() -> None:
    """Reset assistant DB init tracking for tests."""
    with _INIT_LOCK:
        _INITIALIZED_DBS.clear()


def init_assistant_db(conn: sqlite3.Connection) -> None:
    """Create CICD assistant tables. Safe to run repeatedly."""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS assistant_conversations (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            title TEXT NOT NULL DEFAULT '新会话',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            deleted_at TEXT NOT NULL DEFAULT '',
            message_count INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS assistant_messages (
            id TEXT PRIMARY KEY,
            conversation_id TEXT NOT NULL REFERENCES assistant_conversations(id) ON DELETE CASCADE,
            sequence INTEGER NOT NULL,
            role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
            content TEXT NOT NULL,
            metadata_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            UNIQUE (conversation_id, sequence)
        );

        CREATE TABLE IF NOT EXISTS assistant_conversation_state (
            conversation_id TEXT PRIMARY KEY
                REFERENCES assistant_conversations(id) ON DELETE CASCADE,
            rolling_summary TEXT NOT NULL DEFAULT '',
            slots_json TEXT NOT NULL DEFAULT '{}',
            summarized_until_sequence INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_assistant_conversations_user_updated
            ON assistant_conversations(user_id, deleted_at, updated_at DESC);

        CREATE INDEX IF NOT EXISTS idx_assistant_messages_conversation_sequence
            ON assistant_messages(conversation_id, sequence);
        """
    )
    _ensure_column(
        conn,
        "assistant_conversation_state",
        "summarized_until_sequence",
        "INTEGER NOT NULL DEFAULT 0",
    )
    conn.commit()


def _ensure_column(
    conn: sqlite3.Connection,
    table: str,
    column: str,
    ddl: str,
) -> None:
    columns = {
        row["name"]
        for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
    }
    if column not in columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
