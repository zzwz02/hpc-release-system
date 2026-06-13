"""Shared pytest fixtures for the HPC release system test suite.

Phase 1: temp_db and related fixtures now use app.db.connection.connect (the
canonical new connection module) instead of release_system.core.connect.

The seed helper functions (seed_release, seed_app, etc.) still call
release_system.core for data operations — those functions accept any
sqlite3.Connection, so they remain compatible with both old and new connections.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from app.db.connection import connect as _app_connect
from release_system import core


# ---------------------------------------------------------------------------
# Core infrastructure fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def tmp_dir():
    """A temporary directory cleaned up after the test."""
    with tempfile.TemporaryDirectory() as d:
        yield Path(d)


@pytest.fixture()
def temp_db(tmp_dir):
    """A fresh, schema-initialised SQLite connection backed by a temp file.

    Uses app.db.connection.connect so the new schema (including app_id on
    cicd_tasks, origin on cicd_task_requests, and the new indexes) is
    applied.  Compatible with all release_system.core helper functions.
    """
    db_path = tmp_dir / "test.db"
    conn = _app_connect(db_path)
    yield conn
    conn.close()


@pytest.fixture()
def db_path(tmp_dir):
    """Absolute path to a temp DB file.

    Use this when a function needs the path alongside the connection
    (e.g. core.backup_sqlite, core.qa_upload_log).
    """
    return tmp_dir / "test.db"


@pytest.fixture()
def temp_db_with_path(db_path):
    """Yields (conn, db_path) for tests that need both."""
    conn = _app_connect(db_path)
    yield conn, db_path
    conn.close()


# ---------------------------------------------------------------------------
# Minimal CSV for a single app import
# ---------------------------------------------------------------------------

_INIT_CSV = (
    "官方名称,类型,APP类型,Owner,app_version,maca_chip,hpcc_chip,arch,"
    "maca_version,git_url,git_branch\n"
    "TestApp,HPC,分子动力学,test_owner,1.0,c500,,x86,20260601-001,"
    "ssh://sw-gerrit-devops.metax-internal.com:29418/PDE/HPC/hpc_testapp,maca\n"
)


# ---------------------------------------------------------------------------
# Seed / factory helpers
#
# These are plain functions (not fixtures) so capture.py and other scripts
# can import and call them directly: from tests.conftest import seed_release
# ---------------------------------------------------------------------------

def seed_release(conn, *, csv_text: str = _INIT_CSV, tmp_path: Path | None = None) -> str:
    """Import an initial release from *csv_text* and return the release_id.

    *tmp_path*: an existing directory to write the temporary CSV into.
    If omitted a TemporaryDirectory is created and cleaned up internally.
    """
    if tmp_path is None:
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "init.csv"
            p.write_text(csv_text, encoding="utf-8")
            return core.import_initial(conn, p)
    p = tmp_path / "init.csv"
    p.write_text(csv_text, encoding="utf-8")
    return core.import_initial(conn, p)


def seed_app(
    conn,
    release_id: str,
    *,
    official_name: str = "ExtraApp",
    git_url: str = "ssh://sw-gerrit-devops.metax-internal.com:29418/PDE/HPC/hpc_extra",
    git_branch: str = "maca",
    release_decision: str = "release",
    owner: str = "extra_owner",
) -> str:
    """Add a new app to *release_id* and return its app_id."""
    return core.add_new_app_request(
        conn,
        release_id,
        official_name=official_name,
        git_url=git_url,
        git_branch=git_branch,
        release_decision=release_decision,
        owner=owner,
    )


def seed_snapshot(
    conn,
    release_id: str,
    app_id: str,
    *,
    app_info: dict | None = None,
    owner_confirmed: bool = False,
    qa_status: str | None = None,
) -> dict:
    """Apply *app_info* to a snapshot; optionally confirm owner and set QA status.

    Returns the final snapshot dict.
    """
    if app_info is None:
        app_info = {
            "app_version": "1.0",
            "app_name": "testapp",
            "app_build": {
                "ubuntu20.04_amd64": {
                    "build_target": "release",
                    "arch": "amd64",
                    "supported_chip": ["c500"],
                    "enabled": True,
                }
            },
            "app_test": {
                "sanity": {
                    "test_cmd": "testapp --version",
                    "supported_chip": {"c500": ["ubuntu20.04_amd64"]},
                    "enabled": True,
                }
            },
        }
    core.apply_app_info(conn, release_id, app_id, app_info, source="conftest")

    if owner_confirmed:
        def _confirm(snap: dict) -> None:
            snap["owner_confirmed"] = True
            snap["description"] = "conftest 测试描述"
            snap["doc"].update({
                "intro": "conftest intro",
                "image_usage": "conftest image_usage",
                "binary_usage": "conftest binary_usage",
                "env_setup": "conftest env_setup",
            })
            for doc in snap["test_docs"]:
                doc.update({
                    "dataset": "conftest dataset",
                    "content": "conftest content",
                    "result_view": "conftest result_view",
                    "pass_criteria": "conftest pass_criteria",
                })
        core.update_snapshot(conn, release_id, app_id, _confirm)

    if qa_status is not None:
        core.qa_set_status(conn, release_id, app_id, qa_status)

    return core.get_release(conn, release_id)["snapshots"][app_id]


def seed_cicd_task(
    conn,
    *,
    app_name: str = "hpc-cicd-test",
    app_version: str = "1.0",
    repo_type: str = "git",
    repo_name: str = "ssh://sw-gerrit-devops.metax-internal.com:29418/PDE/HPC/hpc_cicd_test",
    branch: str = "maca",
    status: str = "Running",
    submitter: str = "rm",
    submitter_role: str = "RM",
    submitter_display: str = "RM",
) -> dict:
    """Submit a CICD create request and return the pending request dict."""
    return core.submit_cicd_request(
        conn,
        task_id=None,
        request_type="create",
        payload={
            "app_name": app_name,
            "app_version": app_version,
            "repo_type": repo_type,
            "repo_name": repo_name,
            "branch": branch,
            "build_product": ["maca"],
            "community_artifact": ["image"],
            "build_image": "hpc/base:latest",
            "test_timeout": 40,
            "owner_username": submitter,
            "status": status,
            "notes": "",
        },
        submitter=submitter,
        submitter_role=submitter_role,
        submitter_display=submitter_display,
    )


def seed_cicd_request(
    conn,
    task_id: str,
    *,
    payload: dict | None = None,
    submitter: str = "owner_test",
    submitter_role: str = "Owner",
    submitter_display: str = "Owner",
) -> dict:
    """Submit a CICD modify request against *task_id* and return the request dict."""
    if payload is None:
        payload = {"notes": {"old": "", "new": "conftest note"}}
    return core.submit_cicd_request(
        conn,
        task_id=task_id,
        request_type="modify",
        payload=payload,
        submitter=submitter,
        submitter_role=submitter_role,
        submitter_display=submitter_display,
    )


def seed_wiki_article(
    conn,
    *,
    title: str = "Test Article",
    body_md: str = "# conftest article",
    pinned: bool = False,
    user: str = "rm",
    role: str = "RM",
) -> dict:
    """Create a wiki article and return its dict."""
    from release_system.wiki import core as wiki_core

    return wiki_core.save_article(conn, title=title, body_md=body_md, pinned=pinned, user=user, role=role)


# ---------------------------------------------------------------------------
# Convenience composite fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def release_with_app(temp_db, tmp_dir):
    """Yields (conn, release_id, app_id) for the simplest one-app scenario."""
    conn = temp_db
    release_id = seed_release(conn, tmp_path=tmp_dir)
    app_id = core.normalize_name("TestApp")
    yield conn, release_id, app_id


@pytest.fixture()
def release_with_snapshot(release_with_app):
    """Like release_with_app but with app_info applied and owner confirmed."""
    conn, release_id, app_id = release_with_app
    seed_snapshot(conn, release_id, app_id, owner_confirmed=True)
    yield conn, release_id, app_id
