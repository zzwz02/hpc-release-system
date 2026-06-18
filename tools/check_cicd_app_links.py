#!/usr/bin/env python3
"""Check App ↔ CICD linkage in a migrated SQLite DB.

This is intentionally SQL-only and never contacts Gerrit.  It verifies the
post-migration relational state:

  - CICD tasks whose app_id is NULL or points to a missing app
  - Apps that have no linked CICD task
  - Apps with multiple linked CICD tasks, which violates the intended 1:1 model

Default DB path matches CUTOVER.md's migrated candidate.
"""
from __future__ import annotations

import argparse
import csv
import sqlite3
import sys
from pathlib import Path
from typing import Iterable

DEFAULT_DB = Path("/tmp/release_system_candidate.db.migrated")


def _connect(path: Path) -> sqlite3.Connection:
    uri = f"file:{path}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _required_tables_exist(conn: sqlite3.Connection) -> bool:
    names = {
        row["name"]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    missing = {"apps", "cicd_tasks"} - names
    if missing:
        print(f"ERROR: missing table(s): {', '.join(sorted(missing))}", file=sys.stderr)
        return False
    return True


def _rows(conn: sqlite3.Connection) -> dict[str, list[sqlite3.Row]]:
    return {
        "orphan_tasks": conn.execute(
            """
            SELECT id, app_name, app_id, repo_type, repo_name, branch, status, owner_username
            FROM cicd_tasks
            WHERE app_id IS NULL
            ORDER BY id
            """
        ).fetchall(),
        "bad_fk_tasks": conn.execute(
            """
            SELECT t.id, t.app_name, t.app_id, t.repo_type, t.repo_name, t.branch,
                   t.status, t.owner_username
            FROM cicd_tasks t
            LEFT JOIN apps a ON a.id = t.app_id
            WHERE t.app_id IS NOT NULL AND a.id IS NULL
            ORDER BY t.id
            """
        ).fetchall(),
        "apps_without_cicd": conn.execute(
            """
            SELECT a.id, a.git_url, a.git_branch
            FROM apps a
            LEFT JOIN cicd_tasks t ON t.app_id = a.id
            WHERE t.id IS NULL
            ORDER BY a.id
            """
        ).fetchall(),
        "apps_with_multiple_cicd": conn.execute(
            """
            SELECT a.id, a.git_url, a.git_branch,
                   COUNT(t.id) AS task_count,
                   GROUP_CONCAT(t.id, ', ') AS task_ids
            FROM apps a
            JOIN cicd_tasks t ON t.app_id = a.id
            GROUP BY a.id, a.git_url, a.git_branch
            HAVING COUNT(t.id) > 1
            ORDER BY a.id
            """
        ).fetchall(),
    }


def _print_table(title: str, rows: Iterable[sqlite3.Row], limit: int) -> None:
    rows = list(rows)
    print(f"\n{title}: {len(rows)}")
    if not rows:
        return
    shown = rows if limit <= 0 else rows[:limit]
    cols = rows[0].keys()
    print("  " + " | ".join(cols))
    for row in shown:
        print("  " + " | ".join(str(row[col] if row[col] is not None else "") for col in cols))
    if limit > 0 and len(rows) > limit:
        print(f"  ... {len(rows) - limit} more")


def _write_csv(out_dir: Path, groups: dict[str, list[sqlite3.Row]]) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, rows in groups.items():
        path = out_dir / f"{name}.csv"
        with path.open("w", newline="", encoding="utf-8") as f:
            if not rows:
                f.write("")
                continue
            writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(dict(row) for row in rows)
        print(f"wrote {path}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Check migrated DB App ↔ CICD linkage."
    )
    parser.add_argument(
        "db",
        nargs="?",
        type=Path,
        default=DEFAULT_DB,
        help=f"SQLite DB path (default: {DEFAULT_DB})",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=30,
        help="Rows to print per section; use 0 for all rows (default: 30).",
    )
    parser.add_argument(
        "--csv-dir",
        type=Path,
        help="Optional directory to write one CSV per finding category.",
    )
    args = parser.parse_args(argv)

    if not args.db.exists():
        print(f"ERROR: DB not found: {args.db}", file=sys.stderr)
        return 2

    conn = _connect(args.db)
    try:
        if not _required_tables_exist(conn):
            return 2

        groups = _rows(conn)
        total_apps = conn.execute("SELECT COUNT(*) FROM apps").fetchone()[0]
        total_tasks = conn.execute("SELECT COUNT(*) FROM cicd_tasks").fetchone()[0]
        linked_tasks = conn.execute(
            "SELECT COUNT(*) FROM cicd_tasks WHERE app_id IS NOT NULL"
        ).fetchone()[0]
        linked_apps = conn.execute(
            "SELECT COUNT(DISTINCT app_id) FROM cicd_tasks WHERE app_id IS NOT NULL"
        ).fetchone()[0]

        print(f"DB: {args.db}")
        print(f"apps: {total_apps}")
        print(f"cicd_tasks: {total_tasks}")
        print(f"linked_tasks: {linked_tasks}")
        print(f"linked_apps: {linked_apps}")

        _print_table("CICD tasks without app", groups["orphan_tasks"], args.limit)
        _print_table("CICD tasks with missing app FK", groups["bad_fk_tasks"], args.limit)
        _print_table("Apps without CICD task", groups["apps_without_cicd"], args.limit)
        _print_table("Apps with multiple CICD tasks", groups["apps_with_multiple_cicd"], args.limit)

        if args.csv_dir:
            _write_csv(args.csv_dir, groups)

        has_findings = any(groups[name] for name in groups)
        return 1 if has_findings else 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
