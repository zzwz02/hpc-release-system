"""SQLite connection management for the FastAPI service.

The legacy ``cicd_tasks`` table is still created so old DB files and frozen
reference tests can open cleanly. Cutover runtime code derives CICD task-shaped
API rows from ``apps``, and persists workflow state in
``cicd_task_requests``.

Schema additions kept for current runtime:
  - cicd_task_requests.app_id TEXT REFERENCES apps(id) ON DELETE CASCADE
  - cicd_task_requests.origin TEXT column
  - qa_logs.content BLOB (legacy storage_path is retained only for migration)
  - Online-ALTER columns folded into base DDL (ALTER loop kept for idempotency)
  - Additional indexes
"""
from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from app.domain import authn

_INIT_LOCK = threading.Lock()
_INITIALIZED_DBS: set[str] = set()


class ManagedConnection(sqlite3.Connection):
    """SQLite connection that can defer helper-level commits in transaction()."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._transaction_depth = 0
        self._after_commit: list[Any] = []

    def commit(self) -> None:
        if self._transaction_depth:
            return
        super().commit()

    def commit_now(self) -> None:
        super().commit()


@contextmanager
def transaction(conn: sqlite3.Connection, *, immediate: bool = False) -> Iterator[None]:
    """Commit once on success and rollback all pending writes on failure.

    Connections returned by connect() suppress nested helper commit() calls
    inside this context, so helpers that still call commit() cannot split
    the transaction.

    SAVEPOINT nesting: if a transaction() is already active on a
    ManagedConnection, opens a SAVEPOINT instead of a plain BEGIN so that
    the inner block is atomic relative to the outer one.
    ``immediate=True`` reserves the write lock before validating mutable state.
    """
    if not isinstance(conn, ManagedConnection):
        if immediate and not conn.in_transaction:
            conn.execute("BEGIN IMMEDIATE")
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
            conn.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
        yield
    except Exception:
        conn.rollback()
        conn._after_commit.clear()
        raise
    else:
        conn.commit_now()
    finally:
        conn._transaction_depth = 0
    callbacks, conn._after_commit = conn._after_commit, []
    for callback in callbacks:
        callback()


def run_after_commit(conn: sqlite3.Connection, callback: Any) -> None:
    """Run *callback* once the enclosing transaction() commits (now if none)."""
    if isinstance(conn, ManagedConnection) and conn._transaction_depth:
        conn._after_commit.append(callback)
    else:
        callback()


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

    A database still on the per-release snapshot model is converted once to
    the monthly signup model (app/db/monthly_migration.py) before the
    current DDL runs.
    """
    from app.db import monthly_migration

    legacy_snapshots = monthly_migration.needs_migration(conn)
    if legacy_snapshots:
        monthly_migration.rebuild_tables(conn)
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
            cicd_notes TEXT NOT NULL DEFAULT '',
            -- running | stopped; changed only when a CICD status request takes effect
            cicd_status TEXT NOT NULL DEFAULT 'running',
            -- Optional Owner profile (not a release prerequisite)
            official_name TEXT NOT NULL DEFAULT '',
            app_type TEXT NOT NULL DEFAULT '',
            official_url TEXT NOT NULL DEFAULT '',
            description TEXT NOT NULL DEFAULT '',
            doc_target TEXT NOT NULL DEFAULT 'manual',
            owners_json TEXT NOT NULL DEFAULT '[]',
            doc_json TEXT NOT NULL DEFAULT '{}',
            community_json TEXT NOT NULL DEFAULT '{}',
            sanity_json TEXT NOT NULL DEFAULT '{}',
            test_docs_json TEXT NOT NULL DEFAULT '[]',
            -- app_info auto-sync state
            sync_head_commit TEXT NOT NULL DEFAULT '',
            sync_checked_at TEXT NOT NULL DEFAULT '',
            sync_error TEXT NOT NULL DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS releases (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            signup_deadline TEXT NOT NULL DEFAULT '',
            qa_deadline TEXT NOT NULL DEFAULT '',
            release_date TEXT NOT NULL DEFAULT '',
            released_locked INTEGER NOT NULL DEFAULT 0,
            released_locked_at TEXT NOT NULL DEFAULT '',
            released_locked_by TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            created_by TEXT NOT NULL DEFAULT ''
        );

        -- Every app_info.json content seen for an App (deduplicated by hash).
        CREATE TABLE IF NOT EXISTS app_info_versions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            app_id TEXT NOT NULL REFERENCES apps(id) ON DELETE CASCADE,
            commit_id TEXT NOT NULL DEFAULT '',
            content_hash TEXT NOT NULL,
            raw_json TEXT NOT NULL,
            source TEXT NOT NULL DEFAULT '',
            fetched_at TEXT NOT NULL,
            fetched_by TEXT NOT NULL DEFAULT ''
        );
        CREATE INDEX IF NOT EXISTS idx_app_info_versions_app
            ON app_info_versions(app_id, id);

        -- Components an App declares in app_info ``app_dependency``.
        -- ``apps`` without any row and without a declaration are "未声明".
        CREATE TABLE IF NOT EXISTS app_dependencies (
            app_id TEXT NOT NULL REFERENCES apps(id) ON DELETE CASCADE,
            component TEXT NOT NULL,
            PRIMARY KEY (app_id, component)
        );

        -- Monthly environment entered by RM (component/version rows).
        CREATE TABLE IF NOT EXISTS cycle_environment (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            release_id TEXT NOT NULL REFERENCES releases(id) ON DELETE CASCADE,
            component TEXT NOT NULL,
            version TEXT NOT NULL DEFAULT '',
            note TEXT NOT NULL DEFAULT '',
            sort INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS base_images (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            release_id TEXT NOT NULL REFERENCES releases(id) ON DELETE CASCADE,
            name TEXT NOT NULL,
            image TEXT NOT NULL DEFAULT '',
            digest TEXT NOT NULL DEFAULT '',
            note TEXT NOT NULL DEFAULT ''
        );

        -- One signup per (release, app). Withdrawn rows are reused on re-signup.
        CREATE TABLE IF NOT EXISTS entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            release_id TEXT NOT NULL REFERENCES releases(id) ON DELETE CASCADE,
            app_id TEXT NOT NULL REFERENCES apps(id) ON DELETE CASCADE,
            status TEXT NOT NULL DEFAULT 'active',
            base_image_id INTEGER REFERENCES base_images(id) ON DELETE SET NULL,
            target_version TEXT NOT NULL DEFAULT '',
            planned_features TEXT NOT NULL DEFAULT '',
            actual_delivery TEXT NOT NULL DEFAULT '',
            known_issues TEXT NOT NULL DEFAULT '',
            locked_version_id INTEGER REFERENCES app_info_versions(id),
            excluded_tests_json TEXT NOT NULL DEFAULT '[]',
            qa_status TEXT NOT NULL DEFAULT 'not_checked',
            qa_note TEXT NOT NULL DEFAULT '',
            deliverable_image TEXT NOT NULL DEFAULT '',
            deliverable_digest TEXT NOT NULL DEFAULT '',
            deliverable_commit TEXT NOT NULL DEFAULT '',
            deliverable_images_json TEXT NOT NULL DEFAULT '[]',
            result TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            created_by TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL DEFAULT '',
            updated_by TEXT NOT NULL DEFAULT '',
            UNIQUE (release_id, app_id)
        );
        CREATE INDEX IF NOT EXISTS idx_entries_app ON entries(app_id);
        CREATE INDEX IF NOT EXISTS idx_entries_digest ON entries(deliverable_digest);

        -- QA approval for app_info changes that widen a signed-up entry's scope
        -- after the signup deadline.
        CREATE TABLE IF NOT EXISTS scope_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            entry_id INTEGER NOT NULL REFERENCES entries(id) ON DELETE CASCADE,
            base_version_id INTEGER NOT NULL REFERENCES app_info_versions(id),
            target_version_id INTEGER NOT NULL REFERENCES app_info_versions(id),
            additions_json TEXT NOT NULL DEFAULT '[]',
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TEXT NOT NULL,
            decided_by TEXT NOT NULL DEFAULT '',
            decided_at TEXT NOT NULL DEFAULT '',
            decision_note TEXT NOT NULL DEFAULT ''
        );
        CREATE INDEX IF NOT EXISTS idx_scope_requests_entry
            ON scope_requests(entry_id, status);

        -- One final publication per App/cycle. Current official is selected by cycle date.
        CREATE TABLE IF NOT EXISTS official_versions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            app_id TEXT NOT NULL REFERENCES apps(id) ON DELETE CASCADE,
            release_id TEXT NOT NULL REFERENCES releases(id) ON DELETE CASCADE,
            entry_id INTEGER REFERENCES entries(id) ON DELETE SET NULL,
            version TEXT NOT NULL DEFAULT '',
            app_info_version_id INTEGER REFERENCES app_info_versions(id),
            image TEXT NOT NULL DEFAULT '',
            digest TEXT NOT NULL DEFAULT '',
            released_at TEXT NOT NULL,
            images_json TEXT NOT NULL DEFAULT '[]',
            test_manifest_json TEXT
        );

        -- One DingTalk robot per notification kind, managed by RM in the UI.
        CREATE TABLE IF NOT EXISTS notification_channels (
            kind TEXT PRIMARY KEY,
            enabled INTEGER NOT NULL DEFAULT 0,
            access_token TEXT NOT NULL DEFAULT '',
            secret TEXT NOT NULL DEFAULT '',
            at_user_ids_json TEXT NOT NULL DEFAULT '[]',
            updated_at TEXT NOT NULL DEFAULT '',
            updated_by TEXT NOT NULL DEFAULT ''
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

        -- The latest AI analysis of a release's QA log, kept until it is
        -- reviewed (QA statuses saved), discarded, or the log is replaced.
        CREATE TABLE IF NOT EXISTS qa_ai_suggestions (
            release_id TEXT PRIMARY KEY REFERENCES releases(id) ON DELETE CASCADE,
            log_filename TEXT NOT NULL DEFAULT '',
            result_json TEXT NOT NULL,
            analyzed_by TEXT NOT NULL DEFAULT '',
            analyzed_at TEXT NOT NULL
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
            -- historical rows may say 'release_decision_sync' (removed workflow)
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

    # Weekly report check (AI助手 → 周报).  One row per ISO week: analysing a
    # week again replaces its row and findings.  weekly_report_items caches the
    # LLM work-item split by report text.
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS weekly_report_weeks (
            week_key TEXT PRIMARY KEY,
            week_start TEXT NOT NULL,
            week_end TEXT NOT NULL,
            report_date TEXT NOT NULL,
            xlsx_name TEXT NOT NULL DEFAULT '',
            xlsx_size INTEGER NOT NULL DEFAULT 0,
            uploaded_by TEXT NOT NULL DEFAULT '',
            uploaded_at TEXT NOT NULL DEFAULT '',
            analyzed_by TEXT NOT NULL DEFAULT '',
            analyzed_at TEXT NOT NULL DEFAULT '',
            active_hours INTEGER NOT NULL DEFAULT 0,
            summary_json TEXT NOT NULL DEFAULT '{}'
        );

        CREATE TABLE IF NOT EXISTS weekly_report_findings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            week_key TEXT NOT NULL REFERENCES weekly_report_weeks(week_key) ON DELETE CASCADE,
            person TEXT NOT NULL,
            sheet_date TEXT NOT NULL DEFAULT '',
            problem TEXT NOT NULL,
            item TEXT NOT NULL DEFAULT '',
            jira_key TEXT NOT NULL DEFAULT '',
            jira_status TEXT NOT NULL DEFAULT '',
            assignee TEXT NOT NULL DEFAULT '',
            last_updated TEXT NOT NULL DEFAULT '',
            last_updater TEXT NOT NULL DEFAULT '',
            note TEXT NOT NULL DEFAULT ''
        );

        CREATE INDEX IF NOT EXISTS idx_weekly_report_findings_week
            ON weekly_report_findings(week_key, person);

        CREATE TABLE IF NOT EXISTS weekly_report_members (
            sheet TEXT PRIMARY KEY,
            jira_username TEXT NOT NULL DEFAULT '',
            skip INTEGER NOT NULL DEFAULT 0,
            note TEXT NOT NULL DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS weekly_report_items (
            sheet TEXT NOT NULL,
            text_sha256 TEXT NOT NULL,
            items_json TEXT NOT NULL,
            PRIMARY KEY (sheet, text_sha256)
        );

        -- MCP server (AI助手 → MCP): personal access tokens, client sessions
        -- and one log row per connection / tool call / attachment download.
        CREATE TABLE IF NOT EXISTS mcp_tokens (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL,
            name TEXT NOT NULL DEFAULT '',
            token_hash TEXT NOT NULL UNIQUE,
            token_prefix TEXT NOT NULL,
            created_at TEXT NOT NULL,
            last_used_at TEXT NOT NULL DEFAULT '',
            revoked_at TEXT NOT NULL DEFAULT ''
        );

        CREATE INDEX IF NOT EXISTS idx_mcp_tokens_user ON mcp_tokens(username);

        CREATE TABLE IF NOT EXISTS mcp_sessions (
            id TEXT PRIMARY KEY,
            token_id INTEGER NOT NULL,
            username TEXT NOT NULL,
            client_name TEXT NOT NULL DEFAULT '',
            client_version TEXT NOT NULL DEFAULT '',
            protocol_version TEXT NOT NULL DEFAULT '',
            -- The client's declared capabilities (JSON); "elicitation" means the
            -- client can show a server-requested confirmation to the user.
            client_capabilities TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            ended_at TEXT NOT NULL DEFAULT ''
        );

        -- Generic on purpose so new tools need no schema change:
        --   kind      connection | tool | download | <future kinds>
        --   category  the tool's feature area (jira, ...), declared by the tool
        --   target_type / target  the object acted on (jira_issue / MC3-1)
        --   extra_json  tool-specific fields (counts, ids) that stats may group on later
        CREATE TABLE IF NOT EXISTS mcp_call_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            username TEXT NOT NULL,
            token_id INTEGER,
            session_id TEXT NOT NULL DEFAULT '',
            client_name TEXT NOT NULL DEFAULT '',
            client_version TEXT NOT NULL DEFAULT '',
            kind TEXT NOT NULL,
            category TEXT NOT NULL DEFAULT '',
            tool TEXT NOT NULL DEFAULT '',
            target_type TEXT NOT NULL DEFAULT '',
            target TEXT NOT NULL DEFAULT '',
            args_json TEXT NOT NULL DEFAULT '',
            extra_json TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL,
            error TEXT NOT NULL DEFAULT '',
            result_summary TEXT NOT NULL DEFAULT '',
            duration_ms INTEGER NOT NULL DEFAULT 0
        );

        CREATE INDEX IF NOT EXISTS idx_mcp_call_logs_created ON mcp_call_logs(created_at);
        CREATE INDEX IF NOT EXISTS idx_mcp_call_logs_user ON mcp_call_logs(username, created_at);
        CREATE INDEX IF NOT EXISTS idx_mcp_call_logs_tool ON mcp_call_logs(category, tool, created_at);
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
        ("entries", "deliverable_images_json", "TEXT NOT NULL DEFAULT '[]'"),
        ("official_versions", "images_json", "TEXT NOT NULL DEFAULT '[]'"),
        ("official_versions", "test_manifest_json", "TEXT"),
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
        ("mcp_sessions",       "client_capabilities", "TEXT NOT NULL DEFAULT ''"),
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

    _migrate_repo_manifest_paths(conn)
    _migrate_qa_log_files(conn)
    if legacy_snapshots:
        monthly_migration.convert_snapshots(conn)

    _migrate_official_versions(conn)

    conn.commit()


def _migrate_official_versions(conn: sqlite3.Connection) -> None:
    """Retire revision history and enforce one final record per App/cycle, without backfilling manifests."""
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(official_versions)")}
    unique_index = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='index' AND name='idx_official_versions_app_release'"
    ).fetchone()
    if "withdrawn" not in columns and unique_index:
        return
    with transaction(conn, immediate=True):
        conn.execute("DROP INDEX IF EXISTS idx_official_versions_app")
        if "withdrawn" in columns:
            conn.execute("DELETE FROM official_versions WHERE withdrawn <> 0")
            conn.execute("ALTER TABLE official_versions DROP COLUMN withdrawn")
        conn.execute("DELETE FROM official_versions WHERE release_id IN (SELECT id FROM releases WHERE released_locked=0)")
        conn.execute("DELETE FROM official_versions WHERE id NOT IN "
                     "(SELECT MAX(id) FROM official_versions GROUP BY app_id,release_id)")
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_official_versions_app_release "
                     "ON official_versions(app_id,release_id)")


def _migrate_repo_manifest_paths(conn: sqlite3.Connection) -> None:
    """Restore repo-type Apps that were overwritten with a resolved Git URL.

    Older FastAPI code resolved a manifest during CICD approval and persisted
    the result in ``apps.git_url/git_branch``.  The manifest path is the durable
    repo configuration, so recover the latest applied value from request
    history.  CICD-first requests that created the App are also eligible before
    approval because that App row is retained after rejection/cancellation;
    requests merely associated with an existing App must have taken effect.
    """
    from app.config import settings

    hpc_project = settings.gerrit_hpc_project.strip("/")
    manifest_project = settings.gerrit_manifest_project.strip("/")
    manifest_prefixes = (
        f"{hpc_project}/{manifest_project}/",
        f"{manifest_project}/",
    )
    apps = conn.execute(
        """
        SELECT id, git_url, git_branch
        FROM apps
        WHERE cicd_repo_type = 'repo'
          AND git_url NOT LIKE '%.xml'
        """
    ).fetchall()
    for app in apps:
        manifest_path = ""
        manifest_branch = ""
        rows = conn.execute(
            """
            SELECT request_type, payload, status, approval_mode, delivery_status
            FROM cicd_task_requests
            WHERE COALESCE(app_id, task_id) = ?
              AND request_type IN ('create', 'modify')
            ORDER BY id
            """,
            (app["id"],),
        ).fetchall()
        for row in rows:
            try:
                payload = json.loads(row["payload"] or "{}")
            except (TypeError, ValueError):
                continue
            applied = row["status"] == "approved" and (
                row["approval_mode"] != "dispatch_spd"
                or row["delivery_status"] == "delivered"
            )
            created_app = (
                row["request_type"] == "create"
                and payload.get("_cicd_first_action") == "created"
            )
            if not applied and not created_app:
                continue
            repo_name = payload.get("repo_name")
            branch = payload.get("branch")
            if row["request_type"] == "modify":
                repo_name = repo_name.get("new") if isinstance(repo_name, dict) else None
                branch = branch.get("new") if isinstance(branch, dict) else None
            candidate = str(repo_name or "").strip().lstrip("/")
            for prefix in manifest_prefixes:
                if candidate.startswith(prefix):
                    candidate = candidate[len(prefix):]
                    break
            if candidate.endswith(".xml"):
                manifest_path = candidate
            candidate_branch = str(branch or "").strip()
            if candidate_branch:
                manifest_branch = candidate_branch
        if manifest_path and (
            manifest_path != app["git_url"] or manifest_branch != app["git_branch"]
        ):
            conn.execute(
                "UPDATE apps SET git_url = ?, git_branch = ? WHERE id = ?",
                (manifest_path, manifest_branch or settings.gerrit_manifest_branch, app["id"]),
            )


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


def _ensure_default_user(conn: sqlite3.Connection) -> None:
    """Ensure built-in dev users exist without overwriting existing accounts."""
    for username, password, role in authn.DEFAULT_USERS:
        conn.execute(
            "INSERT OR IGNORE INTO users(username, password_hash, role) VALUES (?,?,?)",
            (username, authn.hash_password(password), role),
        )
