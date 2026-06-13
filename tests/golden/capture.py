"""Phase 0 golden fixture capture script.

Creates an isolated temp DB seeded with a small representative dataset, boots
the current server.py as a subprocess on an ephemeral port, fires a
representative set of GET + POST /api/* requests, and writes each response as a
golden file under tests/golden/responses/<name>.json.

Usage (from repo root):
    python tests/golden/capture.py

Environment:
    HPC_ADMIN_PASSWORD  — optional; defaults to "golden_admin_pw" for capture runs

The script is fully self-contained: it does NOT depend on pytest or conftest.py.
Re-running it overwrites the golden files in place.

Alignment with tests/conftest.py (impl-testbase):
    tests/conftest.py exposes seed_release / seed_cicd_task / seed_wiki_article /
    seed_snapshot / seed_cicd_request as plain module-level helpers that are
    importable here.  This script uses its own slightly richer seed (3 apps vs. 1,
    plus artifacts + release schedule) so the goldens cover more response shapes.
    Phase 2 replay uses the conftest fixtures for the authoritative minimal DB;
    this seed is only for the golden recording session.  The user roster is shared
    — both call core.ensure_default_user which core.connect invokes automatically.

Scrubbing:
    All non-deterministic normalisation is delegated to scrub.scrub() from
    tests/golden/scrub.py.  Do NOT add a local scrub() — a single canonical
    implementation prevents capture/replay divergence (the 'now' key in QA
    reports was a past source of silent mismatch).
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

# Repo root is two directories above this file (tests/golden/capture.py -> root)
REPO_ROOT = Path(__file__).resolve().parent.parent.parent

# tests/golden/ directory — scrub.py lives here alongside this script
GOLDEN_DIR = Path(__file__).resolve().parent

# Output directory for golden files
RESPONSES_DIR = GOLDEN_DIR / "responses"

# Fixed admin password used only for capture runs so the DB stays deterministic
CAPTURE_ADMIN_PW = "golden_admin_pw"

# Artifact kinds the server exposes
ARTIFACT_KINDS = ["release_note", "manual", "ai4sci", "data"]

# Inject GOLDEN_DIR into sys.path so scrub is importable even when this script
# is run directly (not via pytest which would set it up automatically).
if str(GOLDEN_DIR) not in sys.path:
    sys.path.insert(0, str(GOLDEN_DIR))

from scrub import scrub  # noqa: E402  (after sys.path tweak)

# ---------------------------------------------------------------------------
# DB seeding
# ---------------------------------------------------------------------------

SEED_CSV_TEXT = """\
官方名称,类型,APP类型,Owner,app_version,maca_chip,hpcc_chip,arch,maca_version,git_url,git_branch
GoldenAmber,HPC,分子动力学,owner_test,22,"c500,x301",x201,x86,20260511,ssh://gerrit/PDE/HPC/hpc_amber,maca
GoldenLammps,AI4Sci,分子动力学,owner_test,stable,,,,20260511,ssh://gerrit/PDE/HPC/hpc_lammps,main
GoldenHpl,HPC,高性能计算,owner_test,2.3,c500,,x86,20260511,ssh://gerrit/PDE/HPC/hpc_hpl,maca
"""


def seed_db(db_path: Path, admin_password: str) -> dict:
    """Seed the SQLite DB at db_path with a minimal representative dataset.

    Returns a dict of key IDs (release_id, app_ids, cicd_task_id, wiki_article_id)
    so capture() can form correct request URLs/bodies.
    """
    sys.path.insert(0, str(REPO_ROOT))
    from release_system import core
    from release_system.wiki import core as wiki_core

    conn = core.connect(db_path)

    # 1. Create admin user (mirrors server.py ensure_admin_user logic)
    core.create_user(conn, "admin", admin_password, "Admin")

    # 2. Seed default users (rm / owner_test / qa / spd_test / guest);
    #    ensure_default_user is idempotent — core.connect already calls it.
    core.ensure_default_user(conn)

    # 3. Import initial CSV → creates first release and three apps
    rows = list(core.parse_csv_text(SEED_CSV_TEXT))
    release_id = core.import_initial_rows(
        conn,
        rows,
        release_name="Golden-Release-1",
        maca_version="20260511",
        app_freeze_deadline="2026-12-31 23:59",
        doc_deadline="2026-12-31 23:59",
    )

    # 4. Collect app_ids from the seeded release
    release = core.get_release(conn, release_id)
    app_ids = list(release["snapshots"].keys())

    # 5. Seed a CICD task via RM auto-approve path (RM submitter → immediately approved)
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

    # 6. Leave a pending Owner modify request so /api/cicd/requests shows variety
    core.submit_cicd_request(
        conn,
        task_id=cicd_task_id,
        request_type="modify",
        payload={"notes": {"old": "golden capture seed task", "new": "owner wants change"}},
        submitter="owner_test",
        submitter_role="Owner",
        submitter_display="Owner Test",
    )

    # 7. Seed a wiki article (RM role — required by wiki WRITE_ROLES)
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

    # 8. Set QA status on one app so /api/qa-reports has content
    # Use qa_passed — has_issues requires a non-empty note and we don't want
    # the batch to fail on validation.
    core.qa_set_status_batch(
        conn,
        release_id,
        [{"app_id": app_ids[0], "status": "qa_passed", "note": "golden qa note"}],
        user="qa",
        role="QA",
    )

    # 9. Generate artifacts (release_note, manual, ai4sci, data)
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


# ---------------------------------------------------------------------------
# Free port helper
# ---------------------------------------------------------------------------


def find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# ---------------------------------------------------------------------------
# Server lifecycle
# ---------------------------------------------------------------------------


def wait_for_server(host: str, port: int, timeout: float = 15.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.3):
                return
        except OSError:
            time.sleep(0.1)
    raise RuntimeError(f"Server at {host}:{port} did not become ready within {timeout}s")


# ---------------------------------------------------------------------------
# HTTP client (using httpx)
# ---------------------------------------------------------------------------


def _make_headers(cookie: str) -> dict[str, str]:
    headers: dict[str, str] = {"Content-Type": "application/json"}
    if cookie:
        headers["Cookie"] = cookie
    return headers


def _client():
    """Return an httpx Client with environment proxy bypassed for localhost.

    The environment may carry http_proxy (e.g. 127.0.0.1:3576 MCP proxy).
    We always talk directly to the test server on 127.0.0.1, so trust_env=False
    is required to avoid routing through the proxy.
    """
    import httpx

    return httpx.Client(trust_env=False, timeout=15)


def _do_get(
    base: str, path: str, cookie: str, params: str = ""
) -> tuple[int, object, dict]:
    url = f"{base}{path}"
    if params:
        url = f"{url}?{params}"
    with _client() as client:
        resp = client.get(url, headers=_make_headers(cookie), follow_redirects=False)
    try:
        body = resp.json()
    except Exception:
        body = resp.text
    return resp.status_code, body, dict(resp.headers)


def _do_post(
    base: str, path: str, cookie: str, payload: dict
) -> tuple[int, object, dict, str]:
    """Returns (status, body, headers, new_cookie)."""
    with _client() as client:
        resp = client.post(
            f"{base}{path}",
            content=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers=_make_headers(cookie),
            follow_redirects=False,
        )
    new_cookie = cookie
    set_cookie = resp.headers.get("set-cookie", "")
    if "hpc_session=" in set_cookie:
        for part in set_cookie.split(";"):
            if part.strip().startswith("hpc_session="):
                new_cookie = part.strip()
                break
    try:
        body = resp.json()
    except Exception:
        body = resp.text
    return resp.status_code, body, dict(resp.headers), new_cookie


# ---------------------------------------------------------------------------
# Golden file writer
# ---------------------------------------------------------------------------


def write_golden(
    name: str,
    status: int,
    body: object,
    note: str = "",
    *,
    method: str = "GET",
    path: str = "",
    params: str = "",
    role: str = "",
    post_body: dict | None = None,
    expected_status: int | None = None,
) -> None:
    """Write one golden file to responses/<name>.json.

    The envelope contains both the captured response (status + scrubbed body)
    and the request metadata needed for Phase 2 automated replay:

        _method   — "GET" or "POST"
        _path     — e.g. "/api/cicd/tasks"
        _params   — query string without leading '?', e.g. "status=Running"
        _role     — which seeded user's session to use: "rm", "owner", "qa", "admin"
        _body     — POST payload dict (None for GET requests)

    Phase 2 test_golden_replay.py reads these fields to re-issue the request
    against the new FastAPI backend without any hand-written request specs.
    """
    RESPONSES_DIR.mkdir(parents=True, exist_ok=True)
    scrubbed = scrub(body)
    record: dict = {
        "_golden_name": name,
        "_note": note,
        # Replay metadata — Phase 2 uses these to re-issue the request
        "_method": method,
        "_path": path,
        "_params": params,
        "_role": role,
        "_body": post_body,
        "status": status,
        "body": scrubbed,
    }
    out_path = RESPONSES_DIR / f"{name}.json"
    out_path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    # Treat caller-supplied expected_status as the success criterion
    exp = expected_status if expected_status is not None else (status if status < 400 else None)
    ok_mark = "OK" if exp is not None and status == exp and status < 500 else "ERR"
    print(f"  [{ok_mark}] {name}  HTTP {status}  -> {out_path.name}")


# ---------------------------------------------------------------------------
# Main capture routine
# ---------------------------------------------------------------------------


def capture(db_path: Path, port: int, ids: dict) -> None:  # noqa: C901  (long but linear)
    base = f"http://127.0.0.1:{port}"
    release_id = ids["release_id"]
    first_app_id = ids["first_app_id"]
    cicd_task_id = ids["cicd_task_id"]
    wiki_article_id = ids["wiki_article_id"]

    print("\n--- Phase 1: Login ---")

    # POST /api/login (admin)
    status, body, _, admin_cookie = _do_post(
        base, "/api/login", "", {"username": "admin", "password": CAPTURE_ADMIN_PW}
    )
    write_golden(
        "post_login_admin", status, body, "Admin login response body",
        method="POST", path="/api/login", role="admin",
        post_body={"username": "admin", "password": CAPTURE_ADMIN_PW},
    )

    # POST /api/login (rm)
    status, body, _, rm_cookie = _do_post(
        base, "/api/login", "", {"username": "rm", "password": "rm"}
    )
    write_golden(
        "post_login_rm", status, body, "RM login response body",
        method="POST", path="/api/login", role="rm",
        post_body={"username": "rm", "password": "rm"},
    )

    # POST /api/login (owner)
    status, body, _, owner_cookie = _do_post(
        base, "/api/login", "", {"username": "owner_test", "password": "owner_test"}
    )
    write_golden(
        "post_login_owner", status, body, "Owner login response body",
        method="POST", path="/api/login", role="owner",
        post_body={"username": "owner_test", "password": "owner_test"},
    )

    # POST /api/login (qa)
    status, body, _, qa_cookie = _do_post(
        base, "/api/login", "", {"username": "qa", "password": "qa"}
    )
    write_golden(
        "post_login_qa", status, body, "QA login response body",
        method="POST", path="/api/login", role="qa",
        post_body={"username": "qa", "password": "qa"},
    )

    print("\n--- Phase 2: Public / lightweight GET endpoints ---")

    status, body, _ = _do_get(base, "/api/me", "")
    write_golden(
        "get_me_unauthenticated", status, body, "me with no session -> user: null",
        path="/api/me", role="",
    )

    status, body, _ = _do_get(base, "/api/me", rm_cookie)
    write_golden(
        "get_me_rm", status, body, "me with RM session",
        path="/api/me", role="rm",
    )

    status, body, _ = _do_get(base, "/api/ldap/status", "")
    write_golden(
        "get_ldap_status", status, body, "LDAP status (public endpoint)",
        path="/api/ldap/status", role="",
    )

    print("\n--- Phase 3: Core state ---")

    status, body, _ = _do_get(base, "/api/state", rm_cookie, f"release_id={release_id}")
    write_golden(
        "get_state_rm", status, body, "/api/state for RM with seeded release",
        path="/api/state", params=f"release_id={release_id}", role="rm",
    )

    status, body, _ = _do_get(base, "/api/state", owner_cookie, f"release_id={release_id}")
    write_golden(
        "get_state_owner", status, body, "/api/state for Owner",
        path="/api/state", params=f"release_id={release_id}", role="owner",
    )

    print("\n--- Phase 4: CICD workbench ---")

    status, body, _ = _do_get(base, "/api/cicd/tasks", rm_cookie)
    write_golden(
        "get_cicd_tasks", status, body, "list all CICD tasks",
        path="/api/cicd/tasks", role="rm",
    )

    status, body, _ = _do_get(base, "/api/cicd/tasks", rm_cookie, "status=Running")
    write_golden(
        "get_cicd_tasks_running", status, body, "CICD tasks filtered to Running",
        path="/api/cicd/tasks", params="status=Running", role="rm",
    )

    status, body, _ = _do_get(
        base, f"/api/cicd/tasks/{cicd_task_id}/history", rm_cookie
    )
    write_golden(
        "get_cicd_task_history", status, body, "task request history",
        path=f"/api/cicd/tasks/{cicd_task_id}/history", role="rm",
    )

    status, body, _ = _do_get(base, "/api/cicd/requests", rm_cookie)
    write_golden(
        "get_cicd_requests_rm", status, body, "CICD requests RM view",
        path="/api/cicd/requests", role="rm",
    )

    status, body, _ = _do_get(base, "/api/cicd/requests", rm_cookie, "status=pending")
    write_golden(
        "get_cicd_requests_pending", status, body, "CICD requests pending only",
        path="/api/cicd/requests", params="status=pending", role="rm",
    )

    status, body, _ = _do_get(base, "/api/cicd/requests", owner_cookie, "only_mine=1")
    write_golden(
        "get_cicd_requests_owner_mine", status, body, "Owner's own CICD requests",
        path="/api/cicd/requests", params="only_mine=1", role="owner",
    )

    status, body, _ = _do_get(base, "/api/cicd/notifications", rm_cookie)
    write_golden(
        "get_cicd_notifications_rm", status, body, "CICD notification counts for RM",
        path="/api/cicd/notifications", role="rm",
    )

    status, body, _ = _do_get(base, "/api/cicd/notifications", owner_cookie)
    write_golden(
        "get_cicd_notifications_owner", status, body, "CICD notification counts for Owner",
        path="/api/cicd/notifications", role="owner",
    )

    status, body, _ = _do_get(base, "/api/cicd/deliveries", rm_cookie)
    write_golden(
        "get_cicd_deliveries_rm", status, body, "CICD deliveries RM view",
        path="/api/cicd/deliveries", role="rm",
    )

    print("\n--- Phase 5: QA reports ---")

    status, body, _ = _do_get(
        base, "/api/qa-reports", rm_cookie, f"release_id={release_id}"
    )
    write_golden(
        "get_qa_reports", status, body, "QA reports for seeded release",
        path="/api/qa-reports", params=f"release_id={release_id}", role="rm",
    )

    print("\n--- Phase 6: Wiki ---")

    status, body, _ = _do_get(base, "/api/wiki/articles", rm_cookie)
    write_golden(
        "get_wiki_articles", status, body, "list wiki articles (RM)",
        path="/api/wiki/articles", role="rm",
    )

    status, body, _ = _do_get(
        base, f"/api/wiki/articles/{wiki_article_id}", rm_cookie
    )
    write_golden(
        "get_wiki_article_by_id", status, body, "single wiki article",
        path=f"/api/wiki/articles/{wiki_article_id}", role="rm",
    )

    # Intentional 404
    status, body, _ = _do_get(base, "/api/wiki/articles/wiki_doesnotexist", rm_cookie)
    write_golden(
        "get_wiki_article_404", status, body, "nonexistent wiki article returns 404",
        path="/api/wiki/articles/wiki_doesnotexist", role="rm",
        expected_status=404,
    )

    print("\n--- Phase 7: Artifacts ---")

    for kind in ARTIFACT_KINDS:
        status, body, _ = _do_get(
            base, f"/api/artifacts/{kind}", rm_cookie, f"release_id={release_id}"
        )
        # Artifact responses are plain text/CSV; store a truncated preview
        record_body: object
        if isinstance(body, str):
            record_body = body[:500] + ("...[truncated]" if len(body) > 500 else "")
        else:
            record_body = body
        write_golden(
            f"get_artifact_{kind}", status, record_body,
            f"artifact kind={kind} for seeded release",
            path=f"/api/artifacts/{kind}",
            params=f"release_id={release_id}",
            role="rm",
        )

    print("\n--- Phase 8: Test scope CSV ---")

    status, body, _ = _do_get(
        base, "/api/test-scope.csv", rm_cookie, f"release_id={release_id}"
    )
    csv_preview: object
    if isinstance(body, str):
        csv_preview = body[:500] + ("...[truncated]" if len(body) > 500 else "")
    else:
        csv_preview = body
    write_golden(
        "get_test_scope_csv", status, csv_preview, "test-scope CSV for RM",
        path="/api/test-scope.csv", params=f"release_id={release_id}", role="rm",
    )

    print("\n--- Phase 9: App audit ---")

    status, body, _ = _do_get(
        base, "/api/app-audit", rm_cookie,
        f"app_id={first_app_id}&release_id={release_id}",
    )
    write_golden(
        "get_app_audit", status, body, "audit log for first seeded app",
        path="/api/app-audit",
        params=f"app_id={first_app_id}&release_id={release_id}",
        role="rm",
    )

    print("\n--- Phase 10: Admin endpoints ---")

    status, body, _ = _do_get(base, "/api/admin/users", admin_cookie)
    write_golden(
        "get_admin_users", status, body, "user list (Admin only)",
        path="/api/admin/users", role="admin",
    )

    print("\n--- Phase 11: Representative POSTs ---")

    # POST /api/qa/status-batch (QA)
    _qa_batch_body = {
        "release_id": release_id,
        "items": [{"app_id": first_app_id, "status": "qa_passed", "note": "golden qa passed"}],
    }
    status, body, _, _ = _do_post(base, "/api/qa/status-batch", qa_cookie, _qa_batch_body)
    write_golden(
        "post_qa_status_batch", status, body, "QA status batch update (qa_passed)",
        method="POST", path="/api/qa/status-batch", role="qa",
        post_body=_qa_batch_body,
    )

    # POST /api/apps/update — RM updates release_decision
    _update_decision_body = {
        "app_id": first_app_id,
        "release_id": release_id,
        "snapshot": {"release_decision": "cicd_only"},
    }
    status, body, _, _ = _do_post(
        base, "/api/apps/update", rm_cookie, _update_decision_body
    )
    write_golden(
        "post_apps_update_decision", status, body,
        "RM updates release_decision on first app",
        method="POST", path="/api/apps/update", role="rm",
        post_body=_update_decision_body,
    )

    # POST /api/apps/update — RM updates doc field
    _update_doc_body = {
        "app_id": first_app_id,
        "release_id": release_id,
        "snapshot": {"doc": {"intro": "Golden intro text for parity test."}},
    }
    status, body, _, _ = _do_post(base, "/api/apps/update", rm_cookie, _update_doc_body)
    write_golden(
        "post_apps_update_doc", status, body, "RM updates doc field on first app",
        method="POST", path="/api/apps/update", role="rm",
        post_body=_update_doc_body,
    )

    # POST /api/cicd/notifications/mark-visited (RM)
    status, body, _, _ = _do_post(
        base, "/api/cicd/notifications/mark-visited", rm_cookie, {}
    )
    write_golden(
        "post_cicd_mark_visited", status, body, "mark CICD notifications as visited",
        method="POST", path="/api/cicd/notifications/mark-visited", role="rm",
        post_body={},
    )

    # POST /api/cicd/requests/submit — Owner submits create request (→ pending)
    _owner_submit_body = {
        "task_id": None,
        "request_type": "create",
        "payload": {
            "app_name": "GoldenLammps",
            "app_version": "stable",
            "repo_type": "git",
            "repo_name": "ssh://gerrit/PDE/HPC/hpc_lammps",
            "branch": "main",
            "build_product": ["maca"],
            "community_artifact": [],
            "build_image": "hpc/lammps:latest",
            "test_timeout": 60,
            "owner_username": "owner_test",
            "status": "Running",
            "notes": "owner golden submission",
        },
    }
    status, body, _, _ = _do_post(
        base, "/api/cicd/requests/submit", owner_cookie, _owner_submit_body
    )
    write_golden(
        "post_cicd_request_submit_owner", status, body,
        "Owner submits CICD create request (enters pending queue)",
        method="POST", path="/api/cicd/requests/submit", role="owner",
        post_body=_owner_submit_body,
    )

    print("\n--- Phase 12: Logout ---")

    status, body, _, _ = _do_post(base, "/api/logout", rm_cookie, {})
    write_golden(
        "post_logout", status, body, "logout clears session cookie",
        method="POST", path="/api/logout", role="rm", post_body={},
    )

    print(f"\nGolden responses written to: {RESPONSES_DIR}")
    total = len(list(RESPONSES_DIR.glob("*.json")))
    print(f"Total golden files: {total}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> int:
    admin_pw = os.environ.get("HPC_ADMIN_PASSWORD", CAPTURE_ADMIN_PW)

    tmp_dir = tempfile.mkdtemp(prefix="hpc_golden_")
    db_path = Path(tmp_dir) / "golden.db"
    port = find_free_port()

    print("Golden capture starting")
    print(f"  Temp dir : {tmp_dir}")
    print(f"  DB path  : {db_path}")
    print(f"  Port     : {port}")

    try:
        print("\nSeeding database...")
        ids = seed_db(db_path, admin_pw)
        print(f"  release_id      : {ids['release_id']}")
        print(f"  app_ids         : {ids['app_ids']}")
        print(f"  cicd_task_id    : {ids['cicd_task_id']}")
        print(f"  wiki_article_id : {ids['wiki_article_id']}")

        # Launch server.py via a thin wrapper that patches DB_PATH before main().
        # server.py hardcodes DB_PATH = ROOT / "release_system.db"; the wrapper
        # overrides the global after import but before main() opens a connection.
        wrapper_path = Path(tmp_dir) / "_capture_server.py"
        wrapper_path.write_text(
            f"""\
import sys
sys.path.insert(0, {str(REPO_ROOT)!r})
import server
from pathlib import Path
server.DB_PATH = Path({str(db_path)!r})
server.ADMIN_PASSWORD_FILE = Path({str(tmp_dir)!r}) / "admin_password.local"
server.main()
""",
            encoding="utf-8",
        )

        proc = subprocess.Popen(
            [sys.executable, str(wrapper_path), "--host", "127.0.0.1", "--port", str(port)],
            env={**os.environ, "HPC_ADMIN_PASSWORD": admin_pw},
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=str(REPO_ROOT),
        )

        try:
            print(f"\nWaiting for server on port {port}...")
            wait_for_server("127.0.0.1", port, timeout=20.0)
            print("Server ready.")
            capture(db_path, port, ids)
        finally:
            print("\nShutting down server...")
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
            stderr_out = (
                proc.stderr.read().decode("utf-8", errors="replace") if proc.stderr else ""
            )
            if stderr_out.strip():
                print("[server stderr]")
                for line in stderr_out.splitlines()[-30:]:
                    print(" ", line)

    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    return 0


if __name__ == "__main__":
    sys.exit(main())
