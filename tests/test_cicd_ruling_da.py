"""Phase-4 Wave-2 tests — Ruling D (decision→CICD status), Ruling A (abandon), status-lock V3.

Ruling D (plan §3.5 b):
  sync_decision_to_cicd creates ONE pending modify request with correct mapping
  (release/cicd_only→Running, stopped→Stopped) and origin="release_decision_sync".
  Idempotent: no-op when target matches current status or a pending modify on
  'status' already exists.  Wired into app_service.update_snapshot — response
  carries cicd_sync:{created, request}.

Ruling A (plan §3.5 c):
  abandon_task: RM-only direct action (no pending queue), Stopped→Abandoned (terminal).
  Non-Stopped tasks and non-RM reviewer_role are rejected.
  Creates an approved audit record (mirrors transfer_owner pattern).

Status-lock V3 (plan §3.5, §6 V3):
  submit_request with request_type='modify' and 'status' in payload → RuntimeError.
  Only sync_decision_to_cicd and abandon_task may write task status.

Golden notes:
  post_apps_update_decision already carries cicd_sync (parity golden: no linked task
  → created=false).  New goldens added this wave: post_cicd_abandon_admin (403),
  post_cicd_abandon_rm_running (RM on Running CICD-0001 → 400).
"""
from __future__ import annotations

import json

import pytest

from app.db.connection import transaction
from app.repositories import cicd_repo
from app.services import cicd_service
from app.timeutil import beijing_timestamp


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_APP_A = "app_wave2_da_aaa"   # app with a linked Running task
_APP_B = "app_wave2_da_bbb"   # app with a linked Stopped task
_APP_NONE = "app_wave2_da_no_task"  # app with NO linked task


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ensure_app_row(conn, app_id: str) -> None:
    """Insert a minimal row into apps to satisfy the FK constraint on cicd_tasks.app_id.

    The apps table has defaults for all non-PK columns, so just inserting the id works.
    """
    with transaction(conn):
        conn.execute(
            "INSERT OR IGNORE INTO apps (id) VALUES (?)", (app_id,)
        )


def _create_task(
    conn,
    *,
    app_id: str | None = None,
    status: str = "Running",
    task_name: str = "Wave2DATask",
) -> str:
    """Create and commit a cicd_task row; return task_id.

    If app_id is provided, inserts a minimal apps row first (FK requirement).
    """
    if app_id is not None:
        _ensure_app_row(conn, app_id)
    ts = beijing_timestamp()
    task_id = cicd_repo.next_cicd_id(conn)
    with transaction(conn):
        cicd_repo.create_task(
            conn,
            task_id=task_id,
            app_id=app_id,
            app_name=task_name,
            app_version="1.0",
            repo_type="git",
            repo_name="ssh://gerrit/PDE/HPC/hpc_wave2_da",
            branch="main",
            build_product=["maca"],
            community_artifact=[],
            build_image="hpc/wave2-da:latest",
            test_timeout=40,
            owner_username="rm",
            status=status,
            notes="ruling DA wave2 test",
            created_at=ts,
            updated_at=ts,
        )
    return task_id


def _get_task(conn, task_id: str) -> dict | None:
    return cicd_repo.get_task(conn, task_id)


def _all_requests_for_task(conn, task_id: str) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM cicd_task_requests WHERE task_id = ? ORDER BY id",
        (task_id,),
    ).fetchall()
    result = []
    for r in rows:
        d = dict(r)
        d["_payload"] = json.loads(d.get("payload") or "{}")
        result.append(d)
    return result


def _pending_modify_requests(conn, task_id: str) -> list[dict]:
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


def _sync(conn, app_id: str, decision: str, submitter: str = "rm") -> dict | None:
    """Call sync_decision_to_cicd inside its own transaction; return result."""
    with transaction(conn):
        return cicd_service.sync_decision_to_cicd(
            conn, app_id, decision, submitter=submitter
        )


# ---------------------------------------------------------------------------
# Ruling D — decision mapping
# ---------------------------------------------------------------------------


class TestRulingDDecisionMapping:
    """sync_decision_to_cicd: release_decision → CICD status mapping."""

    def test_release_maps_to_running(self, temp_db):
        """release → target Running; creates a pending modify request."""
        task_id = _create_task(temp_db, app_id=_APP_A, status="Stopped")
        result = _sync(temp_db, _APP_A, "release")
        assert result is not None
        reqs = _pending_modify_requests(temp_db, task_id)
        assert len(reqs) == 1
        assert reqs[0]["_payload"]["status"]["new"] == "Running"
        assert reqs[0]["_payload"]["status"]["old"] == "Stopped"

    def test_cicd_only_maps_to_running(self, temp_db):
        """cicd_only → target Running (same as release)."""
        task_id = _create_task(temp_db, app_id=_APP_A, status="Stopped")
        result = _sync(temp_db, _APP_A, "cicd_only")
        assert result is not None
        reqs = _pending_modify_requests(temp_db, task_id)
        assert len(reqs) == 1
        assert reqs[0]["_payload"]["status"]["new"] == "Running"

    def test_stopped_maps_to_stopped(self, temp_db):
        """stopped → target Stopped; records old=Running."""
        task_id = _create_task(temp_db, app_id=_APP_A, status="Running")
        result = _sync(temp_db, _APP_A, "stopped")
        assert result is not None
        reqs = _pending_modify_requests(temp_db, task_id)
        assert len(reqs) == 1
        assert reqs[0]["_payload"]["status"]["new"] == "Stopped"
        assert reqs[0]["_payload"]["status"]["old"] == "Running"


# ---------------------------------------------------------------------------
# Ruling D — request properties
# ---------------------------------------------------------------------------


class TestRulingDRequestProperties:
    """sync_decision_to_cicd: verify created request has correct envelope fields."""

    def test_request_status_is_pending(self, temp_db):
        """Created request: status = 'pending'."""
        task_id = _create_task(temp_db, app_id=_APP_A, status="Running")
        _sync(temp_db, _APP_A, "stopped")
        rows = _all_requests_for_task(temp_db, task_id)
        assert len(rows) == 1
        assert rows[0]["status"] == "pending"

    def test_request_type_is_modify(self, temp_db):
        """Created request: request_type = 'modify'."""
        task_id = _create_task(temp_db, app_id=_APP_A, status="Running")
        _sync(temp_db, _APP_A, "stopped")
        rows = _all_requests_for_task(temp_db, task_id)
        assert rows[0]["request_type"] == "modify"

    def test_request_origin_is_release_decision_sync(self, temp_db):
        """origin column = 'release_decision_sync' (audit trail). F3: now ALSO exposed in API return."""
        task_id = _create_task(temp_db, app_id=_APP_A, status="Running")
        _sync(temp_db, _APP_A, "stopped")
        row = temp_db.execute(
            "SELECT origin FROM cicd_task_requests WHERE task_id=?", (task_id,)
        ).fetchone()
        assert row["origin"] == "release_decision_sync"

    def test_request_submitter_matches_actor(self, temp_db):
        """submitter = the actor passed to sync_decision_to_cicd."""
        task_id = _create_task(temp_db, app_id=_APP_A, status="Running")
        _sync(temp_db, _APP_A, "stopped", submitter="actor_rm")
        row = temp_db.execute(
            "SELECT submitter FROM cicd_task_requests WHERE task_id=?", (task_id,)
        ).fetchone()
        assert row["submitter"] == "actor_rm"

    def test_return_value_is_request_dict(self, temp_db):
        """Non-None return value is the created pending request dict, incl. origin (F3)."""
        _create_task(temp_db, app_id=_APP_A, status="Running")
        result = _sync(temp_db, _APP_A, "stopped")
        assert isinstance(result, dict)
        assert result.get("status") == "pending"
        assert result.get("request_type") == "modify"
        # F3: origin is no longer stripped — the sync request self-identifies.
        assert result.get("origin") == "release_decision_sync"

    def test_exactly_one_request_created(self, temp_db):
        """ONE modify request per sync call — no duplicates (plan §3.5 b+)."""
        task_id = _create_task(temp_db, app_id=_APP_A, status="Running")
        _sync(temp_db, _APP_A, "stopped")
        total = temp_db.execute(
            "SELECT COUNT(*) FROM cicd_task_requests WHERE task_id=?", (task_id,)
        ).fetchone()[0]
        assert total == 1


# ---------------------------------------------------------------------------
# F3 — origin exposed through the /api/cicd/requests read path
# ---------------------------------------------------------------------------


class TestRulingDOriginThroughApi:
    """F3: cicd_task_requests.origin flows through list_requests (what
    GET /api/cicd/requests returns), distinguishing decision-sync requests
    from build-config (workbench) requests."""

    def test_sync_request_carries_origin_through_list_requests(self, temp_db):
        """A decision-sync request exposes origin='release_decision_sync' via the API read path."""
        task_id = _create_task(temp_db, app_id=_APP_A, status="Running")
        _sync(temp_db, _APP_A, "stopped")
        # list_requests is exactly what GET /api/cicd/requests serializes.
        reqs = cicd_service.list_requests(temp_db, role="RM", task_id=task_id)
        sync_reqs = [r for r in reqs if r.get("request_type") == "modify"]
        assert len(sync_reqs) == 1
        assert sync_reqs[0]["origin"] == "release_decision_sync"

    def test_workbench_request_reads_back_cicd_workbench(self, temp_db):
        """Contrast: a user-submitted build-config request exposes origin='cicd_workbench'."""
        task_id = _create_task(temp_db, app_id=_APP_A, status="Running")
        with transaction(temp_db):
            cicd_service.submit_request(
                temp_db,
                task_id=task_id,
                request_type="modify",
                payload={"notes": "owner tweak"},
                submitter="owner_test",
                submitter_role="Owner",
            )
        reqs = cicd_service.list_requests(temp_db, role="RM", task_id=task_id)
        assert reqs, "expected at least one request"
        assert all("origin" in r for r in reqs), "every request object must expose origin"
        workbench = [r for r in reqs if r.get("submitter") == "owner_test"]
        assert len(workbench) == 1
        assert workbench[0]["origin"] == "cicd_workbench"


def conn_execute_list(conn, sql: str) -> list[dict]:
    return [dict(r) for r in conn.execute(sql).fetchall()]


# ---------------------------------------------------------------------------
# Ruling D — idempotency
# ---------------------------------------------------------------------------


class TestRulingDIdempotency:
    """sync_decision_to_cicd: no-op in all idempotent cases."""

    def test_noop_running_to_release(self, temp_db):
        """No-op: task Running, decision=release → target Running == current."""
        _create_task(temp_db, app_id=_APP_A, status="Running")
        result = _sync(temp_db, _APP_A, "release")
        assert result is None
        assert temp_db.execute(
            "SELECT COUNT(*) FROM cicd_task_requests"
        ).fetchone()[0] == 0

    def test_noop_running_to_cicd_only(self, temp_db):
        """No-op: task Running, decision=cicd_only → target Running == current."""
        _create_task(temp_db, app_id=_APP_A, status="Running")
        result = _sync(temp_db, _APP_A, "cicd_only")
        assert result is None

    def test_noop_stopped_to_stopped(self, temp_db):
        """No-op: task Stopped, decision=stopped → target Stopped == current."""
        _create_task(temp_db, app_id=_APP_A, status="Stopped")
        result = _sync(temp_db, _APP_A, "stopped")
        assert result is None

    def test_noop_when_pending_modify_on_status_exists(self, temp_db):
        """No-op: pending modify touching 'status' already present — no duplicate."""
        task_id = _create_task(temp_db, app_id=_APP_A, status="Running")
        # First sync creates a pending modify
        r1 = _sync(temp_db, _APP_A, "stopped")
        assert r1 is not None
        # Second sync with same decision → existing pending guards → no-op
        r2 = _sync(temp_db, _APP_A, "stopped")
        assert r2 is None
        # Still exactly ONE request
        reqs = _pending_modify_requests(temp_db, task_id)
        assert len(reqs) == 1

    def test_app_id_request_when_no_linked_task(self, temp_db):
        """App-backed CICD: no linked legacy task still creates an app-id request."""
        result = _sync(temp_db, _APP_NONE, "stopped")
        assert result is not None
        assert result["task_id"] == _APP_NONE
        assert temp_db.execute(
            "SELECT COUNT(*) FROM cicd_task_requests WHERE task_id=?",
            (_APP_NONE,),
        ).fetchone()[0] == 1

    def test_task_status_unchanged_after_sync(self, temp_db):
        """Sync creates PENDING request — task status not yet changed (awaits approval)."""
        task_id = _create_task(temp_db, app_id=_APP_A, status="Running")
        _sync(temp_db, _APP_A, "stopped")
        task = _get_task(temp_db, task_id)
        assert task["status"] == "Running"  # still Running; not applied yet

    def test_second_different_decision_blocked_by_pending(self, temp_db):
        """Even a different decision is blocked when a pending modify on 'status' exists."""
        task_id = _create_task(temp_db, app_id=_APP_A, status="Running")
        _sync(temp_db, _APP_A, "stopped")   # creates pending: Running → Stopped
        # Now try opposite direction — still blocked by the existing pending
        r2 = _sync(temp_db, _APP_A, "release")   # Running → Running (idempotent guard)
        # The existing pending is for status, so the guard fires regardless of direction
        # (or returns None because Running == Running since task is still Running)
        # Either way, the second call is a no-op
        reqs = _pending_modify_requests(temp_db, task_id)
        assert len(reqs) == 1  # still only one


# ---------------------------------------------------------------------------
# Ruling A — abandon_task
# ---------------------------------------------------------------------------


class TestRulingAAbandon:
    """abandon_task: RM-only direct action, Stopped → Abandoned (terminal)."""

    def test_stopped_becomes_abandoned(self, temp_db):
        """Happy path: Stopped → Abandoned."""
        task_id = _create_task(temp_db, status="Stopped")
        result = cicd_service.abandon_task(
            temp_db, task_id, reviewer="rm", reviewer_role="RM"
        )
        task = _get_task(temp_db, task_id)
        assert task["status"] == "Abandoned"
        assert result["status"] == "Abandoned"
        assert result["id"] == task_id

    def test_return_value_is_updated_task_dict(self, temp_db):
        """Return value: updated task dict with status=Abandoned."""
        task_id = _create_task(temp_db, status="Stopped")
        result = cicd_service.abandon_task(
            temp_db, task_id, reviewer="rm", reviewer_role="RM"
        )
        assert isinstance(result, dict)
        assert result["id"] == task_id
        assert result["status"] == "Abandoned"

    def test_running_task_rejected(self, temp_db):
        """Running task cannot be abandoned (must stop through App decision first)."""
        task_id = _create_task(temp_db, status="Running")
        with pytest.raises(RuntimeError):
            cicd_service.abandon_task(
                temp_db, task_id, reviewer="rm", reviewer_role="RM"
            )
        assert _get_task(temp_db, task_id)["status"] == "Running"

    def test_abandoned_task_rejected_terminal(self, temp_db):
        """Already Abandoned → error (terminal state, cannot re-abandon)."""
        task_id = _create_task(temp_db, status="Stopped")
        cicd_service.abandon_task(temp_db, task_id, reviewer="rm", reviewer_role="RM")
        with pytest.raises((RuntimeError, ValueError)):
            cicd_service.abandon_task(
                temp_db, task_id, reviewer="rm", reviewer_role="RM"
            )

    def test_non_rm_role_raises_permission_error(self, temp_db):
        """Non-RM reviewer_role → PermissionError (role guard, plan §3.5 c)."""
        task_id = _create_task(temp_db, status="Stopped")
        with pytest.raises(PermissionError):
            cicd_service.abandon_task(
                temp_db, task_id, reviewer="owner_test", reviewer_role="Owner"
            )
        # Status unchanged
        assert _get_task(temp_db, task_id)["status"] == "Stopped"

    def test_spd_role_rejected(self, temp_db):
        """SPD role also cannot abandon (RM-only)."""
        task_id = _create_task(temp_db, status="Stopped")
        with pytest.raises(PermissionError):
            cicd_service.abandon_task(
                temp_db, task_id, reviewer="spd_user", reviewer_role="SPD"
            )

    def test_nonexistent_task_raises(self, temp_db):
        """Nonexistent task_id → RuntimeError."""
        with pytest.raises(RuntimeError):
            cicd_service.abandon_task(
                temp_db, "CICD-9999", reviewer="rm", reviewer_role="RM"
            )

    def test_abandon_is_direct_no_pending_request(self, temp_db):
        """Abandon is a direct action — creates APPROVED audit, NOT a pending request."""
        task_id = _create_task(temp_db, status="Stopped")
        cicd_service.abandon_task(temp_db, task_id, reviewer="rm", reviewer_role="RM")
        reqs = _all_requests_for_task(temp_db, task_id)
        # Exactly one record, and it's approved (not pending)
        assert len(reqs) == 1
        assert reqs[0]["status"] == "approved"

    def test_abandon_audit_record_fields(self, temp_db):
        """Audit record: request_type=modify, origin=abandon, payload={status:{old,new}}."""
        task_id = _create_task(temp_db, status="Stopped")
        cicd_service.abandon_task(temp_db, task_id, reviewer="rm", reviewer_role="RM")
        reqs = _all_requests_for_task(temp_db, task_id)
        audit = reqs[0]
        assert audit["request_type"] == "modify"
        assert audit["status"] == "approved"
        assert audit["reviewer"] == "rm"
        payload = audit["_payload"]
        assert payload["status"]["old"] == "Stopped"
        assert payload["status"]["new"] == "Abandoned"

    def test_abandoned_task_deletable(self, temp_db):
        """After Abandoned, delete_task (RM only, Abandoned gate) should succeed."""
        task_id = _create_task(temp_db, status="Stopped")
        cicd_service.abandon_task(temp_db, task_id, reviewer="rm", reviewer_role="RM")
        cicd_service.delete_task(temp_db, task_id, actor="rm", actor_role="RM")
        assert _get_task(temp_db, task_id) is None


# ---------------------------------------------------------------------------
# Status-lock V3 — modify requests must reject 'status' field
# ---------------------------------------------------------------------------


_MODIFY_STATUS_PAYLOAD: dict = {"status": {"old": "Running", "new": "Stopped"}}
_VALID_MODIFY_PAYLOAD: dict = {"notes": {"old": "old", "new": "new"}}

_CREATE_PAYLOAD_W2: dict = {
    "app_name": "StatusLockV3App",
    "app_version": "1.0",
    "repo_type": "git",
    "repo_name": "ssh://gerrit/PDE/HPC/hpc_sl_v3",
    "branch": "main",
    "build_product": ["maca"],
    "community_artifact": [],
    "build_image": "hpc/sl-v3:latest",
    "test_timeout": 40,
    "owner_username": "rm",
    "notes": "status lock v3 test",
}


def _submit_modify(conn, task_id: str, payload: dict) -> dict:
    return cicd_service.submit_request(
        conn,
        task_id=task_id,
        request_type="modify",
        payload=payload,
        submitter="rm",
        submitter_role="RM",
    )


def _submit_create(conn, extra: dict | None = None) -> dict:
    payload = {**_CREATE_PAYLOAD_W2, **(extra or {})}
    return cicd_service.submit_request(
        conn,
        task_id=None,
        request_type="create",
        payload=payload,
        submitter="rm",
        submitter_role="RM",
    )


class TestStatusLockV3:
    """V3: modify requests must REJECT 'status' field in payload."""

    def test_modify_status_only_raises(self, temp_db):
        """Modify with only 'status' → RuntimeError."""
        task_id = _create_task(temp_db)
        with pytest.raises(RuntimeError):
            _submit_modify(temp_db, task_id, _MODIFY_STATUS_PAYLOAD)

    def test_modify_status_plus_other_field_raises(self, temp_db):
        """Modify with 'status' AND another field → still rejected."""
        task_id = _create_task(temp_db)
        with pytest.raises(RuntimeError):
            _submit_modify(
                temp_db,
                task_id,
                {
                    "status": {"old": "Running", "new": "Stopped"},
                    "notes": {"old": "", "new": "trying to smuggle status through"},
                },
            )

    def test_modify_without_status_succeeds(self, temp_db):
        """Modify without 'status' → accepted (pending)."""
        task_id = _create_task(temp_db)
        result = _submit_modify(temp_db, task_id, _VALID_MODIFY_PAYLOAD)
        assert result["status"] == "pending"
        assert result["request_type"] == "modify"

    def test_modify_notes_succeeds(self, temp_db):
        """Notes-only modify → accepted."""
        task_id = _create_task(temp_db)
        result = _submit_modify(
            temp_db, task_id, {"notes": {"old": "a", "new": "b"}}
        )
        assert result["status"] == "pending"

    def test_modify_build_image_succeeds(self, temp_db):
        """build_image modify (in CICD_TASK_MUTABLE_FIELDS) → accepted."""
        task_id = _create_task(temp_db)
        result = _submit_modify(
            temp_db, task_id,
            {"build_image": {"old": "hpc/wave2-da:latest", "new": "hpc/wave2-da:v2"}},
        )
        assert result["status"] == "pending"

    def test_modify_branch_succeeds(self, temp_db):
        """branch modify → accepted."""
        task_id = _create_task(temp_db)
        result = _submit_modify(
            temp_db, task_id, {"branch": {"old": "main", "new": "feature"}}
        )
        assert result["status"] == "pending"

    def test_create_with_status_running_allowed(self, temp_db):
        """CREATE requests may include 'status' (sets initial task status)."""
        result = _submit_create(temp_db, {"status": "Running"})
        assert result["status"] == "pending"
        assert result["request_type"] == "create"

    def test_create_with_status_stopped_allowed(self, temp_db):
        """CREATE with status=Stopped is valid."""
        result = _submit_create(temp_db, {"status": "Stopped"})
        assert result["status"] == "pending"


# ---------------------------------------------------------------------------
# Integration — app_service.update_snapshot wires sync_decision_to_cicd
# ---------------------------------------------------------------------------


class TestDecisionSyncIntegration:
    """update_snapshot wires sync correctly; response carries cicd_sync key."""

    def _setup_release_app(self, temp_db, tmp_dir) -> tuple[str, str]:
        """Seed minimal release + app; return (release_id, app_id)."""
        from tests.conftest import seed_release
        import release_system.core as core

        release_id = seed_release(temp_db, tmp_path=tmp_dir)
        app_id = core.normalize_name("TestApp")
        return release_id, app_id

    def test_decision_change_adds_cicd_sync_key(self, temp_db, tmp_dir):
        """Any decision change → response includes 'cicd_sync' key."""
        from app.services import app_service

        release_id, app_id = self._setup_release_app(temp_db, tmp_dir)
        # No linked task for this app
        response = app_service.update_snapshot(
            temp_db, release_id, app_id,
            user="rm", role="RM",
            fields={"snapshot": {"release_decision": "stopped"}},
        )
        assert "cicd_sync" in response

    def test_no_linked_task_created_app_id_request(self, temp_db, tmp_dir):
        """Decision change with no linked legacy task creates an App-backed request."""
        from app.services import app_service

        release_id, app_id = self._setup_release_app(temp_db, tmp_dir)
        response = app_service.update_snapshot(
            temp_db, release_id, app_id,
            user="rm", role="RM",
            fields={"snapshot": {"release_decision": "stopped"}},
        )
        assert response["cicd_sync"]["created"] is True
        assert response["cicd_sync"]["request"]["task_id"] == app_id

    def test_orphan_task_matching_app_identity_is_linked_and_synced(self, temp_db, tmp_dir):
        """Decision sync repairs one orphan CICD task with the same repo identity."""
        from app.services import app_service

        release_id, app_id = self._setup_release_app(temp_db, tmp_dir)
        ts = beijing_timestamp()
        with transaction(temp_db):
            cicd_repo.create_task(
                temp_db,
                task_id="CICD-ORPHAN",
                app_id=None,
                app_name="TestApp",
                app_version="1.0",
                repo_type="git",
                repo_name="hpc_testapp",
                branch="maca",
                build_product=["maca"],
                community_artifact=[],
                build_image="",
                test_timeout=40,
                owner_username="rm",
                status="Running",
                notes="orphan from migration",
                created_at=ts,
                updated_at=ts,
            )

        response = app_service.update_snapshot(
            temp_db, release_id, app_id,
            user="rm", role="RM",
            fields={"snapshot": {"release_decision": "stopped"}},
        )

        assert response["cicd_sync"]["created"] is True
        task = cicd_repo.get_task(temp_db, "CICD-ORPHAN")
        assert task["app_id"] == app_id
        req = response["cicd_sync"]["request"]
        raw_payload = req.get("payload", {})
        payload = (
            json.loads(raw_payload) if isinstance(raw_payload, str) else raw_payload
        )
        assert payload["status"]["new"] == "Stopped"

    def test_linked_task_created_true(self, temp_db, tmp_dir):
        """Decision change WITH linked task → cicd_sync.created = True."""
        from app.services import app_service

        release_id, app_id = self._setup_release_app(temp_db, tmp_dir)
        task_id = _create_task(temp_db, app_id=app_id, status="Running")
        response = app_service.update_snapshot(
            temp_db, release_id, app_id,
            user="rm", role="RM",
            fields={"snapshot": {"release_decision": "stopped"}},
        )
        assert response["cicd_sync"]["created"] is True
        assert response["cicd_sync"]["request"] is not None
        # origin is stripped from the return value by _strip_request; verify via DB
        row = temp_db.execute(
            "SELECT origin FROM cicd_task_requests WHERE task_id=?", (task_id,)
        ).fetchone()
        assert row["origin"] == "release_decision_sync"

    def test_linked_task_pending_modify_status_correct(self, temp_db, tmp_dir):
        """Pending modify targets correct status: stopped decision → Stopped."""
        from app.services import app_service

        release_id, app_id = self._setup_release_app(temp_db, tmp_dir)
        task_id = _create_task(temp_db, app_id=app_id, status="Running")
        response = app_service.update_snapshot(
            temp_db, release_id, app_id,
            user="rm", role="RM",
            fields={"snapshot": {"release_decision": "stopped"}},
        )
        req = response["cicd_sync"]["request"]
        # payload may be dict or JSON string depending on _strip_request
        raw_payload = req.get("payload", {})
        payload = (
            json.loads(raw_payload) if isinstance(raw_payload, str) else raw_payload
        )
        assert payload["status"]["new"] == "Stopped"

    def test_stopped_task_release_decision_maps_to_running(self, temp_db, tmp_dir):
        """release decision + Stopped task → cicd_sync.request.payload targets Running."""
        from app.services import app_service

        release_id, app_id = self._setup_release_app(temp_db, tmp_dir)
        # Create task already in Stopped state
        task_id = _create_task(temp_db, app_id=app_id, status="Stopped")
        # Initial decision is "release" (from CSV seed); change to "cicd_only"
        response = app_service.update_snapshot(
            temp_db, release_id, app_id,
            user="rm", role="RM",
            fields={"snapshot": {"release_decision": "cicd_only"}},
        )
        assert response["cicd_sync"]["created"] is True
        req = response["cicd_sync"]["request"]
        raw_payload = req.get("payload", {})
        payload = (
            json.loads(raw_payload) if isinstance(raw_payload, str) else raw_payload
        )
        assert payload["status"]["new"] == "Running"
        assert payload["status"]["old"] == "Stopped"

    def test_same_decision_no_cicd_sync_key(self, temp_db, tmp_dir):
        """If release_decision doesn't change → no 'cicd_sync' key in response."""
        from app.services import app_service

        release_id, app_id = self._setup_release_app(temp_db, tmp_dir)
        _create_task(temp_db, app_id=app_id, status="Running")
        # Send same decision ("release" is the seeded default)
        response = app_service.update_snapshot(
            temp_db, release_id, app_id,
            user="rm", role="RM",
            fields={"snapshot": {"release_decision": "release"}},
        )
        assert "cicd_sync" not in response

    def test_idempotent_second_update_noop(self, temp_db, tmp_dir):
        """Second decision update with pending modify on status → cicd_sync.created=False."""
        from app.services import app_service

        release_id, app_id = self._setup_release_app(temp_db, tmp_dir)
        task_id = _create_task(temp_db, app_id=app_id, status="Running")
        # First update: creates the pending modify
        app_service.update_snapshot(
            temp_db, release_id, app_id,
            user="rm", role="RM",
            fields={"snapshot": {"release_decision": "stopped"}},
        )
        # Snapshot is now "stopped"; change back to "cicd_only" from "stopped"
        # to trigger another sync attempt — but pending still guards
        # Actually, snapshot has been updated to "stopped", so changing to "cicd_only" is a change
        response2 = app_service.update_snapshot(
            temp_db, release_id, app_id,
            user="rm", role="RM",
            fields={"snapshot": {"release_decision": "cicd_only"}},
        )
        # A pending modify on 'status' exists from the first update → no new request
        assert response2["cicd_sync"]["created"] is False
