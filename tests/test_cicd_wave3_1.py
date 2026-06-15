"""Phase-4 Wave-3.1 tests — fetch-preview endpoint + cicd-first with app_info.

New features (task 7 / impl-1 — now implemented):

1. POST /api/cicd/apps/fetch-preview (auth Owner/RM):
   Body: {repo_type, repo_name, branch}
   - Derives (git_url, git_branch) FIRST via identity.repo_to_git_identity
     (offline for short names; network only for .xml manifests).
   - Returns identity REGARDLESS of whether the Gerrit content fetch succeeds
     (Wave-4: wizard shows the mapping even when Gerrit is unreachable).
   - Returns: {git_url, git_branch, needs_network, app_info_unavailable,
               app_info_error, ...app_info fields when available}
     app_info fields (app_version/x86_chips/arm_chips/python_label/pytorch_label/
     os/arch/commit_id/parsed) are present only when app_info_unavailable=False.
   - On Gerrit content failure: HTTP 200, app_info_unavailable=True, app_info_error set.
   - On bad repo_name / branch: ValueError → HTTP 400.
   - Auth: CICD_CREATE_ROLES (Owner/RM); Admin/Guest → 403.

2. cicd_first_new_app with optional app_info:
   New kwargs: app_info_parsed: dict | None = None, app_info_commit_id: str = ""
   - When provided: all unlocked release snapshots receive the parsed fields
     (version, x86_chips, arm_chips, python_labels, pytorch_labels, build_os,
     build_arches, app_info blob) and owner_confirmed=True
   - When absent: snapshot stays at cicd_only defaults

   Mockable: preview_cicd_app_info has _fetch_fn kwarg for direct injection.
   For HTTP tests: patch app.integrations.gerrit.fetch_app_info at module level.
   Factory: cicd_service.make_fake_app_info_fetch() for offline e2e without Gerrit.

3. FE-contract update (see TestCicdFirstFeContract in test_cicd_wave3.py):
   New wizard payload: official_name + repo + app_info_parsed/commit_id + context.
   Old per-field build params (build_product, build_image…) are no longer the
   primary wizard fields; they come from app_info.
"""
from __future__ import annotations

import json

import pytest

from app.services import cicd_service
from tests.conftest import seed_release

# ---------------------------------------------------------------------------
# Shared constants
# ---------------------------------------------------------------------------

_REPO_SHORT = "hpc_w3cicd"
_BRANCH = "wave3"
_OFFICIAL_NAME = "W3CicdFirst"
_RESOLVED_URL = "ssh://sw-gerrit-devops.metax-internal.com:29418/PDE/HPC/hpc_w3cicd"
_APP_ID = "w3cicdfirst"

# ---------------------------------------------------------------------------
# Mock Gerrit app_info — minimal but realistic app_info.json payload.
# core.parse_app_info() produces the 7 preview fields from this.
# ---------------------------------------------------------------------------

_MOCK_APP_INFO_RAW = json.dumps({
    "app_name": "W3CicdFirst",
    "app_version": "1.0",
    "app_build": {
        "ubuntu20.04_amd64": {
            "arch": "amd64",
            "supported_chip": ["C500"],
            "enabled": True,
            "python_label": "3.10",
            "pytorch_label": "2.1",
            "os": "ubuntu20.04",
        },
    },
    "app_test": {
        "sanity": {
            "test_cmd": "w3cicd --version",
            "supported_chip": {"C500": ["ubuntu20.04_amd64"]},
            "enabled": True,
        }
    },
})
_MOCK_COMMIT_ID = "abcdef1234567890abcdef12"


def _mock_fetch_fn(git_url, git_branch, **kwargs):
    """Injectable fetch function; returns (raw_json, commit_id) without network."""
    return (_MOCK_APP_INFO_RAW, _MOCK_COMMIT_ID)


def _mock_fetch_fail(git_url, git_branch, **kwargs):
    """Injectable fetch that simulates a Gerrit outage."""
    raise RuntimeError("无法连接 Gerrit: Connection refused")


# ---------------------------------------------------------------------------
# Helpers — TestClient factory
# ---------------------------------------------------------------------------

def _make_app(db_path, *, role: str = "RM", username: str = "rm"):
    """Create a FastAPI app with test-DB + fixed-auth overrides."""
    from app.db.connection import connect as app_connect
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


# ---------------------------------------------------------------------------
# Preview service — direct (no HTTP)
# ---------------------------------------------------------------------------


class TestPreviewCicdAppInfoService:
    """Unit tests for preview_cicd_app_info() service function."""

    def _preview(self, **overrides):
        kwargs = dict(
            repo_type="git",
            repo_name=_REPO_SHORT,
            branch=_BRANCH,
            submitter_role="RM",
            _fetch_fn=_mock_fetch_fn,
        )
        kwargs.update(overrides)
        return cicd_service.preview_cicd_app_info(**kwargs)

    # --- identity ---

    def test_short_name_resolves_git_url(self):
        result = self._preview()
        assert result["git_url"] == _RESOLVED_URL

    def test_branch_returned_unchanged(self):
        result = self._preview()
        assert result["git_branch"] == _BRANCH

    # --- 7 app_info fields (all comma-joined strings) ---

    def test_app_version(self):
        result = self._preview()
        assert result["app_version"] == "1.0"

    def test_x86_chips_string(self):
        result = self._preview()
        assert result["x86_chips"] == "C500"

    def test_arm_chips_empty_string(self):
        result = self._preview()
        # No ARM build env in mock → empty string (not None or [])
        assert result["arm_chips"] == ""

    def test_python_label_string(self):
        result = self._preview()
        assert result["python_label"] == "3.10"

    def test_pytorch_label_string(self):
        result = self._preview()
        assert result["pytorch_label"] == "2.1"

    def test_os_string(self):
        result = self._preview()
        assert result["os"] == "ubuntu20.04"

    def test_arch_string(self):
        result = self._preview()
        assert result["arch"] == "amd64"

    # --- extra response fields ---

    def test_commit_id_returned(self):
        result = self._preview()
        assert result["commit_id"] == _MOCK_COMMIT_ID

    def test_parsed_blob_returned(self):
        """'parsed' blob is included for FE to pass back as app_info_parsed."""
        result = self._preview()
        assert "parsed" in result
        assert result["parsed"]["app_version"] == "1.0"
        assert "x86_chips" in result["parsed"]

    # --- no DB writes ---

    def test_no_db_access_needed(self):
        """preview_cicd_app_info is stateless — no connection param."""
        import inspect
        sig = inspect.signature(cicd_service.preview_cicd_app_info)
        assert "conn" not in sig.parameters

    # --- Gerrit content fetch failure: Wave-4 new behavior ---
    # Identity is ALWAYS returned; app_info_unavailable=True soft flag (no exception).

    def test_gerrit_failure_returns_unavailable_flag(self):
        """Gerrit failure → app_info_unavailable=True in response, no exception."""
        result = self._preview(_fetch_fn=_mock_fetch_fail)
        assert result["app_info_unavailable"] is True

    def test_gerrit_failure_still_returns_identity(self):
        """Even when Gerrit content fetch fails, git_url / git_branch are returned."""
        result = self._preview(_fetch_fn=_mock_fetch_fail)
        assert result["git_url"] == _RESOLVED_URL
        assert result["git_branch"] == _BRANCH

    def test_gerrit_failure_returns_error_string(self):
        """app_info_error contains the failure detail."""
        result = self._preview(_fetch_fn=_mock_fetch_fail)
        assert result.get("app_info_error")

    def test_gerrit_failure_no_app_info_fields(self):
        """app_info fields (app_version etc.) absent when unavailable."""
        result = self._preview(_fetch_fn=_mock_fetch_fail)
        assert "app_version" not in result
        assert "parsed" not in result

    def test_success_returns_unavailable_false(self):
        """Happy path: app_info_unavailable=False."""
        result = self._preview()
        assert result["app_info_unavailable"] is False
        assert result.get("app_info_error") is None

    # --- needs_network flag ---

    def test_git_type_needs_network_false(self):
        """Short git-type repo names: offline identity → needs_network=False."""
        result = self._preview()
        assert result["needs_network"] is False

    # --- input validation (still raises ValueError → 400) ---

    def test_empty_repo_name_raises_value_error(self):
        with pytest.raises(ValueError, match="repo"):
            self._preview(repo_name="")

    def test_empty_branch_raises_value_error(self):
        with pytest.raises(ValueError, match="branch"):
            self._preview(branch="")


# ---------------------------------------------------------------------------
# Fetch-preview HTTP — role-gating
# ---------------------------------------------------------------------------

_PREVIEW_PATH = "/api/cicd/apps/fetch-preview"
_PREVIEW_BODY = {"repo_type": "git", "repo_name": _REPO_SHORT, "branch": _BRANCH}


class TestFetchPreviewRoleGating:
    """Admin/Guest → 403; Owner/RM → past auth (200 or 502 on gerrit, not 403)."""

    def test_admin_gets_403(self, db_path):
        from fastapi.testclient import TestClient
        app = _make_app(db_path, role="Admin", username="admin_user")
        with TestClient(app, raise_server_exceptions=False) as client:
            resp = client.post(_PREVIEW_PATH, json=_PREVIEW_BODY)
        assert resp.status_code == 403, resp.text
        body = resp.json()
        assert body["ok"] is False
        assert "Owner" in body["error"] or "RM" in body["error"]

    def test_guest_gets_403(self, db_path):
        from fastapi.testclient import TestClient
        app = _make_app(db_path, role="Guest", username="guest_user")
        with TestClient(app, raise_server_exceptions=False) as client:
            resp = client.post(_PREVIEW_PATH, json=_PREVIEW_BODY)
        assert resp.status_code == 403, resp.text

    def test_spd_gets_403(self, db_path):
        from fastapi.testclient import TestClient
        app = _make_app(db_path, role="SPD", username="spd_user")
        with TestClient(app, raise_server_exceptions=False) as client:
            resp = client.post(_PREVIEW_PATH, json=_PREVIEW_BODY)
        assert resp.status_code == 403, resp.text

    def test_rm_passes_auth(self, db_path):
        """RM is in CICD_CREATE_ROLES → gets past auth (mocked Gerrit → 200)."""
        from unittest.mock import patch
        from fastapi.testclient import TestClient
        app = _make_app(db_path, role="RM", username="rm")
        with TestClient(app, raise_server_exceptions=False) as client:
            with patch(
                "app.integrations.gerrit.fetch_app_info",
                return_value=(_MOCK_APP_INFO_RAW, _MOCK_COMMIT_ID),
            ):
                resp = client.post(_PREVIEW_PATH, json=_PREVIEW_BODY)
        assert resp.status_code not in (401, 403), (
            f"RM should pass auth, got {resp.status_code}: {resp.text}"
        )

    def test_owner_passes_auth(self, db_path):
        """Owner is in CICD_CREATE_ROLES → passes auth gate."""
        from unittest.mock import patch
        from fastapi.testclient import TestClient
        app = _make_app(db_path, role="Owner", username="owner_user")
        with TestClient(app, raise_server_exceptions=False) as client:
            with patch(
                "app.integrations.gerrit.fetch_app_info",
                return_value=(_MOCK_APP_INFO_RAW, _MOCK_COMMIT_ID),
            ):
                resp = client.post(_PREVIEW_PATH, json=_PREVIEW_BODY)
        assert resp.status_code not in (401, 403), (
            f"Owner should pass auth, got {resp.status_code}: {resp.text}"
        )


# ---------------------------------------------------------------------------
# Fetch-preview HTTP — success response shape
# ---------------------------------------------------------------------------


class TestFetchPreviewHttp:
    """HTTP-level tests for POST /api/cicd/apps/fetch-preview success path."""

    def _post_preview(self, db_path, body=None):
        from unittest.mock import patch
        from fastapi.testclient import TestClient
        app = _make_app(db_path)
        with TestClient(app, raise_server_exceptions=False) as client:
            with patch(
                "app.integrations.gerrit.fetch_app_info",
                return_value=(_MOCK_APP_INFO_RAW, _MOCK_COMMIT_ID),
            ):
                resp = client.post(_PREVIEW_PATH, json=body or _PREVIEW_BODY)
        return resp

    def test_returns_200(self, db_path):
        resp = self._post_preview(db_path)
        assert resp.status_code == 200, resp.text

    def test_git_url_resolved(self, db_path):
        body = self._post_preview(db_path).json()
        assert body["git_url"] == _RESOLVED_URL

    def test_git_branch_returned(self, db_path):
        body = self._post_preview(db_path).json()
        assert body["git_branch"] == _BRANCH

    def test_app_version_field(self, db_path):
        body = self._post_preview(db_path).json()
        assert body["app_version"] == "1.0"

    def test_x86_chips_comma_string(self, db_path):
        body = self._post_preview(db_path).json()
        assert body["x86_chips"] == "C500"

    def test_arm_chips_empty(self, db_path):
        body = self._post_preview(db_path).json()
        assert body["arm_chips"] == ""

    def test_python_label_comma_string(self, db_path):
        body = self._post_preview(db_path).json()
        assert body["python_label"] == "3.10"

    def test_pytorch_label_comma_string(self, db_path):
        body = self._post_preview(db_path).json()
        assert body["pytorch_label"] == "2.1"

    def test_os_comma_string(self, db_path):
        body = self._post_preview(db_path).json()
        assert body["os"] == "ubuntu20.04"

    def test_arch_comma_string(self, db_path):
        body = self._post_preview(db_path).json()
        assert body["arch"] == "amd64"

    def test_commit_id_in_response(self, db_path):
        body = self._post_preview(db_path).json()
        assert body["commit_id"] == _MOCK_COMMIT_ID

    def test_parsed_blob_in_response(self, db_path):
        """'parsed' key is present so FE can pass it back as app_info_parsed."""
        body = self._post_preview(db_path).json()
        assert "parsed" in body, "'parsed' blob must be in preview response"
        assert body["parsed"]["app_version"] == "1.0"

    def test_empty_repo_name_gives_400(self, db_path):
        resp = self._post_preview(db_path, body={
            "repo_type": "git", "repo_name": "", "branch": _BRANCH
        })
        assert resp.status_code == 400, resp.text

    def test_no_db_writes(self, db_path, tmp_dir):
        """fetch-preview is read-only — apps + requests tables unchanged."""
        from app.db.connection import connect as app_connect
        conn = app_connect(db_path)
        seed_release(conn, tmp_path=tmp_dir)
        before_apps = conn.execute("SELECT COUNT(*) FROM apps").fetchone()[0]
        before_reqs = conn.execute("SELECT COUNT(*) FROM cicd_task_requests").fetchone()[0]
        conn.close()

        self._post_preview(db_path)

        conn = app_connect(db_path)
        after_apps = conn.execute("SELECT COUNT(*) FROM apps").fetchone()[0]
        after_reqs = conn.execute("SELECT COUNT(*) FROM cicd_task_requests").fetchone()[0]
        conn.close()
        assert after_apps == before_apps
        assert after_reqs == before_reqs


# ---------------------------------------------------------------------------
# Fetch-preview HTTP — Gerrit content fetch failure (Wave-4 new behavior)
# ---------------------------------------------------------------------------
# Identity is ALWAYS returned; content failure → 200 with app_info_unavailable=True.


class TestFetchPreviewGerritFail:
    """Gerrit outage → HTTP 200 with app_info_unavailable=True + identity.

    Wave-4 change: previously → 502.  Now → 200 + soft flag so the wizard
    can still display the derived git_url/git_branch even when Gerrit is down.
    """

    def _post_with_gerrit_fail(self, db_path, exc):
        from unittest.mock import patch
        from fastapi.testclient import TestClient
        app = _make_app(db_path)
        with TestClient(app, raise_server_exceptions=False) as client:
            with patch(
                "app.integrations.gerrit.fetch_app_info",
                side_effect=exc,
            ):
                return client.post(_PREVIEW_PATH, json=_PREVIEW_BODY)

    def test_gerrit_failure_gives_200(self, db_path):
        """Content fetch failure → 200 (identity still returned)."""
        resp = self._post_with_gerrit_fail(
            db_path, RuntimeError("无法连接 Gerrit: Connection refused")
        )
        assert resp.status_code == 200, (
            f"Gerrit content failure must return 200 with unavailable flag, "
            f"got {resp.status_code}: {resp.text}"
        )

    def test_unavailable_flag_set(self, db_path):
        """Response contains app_info_unavailable=True."""
        resp = self._post_with_gerrit_fail(
            db_path, RuntimeError("无法连接 Gerrit: Connection refused")
        )
        body = resp.json()
        assert body.get("app_info_unavailable") is True

    def test_identity_still_returned(self, db_path):
        """git_url and git_branch present even when content fetch failed."""
        resp = self._post_with_gerrit_fail(
            db_path, RuntimeError("Connection refused")
        )
        body = resp.json()
        assert body.get("git_url") == _RESOLVED_URL
        assert body.get("git_branch") == _BRANCH

    def test_error_detail_in_response(self, db_path):
        """app_info_error contains the failure detail."""
        resp = self._post_with_gerrit_fail(
            db_path, RuntimeError("timed out fetching app_info.json")
        )
        body = resp.json()
        assert body.get("app_info_error"), "app_info_error must be set on failure"

    def test_no_app_info_fields_when_unavailable(self, db_path):
        """app_version / parsed absent when content unavailable."""
        resp = self._post_with_gerrit_fail(
            db_path, RuntimeError("network error")
        )
        body = resp.json()
        assert "app_version" not in body
        assert "parsed" not in body

    def test_not_500(self, db_path):
        """Gerrit failure must not bubble up as 500."""
        resp = self._post_with_gerrit_fail(
            db_path, RuntimeError("network error")
        )
        assert resp.status_code != 500, (
            "Gerrit failure must not be a 500 — must return 200 with unavailable flag"
        )


# ---------------------------------------------------------------------------
# cicd_first_new_app WITH app_info — snapshot carries parsed fields
# ---------------------------------------------------------------------------


class TestCicdFirstWithAppInfo:
    """cicd_first_new_app WITH app_info_parsed → snapshot fields + owner_confirmed=True."""

    _BUILD_PAYLOAD: dict = {
        "build_product": ["maca"],
        "community_artifact": [],
        "build_image": "hpc/w3cicd:latest",
        "test_timeout": 40,
        "notes": "wave3.1 test",
    }

    def _get_parsed(self):
        """Get the parsed blob from preview (using mock fetch)."""
        result = cicd_service.preview_cicd_app_info(
            repo_type="git",
            repo_name=_REPO_SHORT,
            branch=_BRANCH,
            submitter_role="RM",
            _fetch_fn=_mock_fetch_fn,
        )
        return result["parsed"], result["commit_id"]

    def _create_with_app_info(self, conn):
        parsed, commit_id = self._get_parsed()
        return cicd_service.cicd_first_new_app(
            conn,
            official_name=_OFFICIAL_NAME,
            repo_type="git",
            repo_name=_REPO_SHORT,
            branch=_BRANCH,
            submitter="rm",
            submitter_role="RM",
            submitter_display="",
            payload=self._BUILD_PAYLOAD,
            app_info_parsed=parsed,
            app_info_commit_id=commit_id,
        )

    def _get_snap(self, conn, app_id: str) -> dict:
        from release_system import core
        releases = core.list_releases(conn)
        unlocked = [r for r in releases if not r.get("released_locked")]
        assert unlocked, "No unlocked release"
        rel_full = core.get_release(conn, unlocked[0]["id"])
        return rel_full["snapshots"][app_id]

    # --- happy path ---

    def test_action_created(self, temp_db, tmp_dir):
        seed_release(temp_db, tmp_path=tmp_dir)
        result = self._create_with_app_info(temp_db)
        assert result["ok"] is True
        assert result["action"] == "created"
        assert result["app_id"] == _APP_ID

    def test_owner_confirmed_true(self, temp_db, tmp_dir):
        """owner_confirmed=True when app_info_parsed provided at creation."""
        seed_release(temp_db, tmp_path=tmp_dir)
        result = self._create_with_app_info(temp_db)
        snap = self._get_snap(temp_db, result["app_id"])
        assert snap.get("owner_confirmed") is True, (
            "owner_confirmed must be True when app_info_parsed provided"
        )

    def test_version_set_from_app_info(self, temp_db, tmp_dir):
        seed_release(temp_db, tmp_path=tmp_dir)
        result = self._create_with_app_info(temp_db)
        snap = self._get_snap(temp_db, result["app_id"])
        assert snap.get("version") == "1.0", (
            f"snapshot.version should be '1.0', got: {snap.get('version')}"
        )

    def test_x86_chips_set(self, temp_db, tmp_dir):
        seed_release(temp_db, tmp_path=tmp_dir)
        result = self._create_with_app_info(temp_db)
        snap = self._get_snap(temp_db, result["app_id"])
        assert "C500" in (snap.get("x86_chips") or ""), (
            f"snapshot.x86_chips should contain C500, got: {snap.get('x86_chips')}"
        )

    def test_arm_chips_empty(self, temp_db, tmp_dir):
        seed_release(temp_db, tmp_path=tmp_dir)
        result = self._create_with_app_info(temp_db)
        snap = self._get_snap(temp_db, result["app_id"])
        assert snap.get("arm_chips", "") == "", (
            f"No ARM in mock → arm_chips should be '', got: {snap.get('arm_chips')}"
        )

    def test_python_labels_set(self, temp_db, tmp_dir):
        seed_release(temp_db, tmp_path=tmp_dir)
        result = self._create_with_app_info(temp_db)
        snap = self._get_snap(temp_db, result["app_id"])
        assert "3.10" in (snap.get("python_labels") or ""), (
            f"snapshot.python_labels should contain 3.10, got: {snap.get('python_labels')}"
        )

    def test_app_info_blob_stored(self, temp_db, tmp_dir):
        """snapshot.app_info is set with source_type='cicd_workbench'."""
        seed_release(temp_db, tmp_path=tmp_dir)
        result = self._create_with_app_info(temp_db)
        snap = self._get_snap(temp_db, result["app_id"])
        ai = snap.get("app_info")
        assert ai is not None, "snapshot.app_info should be set"
        assert ai.get("source_type") == "cicd_workbench"
        assert ai.get("commit_id") == _MOCK_COMMIT_ID

    def test_pending_request_still_created(self, temp_db, tmp_dir):
        """app_info attachment does not skip the pending CICD create request."""
        seed_release(temp_db, tmp_path=tmp_dir)
        result = self._create_with_app_info(temp_db)
        req = result["request"]
        assert req["status"] == "pending"
        assert req["task_id"] is None  # task only on RM approval

    # --- without app_info: owner_confirmed stays False ---

    def test_owner_confirmed_false_without_app_info(self, temp_db, tmp_dir):
        """Without app_info_parsed, owner_confirmed stays falsy (default)."""
        from release_system import core
        seed_release(temp_db, tmp_path=tmp_dir)
        result = cicd_service.cicd_first_new_app(
            temp_db,
            official_name=_OFFICIAL_NAME,
            repo_type="git",
            repo_name=_REPO_SHORT,
            branch=_BRANCH,
            submitter="rm",
            submitter_role="RM",
            submitter_display="",
            payload=self._BUILD_PAYLOAD,
            # No app_info_parsed
        )
        releases = core.list_releases(temp_db)
        unlocked = [r for r in releases if not r.get("released_locked")]
        rel_full = core.get_release(temp_db, unlocked[0]["id"])
        snap = rel_full["snapshots"].get(result["app_id"], {})
        assert not snap.get("owner_confirmed"), (
            "owner_confirmed should be falsy without app_info_parsed"
        )

    def test_version_empty_without_app_info(self, temp_db, tmp_dir):
        """Without app_info_parsed, snapshot.version stays at default ('')."""
        from release_system import core
        seed_release(temp_db, tmp_path=tmp_dir)
        result = cicd_service.cicd_first_new_app(
            temp_db,
            official_name=_OFFICIAL_NAME,
            repo_type="git",
            repo_name=_REPO_SHORT,
            branch=_BRANCH,
            submitter="rm",
            submitter_role="RM",
            submitter_display="",
            payload=self._BUILD_PAYLOAD,
        )
        releases = core.list_releases(temp_db)
        unlocked = [r for r in releases if not r.get("released_locked")]
        rel_full = core.get_release(temp_db, unlocked[0]["id"])
        snap = rel_full["snapshots"].get(result["app_id"], {})
        assert snap.get("version", "") == "", (
            f"version should be '' without app_info, got: {snap.get('version')}"
        )

    # --- HTTP contract: POST /api/cicd/apps/new with app_info_parsed ---

    def test_http_create_with_app_info_parsed_gives_200(self, db_path, tmp_dir):
        """POST /api/cicd/apps/new with app_info_parsed in body → 200."""
        import json as _json
        from fastapi.testclient import TestClient
        from app.db.connection import connect as app_connect

        # Seed release
        conn = app_connect(db_path)
        seed_release(conn, tmp_path=tmp_dir)
        conn.close()

        # Get the parsed blob via the service (mocked Gerrit)
        preview = cicd_service.preview_cicd_app_info(
            repo_type="git",
            repo_name=_REPO_SHORT,
            branch=_BRANCH,
            submitter_role="RM",
            _fetch_fn=_mock_fetch_fn,
        )

        # New wizard payload: official_name + repo + app_info_parsed + context fields
        new_wizard_payload = {
            "official_name": _OFFICIAL_NAME,
            "repo_type": "git",
            "repo_name": _REPO_SHORT,
            "branch": _BRANCH,
            "app_info_parsed": preview["parsed"],
            "app_info_commit_id": preview["commit_id"],
            # FE context fields — ignored by BE:
            "release_id": "some-release-id",
            "owner_username": "rm",
        }

        app = _make_app(db_path)
        with TestClient(app, raise_server_exceptions=False) as client:
            resp = client.post("/api/cicd/apps/new", json=new_wizard_payload)

        assert resp.status_code == 200, (
            f"Expected 200 with new wizard payload, got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        assert body["ok"] is True
        assert body["app_id"] == _APP_ID
        assert body["request"]["status"] == "pending"
        assert body["request"]["task_id"] is None


# ---------------------------------------------------------------------------
# make_fake_app_info_fetch — injectable factory for offline tests/e2e
# ---------------------------------------------------------------------------


class TestMakeFakeAppInfoFetch:
    """cicd_service.make_fake_app_info_fetch() returns a usable _fetch_fn."""

    def _preview_with_fake(self, **factory_kwargs):
        fetch = cicd_service.make_fake_app_info_fetch(**factory_kwargs)
        return cicd_service.preview_cicd_app_info(
            repo_type="git",
            repo_name=_REPO_SHORT,
            branch=_BRANCH,
            submitter_role="RM",
            _fetch_fn=fetch,
        )

    def test_returns_callable(self):
        fetch = cicd_service.make_fake_app_info_fetch()
        assert callable(fetch)

    def test_callable_signature(self):
        """Fake fetch must accept (git_url, branch, **kwargs) → (raw_json, commit_id)."""
        fetch = cicd_service.make_fake_app_info_fetch()
        raw_json, commit_id = fetch("ssh://example.com/repo", "main")
        assert isinstance(raw_json, str)
        assert isinstance(commit_id, str)

    def test_default_yields_parseable_app_info(self):
        """Default factory yields JSON that core.parse_app_info() can parse."""
        import json
        from release_system import core

        fetch = cicd_service.make_fake_app_info_fetch()
        raw_json, _ = fetch("ssh://example.com/repo", "main")
        data = json.loads(raw_json)
        assert "app_version" in data
        parsed = core.parse_app_info(raw_json)
        assert "app_version" in parsed

    def test_custom_app_version(self):
        result = self._preview_with_fake(app_version="9.9.9-custom")
        assert result["app_version"] == "9.9.9-custom"

    def test_custom_x86_chips(self):
        result = self._preview_with_fake(x86_chips=["C600", "N200"])
        assert "C600" in result["x86_chips"]

    def test_custom_commit_id(self):
        fetch = cicd_service.make_fake_app_info_fetch(
            commit_id="deadbeef" * 5
        )
        result = cicd_service.preview_cicd_app_info(
            repo_type="git",
            repo_name=_REPO_SHORT,
            branch=_BRANCH,
            submitter_role="RM",
            _fetch_fn=fetch,
        )
        assert result["commit_id"] == "deadbeef" * 5

    def test_fake_identity_unaffected(self):
        """Fake fetcher does not affect identity derivation."""
        result = self._preview_with_fake()
        assert result["git_url"] == _RESOLVED_URL
        assert result["git_branch"] == _BRANCH

    def test_unavailable_false_with_fake(self):
        """Fake fetcher succeeds → app_info_unavailable=False."""
        result = self._preview_with_fake()
        assert result["app_info_unavailable"] is False

    def test_fake_as_patch_target(self, db_path):
        """Fake fetch works as a patch target for HTTP-level tests."""
        from unittest.mock import patch
        from fastapi.testclient import TestClient

        fake = cicd_service.make_fake_app_info_fetch(app_version="3.0.0-http")
        app = _make_app(db_path)
        with TestClient(app, raise_server_exceptions=False) as client:
            with patch("app.integrations.gerrit.fetch_app_info", fake):
                resp = client.post(_PREVIEW_PATH, json=_PREVIEW_BODY)

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["app_version"] == "3.0.0-http"
        assert body["app_info_unavailable"] is False

    def test_cicd_first_with_fake_app_info(self, temp_db, tmp_dir):
        """Full chain: make_fake_app_info_fetch → preview → cicd_first_new_app."""
        from release_system import core
        from tests.conftest import seed_release

        seed_release(temp_db, tmp_path=tmp_dir)
        fetch = cicd_service.make_fake_app_info_fetch(
            app_version="2.5-e2e",
            x86_chips=["C500"],
            python_label="3.11",
        )
        preview = cicd_service.preview_cicd_app_info(
            repo_type="git",
            repo_name=_REPO_SHORT,
            branch=_BRANCH,
            submitter_role="RM",
            _fetch_fn=fetch,
        )
        assert preview["app_info_unavailable"] is False

        result = cicd_service.cicd_first_new_app(
            temp_db,
            official_name=_OFFICIAL_NAME,
            repo_type="git",
            repo_name=_REPO_SHORT,
            branch=_BRANCH,
            submitter="rm",
            submitter_role="RM",
            submitter_display="",
            payload={
                "build_product": [],
                "community_artifact": [],
                "build_image": "",
                "test_timeout": 40,
                "notes": "e2e fake",
            },
            app_info_parsed=preview["parsed"],
            app_info_commit_id=preview["commit_id"],
        )
        assert result["ok"] is True

        # Verify snapshot carries the fake app_info
        releases = core.list_releases(temp_db)
        unlocked = [r for r in releases if not r.get("released_locked")]
        rel_full = core.get_release(temp_db, unlocked[0]["id"])
        snap = rel_full["snapshots"][result["app_id"]]
        assert snap.get("version") == "2.5-e2e"
        assert snap.get("owner_confirmed") is True


# ---------------------------------------------------------------------------
# Manifest repo: needs_network=True, graceful identity failure
# (identity mocked to avoid 60-second git-archive network timeout)
# ---------------------------------------------------------------------------


class TestFetchPreviewManifest:
    """For .xml manifest paths: needs_network=True + graceful identity failure.

    app.identity.repo_to_git_identity is mocked to (None, None) to simulate
    the offline Gerrit-unreachable case without hitting the 60s timeout.
    """

    _MANIFEST_PATH = "APP/chroma/hpc_2024-7-devel.xml"
    _MANIFEST_BRANCH = "master"
    _RESOLVED_MANIFEST_URL = (
        "ssh://sw-gerrit-devops.metax-internal.com:29418/PDE/HPC/hpc_chroma"
    )

    def _preview_manifest_offline(self):
        """Simulate manifest identity resolution failure (network down)."""
        from unittest.mock import patch

        with patch(
            "app.identity.repo_to_git_identity",
            return_value=(None, None),
        ):
            return cicd_service.preview_cicd_app_info(
                repo_type="repo",
                repo_name=self._MANIFEST_PATH,
                branch=self._MANIFEST_BRANCH,
                submitter_role="RM",
                _fetch_fn=_mock_fetch_fn,
            )

    def _preview_manifest_online(self):
        """Simulate manifest identity resolved (network available)."""
        from unittest.mock import patch

        with patch(
            "app.identity.repo_to_git_identity",
            return_value=(self._RESOLVED_MANIFEST_URL, self._MANIFEST_BRANCH),
        ):
            return cicd_service.preview_cicd_app_info(
                repo_type="repo",
                repo_name=self._MANIFEST_PATH,
                branch=self._MANIFEST_BRANCH,
                submitter_role="RM",
                _fetch_fn=_mock_fetch_fn,
            )

    def test_manifest_sets_needs_network_true(self):
        """needs_network=True for .xml manifest repos."""
        result = self._preview_manifest_offline()
        assert result["needs_network"] is True

    def test_manifest_offline_no_exception(self):
        """Manifest identity failure (no network) → returns dict, no exception."""
        try:
            result = self._preview_manifest_offline()
            assert "app_info_unavailable" in result
        except ValueError:
            pytest.fail(
                "ValueError must not be raised for manifest identity failure"
                " — should return soft flag"
            )

    def test_manifest_offline_sets_unavailable(self):
        """Offline manifest → app_info_unavailable=True, git_url=None."""
        result = self._preview_manifest_offline()
        assert result["app_info_unavailable"] is True
        assert result.get("git_url") is None
        assert result.get("git_branch") is None

    def test_manifest_offline_has_error_string(self):
        """Offline manifest → app_info_error contains an explanation."""
        result = self._preview_manifest_offline()
        assert result.get("app_info_error"), "app_info_error must be set when offline"

    def test_manifest_online_needs_network_true(self):
        """needs_network stays True even when identity resolved (it's a manifest)."""
        result = self._preview_manifest_online()
        assert result["needs_network"] is True

    def test_manifest_online_returns_identity(self):
        """When manifest resolves and Gerrit content succeeds, returns full response."""
        result = self._preview_manifest_online()
        assert result["git_url"] == self._RESOLVED_MANIFEST_URL
        assert result["app_info_unavailable"] is False
        assert "app_version" in result

    def test_git_type_always_needs_network_false(self):
        """Short git-type names: offline identity → needs_network=False."""
        result = cicd_service.preview_cicd_app_info(
            repo_type="git",
            repo_name=_REPO_SHORT,
            branch=_BRANCH,
            submitter_role="RM",
            _fetch_fn=_mock_fetch_fn,
        )
        assert result["needs_network"] is False
