from __future__ import annotations

import json

import pytest

from app.db.connection import connect, reset_init_state
from app.repositories import apps_repo
from app.services import app_service, cicd_service

_GERRIT_BASE = "ssh://sw-gerrit-devops.metax-internal.com:29418/PDE/HPC"


def fresh_conn():
    reset_init_state()
    return connect(":memory:")


def seed_app_release(conn) -> None:
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
    apps_repo.save_app(conn, {
        "id": "app1",
        "git_url": "hpc_app_new",
        "git_branch": "maca_new",
        "aliases": ["App One"],
        "created_by": "test",
        "created_at": "2026-01-01 00:00:00",
    })
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
    conn.commit()


def test_cicd_task_display_is_derived_from_app_identity():
    conn = fresh_conn()
    try:
        seed_app_release(conn)
        tasks = cicd_service.list_tasks(conn)
        task = next(t for t in tasks if t["id"] == "app1")
        assert task["app_id"] == "app1"
        assert task["repo_name"] == "hpc_app_new"
        assert task["branch"] == "maca_new"
        assert task["status"] == "Running"
    finally:
        conn.close()


def test_cicd_task_display_uses_app_cicd_config_columns():
    conn = fresh_conn()
    try:
        seed_app_release(conn)
        apps_repo.update_cicd_config(conn, "app1", {
            "cicd_repo_type": "repo",
            "cicd_community_artifact": "image",
            "cicd_build_image": "app/build:latest",
            "cicd_test_timeout": "75",
            "cicd_notes": "from app table",
        })
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
        seed_app_release(conn)
        req = cicd_service.submit_request(
            conn,
            task_id="app1",
            request_type="modify",
            payload={
                "build_image": {"old": "", "new": "app/build:v2"},
                "test_timeout": {"old": 0, "new": 90},
                "community_artifact": {"old": [], "new": ["image", "pkg"]},
                "notes": {"old": "", "new": "approved config"},
            },
            submitter="owner",
            submitter_role="Owner",
            source="app_workbench",
        )
        cicd_service.approve_request(conn, req["id"], reviewer="rm", reviewer_role="RM")
        app = apps_repo.get_app(conn, "app1")
        assert app["cicd_build_image"] == "app/build:v2"
        assert app["cicd_test_timeout"] == "90"
        assert app["cicd_community_artifact"] == "image, pkg"
        assert app["cicd_notes"] == "approved config"
    finally:
        conn.close()


def test_rm_app_git_identity_update_changes_app_table_only():
    conn = fresh_conn()
    try:
        seed_app_release(conn)
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
        app = apps_repo.get_app(conn, "app1")
        assert app["git_url"] == "hpc_app_latest"
        assert app["git_branch"] == "maca_latest"
        assert not conn.execute("SELECT 1 FROM cicd_tasks").fetchone()
    finally:
        conn.close()


def test_app_workbench_audit_uses_naive_beijing_timestamp(monkeypatch):
    conn = fresh_conn()
    try:
        seed_app_release(conn)
        monkeypatch.setattr(
            "app.timeutil.beijing_timestamp",
            lambda: "2026-06-24 15:30:00",
        )
        monkeypatch.setattr(
            app_service.core,
            "now",
            lambda: "2026-06-24T07:30:00+00:00",
        )

        app_service.update_snapshot(
            conn,
            "rel-1",
            "app1",
            user="rm",
            role="RM",
            fields={"snapshot": {"official_url": "https://example.com/abacus"}},
        )

        row = conn.execute(
            "SELECT ts, event FROM audit WHERE app_id = ? ORDER BY id DESC LIMIT 1",
            ("app1",),
        ).fetchone()
        assert row["event"] == "update_app_meta"
        assert row["ts"] == "2026-06-24 15:30:00"
    finally:
        conn.close()


def test_cicd_modify_repo_identity_change_updates_app_after_approval():
    conn = fresh_conn()
    try:
        seed_app_release(conn)
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
            source="app_workbench",
        )
        cicd_service.approve_request(conn, req["id"], reviewer="rm", reviewer_role="RM")
        app = apps_repo.get_app(conn, "app1")
        assert app["git_url"] == f"{_GERRIT_BASE}/hpc_app_latest"
        assert app["git_branch"] == "maca_latest"
        assert not conn.execute("SELECT 1 FROM cicd_tasks").fetchone()
    finally:
        conn.close()


def test_cicd_modify_rejects_full_git_url_on_submit():
    conn = fresh_conn()
    try:
        seed_app_release(conn)
        with pytest.raises(ValueError, match="git 类型只填写 PDE/HPC 后的短路径"):
            cicd_service.submit_request(
                conn,
                task_id="app1",
                request_type="modify",
                payload={
                    "repo_name": {
                        "old": "hpc_app_new",
                        "new": f"{_GERRIT_BASE}/hpc_app_latest",
                    },
                },
                submitter="owner",
                submitter_role="Owner",
                source="app_workbench",
            )
    finally:
        conn.close()


def test_cicd_modify_rejects_full_manifest_url_on_submit():
    conn = fresh_conn()
    try:
        seed_app_release(conn)
        with pytest.raises(ValueError, match="repo 类型只填写 manifest 内 XML 路径"):
            cicd_service.submit_request(
                conn,
                task_id="app1",
                request_type="modify",
                payload={
                    "repo_type": {"old": "git", "new": "repo"},
                    "repo_name": {
                        "old": "hpc_app_new",
                        "new": f"{_GERRIT_BASE}/manifest/APP/openfoam/hpc_v2206_v0.xml",
                    },
                },
                submitter="owner",
                submitter_role="Owner",
                source="app_workbench",
            )
    finally:
        conn.close()
