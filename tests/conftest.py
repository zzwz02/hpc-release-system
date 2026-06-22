"""Shared pytest fixtures for the HPC release system test suite.

Phase 1: temp_db and related fixtures now use app.db.connection.connect (the
canonical new connection module) instead of release_system.core.connect.

The seed helper functions (seed_release, seed_app, etc.) still call
release_system.core for data operations — those functions accept any
sqlite3.Connection, so they remain compatible with both old and new connections.

Phase 2: fastapi_base_url and fastapi_session_cookies fixtures boot the new
FastAPI app via httpx ASGITransport against a DB seeded identically to
tests/golden/capture.py seed_db().  Do NOT un-skip test_fastapi_parity yet
(that is Wave 3).
"""

from __future__ import annotations

import json
import socket
import tempfile
import threading
from pathlib import Path

import pytest

from app.db.connection import connect as _app_connect
from app.db.connection import reset_init_state
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

    return wiki_core.save_article(
        conn, title=title, body_md=body_md, pinned=pinned, user=user, role=role
    )


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


# ---------------------------------------------------------------------------
# Phase 2 FastAPI parity fixtures
#
# These fixtures boot the new FastAPI app via uvicorn in a background thread
# against a DB seeded identically to tests/golden/capture.py seed_db().
# The session scope ensures the server starts once per test session.
#
# Do NOT remove the @pytest.mark.skip on test_fastapi_parity — that is Wave 3.
# ---------------------------------------------------------------------------

# CSV matching capture.py SEED_CSV_TEXT exactly
_PARITY_SEED_CSV = (
    "官方名称,类型,APP类型,Owner,app_version,maca_chip,hpcc_chip,arch,maca_version,git_url,git_branch\n"
    "GoldenAmber,HPC,分子动力学,owner_test,22,\"c500,x301\",x201,x86,20260511,"
    "ssh://gerrit/PDE/HPC/hpc_amber,maca\n"
    "GoldenLammps,AI4Sci,分子动力学,owner_test,stable,,,,20260511,"
    "ssh://gerrit/PDE/HPC/hpc_lammps,main\n"
    "GoldenHpl,HPC,高性能计算,owner_test,2.3,c500,,x86,20260511,"
    "ssh://gerrit/PDE/HPC/hpc_hpl,maca\n"
)

# Admin password used by capture.py
_PARITY_ADMIN_PW = "golden_admin_pw"


def _seed_parity_db(db_path: Path) -> dict:
    """Seed db_path identically to capture.py seed_db().

    Returns {"release_id", "app_ids", "first_app_id", "cicd_task_id",
             "wiki_article_id"}.
    """
    from release_system.wiki import core as wiki_core

    conn = _app_connect(db_path)

    # 1. Create admin user
    core.create_user(conn, "admin", _PARITY_ADMIN_PW, "Admin")

    # 2. Seed default users (idempotent — connect already calls ensure_default_user)
    core.ensure_default_user(conn)

    # 3. Import initial CSV → creates first release and three apps
    rows = list(core.parse_csv_text(_PARITY_SEED_CSV))
    release_id = core.import_initial_rows(
        conn,
        rows,
        release_name="Golden-Release-1",
        maca_version="20260511",
        app_freeze_deadline="2026-12-31 23:59",
        doc_deadline="2026-12-31 23:59",
    )

    # 4. Collect app_ids
    release = core.get_release(conn, release_id)
    app_ids = list(release["snapshots"].keys())

    # 5. Seed a CICD task via RM auto-approve path
    cicd_req = core.submit_cicd_request(
        conn,
        task_id=None,
        request_type="create",
        payload={
            "app_name": "GoldenAmber",
            "app_version": "22",
            "repo_type": "git",
            "repo_name": "ssh://gerrit/PDE/HPC/hpc_amber",
            "branch": "maca",
            "build_product": ["maca"],
            "community_artifact": [],
            "build_image": "hpc/amber-builder:latest",
            "test_timeout": 40,
            "owner_username": "owner_test",
            "status": "Running",
            "notes": "golden capture seed task",
        },
        submitter="rm",
        submitter_role="RM",
        submitter_display="RM User",
    )
    cicd_task_id = cicd_req["task_id"]

    # 6. Leave a pending Owner modify request
    core.submit_cicd_request(
        conn,
        task_id=cicd_task_id,
        request_type="modify",
        payload={"notes": {"old": "golden capture seed task", "new": "owner wants change"}},
        submitter="owner_test",
        submitter_role="Owner",
        submitter_display="Owner Test",
    )

    # 7. Seed a wiki article
    wiki_article = wiki_core.save_article(
        conn,
        article_id=None,
        title="Golden Test Article",
        body_md="# Golden\nThis is a golden capture test article.",
        pinned=False,
        user="rm",
        role="RM",
    )
    wiki_article_id = wiki_article["id"]

    # 8. Set QA status on one app
    core.qa_set_status_batch(
        conn,
        release_id,
        [{"app_id": app_ids[0], "status": "qa_passed", "note": "golden qa note"}],
        user="qa",
        role="QA",
    )

    # 9. Generate artifacts (best-effort)
    try:
        core.generate_artifacts(conn, release_id, final=False)
    except Exception as exc:
        print(f"  [warn] generate_artifacts: {exc}")

    # 10. Seed a release schedule entry
    core.upsert_release_schedule(
        conn,
        entry_id=None,
        version="Golden-2.0",
        branch_cut_at="2026-07-01",
        release_at="2026-08-01",
        note="golden schedule entry",
        user="rm",
        role="RM",
    )

    conn.commit()
    conn.close()

    return {
        "release_id": release_id,
        "app_ids": app_ids,
        "first_app_id": app_ids[0] if app_ids else "",
        "cicd_task_id": cicd_task_id,
        "wiki_article_id": wiki_article_id,
    }


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="session")
def fastapi_base_url():
    """Boot the new FastAPI app via uvicorn in a background thread.

    Yields the base URL (http://127.0.0.1:<port>) pointing at the live app
    seeded identically to capture.py seed_db().

    The server runs for the entire test session and is stopped on teardown.
    The DB is a temp file that is deleted after the session.
    """
    import uvicorn

    tmp = tempfile.mkdtemp(prefix="hpc_parity_")
    db_path = Path(tmp) / "parity.db"

    # Reset the once-guard so connect() runs init_db on our fresh file
    reset_init_state()

    # Seed the DB identically to capture.py
    _seed_parity_db(db_path)

    port = _find_free_port()

    # Override settings.db_path for this process
    from app import config as _cfg
    original_db_path = _cfg.settings.db_path
    _cfg.settings.db_path = db_path

    # Reset once-guard again so the FastAPI lifespan connect() picks up the new path
    reset_init_state()

    from app.main import create_app
    fastapi_app = create_app()

    server_config = uvicorn.Config(
        fastapi_app,
        host="127.0.0.1",
        port=port,
        log_level="warning",
    )
    server = uvicorn.Server(server_config)

    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    # Wait until server is ready
    import time
    deadline = time.monotonic() + 15.0
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.3):
                break
        except OSError:
            time.sleep(0.1)
    else:
        raise RuntimeError(f"FastAPI test server did not start on port {port}")

    yield f"http://127.0.0.1:{port}"

    # Teardown
    server.should_exit = True
    thread.join(timeout=5)
    _cfg.settings.db_path = original_db_path
    reset_init_state()

    import shutil
    shutil.rmtree(tmp, ignore_errors=True)


@pytest.fixture(scope="session")
def fastapi_parity_ids(
    fastapi_base_url: str,
    fastapi_session_cookies: dict,
) -> dict:
    """Discover non-deterministic IDs from the live parity server.

    Returns a dict with mapping ``live_id → golden_id`` for each ID type that
    is random at creation time (release_id, wiki_article_id, schedule_id).
    App IDs and CICD task IDs are deterministic and need no mapping.

    The dict is used by test_fastapi_parity to:
      1. Substitute golden IDs → live IDs in request _params/_path/_body.
      2. Substitute live IDs → golden IDs in the live response before scrub().
    """
    import httpx

    GOLDEN_RELEASE_ID = "rel_d13b4cc2e9b4"
    GOLDEN_WIKI_ID = "wiki_cbd5c625e992"
    GOLDEN_SCHED_ID = "sched_1265776a4aba"

    rm_cookie = fastapi_session_cookies.get("rm", "")
    headers = {"Content-Type": "application/json", "Cookie": rm_cookie}

    with httpx.Client(trust_env=False, timeout=15) as client:
        # Discover live release_id and sched_id from /api/state.
        # No release_id param → returns the most recent release (the only seeded one).
        resp = client.get(f"{fastapi_base_url}/api/state", headers=headers)
        live_release_id = GOLDEN_RELEASE_ID  # fallback
        live_sched_id = GOLDEN_SCHED_ID  # fallback
        if resp.status_code == 200:
            state = resp.json()
            releases = state.get("releases") or []
            if releases:
                live_release_id = releases[0]["id"]
            schedule = state.get("release_schedule") or []
            if schedule:
                live_sched_id = schedule[0]["id"]

        # Discover live wiki article id from /api/wiki/articles
        resp = client.get(f"{fastapi_base_url}/api/wiki/articles", headers=headers)
        live_wiki_id = GOLDEN_WIKI_ID  # fallback
        if resp.status_code == 200:
            articles = resp.json().get("articles") or []
            if articles:
                live_wiki_id = articles[0]["id"]

    # Map: live → golden  (used in normalize_ids before comparison)
    id_map: dict[str, str] = {}
    if live_release_id != GOLDEN_RELEASE_ID:
        id_map[live_release_id] = GOLDEN_RELEASE_ID
    if live_wiki_id != GOLDEN_WIKI_ID:
        id_map[live_wiki_id] = GOLDEN_WIKI_ID
    if live_sched_id != GOLDEN_SCHED_ID:
        id_map[live_sched_id] = GOLDEN_SCHED_ID

    return {
        "id_map": id_map,
        "live_release_id": live_release_id,
        "live_wiki_id": live_wiki_id,
        "live_sched_id": live_sched_id,
        "golden_release_id": GOLDEN_RELEASE_ID,
        "golden_wiki_id": GOLDEN_WIKI_ID,
        "golden_sched_id": GOLDEN_SCHED_ID,
    }


@pytest.fixture(scope="session")
def fastapi_session_cookies(fastapi_base_url: str) -> dict:
    """Return {role: cookie_str} for rm/owner/qa/admin via /api/login.

    Roles match capture.py's login sequence exactly.  The cookie string is in
    the format "hpc_session=<token>" ready to be passed as a Cookie header.
    """
    import httpx

    base = fastapi_base_url
    credentials = {
        "rm":    ("rm",         "rm"),
        "owner": ("owner_test", "owner_test"),
        "qa":    ("qa",         "qa"),
        "admin": ("admin",      _PARITY_ADMIN_PW),
    }
    cookies: dict[str, str] = {}
    with httpx.Client(trust_env=False, timeout=15) as client:
        for role, (username, password) in credentials.items():
            resp = client.post(
                f"{base}/api/login",
                content=json.dumps(
                    {"username": username, "password": password},
                    ensure_ascii=False,
                ).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                follow_redirects=False,
            )
            assert resp.status_code == 200, (
                f"Login failed for role={role} ({username}): HTTP {resp.status_code}"
            )
            # Extract hpc_session from Set-Cookie header
            set_cookie = resp.headers.get("set-cookie", "")
            token = ""
            for part in set_cookie.split(";"):
                if part.strip().startswith("hpc_session="):
                    token = part.strip()
                    break
            assert token, f"No hpc_session cookie in login response for role={role}"
            cookies[role] = token
    return cookies
