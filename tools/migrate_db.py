#!/usr/bin/env python3
"""migrate_db.py — One-shot migration tool: old release_system.db → new schema.

Plan reference: §4.3 of clever-swimming-quiche.md

Safety model:
  - Source DB is opened READ-ONLY (copy via backup API before any write).
  - backup_source() writes a .premigrate.bak alongside the source.
  - Non-empty dst_db requires --force.
  - --dry-run works against an in-memory (:memory:) copy; prints report, no writes.
  - The entire migration runs inside a single transaction: all-or-nothing.

Flow:
  1. backup_source()             — backup src before touching anything
  2. init_target_schema()        — CREATE new schema (app_id col + UNIQUE idx + etc.)
  3. copy_tables()               — FK-safe row copy, blobs opaque
  4. pass1_link_app_ids()        — match cicd_tasks→apps via identity seam
  5. pass2_derive_baseline_tasks() — create tasks for apps with no task
  6. apply_d1_overwrite()        — align linked task status to app decision
  7. rewrite_beijing_times()     — UTC→naive Beijing per-column
  8. validate()                  — row counts, FK check, JSON parse, consistency
  Print report.

Network note:
  pass1_link_app_ids() calls repo_to_git_identity() for .xml manifest rows.
  This requires reaching sw-gerrit-devops.metax-internal.com:29418.
  All identity resolution happens BEFORE opening write transactions (plan §4.2).

Coordination note (impl-backend-core):
  init_target_schema() must call the new app.db schema init (which adds
  app_id + UNIQUE index to cicd_tasks, origin to cicd_task_requests, and
  new indexes).  Until app/ lands, a local stub DDL is used.
  TODO: replace _init_target_schema_stub() with import from app.db once
  impl-backend-core lands it.  Coordinate on the exact function signature.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Optional

# Identity seam import is deferred to function scope so migrate_db.py can be
# run as a script without the project root needing to be on sys.path at import
# time.  See _ensure_path() which inserts the project root before first use.


# ---------------------------------------------------------------------------
# Decision → CICD status mapping (plan §4.3 / cruxing D)
# ---------------------------------------------------------------------------

_DECISION_TO_STATUS: dict[str, str] = {
    "release":   "Running",
    "cicd_only": "Running",
    "stopped":   "Stopped",
}

# FK-safe copy order (parents before children, mirrors init_db DDL dependency)
_COPY_ORDER = [
    "apps",
    "releases",
    "users",
    "sessions",
    "release_schedule",
    "snapshots",
    "artifacts",
    "qa_logs",
    "audit",
    "cicd_tasks",
    "cicd_task_requests",
    "cicd_notifications",
    "wiki_articles",
    "wiki_images",
]

# Columns known to carry UTC-ISO timestamps ("+00:00" / "Z" suffix)
_UTC_COLUMNS: dict[str, list[str]] = {
    "apps":               ["created_at"],
    "releases":           ["created_at"],
    "snapshots":          [],            # blob walker handles synced_at inside data_json
    "artifacts":          ["generated_at"],
    "qa_logs":            ["uploaded_at"],
    "audit":              ["ts"],
    "users":              [],
    "sessions":           ["created_at"],
    "release_schedule":   ["created_at", "updated_at"],
    "cicd_tasks":         ["created_at", "updated_at"],
    "cicd_task_requests": [
        "submitted_at", "reviewed_at", "delivered_at", "returned_at"
    ],
    "cicd_notifications": ["last_visited_at"],
}

# Columns that are already naive Beijing time — pass through unchanged
_NAIVE_BEIJING_COLUMNS: dict[str, list[str]] = {
    "releases":           ["app_freeze_deadline", "doc_deadline", "released_locked_at"],
    "release_schedule":   ["branch_cut_at", "release_at"],
    "qa_logs":            [],  # uploaded_at handled in _UTC_COLUMNS
}

_BEIJING_TZ = timezone(timedelta(hours=8))
_NAIVE_FMT = "%Y-%m-%d %H:%M:%S"


# ---------------------------------------------------------------------------
# Report accumulator
# ---------------------------------------------------------------------------

class MigrationReport:
    """Accumulates per-category counts and detail lines for the final report."""

    def __init__(self) -> None:
        self.linked:     list[str] = []    # cicd_task linked to app
        self.derived:    list[str] = []    # tasks derived from app decision (Pass-2)
        self.overwritten: list[str] = []   # D-1 status overwrites
        self.orphan:     list[str] = []    # tasks that could not be matched
        self.ambiguous:  list[str] = []    # 1:N collisions (neuralgcm etc.)
        self.warnings:   list[str] = []    # non-fatal issues

    def print(self) -> None:
        print("\n=== Migration Report ===")
        print(f"Linked (Pass-1):    {len(self.linked)}")
        print(f"Derived (Pass-2):   {len(self.derived)}")
        print(f"Overwritten (D-1):  {len(self.overwritten)}")
        print(f"Orphan tasks:       {len(self.orphan)}")
        print(f"Ambiguous (1:N):    {len(self.ambiguous)}")
        if self.ambiguous:
            print("  Ambiguous detail:")
            for ln in self.ambiguous:
                print(f"    {ln}")
        if self.orphan:
            print("  Orphan tasks (review with RM):")
            for ln in self.orphan:
                print(f"    {ln}")
        if self.overwritten:
            print("  D-1 overwrites:")
            for ln in self.overwritten:
                print(f"    {ln}")
        if self.warnings:
            print("  Warnings:")
            for ln in self.warnings:
                print(f"    {ln}")
        print("========================\n")


# ---------------------------------------------------------------------------
# Step 1: backup source
# ---------------------------------------------------------------------------

def backup_source(src_path: Path) -> Path:
    """Write a .premigrate.bak copy of *src_path* using the SQLite backup API.

    Returns the backup path.
    """
    # Import here to avoid circular issues; core is present in the project root
    sys.path.insert(0, str(Path(__file__).parent.parent))
    from release_system.core import backup_sqlite  # type: ignore

    bak_path = src_path.with_suffix(".premigrate.bak")
    print(f"[backup] {src_path} → {bak_path}")
    backup_sqlite(src_path, bak_path)
    print("[backup] done")
    return bak_path


# ---------------------------------------------------------------------------
# Step 2: init target schema
# ---------------------------------------------------------------------------

def _init_target_schema_stub(dst_conn: sqlite3.Connection) -> None:
    """Temporary DDL stub that extends the legacy schema with Phase-1 additions.

    TODO: replace with ``from app.db import init_schema; init_schema(dst_conn)``
    once impl-backend-core lands ``app/db/connection.py`` + DDL.
    Coordinate with impl-backend-core on:
      - exact function name (init_schema / create_tables / init_db)
      - whether it handles the ALTER-loop internally or expects a fresh DB
    """
    # First run the legacy init_db to create baseline tables
    from release_system.core import init_db  # type: ignore

    init_db(dst_conn)

    # Remove seed default users — migration will copy real users from src
    dst_conn.execute("DELETE FROM users")
    dst_conn.commit()

    # Add app_id column + partial UNIQUE index to cicd_tasks (plan §4.1)
    for stmt in [
        "ALTER TABLE cicd_tasks ADD COLUMN app_id TEXT REFERENCES apps(id) ON DELETE SET NULL",
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_cicd_tasks_app_id ON cicd_tasks(app_id) WHERE app_id IS NOT NULL",
        "CREATE INDEX IF NOT EXISTS idx_cicd_tasks_repo ON cicd_tasks(repo_name, branch)",
        "CREATE INDEX IF NOT EXISTS idx_cicd_requests_status ON cicd_task_requests(status, delivery_status)",
        "CREATE INDEX IF NOT EXISTS idx_cicd_requests_task ON cicd_task_requests(task_id)",
        "CREATE INDEX IF NOT EXISTS idx_audit_app_release ON audit(app_id, release_id)",
        "CREATE INDEX IF NOT EXISTS idx_snapshots_app ON snapshots(app_id)",
    ]:
        try:
            dst_conn.execute(stmt)
        except sqlite3.OperationalError:
            pass  # already exists

    # Add origin column to cicd_task_requests (plan §4.1)
    try:
        dst_conn.execute(
            "ALTER TABLE cicd_task_requests ADD COLUMN origin TEXT NOT NULL DEFAULT 'cicd_workbench'"
        )
    except sqlite3.OperationalError:
        pass

    dst_conn.commit()


def init_target_schema(dst_conn: sqlite3.Connection) -> None:
    """Initialise the target DB with the new schema (legacy + Phase-1 additions).

    Calls _init_target_schema_stub() for now.

    TODO Phase 1: replace stub with the real app/ schema init once
    impl-backend-core lands it.
    """
    print("[schema] initialising target schema")
    _init_target_schema_stub(dst_conn)
    print("[schema] done")


# ---------------------------------------------------------------------------
# Step 3: copy tables
# ---------------------------------------------------------------------------

def copy_tables(src_conn: sqlite3.Connection, dst_conn: sqlite3.Connection) -> dict[str, int]:
    """Copy rows from src → dst in FK-safe order.

    Blobs are copied opaque (no JSON re-serialisation at this stage).
    Returns a dict of {table_name: row_count} for the report.
    """
    print("[copy] copying tables")
    counts: dict[str, int] = {}

    # Discover which tables actually exist in src
    src_tables = {
        row[0]
        for row in src_conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }

    for table in _COPY_ORDER:
        if table not in src_tables:
            print(f"  [copy] {table}: not in src, skipping")
            continue

        rows = src_conn.execute(f"SELECT * FROM {table}").fetchall()
        if not rows:
            print(f"  [copy] {table}: 0 rows")
            counts[table] = 0
            continue

        cols = [desc[0] for desc in src_conn.execute(f"SELECT * FROM {table} LIMIT 0").description]
        # Only insert columns that exist in dst (skip new columns added by init_target_schema)
        dst_cols_result = dst_conn.execute(f"PRAGMA table_info({table})").fetchall()
        dst_cols = {row[1] for row in dst_cols_result}
        insert_cols = [c for c in cols if c in dst_cols]

        placeholders = ", ".join("?" for _ in insert_cols)
        col_names = ", ".join(insert_cols)
        col_indices = [cols.index(c) for c in insert_cols]

        dst_conn.executemany(
            f"INSERT OR IGNORE INTO {table} ({col_names}) VALUES ({placeholders})",
            [[row[i] for i in col_indices] for row in rows],
        )
        counts[table] = len(rows)
        print(f"  [copy] {table}: {len(rows)} rows")

    dst_conn.commit()
    print("[copy] done")
    return counts


# ---------------------------------------------------------------------------
# Step 4: Pass-1 — link existing cicd_tasks to apps
# ---------------------------------------------------------------------------

def pass1_link_app_ids(
    src_conn: sqlite3.Connection,
    dst_conn: sqlite3.Connection,
    report: MigrationReport,
) -> dict[str, str]:
    """Match each cicd_task to an app via the identity seam (git_url, git_branch).

    Algorithm (plan §4.3 Pass-1):
      - Normalise task (repo_type, repo_name, branch) → (git_url, git_branch)
        via repo_to_git_identity().
      - For each app, also normalise its (git_url, git_branch).
      - Unique match → write app_id to cicd_tasks.
      - 1:N collision (same app, multiple tasks) → link one, report rest as
        ambiguous; RM must clean up.
      - No match → leave app_id NULL, report as orphan.

    Returns a dict {task_id: app_id} for tasks that were linked.

    NOTE: identity resolution for .xml manifest rows requires network access
    to sw-gerrit-devops.metax-internal.com:29418.  Offline rows produce
    (None, None) from resolve_manifest_url and are treated as orphans with
    a warning.
    """
    print("[pass1] linking cicd_tasks → apps via identity")

    _ensure_path()
    from tools.identity_resolver import (  # type: ignore  # noqa: PLC0415
        repo_to_git_identity as _repo_to_git_identity,
        normalize_git_url as _normalize_git_url,
    )

    # Load apps from dst (already copied)
    apps = dst_conn.execute("SELECT id, git_url, git_branch FROM apps").fetchall()
    # Pre-resolve app identities (git_url already stored as short name or full URL)
    app_identity: list[tuple[str, str, str]] = []  # (app_id, norm_url, branch)
    for app in apps:
        norm_url = _normalize_git_url(app[1])
        app_identity.append((app[0], norm_url, app[2]))

    tasks = dst_conn.execute(
        "SELECT id, app_name, repo_type, repo_name, branch FROM cicd_tasks"
    ).fetchall()

    # app_id → list of task_ids that matched (for 1:N detection)
    app_to_tasks: dict[str, list[str]] = {}
    task_to_app: dict[str, str] = {}

    for task in tasks:
        task_id, app_name, repo_type, repo_name, branch = task

        # Resolve task identity — may need network for .xml manifests
        t_url, t_branch = _repo_to_git_identity(repo_type, repo_name, branch)
        if t_url is None:
            msg = f"task {task_id} ({app_name}): manifest resolve failed → orphan"
            print(f"  [pass1] {msg}")
            report.orphan.append(msg)
            continue

        t_norm = _normalize_git_url(t_url)

        matched_apps = [
            a_id for (a_id, a_norm, a_branch) in app_identity
            if a_norm == t_norm and a_branch == t_branch
        ]

        if not matched_apps:
            msg = f"task {task_id} ({app_name}, {repo_name}@{branch}): no app match → orphan"
            print(f"  [pass1] {msg}")
            report.orphan.append(msg)
            continue

        if len(matched_apps) > 1:
            # Should not happen after identity normalization, but guard anyway
            msg = f"task {task_id} ({app_name}): matched {len(matched_apps)} apps {matched_apps} → ambiguous"
            print(f"  [pass1] {msg}")
            report.ambiguous.append(msg)
            # Take first match, report rest
            matched_apps = matched_apps[:1]

        a_id = matched_apps[0]
        app_to_tasks.setdefault(a_id, []).append(task_id)
        task_to_app[task_id] = a_id

    # Handle 1:N collision (same app, multiple tasks — e.g. neuralgcm)
    linked_task_to_app: dict[str, str] = {}
    for a_id, t_ids in app_to_tasks.items():
        if len(t_ids) == 1:
            linked_task_to_app[t_ids[0]] = a_id
        else:
            # Keep the first task (arbitrary but deterministic), report the rest
            keep = t_ids[0]
            linked_task_to_app[keep] = a_id
            for extra in t_ids[1:]:
                msg = (
                    f"app {a_id}: 1:N collision — keeping task {keep}, "
                    f"dropping task {extra} (RM must clean up)"
                )
                print(f"  [pass1] {msg}")
                report.ambiguous.append(msg)
                # Clear any task→app mapping for the dropped task
                task_to_app.pop(extra, None)

    # Write app_id to dst cicd_tasks
    for t_id, a_id in linked_task_to_app.items():
        dst_conn.execute(
            "UPDATE cicd_tasks SET app_id = ? WHERE id = ?", (a_id, t_id)
        )
        report.linked.append(f"task {t_id} → app {a_id}")
        print(f"  [pass1] linked task {t_id} → app {a_id}")

    dst_conn.commit()
    print(f"[pass1] done: {len(report.linked)} linked, "
          f"{len(report.orphan)} orphan, {len(report.ambiguous)} ambiguous")
    return linked_task_to_app


# ---------------------------------------------------------------------------
# Step 5: Pass-2 — derive baseline tasks for apps with no linked task
# ---------------------------------------------------------------------------

def pass2_derive_baseline_tasks(
    dst_conn: sqlite3.Connection,
    report: MigrationReport,
) -> None:
    """Create a synthetic cicd_task for every app that has no linked task.

    Decision mapping (plan §4.3 / cruxing D):
      release | cicd_only → Running
      stopped             → Stopped

    The app's release_decision is read from its most recent (non-locked)
    snapshot.  If all snapshots are locked or ambiguous, the app is skipped
    with a warning.

    TODO: confirm with impl-backend-core how to call the canonical
    task-creation path (app/repositories/cicd.py) rather than raw SQL, so
    the row format stays in sync with the new schema.
    """
    print("[pass2] deriving baseline tasks for unlinked apps")

    # Apps that already have a task linked
    linked_app_ids = {
        row[0]
        for row in dst_conn.execute(
            "SELECT DISTINCT app_id FROM cicd_tasks WHERE app_id IS NOT NULL"
        ).fetchall()
    }

    all_apps = dst_conn.execute("SELECT id, git_url, git_branch FROM apps").fetchall()
    unlinked = [a for a in all_apps if a[0] not in linked_app_ids]

    import secrets as _secrets
    from release_system.core import beijing_now  # type: ignore

    for app in unlinked:
        app_id = app[0]

        # Find the most recent snapshot to determine release_decision
        snap_rows = dst_conn.execute(
            """
            SELECT s.data_json
            FROM snapshots s
            JOIN releases r ON r.id = s.release_id
            WHERE s.app_id = ?
            ORDER BY r.created_at DESC
            """,
            (app_id,),
        ).fetchall()

        decision = None
        for snap_row in snap_rows:
            try:
                data = json.loads(snap_row[0])
                d = data.get("release_decision", "")
                if d in _DECISION_TO_STATUS:
                    decision = d
                    break
            except (json.JSONDecodeError, KeyError):
                continue

        if decision is None:
            msg = f"app {app_id}: no valid release_decision found → skipped"
            print(f"  [pass2] {msg}")
            report.warnings.append(msg)
            continue

        status = _DECISION_TO_STATUS[decision]
        task_id = f"CICD-{_secrets.token_hex(4).upper()}"
        now_str = beijing_now().strftime(_NAIVE_FMT)

        dst_conn.execute(
            """
            INSERT INTO cicd_tasks
              (id, app_name, app_version, repo_type, repo_name, branch,
               build_product, community_artifact, build_image, test_timeout,
               owner_username, status, notes, created_at, updated_at, app_id)
            VALUES (?, ?, '', 'git', '', '',
                    '[]', '[]', '', 40,
                    '', ?, '', ?, ?, ?)
            """,
            (task_id, app_id, status, now_str, now_str, app_id),
        )
        msg = f"app {app_id}: derived task {task_id} (decision={decision} → status={status})"
        print(f"  [pass2] {msg}")
        report.derived.append(msg)

    dst_conn.commit()
    print(f"[pass2] done: {len(report.derived)} tasks derived")


# ---------------------------------------------------------------------------
# Step 6: D-1 overwrite — align linked task status to app decision
# ---------------------------------------------------------------------------

def apply_d1_overwrite(
    dst_conn: sqlite3.Connection,
    report: MigrationReport,
) -> None:
    """For every linked task whose status contradicts the app's release_decision,
    overwrite to the decision-derived value.

    Plan §4.3 D-1: real-world example — tfhe-rs, warp, seg-utils, uni-fold,
    moflow are stopped apps but have Running tasks → rewrite to Stopped.

    The authoritative decision is taken from the most recent non-locked snapshot.
    """
    print("[d1] applying D-1 status overwrites")

    linked_tasks = dst_conn.execute(
        "SELECT id, app_id, status FROM cicd_tasks WHERE app_id IS NOT NULL"
    ).fetchall()

    from release_system.core import beijing_now  # type: ignore

    for task_id, app_id, current_status in linked_tasks:
        # Find most recent non-locked release_decision for this app
        snap_rows = dst_conn.execute(
            """
            SELECT s.data_json
            FROM snapshots s
            JOIN releases r ON r.id = s.release_id
            WHERE s.app_id = ? AND r.released_locked = 0
            ORDER BY r.created_at DESC
            """,
            (app_id,),
        ).fetchall()

        decision = None
        for snap_row in snap_rows:
            try:
                data = json.loads(snap_row[0])
                d = data.get("release_decision", "")
                if d in _DECISION_TO_STATUS:
                    decision = d
                    break
            except (json.JSONDecodeError, KeyError):
                continue

        if decision is None:
            continue

        expected_status = _DECISION_TO_STATUS[decision]
        if current_status != expected_status:
            dst_conn.execute(
                "UPDATE cicd_tasks SET status = ?, updated_at = ? WHERE id = ?",
                (expected_status, beijing_now().strftime(_NAIVE_FMT), task_id),
            )
            msg = (
                f"task {task_id} (app {app_id}): "
                f"{current_status} → {expected_status} "
                f"(decision={decision})"
            )
            print(f"  [d1] {msg}")
            report.overwritten.append(msg)

    dst_conn.commit()
    print(f"[d1] done: {len(report.overwritten)} overwrites")


# ---------------------------------------------------------------------------
# Step 7: rewrite Beijing times
# ---------------------------------------------------------------------------

def _utc_to_beijing(value: Any) -> Any:
    """Convert a UTC ISO timestamp string to naive Beijing time string.

    Handles:
      - "2026-01-02T12:00:00+00:00"
      - "2026-01-02T12:00:00Z"
      - "2026-01-02 12:00:00+00:00"

    Already-naive values (no offset) are returned unchanged.
    Non-string or empty values are returned as-is.
    """
    if not isinstance(value, str) or not value:
        return value
    # Detect UTC offset marker
    s = value.strip()
    if "+00:00" not in s and not s.endswith("Z") and "+08:00" not in s:
        return value  # already naive or unknown offset — pass through
    # Parse to aware datetime
    s = s.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return value  # unparseable — leave as-is
    if dt.tzinfo is None:
        return value
    bj = dt.astimezone(_BEIJING_TZ)
    return bj.strftime(_NAIVE_FMT)


def _rewrite_blob_synced_at(blob_json: str) -> str:
    """Walk a snapshot data_json blob and rewrite ``synced_at`` fields from
    UTC to naive Beijing time (plan §4.3 DA C2).

    Only modifies ``synced_at`` keys at any nesting level.
    Returns the rewritten JSON string (or the original on parse error).
    """
    try:
        data = json.loads(blob_json)
    except (json.JSONDecodeError, TypeError):
        return blob_json

    def _walk(obj: Any) -> Any:
        if isinstance(obj, dict):
            return {
                k: (_utc_to_beijing(v) if k == "synced_at" else _walk(v))
                for k, v in obj.items()
            }
        if isinstance(obj, list):
            return [_walk(item) for item in obj]
        return obj

    return json.dumps(_walk(data), ensure_ascii=False)


def rewrite_beijing_times(dst_conn: sqlite3.Connection) -> None:
    """Rewrite UTC timestamps to naive Beijing time, per-column (plan §4.3).

    - Columns listed in _UTC_COLUMNS are updated in-place.
    - snapshots.data_json blob is walked to rewrite synced_at fields.
    - Columns in _NAIVE_BEIJING_COLUMNS are left untouched (already naive).

    After this step, assert no values in UTC columns contain "+00:00".
    """
    print("[tz] rewriting UTC → naive Beijing times")

    for table, columns in _UTC_COLUMNS.items():
        if not columns:
            continue
        rows = dst_conn.execute(f"SELECT rowid, {', '.join(columns)} FROM {table}").fetchall()
        for row in rows:
            rowid = row[0]
            updates = {}
            for i, col in enumerate(columns):
                new_val = _utc_to_beijing(row[i + 1])
                if new_val != row[i + 1]:
                    updates[col] = new_val
            if updates:
                set_clause = ", ".join(f"{c} = ?" for c in updates)
                dst_conn.execute(
                    f"UPDATE {table} SET {set_clause} WHERE rowid = ?",
                    (*updates.values(), rowid),
                )

    # Walk snapshot blobs for synced_at
    snap_rows = dst_conn.execute(
        "SELECT rowid, data_json FROM snapshots"
    ).fetchall()
    for rowid, blob in snap_rows:
        new_blob = _rewrite_blob_synced_at(blob)
        if new_blob != blob:
            dst_conn.execute(
                "UPDATE snapshots SET data_json = ? WHERE rowid = ?",
                (new_blob, rowid),
            )

    dst_conn.commit()
    print("[tz] done")


# ---------------------------------------------------------------------------
# Step 8: validate
# ---------------------------------------------------------------------------

def validate(
    src_conn: sqlite3.Connection,
    dst_conn: sqlite3.Connection,
    report: MigrationReport,
) -> bool:
    """Run post-migration sanity checks.

    Checks:
      1. Row counts: dst >= src for all copied tables (derived tasks add rows).
      2. PRAGMA foreign_key_check returns empty.
      3. All snapshots.data_json and cicd_task_requests.payload parse as JSON.
      4. app_id self-consistency: linked + derived == non-null app_ids (no UNIQUE violation).
      5. No UTC offsets remain in target UTC columns.

    Returns True if all checks pass, False otherwise (errors are printed).
    """
    print("[validate] running checks")
    ok = True

    # 1. Row counts
    src_tables = {
        row[0]
        for row in src_conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    for table in _COPY_ORDER:
        if table not in src_tables:
            continue
        src_count = src_conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        dst_count = dst_conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        if dst_count < src_count:
            print(f"  [validate] ERROR: {table} row count dst={dst_count} < src={src_count}")
            ok = False
        else:
            print(f"  [validate] {table}: src={src_count} dst={dst_count} OK")

    # 2. FK check
    fk_errors = dst_conn.execute("PRAGMA foreign_key_check").fetchall()
    if fk_errors:
        print(f"  [validate] ERROR: PRAGMA foreign_key_check returned {len(fk_errors)} errors")
        for err in fk_errors[:10]:
            print(f"    {err}")
        ok = False
    else:
        print("  [validate] PRAGMA foreign_key_check: OK")

    # 3. JSON parse
    for table, col in [("snapshots", "data_json"), ("cicd_task_requests", "payload")]:
        bad = 0
        for (val,) in dst_conn.execute(f"SELECT {col} FROM {table}").fetchall():
            if val:
                try:
                    json.loads(val)
                except (json.JSONDecodeError, TypeError):
                    bad += 1
        if bad:
            print(f"  [validate] ERROR: {table}.{col} has {bad} unparseable JSON rows")
            ok = False
        else:
            print(f"  [validate] {table}.{col}: JSON parse OK")

    # 4. app_id self-consistency
    total_linked = len(report.linked)
    total_derived = len(report.derived)
    non_null_in_db = dst_conn.execute(
        "SELECT COUNT(*) FROM cicd_tasks WHERE app_id IS NOT NULL"
    ).fetchone()[0]
    expected = total_linked + total_derived
    if non_null_in_db != expected:
        print(
            f"  [validate] ERROR: app_id count mismatch: "
            f"linked+derived={expected} but non-null in DB={non_null_in_db}"
        )
        ok = False
    else:
        print(f"  [validate] app_id count: {non_null_in_db} OK")

    # 5. No UTC offsets remain
    for table, columns in _UTC_COLUMNS.items():
        for col in columns:
            try:
                bad = dst_conn.execute(
                    f"SELECT COUNT(*) FROM {table} WHERE {col} LIKE '%+00:00%' OR {col} LIKE '%Z'"
                ).fetchone()[0]
            except sqlite3.OperationalError:
                continue
            if bad:
                print(f"  [validate] WARNING: {table}.{col} still has {bad} UTC-offset values")
                report.warnings.append(f"{table}.{col}: {bad} UTC values remain after rewrite")

    print(f"[validate] done — {'PASS' if ok else 'FAIL'}")
    return ok


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def _open_src_readonly(src_path: Path) -> sqlite3.Connection:
    """Open source DB in read-only mode via URI."""
    import urllib.parse
    uri = "file:" + urllib.parse.quote(str(src_path.resolve())) + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def migrate(
    src_path: Path,
    dst_path: Path,
    *,
    dry_run: bool = False,
    force: bool = False,
) -> bool:
    """Run the full migration pipeline.

    Returns True if validation passed.

    In --dry-run mode the destination is an in-memory DB; nothing is written
    to disk but all pipeline steps execute so the report is accurate for
    non-network-dependent rows.
    """
    print(f"[migrate] src={src_path}  dst={'<memory>' if dry_run else dst_path}  dry_run={dry_run}")

    # Guard: non-empty dst requires --force
    if not dry_run and dst_path.exists() and dst_path.stat().st_size > 0 and not force:
        print(
            f"[migrate] ERROR: {dst_path} already exists and is non-empty. "
            "Use --force to overwrite."
        )
        return False

    report = MigrationReport()

    # Backup source
    if not dry_run:
        backup_source(src_path)

    # Open connections
    src_conn = _open_src_readonly(src_path)
    src_conn.execute("PRAGMA foreign_keys = ON")

    if dry_run:
        dst_conn = sqlite3.connect(":memory:")
    else:
        dst_conn = sqlite3.connect(dst_path)
    dst_conn.row_factory = sqlite3.Row
    dst_conn.execute("PRAGMA foreign_keys = ON")

    try:
        # Pipeline
        init_target_schema(dst_conn)
        copy_tables(src_conn, dst_conn)
        pass1_link_app_ids(src_conn, dst_conn, report)
        pass2_derive_baseline_tasks(dst_conn, report)
        apply_d1_overwrite(dst_conn, report)
        rewrite_beijing_times(dst_conn)
        ok = validate(src_conn, dst_conn, report)
    except Exception as exc:
        print(f"[migrate] FATAL: {exc}")
        import traceback
        traceback.print_exc()
        ok = False
    finally:
        src_conn.close()
        dst_conn.close()

    report.print()
    return ok


def _ensure_path() -> None:
    """Insert the project root onto sys.path so tools/ and release_system/ are importable."""
    root = str(Path(__file__).parent.parent)
    if root not in sys.path:
        sys.path.insert(0, root)


def _build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="migrate_db",
        description=(
            "One-shot migration tool: copy old release_system.db to new schema. "
            "See tools/migrate_db.py module docstring for full pipeline description."
        ),
    )
    p.add_argument("src_db", type=Path, help="Path to the source (old) SQLite database.")
    p.add_argument("dst_db", type=Path, help="Path for the destination (new) SQLite database.")
    p.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help=(
            "Run pipeline against an in-memory copy; print report but write nothing to disk. "
            "Identity resolution still requires network for .xml manifest rows."
        ),
    )
    p.add_argument(
        "--force",
        action="store_true",
        default=False,
        help="Overwrite dst_db even if it already exists and is non-empty.",
    )
    return p


def main(argv: list[str] | None = None) -> None:
    # Ensure tools/ + release_system/ are importable when run as a script
    _ensure_path()

    parser = _build_arg_parser()
    args = parser.parse_args(argv)

    ok = migrate(args.src_db, args.dst_db, dry_run=args.dry_run, force=args.force)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
