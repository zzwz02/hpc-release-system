"""Phase-4 Wave-3 tests — CICD-first app creation (plan §3.5 a).

Ruling B applies: ALL cicd_first_new_app calls → pending create request (never auto-approved).
Ruling C applies: only Owner/RM may call cicd_first_new_app (Admin excluded — plan §3.7).

1:1 cardinality (plan §3.5 a, §4.2):
  - new identity → create new app + snapshot(cicd_only) + pending create request
  - identity matches CICD-less orphan app → associate (pending request linked to existing app)
  - identity matches app WITH task → reject "该 app 已有 CICD 任务"
  - pending create already exists for same app → idempotency guard rejects

Identity derivation (app/identity.py, plan §4.2):
  - short name (e.g. 'hpc_w3cicd') → full SSH Gerrit URL (offline)
  - absolute URL passthrough
  - .xml manifest path → network required (document/skip)
  - app.identity.same_identity: compares after normalising both sides (short ↔ full URL)

Golden re-baseline notes (Wave 3):
  - Wave 3 adds only POST /api/cicd/apps/new (new endpoint; no existing endpoint changes).
  - All existing parity goldens remain UNCHANGED (parity verified: 718 pass pre-Wave-3).
  - New goldens added: post_cicd_apps_new_admin_403, post_cicd_apps_new_rm_success,
    post_cicd_apps_new_rm_approve, post_cicd_apps_new_collision_reject.
"""
from __future__ import annotations

import json

import pytest

from app.repositories import apps_repo, cicd_repo
from app.repositories.base import dumps_json
from app.services import cicd_service
from app.timeutil import beijing_timestamp
from tests.conftest import seed_release

# ---------------------------------------------------------------------------
# Constants — shared across test classes
# ---------------------------------------------------------------------------

# Short repo name that resolves offline to a deterministic full URL
_REPO_SHORT = "hpc_w3cicd"
_BRANCH = "wave3"
_OFFICIAL_NAME = "W3CicdFirst"
_RESOLVED_URL = "ssh://sw-gerrit-devops.metax-internal.com:29418/PDE/HPC/hpc_w3cicd"
_APP_ID = "w3cicdfirst"   # normalize_name("W3CicdFirst")

_BUILD_PAYLOAD: dict = {
    "build_product": ["maca"],
    "community_artifact": [],
    "build_image": "hpc/w3cicd:latest",
    "test_timeout": 40,
    "notes": "wave3 test",
}


def _create_app(
    conn,
    tmp_dir,
    *,
    official_name: str = _OFFICIAL_NAME,
    repo_name: str = _REPO_SHORT,
    branch: str = _BRANCH,
    submitter: str = "rm",
    submitter_role: str = "RM",
    **extra_payload,
) -> dict:
    """Seed a release and call cicd_first_new_app; return the result dict."""
    seed_release(conn, tmp_path=tmp_dir)
    return cicd_service.cicd_first_new_app(
        conn,
        official_name=official_name,
        repo_type="git",
        repo_name=repo_name,
        branch=branch,
        submitter=submitter,
        submitter_role=submitter_role,
        submitter_display="",
        payload={**_BUILD_PAYLOAD, **extra_payload},
    )


def _task_count(conn) -> int:
    return conn.execute("SELECT COUNT(*) FROM cicd_tasks").fetchone()[0]


def _request_count(conn) -> int:
    return conn.execute("SELECT COUNT(*) FROM cicd_task_requests").fetchone()[0]


# ---------------------------------------------------------------------------
# Identity derivation — these test app/identity.py directly
# ---------------------------------------------------------------------------


class TestIdentityDerivation:
    """repo_to_git_identity() shape and offline behaviour."""

    def test_short_name_expands_to_full_ssh_url(self):
        """Short name ('hpc_myapp') → full Gerrit SSH URL (plan §4.2, offline)."""
        from app.identity import repo_to_git_identity

        url, branch = repo_to_git_identity("git", "hpc_myapp", "main")
        assert url == "ssh://sw-gerrit-devops.metax-internal.com:29418/PDE/HPC/hpc_myapp"
        assert branch == "main"

    def test_absolute_ssh_url_passthrough(self):
        """Absolute SSH URL passes through normalize_git_url unchanged."""
        from app.identity import repo_to_git_identity

        absolute = "ssh://sw-gerrit-devops.metax-internal.com:29418/PDE/HPC/hpc_hpl"
        url, branch = repo_to_git_identity("git", absolute, "maca")
        assert url == absolute
        assert branch == "maca"

    def test_empty_repo_name_returns_none_pair(self):
        """Empty repo_name → (None, None) indicating unresolvable identity."""
        from app.identity import repo_to_git_identity

        url, branch = repo_to_git_identity("git", "", "main")
        assert url is None
        assert branch is None

    def test_xml_manifest_path_needs_network(self):
        """manifest .xml path → resolve_manifest_url; returns (None, None) when unreachable.

        Network to sw-gerrit-devops:29418 is NOT available in this CI environment.
        We document that the function returns (None, None) gracefully on failure
        rather than raising.  The caller (cicd_first_new_app) treats this as an
        unresolvable entry and raises ValueError.
        """
        from app.identity import repo_to_git_identity

        # This will attempt a git archive --remote fetch and fail silently
        url, branch = repo_to_git_identity("repo", "PDE/HPC/manifest.xml", "master")
        # Offline → should be None, None (graceful failure) OR the real resolved URL
        # if the machine happens to have network access.  We only assert it doesn't raise.
        assert url is None or isinstance(url, str)

    def test_short_repo_specific_to_wave3(self):
        """'hpc_w3cicd' → RESOLVED_URL exactly (used in golden files)."""
        from app.identity import normalize_git_url

        assert normalize_git_url(_REPO_SHORT) == _RESOLVED_URL

    def test_same_identity_short_vs_full_url(self):
        """same_identity: short stored name matches full derived URL (plan §4.2 规范化对齐)."""
        from app.identity import same_identity

        assert same_identity("hpc_w3cicd", "wave3", _RESOLVED_URL, "wave3")

    def test_same_identity_different_branch_returns_false(self):
        from app.identity import same_identity

        assert not same_identity("hpc_w3cicd", "main", _RESOLVED_URL, "wave3")

    def test_same_identity_empty_url_returns_false(self):
        from app.identity import same_identity

        assert not same_identity("", "main", _RESOLVED_URL, "wave3")


# ---------------------------------------------------------------------------
# Role gating — service-level checks
# ---------------------------------------------------------------------------


class TestCicdFirstRoleGating:
    """cicd_first_new_app: only Owner/RM may submit (plan §3.7, Ruling C)."""

    def test_admin_raises_permission_error(self, temp_db, tmp_dir):
        """Admin must not call cicd_first_new_app (Ruling C: Admin out of CICD)."""
        seed_release(temp_db, tmp_path=tmp_dir)
        with pytest.raises(PermissionError):
            cicd_service.cicd_first_new_app(
                temp_db,
                official_name="AdminApp",
                repo_type="git",
                repo_name="hpc_adminapp",
                branch="main",
                submitter="admin",
                submitter_role="Admin",
                payload=_BUILD_PAYLOAD,
            )

    def test_guest_raises_permission_error(self, temp_db, tmp_dir):
        """Guest may not submit CICD requests at all."""
        seed_release(temp_db, tmp_path=tmp_dir)
        with pytest.raises(PermissionError):
            cicd_service.cicd_first_new_app(
                temp_db,
                official_name="GuestApp",
                repo_type="git",
                repo_name="hpc_guestapp",
                branch="main",
                submitter="guest",
                submitter_role="Guest",
                payload=_BUILD_PAYLOAD,
            )

    def test_spd_raises_permission_error(self, temp_db, tmp_dir):
        """SPD cannot create CICD-first apps (SPD is delivery only)."""
        seed_release(temp_db, tmp_path=tmp_dir)
        with pytest.raises(PermissionError):
            cicd_service.cicd_first_new_app(
                temp_db,
                official_name="SpdApp",
                repo_type="git",
                repo_name="hpc_spdapp",
                branch="main",
                submitter="spd_user",
                submitter_role="SPD",
                payload=_BUILD_PAYLOAD,
            )

    def test_rm_is_allowed(self, temp_db, tmp_dir):
        """RM can call cicd_first_new_app (CICD_CREATE_ROLES includes RM)."""
        result = _create_app(temp_db, tmp_dir, submitter="rm", submitter_role="RM")
        assert result["ok"] is True

    def test_owner_is_allowed(self, temp_db, tmp_dir):
        """Owner can call cicd_first_new_app (CICD_CREATE_ROLES includes Owner)."""
        result = _create_app(
            temp_db, tmp_dir,
            submitter="owner_test",
            submitter_role="Owner",
        )
        assert result["ok"] is True


# ---------------------------------------------------------------------------
# Validation guards
# ---------------------------------------------------------------------------


class TestCicdFirstValidation:
    """Input validation before any DB write."""

    def test_empty_official_name_raises_value_error(self, temp_db, tmp_dir):
        """Empty official_name → ValueError before any DB writes."""
        seed_release(temp_db, tmp_path=tmp_dir)
        with pytest.raises(ValueError, match="official_name|app 名称"):
            cicd_service.cicd_first_new_app(
                temp_db,
                official_name="",
                repo_type="git",
                repo_name="hpc_valid",
                branch="main",
                submitter="rm",
                submitter_role="RM",
                payload=_BUILD_PAYLOAD,
            )
        # No DB writes should have happened
        assert _task_count(temp_db) == 0
        assert _request_count(temp_db) == 0

    def test_empty_repo_name_raises_value_error(self, temp_db, tmp_dir):
        """Empty repo_name → unresolvable identity → ValueError."""
        seed_release(temp_db, tmp_path=tmp_dir)
        with pytest.raises(ValueError, match="repo|身份"):
            cicd_service.cicd_first_new_app(
                temp_db,
                official_name="SomeApp",
                repo_type="git",
                repo_name="",
                branch="main",
                submitter="rm",
                submitter_role="RM",
                payload=_BUILD_PAYLOAD,
            )

    def test_no_unlocked_release_raises_runtime_error(self, temp_db, tmp_dir):
        """If no unlocked releases exist, the 'created' path raises RuntimeError."""
        # Don't seed a release → list_releases returns []
        with pytest.raises(RuntimeError, match="release|unlock"):
            cicd_service.cicd_first_new_app(
                temp_db,
                official_name="NoRelApp",
                repo_type="git",
                repo_name="hpc_norel",
                branch="main",
                submitter="rm",
                submitter_role="RM",
                payload=_BUILD_PAYLOAD,
            )


# ---------------------------------------------------------------------------
# Happy path — new identity creates app + snapshot + pending request
# ---------------------------------------------------------------------------


class TestCicdFirstHappyPath:
    """cicd_first_new_app with a brand-new (git_url, git_branch) identity."""

    def test_action_is_created(self, temp_db, tmp_dir):
        """New identity → action='created'."""
        result = _create_app(temp_db, tmp_dir)
        assert result["action"] == "created"

    def test_ok_true(self, temp_db, tmp_dir):
        """Return value carries ok=True."""
        result = _create_app(temp_db, tmp_dir)
        assert result["ok"] is True

    def test_app_id_is_normalised_name(self, temp_db, tmp_dir):
        """app_id derived via normalize_name from official_name."""
        result = _create_app(temp_db, tmp_dir)
        assert result["app_id"] == _APP_ID

    def test_git_url_in_response(self, temp_db, tmp_dir):
        """git_url in response = full derived URL (short name expanded)."""
        result = _create_app(temp_db, tmp_dir)
        assert result["git_url"] == _RESOLVED_URL

    def test_git_branch_in_response(self, temp_db, tmp_dir):
        """git_branch in response = the branch provided."""
        result = _create_app(temp_db, tmp_dir)
        assert result["git_branch"] == _BRANCH

    def test_app_row_created_in_db(self, temp_db, tmp_dir):
        """apps table gets a new row with correct git_url / git_branch."""
        _create_app(temp_db, tmp_dir)
        app = apps_repo.get_app(temp_db, _APP_ID)
        assert app is not None
        assert app["git_url"] == _RESOLVED_URL
        assert app["git_branch"] == _BRANCH

    def test_no_cicd_task_created_at_submit(self, temp_db, tmp_dir):
        """Ruling B: no cicd_task row is created at submit time."""
        _create_app(temp_db, tmp_dir)
        assert _task_count(temp_db) == 0, (
            "No cicd_task should exist before RM approves the pending create request"
        )

    def test_pending_create_request_exists(self, temp_db, tmp_dir):
        """Exactly one pending 'create' request is left in the queue."""
        _create_app(temp_db, tmp_dir)
        rows = temp_db.execute(
            "SELECT * FROM cicd_task_requests WHERE status='pending' AND request_type='create'"
        ).fetchall()
        assert len(rows) == 1

    def test_request_status_is_pending(self, temp_db, tmp_dir):
        """The returned request has status='pending' (Ruling B)."""
        result = _create_app(temp_db, tmp_dir)
        assert result["request"]["status"] == "pending"

    def test_request_type_is_create(self, temp_db, tmp_dir):
        result = _create_app(temp_db, tmp_dir)
        assert result["request"]["request_type"] == "create"

    def test_request_has_no_task_id_yet(self, temp_db, tmp_dir):
        """task_id is None before approval (task not yet created)."""
        result = _create_app(temp_db, tmp_dir)
        assert result["request"]["task_id"] is None

    def test_request_submitter_matches(self, temp_db, tmp_dir):
        result = _create_app(temp_db, tmp_dir, submitter="rm_submitter")
        assert result["request"]["submitter"] == "rm_submitter"

    def test_request_payload_contains_app_id(self, temp_db, tmp_dir):
        """Request payload includes app_id for task linkage on approval."""
        result = _create_app(temp_db, tmp_dir)
        raw_payload = result["request"].get("payload", {})
        payload = (
            json.loads(raw_payload) if isinstance(raw_payload, str) else raw_payload
        )
        assert payload.get("app_id") == _APP_ID, (
            "app_id must be in create request payload so _apply_cicd_request "
            "can link the new task to its parent app on RM approval"
        )

    def test_request_payload_has_repo_info(self, temp_db, tmp_dir):
        """Request payload includes repo_name and branch (for task creation)."""
        result = _create_app(temp_db, tmp_dir)
        raw_payload = result["request"].get("payload", {})
        payload = (
            json.loads(raw_payload) if isinstance(raw_payload, str) else raw_payload
        )
        assert payload.get("repo_name") == _REPO_SHORT
        assert payload.get("branch") == _BRANCH
        assert payload.get("repo_type") == "git"

    def test_request_payload_status_is_running(self, temp_db, tmp_dir):
        """Initial task status is Running (aligned with cicd_only decision)."""
        result = _create_app(temp_db, tmp_dir)
        raw_payload = result["request"].get("payload", {})
        payload = (
            json.loads(raw_payload) if isinstance(raw_payload, str) else raw_payload
        )
        assert payload.get("status") == "Running", (
            "cicd_only decision maps to Running CICD status (Ruling D mapping)"
        )

    def test_snapshot_has_cicd_only_decision(self, temp_db, tmp_dir):
        """The initial snapshot in the anchored release has release_decision='cicd_only'."""
        result = _create_app(temp_db, tmp_dir)
        app_id = result["app_id"]
        # Find the release seeded by _create_app
        from release_system import core
        releases = core.list_releases(temp_db)
        assert releases, "A release must exist"
        release = core.get_release(temp_db, releases[0]["id"])
        snap = release["snapshots"].get(app_id)
        assert snap is not None, f"app {app_id} must have a snapshot in the release"
        assert snap.get("release_decision") == "cicd_only", (
            "CICD-first app initial snapshot must have release_decision='cicd_only'"
        )

    def test_origin_column_is_cicd_workbench(self, temp_db, tmp_dir):
        """DB origin column for the created request is 'cicd_workbench'."""
        result = _create_app(temp_db, tmp_dir)
        req_id = result["request"]["id"]
        row = temp_db.execute(
            "SELECT origin FROM cicd_task_requests WHERE id = ?", (req_id,)
        ).fetchone()
        assert row["origin"] == "cicd_workbench"

    def test_is_self_approved_zero_at_submit(self, temp_db, tmp_dir):
        """is_self_approved=0 at submit time (flag is set only on approve)."""
        result = _create_app(temp_db, tmp_dir)
        assert result["request"]["is_self_approved"] == 0

    def test_reviewer_empty_at_submit(self, temp_db, tmp_dir):
        result = _create_app(temp_db, tmp_dir)
        assert result["request"]["reviewer"] == ""

    def test_approval_mode_defaults_to_immediate(self, temp_db, tmp_dir):
        result = _create_app(temp_db, tmp_dir)
        assert result["request"]["approval_mode"] == "immediate"


# ---------------------------------------------------------------------------
# Happy path → RM approval creates task with app_id
# ---------------------------------------------------------------------------


class TestCicdFirstApproval:
    """After RM approval, cicd_task is created with app_id linked to the app."""

    def _submit_and_approve(self, temp_db, tmp_dir) -> tuple[dict, dict]:
        """Submit a CICD-first request and approve it; return (submit_result, approved_req)."""
        submit_result = _create_app(temp_db, tmp_dir)
        req_id = submit_result["request"]["id"]
        approved = cicd_service.approve_request(
            temp_db, req_id, reviewer="rm", reviewer_role="RM"
        )
        return submit_result, approved

    def test_approve_creates_task(self, temp_db, tmp_dir):
        """RM approval creates exactly one cicd_task."""
        self._submit_and_approve(temp_db, tmp_dir)
        assert _task_count(temp_db) == 1

    def test_task_id_set_on_approved_request(self, temp_db, tmp_dir):
        """approved request has task_id set (e.g. CICD-0001)."""
        _, approved = self._submit_and_approve(temp_db, tmp_dir)
        assert approved["task_id"] is not None
        assert approved["task_id"].startswith("CICD-")

    def test_task_has_app_id_linked(self, temp_db, tmp_dir):
        """Created task has app_id pointing to the newly created app (1:1 linkage)."""
        submit_result, approved = self._submit_and_approve(temp_db, tmp_dir)
        task_id = approved["task_id"]
        task_row = temp_db.execute(
            "SELECT app_id FROM cicd_tasks WHERE id = ?", (task_id,)
        ).fetchone()
        assert task_row is not None
        assert task_row["app_id"] == submit_result["app_id"], (
            "cicd_task.app_id must be set to the parent app_id after RM approval"
        )

    def test_task_status_is_running(self, temp_db, tmp_dir):
        """Task initial status is Running (aligned with cicd_only decision)."""
        _, approved = self._submit_and_approve(temp_db, tmp_dir)
        task_id = approved["task_id"]
        task = cicd_repo.get_task(temp_db, task_id)
        assert task["status"] == "Running"

    def test_is_self_approved_set_when_rm_self_approves(self, temp_db, tmp_dir):
        """RM approving their own CICD-first request → is_self_approved=1 (Ruling B audit)."""
        submit_result = _create_app(temp_db, tmp_dir, submitter="rm")
        req_id = submit_result["request"]["id"]
        approved = cicd_service.approve_request(
            temp_db, req_id, reviewer="rm", reviewer_role="RM"
        )
        assert approved["is_self_approved"] == 1

    def test_is_self_approved_zero_for_different_approver(self, temp_db, tmp_dir):
        """Different RM approving → is_self_approved=0."""
        submit_result = _create_app(temp_db, tmp_dir, submitter="owner_test", submitter_role="Owner")
        req_id = submit_result["request"]["id"]
        approved = cicd_service.approve_request(
            temp_db, req_id, reviewer="rm", reviewer_role="RM"
        )
        assert approved["is_self_approved"] == 0

    def test_tasks_for_app_returns_linked_task(self, temp_db, tmp_dir):
        """After approval, tasks_for_app(conn, app_id) returns the new task."""
        submit_result, _ = self._submit_and_approve(temp_db, tmp_dir)
        tasks = cicd_repo.tasks_for_app(temp_db, submit_result["app_id"])
        assert len(tasks) == 1
        assert tasks[0]["app_id"] == submit_result["app_id"]

    def test_after_approval_collision_reject_works(self, temp_db, tmp_dir):
        """After approval (task linked), another cicd_first call with same identity → RuntimeError."""
        self._submit_and_approve(temp_db, tmp_dir)
        # Call service directly (no second seed_release — release already exists)
        with pytest.raises(RuntimeError, match="已有 CICD 任务"):
            cicd_service.cicd_first_new_app(
                temp_db,
                official_name=_OFFICIAL_NAME,
                repo_type="git",
                repo_name=_REPO_SHORT,
                branch=_BRANCH,
                submitter="rm",
                submitter_role="RM",
                payload=_BUILD_PAYLOAD,
            )


# ---------------------------------------------------------------------------
# 1:1 orphan association
# ---------------------------------------------------------------------------


class TestCicdFirstOrphanAssociation:
    """identity matches a CICD-less orphan app → associate (plan §3.5 a)."""

    def _insert_orphan_app(
        self,
        conn,
        *,
        git_url: str = "hpc_w3orphan",
        git_branch: str = "orphan-branch",
        app_id: str = "w3orphan",
    ) -> str:
        """Insert a minimal app row (no releases, no tasks). Returns app_id."""
        ts = beijing_timestamp()
        apps_repo.save_app(conn, {
            "id": app_id,
            "git_url": git_url,
            "git_branch": git_branch,
            "aliases": [app_id],
            "created_by": "test",
            "created_at": ts,
        })
        conn.commit()
        return app_id

    def test_action_is_associated_for_orphan(self, temp_db, tmp_dir):
        """Orphan app (same identity, no tasks) → action='associated'."""
        existing_id = self._insert_orphan_app(temp_db)
        result = cicd_service.cicd_first_new_app(
            temp_db,
            official_name="OrphanApp",
            repo_type="git",
            repo_name="hpc_w3orphan",  # resolves to same URL as stored short name
            branch="orphan-branch",
            submitter="rm",
            submitter_role="RM",
            payload=_BUILD_PAYLOAD,
        )
        assert result["action"] == "associated"

    def test_associated_app_id_matches_existing(self, temp_db, tmp_dir):
        """Orphan association: returned app_id = existing app's id."""
        existing_id = self._insert_orphan_app(temp_db)
        result = cicd_service.cicd_first_new_app(
            temp_db,
            official_name="OrphanApp",
            repo_type="git",
            repo_name="hpc_w3orphan",
            branch="orphan-branch",
            submitter="rm",
            submitter_role="RM",
            payload=_BUILD_PAYLOAD,
        )
        assert result["app_id"] == existing_id

    def test_no_duplicate_app_created(self, temp_db, tmp_dir):
        """Orphan association must NOT create a new apps row."""
        existing_id = self._insert_orphan_app(temp_db)
        before = len(apps_repo.list_apps(temp_db))
        cicd_service.cicd_first_new_app(
            temp_db,
            official_name="OrphanApp",
            repo_type="git",
            repo_name="hpc_w3orphan",
            branch="orphan-branch",
            submitter="rm",
            submitter_role="RM",
            payload=_BUILD_PAYLOAD,
        )
        after = len(apps_repo.list_apps(temp_db))
        assert after == before, "No new app row should be created for orphan association"

    def test_pending_request_created_for_orphan(self, temp_db, tmp_dir):
        """Pending create request is created for the orphan app (Ruling B)."""
        existing_id = self._insert_orphan_app(temp_db)
        result = cicd_service.cicd_first_new_app(
            temp_db,
            official_name="OrphanApp",
            repo_type="git",
            repo_name="hpc_w3orphan",
            branch="orphan-branch",
            submitter="rm",
            submitter_role="RM",
            payload=_BUILD_PAYLOAD,
        )
        assert result["request"]["status"] == "pending"
        assert result["request"]["task_id"] is None  # task only on approval

    def test_request_payload_has_existing_app_id(self, temp_db, tmp_dir):
        """Request payload embeds the existing orphan app_id for task linkage."""
        existing_id = self._insert_orphan_app(temp_db)
        result = cicd_service.cicd_first_new_app(
            temp_db,
            official_name="OrphanApp",
            repo_type="git",
            repo_name="hpc_w3orphan",
            branch="orphan-branch",
            submitter="rm",
            submitter_role="RM",
            payload=_BUILD_PAYLOAD,
        )
        raw_payload = result["request"].get("payload", {})
        payload = json.loads(raw_payload) if isinstance(raw_payload, str) else raw_payload
        assert payload.get("app_id") == existing_id

    def test_full_url_stored_app_also_matches(self, temp_db, tmp_dir):
        """same_identity: full URL stored in DB matches derived full URL from short name."""
        full_url = "ssh://sw-gerrit-devops.metax-internal.com:29418/PDE/HPC/hpc_fulltest"
        existing_id = self._insert_orphan_app(
            temp_db, git_url=full_url, git_branch="feat", app_id="hpc-fulltest"
        )
        result = cicd_service.cicd_first_new_app(
            temp_db,
            official_name="FullTestApp",
            repo_type="git",
            repo_name="hpc_fulltest",  # short name → same full URL
            branch="feat",
            submitter="rm",
            submitter_role="RM",
            payload=_BUILD_PAYLOAD,
        )
        assert result["action"] == "associated"
        assert result["app_id"] == existing_id


# ---------------------------------------------------------------------------
# 1:1 collision reject
# ---------------------------------------------------------------------------


class TestCicdFirstCollisionReject:
    """identity matches existing app that already has a linked task → reject."""

    def _setup_app_with_task(
        self,
        conn,
        tmp_dir,
        *,
        repo_name: str = "hpc_w3linked",
        branch: str = "linked-branch",
    ) -> tuple[str, str]:
        """Create an app + task linked via app_id; return (app_id, task_id)."""
        from app.identity import normalize_git_url

        ts = beijing_timestamp()
        git_url = normalize_git_url(repo_name)
        app_id = "hpc-" + repo_name.replace("_", "-")
        # Insert app
        apps_repo.save_app(conn, {
            "id": app_id,
            "git_url": git_url,
            "git_branch": branch,
            "aliases": [app_id],
            "created_by": "test",
            "created_at": ts,
        })
        # Insert task with app_id set (simulates post-approval state)
        task_id = cicd_repo.next_cicd_id(conn)
        from app.db.connection import transaction

        with transaction(conn):
            cicd_repo.create_task(
                conn,
                task_id=task_id,
                app_id=app_id,
                app_name="LinkedApp",
                app_version="1.0",
                repo_type="git",
                repo_name=repo_name,
                branch=branch,
                build_product=["maca"],
                community_artifact=[],
                build_image="hpc/linked:latest",
                test_timeout=40,
                owner_username="rm",
                status="Running",
                notes="collision test",
                created_at=ts,
                updated_at=ts,
            )
        return app_id, task_id

    def test_collision_raises_runtime_error(self, temp_db, tmp_dir):
        """App with linked task → RuntimeError (plan §3.5 a collision reject)."""
        app_id, task_id = self._setup_app_with_task(temp_db, tmp_dir)
        with pytest.raises(RuntimeError, match="已有 CICD 任务"):
            cicd_service.cicd_first_new_app(
                temp_db,
                official_name="AnyName",
                repo_type="git",
                repo_name="hpc_w3linked",
                branch="linked-branch",
                submitter="rm",
                submitter_role="RM",
                payload=_BUILD_PAYLOAD,
            )

    def test_collision_error_mentions_app_id(self, temp_db, tmp_dir):
        """Error message includes the app_id (for RM debugging)."""
        app_id, task_id = self._setup_app_with_task(temp_db, tmp_dir)
        with pytest.raises(RuntimeError) as exc_info:
            cicd_service.cicd_first_new_app(
                temp_db,
                official_name="AnyName",
                repo_type="git",
                repo_name="hpc_w3linked",
                branch="linked-branch",
                submitter="rm",
                submitter_role="RM",
                payload=_BUILD_PAYLOAD,
            )
        assert app_id in str(exc_info.value)

    def test_collision_error_mentions_task_id(self, temp_db, tmp_dir):
        """Error message includes the existing task_id (for RM debugging)."""
        app_id, task_id = self._setup_app_with_task(temp_db, tmp_dir)
        with pytest.raises(RuntimeError) as exc_info:
            cicd_service.cicd_first_new_app(
                temp_db,
                official_name="AnyName",
                repo_type="git",
                repo_name="hpc_w3linked",
                branch="linked-branch",
                submitter="rm",
                submitter_role="RM",
                payload=_BUILD_PAYLOAD,
            )
        assert task_id in str(exc_info.value)

    def test_collision_leaves_db_unchanged(self, temp_db, tmp_dir):
        """Collision reject must not create any new rows."""
        app_id, task_id = self._setup_app_with_task(temp_db, tmp_dir)
        apps_before = len(apps_repo.list_apps(temp_db))
        tasks_before = _task_count(temp_db)
        reqs_before = _request_count(temp_db)
        with pytest.raises(RuntimeError):
            cicd_service.cicd_first_new_app(
                temp_db,
                official_name="AnyName",
                repo_type="git",
                repo_name="hpc_w3linked",
                branch="linked-branch",
                submitter="rm",
                submitter_role="RM",
                payload=_BUILD_PAYLOAD,
            )
        assert len(apps_repo.list_apps(temp_db)) == apps_before
        assert _task_count(temp_db) == tasks_before
        assert _request_count(temp_db) == reqs_before

    def test_full_flow_collision_reject(self, temp_db, tmp_dir):
        """Full flow: cicd_first create → approve (links task+app) → second create rejects."""
        # Step 1: create + approve → task gets app_id
        result = _create_app(temp_db, tmp_dir)
        req_id = result["request"]["id"]
        cicd_service.approve_request(
            temp_db, req_id, reviewer="rm", reviewer_role="RM"
        )
        # Step 2: second cicd_first with same identity → reject
        # (do NOT call _create_app which would try to re-seed the release)
        with pytest.raises(RuntimeError, match="已有 CICD 任务"):
            cicd_service.cicd_first_new_app(
                temp_db,
                official_name=_OFFICIAL_NAME,
                repo_type="git",
                repo_name=_REPO_SHORT,
                branch=_BRANCH,
                submitter="rm",
                submitter_role="RM",
                payload=_BUILD_PAYLOAD,
            )


# ---------------------------------------------------------------------------
# Idempotency guard — pending create already exists for same app
# ---------------------------------------------------------------------------


class TestCicdFirstIdempotencyGuard:
    """Duplicate pending create for same app_id → RuntimeError (before RM approval)."""

    def _second_call(self, conn) -> None:
        """Call cicd_first_new_app with the same identity without re-seeding the release."""
        cicd_service.cicd_first_new_app(
            conn,
            official_name=_OFFICIAL_NAME,
            repo_type="git",
            repo_name=_REPO_SHORT,
            branch=_BRANCH,
            submitter="rm",
            submitter_role="RM",
            payload=_BUILD_PAYLOAD,
        )

    def test_duplicate_pending_create_rejected(self, temp_db, tmp_dir):
        """Second cicd_first call with same identity while first is pending → RuntimeError."""
        # First call: creates pending request (no task yet)
        _create_app(temp_db, tmp_dir)
        # Second call: same identity, still pending (not yet approved)
        # Release already seeded; call service directly
        with pytest.raises(RuntimeError, match="待审批|CICD 创建申请"):
            self._second_call(temp_db)

    def test_duplicate_leaves_single_pending_request(self, temp_db, tmp_dir):
        """DB should have exactly ONE pending create request for the app."""
        _create_app(temp_db, tmp_dir)
        with pytest.raises(RuntimeError):
            self._second_call(temp_db)
        rows = temp_db.execute(
            "SELECT * FROM cicd_task_requests WHERE status='pending' AND request_type='create'"
        ).fetchall()
        assert len(rows) == 1, "Only the first pending request should exist"


# ---------------------------------------------------------------------------
# FE↔BE contract — exact payload shape the frontend sends
# ---------------------------------------------------------------------------


class TestCicdFirstFeContract:
    """Contract test: the exact JSON payload the FE sends to POST /api/cicd/apps/new
    must be accepted with HTTP 200 and return a pending create request.

    W3.1 wizard payload (after task-5 + task-7 fixes):
        official_name      — required for app_id derivation
        repo_type / repo_name / branch  — repo identity (no git_url/git_branch)
        app_info_parsed    — optional: full parsed blob from POST /api/cicd/apps/fetch-preview
        app_info_commit_id — optional: commit_id from fetch-preview (source attribution)
        release_id         — FE context field  → IGNORED by BE
        owner_username     — FE context field  → IGNORED by BE

    The old per-field build params (build_product, build_image, test_timeout…) are no
    longer mandatory wizard fields after W3.1 — they may still be sent but app_info
    is the authoritative source when provided.

    Guards the contract so payload mismatches fail CI, not live.
    """

    # The new W3.1 wizard payload shape (official_name + repo + app_info_parsed).
    # app_info_parsed is the parsed blob from POST /api/cicd/apps/fetch-preview.
    _MOCK_PARSED = {
        "app_name": _OFFICIAL_NAME,
        "app_version": "1.0",
        "x86_chips": ["C500"],
        "arm_chips": [],
        "python_labels": ["3.10"],
        "pytorch_labels": ["2.1"],
        "build_os": ["ubuntu20.04"],
        "build_arches": ["amd64"],
        "build_targets": [],
        "test_targets": [],
        "tests": [],
        "raw": {
            "app_version": "1.0",
            "app_name": _OFFICIAL_NAME,
            "app_build": {},
            "app_test": {},
        },
    }

    _FE_PAYLOAD = {
        "official_name": _OFFICIAL_NAME,        # required — app_id derivation
        "repo_type": "git",
        "repo_name": _REPO_SHORT,
        "branch": _BRANCH,
        "app_info_parsed": _MOCK_PARSED,         # pre-fetched from Gerrit via fetch-preview
        "app_info_commit_id": "abcdef123456",    # from fetch-preview response
        # FE context fields — IGNORED by BE:
        "release_id": "some-release-id",
        "owner_username": "rm",
    }

    def test_fe_shaped_payload_returns_200(self, db_path, tmp_dir):
        """HTTP POST with exact FE payload (including ignored fields) → 200, pending request."""
        from fastapi.testclient import TestClient

        from app.db.connection import connect as app_connect
        from app.deps import get_db, require_login
        from app.main import create_app

        # Seed a release in the temp DB so the service has something to attach to
        conn = app_connect(db_path)
        seed_release(conn, tmp_path=tmp_dir)
        conn.close()

        fastapi_app = create_app()

        # Override get_db → use the test DB (not settings.db_path)
        def _override_get_db():
            c = app_connect(db_path)
            try:
                yield c
            finally:
                c.close()

        # Override require_login → return a fixed RM user (no cookie needed)
        def _override_require_login():
            return {"username": "rm", "role": "RM", "display_name": ""}

        fastapi_app.dependency_overrides[get_db] = _override_get_db
        fastapi_app.dependency_overrides[require_login] = _override_require_login

        with TestClient(fastapi_app, raise_server_exceptions=False) as client:
            resp = client.post("/api/cicd/apps/new", json=self._FE_PAYLOAD)

        assert resp.status_code == 200, (
            f"Expected 200 from FE-shaped payload, got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        assert body["ok"] is True
        assert body["action"] == "created"
        assert body["app_id"] == _APP_ID
        req = body["request"]
        assert req["status"] == "pending"
        assert req["task_id"] is None         # no CICD task until RM approves

    def test_fe_payload_ignored_fields_do_not_error(self, db_path, tmp_dir):
        """release_id and owner_username in body are silently ignored; no 400/422."""
        from fastapi.testclient import TestClient

        from app.db.connection import connect as app_connect
        from app.deps import get_db, require_login
        from app.main import create_app

        conn = app_connect(db_path)
        seed_release(conn, tmp_path=tmp_dir)
        conn.close()

        fastapi_app = create_app()
        fastapi_app.dependency_overrides[get_db] = (
            lambda: (yield app_connect(db_path))
        )
        fastapi_app.dependency_overrides[require_login] = (
            lambda: {"username": "rm", "role": "RM", "display_name": ""}
        )

        with TestClient(fastapi_app, raise_server_exceptions=False) as client:
            # Extra unknown fields must not trigger a validation error
            resp = client.post(
                "/api/cicd/apps/new",
                json={**self._FE_PAYLOAD, "extra_unknown_field": "should_be_ignored"},
            )

        assert resp.status_code == 200, resp.text

    def test_fe_payload_pending_request_linkage(self, db_path, tmp_dir):
        """Pending request payload contains app_id linkage field."""
        from fastapi.testclient import TestClient

        from app.db.connection import connect as app_connect
        from app.deps import get_db, require_login
        from app.main import create_app

        conn = app_connect(db_path)
        seed_release(conn, tmp_path=tmp_dir)
        conn.close()

        fastapi_app = create_app()
        fastapi_app.dependency_overrides[get_db] = (
            lambda: (yield app_connect(db_path))
        )
        fastapi_app.dependency_overrides[require_login] = (
            lambda: {"username": "rm", "role": "RM", "display_name": ""}
        )

        with TestClient(fastapi_app, raise_server_exceptions=False) as client:
            resp = client.post("/api/cicd/apps/new", json=self._FE_PAYLOAD)

        assert resp.status_code == 200, resp.text
        payload_obj = json.loads(resp.json()["request"]["payload"])
        assert payload_obj.get("app_id") == _APP_ID, (
            "request.payload must include app_id for RM-approval→task linkage"
        )
