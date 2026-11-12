"""Shared pytest fixtures for the HPC release system test suite.

The site runs the monthly signup model: Apps own their CICD status, profile
and app_info history; a release is a monthly cycle an App signs up for.  The
seed helpers below build that data through the app's own services, so tests
always see exactly what the site would produce.

They are plain functions (not fixtures) so tests and scripts can import them:
``from tests.conftest import seed_app, seed_cycle, seed_signup``.
"""

from __future__ import annotations

import tempfile
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

from app.config import settings
from app.db.connection import connect as _app_connect
from app.db.connection import transaction
from app.domain.cycles import CICD_RUNNING
from app.repositories import apps_repo
from app.services import catalog_service, cycle_service
from app.timeutil import beijing_timestamp

# ---------------------------------------------------------------------------
# Core infrastructure fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session", autouse=True)
def _isolated_runtime_config(tmp_path_factory):
    """Keep tests off the real release_system.conf and off the network.

    The conf path points at a missing file, so integrations see an empty
    config.  Tests that need config monkeypatch settings.runtime_conf_path to
    their own file.  ``settings.db_path`` points at a throw-away file so a
    TestClient lifespan never opens (or migrates) the developer's real
    database, and the daily app_info sync is disabled so no test starts a
    background Gerrit thread.
    """
    original = settings.runtime_conf_path
    original_cases = settings.jira_testing_cases_dir
    original_agent_db = settings.jira_agent_database_url
    original_sync = settings.app_info_sync_enabled
    original_db_path = settings.db_path
    settings.runtime_conf_path = tmp_path_factory.mktemp("runtime_config") / "missing.conf"
    settings.jira_testing_cases_dir = tmp_path_factory.mktemp("builtin_cases")
    settings.jira_agent_database_url = f"sqlite:///{tmp_path_factory.mktemp('agent_database') / 'agent.db'}"
    settings.app_info_sync_enabled = False
    settings.db_path = tmp_path_factory.mktemp("session_db") / "release_system.db"
    yield
    settings.runtime_conf_path = original
    settings.jira_testing_cases_dir = original_cases
    settings.jira_agent_database_url = original_agent_db
    settings.app_info_sync_enabled = original_sync
    settings.db_path = original_db_path


@pytest.fixture()
def tmp_dir():
    """A temporary directory cleaned up after the test."""
    with tempfile.TemporaryDirectory() as d:
        yield Path(d)


@pytest.fixture()
def temp_db(tmp_dir):
    """A fresh, schema-initialised SQLite connection backed by a temp file."""
    db_path = tmp_dir / "test.db"
    conn = _app_connect(db_path)
    yield conn
    conn.close()


@pytest.fixture()
def db_path(tmp_dir):
    """Absolute path to a temp DB file (for helpers that need the path)."""
    return tmp_dir / "test.db"


@pytest.fixture()
def temp_db_with_path(db_path):
    """Yields (conn, db_path) for tests that need both."""
    conn = _app_connect(db_path)
    yield conn, db_path
    conn.close()


# ---------------------------------------------------------------------------
# app_info documents
# ---------------------------------------------------------------------------

#: A minimal but complete app_info.json: one enabled build and one test.
APP_INFO: dict[str, Any] = {
    "app_name": "testapp",
    "app_version": "1.0",
    "app_build": {
        "ubuntu20.04_amd64": {
            "build_target": "release",
            "arch": "amd64",
            "os": "ubuntu20.04",
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


def make_app_info(
    *,
    app_name: str = "testapp",
    app_version: str = "1.0",
    chips: list[str] | tuple[str, ...] = ("c500",),
    tests: dict[str, Any] | None = None,
    dependencies: list[str] | None = None,
) -> dict[str, Any]:
    """An app_info.json document for *app_name* with the given chips and tests.

    *tests* maps a test name to its supported chips, e.g. ``{"sanity": ["c500"]}``.
    """
    data = deepcopy(APP_INFO)
    data["app_name"] = app_name
    data["app_version"] = app_version
    data["app_build"]["ubuntu20.04_amd64"]["supported_chip"] = list(chips)
    if tests is not None:
        data["app_test"] = {
            name: {
                "test_cmd": f"{app_name} {name}",
                "supported_chip": {chip: ["ubuntu20.04_amd64"] for chip in test_chips},
                "enabled": True,
            }
            for name, test_chips in tests.items()
        }
    if dependencies is not None:
        data["app_dependency"] = list(dependencies)
    return data


def make_platform_app_info():
    x86 = ["C500", "C588", "C600", "C600U", "X201-A", "X206", "X301", "X302", "X201"]
    return {
        "app_name": "hpcg", "app_version": "0.9.0",
        "app_build": {
            "ubuntu20.04_amd64": {"arch": "amd64", "os": "ubuntu20.04", "supported_chip": x86},
            "kylinv11_amd64": {"arch": "amd64", "os": "kylinv11", "supported_chip": ["X201", "C600"]},
            "ubuntu20.04_arm64": {"arch": "arm64", "os": "ubuntu20.04", "supported_chip": ["C500", "C600", "X302", "X201"]},
        },
        "app_test": {
            "benchmark": {"test_cmd": "/opt/HPCG/autotest.sh", "img_target": "release", "supported_chip": {
                chip: ["ubuntu20.04_amd64", "kylinv11_amd64"] if chip in ["X201", "C600"] else ["ubuntu20.04_amd64"]
                for chip in x86
            }},
            "benchmark_arm": {"test_cmd": "/opt/HPCG/arm.sh", "img_target": "release",
                              "supported_chip": {"C500": ["ubuntu20.04_arm64"]}},
        },
    }


# ---------------------------------------------------------------------------
# Seed / factory helpers
# ---------------------------------------------------------------------------


def seed_app(
    conn,
    app_id: str = "testapp",
    *,
    official_name: str = "TestApp",
    git_url: str = "",
    git_branch: str = "maca",
    owners: list[str] | tuple[str, ...] = ("owner_test",),
    app_type: str = "分子动力学",
    doc_target: str = "manual",
    cicd_status: str = CICD_RUNNING,
    app_info: dict[str, Any] | None = APP_INFO,
    commit_id: str = "commit0",
) -> str:
    """Create an App (optionally with a first app_info version) and return its id."""
    git_url = git_url or f"{settings.gerrit_hpc_base_url}/hpc_{app_id}"
    with transaction(conn):
        catalog_service.create_app(
            conn,
            app_id=app_id,
            official_name=official_name,
            git_url=git_url,
            git_branch=git_branch,
            owners=list(owners),
            doc_target=doc_target,
            app_type=app_type,
            created_by="conftest",
            ts=beijing_timestamp(),
        )
        if app_info is not None:
            catalog_service.store_app_info(
                conn,
                app_id,
                deepcopy(app_info),
                commit_id=commit_id,
                source="conftest",
                user="conftest",
            )
        if cicd_status != CICD_RUNNING:
            apps_repo.set_cicd_status(conn, app_id, cicd_status)
    return app_id


def store_app_info(
    conn,
    app_id: str,
    app_info: dict[str, Any],
    *,
    commit_id: str = "",
    user: str = "conftest",
) -> int | None:
    """Store a new app_info version (entries react exactly as in production)."""
    with transaction(conn):
        return catalog_service.store_app_info(
            conn,
            app_id,
            deepcopy(app_info),
            commit_id=commit_id or f"commit{beijing_timestamp()}",
            source="conftest",
            user=user,
        )


def seed_cycle(
    conn,
    *,
    name: str = "2099-01",
    signup_deadline: str = "2099-01-01 23:59",
    qa_deadline: str = "2099-01-26 23:59",
    release_date: str = "2099-01-31",
    user: str = "rm",
    role: str = "RM",
) -> str:
    """Create a monthly cycle and return its release_id (signup phase by default)."""
    return cycle_service.create_cycle(
        conn,
        name=name,
        signup_deadline=signup_deadline,
        qa_deadline=qa_deadline,
        release_date=release_date,
        user=user,
        role=role,
    )["release_id"]


def set_cycle_dates(
    conn,
    release_id: str,
    *,
    signup_deadline: str,
    qa_deadline: str,
    release_date: str,
) -> None:
    """Move a cycle's deadlines directly (used to reach a later phase)."""
    conn.execute(
        "UPDATE releases SET signup_deadline = ?, qa_deadline = ?, release_date = ? WHERE id = ?",
        (signup_deadline, qa_deadline, release_date, release_id),
    )
    conn.commit()


def close_signup(conn, release_id: str) -> None:
    """Push the signup deadline into the past (cycle phase -> in_progress)."""
    set_cycle_dates(
        conn,
        release_id,
        signup_deadline="2000-01-01 23:59",
        qa_deadline="2099-01-26 23:59",
        release_date="2099-01-31",
    )


def close_qa(conn, release_id: str) -> None:
    """Push both deadlines into the past (cycle phase -> qa_closed)."""
    set_cycle_dates(
        conn,
        release_id,
        signup_deadline="2000-01-01 23:59",
        qa_deadline="2000-01-26 23:59",
        release_date="2000-01-31",
    )


def seed_signup(
    conn,
    release_id: str,
    app_id: str,
    *,
    base_image_id: Any = None,
    target_version: str = "1.0",
    planned_features: str = "conftest feature",
    excluded_tests: list[str] | None = None,
    user: str = "rm",
    role: str = "RM",
) -> dict[str, Any]:
    """Sign *app_id* up for *release_id* and return the entry view."""
    return cycle_service.signup(
        conn,
        release_id,
        app_id,
        base_image_id=base_image_id,
        target_version=target_version,
        planned_features=planned_features,
        excluded_tests=excluded_tests or [],
        user=user,
        role=role,
    )


def seed_cicd_request(
    conn,
    app_id: str,
    *,
    payload: dict | None = None,
    submitter: str = "owner_test",
    submitter_role: str = "Owner",
    submitter_display: str = "Owner",
) -> dict:
    """Submit a CICD config-modify request for *app_id*."""
    from app.services import cicd_service

    if payload is None:
        payload = {"notes": {"old": "", "new": "conftest note"}}
    return cicd_service.submit_request(
        conn,
        task_id=app_id,
        request_type="modify",
        payload=payload,
        submitter=submitter,
        submitter_role=submitter_role,
        submitter_display=submitter_display,
        source="app_workbench",
    )


# ---------------------------------------------------------------------------
# Convenience composite fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def cycle_with_app(temp_db):
    """Yields (conn, release_id, app_id): one open cycle and one running App."""
    app_id = seed_app(temp_db)
    release_id = seed_cycle(temp_db)
    yield temp_db, release_id, app_id


@pytest.fixture()
def cycle_with_entry(cycle_with_app):
    """Like cycle_with_app but the App is already signed up."""
    conn, release_id, app_id = cycle_with_app
    seed_signup(conn, release_id, app_id)
    yield conn, release_id, app_id
