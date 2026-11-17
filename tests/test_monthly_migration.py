"""One-time conversion of an old-schema database to the monthly signup model.

The fixture below builds a small database in the pre-rewrite shape (releases
with app_freeze_deadline / doc_deadline / maca_version, a JSON ``snapshots``
row per App with a ``release_decision``) and opens it with the current
connect(), which runs app/db/monthly_migration.py exactly once.
"""
from __future__ import annotations

import json
import sqlite3

import pytest

from app.db.connection import connect, reset_init_state
from app.domain import app_info as app_info_domain

OLD_SCHEMA = """
CREATE TABLE releases (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    maca_version TEXT NOT NULL DEFAULT '',
    app_freeze_deadline TEXT NOT NULL DEFAULT '',
    doc_deadline TEXT NOT NULL DEFAULT '',
    released_locked INTEGER NOT NULL DEFAULT 0,
    released_locked_at TEXT NOT NULL DEFAULT '',
    released_locked_by TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'manual',
    cloned_from TEXT NOT NULL DEFAULT ''
);
CREATE TABLE apps (
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
CREATE TABLE snapshots (
    release_id TEXT NOT NULL,
    app_id TEXT NOT NULL,
    data_json TEXT NOT NULL,
    PRIMARY KEY (release_id, app_id)
);
CREATE TABLE cicd_task_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT,
    app_id TEXT,
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
);
"""


def app_info_doc(app_version: str, *, tests: tuple[str, ...] = ("sanity",)) -> dict:
    return {
        "app_name": "amber",
        "app_version": app_version,
        "app_dependency": ["mcpy"],
        "app_build": {
            "ubuntu20.04_amd64": {
                "arch": "amd64",
                "os": "ubuntu20.04",
                "supported_chip": ["c500"],
                "enabled": True,
            }
        },
        "app_test": {
            name: {
                "test_cmd": f"amber {name}",
                "supported_chip": {"c500": ["ubuntu20.04_amd64"]},
                "enabled": True,
            }
            for name in tests
        },
    }


def snapshot(
    app_id: str,
    *,
    decision: str = "release",
    version: str = "1.0",
    qa_status: str = "qa_passed",
    app_info: dict | None = None,
    limitations: str = "",
) -> str:
    data = {
        "app_id": app_id,
        "official_name": app_id.title(),
        "type": "分子动力学",
        "official_url": "https://example.test",
        "description": "测试应用",
        "doc_target": "manual",
        "owners": ["owner_test", "owner_test"],
        "owner_confirmed": True,
        "version": version,
        "release_decision": decision,
        "qa_status": qa_status,
        "qa_issue_note": "偶发超时" if qa_status == "has_issues" else "",
        "doc": {"intro": "x", "limitations": limitations},
        "community": {"release_status": "镜像：1"},
        "sanity": {"arm": "ok"},
        "test_docs": [{"path": "sanity", "dataset": "d"}],
        "app_info": None
        if app_info is None
        else {
            "commit_id": "c" * 12,
            "source": "gerrit",
            "uploaded_at": "2026-01-01 00:00:00",
            "uploaded_by": "owner_test",
            "parsed": {"raw": app_info},
        },
    }
    return json.dumps(data, ensure_ascii=False)


def build_old_db(path, *, open_request: bool = False) -> None:
    """Two releases (the first locked) with three Apps in the old shape."""
    old = sqlite3.connect(path)
    old.executescript(OLD_SCHEMA)
    old.executemany(
        "INSERT INTO releases(id, name, maca_version, app_freeze_deadline, doc_deadline,"
        " released_locked, released_locked_at, released_locked_by, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("rel-1", "3.9.0", "20260301", "2026-03-10 23:59", "2026-03-20 23:59",
             1, "2026-03-25 10:00:00", "rm", "2026-03-01 00:00:00"),
            ("rel-2", "3.10.0", "20260401", "2026-04-10 23:59", "2026-04-20 23:59",
             1, "2026-04-25 10:00:00", "rm", "2026-04-01 00:00:00"),
        ],
    )
    old.executemany(
        "INSERT INTO apps(id, git_url, git_branch, created_at) VALUES (?, ?, ?, ?)",
        [
            ("amber", "hpc_amber", "maca", "2026-01-01 00:00:00"),
            ("lammps", "hpc_lammps", "maca", "2026-01-01 00:00:00"),
            ("retired", "hpc_retired", "maca", "2026-01-01 00:00:00"),
        ],
    )
    old.executemany(
        "INSERT INTO snapshots(release_id, app_id, data_json) VALUES (?, ?, ?)",
        [
            # amber: same app_info in both releases, released twice
            ("rel-1", "amber", snapshot("amber", version="22", app_info=app_info_doc("22"))),
            ("rel-2", "amber", snapshot("amber", version="23", app_info=app_info_doc("22"),
                                        qa_status="has_issues", limitations="已知限制")),
            # lammps: cicd_only in the newest release -> no entry there
            ("rel-1", "lammps", snapshot("lammps", version="2024", app_info=app_info_doc("2024"))),
            ("rel-2", "lammps", snapshot("lammps", decision="cicd_only", version="2024",
                                         app_info=app_info_doc("2025"))),
            # retired: stopped everywhere
            ("rel-1", "retired", snapshot("retired", decision="stopped", qa_status="not_checked")),
            ("rel-2", "retired", snapshot("retired", decision="stopped", qa_status="not_checked")),
        ],
    )
    old.execute(
        "INSERT INTO cicd_task_requests(task_id, app_id, request_type, payload, submitter,"
        " submitted_at, status, origin) VALUES (?, ?, 'modify', ?, 'rm', '2026-02-01 00:00:00',"
        " ?, 'release_decision_sync')",
        (
            "amber",
            "amber",
            json.dumps({
                "status": {"old": "Running", "new": "Stopped"},
                "release_decision": "stopped",
                "_decision_sync_release_id": "rel-2",
                "_cicd_first_action": "created",
            }),
            "pending" if open_request else "approved",
        ),
    )
    old.commit()
    old.close()


@pytest.fixture()
def migrated(tmp_path):
    db_path = tmp_path / "legacy.db"
    build_old_db(db_path)
    reset_init_state()
    conn = connect(db_path)
    try:
        yield conn
    finally:
        conn.close()
        reset_init_state()


def test_migration_refuses_to_run_while_cicd_requests_are_open(tmp_path):
    db_path = tmp_path / "open.db"
    build_old_db(db_path, open_request=True)
    reset_init_state()
    try:
        with pytest.raises(RuntimeError, match="未完成的 CICD 申请"):
            connect(db_path)
    finally:
        reset_init_state()
    # Nothing was converted: the old tables are untouched.
    old = sqlite3.connect(db_path)
    try:
        assert old.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0] == 6
        columns = {row[1] for row in old.execute("PRAGMA table_info(releases)")}
        assert "app_freeze_deadline" in columns
    finally:
        old.close()


def test_releases_become_cycles_with_the_old_deadlines_and_maca_environment(migrated):
    rows = {
        row["id"]: dict(row)
        for row in migrated.execute("SELECT * FROM releases ORDER BY created_at")
    }
    assert rows["rel-1"]["signup_deadline"] == "2026-03-10 23:59"
    assert rows["rel-1"]["qa_deadline"] == "2026-03-20 23:59"
    assert rows["rel-1"]["release_date"] == "2026-03-25"
    assert rows["rel-1"]["released_locked"] == 1

    env = [
        (row["release_id"], row["component"], row["version"])
        for row in migrated.execute("SELECT * FROM cycle_environment ORDER BY release_id")
    ]
    assert env == [("rel-1", "MACA", "20260301"), ("rel-2", "MACA", "20260401")]
    assert not migrated.execute(
        "SELECT 1 FROM sqlite_master WHERE name IN ('snapshots', 'monthly_migration_maca')"
    ).fetchone()


def test_app_profile_and_cicd_status_come_from_the_newest_snapshot(migrated):
    apps = {row["id"]: dict(row) for row in migrated.execute("SELECT * FROM apps")}

    assert apps["amber"]["cicd_status"] == "running"
    assert apps["retired"]["cicd_status"] == "stopped"
    assert apps["amber"]["official_name"] == "Amber"
    assert apps["amber"]["app_type"] == "分子动力学"
    assert apps["amber"]["doc_target"] == "manual"
    assert json.loads(apps["amber"]["owners_json"]) == ["owner_test"]
    assert json.loads(apps["amber"]["doc_json"])["intro"] == "x"
    assert json.loads(apps["amber"]["test_docs_json"])[0]["path"] == "sanity"
    assert apps["amber"]["sync_head_commit"] == "c" * 12


def test_every_distinct_app_info_becomes_one_version_with_dependencies(migrated):
    versions = [
        (row["app_id"], json.loads(row["raw_json"])["app_version"])
        for row in migrated.execute("SELECT * FROM app_info_versions ORDER BY id")
    ]
    # amber uploaded the same content twice -> one version; lammps changed -> two.
    assert versions == [("amber", "22"), ("lammps", "2024"), ("lammps", "2025")]
    assert sorted(
        tuple(row) for row in migrated.execute("SELECT app_id, component FROM app_dependencies")
    ) == [("amber", "mcpy"), ("lammps", "mcpy")]
    stored = migrated.execute(
        "SELECT raw_json FROM app_info_versions WHERE app_id = 'amber'"
    ).fetchone()[0]
    assert stored == app_info_domain.canonical_json(app_info_doc("22"))


def test_only_release_snapshots_become_entries_and_carry_their_qa_verdict(migrated):
    entries = {
        (row["release_id"], row["app_id"]): dict(row)
        for row in migrated.execute("SELECT * FROM entries")
    }
    assert set(entries) == {("rel-1", "amber"), ("rel-2", "amber"), ("rel-1", "lammps")}
    assert entries[("rel-2", "amber")]["target_version"] == "23"
    assert entries[("rel-2", "amber")]["qa_status"] == "has_issues"
    assert entries[("rel-2", "amber")]["qa_note"] == "偶发超时"
    assert entries[("rel-2", "amber")]["known_issues"] == "已知限制"
    # Locked releases get a result; releasable QA verdicts count as released.
    assert entries[("rel-2", "amber")]["result"] == "released"
    assert entries[("rel-1", "lammps")]["result"] == "released"
    assert entries[("rel-1", "amber")]["locked_version_id"] is not None


def test_only_the_newest_locked_release_writes_official_versions(migrated):
    officials = [
        (row["app_id"], row["release_id"], row["version"])
        for row in migrated.execute("SELECT * FROM official_versions")
    ]
    assert officials == [("amber", "rel-2", "23")]


def test_app_detail_recovers_older_publications_without_changing_current_officials(migrated):
    from app.repositories import official_repo
    from app.services import catalog_service

    before = migrated.total_changes
    current = official_repo.current_by_app(migrated)
    history = catalog_service.app_detail(migrated, "amber")["official_versions"]
    assert [(row["release_id"], row["version"]) for row in history] == [("rel-2", "23"), ("rel-1", "22")]
    assert history[0]["id"] is not None
    assert history[1]["id"] is None
    assert history[1]["entry_id"] is not None
    assert history[1]["released_at"] == "2026-03-25 10:00:00"
    assert "withdrawn" not in history[1]
    # This App has an older release but no current official row after migration.
    older_only = catalog_service.app_detail(migrated, "lammps")["official_versions"]
    assert [(row["release_id"], row["version"]) for row in older_only] == [("rel-1", "2024")]
    assert official_repo.current_by_app(migrated) == current
    assert migrated.total_changes == before


def test_cicd_payloads_lose_the_removed_keys_and_use_lowercase_statuses(migrated):
    payload = json.loads(
        migrated.execute("SELECT payload FROM cicd_task_requests").fetchone()[0]
    )
    assert payload == {"status": {"old": "running", "new": "stopped"}}


def test_migration_runs_once_and_the_database_opens_normally_afterwards(tmp_path):
    db_path = tmp_path / "once.db"
    build_old_db(db_path)
    reset_init_state()
    first = connect(db_path)
    counts = (
        first.execute("SELECT COUNT(*) FROM entries").fetchone()[0],
        first.execute("SELECT COUNT(*) FROM app_info_versions").fetchone()[0],
    )
    first.close()

    reset_init_state()
    second = connect(db_path)
    try:
        assert (
            second.execute("SELECT COUNT(*) FROM entries").fetchone()[0],
            second.execute("SELECT COUNT(*) FROM app_info_versions").fetchone()[0],
        ) == counts
    finally:
        second.close()
        reset_init_state()
