"""One-time conversion from the per-release snapshot model to monthly signups.

Old model: every release cloned a JSON ``snapshots`` row for every App and
carried a ``release_decision`` (release / cicd_only / stopped).  New model:
App profile, CICD status and app_info history live on the App; a release only
holds the Apps that signed up (``entries``).

Conversion rules (agreed in the monthly-release plan):

* Each App's profile (name, type, owners, doc fields …) comes from its most
  recent snapshot.
* ``apps.cicd_status`` is ``stopped`` when that snapshot's decision was
  ``stopped``, otherwise ``running``.
* Every distinct app_info seen in any snapshot becomes an
  ``app_info_versions`` row (oldest release first).
* Only ``release`` snapshots become entries.  In a locked release the entry
  result is ``released`` when the QA status was releasable, else ``failed``.
* Only the newest locked release writes ``official_versions`` — that release
  (3.10.0 at cut-over) defines the first compatibility list.
* ``maca_version`` becomes a ``MACA`` row of the release environment.
* CICD request payloads lose the removed decision-sync / CICD-first keys
  and use lowercase ``running`` / ``stopped``.
* The old ``[dingtalk]`` robot is copied to the two existing notification
  kinds.

Open CICD requests make the conversion ambiguous (they may carry the removed
decision-sync / CICD-first semantics), so conversion refuses to run until
they are closed on the old site.
"""
from __future__ import annotations

import json
import sqlite3
from typing import Any

from app.domain import app_info as app_info_domain
from app.domain import qa as qa_domain
from app.domain.app_profile import normalize_doc_target
from app.timeutil import beijing_timestamp

_REMOVED_PAYLOAD_KEYS = (
    "_cicd_first_action",
    "_cicd_first_target_decision",
    "_cicd_first_release_decisions",
    "_decision_sync_release_id",
    "_decision_sync_target_decision",
    "_decision_sync_deferred_releases",
    "_decision_sync_rollback",
    "release_decision",
)

_NEW_APP_COLUMNS = (
    ("cicd_status", "TEXT NOT NULL DEFAULT 'running'"),
    ("official_name", "TEXT NOT NULL DEFAULT ''"),
    ("app_type", "TEXT NOT NULL DEFAULT ''"),
    ("official_url", "TEXT NOT NULL DEFAULT ''"),
    ("description", "TEXT NOT NULL DEFAULT ''"),
    ("doc_target", "TEXT NOT NULL DEFAULT 'manual'"),
    ("owners_json", "TEXT NOT NULL DEFAULT '[]'"),
    ("doc_json", "TEXT NOT NULL DEFAULT '{}'"),
    ("community_json", "TEXT NOT NULL DEFAULT '{}'"),
    ("sanity_json", "TEXT NOT NULL DEFAULT '{}'"),
    ("test_docs_json", "TEXT NOT NULL DEFAULT '[]'"),
    ("sync_head_commit", "TEXT NOT NULL DEFAULT ''"),
    ("sync_checked_at", "TEXT NOT NULL DEFAULT ''"),
    ("sync_error", "TEXT NOT NULL DEFAULT ''"),
)


def needs_migration(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'snapshots'"
    ).fetchone()
    return row is not None


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _ensure_no_open_cicd_requests(conn: sqlite3.Connection) -> None:
    rows = conn.execute(
        """
        SELECT id FROM cicd_task_requests
        WHERE status = 'pending' OR delivery_status IN ('pending', 'returned')
        ORDER BY id
        """
    ).fetchall()
    if rows:
        ids = ", ".join(f"#{row[0]}" for row in rows)
        raise RuntimeError(
            "数据库迁移到月度发布模型前，必须先在旧网站处理完所有未完成的 CICD 申请："
            + ids
        )


def rebuild_tables(conn: sqlite3.Connection) -> None:
    """Rebuild ``releases`` with the new columns and extend ``apps``.

    Runs before the current DDL so ``CREATE TABLE IF NOT EXISTS`` sees the new
    shapes.  Uses the SQLite table-rebuild procedure with foreign keys off so
    dependent rows (artifacts, QA logs, snapshots) are kept.
    """
    _ensure_no_open_cicd_requests(conn)
    conn.commit()
    conn.execute("PRAGMA foreign_keys = OFF")
    try:
        conn.execute("BEGIN")
        conn.execute(
            """
            CREATE TABLE releases_monthly (
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
            )
            """
        )
        conn.execute(
            """
            INSERT INTO releases_monthly(
                id, name, signup_deadline, qa_deadline, release_date,
                released_locked, released_locked_at, released_locked_by,
                created_at, created_by)
            SELECT id, name, app_freeze_deadline, doc_deadline,
                   CASE WHEN released_locked_at <> '' THEN substr(released_locked_at, 1, 10)
                        ELSE substr(doc_deadline, 1, 10) END,
                   released_locked, released_locked_at, released_locked_by,
                   created_at, ''
            FROM releases
            """
        )
        # The old maca_version is carried over as an environment row by
        # convert_snapshots(); keep it in a scratch table until then.
        conn.execute(
            "CREATE TABLE monthly_migration_maca AS SELECT id, maca_version FROM releases"
        )
        conn.execute("DROP TABLE releases")
        conn.execute("ALTER TABLE releases_monthly RENAME TO releases")
        existing = {row[1] for row in conn.execute("PRAGMA table_info(apps)")}
        for column, definition in _NEW_APP_COLUMNS:
            if column not in existing:
                conn.execute(f"ALTER TABLE apps ADD COLUMN {column} {definition}")
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.execute("PRAGMA foreign_keys = ON")


def _store_version(
    conn: sqlite3.Connection,
    cache: dict[tuple[str, str], int],
    app_id: str,
    app_info: dict[str, Any] | None,
) -> int | None:
    if not isinstance(app_info, dict):
        return None
    parsed = app_info.get("parsed")
    raw = parsed.get("raw") if isinstance(parsed, dict) else None
    if not isinstance(raw, dict):
        return None
    content_hash = app_info_domain.content_hash(raw)
    key = (app_id, content_hash)
    if key in cache:
        return cache[key]
    cur = conn.execute(
        """
        INSERT INTO app_info_versions(app_id, commit_id, content_hash, raw_json,
                                      source, fetched_at, fetched_by)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            app_id,
            str(app_info.get("commit_id") or ""),
            content_hash,
            app_info_domain.canonical_json(raw),
            str(app_info.get("source") or "migration"),
            str(app_info.get("uploaded_at") or beijing_timestamp()),
            str(app_info.get("uploaded_by") or "migration"),
        ),
    )
    cache[key] = int(cur.lastrowid)
    return cache[key]


def _rebuild_dependencies(conn: sqlite3.Connection, app_id: str, version_id: int) -> None:
    raw_json = conn.execute(
        "SELECT raw_json FROM app_info_versions WHERE id = ?", (version_id,)
    ).fetchone()[0]
    components = app_info_domain.declared_dependencies(json.loads(raw_json)) or []
    conn.execute("DELETE FROM app_dependencies WHERE app_id = ?", (app_id,))
    for component in components:
        conn.execute(
            "INSERT OR IGNORE INTO app_dependencies(app_id, component) VALUES (?, ?)",
            (app_id, component),
        )


def _copy_dingtalk_robot(conn: sqlite3.Connection) -> None:
    from app import runtime_config

    try:
        values = runtime_config.section("dingtalk")
    except Exception:  # noqa: BLE001 - a missing/broken conf just means no robot
        return
    token = str((values or {}).get("ACCESS_TOKEN") or "").strip()
    if not token:
        return
    users = [
        item.strip()
        for item in str(values.get("USERID") or "").split(",")
        if item.strip()
    ]
    for kind in ("jira_agent_turn", "jenkins_failures"):
        conn.execute(
            """
            INSERT OR IGNORE INTO notification_channels(
                kind, enabled, access_token, secret, at_user_ids_json, updated_at, updated_by)
            VALUES (?, 1, ?, ?, ?, ?, 'migration')
            """,
            (kind, token, str(values.get("SECRET") or ""), _dumps(users), beijing_timestamp()),
        )


def convert_snapshots(conn: sqlite3.Connection) -> None:
    """Convert snapshot rows into App profiles, versions and entries."""
    releases = [
        dict(row)
        for row in conn.execute("SELECT * FROM releases ORDER BY created_at, rowid")
    ]
    maca = {
        row[0]: str(row[1] or "").strip()
        for row in conn.execute("SELECT id, maca_version FROM monthly_migration_maca")
    }
    latest_locked = [r for r in releases if r["released_locked"]]
    official_release_id = latest_locked[-1]["id"] if latest_locked else ""
    version_cache: dict[tuple[str, str], int] = {}
    latest_snapshot: dict[str, dict[str, Any]] = {}
    latest_version: dict[str, int] = {}
    now = beijing_timestamp()

    for release in releases:
        release_id = release["id"]
        if maca.get(release_id):
            conn.execute(
                "INSERT INTO cycle_environment(release_id, component, version, note, sort)"
                " VALUES (?, 'MACA', ?, '', 0)",
                (release_id, maca[release_id]),
            )
        rows = conn.execute(
            "SELECT app_id, data_json FROM snapshots WHERE release_id = ? ORDER BY app_id",
            (release_id,),
        ).fetchall()
        for app_id, data_json in rows:
            snapshot = json.loads(data_json or "{}")
            latest_snapshot[app_id] = snapshot
            version_id = _store_version(conn, version_cache, app_id, snapshot.get("app_info"))
            if version_id is not None:
                latest_version[app_id] = version_id
            decision = str(snapshot.get("release_decision") or "release")
            if decision != "release":
                continue
            qa_status = str(snapshot.get("qa_status") or qa_domain.QA_STATUS_DEFAULT)
            if qa_status not in qa_domain.QA_STATUSES:
                qa_status = qa_domain.QA_STATUS_DEFAULT
            result = ""
            if release["released_locked"]:
                result = "released" if qa_domain.is_releasable(qa_status) else "failed"
            stamp = release["released_locked_at"] or release["created_at"]
            cur = conn.execute(
                """
                INSERT INTO entries(
                    release_id, app_id, status, target_version, known_issues,
                    locked_version_id, qa_status, qa_note, result,
                    created_at, created_by, updated_at, updated_by)
                VALUES (?, ?, 'active', ?, ?, ?, ?, ?, ?, ?, 'migration', ?, 'migration')
                """,
                (
                    release_id,
                    app_id,
                    str(snapshot.get("version") or ""),
                    str((snapshot.get("doc") or {}).get("limitations") or ""),
                    version_id,
                    qa_status,
                    str(snapshot.get("qa_issue_note") or ""),
                    result,
                    release["created_at"],
                    stamp,
                ),
            )
            if result == "released" and release_id == official_release_id:
                conn.execute(
                    """
                    INSERT INTO official_versions(
                        app_id, release_id, entry_id, version, app_info_version_id,
                        released_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        app_id,
                        release_id,
                        cur.lastrowid,
                        str(snapshot.get("version") or ""),
                        version_id,
                        release["released_locked_at"] or now,
                    ),
                )

    for app_id, snapshot in latest_snapshot.items():
        stopped = str(snapshot.get("release_decision") or "") in {"stopped", "no_release"}
        conn.execute(
            """
            UPDATE apps SET
                cicd_status = ?, official_name = ?, app_type = ?, official_url = ?,
                description = ?, doc_target = ?, owners_json = ?, doc_json = ?,
                community_json = ?, sanity_json = ?, test_docs_json = ?,
                sync_head_commit = ?
            WHERE id = ?
            """,
            (
                "stopped" if stopped else "running",
                str(snapshot.get("official_name") or ""),
                str(snapshot.get("type") or ""),
                str(snapshot.get("official_url") or ""),
                str(snapshot.get("description") or ""),
                normalize_doc_target(snapshot.get("doc_target")),
                _dumps(sorted(set(snapshot.get("owners") or []))),
                _dumps(snapshot.get("doc") or {}),
                _dumps(snapshot.get("community") or {}),
                _dumps(snapshot.get("sanity") or {}),
                _dumps(snapshot.get("test_docs") or []),
                str((snapshot.get("app_info") or {}).get("commit_id") or ""),
                app_id,
            ),
        )
    for app_id, version_id in latest_version.items():
        _rebuild_dependencies(conn, app_id, version_id)

    for row in conn.execute("SELECT id, payload FROM cicd_task_requests").fetchall():
        try:
            payload = json.loads(row[1] or "{}")
        except ValueError:
            continue
        if not isinstance(payload, dict):
            continue
        changed = any(key in payload for key in _REMOVED_PAYLOAD_KEYS)
        for key in _REMOVED_PAYLOAD_KEYS:
            payload.pop(key, None)
        status = payload.get("status")
        if isinstance(status, str) and status != status.lower():
            payload["status"] = status.lower()
            changed = True
        if isinstance(status, dict):
            lowered = {k: str(v).lower() for k, v in status.items()}
            if lowered != status:
                payload["status"] = lowered
                changed = True
        if changed:
            conn.execute(
                "UPDATE cicd_task_requests SET payload = ? WHERE id = ?",
                (_dumps(payload), row[0]),
            )

    _copy_dingtalk_robot(conn)
    conn.execute("DROP TABLE snapshots")
    conn.execute("DROP TABLE monthly_migration_maca")
