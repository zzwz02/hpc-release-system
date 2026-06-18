#!/usr/bin/env python3
"""Migrate CICD request ownership from legacy cicd_tasks to apps.

This is the post-cutover migration for the app-backed CICD design:

  - cicd_task_requests.app_id becomes the canonical relation to apps(id)
  - task_id is kept as a compatibility alias and is set to app_id when possible
  - legacy cicd_tasks can be cleared after requests are migrated

The script operates on a copy of the DB path supplied by the caller.  Use
--dry-run to only print the migration report and leave the DB unchanged.
"""
from __future__ import annotations

import argparse
import shutil
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.identity import same_identity


def _connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def _table_names(conn: sqlite3.Connection) -> set[str]:
    return {
        row["name"]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }


def _columns(conn: sqlite3.Connection, table: str) -> list[str]:
    return [row["name"] for row in conn.execute(f"PRAGMA table_info({table})")]


def _ensure_tables(conn: sqlite3.Connection) -> None:
    names = _table_names(conn)
    missing = {"apps", "cicd_task_requests"} - names
    if missing:
        raise RuntimeError(f"missing table(s): {', '.join(sorted(missing))}")


def _resolve_app_id(conn: sqlite3.Connection, task_id: str | None, payload: str) -> str | None:
    if task_id:
        row = conn.execute("SELECT id FROM apps WHERE id = ?", (task_id,)).fetchone()
        if row:
            return task_id
        if "cicd_tasks" in _table_names(conn):
            row = conn.execute(
                "SELECT app_id FROM cicd_tasks WHERE id = ? AND app_id IS NOT NULL",
                (task_id,),
            ).fetchone()
            if row and row["app_id"]:
                return row["app_id"]
    try:
        import json

        data = json.loads(payload or "{}")
    except Exception:
        data = {}
    app_id = data.get("app_id") if isinstance(data, dict) else None
    if app_id:
        row = conn.execute("SELECT id FROM apps WHERE id = ?", (app_id,)).fetchone()
        if row:
            return str(app_id)
    repo_name, branch = _payload_identity(data)
    if repo_name and branch:
        matches = []
        for row in conn.execute("SELECT id, git_url, git_branch FROM apps").fetchall():
            if same_identity(row["git_url"], row["git_branch"], repo_name, branch):
                matches.append(row["id"])
        if len(matches) == 1:
            return matches[0]
    return None


def _change_value(value: object, key: str) -> str:
    if isinstance(value, dict):
        return str(value.get(key) or value.get("new") or value.get("old") or "").strip()
    return str(value or "").strip()


def _payload_identity(data: object) -> tuple[str, str]:
    if not isinstance(data, dict):
        return "", ""
    repo_raw = data.get("repo_name")
    branch_raw = data.get("branch")
    candidates = [
        (_change_value(repo_raw, "new"), _change_value(branch_raw, "new")),
        (_change_value(repo_raw, "old"), _change_value(branch_raw, "old")),
        (_change_value(repo_raw, ""), _change_value(branch_raw, "")),
    ]
    for repo_name, branch in candidates:
        if repo_name and branch:
            return repo_name, branch
    return "", ""


def _report(conn: sqlite3.Connection) -> dict[str, int]:
    _ensure_tables(conn)
    req_cols = _columns(conn, "cicd_task_requests")
    has_app_id = "app_id" in req_cols
    rows = conn.execute("SELECT * FROM cicd_task_requests ORDER BY id").fetchall()
    total = len(rows)
    resolved = 0
    unresolved = 0
    already = 0
    for row in rows:
        if has_app_id and row["app_id"]:
            app = conn.execute("SELECT id FROM apps WHERE id = ?", (row["app_id"],)).fetchone()
            if app:
                already += 1
                resolved += 1
                continue
        app_id = _resolve_app_id(conn, row["task_id"], row["payload"])
        if app_id:
            resolved += 1
        else:
            unresolved += 1
    return {
        "requests_total": total,
        "requests_resolved": resolved,
        "requests_already_app_id": already,
        "requests_unresolved": unresolved,
    }


def _rebuild_requests(conn: sqlite3.Connection, *, drop_unresolved: bool) -> None:
    req_cols = _columns(conn, "cicd_task_requests")
    has_app_id = "app_id" in req_cols

    rows = conn.execute("SELECT * FROM cicd_task_requests ORDER BY id").fetchall()
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.execute("DROP TABLE IF EXISTS cicd_task_requests_new")
    conn.execute(
        """
        CREATE TABLE cicd_task_requests_new (
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
            origin TEXT NOT NULL DEFAULT 'cicd_workbench'
        )
        """
    )
    inserted = 0
    skipped = 0
    for row in rows:
        app_id = row["app_id"] if has_app_id and row["app_id"] else None
        if not app_id:
            app_id = _resolve_app_id(conn, row["task_id"], row["payload"])
        if not app_id:
            if drop_unresolved:
                skipped += 1
                continue
            raise RuntimeError(
                f"request #{row['id']} cannot be mapped to apps.id; "
                "rerun with --drop-unresolved to discard orphan requests"
            )
        conn.execute(
            """
            INSERT INTO cicd_task_requests_new (
                id, task_id, app_id, request_type, payload, submitter,
                submitter_display, submitted_at, status, reviewer, reviewed_at,
                review_note, is_self_approved, approval_mode, delivery_status,
                jira_id, jira_auto_created, delivered_by, delivered_at,
                returned_reason, returned_at, origin
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row["id"],
                app_id,
                app_id,
                row["request_type"],
                row["payload"],
                row["submitter"],
                row["submitter_display"] if "submitter_display" in req_cols else "",
                row["submitted_at"],
                row["status"],
                row["reviewer"],
                row["reviewed_at"],
                row["review_note"],
                row["is_self_approved"],
                row["approval_mode"] if "approval_mode" in req_cols else "immediate",
                row["delivery_status"] if "delivery_status" in req_cols else "",
                row["jira_id"] if "jira_id" in req_cols else "",
                row["jira_auto_created"] if "jira_auto_created" in req_cols else 0,
                row["delivered_by"] if "delivered_by" in req_cols else "",
                row["delivered_at"] if "delivered_at" in req_cols else "",
                row["returned_reason"] if "returned_reason" in req_cols else "",
                row["returned_at"] if "returned_at" in req_cols else "",
                row["origin"] if "origin" in req_cols else "cicd_workbench",
            ),
        )
        inserted += 1
    conn.execute("DROP TABLE cicd_task_requests")
    conn.execute("ALTER TABLE cicd_task_requests_new RENAME TO cicd_task_requests")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_cicd_task_requests_status_delivery "
        "ON cicd_task_requests(status, delivery_status)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_cicd_task_requests_task_id "
        "ON cicd_task_requests(task_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_cicd_task_requests_app_id "
        "ON cicd_task_requests(app_id)"
    )
    conn.execute("PRAGMA foreign_keys = ON")
    print(f"rewrote cicd_task_requests: inserted={inserted} skipped={skipped}")


def migrate(path: Path, *, dry_run: bool, drop_unresolved: bool, clear_cicd_tasks: bool) -> int:
    if not path.exists():
        print(f"ERROR: DB not found: {path}", file=sys.stderr)
        return 2

    backup_path = path.with_suffix(path.suffix + ".before-cicd-app-requests.bak")
    conn = _connect(path)
    try:
        before = _report(conn)
        print(f"DB: {path}")
        for key, value in before.items():
            print(f"{key}: {value}")
        if before["requests_unresolved"] and not drop_unresolved:
            print(
                "ERROR: unresolved CICD requests exist; use --drop-unresolved "
                "to discard orphan request rows after reviewing the report",
                file=sys.stderr,
            )
            return 1
        if dry_run:
            print("dry-run: no changes written")
            return 0

        conn.close()
        shutil.copy2(path, backup_path)
        print(f"backup: {backup_path}")
        conn = _connect(path)
        with conn:
            _rebuild_requests(conn, drop_unresolved=drop_unresolved)
            if clear_cicd_tasks and "cicd_tasks" in _table_names(conn):
                count = conn.execute("SELECT COUNT(*) FROM cicd_tasks").fetchone()[0]
                conn.execute("DELETE FROM cicd_tasks")
                print(f"cleared cicd_tasks: {count} rows")
        fk_err = conn.execute("PRAGMA foreign_key_check").fetchall()
        if fk_err:
            raise RuntimeError(f"foreign_key_check failed: {fk_err}")
        after = _report(conn)
        print("after:")
        for key, value in after.items():
            print(f"{key}: {value}")
        return 0
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Migrate CICD requests to app_id-backed relations."
    )
    parser.add_argument("db", type=Path, help="SQLite DB path to migrate")
    parser.add_argument("--dry-run", action="store_true", help="Only print report")
    parser.add_argument(
        "--drop-unresolved",
        action="store_true",
        help="Drop request rows that cannot be mapped to an app",
    )
    parser.add_argument(
        "--clear-cicd-tasks",
        action="store_true",
        help="Delete all legacy cicd_tasks rows after request migration",
    )
    args = parser.parse_args(argv)
    return migrate(
        args.db,
        dry_run=args.dry_run,
        drop_unresolved=args.drop_unresolved,
        clear_cicd_tasks=args.clear_cicd_tasks,
    )


if __name__ == "__main__":
    raise SystemExit(main())
