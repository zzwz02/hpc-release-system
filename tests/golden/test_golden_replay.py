"""Phase 0 — golden parity gate (read-only validation).

This module does TWO things:

1. **Smoke-validates** the captured golden files: every .json in responses/
   must be loadable, have the expected envelope schema, and already be in
   scrubbed form (idempotency check on the scrubber).

2. Provides a **replay harness skeleton** (currently skipped) that Phase 2
   will complete: it will start the NEW FastAPI backend, re-issue the same
   requests, and diff the responses against these golden files.

Run from repo root:
    pytest tests/golden/test_golden_replay.py -v

No live server is required — Phase 0 tests only read files from disk.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# Make sure the project root is importable (for scrub.py and release_system)
GOLDEN_DIR = Path(__file__).resolve().parent
RESPONSES_DIR = GOLDEN_DIR / "responses"

# Ensure local imports work when pytest is run from the repo root
sys.path.insert(0, str(GOLDEN_DIR))

from scrub import scrub, is_scrubbed  # noqa: E402  (after sys.path tweak)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_goldens() -> list[tuple[str, dict]]:
    """Return [(name, record), ...] for every golden file."""
    files = sorted(RESPONSES_DIR.glob("*.json"))
    return [(p.stem, json.loads(p.read_text(encoding="utf-8"))) for p in files]


# ---------------------------------------------------------------------------
# Phase 0 tests — golden file integrity
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
        assert len(files) > 0, (
            "No golden .json files found.\n"
            "Run `python tests/golden/capture.py` first."
        )


class TestGoldenEnvelopeSchema:
    """Each golden file must have the canonical envelope structure."""

    REQUIRED_ENVELOPE_KEYS = {"_golden_name", "_note", "status", "body"}

    @pytest.mark.parametrize("name,record", _load_goldens() if RESPONSES_DIR.exists() else [])
    def test_envelope_has_required_keys(self, name: str, record: dict) -> None:
        missing = self.REQUIRED_ENVELOPE_KEYS - record.keys()
        assert not missing, f"{name}.json missing envelope keys: {missing}"

    @pytest.mark.parametrize("name,record", _load_goldens() if RESPONSES_DIR.exists() else [])
    def test_name_matches_filename(self, name: str, record: dict) -> None:
        assert record["_golden_name"] == name, (
            f"{name}.json: _golden_name={record['_golden_name']!r} != filename stem {name!r}"
        )

    @pytest.mark.parametrize("name,record", _load_goldens() if RESPONSES_DIR.exists() else [])
    def test_status_is_integer(self, name: str, record: dict) -> None:
        assert isinstance(record["status"], int), (
            f"{name}.json: status must be int, got {type(record['status'])}"
        )


class TestGoldenScrubberIdempotency:
    """Scrubbing an already-scrubbed golden must be a no-op.

    Verifies both that capture.py applied the scrubber correctly and that the
    scrubber itself is idempotent (double-scrub == single-scrub).
    """

    @pytest.mark.parametrize("name,record", _load_goldens() if RESPONSES_DIR.exists() else [])
    def test_body_is_scrubbed(self, name: str, record: dict) -> None:
        body = record["body"]
        assert is_scrubbed(body), (
            f"{name}.json: body does not appear to be scrubbed (or scrubber is not idempotent).\n"
            "Re-run capture.py to regenerate goldens."
        )

    def test_scrubber_replaces_known_ts_key(self) -> None:
        obj = {"created_at": "2026-06-14 10:00:00", "id": "app-1"}
        result = scrub(obj)
        assert result["created_at"] == "SCRUBBED_TIMESTAMP"
        assert result["id"] == "app-1", "stable id must not be scrubbed"

    def test_scrubber_replaces_password_hash(self) -> None:
        obj = {"username": "rm", "password_hash": "abc$def"}
        result = scrub(obj)
        assert result["password_hash"] == "SCRUBBED_HASH"
        assert result["username"] == "rm"

    def test_scrubber_replaces_token(self) -> None:
        obj = {"token": "secret-abc", "role": "RM"}
        result = scrub(obj)
        assert result["token"] == "SCRUBBED_TOKEN"
        assert result["role"] == "RM"

    def test_scrubber_is_idempotent(self) -> None:
        obj = {
            "created_at": "2026-06-14 10:00:00",
            "status": "Running",
            "nested": {"ts": "2026-06-14 09:59:00", "value": 42},
        }
        once = scrub(obj)
        twice = scrub(once)
        assert once == twice, "Scrubber is not idempotent"

    def test_scrubber_handles_lists(self) -> None:
        obj = {"items": [{"created_at": "2026-01-01", "id": "x"}]}
        result = scrub(obj)
        assert result["items"][0]["created_at"] == "SCRUBBED_TIMESTAMP"
        assert result["items"][0]["id"] == "x"


class TestGoldenHttpStatuses:
    """Spot-check that expected endpoints returned successful status codes."""

    # Map of golden name prefix → expected HTTP status
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

    def _get_record(self, name: str) -> dict | None:
        p = RESPONSES_DIR / f"{name}.json"
        if not p.exists():
            return None
        return json.loads(p.read_text(encoding="utf-8"))

    @pytest.mark.parametrize("name,expected_status", EXPECTED_STATUS.items())
    def test_expected_status(self, name: str, expected_status: int) -> None:
        record = self._get_record(name)
        if record is None:
            pytest.skip(f"Golden file {name}.json not yet captured")
        assert record["status"] == expected_status, (
            f"{name}.json: expected HTTP {expected_status}, got {record['status']}"
        )


# ---------------------------------------------------------------------------
# Phase 2 replay harness skeleton (all skipped until Phase 2)
# ---------------------------------------------------------------------------
#
# In Phase 2, ``conftest.py`` will provide a ``fastapi_base_url`` fixture that
# boots the new FastAPI backend against the same seed DB.  Each parametrised
# test below will:
#   1. Re-issue the same request (method + path + body stored in the golden).
#   2. Scrub the live response.
#   3. Assert it matches the golden body exactly (or within allowed deviations).
#
# The golden envelope will be extended in Phase 2 with:
#   "_method": "GET" | "POST"
#   "_path": "/api/..."
#   "_params": "release_id=..."   (optional)
#   "_role": "rm" | "owner" | ...  (which session to use)
#   "_body": {...}                  (POST payload, if any)
#
# Example (to be completed in Phase 2):
#
#   @pytest.mark.phase2
#   @pytest.mark.parametrize("name,record", _load_goldens())
#   def test_fastapi_parity(name, record, fastapi_base_url, session_cookies):
#       method = record.get("_method", "GET")
#       path = record["_path"]
#       role = record.get("_role", "rm")
#       cookie = session_cookies[role]
#       if method == "GET":
#           resp = httpx.get(f"{fastapi_base_url}{path}?{record.get('_params','')}", ...)
#       else:
#           resp = httpx.post(f"{fastapi_base_url}{path}", json=record.get("_body"), ...)
#       live_body = scrub(resp.json())
#       assert live_body == record["body"], parity_diff(record["body"], live_body)


@pytest.mark.skip(reason="Phase 2 replay harness — not yet implemented")
def test_phase2_fastapi_parity_placeholder() -> None:
    """Placeholder: Phase 2 will parametrize this against all goldens."""
    pass
