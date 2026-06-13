"""Phase 0 / Phase 2 — golden parity gate.

Phase 0 (now):
    Validates that every captured golden file in responses/ is well-formed,
    has the canonical envelope schema including replay metadata, and that its
    body is already fully scrubbed (scrubber idempotency check).  No live
    server required.

Phase 2 (skeleton, currently skipped):
    Parametrised replay of every golden against the NEW FastAPI backend.
    When Phase 2 wires in a ``fastapi_base_url`` fixture, ``test_fastapi_parity``
    will automatically re-issue all 35 captured requests using the ``_method /
    _path / _params / _role / _body`` metadata stored in each golden file,
    scrub the live response, and assert exact equality against the golden body.

Run from repo root:
    pytest tests/golden/test_golden_replay.py -v
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

GOLDEN_DIR = Path(__file__).resolve().parent
RESPONSES_DIR = GOLDEN_DIR / "responses"

# Ensure scrub.py is importable when pytest is run from the repo root
if str(GOLDEN_DIR) not in sys.path:
    sys.path.insert(0, str(GOLDEN_DIR))

from scrub import is_scrubbed, scrub  # noqa: E402

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_goldens() -> list[tuple[str, dict]]:
    """Return [(name, record), ...] sorted by name for every golden file."""
    if not RESPONSES_DIR.exists():
        return []
    return [
        (p.stem, json.loads(p.read_text(encoding="utf-8")))
        for p in sorted(RESPONSES_DIR.glob("*.json"))
    ]


def _goldens_with_metadata() -> list[tuple[str, dict]]:
    """Return only golden files that carry replay metadata (_method/_path)."""
    return [
        (name, rec)
        for name, rec in _load_goldens()
        if rec.get("_method") and rec.get("_path")
    ]


# ---------------------------------------------------------------------------
# Phase 0 — golden file integrity
# ---------------------------------------------------------------------------


class TestGoldenFilesExist:
    """At least one golden file must be present (capture.py was run)."""

    def test_responses_dir_exists(self) -> None:
        assert RESPONSES_DIR.exists(), (
            f"responses/ directory missing: {RESPONSES_DIR}\n"
            "Run `python tests/golden/capture.py` first."
        )

    def test_at_least_one_golden_file(self) -> None:
        files = list(RESPONSES_DIR.glob("*.json"))
        assert files, (
            "No golden .json files found.\n"
            "Run `python tests/golden/capture.py` first."
        )


class TestGoldenEnvelopeSchema:
    """Each golden file must have the canonical envelope structure."""

    # Base keys present in every golden
    REQUIRED_KEYS = {"_golden_name", "_note", "_method", "_path", "_params",
                     "_role", "_body", "status", "body"}

    @pytest.mark.parametrize("name,record", _load_goldens())
    def test_envelope_has_required_keys(self, name: str, record: dict) -> None:
        missing = self.REQUIRED_KEYS - record.keys()
        assert not missing, f"{name}.json missing envelope keys: {missing}"

    @pytest.mark.parametrize("name,record", _load_goldens())
    def test_name_matches_filename(self, name: str, record: dict) -> None:
        assert record["_golden_name"] == name, (
            f"{name}.json: _golden_name={record['_golden_name']!r} != stem {name!r}"
        )

    @pytest.mark.parametrize("name,record", _load_goldens())
    def test_status_is_integer(self, name: str, record: dict) -> None:
        assert isinstance(record["status"], int), (
            f"{name}.json: status must be int, got {type(record['status'])}"
        )

    @pytest.mark.parametrize("name,record", _load_goldens())
    def test_method_is_get_or_post(self, name: str, record: dict) -> None:
        assert record.get("_method") in {"GET", "POST"}, (
            f"{name}.json: _method must be 'GET' or 'POST', got {record.get('_method')!r}"
        )

    @pytest.mark.parametrize("name,record", _load_goldens())
    def test_path_starts_with_slash(self, name: str, record: dict) -> None:
        path = record.get("_path", "")
        assert path.startswith("/"), (
            f"{name}.json: _path must start with '/', got {path!r}"
        )

    @pytest.mark.parametrize("name,record", _load_goldens())
    def test_post_body_present_for_posts(self, name: str, record: dict) -> None:
        if record.get("_method") == "POST":
            # _body must be a dict (possibly empty {}) for POST goldens —
            # None means the metadata was not populated.
            assert isinstance(record.get("_body"), dict), (
                f"{name}.json: POST golden must have _body dict, got {record.get('_body')!r}"
            )


class TestGoldenScrubberIdempotency:
    """Scrubbing an already-scrubbed golden must be a no-op."""

    @pytest.mark.parametrize("name,record", _load_goldens())
    def test_body_is_scrubbed(self, name: str, record: dict) -> None:
        assert is_scrubbed(record["body"]), (
            f"{name}.json: body is not fully scrubbed (or scrubber is not idempotent).\n"
            "Re-run capture.py to regenerate goldens."
        )

    def test_scrubber_replaces_ts_keys(self) -> None:
        obj: dict[str, Any] = {"created_at": "2026-06-14 10:00:00", "id": "app-1"}
        result = scrub(obj)
        assert result["created_at"] == "SCRUBBED_TIMESTAMP"
        assert result["id"] == "app-1", "stable id must not be scrubbed"

    def test_scrubber_replaces_now_key(self) -> None:
        """QA reports include a top-level 'now' timestamp — must be scrubbed."""
        obj: dict[str, Any] = {"now": "2026-06-14 10:00:00", "apps": []}
        result = scrub(obj)
        assert result["now"] == "SCRUBBED_TIMESTAMP"

    def test_scrubber_replaces_password_hash(self) -> None:
        obj: dict[str, Any] = {"username": "rm", "password_hash": "abc$def"}
        result = scrub(obj)
        assert result["password_hash"] == "SCRUBBED_HASH"
        assert result["username"] == "rm"

    def test_scrubber_replaces_token(self) -> None:
        obj: dict[str, Any] = {"token": "secret-abc", "role": "RM"}
        result = scrub(obj)
        assert result["token"] == "SCRUBBED_TOKEN"
        assert result["role"] == "RM"

    def test_scrubber_is_idempotent(self) -> None:
        obj: dict[str, Any] = {
            "created_at": "2026-06-14 10:00:00",
            "status": "Running",
            "nested": {"ts": "2026-06-14 09:59:00", "value": 42},
        }
        assert scrub(obj) == scrub(scrub(obj)), "Scrubber is not idempotent"

    def test_scrubber_handles_lists(self) -> None:
        obj: dict[str, Any] = {"items": [{"created_at": "2026-01-01", "id": "x"}]}
        result = scrub(obj)
        assert result["items"][0]["created_at"] == "SCRUBBED_TIMESTAMP"
        assert result["items"][0]["id"] == "x"


class TestGoldenHttpStatuses:
    """Spot-check that expected endpoints returned the right status codes."""

    EXPECTED_STATUS: dict[str, int] = {
        "post_login_admin": 200,
        "post_login_rm": 200,
        "post_login_owner": 200,
        "post_login_qa": 200,
        "get_me_unauthenticated": 200,
        "get_me_rm": 200,
        "get_ldap_status": 200,
        "get_state_rm": 200,
        "get_cicd_tasks": 200,
        "get_cicd_requests_rm": 200,
        "get_cicd_notifications_rm": 200,
        "get_qa_reports": 200,
        "get_wiki_articles": 200,
        "get_wiki_article_by_id": 200,
        "get_wiki_article_404": 404,
        "get_admin_users": 200,
        "post_qa_status_batch": 200,
        "post_apps_update_decision": 200,
        "post_logout": 200,
    }

    @pytest.mark.parametrize("name,expected_status", EXPECTED_STATUS.items())
    def test_expected_status(self, name: str, expected_status: int) -> None:
        p = RESPONSES_DIR / f"{name}.json"
        if not p.exists():
            pytest.skip(f"Golden file {name}.json not yet captured")
        record = json.loads(p.read_text(encoding="utf-8"))
        assert record["status"] == expected_status, (
            f"{name}.json: expected HTTP {expected_status}, got {record['status']}"
        )


class TestGoldenReplayMetadata:
    """Every golden with _method/_path must carry coherent replay metadata."""

    @pytest.mark.parametrize("name,record", _goldens_with_metadata())
    def test_role_is_known(self, name: str, record: dict) -> None:
        known = {"rm", "owner", "qa", "admin", ""}
        assert record.get("_role") in known, (
            f"{name}.json: _role={record.get('_role')!r} not in {known}"
        )

    @pytest.mark.parametrize("name,record", _goldens_with_metadata())
    def test_params_has_no_leading_question_mark(self, name: str, record: dict) -> None:
        params = record.get("_params") or ""
        assert not params.startswith("?"), (
            f"{name}.json: _params must not start with '?', got {params!r}"
        )


# ---------------------------------------------------------------------------
# Phase 2 replay harness — metadata-driven, currently skipped
# ---------------------------------------------------------------------------
#
# To activate in Phase 2:
#
#   1. impl-backend-core / impl-testbase adds a ``fastapi_base_url`` fixture to
#      tests/conftest.py that boots the new FastAPI app against the same seed
#      DB used by capture.py (same CSV, same users, same CICD/wiki state).
#
#   2. Remove the ``@pytest.mark.skip`` below.  The test parametrises over all
#      goldens that carry ``_method`` + ``_path`` replay metadata and re-issues
#      each request via httpx (trust_env=False — see proxy note in capture.py).
#
#   3. The parity assertion is:
#         scrub(live_response) == golden["body"]
#      A failure means a behavioural regression in the new backend.
#
# Important notes for Phase 2:
#   * Artifact / CSV goldens store a 500-char truncated preview in "body";
#     Phase 2 should compare the full text response, not the JSON preview.
#     Goldens for these endpoints have _path starting with /api/artifacts/ or
#     /api/test-scope.csv — detect them and handle separately.
#   * The login goldens (post_login_*) only assert {"ok": true}; do not replay
#     them as parity tests — use them only to obtain session cookies.
#   * Phase 2 must seed the FastAPI DB with the SAME deterministic data before
#     the first test runs (conftest session-scoped fixture).


@pytest.mark.skip(reason="Phase 2 — requires fastapi_base_url fixture from impl-backend-core")
@pytest.mark.phase2
@pytest.mark.parametrize("name,record", _goldens_with_metadata())
def test_fastapi_parity(
    name: str,
    record: dict,
    fastapi_base_url: str,  # injected by Phase 2 conftest
    fastapi_session_cookies: dict,  # injected by Phase 2 conftest: {role: cookie_str}
) -> None:
    """Re-issue the captured request against FastAPI and diff the response.

    Uses ``_method``, ``_path``, ``_params``, ``_role``, and ``_body`` from
    the golden envelope to reconstruct the original request without any
    hand-written request specs.
    """
    import httpx

    # Skip login goldens — they are used only to seed session cookies
    if record["_path"] == "/api/login":
        pytest.skip("Login goldens are used for cookie setup, not parity testing")

    method: str = record["_method"]
    path: str = record["_path"]
    params: str = record.get("_params") or ""
    role: str = record.get("_role") or ""
    post_body: dict | None = record.get("_body")

    url = f"{fastapi_base_url}{path}"
    if params:
        url = f"{url}?{params}"

    cookie = fastapi_session_cookies.get(role, "")
    headers: dict[str, str] = {"Content-Type": "application/json"}
    if cookie:
        headers["Cookie"] = cookie

    with httpx.Client(trust_env=False, timeout=15) as client:
        if method == "GET":
            resp = client.get(url, headers=headers, follow_redirects=False)
        else:
            resp = client.post(
                url,
                content=json.dumps(post_body or {}, ensure_ascii=False).encode("utf-8"),
                headers=headers,
                follow_redirects=False,
            )

    # For artifact / CSV endpoints the body is plain text, not JSON
    _text_paths = {"/api/test-scope.csv"}
    is_text_endpoint = path in _text_paths or path.startswith("/api/artifacts/")

    if is_text_endpoint:
        live_body: object = resp.text[:500] + (
            "...[truncated]" if len(resp.text) > 500 else ""
        )
    else:
        try:
            live_body = resp.json()
        except Exception:
            live_body = resp.text

    live_scrubbed = scrub(live_body)
    golden_body = record["body"]

    assert resp.status_code == record["status"], (
        f"{name}: expected HTTP {record['status']}, got {resp.status_code}"
    )
    assert live_scrubbed == golden_body, (
        f"Parity failure for {name} [{method} {path}]\n"
        f"Golden : {json.dumps(golden_body, ensure_ascii=False, indent=2)[:800]}\n"
        f"Live   : {json.dumps(live_scrubbed, ensure_ascii=False, indent=2)[:800]}"
    )
