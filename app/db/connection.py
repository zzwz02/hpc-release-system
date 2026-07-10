"""SQLite connection management for the FastAPI service.

The legacy ``cicd_tasks`` table is still created so old DB files and frozen
reference tests can open cleanly. Cutover runtime code derives CICD task-shaped
API rows from ``apps`` plus snapshots, and persists workflow state in
``cicd_task_requests``.

Schema additions kept for current runtime:
  - cicd_task_requests.app_id TEXT REFERENCES apps(id) ON DELETE CASCADE
  - cicd_task_requests.origin TEXT column
  - qa_logs.content BLOB (legacy storage_path is retained only for migration)
  - Online-ALTER columns folded into base DDL (ALTER loop kept for idempotency)
  - Additional indexes
"""
from __future__ import annotations

import sqlite3
import threading
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

_INIT_LOCK = threading.Lock()
_INITIALIZED_DBS: set[str] = set()


class ManagedConnection(sqlite3.Connection):
    """SQLite connection that can defer helper-level commits in transaction()."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._transaction_depth = 0

    def commit(self) -> None:
        if self._transaction_depth:
            return
        super().commit()

    def commit_now(self) -> None:
        super().commit()


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[None]:
    """Commit once on success and rollback all pending writes on failure.

    Connections returned by connect() suppress nested helper commit() calls
    inside this context, so helpers that still call commit() cannot split
    the transaction.

    SAVEPOINT nesting: if a transaction() is already active on a
    ManagedConnection, opens a SAVEPOINT instead of a plain BEGIN so that
    the inner block is atomic relative to the outer one.
    """
    if not isinstance(conn, ManagedConnection):
        try:
            yield
        except Exception:
            conn.rollback()
            raise
        else:
            conn.commit()
        return

    if conn._transaction_depth:
        savepoint = f"app_tx_{conn._transaction_depth}_{uuid.uuid4().hex}"
        conn.execute(f"SAVEPOINT {savepoint}")
        conn._transaction_depth += 1
        try:
            yield
        except Exception:
            conn.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
            conn.execute(f"RELEASE SAVEPOINT {savepoint}")
            raise
        else:
            conn.execute(f"RELEASE SAVEPOINT {savepoint}")
        finally:
            conn._transaction_depth -= 1
        return

    conn._transaction_depth = 1
    try:
        if not conn.in_transaction:
            conn.execute("BEGIN")
        yield
    except Exception:
        conn.rollback()
        raise
    else:
        conn.commit_now()
    finally:
        conn._transaction_depth = 0


def backup_sqlite(src_path: str | Path, dest_path: str | Path) -> None:
    """Write a consistent backup of the SQLite db at *src_path* to *dest_path*.

    Uses the SQLite online-backup API rather than a file copy: in WAL mode a
    plain file copy can miss committed transactions still in the -wal file and
    skips the -wal/-shm sidecars, producing a silently stale backup.
    """
    source = sqlite3.connect(src_path)
    try:
        dest = sqlite3.connect(dest_path)
        try:
            source.backup(dest)
        finally:
            dest.close()
    finally:
        source.close()


def connect(path: str | Path = "release_system.db") -> sqlite3.Connection:
    """Open (or create) the SQLite database at *path*.

    Per-request: one connection, no pooling.  The caller must close() it in a
    finally block (see app/deps.py get_db).

    WAL mode + foreign_keys + busy_timeout are set unconditionally.
    init_db() is called exactly once per resolved path (once-guard).
    """
    conn = sqlite3.connect(path, timeout=10.0, factory=ManagedConnection, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    key = str(Path(path).resolve()) if not str(path).startswith(":") else str(path)
    with _INIT_LOCK:
        if key not in _INITIALIZED_DBS:
            try:
                conn.execute("PRAGMA journal_mode = WAL")
            except sqlite3.OperationalError:
                pass  # in-memory DBs don't support WAL
            init_db(conn)
            _INITIALIZED_DBS.add(key)
    return conn


def reset_init_state() -> None:
    """Reset the initialized-db tracker; used by tests when cycling DBs."""
    with _INIT_LOCK:
        _INITIALIZED_DBS.clear()


def init_db(conn: sqlite3.Connection) -> None:
    """Create all tables and indexes.  Safe to call on an existing database.

    Columns introduced after initial deployment are still present in the base
    DDL here; the tolerant ALTER loop below keeps things idempotent when
    running against an older DB that pre-dates this rewrite.

    Legacy cicd_tasks columns/indexes are kept only for old DB compatibility
    and frozen reference tests. Current CICD runtime is app-backed.

    Current runtime additions:
      - cicd_task_requests.origin  — 'cicd_workbench' | 'release_decision_sync'
      - Additional performance indexes
    """
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS apps (
            id TEXT PRIMARY KEY,
            git_url TEXT NOT NULL DEFAULT '',
            git_branch TEXT NOT NULL DEFAULT '',
            aliases_json TEXT NOT NULL DEFAULT '[]',
            created_by TEXT NOT NULL DEFAULT 'import',
            created_at TEXT NOT NULL DEFAULT '',
            cicd_repo_type TEXT NOT NULL DEFAULT '',
            cicd_community_artifact TEXT NOT NULL DEFAULT '',
            cicd_build_image TEXT NOT NULL DEFAULT '',
            cicd_test_timeout TEXT NOT NULL DEFAULT '',
            cicd_notes TEXT NOT NULL DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS releases (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            maca_version TEXT NOT NULL DEFAULT '',
            app_freeze_deadline TEXT NOT NULL DEFAULT '',
            doc_deadline TEXT NOT NULL DEFAULT '',
            released_locked INTEGER NOT NULL DEFAULT 0,
            released_locked_at TEXT NOT NULL DEFAULT '',
            released_locked_by TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            source TEXT NOT NULL,
            cloned_from TEXT NOT NULL DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS snapshots (
            release_id TEXT NOT NULL REFERENCES releases(id) ON DELETE CASCADE,
            app_id TEXT NOT NULL REFERENCES apps(id) ON DELETE CASCADE,
            data_json TEXT NOT NULL,
            PRIMARY KEY (release_id, app_id)
        );

        CREATE TABLE IF NOT EXISTS artifacts (
            release_id TEXT NOT NULL REFERENCES releases(id) ON DELETE CASCADE,
            kind TEXT NOT NULL,
            name TEXT NOT NULL,
            content TEXT NOT NULL,
            final INTEGER NOT NULL DEFAULT 0,
            generated_at TEXT NOT NULL,
            PRIMARY KEY (release_id, kind)
        );

        CREATE TABLE IF NOT EXISTS qa_logs (
            release_id TEXT PRIMARY KEY REFERENCES releases(id) ON DELETE CASCADE,
            filename TEXT NOT NULL,
            content BLOB,
            storage_path TEXT NOT NULL DEFAULT '',
            uploaded_at TEXT NOT NULL,
            uploaded_by TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS audit (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            user TEXT NOT NULL,
            role TEXT NOT NULL,
            app_id TEXT NOT NULL DEFAULT '',
            release_id TEXT NOT NULL DEFAULT '',
            event TEXT NOT NULL DEFAULT '',
            message TEXT NOT NULL,
            detail TEXT NOT NULL DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS users (
            username TEXT PRIMARY KEY,
            password_hash TEXT NOT NULL DEFAULT '',
            role TEXT NOT NULL,
            auth_source TEXT NOT NULL DEFAULT 'local',
            display_name TEXT NOT NULL DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS sessions (
            token TEXT PRIMARY KEY,
            username TEXT NOT NULL REFERENCES users(username) ON DELETE CASCADE,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS release_schedule (
            id TEXT PRIMARY KEY,
            version TEXT NOT NULL,
            branch_cut_at TEXT NOT NULL DEFAULT '',
            release_at TEXT NOT NULL DEFAULT '',
            note TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            created_by TEXT NOT NULL,
            updated_at TEXT NOT NULL DEFAULT '',
            updated_by TEXT NOT NULL DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS cicd_tasks (
            id TEXT PRIMARY KEY,
            app_name TEXT NOT NULL,
            app_id TEXT REFERENCES apps(id) ON DELETE SET NULL,
            app_version TEXT NOT NULL DEFAULT '',
            repo_type TEXT NOT NULL DEFAULT 'git',
            repo_name TEXT NOT NULL DEFAULT '',
            branch TEXT NOT NULL DEFAULT '',
            build_product TEXT NOT NULL DEFAULT '[]',
            community_artifact TEXT NOT NULL DEFAULT '[]',
            build_image TEXT NOT NULL DEFAULT '',
            test_timeout INTEGER NOT NULL DEFAULT 40,
            owner_username TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'Running',
            notes TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        -- Legacy lookup index; current runtime derives task-shaped rows from apps.
        CREATE INDEX IF NOT EXISTS idx_cicd_tasks_repo
            ON cicd_tasks(repo_name, branch);
        -- NOTE: the partial UNIQUE index on cicd_tasks(app_id) is created AFTER the
        -- tolerant ALTER loop below (NOT here).  Creating it inside this
        -- executescript would reference app_id before the ALTER adds it on an
        -- old-schema DB and raise "no such column: app_id" (Phase-0 review HIGH fix).

        CREATE TABLE IF NOT EXISTS cicd_task_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id TEXT,
            app_id TEXT REFERENCES apps(id) ON DELETE CASCADE,
            request_type TEXT NOT NULL DEFAULT 'create',
            payload TEXT NOT NULL DEFAULT '{}',
            submitter TEXT NOT NULL,
            submitter_display TEXT NOT NULL DEFAULT '',
            submitted_at TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            reviewer TEXT NOT NULL DEFAULT '',
            reviewed_at TEXT NOT NULL DEFAULT '',
            review_note TEXT NOT NULL DEFAULT '',
            is_self_approved INTEGER NOT NULL DEFAULT 0,
            approval_mode TEXT NOT NULL DEFAULT 'immediate',
            delivery_status TEXT NOT NULL DEFAULT '',
            jira_id TEXT NOT NULL DEFAULT '',
            jira_auto_created INTEGER NOT NULL DEFAULT 0,
            delivered_by TEXT NOT NULL DEFAULT '',
            delivered_at TEXT NOT NULL DEFAULT '',
            returned_reason TEXT NOT NULL DEFAULT '',
            returned_at TEXT NOT NULL DEFAULT '',
            -- Phase 0 addition: distinguishes workbench requests from
            -- decision-sync requests ('cicd_workbench' | 'release_decision_sync')
            origin TEXT NOT NULL DEFAULT 'cicd_workbench'
        );

        -- Performance indexes for common query patterns (plan §4.1)
        CREATE INDEX IF NOT EXISTS idx_cicd_task_requests_status_delivery
            ON cicd_task_requests(status, delivery_status);

        CREATE INDEX IF NOT EXISTS idx_cicd_task_requests_task_id
            ON cicd_task_requests(task_id);

        CREATE TABLE IF NOT EXISTS cicd_notifications (
            username TEXT NOT NULL,
            last_visited_at TEXT NOT NULL DEFAULT '',
            PRIMARY KEY (username)
        );

        -- Additional indexes (plan §4.1)
        CREATE INDEX IF NOT EXISTS idx_audit_app_release
            ON audit(app_id, release_id);

        CREATE INDEX IF NOT EXISTS idx_snapshots_app_id
            ON snapshots(app_id);
        """
    )

    # Wiki tables (mirrors release_system/wiki/core.py init_db)
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS wiki_articles (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            body_md TEXT NOT NULL DEFAULT '',
            pinned INTEGER NOT NULL DEFAULT 0,
            created_by TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_by TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL DEFAULT '',
            deleted INTEGER NOT NULL DEFAULT 0,
            deleted_by TEXT NOT NULL DEFAULT '',
            deleted_at TEXT NOT NULL DEFAULT ''
        );

        CREATE INDEX IF NOT EXISTS idx_wiki_articles_visible
            ON wiki_articles(deleted, pinned, updated_at);

        CREATE TABLE IF NOT EXISTS wiki_images (
            id TEXT PRIMARY KEY,
            filename TEXT NOT NULL DEFAULT '',
            content_type TEXT NOT NULL,
            content BLOB NOT NULL,
            uploaded_by TEXT NOT NULL,
            uploaded_at TEXT NOT NULL
        );
        """
    )

    _ensure_default_user(conn)

    # --- Tolerant online-ALTER loop (idempotent) -----------------------------------
    # These columns are already in the base DDL above.  This loop is kept so that
    # the new app/db/connection.py can safely be pointed at an older DB produced by
    # the original release_system/core.py without crashing.
    for _col, _col_def in [
        ("auth_source", "TEXT NOT NULL DEFAULT 'local'"),
        ("display_name", "TEXT NOT NULL DEFAULT ''"),
    ]:
        try:
            conn.execute(f"ALTER TABLE users ADD COLUMN {_col} {_col_def}")
        except sqlite3.OperationalError:
            pass  # column already exists

    for _tbl, _col, _col_def in [
        ("apps", "cicd_repo_type", "TEXT NOT NULL DEFAULT ''"),
        ("apps", "cicd_community_artifact", "TEXT NOT NULL DEFAULT ''"),
        ("apps", "cicd_build_image", "TEXT NOT NULL DEFAULT ''"),
        ("apps", "cicd_test_timeout", "TEXT NOT NULL DEFAULT ''"),
        ("apps", "cicd_notes", "TEXT NOT NULL DEFAULT ''"),
        ("cicd_task_requests", "approval_mode",    "TEXT NOT NULL DEFAULT 'immediate'"),
        ("cicd_task_requests", "delivery_status",  "TEXT NOT NULL DEFAULT ''"),
        ("cicd_task_requests", "jira_id",           "TEXT NOT NULL DEFAULT ''"),
        ("cicd_task_requests", "jira_auto_created", "INTEGER NOT NULL DEFAULT 0"),
        ("cicd_task_requests", "delivered_by",     "TEXT NOT NULL DEFAULT ''"),
        ("cicd_task_requests", "delivered_at",     "TEXT NOT NULL DEFAULT ''"),
        ("cicd_task_requests", "returned_reason",  "TEXT NOT NULL DEFAULT ''"),
        ("cicd_task_requests", "returned_at",      "TEXT NOT NULL DEFAULT ''"),
        ("cicd_task_requests", "app_id",           "TEXT REFERENCES apps(id) ON DELETE CASCADE"),
        ("cicd_tasks",         "community_artifact", "TEXT NOT NULL DEFAULT '[]'"),
        # Legacy table columns — tolerate older DBs
        ("cicd_tasks",         "app_id",            "TEXT REFERENCES apps(id) ON DELETE SET NULL"),
        ("cicd_task_requests", "origin",             "TEXT NOT NULL DEFAULT 'cicd_workbench'"),
        # ``storage_path`` stays in old and new DBs only so startup can import
        # pre-cutover files. Current runtime writes the file body to ``content``.
        ("qa_logs",            "content",            "BLOB"),
    ]:
        try:
            conn.execute(f"ALTER TABLE {_tbl} ADD COLUMN {_col} {_col_def}")
        except sqlite3.OperationalError:
            pass  # column already exists

    # Legacy index retained so old DBs remain openable; app-backed CICD runtime
    # does not write cicd_tasks.
    try:
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_cicd_tasks_app_id_unique "
            "ON cicd_tasks(app_id) WHERE app_id IS NOT NULL"
        )
    except sqlite3.OperationalError:
        pass

    _migrate_qa_log_files(conn)

    conn.commit()


def _migrate_qa_log_files(conn: sqlite3.Connection) -> None:
    """Copy pre-cutover QA log files into ``qa_logs.content``.

    The legacy table stored only an absolute path.  Migration is deliberately
    non-destructive: successfully imported files are left in place so an
    operator can verify the upgraded database before removing ``qa_logs/``.
    Missing or unreadable files leave ``content`` as NULL and do not prevent
    the application from starting.
    """
    columns = {
        row["name"] if isinstance(row, sqlite3.Row) else row[1]
        for row in conn.execute("PRAGMA table_info(qa_logs)")
    }
    if "content" not in columns or "storage_path" not in columns:
        return

    rows = conn.execute(
        "SELECT release_id, storage_path FROM qa_logs "
        "WHERE content IS NULL AND storage_path <> ''"
    ).fetchall()
    for row in rows:
        release_id = row["release_id"] if isinstance(row, sqlite3.Row) else row[0]
        storage_path = row["storage_path"] if isinstance(row, sqlite3.Row) else row[1]
        path = Path(storage_path)
        try:
            if not path.is_file():
                continue
            content = path.read_bytes()
        except OSError:
            continue
        conn.execute(
            "UPDATE qa_logs SET content = ? WHERE release_id = ? AND content IS NULL",
            (content, release_id),
        )


_DEFAULT_USERS: tuple[tuple[str, str, str], ...] = (
    ("rm", "rm", "RM"),
    ("owner_test", "owner_test", "Owner"),
    ("qa", "qa", "QA"),
    ("spd_test", "spd_test", "SPD"),
    ("guest", "guest", "Guest"),
)


def _ensure_default_user(conn: sqlite3.Connection) -> None:
    """Ensure built-in dev users exist without overwriting existing accounts."""
    import hashlib
    import secrets as _secrets

    def _hash(pw: str) -> str:
        salt = _secrets.token_hex(16)
        digest = hashlib.pbkdf2_hmac(
            "sha256", pw.encode(), salt.encode(), 120_000
        ).hex()
        return f"{salt}${digest}"

    for username, password, role in _DEFAULT_USERS:
        conn.execute(
            "INSERT OR IGNORE INTO users(username, password_hash, role) VALUES (?,?,?)",
            (username, _hash(password), role),
        )
