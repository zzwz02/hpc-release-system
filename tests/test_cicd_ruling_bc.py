"""Phase-4 Wave-1 tests — Ruling B (no auto-approve) and Ruling C (Admin out of CICD).

Ruling B (plan §3.5 b, DA V1):
  ALL submit_request calls → status="pending" regardless of role.
  No task is created at submit time.
  Approval only via approve_request performed by an RM.
  RM approving their own request → is_self_approved=1 (audit flag, keep trail).

Ruling C (plan §3.7, DA V2):
  CICD_CREATE_ROLES  = {Owner, RM}   — Admin removed.
  CICD_APPROVER_ROLES = {RM}          — Admin removed.
  Deliver roles       = {SPD, RM}     — Admin removed.
  Admin gets PermissionError on ALL CICD write paths (tested at service layer).

Golden re-baseline rationale (all Wave-1 parity goldens UNCHANGED):
  • _seed_parity_db uses OLD core.submit_cicd_request (frozen, still auto-approves for RM)
    → DB state identical → all GET goldens unchanged.
  • post_cicd_request_submit_owner: Owner submit → pending (was always pending before B).
  • get_cicd_notifications_rm: count=1 (owner modify pending); RM still in CICD_APPROVER_ROLES={RM}.
  No golden re-baselining needed for Wave 1.
"""
from __future__ import annotations

import pytest

from app.services import cicd_service

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

_CREATE_PAYLOAD: dict = {
    "app_name": "RulingBCTestApp",
    "app_version": "1.0",
    "repo_type": "git",
    "repo_name": "ssh://gerrit/PDE/HPC/hpc_ruling_bc",
    "branch": "main",
    "build_product": ["maca"],
    "community_artifact": [],
    "build_image": "hpc/ruling-bc:latest",
    "test_timeout": 40,
    "owner_username": "rm",
    "notes": "ruling-bc test",
}


def _submit(conn, submitter: str, role: str, **overrides) -> dict:
    """Submit a CICD create request as the given user/role."""
    payload = {**_CREATE_PAYLOAD, **overrides}
    return cicd_service.submit_request(
        conn,
        task_id=None,
        request_type="create",
        payload=payload,
        submitter=submitter,
        submitter_role=role,
        submitter_display=f"{role} display",
    )


def _approve(conn, req_id: int, reviewer: str, role: str = "RM", **kwargs) -> dict:
    """Approve a pending CICD request as the given user/role."""
    return cicd_service.approve_request(
        conn,
        req_id,
        reviewer=reviewer,
        reviewer_role=role,
        **kwargs,
    )


def _task_count(conn) -> int:
    return conn.execute("SELECT COUNT(*) FROM cicd_tasks").fetchone()[0]


# ---------------------------------------------------------------------------
# Ruling B — role constants
# ---------------------------------------------------------------------------


class TestRulingBRoleConstants:
    """Sanity-check: service constants reflect Ruling B/C after impl-1's changes."""

    def test_cicd_create_roles_excludes_admin(self):
        """Ruling C: Admin must NOT be in CICD_CREATE_ROLES."""
        assert "Admin" not in cicd_service.CICD_CREATE_ROLES

    def test_cicd_create_roles_includes_owner_and_rm(self):
        assert "Owner" in cicd_service.CICD_CREATE_ROLES
        assert "RM" in cicd_service.CICD_CREATE_ROLES

    def test_cicd_approver_roles_is_rm_only(self):
        """Ruling C: CICD_APPROVER_ROLES must be exactly {RM}."""
        assert cicd_service.CICD_APPROVER_ROLES == frozenset({"RM"})

    def test_cicd_approver_roles_excludes_admin(self):
        assert "Admin" not in cicd_service.CICD_APPROVER_ROLES


# ---------------------------------------------------------------------------
# Ruling B — submit always returns pending (no auto-approve)
# ---------------------------------------------------------------------------


class TestRulingBSubmitAlwaysPending:
    """ALL roles that are allowed to submit → status='pending'.  No auto-approve."""

    def test_rm_submit_is_pending(self, temp_db):
        """Ruling B: RM submit must be pending (was auto-approved before B)."""
        req = _submit(temp_db, "rm", "RM")
        assert req["status"] == "pending", (
            "RM submit must return status='pending' under Ruling B (no auto-approve)"
        )
        assert req["reviewer"] == "", "reviewer must be empty at submit time"
        assert req["reviewed_at"] == "", "reviewed_at must be empty at submit time"
        assert req["is_self_approved"] == 0, (
            "is_self_approved must be 0 at submit time (flag is set only on approve)"
        )

    def test_owner_submit_is_pending(self, temp_db):
        """Owner submit → pending (unchanged from pre-B, but explicitly verified)."""
        req = _submit(temp_db, "owner_test", "Owner")
        assert req["status"] == "pending"
        assert req["is_self_approved"] == 0

    def test_rm_submit_does_not_create_task(self, temp_db):
        """Ruling B: task must NOT be created at submit time, even for RM."""
        req = _submit(temp_db, "rm", "RM")
        assert req.get("task_id") is None, (
            "task_id must be None before approval — no task created at submit"
        )
        assert _task_count(temp_db) == 0, (
            "No cicd_task should exist in DB until the request is approved"
        )

    def test_owner_submit_does_not_create_task(self, temp_db):
        _submit(temp_db, "owner_test", "Owner")
        assert _task_count(temp_db) == 0


# ---------------------------------------------------------------------------
# Ruling B — RM approval creates task; self-approve flag
# ---------------------------------------------------------------------------


class TestRulingBRMApproval:
    """RM can approve pending requests; self-approve audit flag is set correctly."""

    def test_rm_approve_creates_task(self, temp_db):
        """RM approval (immediate mode) must create the cicd_task row."""
        req = _submit(temp_db, "rm", "RM")
        approved = _approve(temp_db, req["id"], reviewer="rm2")
        assert approved["status"] == "approved"
        assert approved["reviewer"] == "rm2"
        assert approved["task_id"] is not None, "task_id must be set after approval"
        assert _task_count(temp_db) == 1, "Exactly one task must be created"

    def test_rm_approve_sets_task_id_on_request(self, temp_db):
        """Approval backfills task_id on the request row (mirrors core.py)."""
        req = _submit(temp_db, "rm", "RM")
        assert req.get("task_id") is None  # pending: no task yet
        approved = _approve(temp_db, req["id"], reviewer="rm")
        assert approved["task_id"] == "CICD-0001"

    def test_rm_self_approve_sets_is_self_approved(self, temp_db):
        """Ruling B: RM approving their own request → is_self_approved=1 (audit)."""
        req = _submit(temp_db, "rm", "RM")
        approved = _approve(temp_db, req["id"], reviewer="rm")  # same user: "rm"
        assert approved["is_self_approved"] == 1, (
            "RM self-approving own request must set is_self_approved=1"
        )
        assert approved["status"] == "approved"
        assert approved["reviewer"] == "rm"

    def test_rm_approve_others_request_is_not_self_approved(self, temp_db):
        """RM approving a different user's request → is_self_approved=0."""
        req = _submit(temp_db, "owner_test", "Owner")
        approved = _approve(temp_db, req["id"], reviewer="rm")
        assert approved["is_self_approved"] == 0
        assert approved["status"] == "approved"

    def test_rm_approve_own_modify_request_sets_flag(self, temp_db):
        """RM self-approve also applies to modify requests (not only create)."""
        # Create a task first: RM submit + approve
        create_req = _submit(temp_db, "rm", "RM")
        approved_create = _approve(temp_db, create_req["id"], reviewer="rm")
        task_id = approved_create["task_id"]

        # RM submits a modify on the same task (pending under Ruling B)
        modify_req = cicd_service.submit_request(
            temp_db,
            task_id=task_id,
            request_type="modify",
            payload={"notes": {"old": "ruling-bc test", "new": "updated note"}},
            submitter="rm",
            submitter_role="RM",
            source="app_workbench",
        )
        assert modify_req["status"] == "pending"

        # RM approves own modify → is_self_approved=1
        approved_modify = _approve(temp_db, modify_req["id"], reviewer="rm")
        assert approved_modify["is_self_approved"] == 1
        assert approved_modify["status"] == "approved"

    def test_dispatch_spd_approval_does_not_create_task(self, temp_db):
        """dispatch_spd mode: task is NOT created until SPD delivers."""
        req = _submit(temp_db, "rm", "RM")
        approved = _approve(temp_db, req["id"], reviewer="rm", approval_mode="dispatch_spd")
        assert approved["delivery_status"] == "pending"
        assert _task_count(temp_db) == 0, (
            "Task must not be created on dispatch_spd approval; created on delivery"
        )

    def test_owner_submit_then_rm_approve(self, temp_db):
        """Full flow: Owner submit → pending, RM approve → task created."""
        req = _submit(temp_db, "owner_test", "Owner")
        assert req["status"] == "pending"
        approved = _approve(temp_db, req["id"], reviewer="rm")
        assert approved["status"] == "approved"
        assert _task_count(temp_db) == 1


# ---------------------------------------------------------------------------
# Ruling C — Owner and RM can create (positive cases)
# ---------------------------------------------------------------------------


class TestRulingCAllowedRoles:
    """Owner and RM remain permitted to submit CICD requests."""

    def test_owner_can_submit(self, temp_db):
        req = _submit(temp_db, "owner_test", "Owner")
        assert req["status"] == "pending"

    def test_rm_can_submit(self, temp_db):
        req = _submit(temp_db, "rm", "RM")
        assert req["status"] == "pending"


# ---------------------------------------------------------------------------
# Ruling C — Admin blocked from all CICD write paths
# ---------------------------------------------------------------------------


class TestRulingCAdminBlocked:
    """Admin gets PermissionError on every CICD write path (service-layer check)."""

    def test_admin_cannot_submit(self, temp_db):
        """Ruling C: Admin must not be in CICD_CREATE_ROLES."""
        with pytest.raises(PermissionError):
            _submit(temp_db, "admin", "Admin")

    def test_admin_cannot_approve(self, temp_db):
        """Ruling C: Admin must not be in CICD_APPROVER_ROLES."""
        req = _submit(temp_db, "owner_test", "Owner")
        with pytest.raises(PermissionError):
            _approve(temp_db, req["id"], reviewer="admin", role="Admin")

    def test_admin_cannot_reject(self, temp_db):
        """Ruling C: reject_request uses CICD_APPROVER_ROLES; Admin blocked."""
        req = _submit(temp_db, "owner_test", "Owner")
        with pytest.raises(PermissionError):
            cicd_service.reject_request(
                temp_db,
                req["id"],
                reviewer="admin",
                reviewer_role="Admin",
                review_note="Admin reject attempt",
            )

    def test_admin_cannot_cancel_others_request(self, temp_db):
        """Ruling C: Admin (non-submitter) cannot cancel via CICD_APPROVER_ROLES path."""
        req = _submit(temp_db, "owner_test", "Owner")
        with pytest.raises(PermissionError):
            cicd_service.cancel_request(
                temp_db,
                req["id"],
                username="admin",
                role="Admin",
            )

    def test_admin_cannot_deliver(self, temp_db):
        """Ruling C: deliver role set is {SPD, RM}; Admin is excluded."""
        # Setup: RM submit → pending → RM approve with dispatch_spd → delivery_status='pending'
        req = _submit(temp_db, "rm", "RM")
        _approve(temp_db, req["id"], reviewer="rm", approval_mode="dispatch_spd")

        with pytest.raises(PermissionError):
            cicd_service.deliver_request(
                temp_db,
                req["id"],
                deliverer="admin",
                deliverer_role="Admin",
            )

    def test_admin_cannot_re_dispatch(self, temp_db):
        """Ruling C: re_dispatch_request uses CICD_APPROVER_ROLES; Admin blocked."""
        # Setup: RM submit → RM dispatch_spd → SPD returns
        req = _submit(temp_db, "rm", "RM")
        _approve(temp_db, req["id"], reviewer="rm", approval_mode="dispatch_spd")
        cicd_service.return_delivery(
            temp_db,
            req["id"],
            returner="spd",
            returner_role="SPD",
            reason="SPD return for Ruling C test",
        )

        with pytest.raises(PermissionError):
            cicd_service.re_dispatch_request(
                temp_db,
                req["id"],
                actor="admin",
                actor_role="Admin",
            )

    def test_admin_cannot_apply_returned(self, temp_db):
        """Ruling C: apply_returned_request uses CICD_APPROVER_ROLES; Admin blocked."""
        req = _submit(temp_db, "rm", "RM")
        _approve(temp_db, req["id"], reviewer="rm", approval_mode="dispatch_spd")
        cicd_service.return_delivery(
            temp_db,
            req["id"],
            returner="spd",
            returner_role="SPD",
            reason="SPD return for apply-returned test",
        )

        with pytest.raises(PermissionError):
            cicd_service.apply_returned_request(
                temp_db,
                req["id"],
                actor="admin",
                actor_role="Admin",
            )

    def test_admin_cannot_transfer_owner(self, temp_db):
        """Ruling C: transfer_owner uses CICD_APPROVER_ROLES; Admin blocked."""
        req = _submit(temp_db, "rm", "RM")
        approved = _approve(temp_db, req["id"], reviewer="rm")
        task_id = approved["task_id"]

        with pytest.raises(PermissionError):
            cicd_service.transfer_owner(
                temp_db,
                task_id,
                "new_owner",
                actor="admin",
                actor_role="Admin",
            )

    def test_delete_task_service_removed(self):
        """CICD deletion now happens through App deletion, not CICD service APIs."""
        assert not hasattr(cicd_service, "delete_task")


# ---------------------------------------------------------------------------
# Ruling C — SPD deliver path remains intact
# ---------------------------------------------------------------------------


class TestRulingCSPDDeliverAllowed:
    """SPD can still deliver dispatched requests (SPD is in the deliver set)."""

    def test_spd_can_deliver(self, temp_db):
        req = _submit(temp_db, "rm", "RM")
        _approve(temp_db, req["id"], reviewer="rm", approval_mode="dispatch_spd")

        delivered = cicd_service.deliver_request(
            temp_db,
            req["id"],
            deliverer="spd",
            deliverer_role="SPD",
        )
        assert delivered["delivery_status"] == "delivered"
        # Task is created on delivery for 'create' request type
        assert _task_count(temp_db) == 1

    def test_rm_can_still_deliver(self, temp_db):
        """RM remains in the deliver set {SPD, RM}."""
        req = _submit(temp_db, "rm", "RM")
        _approve(temp_db, req["id"], reviewer="rm", approval_mode="dispatch_spd")

        delivered = cicd_service.deliver_request(
            temp_db,
            req["id"],
            deliverer="rm",
            deliverer_role="RM",
        )
        assert delivered["delivery_status"] == "delivered"


# ---------------------------------------------------------------------------
# Golden re-baseline audit (no changes expected; documents the reasoning)
# ---------------------------------------------------------------------------


class TestGoldenRebaselineAudit:
    """Verifies that the parity seed produces DB state matching existing goldens.

    These structural assertions confirm the 'no re-baseline needed' claim:
    the _seed_parity_db() function uses old core.submit_cicd_request() (which
    still auto-approves for RM), so the parity DB state is identical under
    Ruling B/C, and all GET goldens continue to match.
    """

    def test_seed_cicd_task_helper_still_auto_approves_via_old_core(self, temp_db):
        """core.submit_cicd_request (old server, frozen) still auto-approves RM.

        _seed_parity_db() relies on this to create CICD-0001 in the parity DB.
        If old core behaviour changed, this test would fail and we'd need to
        re-baseline the get_cicd_requests_rm / get_cicd_tasks golden bodies.
        """
        import release_system.core as _old_core

        req = _old_core.submit_cicd_request(
            temp_db,
            task_id=None,
            request_type="create",
            payload={**_CREATE_PAYLOAD, "owner_username": "rm"},
            submitter="rm",
            submitter_role="RM",
            submitter_display="RM User",
        )
        # Old core still auto-approves RM; DB has task; golden still valid.
        assert req["status"] == "approved", (
            "Old core auto-approves RM — if this fails, parity golden "
            "get_cicd_requests_rm.json would need re-baselining"
        )
        assert req["task_id"] is not None
        assert req["is_self_approved"] == 1
        assert _task_count(temp_db) == 1

    def test_new_service_submit_rm_is_pending(self, temp_db):
        """New cicd_service.submit_request always returns pending (Ruling B).

        This is the inverse of the above: the NEW FastAPI service behaves
        differently from the frozen old core, but _seed_parity_db() is
        insulated because it calls old core, not the new service.
        """
        req = cicd_service.submit_request(
            temp_db,
            task_id=None,
            request_type="create",
            payload={**_CREATE_PAYLOAD, "owner_username": "rm"},
            submitter="rm",
            submitter_role="RM",
        )
        assert req["status"] == "pending"
        assert req["task_id"] is None
        assert _task_count(temp_db) == 0
