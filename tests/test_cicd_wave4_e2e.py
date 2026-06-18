"""Phase-4 Wave-4 — end-to-end R3 verification with fabricated app_info.

This module proves the full R3 chain works:

  1. fetch-preview with fabricated app_info (make_fake_app_info_fetch factory):
       • preview_cicd_app_info returns all 7 fields from the fake payload
       • identity ALWAYS returned; content failure → soft flag (not 502)

  2. CICD-first 建 app (full e2e, no Gerrit network):
       preview → confirm 7 fields → cicd_first_new_app with app_info_parsed
       → snapshot has owner_confirmed=True + cicd_only decision
       → pending create request (Ruling B: never auto-approved)
       → RM self-approve → cicd_task lands with app_id linked

  3. 决策→状态联动 on migrated data (the key Wave-4 proof):
       BEFORE migration (app_id NULL on task): sync_decision_to_cicd no-op
       AFTER migration (app_id backfilled): sync fires → pending modify request
       → RM approve → task status follows

  4. 停止只能经 App:
       • CICD workbench modify with 'status' → rejected (status-lock V3)
       • App.update_snapshot(stopped) → pending sync request
       • CICD abandon/delete APIs are removed; App lifecycle owns retire/delete

  5. 权限 (plan §3.7 Ruling C):
       Admin → 403 on CICD create/approve/deliver
       RM → self-approve (is_self_approved=1)
       SPD → delivery chain allowed; create/approve → 403
       Owner → create allowed; approve → 403

  6. 时区: timestamps are naive Beijing — no UTC offset, no double offset.

All tests are offline (no Gerrit network, no LDAP, no Jira).
Uses TestClient (ASGI) + temp_db fixture; never touches the live DB.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from app.db.connection import connect as app_connect
from app.db.connection import transaction
from app.repositories import apps_repo, cicd_repo
from app.services import app_service, cicd_service
from app.timeutil import beijing_timestamp
from tests.conftest import seed_release


# ---------------------------------------------------------------------------
# Shared constants / helpers
# ---------------------------------------------------------------------------

_SHORT_NAME = "hpc_w4e2e"
_BRANCH = "w4-test"
_OFFICIAL_NAME = "W4E2EApp"
_RESOLVED_URL = "ssh://sw-gerrit-devops.metax-internal.com:29418/PDE/HPC/hpc_w4e2e"
_APP_ID = "w4e2eapp"   # normalize_name("W4E2EApp")

_BUILD_PAYLOAD: dict = {
    "build_product": ["maca"],
    "community_artifact": [],
    "build_image": "hpc/w4e2e:latest",
    "test_timeout": 40,
    "notes": "wave4 e2e test",
}


def _make_fastapi_app(db_path, *, role: str = "RM", username: str = "rm"):
    """Create a FastAPI app with test-DB + fixed-auth overrides."""
    from app.deps import get_db, require_login
    from app.main import create_app

    fastapi_app = create_app()
    fastapi_app.dependency_overrides[get_db] = (
        lambda: (yield app_connect(db_path))
    )
    fastapi_app.dependency_overrides[require_login] = (
        lambda: {"username": username, "role": role, "display_name": ""}
    )
    return fastapi_app


def _insert_app_row(
    conn: sqlite3.Connection,
    *,
    app_id: str,
    git_url: str,
    git_branch: str,
) -> None:
    """Insert a minimal app row (satisfies FK for cicd_tasks.app_id)."""
    ts = beijing_timestamp()
    conn.execute(
        """
        INSERT OR IGNORE INTO apps (id, git_url, git_branch, created_by, created_at)
        VALUES (?, ?, ?, 'test', ?)
        """,
        (app_id, git_url, git_branch, ts),
    )


def _insert_linked_task(
    conn: sqlite3.Connection,
    *,
    app_id: str,
    status: str = "Running",
) -> str:
    """Insert a cicd_task linked to app_id.  Returns task_id."""
    _insert_app_row(conn, app_id=app_id, git_url=_RESOLVED_URL, git_branch=_BRANCH)
    task_id = cicd_repo.next_cicd_id(conn)
    ts = beijing_timestamp()
    with transaction(conn):
        cicd_repo.create_task(
            conn,
            task_id=task_id,
            app_id=app_id,         # ← backfilled (post-migration state)
            app_name="W4LinkedApp",
            app_version="1.0",
            repo_type="git",
            repo_name=_SHORT_NAME,
            branch=_BRANCH,
            build_product=["maca"],
            community_artifact=[],
            build_image="hpc/w4-linked:latest",
            test_timeout=40,
            owner_username="rm",
            status=status,
            notes="wave4 migration e2e",
            created_at=ts,
            updated_at=ts,
        )
    return task_id


def _insert_unlinked_task(
    conn: sqlite3.Connection,
    *,
    app_id: str,
    status: str = "Running",
) -> str:
    """Insert a cicd_task with app_id=NULL (pre-migration state).  Returns task_id."""
    _insert_app_row(conn, app_id=app_id, git_url=_RESOLVED_URL, git_branch=_BRANCH)
    task_id = cicd_repo.next_cicd_id(conn)
    ts = beijing_timestamp()
    with transaction(conn):
        cicd_repo.create_task(
            conn,
            task_id=task_id,
            app_id=None,          # ← NOT backfilled (pre-migration state)
            app_name="W4UnlinkedApp",
            app_version="1.0",
            repo_type="git",
            repo_name=_SHORT_NAME,
            branch=_BRANCH,
            build_product=["maca"],
            community_artifact=[],
            build_image="hpc/w4-unlinked:latest",
            test_timeout=40,
            owner_username="rm",
            status=status,
            notes="wave4 pre-migration e2e",
            created_at=ts,
            updated_at=ts,
        )
    return task_id


def _pending_modify_requests(conn: sqlite3.Connection, task_id: str) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM cicd_task_requests "
        "WHERE task_id = ? AND status = 'pending' AND request_type = 'modify' ORDER BY id",
        (task_id,),
    ).fetchall()
    result = []
    for r in rows:
        d = dict(r)
        d["_payload"] = json.loads(d.get("payload") or "{}")
        result.append(d)
    return result


def _get_task(conn: sqlite3.Connection, task_id: str) -> dict | None:
    return cicd_repo.get_task(conn, task_id)


# ---------------------------------------------------------------------------
# 1. make_fake_app_info_fetch factory — verifies 7 preview fields
# ---------------------------------------------------------------------------


class TestMakeFakeAppInfoFetch:
    """cicd_service.make_fake_app_info_fetch() creates a correct offline fetch fn."""

    def _preview(self, **overrides):
        """Call preview_cicd_app_info with the fake factory."""
        factory_kwargs = {
            "app_name": "FakeW4App",
            "app_version": "2.5",
            "x86_chips": ["C500", "N100"],
            "arm_chips": ["T5000"],
            "python_label": "3.11",
            "pytorch_label": "2.3",
            "os_label": "ubuntu22.04",
            "arch": "amd64",
            "commit_id": "fakew4commit0000000000000000000000000000",
        }
        factory_kwargs.update(overrides)
        fake_fetch = cicd_service.make_fake_app_info_fetch(**factory_kwargs)
        return cicd_service.preview_cicd_app_info(
            repo_type="git",
            repo_name=_SHORT_NAME,
            branch=_BRANCH,
            submitter_role="RM",
            _fetch_fn=fake_fetch,
        )

    def test_factory_returns_callable(self):
        """make_fake_app_info_fetch returns a callable."""
        fn = cicd_service.make_fake_app_info_fetch()
        assert callable(fn)

    def test_factory_callable_signature(self):
        """Returned callable accepts (git_url, branch, **kwargs)."""
        fn = cicd_service.make_fake_app_info_fetch()
        raw, commit_id = fn("ssh://gerrit/test", "main")
        assert isinstance(raw, str)
        assert isinstance(commit_id, str)

    def test_git_url_resolved(self):
        """preview returns the resolved full URL."""
        result = self._preview()
        assert result["git_url"] == _RESOLVED_URL

    def test_git_branch_returned(self):
        result = self._preview()
        assert result["git_branch"] == _BRANCH

    def test_app_version_field(self):
        """app_version from factory is returned."""
        result = self._preview()
        assert result["app_version"] == "2.5"

    def test_x86_chips_comma_string(self):
        """x86_chips is a comma-joined string."""
        result = self._preview()
        # order_chips sorts by a canonical order — just check chips are present
        assert "C500" in result["x86_chips"]
        assert "N100" in result["x86_chips"]

    def test_arm_chips_present(self):
        """arm_chips non-empty when factory has ARM chips."""
        result = self._preview()
        assert "T5000" in result["arm_chips"]

    def test_python_label_field(self):
        result = self._preview()
        assert result["python_label"] == "3.11"

    def test_pytorch_label_field(self):
        result = self._preview()
        assert result["pytorch_label"] == "2.3"

    def test_os_field(self):
        result = self._preview()
        assert result["os"] == "ubuntu22.04"

    def test_arch_field(self):
        """arch string includes amd64 (may also include arm64 when ARM chips provided)."""
        result = self._preview()
        assert "amd64" in result["arch"], (
            f"Expected 'amd64' in arch string, got: {result['arch']!r}"
        )

    def test_commit_id_returned(self):
        """commit_id from factory is passed through."""
        result = self._preview()
        assert result["commit_id"] == "fakew4commit0000000000000000000000000000"

    def test_parsed_blob_present(self):
        """'parsed' blob is present so it can be passed to cicd_first_new_app."""
        result = self._preview()
        assert "parsed" in result
        assert result["parsed"]["app_version"] == "2.5"

    def test_unavailable_false_on_success(self):
        """Happy path: app_info_unavailable=False."""
        result = self._preview()
        assert result["app_info_unavailable"] is False
        assert result.get("app_info_error") is None

    def test_needs_network_false_for_git_type(self):
        """Short git-type repo → identity offline → needs_network=False."""
        result = self._preview()
        assert result["needs_network"] is False

    def test_all_7_fields_present(self):
        """All 7 required preview fields are present when fetch succeeds."""
        result = self._preview()
        required = {
            "app_version", "x86_chips", "arm_chips",
            "python_label", "pytorch_label", "os", "arch",
        }
        missing = required - set(result.keys())
        assert not missing, f"Missing preview fields: {missing}"

    def test_custom_app_version(self):
        """Factory accepts custom app_version."""
        result = self._preview(app_version="9.9.9")
        assert result["app_version"] == "9.9.9"

    def test_arm_chips_empty_when_none(self):
        """arm_chips=[] (default) → arm_chips='' in preview."""
        fn = cicd_service.make_fake_app_info_fetch(arm_chips=[])
        result = cicd_service.preview_cicd_app_info(
            repo_type="git",
            repo_name=_SHORT_NAME,
            branch=_BRANCH,
            submitter_role="RM",
            _fetch_fn=fn,
        )
        assert result["arm_chips"] == ""


# ---------------------------------------------------------------------------
# 2. fetch-preview: identity returned even on content-fetch failure
# ---------------------------------------------------------------------------


class TestFetchPreviewIdentityOnFailure:
    """Wave-4 requirement: identity ALWAYS returned regardless of fetch result.

    These tests verify that a Gerrit outage / network error does NOT cause
    the endpoint to return 502 or raise — instead it returns the derived
    (git_url, git_branch) with app_info_unavailable=True so the frontend
    wizard can still display the mapping.
    """

    def _preview_with_failing_fetch(self, *, exc_msg="Connection refused"):
        """Call preview_cicd_app_info with an injected failing fetch fn."""
        def _fail(git_url, branch, **kwargs):
            raise RuntimeError(exc_msg)

        return cicd_service.preview_cicd_app_info(
            repo_type="git",
            repo_name=_SHORT_NAME,
            branch=_BRANCH,
            submitter_role="RM",
            _fetch_fn=_fail,
        )

    def test_failure_does_not_raise(self):
        """Gerrit content fetch failure must NOT raise — returns dict."""
        result = self._preview_with_failing_fetch()
        assert isinstance(result, dict)

    def test_identity_in_response_on_failure(self):
        """git_url and git_branch present even when fetch failed."""
        result = self._preview_with_failing_fetch()
        assert result["git_url"] == _RESOLVED_URL
        assert result["git_branch"] == _BRANCH

    def test_unavailable_flag_set(self):
        """app_info_unavailable=True on fetch failure."""
        result = self._preview_with_failing_fetch()
        assert result["app_info_unavailable"] is True

    def test_error_detail_set(self):
        """app_info_error contains the failure message."""
        result = self._preview_with_failing_fetch(exc_msg="timed out after 30s")
        assert "timed out" in (result.get("app_info_error") or "")

    def test_app_info_fields_absent_on_failure(self):
        """The 7 app_info fields are NOT present when fetch failed."""
        result = self._preview_with_failing_fetch()
        for field in ("app_version", "x86_chips", "arm_chips",
                      "python_label", "pytorch_label", "os", "arch"):
            assert field not in result, f"Field '{field}' should be absent on failure"

    def test_parsed_absent_on_failure(self):
        """'parsed' blob absent when fetch failed."""
        result = self._preview_with_failing_fetch()
        assert "parsed" not in result

    def test_empty_repo_name_still_raises_value_error(self):
        """Empty repo_name → ValueError (caller input error, not Gerrit issue)."""
        def _never_called(*args, **kwargs):
            raise AssertionError("fetch_fn should not be called for empty repo_name")

        with pytest.raises(ValueError):
            cicd_service.preview_cicd_app_info(
                repo_type="git",
                repo_name="",
                branch=_BRANCH,
                submitter_role="RM",
                _fetch_fn=_never_called,
            )

    def test_http_gerrit_fail_returns_200_not_502(self, db_path):
        """HTTP fetch-preview with Gerrit failure → 200, not 502 (Wave-4 change)."""
        from unittest.mock import patch
        from fastapi.testclient import TestClient

        app = _make_fastapi_app(db_path)
        with TestClient(app, raise_server_exceptions=False) as client:
            with patch(
                "app.integrations.gerrit.fetch_app_info",
                side_effect=RuntimeError("无法连接 Gerrit: refused"),
            ):
                resp = client.post(
                    "/api/cicd/apps/fetch-preview",
                    json={"repo_type": "git", "repo_name": _SHORT_NAME, "branch": _BRANCH},
                )
        assert resp.status_code == 200, (
            f"Gerrit failure should give 200+unavailable, got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        assert body["app_info_unavailable"] is True
        assert body["git_url"] == _RESOLVED_URL

    def test_http_gerrit_fail_identity_visible(self, db_path):
        """HTTP: git_url returned to wizard even when Gerrit is unreachable."""
        from unittest.mock import patch
        from fastapi.testclient import TestClient

        app = _make_fastapi_app(db_path)
        with TestClient(app, raise_server_exceptions=False) as client:
            with patch(
                "app.integrations.gerrit.fetch_app_info",
                side_effect=RuntimeError("network error"),
            ):
                resp = client.post(
                    "/api/cicd/apps/fetch-preview",
                    json={"repo_type": "git", "repo_name": _SHORT_NAME, "branch": _BRANCH},
                )
        body = resp.json()
        assert body.get("git_url") == _RESOLVED_URL
        assert body.get("git_branch") == _BRANCH


# ---------------------------------------------------------------------------
# 3. Full CICD-first chain with fabricated app_info → owner_confirmed
# ---------------------------------------------------------------------------


class TestCicdFirstChainFull:
    """End-to-end CICD-first create chain using fabricated app_info.

    Flow: make_fake_app_info_fetch → preview_cicd_app_info (confirm 7 fields)
    → cicd_first_new_app(app_info_parsed) → owner_confirmed=True, cicd_only
    → RM approve → task lands with app_id linked.
    """

    def _get_fake_preview(self):
        """Return preview result using the fake factory."""
        fake_fetch = cicd_service.make_fake_app_info_fetch(
            app_name=_OFFICIAL_NAME,
            app_version="3.0",
            x86_chips=["C500"],
            python_label="3.10",
            pytorch_label="2.2",
            os_label="ubuntu20.04",
            arch="amd64",
            commit_id="w4chain" + "0" * 33,
        )
        return cicd_service.preview_cicd_app_info(
            repo_type="git",
            repo_name=_SHORT_NAME,
            branch=_BRANCH,
            submitter_role="RM",
            _fetch_fn=fake_fetch,
        )

    def _get_snapshot(self, conn, app_id: str) -> dict:
        from release_system import core
        releases = core.list_releases(conn)
        unlocked = [r for r in releases if not r.get("released_locked")]
        assert unlocked, "No unlocked release"
        rel = core.get_release(conn, unlocked[0]["id"])
        snap = rel["snapshots"].get(app_id)
        assert snap is not None, f"No snapshot for {app_id}"
        return snap

    # --- Step 1: preview returns correct 7 fields from fake fetch ---

    def test_preview_7_fields_from_fake_fetch(self):
        """fake_fetch → preview yields all 7 required fields."""
        result = self._get_fake_preview()
        assert result["app_info_unavailable"] is False
        assert result["app_version"] == "3.0"
        assert "C500" in result["x86_chips"]
        assert result["python_label"] == "3.10"
        assert result["pytorch_label"] == "2.2"
        assert result["os"] == "ubuntu20.04"
        assert result["arch"] == "amd64"
        assert "parsed" in result

    def test_preview_identity_from_fake_fetch(self):
        """fake_fetch → preview yields correct derived identity."""
        result = self._get_fake_preview()
        assert result["git_url"] == _RESOLVED_URL
        assert result["git_branch"] == _BRANCH

    # --- Step 2: cicd_first_new_app with app_info_parsed ---

    def test_action_created(self, temp_db, tmp_dir):
        seed_release(temp_db, tmp_path=tmp_dir)
        preview = self._get_fake_preview()
        result = cicd_service.cicd_first_new_app(
            temp_db,
            official_name=_OFFICIAL_NAME,
            repo_type="git",
            repo_name=_SHORT_NAME,
            branch=_BRANCH,
            submitter="rm",
            submitter_role="RM",
            submitter_display="",
            payload=_BUILD_PAYLOAD,
            app_info_parsed=preview["parsed"],
            app_info_commit_id=preview["commit_id"],
        )
        assert result["ok"] is True
        assert result["action"] == "created"

    def test_owner_confirmed_true_with_app_info(self, temp_db, tmp_dir):
        """owner_confirmed=True when app_info_parsed provided (key R3 requirement)."""
        seed_release(temp_db, tmp_path=tmp_dir)
        preview = self._get_fake_preview()
        result = cicd_service.cicd_first_new_app(
            temp_db,
            official_name=_OFFICIAL_NAME,
            repo_type="git",
            repo_name=_SHORT_NAME,
            branch=_BRANCH,
            submitter="rm",
            submitter_role="RM",
            submitter_display="",
            payload=_BUILD_PAYLOAD,
            app_info_parsed=preview["parsed"],
            app_info_commit_id=preview["commit_id"],
        )
        snap = self._get_snapshot(temp_db, result["app_id"])
        assert snap.get("owner_confirmed") is True, (
            "owner_confirmed must be True when app_info_parsed provided"
        )

    def test_snapshot_decision_is_cicd_only(self, temp_db, tmp_dir):
        """Initial snapshot has release_decision='cicd_only' (CICD-first default)."""
        seed_release(temp_db, tmp_path=tmp_dir)
        preview = self._get_fake_preview()
        result = cicd_service.cicd_first_new_app(
            temp_db,
            official_name=_OFFICIAL_NAME,
            repo_type="git",
            repo_name=_SHORT_NAME,
            branch=_BRANCH,
            submitter="rm",
            submitter_role="RM",
            submitter_display="",
            payload=_BUILD_PAYLOAD,
            app_info_parsed=preview["parsed"],
            app_info_commit_id=preview["commit_id"],
        )
        snap = self._get_snapshot(temp_db, result["app_id"])
        assert snap.get("release_decision") == "cicd_only", (
            "CICD-first app initial decision must be cicd_only"
        )

    def test_version_from_app_info(self, temp_db, tmp_dir):
        """snapshot.version comes from app_info_parsed.app_version."""
        seed_release(temp_db, tmp_path=tmp_dir)
        preview = self._get_fake_preview()
        result = cicd_service.cicd_first_new_app(
            temp_db,
            official_name=_OFFICIAL_NAME,
            repo_type="git",
            repo_name=_SHORT_NAME,
            branch=_BRANCH,
            submitter="rm",
            submitter_role="RM",
            submitter_display="",
            payload=_BUILD_PAYLOAD,
            app_info_parsed=preview["parsed"],
            app_info_commit_id=preview["commit_id"],
        )
        snap = self._get_snapshot(temp_db, result["app_id"])
        assert snap.get("version") == "3.0"

    def test_pending_create_request(self, temp_db, tmp_dir):
        """Pending create request exists after cicd_first_new_app (Ruling B)."""
        seed_release(temp_db, tmp_path=tmp_dir)
        preview = self._get_fake_preview()
        result = cicd_service.cicd_first_new_app(
            temp_db,
            official_name=_OFFICIAL_NAME,
            repo_type="git",
            repo_name=_SHORT_NAME,
            branch=_BRANCH,
            submitter="rm",
            submitter_role="RM",
            submitter_display="",
            payload=_BUILD_PAYLOAD,
            app_info_parsed=preview["parsed"],
            app_info_commit_id=preview["commit_id"],
        )
        req = result["request"]
        assert req["status"] == "pending"
        assert req["task_id"] is None  # task only on RM approval

    # --- Step 3: RM approve → task lands with app_id ---

    def test_rm_approve_creates_linked_task(self, temp_db, tmp_dir):
        """RM approves pending create → cicd_task row created with app_id set."""
        seed_release(temp_db, tmp_path=tmp_dir)
        preview = self._get_fake_preview()
        create_result = cicd_service.cicd_first_new_app(
            temp_db,
            official_name=_OFFICIAL_NAME,
            repo_type="git",
            repo_name=_SHORT_NAME,
            branch=_BRANCH,
            submitter="rm",
            submitter_role="RM",
            submitter_display="",
            payload=_BUILD_PAYLOAD,
            app_info_parsed=preview["parsed"],
            app_info_commit_id=preview["commit_id"],
        )
        req_id = create_result["request"]["id"]
        approved = cicd_service.approve_request(
            temp_db, req_id, reviewer="rm", reviewer_role="RM"
        )
        task_id = approved["task_id"]
        assert task_id is not None
        task_row = temp_db.execute(
            "SELECT app_id FROM cicd_tasks WHERE id = ?", (task_id,)
        ).fetchone()
        assert task_row is not None
        assert task_row["app_id"] == create_result["app_id"], (
            "cicd_task.app_id must point to the created app after RM approval"
        )

    def test_rm_approve_task_status_running(self, temp_db, tmp_dir):
        """Approved create: task starts in Running status (cicd_only maps to Running)."""
        seed_release(temp_db, tmp_path=tmp_dir)
        preview = self._get_fake_preview()
        create_result = cicd_service.cicd_first_new_app(
            temp_db,
            official_name=_OFFICIAL_NAME,
            repo_type="git",
            repo_name=_SHORT_NAME,
            branch=_BRANCH,
            submitter="rm",
            submitter_role="RM",
            submitter_display="",
            payload=_BUILD_PAYLOAD,
            app_info_parsed=preview["parsed"],
            app_info_commit_id=preview["commit_id"],
        )
        req_id = create_result["request"]["id"]
        approved = cicd_service.approve_request(
            temp_db, req_id, reviewer="rm", reviewer_role="RM"
        )
        task = _get_task(temp_db, approved["task_id"])
        assert task["status"] == "Running"

    def test_rm_self_approve_flag(self, temp_db, tmp_dir):
        """RM approving own request: is_self_approved=1 (Ruling B audit)."""
        seed_release(temp_db, tmp_path=tmp_dir)
        preview = self._get_fake_preview()
        create_result = cicd_service.cicd_first_new_app(
            temp_db,
            official_name=_OFFICIAL_NAME,
            repo_type="git",
            repo_name=_SHORT_NAME,
            branch=_BRANCH,
            submitter="rm",  # RM submits
            submitter_role="RM",
            submitter_display="",
            payload=_BUILD_PAYLOAD,
            app_info_parsed=preview["parsed"],
            app_info_commit_id=preview["commit_id"],
        )
        req_id = create_result["request"]["id"]
        approved = cicd_service.approve_request(
            temp_db, req_id,
            reviewer="rm",  # same RM approves → self-approve
            reviewer_role="RM",
        )
        assert approved["is_self_approved"] == 1, (
            "RM self-approval must set is_self_approved=1 (Ruling B audit trail)"
        )


# ---------------------------------------------------------------------------
# 4. Decision→status linkage on migrated data (the KEY Wave-4 proof)
# ---------------------------------------------------------------------------


class TestDecisionSyncMigratedApp:
    """Prove: migration backfills app_id → sync_decision_to_cicd now fires.

    Pre-migration state: cicd_task.app_id = NULL → tasks_for_app() returns []
    → sync_decision_to_cicd returns None (no-op).

    Post-migration state: cicd_task.app_id = "app_id" → tasks_for_app() returns
    [task] → sync_decision_to_cicd creates a pending modify request.

    This is the technical heart of Wave-4: the R3 decision→status linkage
    only works for apps whose tasks have been app_id-backfilled by migration.
    """

    _APP_PRE = "app_w4_pre_mig"   # pre-migration: no app_id on task
    _APP_POST = "app_w4_post_mig"  # post-migration: app_id set on task

    def _sync(self, conn, app_id: str, decision: str) -> dict | None:
        """Call sync_decision_to_cicd in its own txn; return result."""
        with transaction(conn):
            return cicd_service.sync_decision_to_cicd(
                conn, app_id, decision, submitter="rm"
            )

    # --- Pre-migration: sync is no-op because app_id=NULL on task ---

    def test_pre_migration_sync_noop(self, temp_db):
        """Pre-migration: app_id=NULL on task → sync is a no-op (returns None)."""
        task_id = _insert_unlinked_task(temp_db, app_id=self._APP_PRE, status="Running")
        # Verify the task has no app_id (simulating pre-migration state)
        task_row = temp_db.execute(
            "SELECT app_id FROM cicd_tasks WHERE id = ?", (task_id,)
        ).fetchone()
        assert task_row["app_id"] is None, "This test requires app_id to be NULL"

        result = self._sync(temp_db, self._APP_PRE, "stopped")
        assert result is None, (
            "Pre-migration: sync must be no-op because tasks_for_app(app_id) "
            "returns [] when task.app_id is NULL"
        )

    def test_pre_migration_no_pending_request_created(self, temp_db):
        """Pre-migration: no pending modify request is created."""
        task_id = _insert_unlinked_task(temp_db, app_id=self._APP_PRE, status="Running")
        self._sync(temp_db, self._APP_PRE, "stopped")
        reqs = _pending_modify_requests(temp_db, task_id)
        assert len(reqs) == 0, (
            "Pre-migration: sync no-op must not create any pending modify requests"
        )

    # --- Post-migration: sync fires because app_id is backfilled ---

    def test_post_migration_sync_fires(self, temp_db):
        """Post-migration: app_id backfilled on task → sync creates pending modify."""
        task_id = _insert_linked_task(temp_db, app_id=self._APP_POST, status="Running")
        # Verify the task HAS app_id (simulating post-migration state)
        task_row = temp_db.execute(
            "SELECT app_id FROM cicd_tasks WHERE id = ?", (task_id,)
        ).fetchone()
        assert task_row["app_id"] == self._APP_POST, "This test requires app_id to be set"

        result = self._sync(temp_db, self._APP_POST, "stopped")
        assert result is not None, (
            "Post-migration: sync must fire because tasks_for_app(app_id) "
            "returns the linked task"
        )

    def test_post_migration_exactly_one_pending_request(self, temp_db):
        """Post-migration: exactly ONE pending modify request created (plan §3.5 b+)."""
        task_id = _insert_linked_task(temp_db, app_id=self._APP_POST, status="Running")
        self._sync(temp_db, self._APP_POST, "stopped")
        reqs = _pending_modify_requests(temp_db, task_id)
        assert len(reqs) == 1, (
            "Post-migration: exactly one pending modify request must be created"
        )

    def test_post_migration_correct_status_mapping_stopped(self, temp_db):
        """Post-migration: stopped decision → Stopped status in pending modify."""
        task_id = _insert_linked_task(temp_db, app_id=self._APP_POST, status="Running")
        self._sync(temp_db, self._APP_POST, "stopped")
        reqs = _pending_modify_requests(temp_db, task_id)
        assert reqs[0]["_payload"]["status"]["new"] == "Stopped"
        assert reqs[0]["_payload"]["status"]["old"] == "Running"

    def test_post_migration_correct_status_mapping_release(self, temp_db):
        """Post-migration: release decision → Running status in pending modify."""
        task_id = _insert_linked_task(temp_db, app_id=self._APP_POST, status="Stopped")
        self._sync(temp_db, self._APP_POST, "release")
        reqs = _pending_modify_requests(temp_db, task_id)
        assert reqs[0]["_payload"]["status"]["new"] == "Running"

    def test_post_migration_correct_status_mapping_cicd_only(self, temp_db):
        """Post-migration: cicd_only decision → Running status (same as release)."""
        task_id = _insert_linked_task(temp_db, app_id=self._APP_POST, status="Stopped")
        self._sync(temp_db, self._APP_POST, "cicd_only")
        reqs = _pending_modify_requests(temp_db, task_id)
        assert reqs[0]["_payload"]["status"]["new"] == "Running"

    def test_post_migration_request_origin_is_sync(self, temp_db):
        """Post-migration: created request has origin='release_decision_sync'."""
        task_id = _insert_linked_task(temp_db, app_id=self._APP_POST, status="Running")
        self._sync(temp_db, self._APP_POST, "stopped")
        row = temp_db.execute(
            "SELECT origin FROM cicd_task_requests WHERE task_id=?", (task_id,)
        ).fetchone()
        assert row["origin"] == "release_decision_sync"

    def test_post_migration_task_status_unchanged_pending(self, temp_db):
        """Post-migration: task status NOT changed immediately — awaits RM approval."""
        task_id = _insert_linked_task(temp_db, app_id=self._APP_POST, status="Running")
        self._sync(temp_db, self._APP_POST, "stopped")
        task = _get_task(temp_db, task_id)
        assert task["status"] == "Running", (
            "Task status must remain Running until RM approves the pending modify"
        )

    def test_post_migration_rm_approve_changes_status(self, temp_db):
        """Post-migration: RM approves the pending modify → task.status changes."""
        task_id = _insert_linked_task(temp_db, app_id=self._APP_POST, status="Running")
        self._sync(temp_db, self._APP_POST, "stopped")

        reqs = _pending_modify_requests(temp_db, task_id)
        req_id = reqs[0]["id"]

        cicd_service.approve_request(
            temp_db, req_id, reviewer="rm", reviewer_role="RM"
        )
        task = _get_task(temp_db, task_id)
        assert task["status"] == "Stopped", (
            "After RM approval of the pending modify, task.status must be Stopped"
        )

    # --- Contrast: the same app in pre- and post-migration state ---

    def test_contrast_pre_vs_post_migration_sync(self, temp_db):
        """Contrast: same app, same decision — pre-mig no-op, post-mig fires.

        This is the definitive proof that migration backfill is the difference.
        """
        _APP_A_PRE = "app_w4_contrast_pre"
        _APP_A_POST = "app_w4_contrast_post"

        # Pre-migration: task app_id=NULL → no-op
        _insert_unlinked_task(temp_db, app_id=_APP_A_PRE, status="Running")
        result_pre = self._sync(temp_db, _APP_A_PRE, "stopped")
        reqs_pre = temp_db.execute(
            "SELECT COUNT(*) FROM cicd_task_requests"
        ).fetchone()[0]

        # Post-migration: task app_id="app_w4_contrast_post" → fires
        task_id_post = _insert_linked_task(temp_db, app_id=_APP_A_POST, status="Running")
        result_post = self._sync(temp_db, _APP_A_POST, "stopped")
        reqs_post = temp_db.execute(
            "SELECT COUNT(*) FROM cicd_task_requests WHERE task_id=?",
            (task_id_post,)
        ).fetchone()[0]

        assert result_pre is None, "Pre-migration sync must return None (no-op)"
        assert result_post is not None, "Post-migration sync must return the created request"
        assert reqs_pre == 0, "Pre-migration: 0 requests created"
        assert reqs_post == 1, "Post-migration: 1 pending modify request created"


# ---------------------------------------------------------------------------
# 5. Full decision→status loop on app_service.update_snapshot (HTTP-level)
# ---------------------------------------------------------------------------


class TestDecisionSyncHttpMigratedApp:
    """Verify update_snapshot endpoint wires cicd_sync on POST /api/apps/update.

    This uses TestClient to drive the full HTTP path.
    """

    def _setup_migrated_app(
        self,
        db_path: Path,
        tmp_dir: Path,
        *,
        app_id: str = "httpapp_w4post",
        status: str = "Running",
    ) -> tuple[str, str]:
        """Seed a release + app row + linked task; return (release_id, app_id)."""
        conn = app_connect(db_path)
        release_id = seed_release(conn, tmp_path=tmp_dir)
        _insert_linked_task(conn, app_id=app_id, status=status)
        conn.commit()
        conn.close()
        return release_id, app_id

    def test_http_decision_change_creates_cicd_sync(self, db_path, tmp_dir):
        """POST /api/apps/update with new release_decision → cicd_sync.created=True."""
        from fastapi.testclient import TestClient

        release_id, app_id = self._setup_migrated_app(db_path, tmp_dir)

        # We need to make sure the app is in the release's snapshots.
        # The task has app_id set; app_service.update_snapshot reads the snapshot.
        # Since we seeded via seed_release (creates TestApp), and we inserted a
        # separate app row, we need to also add the app to the release.
        conn = app_connect(db_path)
        # Get the seeded app_id from the release (TestApp from seed_release)
        from release_system import core
        releases = core.list_releases(conn)
        rel = core.get_release(conn, releases[0]["id"])
        seeded_app_id = list(rel["snapshots"].keys())[0]
        conn.close()

        # Use the seeded app's identity for the linked task
        # Actually, let's create a proper test: add a linked task for the seeded app
        conn = app_connect(db_path)
        ts = beijing_timestamp()
        task_id = cicd_repo.next_cicd_id(conn)
        with transaction(conn):
            cicd_repo.create_task(
                conn,
                task_id=task_id,
                app_id=seeded_app_id,    # ← linked to the seeded app (post-migration)
                app_name="TestApp",
                app_version="1.0",
                repo_type="git",
                repo_name="hpc_testapp",
                branch="maca",
                build_product=["maca"],
                community_artifact=[],
                build_image="hpc/test:latest",
                test_timeout=40,
                owner_username="rm",
                status="Running",
                notes="wave4 http test",
                created_at=ts,
                updated_at=ts,
            )
        conn.close()

        fastapi_app = _make_fastapi_app(db_path, role="RM", username="rm")
        with TestClient(fastapi_app, raise_server_exceptions=False) as client:
            resp = client.post(
                "/api/apps/update",
                json={
                    "release_id": releases[0]["id"],
                    "app_id": seeded_app_id,
                    "snapshot": {"release_decision": "stopped"},
                },
            )
        assert resp.status_code == 200, (
            f"update_snapshot should succeed, got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        assert "cicd_sync" in body, (
            "Response must include 'cicd_sync' when release_decision changes"
        )
        assert body["cicd_sync"]["created"] is True, (
            "cicd_sync.created must be True when linked task exists (post-migration)"
        )
        req = body["cicd_sync"]["request"]
        assert req is not None
        raw_payload = req.get("payload", {})
        payload = json.loads(raw_payload) if isinstance(raw_payload, str) else raw_payload
        assert payload["status"]["new"] == "Stopped"

    def test_http_no_linked_task_created_false(self, db_path, tmp_dir):
        """POST /api/apps/update with no linked task → cicd_sync.created=False."""
        from fastapi.testclient import TestClient

        conn = app_connect(db_path)
        release_id = seed_release(conn, tmp_path=tmp_dir)
        from release_system import core
        releases = core.list_releases(conn)
        rel = core.get_release(conn, releases[0]["id"])
        seeded_app_id = list(rel["snapshots"].keys())[0]
        # Do NOT create any linked task for seeded_app_id
        conn.close()

        fastapi_app = _make_fastapi_app(db_path, role="RM", username="rm")
        with TestClient(fastapi_app, raise_server_exceptions=False) as client:
            resp = client.post(
                "/api/apps/update",
                json={
                    "release_id": releases[0]["id"],
                    "app_id": seeded_app_id,
                    "snapshot": {"release_decision": "stopped"},
                },
            )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert "cicd_sync" in body
        assert body["cicd_sync"]["created"] is False
        assert body["cicd_sync"]["request"] is None


# ---------------------------------------------------------------------------
# 6. 停止只能经 App — status-lock V3 + stop-via-decision-only
# ---------------------------------------------------------------------------


class TestStopOnlyViaApp:
    """Verify that CICD status can ONLY change via App decision (Ruling A/D/V3).

    Direct CICD modify with 'status' field → rejected (status-lock V3).
    Stopping happens ONLY via App.update_snapshot(stopped) → pending sync.
    Abandon requires Stopped state first (must go through decision, not direct).
    """

    def _create_running_task(self, conn) -> str:
        """Create a linked Running task; return task_id."""
        return _insert_linked_task(
            conn,
            app_id="stoptest_app",
            status="Running",
        )

    def test_cicd_modify_with_status_rejected(self, temp_db):
        """CICD workbench modify with 'status' in payload → RuntimeError (status-lock V3)."""
        task_id = self._create_running_task(temp_db)
        with pytest.raises(RuntimeError, match="运行状态|status"):
            cicd_service.submit_request(
                temp_db,
                task_id=task_id,
                request_type="modify",
                payload={"status": {"old": "Running", "new": "Stopped"}},
                submitter="rm",
                submitter_role="RM",
            )
        # Task must remain Running
        assert _get_task(temp_db, task_id)["status"] == "Running"

    def test_cicd_modify_status_plus_other_field_rejected(self, temp_db):
        """CICD modify with 'status' AND another field → still rejected."""
        task_id = self._create_running_task(temp_db)
        with pytest.raises(RuntimeError):
            cicd_service.submit_request(
                temp_db,
                task_id=task_id,
                request_type="modify",
                payload={
                    "status": {"old": "Running", "new": "Stopped"},
                    "notes": {"old": "", "new": "smuggling status"},
                },
                submitter="rm",
                submitter_role="RM",
            )

    def test_cicd_workbench_modify_without_status_rejected(self, temp_db):
        """CICD workbench modify WITHOUT 'status' is still rejected; use App-CICD tab."""
        task_id = self._create_running_task(temp_db)
        with pytest.raises(RuntimeError, match="App 工作台"):
            cicd_service.submit_request(
                temp_db,
                task_id=task_id,
                request_type="modify",
                payload={"notes": {"old": "old", "new": "new notes"}},
                submitter="rm",
                submitter_role="RM",
            )

    def test_stop_via_decision_creates_pending_sync(self, temp_db):
        """App decision change (stopped) → pending sync request (Ruling D)."""
        task_id = self._create_running_task(temp_db)
        with transaction(temp_db):
            result = cicd_service.sync_decision_to_cicd(
                temp_db, "stoptest_app", "stopped", submitter="rm"
            )
        assert result is not None, "sync must create a pending modify request"
        assert result["status"] == "pending"
        assert result["request_type"] == "modify"

    def test_cicd_abandon_service_removed(self):
        """CICD retire/delete is handled through App operations, not CICD service APIs."""
        assert not hasattr(cicd_service, "abandon_task")

    def test_full_stop_flow_ends_at_stopped(self, temp_db):
        """Full flow: Running → sync(stopped) → RM approve → Stopped."""
        task_id = _insert_linked_task(
            temp_db, app_id="full_stop_flow_app", status="Running"
        )

        # Step 1: Decision change → pending sync request
        with transaction(temp_db):
            sync_result = cicd_service.sync_decision_to_cicd(
                temp_db, "full_stop_flow_app", "stopped", submitter="rm"
            )
        assert sync_result is not None
        req_id = sync_result["id"]

        # Step 2: RM approve → task becomes Stopped
        cicd_service.approve_request(
            temp_db, req_id, reviewer="rm", reviewer_role="RM"
        )
        assert _get_task(temp_db, task_id)["status"] == "Stopped"


# ---------------------------------------------------------------------------
# 7. 权限验证 (Ruling C — Admin out of CICD, RM sole approver)
# ---------------------------------------------------------------------------


class TestPermissionsWave4:
    """Verify role permissions for all Wave-4 CICD actions.

    Plan §3.7 (Ruling C): Admin out of CICD; CICD_CREATE_ROLES={Owner,RM};
    CICD_APPROVER_ROLES={RM}.  RM may self-approve (is_self_approved=1).
    SPD: delivery chain only.
    """

    _CREATE_PAYLOAD = {
        "app_name": "PermTestApp",
        "app_version": "1.0",
        "repo_type": "git",
        "repo_name": "hpc_permtest",
        "branch": "main",
        "build_product": ["maca"],
        "community_artifact": [],
        "build_image": "hpc/permtest:latest",
        "test_timeout": 40,
        "owner_username": "owner_test",
        "notes": "perm test",
    }

    def _submit_create(self, conn, role: str, username: str) -> dict:
        return cicd_service.submit_request(
            conn,
            task_id=None,
            request_type="create",
            payload=self._CREATE_PAYLOAD,
            submitter=username,
            submitter_role=role,
        )

    # --- CICD create: Owner + RM allowed; Admin/SPD/Guest forbidden ---

    def test_admin_cannot_submit_create(self, temp_db):
        """Admin cannot submit CICD create requests (Ruling C)."""
        with pytest.raises((PermissionError, RuntimeError)):
            self._submit_create(temp_db, role="Admin", username="admin")

    def test_spd_cannot_submit_create(self, temp_db):
        """SPD cannot submit CICD create requests."""
        with pytest.raises((PermissionError, RuntimeError)):
            self._submit_create(temp_db, role="SPD", username="spd_user")

    def test_guest_cannot_submit_create(self, temp_db):
        """Guest cannot submit CICD create requests."""
        with pytest.raises((PermissionError, RuntimeError)):
            self._submit_create(temp_db, role="Guest", username="guest")

    def test_owner_can_submit_create(self, temp_db):
        """Owner can submit CICD create requests (CICD_CREATE_ROLES)."""
        result = self._submit_create(temp_db, role="Owner", username="owner_test")
        assert result["status"] == "pending"

    def test_rm_can_submit_create(self, temp_db):
        """RM can submit CICD create requests."""
        result = self._submit_create(temp_db, role="RM", username="rm")
        assert result["status"] == "pending"

    # --- CICD approve: RM only ---

    def test_admin_cannot_approve(self, temp_db):
        """Admin cannot approve CICD requests (Ruling C — CICD_APPROVER_ROLES={RM})."""
        req = self._submit_create(temp_db, role="RM", username="rm")
        req_id = req["id"]
        with pytest.raises(PermissionError, match="RM"):
            cicd_service.approve_request(
                temp_db, req_id, reviewer="admin", reviewer_role="Admin"
            )

    def test_owner_cannot_approve(self, temp_db):
        """Owner cannot approve CICD requests."""
        req = self._submit_create(temp_db, role="RM", username="rm")
        req_id = req["id"]
        with pytest.raises(PermissionError, match="RM"):
            cicd_service.approve_request(
                temp_db, req_id, reviewer="owner_test", reviewer_role="Owner"
            )

    def test_rm_can_approve(self, temp_db):
        """RM can approve CICD requests."""
        req = self._submit_create(temp_db, role="RM", username="rm")
        approved = cicd_service.approve_request(
            temp_db, req["id"], reviewer="rm", reviewer_role="RM"
        )
        assert approved["status"] == "approved"

    def test_rm_self_approve_is_allowed(self, temp_db):
        """RM can approve their own request (self-approve allowed, flagged in audit)."""
        req = self._submit_create(temp_db, role="RM", username="rm")
        approved = cicd_service.approve_request(
            temp_db, req["id"],
            reviewer="rm",         # same user who submitted
            reviewer_role="RM",
        )
        assert approved["status"] == "approved"
        assert approved["is_self_approved"] == 1

    def test_rm_self_approve_different_rm_is_zero(self, temp_db):
        """Different RM approver → is_self_approved=0 (not flagged)."""
        req = self._submit_create(temp_db, role="RM", username="rm_submitter")
        approved = cicd_service.approve_request(
            temp_db, req["id"],
            reviewer="rm_approver",  # different RM user
            reviewer_role="RM",
        )
        assert approved["is_self_approved"] == 0

    # --- HTTP-level: Admin gets 403 on CICD endpoints ---

    def test_http_admin_403_on_submit(self, db_path):
        """HTTP: Admin → 403 on POST /api/cicd/requests/submit."""
        from fastapi.testclient import TestClient
        app = _make_fastapi_app(db_path, role="Admin", username="admin")
        with TestClient(app, raise_server_exceptions=False) as client:
            resp = client.post(
                "/api/cicd/requests/submit",
                json={"task_id": None, "request_type": "create", "payload": {}},
            )
        assert resp.status_code == 403, (
            f"Admin must get 403 on CICD submit, got {resp.status_code}"
        )

    def test_http_admin_403_on_apps_new(self, db_path):
        """HTTP: Admin → 403 on POST /api/cicd/apps/new (CICD-first)."""
        from fastapi.testclient import TestClient
        app = _make_fastapi_app(db_path, role="Admin", username="admin")
        with TestClient(app, raise_server_exceptions=False) as client:
            resp = client.post(
                "/api/cicd/apps/new",
                json={"official_name": "TestApp", "repo_type": "git",
                      "repo_name": "hpc_test", "branch": "main"},
            )
        assert resp.status_code == 403, (
            f"Admin must get 403 on CICD apps/new, got {resp.status_code}"
        )

    def test_http_admin_403_on_fetch_preview(self, db_path):
        """HTTP: Admin → 403 on POST /api/cicd/apps/fetch-preview."""
        from fastapi.testclient import TestClient
        app = _make_fastapi_app(db_path, role="Admin", username="admin")
        with TestClient(app, raise_server_exceptions=False) as client:
            resp = client.post(
                "/api/cicd/apps/fetch-preview",
                json={"repo_type": "git", "repo_name": "hpc_test", "branch": "main"},
            )
        assert resp.status_code == 403

    def test_http_abandon_endpoint_removed(self, db_path):
        """HTTP: POST /api/cicd/tasks/abandon is removed."""
        from fastapi.testclient import TestClient
        app = _make_fastapi_app(db_path, role="RM", username="rm")
        with TestClient(app, raise_server_exceptions=False) as client:
            resp = client.post(
                "/api/cicd/tasks/abandon",
                json={"task_id": "CICD-9999"},
            )
        assert resp.status_code == 405

    def test_http_spd_403_on_submit(self, db_path):
        """HTTP: SPD → 403 on POST /api/cicd/requests/submit."""
        from fastapi.testclient import TestClient
        app = _make_fastapi_app(db_path, role="SPD", username="spd_user")
        with TestClient(app, raise_server_exceptions=False) as client:
            resp = client.post(
                "/api/cicd/requests/submit",
                json={"task_id": None, "request_type": "create", "payload": {}},
            )
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# 8. 时区: timestamps naive Beijing — no UTC offset, no double offset
# ---------------------------------------------------------------------------


class TestTimezoneWave4:
    """Verify all timestamps in the new system are naive Beijing (no +00:00).

    Plan §5.4 DA BLOCKER C1: DB stores naive Beijing, backend down-sends as-is,
    frontend zero-offset display → no double offset.
    """

    def test_beijing_timestamp_is_naive_beijing(self):
        """beijing_timestamp() returns 'YYYY-MM-DD HH:MM:SS' — no UTC offset."""
        ts = beijing_timestamp()
        assert "+" not in ts, f"beijing_timestamp must not contain '+': {ts!r}"
        assert "Z" not in ts, f"beijing_timestamp must not contain 'Z': {ts!r}"
        assert "T" not in ts, (
            f"beijing_timestamp should use space separator, not T: {ts!r}"
        )
        # Validate format: YYYY-MM-DD HH:MM:SS
        import re
        assert re.match(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$", ts), (
            f"Expected 'YYYY-MM-DD HH:MM:SS' format, got: {ts!r}"
        )

    def test_task_created_at_naive_beijing(self, temp_db):
        """cicd_tasks.created_at set by service is naive Beijing."""
        task_id = _insert_linked_task(temp_db, app_id="tz_test_app", status="Running")
        task = _get_task(temp_db, task_id)
        ts = task["created_at"]
        assert "+" not in ts, f"created_at must be naive Beijing, got: {ts!r}"
        assert "Z" not in ts

    def test_request_submitted_at_naive_beijing(self, temp_db):
        """cicd_task_requests.submitted_at is naive Beijing."""
        task_id = _insert_linked_task(temp_db, app_id="tz_req_app", status="Running")
        with transaction(temp_db):
            cicd_service.sync_decision_to_cicd(
                temp_db, "tz_req_app", "stopped", submitter="rm"
            )
        reqs = _pending_modify_requests(temp_db, task_id)
        ts = reqs[0]["submitted_at"]
        assert "+" not in ts, f"submitted_at must be naive Beijing, got: {ts!r}"
        assert "Z" not in ts

    def test_approved_request_reviewed_at_naive_beijing(self, temp_db):
        """cicd_task_requests.reviewed_at is naive Beijing after approval."""
        task_id = _insert_linked_task(temp_db, app_id="tz_approve_app", status="Running")
        with transaction(temp_db):
            sync_req = cicd_service.sync_decision_to_cicd(
                temp_db, "tz_approve_app", "stopped", submitter="rm"
            )
        approved = cicd_service.approve_request(
            temp_db, sync_req["id"], reviewer="rm", reviewer_role="RM"
        )
        ts = approved["reviewed_at"]
        assert ts, "reviewed_at should be set after approval"
        assert "+" not in ts, f"reviewed_at must be naive Beijing, got: {ts!r}"
        assert "Z" not in ts

# ---------------------------------------------------------------------------
# 9. Migrated DB snapshot — verify R3 chain with synthetic migrated data
# ---------------------------------------------------------------------------


class TestMigratedDbSnapshot:
    """Simulate a migrated DB snapshot and drive the full R3 chain.

    We build a synthetic 'migrated' DB with:
      - One release (from seed_release)
      - One app from the release (seeded app: TestApp)
      - One cicd_task linked via app_id to that app (post-migration backfill)

    Then drive:
      (a) Change App decision: stopped → pending sync request
      (b) RM approve → task.status becomes Stopped
      (c) App decision: cicd_only → pending sync request (Running)
      (d) RM approve → task.status becomes Running
      (e) Abandon only via RM on Stopped (not directly on Running)
    """

    def _setup_migrated_snapshot(self, conn, tmp_dir) -> tuple[str, str, str]:
        """Seed release + link a task to the seeded app.

        Returns (release_id, app_id, task_id).
        """
        from release_system import core

        release_id = seed_release(conn, tmp_path=tmp_dir)
        releases = core.list_releases(conn)
        rel = core.get_release(conn, releases[0]["id"])
        app_id = list(rel["snapshots"].keys())[0]

        # Create a linked task (post-migration state)
        ts = beijing_timestamp()
        task_id = cicd_repo.next_cicd_id(conn)
        with transaction(conn):
            cicd_repo.create_task(
                conn,
                task_id=task_id,
                app_id=app_id,    # ← backfilled (post-migration)
                app_name="TestApp",
                app_version="1.0",
                repo_type="git",
                repo_name="hpc_testapp",
                branch="maca",
                build_product=["maca"],
                community_artifact=[],
                build_image="hpc/testapp:latest",
                test_timeout=40,
                owner_username="rm",
                status="Running",
                notes="migrated snapshot test",
                created_at=ts,
                updated_at=ts,
            )
        conn.commit()
        return release_id, app_id, task_id

    def test_full_r3_chain_stop_and_resume(self, temp_db, tmp_dir):
        """Full chain: Running → stop (App decision) → RM approve → Stopped
        → resume (App decision) → RM approve → Running."""
        release_id, app_id, task_id = self._setup_migrated_snapshot(temp_db, tmp_dir)

        # Step 1: Change decision to 'stopped' → sync fires
        with transaction(temp_db):
            sync_req1 = cicd_service.sync_decision_to_cicd(
                temp_db, app_id, "stopped", submitter="rm"
            )
        assert sync_req1 is not None, "Sync must fire on migrated app (app_id set)"
        reqs = _pending_modify_requests(temp_db, task_id)
        assert len(reqs) == 1
        assert reqs[0]["_payload"]["status"]["new"] == "Stopped"

        # Step 2: RM approve → task becomes Stopped
        cicd_service.approve_request(
            temp_db, sync_req1["id"], reviewer="rm", reviewer_role="RM"
        )
        assert _get_task(temp_db, task_id)["status"] == "Stopped"

        # Step 3: Change decision to 'cicd_only' → sync fires (Running)
        with transaction(temp_db):
            sync_req2 = cicd_service.sync_decision_to_cicd(
                temp_db, app_id, "cicd_only", submitter="rm"
            )
        assert sync_req2 is not None
        reqs2 = _pending_modify_requests(temp_db, task_id)
        assert len(reqs2) == 1  # new pending (prev approved)
        assert reqs2[0]["_payload"]["status"]["new"] == "Running"

        # Step 4: RM approve → task becomes Running
        cicd_service.approve_request(
            temp_db, sync_req2["id"], reviewer="rm", reviewer_role="RM"
        )
        assert _get_task(temp_db, task_id)["status"] == "Running"

    def test_decision_sync_idempotent_on_same_status(self, temp_db, tmp_dir):
        """No-op when task is already at the target status."""
        release_id, app_id, task_id = self._setup_migrated_snapshot(temp_db, tmp_dir)
        # Task is Running; decision=release → target=Running → no-op
        with transaction(temp_db):
            result = cicd_service.sync_decision_to_cicd(
                temp_db, app_id, "release", submitter="rm"
            )
        assert result is None, "release→Running should be no-op when task is already Running"

    def test_only_one_pending_sync_request_at_a_time(self, temp_db, tmp_dir):
        """Idempotent guard: second sync while first pending → no duplicate."""
        release_id, app_id, task_id = self._setup_migrated_snapshot(temp_db, tmp_dir)

        with transaction(temp_db):
            r1 = cicd_service.sync_decision_to_cicd(
                temp_db, app_id, "stopped", submitter="rm"
            )
        assert r1 is not None

        # Second sync (same decision or different) → blocked by pending modify on 'status'
        with transaction(temp_db):
            r2 = cicd_service.sync_decision_to_cicd(
                temp_db, app_id, "stopped", submitter="rm"
            )
        assert r2 is None, "Second sync must be no-op (pending modify on 'status' exists)"

        total = temp_db.execute(
            "SELECT COUNT(*) FROM cicd_task_requests WHERE task_id=?", (task_id,)
        ).fetchone()[0]
        assert total == 1, "Still exactly one pending request"

    def test_r2_no_polling_flag(self):
        """R2 verification: there is no polling configured in the service layer.

        The service functions are purely request/response — no background threads,
        no timers, no auto-refresh.  This is a design invariant test.
        """
        import inspect

        # Check that cicd_service has no threading.Thread usage (no background polling)
        source = inspect.getsource(cicd_service)
        assert "threading.Timer" not in source, (
            "cicd_service must not use threading.Timer (R2: no polling)"
        )
        assert "schedule.every" not in source, (
            "cicd_service must not use schedule.every (R2: no polling)"
        )
        # Note: the QA service uses threads (permitted), but CICD must not.


# ---------------------------------------------------------------------------
# 10. Actual migrated DB — real data spot-checks
# ---------------------------------------------------------------------------

_MIGRATED_DB_PATH = "/tmp/release_system.migrated.db"
_migrated_db_exists = pytest.mark.skipif(
    not Path(_MIGRATED_DB_PATH).exists(),
    reason=f"Migrated candidate DB not found at {_MIGRATED_DB_PATH}; run Wave-4 migration first",
)


class TestMigratedDbActual:
    """Spot-check the actual migrated candidate DB (impl-1's output).

    These tests open a READ-ONLY copy of the migrated DB and verify:
      1. app_id is backfilled on tasks (111 linked, 11 orphan)
      2. Timestamps are naive Beijing (no +00:00)
      3. sync_decision_to_cicd fires on a real linked app (but we run in a
         fresh in-memory copy so no writes to the candidate DB)
      4. PRAGMA foreign_key_check passes
      5. cicd_tasks count = src + 16 derived (122 total)
    """

    def _open_copy(self) -> sqlite3.Connection:
        """Open an in-memory copy of the migrated DB for safe writes."""
        import shutil, tempfile
        tmp = Path(tempfile.mkdtemp())
        copy_path = tmp / "migrated_copy.db"
        shutil.copy(_MIGRATED_DB_PATH, copy_path)
        # Reset init guard so connect() can open the existing file
        from app.db.connection import reset_init_state
        reset_init_state()
        conn = app_connect(copy_path)
        return conn

    @_migrated_db_exists
    def test_linked_task_count(self):
        """Migration: 111 apps, all with linked tasks (111 linked)."""
        import sqlite3 as _sqlite3
        conn = _sqlite3.connect(_MIGRATED_DB_PATH)
        conn.row_factory = _sqlite3.Row
        linked = conn.execute(
            "SELECT COUNT(*) FROM cicd_tasks WHERE app_id IS NOT NULL"
        ).fetchone()[0]
        total_apps = conn.execute("SELECT COUNT(*) FROM apps").fetchone()[0]
        conn.close()
        assert linked == total_apps, (
            f"Every app should have a linked task: linked={linked}, apps={total_apps}"
        )

    @_migrated_db_exists
    def test_total_task_count(self):
        """Migration: 106 original + 16 derived = 122 cicd_tasks total."""
        import sqlite3 as _sqlite3
        conn = _sqlite3.connect(_MIGRATED_DB_PATH)
        conn.row_factory = _sqlite3.Row
        total = conn.execute("SELECT COUNT(*) FROM cicd_tasks").fetchone()[0]
        conn.close()
        assert total == 122, f"Expected 122 total tasks (106 src + 16 derived), got {total}"

    @_migrated_db_exists
    def test_orphan_task_count(self):
        """Migration: 11 tasks without app_id (8 manifest + 2 true orphans + 1 neuralgcm)."""
        import sqlite3 as _sqlite3
        conn = _sqlite3.connect(_MIGRATED_DB_PATH)
        conn.row_factory = _sqlite3.Row
        unlinked = conn.execute(
            "SELECT COUNT(*) FROM cicd_tasks WHERE app_id IS NULL"
        ).fetchone()[0]
        conn.close()
        assert unlinked == 11, f"Expected 11 orphan tasks, got {unlinked}"

    @_migrated_db_exists
    def test_no_utc_offset_in_task_timestamps(self):
        """All cicd_tasks timestamps are naive Beijing (no +00:00 or Z)."""
        import sqlite3 as _sqlite3
        conn = _sqlite3.connect(_MIGRATED_DB_PATH)
        conn.row_factory = _sqlite3.Row
        rows = conn.execute("SELECT id, created_at, updated_at FROM cicd_tasks").fetchall()
        conn.close()
        for row in rows:
            for col in ("created_at", "updated_at"):
                ts = row[col] or ""
                assert "+" not in ts, (
                    f"task {row['id']}.{col} has UTC offset: {ts!r}"
                )
                assert "Z" not in ts, (
                    f"task {row['id']}.{col} has Z suffix: {ts!r}"
                )

    @_migrated_db_exists
    def test_foreign_key_check_empty(self):
        """PRAGMA foreign_key_check returns empty → no FK violations."""
        import sqlite3 as _sqlite3
        conn = _sqlite3.connect(_MIGRATED_DB_PATH)
        conn.row_factory = _sqlite3.Row
        violations = conn.execute("PRAGMA foreign_key_check").fetchall()
        conn.close()
        assert not violations, (
            f"FK violations in migrated DB: {[dict(v) for v in violations]}"
        )

    @_migrated_db_exists
    def test_decision_sync_fires_on_real_linked_app(self):
        """On a real linked app from the migrated DB, sync creates a pending modify.

        Uses an in-memory copy so no writes land on the candidate DB.
        """
        conn = self._open_copy()
        # Find any Running linked task
        row = conn.execute(
            "SELECT id, app_id, status FROM cicd_tasks "
            "WHERE app_id IS NOT NULL AND status='Running' LIMIT 1"
        ).fetchone()
        if row is None:
            conn.close()
            pytest.skip("No Running linked task in migrated DB (unusual)")
        task_id = row["id"]
        app_id = row["app_id"]

        with transaction(conn):
            result = cicd_service.sync_decision_to_cicd(
                conn, app_id, "stopped", submitter="rm"
            )

        assert result is not None, (
            f"sync_decision_to_cicd must fire on migrated app {app_id} "
            f"(task {task_id}), but returned None"
        )
        reqs = _pending_modify_requests(conn, task_id)
        assert len(reqs) == 1
        assert reqs[0]["_payload"]["status"]["new"] == "Stopped"
        assert reqs[0]["_payload"]["status"]["old"] == "Running"
        conn.close()

    @_migrated_db_exists
    def test_d1_overwrites_applied(self):
        """D-1 overwrites: 6 stopped-app tasks corrected to Stopped status."""
        import sqlite3 as _sqlite3
        # The 6 D-1 apps: amber, moflow, seg-utils, tfhe-rs, uni-fold, warp
        _D1_APPS = {"amber", "moflow", "seg-utils", "tfhe-rs", "uni-fold", "warp"}
        conn = _sqlite3.connect(_MIGRATED_DB_PATH)
        conn.row_factory = _sqlite3.Row
        rows = conn.execute(
            "SELECT id, app_id, status FROM cicd_tasks WHERE app_id IN ({})".format(
                ",".join("?" * len(_D1_APPS))
            ),
            list(_D1_APPS),
        ).fetchall()
        conn.close()
        for row in rows:
            assert row["status"] == "Stopped", (
                f"D-1 app {row['app_id']}: expected Stopped, got {row['status']!r}"
            )
