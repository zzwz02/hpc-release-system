from __future__ import annotations

import json

from app.db.connection import connect, reset_init_state
from app.repositories import cicd_repo
from app.services import app_service, cicd_service
from app.timeutil import beijing_timestamp


def fresh_conn():
    reset_init_state()
    return connect(":memory:")


def seed_app_release_and_task(conn) -> None:
    conn.execute(
        """
        INSERT INTO releases(
            id, name, maca_version, app_freeze_deadline, doc_deadline,
            released_locked, created_at, source
        )
        VALUES ('rel-1', '3.0', '3.0', '2099-01-01 00:00:00',
                '2099-02-01 00:00:00', 0, '2026-01-01 00:00:00', 'manual')
        """
    )
    conn.execute(
        """
        INSERT INTO apps(id, git_url, git_branch, aliases_json, created_by, created_at)
        VALUES ('app1', 'hpc_app_new', 'maca_new', '["App One"]', 'test', '2026-01-01 00:00:00')
        """
    )
    snapshot = {
        "app_id": "app1",
        "official_name": "App One",
        "version": "1.0",
        "release_decision": "release",
        "owners": ["owner"],
        "owner_confirmed": False,
        "doc_target": "manual",
        "type": "tool",
        "doc": {},
        "community": {},
        "test_docs": [],
        "app_info": None,
        "app_info_diffs": [],
    }
    conn.execute(
        "INSERT INTO snapshots(release_id, app_id, data_json) VALUES (?, ?, ?)",
        ("rel-1", "app1", json.dumps(snapshot)),
    )
    ts = beijing_timestamp()
    cicd_repo.create_task(
        conn,
        task_id="CICD-0001",
        app_name="App One",
        app_id="app1",
        app_version="1.0",
        repo_type="git",
        repo_name="stale_repo",
        branch="stale_branch",
        owner_username="owner",
        created_at=ts,
        updated_at=ts,
    )
    conn.commit()


def test_linked_cicd_task_uses_app_git_identity_for_display():
    conn = fresh_conn()
    try:
        seed_app_release_and_task(conn)
        tasks = cicd_service.list_tasks(conn)
        task = next(t for t in tasks if t["id"] == "CICD-0001")
        assert task["app_id"] == "app1"
        assert task["repo_name"] == "hpc_app_new"
        assert task["branch"] == "maca_new"
    finally:
        conn.close()


def test_cicd_task_display_uses_app_cicd_config_columns():
    conn = fresh_conn()
    try:
        seed_app_release_and_task(conn)
        conn.execute(
            """
            UPDATE apps
            SET cicd_repo_type='repo',
                cicd_community_artifact='image',
                cicd_build_image='app/build:latest',
                cicd_test_timeout='75',
                cicd_notes='from app table'
            WHERE id='app1'
            """
        )
        conn.commit()
        task = next(t for t in cicd_service.list_tasks(conn) if t["app_id"] == "app1")
        assert task["repo_type"] == "repo"
        assert task["community_artifact"] == ["image"]
        assert task["build_image"] == "app/build:latest"
        assert task["test_timeout"] == 75
        assert task["notes"] == "from app table"
    finally:
        conn.close()


def test_cicd_modify_approval_updates_app_cicd_config_columns():
    conn = fresh_conn()
    try:
        seed_app_release_and_task(conn)
        req = cicd_service.submit_request(
            conn,
            task_id="CICD-0001",
            request_type="modify",
            payload={
                "build_image": {"old": "", "new": "app/build:v2"},
                "test_timeout": {"old": 0, "new": 90},
                "community_artifact": {"old": [], "new": ["image", "pkg"]},
                "notes": {"old": "", "new": "approved config"},
            },
            submitter="owner",
            submitter_role="Owner",
        )
        cicd_service.approve_request(
            conn,
            req["id"],
            reviewer="rm",
            reviewer_role="RM",
        )
        app = conn.execute(
            """
            SELECT cicd_community_artifact, cicd_build_image, cicd_test_timeout, cicd_notes
            FROM apps WHERE id='app1'
            """
        ).fetchone()
        task = conn.execute(
            "SELECT build_image, test_timeout, notes FROM cicd_tasks WHERE id='CICD-0001'"
        ).fetchone()
        assert app["cicd_build_image"] == "app/build:v2"
        assert app["cicd_test_timeout"] == "90"
        assert app["cicd_community_artifact"] == "image, pkg"
        assert app["cicd_notes"] == "approved config"
        assert task["build_image"] == ""
        assert task["test_timeout"] == 40
        assert task["notes"] == ""
    finally:
        conn.close()


def test_rm_app_git_identity_update_only_changes_app_table():
    conn = fresh_conn()
    try:
        seed_app_release_and_task(conn)
        app_service.update_snapshot(
            conn,
            "rel-1",
            "app1",
            user="rm",
            role="RM",
            fields={
                "app": {"git_url": "hpc_app_latest", "git_branch": "maca_latest"},
                "snapshot": {},
            },
        )
        app = conn.execute(
            "SELECT git_url, git_branch FROM apps WHERE id='app1'"
        ).fetchone()
        row = conn.execute(
            "SELECT repo_name, branch FROM cicd_tasks WHERE id='CICD-0001'"
        ).fetchone()
        assert app["git_url"] == "hpc_app_latest"
        assert app["git_branch"] == "maca_latest"
        assert row["repo_name"] == "stale_repo"
        assert row["branch"] == "stale_branch"
    finally:
        conn.close()


def test_owner_can_update_app_git_identity_with_confirmation():
    conn = fresh_conn()
    try:
        seed_app_release_and_task(conn)
        app_service.update_snapshot(
            conn,
            "rel-1",
            "app1",
            user="owner",
            role="Owner",
            fields={
                "app": {"git_url": "hpc_owner_latest", "git_branch": "owner_branch"},
                "snapshot": {"owner_confirmed": True},
            },
        )
        app = conn.execute(
            "SELECT git_url, git_branch FROM apps WHERE id='app1'"
        ).fetchone()
        assert app["git_url"] == "hpc_owner_latest"
        assert app["git_branch"] == "owner_branch"
    finally:
        conn.close()


def test_cicd_modify_repo_identity_change_updates_app_after_approval():
    conn = fresh_conn()
    try:
        seed_app_release_and_task(conn)
        req = cicd_service.submit_request(
            conn,
            task_id="app1",
            request_type="modify",
            payload={
                "repo_name": {"old": "hpc_app_new", "new": "hpc_app_latest"},
                "branch": {"old": "maca_new", "new": "maca_latest"},
            },
            submitter="owner",
            submitter_role="Owner",
        )
        app_before = conn.execute(
            "SELECT git_url, git_branch FROM apps WHERE id='app1'"
        ).fetchone()
        assert req["status"] == "pending"
        assert app_before["git_url"] == "hpc_app_new"
        assert app_before["git_branch"] == "maca_new"

        cicd_service.approve_request(conn, req["id"], reviewer="rm", reviewer_role="RM")
        app_after = conn.execute(
            "SELECT git_url, git_branch FROM apps WHERE id='app1'"
        ).fetchone()
        task_after = conn.execute(
            "SELECT repo_name, branch FROM cicd_tasks WHERE id='CICD-0001'"
        ).fetchone()
        assert app_after["git_url"] == "hpc_app_latest"
        assert app_after["git_branch"] == "maca_latest"
        assert task_after["repo_name"] == "stale_repo"
        assert task_after["branch"] == "stale_branch"
    finally:
        conn.close()
