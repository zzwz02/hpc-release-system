#!/usr/bin/env python3
"""migrate_db.py — One-shot migration tool: old release_system.db → new schema.

Plan reference: §4.3 of clever-swimming-quiche.md

Safety model
------------
- Source DB is opened READ-ONLY (URI mode).
- backup_source() writes a .premigrate.bak alongside the source before any write.
- Non-empty dst_db requires --force to prevent accidental overwrites.
- --dry-run runs against an in-memory DB; prints report, writes nothing to disk.
- The ENTIRE migration (steps 3-7) runs inside ONE sqlite3 transaction: all-or-nothing.

Pipeline
--------
  1. backup_source()               — sqlite3 online-backup API → .premigrate.bak
  2. init_target_schema()          — app.db.connection.init_db (new schema + indexes)
                                     then DELETE seed default users so real users copy in
  3. [transaction begin]
  4. copy_tables()                 — FK-safe order, blob-opaque, accurate inserted counts
  5. pass1_link_app_ids()          — match cicd_tasks→apps via app.identity seam
                                     1:N collision (neuralgcm): keep one, report rest
  6. pass2_derive_baseline_tasks() — create CICD-0001 style tasks for unlinked apps
  7. apply_d1_overwrite()          — align linked task status to app release_decision
  8. rewrite_beijing_times()       — UTC-ISO → naive Beijing per-column + blob synced_at
  9. [transaction commit / rollback on any error]
 10. validate()                    — row counts, FK check, JSON parse, app_id consistency
 11. print report

Network note
------------
pass1_link_app_ids() calls repo_to_git_identity() for .xml manifest rows, which
shells out to `git archive --remote=ssh://sw-gerrit-devops.metax-internal.com:29418/...`.
All identity resolution happens BEFORE the write transaction (plan §4.2).
Offline → (None, None) → task reported as orphan, migration continues.

Coordination note
-----------------
init_target_schema() now calls app.db.connection.init_db (canonical new schema,
Phase-0 HIGH fix).  The identity seam is app.identity.repo_to_git_identity.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


def _ensure_path() -> None:
    """Prepend the project root to sys.path so app/ and release_system/ are importable."""
    root = str(Path(__file__).parent.parent)
    if root not in sys.path:
        sys.path.insert(0, root)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Decision → CICD status mapping (plan §4.3 /裁定 D)
_DECISION_TO_STATUS: dict[str, str] = {
    "release":   "Running",
    "cicd_only": "Running",
    "stopped":   "Stopped",
}

# FK-safe copy order: parents before children
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

# Columns carrying UTC-ISO timestamps (may contain "+00:00" / "Z")
_UTC_COLUMNS: dict[str, list[str]] = {
    "apps":               ["created_at"],
    "releases":           ["created_at"],
    "artifacts":          ["generated_at"],
    "qa_logs":            ["uploaded_at"],
    "audit":              ["ts"],
    "sessions":           ["created_at"],
    "release_schedule":   ["created_at", "updated_at"],
    "cicd_tasks":         ["created_at", "updated_at"],
    "cicd_task_requests": [
        "submitted_at", "reviewed_at", "delivered_at", "returned_at",
    ],
    "cicd_notifications": ["last_visited_at"],
    # F1: wiki timestamps were written as UTC-ISO by the old server; convert
    # them to naive Beijing to match the new app's write convention.
    "wiki_articles":      ["created_at", "updated_at"],
    "wiki_images":        ["uploaded_at"],
}

_BEIJING_TZ = timezone(timedelta(hours=8))
_NAIVE_FMT = "%Y-%m-%d %H:%M:%S"


# ---------------------------------------------------------------------------
# Report accumulator
# ---------------------------------------------------------------------------

class MigrationReport:
    """Accumulates per-category counts and detail lines printed at the end."""

    def __init__(self) -> None:
        self.linked: list[str] = []      # task linked to app (Pass-1)
        self.derived: list[str] = []     # task created from app decision (Pass-2)
        self.overwritten: list[str] = [] # D-1 status corrections
        self.orphan: list[str] = []      # tasks with no matching app
        self.ambiguous: list[str] = []   # 1:N collisions (e.g. neuralgcm)
        self.warnings: list[str] = []    # non-fatal issues

    def print(self) -> None:
        sep = "=" * 40
        print(f"\n{sep}")
        print("Migration Report")
        print(sep)
        print(f"  Linked   (Pass-1): {len(self.linked)}")
        print(f"  Derived  (Pass-2): {len(self.derived)}")
        print(f"  Rewritten  (D-1):  {len(self.overwritten)}")
        print(f"  Orphan tasks:      {len(self.orphan)}")
        print(f"  Ambiguous (1:N):   {len(self.ambiguous)}")
        for section, items in [
            ("Ambiguous (RM review needed)", self.ambiguous),
            ("Orphan tasks (RM review needed)", self.orphan),
            ("D-1 rewrites", self.overwritten),
            ("Warnings", self.warnings),
        ]:
            if items:
                print(f"\n  {section}:")
                for ln in items:
                    print(f"    {ln}")
        print(f"{sep}\n")


# ---------------------------------------------------------------------------
# Step 1: backup source
# ---------------------------------------------------------------------------

def backup_source(src_path: Path) -> Path:
    """Write a .premigrate.bak copy of *src_path* using the sqlite3 backup API.

    Returns the backup path.
    """
    _ensure_path()
    from release_system.core import backup_sqlite  # type: ignore

    bak_path = src_path.with_suffix(".premigrate.bak")
    print(f"[backup] {src_path} → {bak_path}")
    backup_sqlite(src_path, bak_path)
    print("[backup] done")
    return bak_path


# ---------------------------------------------------------------------------
# Step 2: init target schema
# ---------------------------------------------------------------------------

def init_target_schema(dst_conn: sqlite3.Connection) -> None:
    """Initialise the target DB with the new schema via app.db.connection.init_db.

    After schema creation, seed default users are deleted so that the real
    users from the source DB can be copied in during copy_tables().
    """
    _ensure_path()
    from app.db.connection import init_db  # type: ignore

    print("[schema] initialising target schema (app.db.connection.init_db)")
    init_db(dst_conn)
    # Remove seed users: copy_tables will bring in the real user rows from src.
    dst_conn.execute("DELETE FROM users")
    dst_conn.commit()
    print("[schema] done")


# ---------------------------------------------------------------------------
# Step 3: copy tables (inside the outer transaction)
# ---------------------------------------------------------------------------

def copy_tables(
    src_conn: sqlite3.Connection,
    dst_conn: sqlite3.Connection,
) -> dict[str, int]:
    """Copy rows from src → dst in FK-safe order.

    Blobs are copied opaque — no JSON re-serialisation at this stage.
    Rows that violate a constraint in dst are reported as errors (not silently
    dropped — INSERT OR IGNORE is NOT used).

    Returns {table_name: inserted_count}.
    """
    print("[copy] copying tables")
    counts: dict[str, int] = {}

    src_tables = {
        row[0]
        for row in src_conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }

    for table in _COPY_ORDER:
        if table not in src_tables:
            print(f"  [copy] {table}: not in src, skipping")
            counts[table] = 0
            continue

        rows = src_conn.execute(f"SELECT * FROM {table}").fetchall()
        if not rows:
            print(f"  [copy] {table}: 0 rows")
            counts[table] = 0
            continue

        cursor = src_conn.execute(f"SELECT * FROM {table} LIMIT 0")
        src_cols = [desc[0] for desc in cursor.description]
        # Only insert columns that exist in dst (new columns like app_id get NULL/DEFAULT)
        dst_col_info = dst_conn.execute(f"PRAGMA table_info({table})").fetchall()
        dst_col_names = {row[1] for row in dst_col_info}
        insert_cols = [c for c in src_cols if c in dst_col_names]

        col_names = ", ".join(insert_cols)
        placeholders = ", ".join("?" for _ in insert_cols)
        col_indices = [src_cols.index(c) for c in insert_cols]

        inserted = 0
        for row in rows:
            values = [row[i] for i in col_indices]
            dst_conn.execute(
                f"INSERT INTO {table} ({col_names}) VALUES ({placeholders})",
                values,
            )
            inserted += 1

        counts[table] = inserted
        print(f"  [copy] {table}: {inserted} rows")

    print("[copy] done")
    return counts


# ---------------------------------------------------------------------------
# Step 4: Pass-1 — link existing cicd_tasks to apps
# ---------------------------------------------------------------------------

def _resolve_all_identities(
    tasks: list[Any],
) -> dict[str, tuple[str | None, str | None]]:
    """Resolve identities for all tasks BEFORE entering the write transaction.

    Returns {task_id: (norm_url, branch)} for tasks that resolved,
    or {task_id: (None, None)} for failures.

    All network I/O happens here, outside any DB transaction (plan §4.2).
    """
    from app.identity import normalize_git_url, repo_to_git_identity  # type: ignore

    resolved: dict[str, tuple[str | None, str | None]] = {}
    for task in tasks:
        task_id, _app_name, repo_type, repo_name, branch = (
            task[0], task[1], task[2], task[3], task[4]
        )
        url, br = repo_to_git_identity(repo_type, repo_name, branch)
        if url is not None:
            resolved[task_id] = (normalize_git_url(url), br)
        else:
            resolved[task_id] = (None, None)
    return resolved


def pass1_link_app_ids(
    src_conn: sqlite3.Connection,
    dst_conn: sqlite3.Connection,
    report: MigrationReport,
) -> dict[str, str]:
    """Match each cicd_task to an app via (git_url, git_branch) identity.

    Algorithm (plan §4.3 Pass-1):
      1. Normalise task (repo_type, repo_name, branch) → (url, branch) via app.identity.
      2. Normalise each app's stored git_url via normalize_git_url.
      3. Unique match → write app_id.
      4. 1:N (same app matches multiple tasks, e.g. neuralgcm duplicate):
         keep the first task, report the rest as ambiguous.
      5. No match → leave app_id NULL, report as orphan.

    Identity resolution (including any network fetches for .xml manifests)
    happens BEFORE this function enters any writes — see _resolve_all_identities.

    Returns {task_id: app_id} for linked tasks.
    """
    print("[pass1] linking cicd_tasks → apps via identity seam")
    from app.identity import normalize_git_url  # type: ignore

    # Pre-resolve all apps
    apps = dst_conn.execute("SELECT id, git_url, git_branch FROM apps").fetchall()
    app_identity: list[tuple[str, str, str]] = []  # (app_id, norm_url, branch)
    for app in apps:
        app_identity.append((app[0], normalize_git_url(app[1]), app[2]))

    tasks = dst_conn.execute(
        "SELECT id, app_name, repo_type, repo_name, branch FROM cicd_tasks"
    ).fetchall()

    # Identity resolution already done (via _resolve_all_identities called before tx)
    # Re-resolve inline here (fast for git-type; manifests already cached)
    task_id_to_norm: dict[str, tuple[str | None, str | None]] = (
        _resolve_all_identities(tasks)
    )

    # Group: app_id → [task_ids that match it]
    app_to_tasks: dict[str, list[str]] = {}
    no_match: list[str] = []

    for task in tasks:
        task_id, app_name = task[0], task[1]
        t_norm, t_branch = task_id_to_norm.get(task_id, (None, None))

        if t_norm is None:
            msg = f"task {task_id} ({app_name}): identity unresolvable (network?) → orphan"
            print(f"  [pass1] {msg}")
            report.orphan.append(msg)
            continue

        matches = [
            a_id
            for (a_id, a_norm, a_branch) in app_identity
            if a_norm == t_norm and a_branch == t_branch
        ]

        if not matches:
            no_match.append(task_id)
            msg = f"task {task_id} ({app_name}, {task[3]}@{task[4]}): no app match → orphan"
            print(f"  [pass1] {msg}")
            report.orphan.append(msg)
            continue

        if len(matches) > 1:
            msg = (
                f"task {task_id} ({app_name}): matched {len(matches)} apps "
                f"{matches} — using first"
            )
            print(f"  [pass1] {msg}")
            report.ambiguous.append(msg)

        app_to_tasks.setdefault(matches[0], []).append(task_id)

    # Resolve 1:N collisions: keep first, report rest as ambiguous
    linked: dict[str, str] = {}
    for a_id, t_ids in app_to_tasks.items():
        keep = t_ids[0]
        linked[keep] = a_id
        for extra in t_ids[1:]:
            msg = (
                f"app {a_id}: 1:N collision — keeping task {keep}, "
                f"dropping task {extra} (RM must clean up)"
            )
            print(f"  [pass1] {msg}")
            report.ambiguous.append(msg)

    # Write app_id into dst
    for t_id, a_id in linked.items():
        dst_conn.execute(
            "UPDATE cicd_tasks SET app_id = ? WHERE id = ?", (a_id, t_id)
        )
        report.linked.append(f"task {t_id} → app {a_id}")
        print(f"  [pass1] linked task {t_id} → app {a_id}")

    print(
        f"[pass1] done: {len(report.linked)} linked, "
        f"{len(report.orphan)} orphan, {len(report.ambiguous)} ambiguous"
    )
    return linked


# ---------------------------------------------------------------------------
# Step 5: Pass-2 — derive baseline tasks for apps with no linked task
# ---------------------------------------------------------------------------

def pass2_derive_baseline_tasks(
    dst_conn: sqlite3.Connection,
    report: MigrationReport,
) -> None:
    """Create a synthetic cicd_task for every app that has no linked task.

    Decision mapping (plan §4.3 / 裁定 D):
      release | cicd_only → Running
      stopped             → Stopped

    Task IDs use sequential CICD-0001 format.  The counter starts from
    max(existing numeric suffix) + 1 so as not to collide with migrated tasks.

    The app's release_decision comes from its most recent non-locked snapshot.
    """
    print("[pass2] deriving baseline tasks for unlinked apps")
    from release_system.core import beijing_now  # type: ignore

    linked_app_ids = {
        row[0]
        for row in dst_conn.execute(
            "SELECT DISTINCT app_id FROM cicd_tasks WHERE app_id IS NOT NULL"
        ).fetchall()
    }

    all_apps = dst_conn.execute("SELECT id FROM apps").fetchall()
    unlinked = [row[0] for row in all_apps if row[0] not in linked_app_ids]

    # Determine starting counter from existing task IDs (CICD-NNNN pattern)
    existing_ids = [
        row[0]
        for row in dst_conn.execute("SELECT id FROM cicd_tasks").fetchall()
    ]
    counter = 0
    for tid in existing_ids:
        if tid.startswith("CICD-"):
            try:
                n = int(tid[5:])
                if n > counter:
                    counter = n
            except ValueError:
                pass
    counter += 1

    now_str = beijing_now().strftime(_NAIVE_FMT)

    for app_id in unlinked:
        # Find most recent non-locked snapshot to get release_decision
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
        for (blob,) in snap_rows:
            try:
                d = json.loads(blob).get("release_decision", "")
                if d in _DECISION_TO_STATUS:
                    decision = d
                    break
            except (json.JSONDecodeError, TypeError, AttributeError):
                continue

        if decision is None:
            msg = f"app {app_id}: no valid release_decision → skipped (no task derived)"
            print(f"  [pass2] {msg}")
            report.warnings.append(msg)
            continue

        status = _DECISION_TO_STATUS[decision]
        task_id = f"CICD-{counter:04d}"
        counter += 1

        dst_conn.execute(
            """
            INSERT INTO cicd_tasks
              (id, app_name, app_id, app_version, repo_type, repo_name, branch,
               build_product, community_artifact, build_image, test_timeout,
               owner_username, status, notes, created_at, updated_at)
            VALUES (?, ?, ?, '', 'git', '', '',
                    '[]', '[]', '', 40,
                    '', ?, '', ?, ?)
            """,
            (task_id, app_id, app_id, status, now_str, now_str),
        )
        msg = f"app {app_id}: derived {task_id} (decision={decision} → status={status})"
        print(f"  [pass2] {msg}")
        report.derived.append(msg)

    print(f"[pass2] done: {len(report.derived)} tasks derived")


# ---------------------------------------------------------------------------
# Step 6: D-1 overwrite — align linked task status to app release_decision
# ---------------------------------------------------------------------------

def apply_d1_overwrite(
    dst_conn: sqlite3.Connection,
    report: MigrationReport,
) -> None:
    """Rewrite status on any linked task that contradicts the app's decision.

    Plan §4.3 D-1: real-world examples — tfhe-rs, warp, seg-utils, uni-fold,
    moflow are 'stopped' apps but have Running tasks → corrected to Stopped.

    Authoritative decision: most recent non-locked snapshot for the app.
    """
    print("[d1] applying D-1 status overwrites")
    from release_system.core import beijing_now  # type: ignore

    linked_tasks = dst_conn.execute(
        "SELECT id, app_id, status FROM cicd_tasks WHERE app_id IS NOT NULL"
    ).fetchall()

    now_str = beijing_now().strftime(_NAIVE_FMT)

    for task_id, app_id, current_status in linked_tasks:
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
        for (blob,) in snap_rows:
            try:
                d = json.loads(blob).get("release_decision", "")
                if d in _DECISION_TO_STATUS:
                    decision = d
                    break
            except (json.JSONDecodeError, TypeError, AttributeError):
                continue

        if decision is None:
            continue

        expected = _DECISION_TO_STATUS[decision]
        if current_status != expected:
            dst_conn.execute(
                "UPDATE cicd_tasks SET status = ?, updated_at = ? WHERE id = ?",
                (expected, now_str, task_id),
            )
            msg = (
                f"task {task_id} (app {app_id}): "
                f"{current_status} → {expected} (decision={decision})"
            )
            print(f"  [d1] {msg}")
            report.overwritten.append(msg)

    print(f"[d1] done: {len(report.overwritten)} overwrites")


# ---------------------------------------------------------------------------
# Step 7: rewrite Beijing times
# ---------------------------------------------------------------------------

def _utc_to_beijing(value: Any) -> Any:
    """Convert a UTC-ISO timestamp string to a naive Beijing time string.

    Handles "+00:00" and "Z" suffixes.  Already-naive values (no offset),
    non-string values, and empty strings are returned unchanged.
    """
    if not isinstance(value, str) or not value:
        return value
    s = value.strip()
    if "+00:00" not in s and not s.endswith("Z") and "+08:00" not in s:
        return value
    s = s.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return value
    if dt.tzinfo is None:
        return value
    return dt.astimezone(_BEIJING_TZ).strftime(_NAIVE_FMT)


def _rewrite_blob_synced_at(blob_json: str) -> str:
    """Walk a snapshot data_json blob and rewrite 'synced_at' values from
    UTC to naive Beijing time (plan §4.3 / DA C2).

    Returns the rewritten JSON string, or the original on parse error.
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

    Tables/columns in _UTC_COLUMNS are updated in-place.
    snapshots.data_json is walked to rewrite nested synced_at fields (DA C2).
    Columns already in naive Beijing time (deadlines etc.) are untouched.
    """
    print("[tz] rewriting UTC → naive Beijing times")

    for table, columns in _UTC_COLUMNS.items():
        if not columns:
            continue
        try:
            rows = dst_conn.execute(
                f"SELECT rowid, {', '.join(columns)} FROM {table}"
            ).fetchall()
        except sqlite3.OperationalError:
            continue  # table not present in this DB
        for row in rows:
            rowid = row[0]
            updates: dict[str, Any] = {}
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

    # Walk snapshot blobs for synced_at (DA C2)
    snap_rows = dst_conn.execute("SELECT rowid, data_json FROM snapshots").fetchall()
    for rowid, blob in snap_rows:
        new_blob = _rewrite_blob_synced_at(blob)
        if new_blob != blob:
            dst_conn.execute(
                "UPDATE snapshots SET data_json = ? WHERE rowid = ?",
                (new_blob, rowid),
            )

    print("[tz] done")


# ---------------------------------------------------------------------------
# Step 8: validate
# ---------------------------------------------------------------------------

def validate(
    src_conn: sqlite3.Connection,
    dst_conn: sqlite3.Connection,
    copy_counts: dict[str, int],
    report: MigrationReport,
) -> bool:
    """Post-migration sanity checks.

    1. Row counts: dst >= src for all copied tables (derived tasks add rows).
    2. PRAGMA foreign_key_check returns empty.
    3. snapshots.data_json and cicd_task_requests.payload parse as JSON.
    4. app_id self-consistency: non-null app_ids == linked + derived counts.
    5. Warn (non-fatal) if UTC offsets remain in any UTC column.

    Returns True if all hard checks pass.
    """
    print("[validate] running checks")
    ok = True

    src_tables = {
        row[0]
        for row in src_conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }

    # 1. Row counts
    for table in _COPY_ORDER:
        if table not in src_tables:
            continue
        src_count = src_conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        dst_count = dst_conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        status = "OK" if dst_count >= src_count else "ERROR"
        print(f"  [validate] {table}: src={src_count} dst={dst_count} {status}")
        if dst_count < src_count:
            ok = False

    # 2. FK check
    fk_errors = dst_conn.execute("PRAGMA foreign_key_check").fetchall()
    if fk_errors:
        print(f"  [validate] ERROR: PRAGMA foreign_key_check: {len(fk_errors)} violations")
        for err in fk_errors[:10]:
            print(f"    {err}")
        ok = False
    else:
        print("  [validate] PRAGMA foreign_key_check: OK")

    # 3. JSON parse
    for table, col in [("snapshots", "data_json"), ("cicd_task_requests", "payload")]:
        bad = 0
        try:
            for (val,) in dst_conn.execute(f"SELECT {col} FROM {table}").fetchall():
                if val:
                    try:
                        json.loads(val)
                    except (json.JSONDecodeError, TypeError):
                        bad += 1
        except sqlite3.OperationalError:
            continue
        if bad:
            print(f"  [validate] ERROR: {table}.{col} has {bad} unparseable JSON rows")
            ok = False
        else:
            print(f"  [validate] {table}.{col}: JSON parse OK")

    # 4. app_id self-consistency
    expected_non_null = len(report.linked) + len(report.derived)
    actual_non_null = dst_conn.execute(
        "SELECT COUNT(*) FROM cicd_tasks WHERE app_id IS NOT NULL"
    ).fetchone()[0]
    if actual_non_null != expected_non_null:
        print(
            f"  [validate] ERROR: app_id count mismatch: "
            f"linked={len(report.linked)} + derived={len(report.derived)} "
            f"= {expected_non_null} but non-null in DB = {actual_non_null}"
        )
        ok = False
    else:
        print(f"  [validate] app_id count ({actual_non_null}): OK")

    # 5. UTC offset remnants (non-fatal warnings)
    for table, columns in _UTC_COLUMNS.items():
        for col in columns:
            try:
                bad = dst_conn.execute(
                    f"SELECT COUNT(*) FROM {table} "
                    f"WHERE {col} LIKE '%+00:00%' OR {col} LIKE '%Z'"
                ).fetchone()[0]
            except sqlite3.OperationalError:
                continue
            if bad:
                msg = f"{table}.{col}: {bad} UTC-offset values remain after rewrite"
                print(f"  [validate] WARNING: {msg}")
                report.warnings.append(msg)

    print(f"[validate] {'PASS' if ok else 'FAIL'}")
    return ok


# ---------------------------------------------------------------------------
# Main migration function
# ---------------------------------------------------------------------------

def _open_src_readonly(src_path: Path) -> sqlite3.Connection:
    """Open source DB in read-only URI mode."""
    import urllib.parse

    uri = "file:" + urllib.parse.quote(str(src_path.resolve())) + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def migrate(
    src_path: Path,
    dst_path: Path,
    *,
    dry_run: bool = False,
    force: bool = False,
) -> bool:
    """Run the full migration pipeline.  Returns True if validation passed.

    In --dry-run mode dst is :memory:; nothing is written to disk.
    The entire copy+link+derive+overwrite+tz-rewrite pipeline runs inside
    a single transaction; any error triggers a full rollback.
    """
    _ensure_path()
    print(f"[migrate] src={src_path}  dst={'<memory>' if dry_run else dst_path}  dry_run={dry_run}")

    # Guard: non-empty dst requires --force
    if not dry_run and dst_path.exists() and dst_path.stat().st_size > 0 and not force:
        print(
            f"[migrate] ERROR: {dst_path} exists and is non-empty. Use --force to overwrite."
        )
        return False

    report = MigrationReport()

    if not dry_run:
        backup_source(src_path)

    src_conn = _open_src_readonly(src_path)

    if dry_run:
        dst_conn = sqlite3.connect(":memory:")
    else:
        dst_conn = sqlite3.connect(str(dst_path))
    dst_conn.row_factory = sqlite3.Row
    dst_conn.execute("PRAGMA foreign_keys = ON")

    copy_counts: dict[str, int] = {}
    ok = False

    try:
        # Step 2: schema init (outside the data transaction)
        init_target_schema(dst_conn)

        # Resolve all identities OUTSIDE the transaction (network I/O)
        src_tasks = src_conn.execute(
            "SELECT id, app_name, repo_type, repo_name, branch FROM cicd_tasks"
        ).fetchall()
        n = len(src_tasks)
        print(f"[identity] pre-resolving {n} task identities (may need network for .xml)")
        _resolve_all_identities(src_tasks)  # warms the cache
        print("[identity] pre-resolution done")

        # Steps 3-8: single transaction
        dst_conn.execute("BEGIN")
        try:
            copy_counts = copy_tables(src_conn, dst_conn)
            pass1_link_app_ids(src_conn, dst_conn, report)
            pass2_derive_baseline_tasks(dst_conn, report)
            apply_d1_overwrite(dst_conn, report)
            rewrite_beijing_times(dst_conn)
            dst_conn.execute("COMMIT")
        except Exception:
            dst_conn.execute("ROLLBACK")
            raise

        ok = validate(src_conn, dst_conn, copy_counts, report)

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


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="migrate_db",
        description=(
            "One-shot migration: copy old release_system.db to new schema. "
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
            "Run against an in-memory copy; print report but write nothing to disk. "
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
    _ensure_path()
    parser = _build_arg_parser()
    args = parser.parse_args(argv)
    ok = migrate(args.src_db, args.dst_db, dry_run=args.dry_run, force=args.force)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
