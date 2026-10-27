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

        -- One business turn can include bounded internal Codex attempts.
        -- Queued rows are the persistent queue; steer never adds a row.
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

        -- Durable publication intent. An uncertain POST is reconciled by GET,
        -- never blindly retried after a timeout or process restart.
        CREATE TABLE IF NOT EXISTS jira_agent_comment_publications (
            turn_id TEXT PRIMARY KEY REFERENCES jira_agent_turns(id) ON DELETE CASCADE,
            body TEXT NOT NULL,
            state TEXT NOT NULL CHECK (state IN ('ready', 'sending', 'uncertain', 'rejected', 'posted')),
            comment_id TEXT NOT NULL DEFAULT '',
            error TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL
        );

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

        -- Learning is isolated from analysis turns and never changes the live
        -- knowledge pack. Queued jobs survive a website restart.
        CREATE TABLE IF NOT EXISTS jira_learning_jobs (
            id TEXT PRIMARY KEY,
            issue_key TEXT NOT NULL,
            agent_group TEXT NOT NULL,
            requested_by TEXT NOT NULL,
            parent_job_id TEXT NOT NULL DEFAULT '',
            revision_of_proposal_id TEXT NOT NULL DEFAULT '',
            revision_feedback_json TEXT NOT NULL DEFAULT '[]',
            knowledge_sha256 TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'queued'
                CHECK (status IN ('queued', 'collecting', 'running', 'completed', 'failed')),
            source_path TEXT NOT NULL DEFAULT '',
            source_sha256 TEXT NOT NULL DEFAULT '',
            acquisition_status TEXT NOT NULL DEFAULT '',
            thread_id TEXT NOT NULL DEFAULT '',
            codex_turn_id TEXT NOT NULL DEFAULT '',
            error TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            started_at TEXT NOT NULL DEFAULT '',
            finished_at TEXT NOT NULL DEFAULT ''
        );
        CREATE INDEX IF NOT EXISTS idx_jira_learning_jobs_status
            ON jira_learning_jobs(status, created_at);

        CREATE TABLE IF NOT EXISTS jira_learning_turns (
            id TEXT PRIMARY KEY,
            job_id TEXT NOT NULL REFERENCES jira_learning_jobs(id) ON DELETE CASCADE,
            seq INTEGER NOT NULL,
            trigger TEXT NOT NULL CHECK (trigger IN ('initial', 'followup')),
            created_by TEXT NOT NULL,
            input_text TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'queued'
                CHECK (status IN ('queued', 'running', 'completed', 'failed')),
            codex_turn_id TEXT NOT NULL DEFAULT '',
            response_text TEXT NOT NULL DEFAULT '',
            error TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            started_at TEXT NOT NULL DEFAULT '',
            finished_at TEXT NOT NULL DEFAULT '',
            UNIQUE (job_id, seq)
        );
        CREATE UNIQUE INDEX IF NOT EXISTS uq_jira_learning_turn_queued
            ON jira_learning_turns(job_id) WHERE status = 'queued';
        CREATE UNIQUE INDEX IF NOT EXISTS uq_jira_learning_turn_running
            ON jira_learning_turns(job_id) WHERE status = 'running';
        CREATE INDEX IF NOT EXISTS idx_jira_learning_turns_queue
            ON jira_learning_turns(status, created_at);

        CREATE TABLE IF NOT EXISTS jira_learning_events (
            id TEXT PRIMARY KEY,
            job_id TEXT NOT NULL REFERENCES jira_learning_jobs(id) ON DELETE CASCADE,
            turn_id TEXT NOT NULL REFERENCES jira_learning_turns(id) ON DELETE CASCADE,
            seq INTEGER NOT NULL,
            kind TEXT NOT NULL,
            item_id TEXT NOT NULL DEFAULT '',
            payload_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_jira_learning_events_job
            ON jira_learning_events(job_id, seq);
        CREATE UNIQUE INDEX IF NOT EXISTS uq_jira_learning_event_item
            ON jira_learning_events(job_id, item_id) WHERE item_id != '';

        CREATE TABLE IF NOT EXISTS jira_learning_proposals (
            id TEXT PRIMARY KEY,
            job_id TEXT NOT NULL REFERENCES jira_learning_jobs(id) ON DELETE CASCADE,
            turn_id TEXT NOT NULL DEFAULT '',
            kind TEXT NOT NULL,
            title TEXT NOT NULL,
            summary TEXT NOT NULL,
            target_path TEXT NOT NULL,
            proposed_text TEXT NOT NULL,
            patch_text TEXT NOT NULL DEFAULT '',
            applicability TEXT NOT NULL,
            evidence_json TEXT NOT NULL,
            limitations TEXT NOT NULL,
            validation TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending'
                CHECK (status IN ('pending', 'approved', 'rejected', 'needs_revision', 'published')),
            published_commit TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_jira_learning_proposals_job
            ON jira_learning_proposals(job_id);

        CREATE TABLE IF NOT EXISTS jira_learning_reviews (
            id TEXT PRIMARY KEY,
            proposal_id TEXT NOT NULL REFERENCES jira_learning_proposals(id) ON DELETE CASCADE,
            reviewer TEXT NOT NULL,
            decision TEXT NOT NULL
                CHECK (decision IN ('approved', 'rejected', 'needs_revision', 'published')),
            note TEXT NOT NULL DEFAULT '',
            commit_sha TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL
        );
        """
    )
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS jira_agent_preflights (
            id TEXT PRIMARY KEY, username TEXT NOT NULL, agent_group TEXT NOT NULL,
            scope_json TEXT NOT NULL, execution_json TEXT NOT NULL, snapshot_json TEXT NOT NULL,
            key_sessions_json TEXT NOT NULL, policy_hash TEXT NOT NULL,
            expires_at REAL NOT NULL, created_at TEXT NOT NULL,
            request_id TEXT NOT NULL DEFAULT '', submission_hash TEXT NOT NULL DEFAULT '',
            consumed_by TEXT NOT NULL DEFAULT ''
        );
        CREATE UNIQUE INDEX IF NOT EXISTS uq_jira_preflight_submission
            ON jira_agent_preflights(username, request_id) WHERE request_id != '';
        CREATE TABLE IF NOT EXISTS jira_agent_key_claims (
            session_id TEXT PRIMARY KEY, preflight_id TEXT NOT NULL
                REFERENCES jira_agent_preflights(id)
        );
    """)
    _ensure_column(conn, "jira_agent_conversations", "execution_config_json", "TEXT NOT NULL DEFAULT ''")
    _ensure_column(conn, "jira_agent_conversations", "execution_snapshot_json", "TEXT NOT NULL DEFAULT ''")
    _ensure_column(conn, "jira_agent_turns", "execution_snapshot_json", "TEXT NOT NULL DEFAULT ''")
    _ensure_column(conn, "jira_agent_turns", "machines_used_json", "TEXT NOT NULL DEFAULT '[]'")
    # '' = the agent picks from the system machine list; else user@host.
    _ensure_column(conn, "jira_agent_conversations", "machine", "TEXT NOT NULL DEFAULT ''")
    # what the agent is actually on: detected from its commands, then its result.
    _ensure_column(conn, "jira_agent_conversations", "machine_used", "TEXT NOT NULL DEFAULT ''")
    # 0 = keep this turn's result on the website only (no JIRA comment); the
    # choice sent with the turn's latest message wins.
    _ensure_column(conn, "jira_agent_turns", "post_comment", "INTEGER NOT NULL DEFAULT 1")
    _ensure_column(conn, "jira_agent_turns", "recovery_attempts", "INTEGER NOT NULL DEFAULT 0")
    # A pending retry still points to the previous Codex turn, allowing restart
    # to distinguish a retry not sent yet from an accepted start with a lost reply.
    _ensure_column(conn, "jira_agent_turns", "retry_pending", "INTEGER NOT NULL DEFAULT 0")
    # Analysis completion and file delivery are independent. Empty preserves
    # the meaning of historical turns, whose delivery was not tracked.
    _ensure_column(conn, "jira_agent_turns", "delivery_status", "TEXT NOT NULL DEFAULT '' CHECK (delivery_status IN ('', 'collecting', 'incomplete', 'complete'))")
    _ensure_column(conn, "jira_agent_turns", "delivery_errors_json", "TEXT NOT NULL DEFAULT '[]'")
    _ensure_column(conn, "jira_learning_jobs", "parent_job_id", "TEXT NOT NULL DEFAULT ''")
    _ensure_column(conn, "jira_learning_jobs", "revision_of_proposal_id", "TEXT NOT NULL DEFAULT ''")
    _ensure_column(conn, "jira_learning_jobs", "revision_feedback_json", "TEXT NOT NULL DEFAULT '[]'")
    _ensure_column(conn, "jira_learning_jobs", "knowledge_sha256", "TEXT NOT NULL DEFAULT ''")
    _ensure_column(conn, "jira_learning_proposals", "patch_text", "TEXT NOT NULL DEFAULT ''")
    _ensure_column(conn, "jira_learning_proposals", "turn_id", "TEXT NOT NULL DEFAULT ''")
    _ensure_column(conn, "jira_learning_proposals", "material_gaps_json", "TEXT NOT NULL DEFAULT '[]'")
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_jira_learning_revision "
        "ON jira_learning_jobs(revision_of_proposal_id) WHERE revision_of_proposal_id != ''"
    )
    # Additive learning metadata keeps existing task/review history intact.
    for table, fields in {
        "jira_learning_jobs": {
            "title": "TEXT NOT NULL DEFAULT ''",
            "request_id": "TEXT NOT NULL DEFAULT ''",
            "request_hash": "TEXT NOT NULL DEFAULT ''",
            "repository_only": "INTEGER NOT NULL DEFAULT 0",
            "cancel_requested": "INTEGER NOT NULL DEFAULT 0",
            "cancelled_at": "TEXT NOT NULL DEFAULT ''",
            "phase": "TEXT NOT NULL DEFAULT ''",
        },
        "jira_learning_turns": {
            "snapshot_json": "TEXT NOT NULL DEFAULT '{}'",
            "revision_ids_json": "TEXT NOT NULL DEFAULT '[]'",
            "cancelled_at": "TEXT NOT NULL DEFAULT ''",
        },
        "jira_learning_proposals": {
            "files_json": "TEXT NOT NULL DEFAULT '[]'",
            "dependencies_json": "TEXT NOT NULL DEFAULT '{}'",
            "base_commit": "TEXT NOT NULL DEFAULT ''",
            "repository_ref": "TEXT NOT NULL DEFAULT ''",
            "previous_id": "TEXT NOT NULL DEFAULT ''",
            "superseded_by": "TEXT NOT NULL DEFAULT ''",
            "version": "INTEGER NOT NULL DEFAULT 1",
            "warnings_json": "TEXT NOT NULL DEFAULT '[]'",
        },
    }.items():
        for column, ddl in fields.items():
            _ensure_column(conn, table, column, ddl)
    conn.executescript("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_learning_request
            ON jira_learning_jobs(requested_by, request_id) WHERE request_id != '';
        CREATE TABLE IF NOT EXISTS jira_learning_members (
            job_id TEXT NOT NULL REFERENCES jira_learning_jobs(id) ON DELETE CASCADE,
            issue_key TEXT NOT NULL,
            position INTEGER NOT NULL,
            PRIMARY KEY (job_id, issue_key)
        );
        CREATE INDEX IF NOT EXISTS idx_learning_member_issue ON jira_learning_members(issue_key, job_id);
        INSERT OR IGNORE INTO jira_learning_members(job_id, issue_key, position)
            SELECT id, issue_key, 0 FROM jira_learning_jobs;
        CREATE TABLE IF NOT EXISTS jira_learning_validations (
            id TEXT PRIMARY KEY,
            job_id TEXT NOT NULL REFERENCES jira_learning_jobs(id) ON DELETE CASCADE,
            suite_id TEXT NOT NULL,
            proposal_ids_json TEXT NOT NULL,
            purpose TEXT NOT NULL,
            note TEXT NOT NULL,
            recorded_by TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS jira_learning_issues (
            issue_key TEXT NOT NULL,
            agent_group TEXT NOT NULL,
            issue_json TEXT NOT NULL,
            added_by TEXT NOT NULL,
            added_at TEXT NOT NULL,
            PRIMARY KEY (issue_key, agent_group)
        );
        CREATE TABLE IF NOT EXISTS jira_learning_deployments (
            id TEXT PRIMARY KEY,
            proposal_id TEXT NOT NULL REFERENCES jira_learning_proposals(id),
            target TEXT NOT NULL,
            commit_sha TEXT NOT NULL,
            result TEXT NOT NULL CHECK (result IN ('succeeded', 'failed')),
            note TEXT NOT NULL,
            recorded_by TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS jira_learning_deployment_events (
            id TEXT PRIMARY KEY,
            agent_group TEXT NOT NULL,
            target TEXT NOT NULL,
            commit_sha TEXT NOT NULL,
            result TEXT NOT NULL CHECK (result IN ('succeeded', 'failed')),
            note TEXT NOT NULL,
            recorded_by TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_learning_deployment_commit
            ON jira_learning_deployment_events(agent_group, commit_sha);
        INSERT OR IGNORE INTO jira_learning_deployment_events
            SELECT d.id,j.agent_group,d.target,d.commit_sha,d.result,d.note,d.recorded_by,d.created_at
            FROM jira_learning_deployments d
            JOIN jira_learning_proposals p ON p.id=d.proposal_id
            JOIN jira_learning_jobs j ON j.id=p.job_id;
    """)
    _ensure_column(conn, "jira_agent_conversations", "source_kind", "TEXT NOT NULL DEFAULT 'jira'")
    from app.repositories.jira_testing_repo import initialize
    initialize(conn)
    # A user-given machine needs a fresh key upload on every hand-over.
    conn.execute("DROP TABLE IF EXISTS jira_agent_ssh_keys")
    conn.commit()


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, ddl: str) -> None:
    columns = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
