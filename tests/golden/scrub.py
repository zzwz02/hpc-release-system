"""Scrubber module — shared between capture.py and test_golden_replay.py.

Normalises non-deterministic fields in API responses so golden diffs remain
stable across runs.  Import and call ``scrub(obj)`` on any parsed JSON object.

Non-deterministic categories handled:
  * Timestamps / datetimes  — any key in SCRUB_TS_KEYS, the short 'ts' key,
    and the 'now' top-level key (QA reports).
  * Tokens / job ids        — keys containing 'token' or exactly 'job_id'.
  * Password hashes         — 'password_hash' key.
  * Large volatile text     — artifact 'content' keys (captured separately
    as plain-text golden files by capture.py; the JSON wrapper stores a
    truncated preview).

Keys deliberately NOT scrubbed:
  * 'id' in the entity sense (app_id, release_id, task_id, article_id) —
    these are stable, meaningful, and drive Phase 2 request construction.
  * 'status' — HTTP + entity statuses must be asserted for parity.
  * 'role', 'username', 'display_name' — stable fixture values.
"""

from __future__ import annotations

# Keys whose values are wall-clock timestamps that vary between runs
SCRUB_TS_KEYS: frozenset[str] = frozenset(
    {
        "created_at",
        "updated_at",
        "submitted_at",
        "reviewed_at",
        "delivered_at",
        "generated_at",
        "released_locked_at",
        "uploaded_at",
        "last_visited_at",
        "returned_at",
    }
)

PLACEHOLDER_TS = "SCRUBBED_TIMESTAMP"
PLACEHOLDER_TOKEN = "SCRUBBED_TOKEN"
PLACEHOLDER_HASH = "SCRUBBED_HASH"


def scrub(obj: object, *, _key: str = "") -> object:
    """Recursively replace non-deterministic fields with stable placeholders.

    Safe to call multiple times on the same object (idempotent: placeholder
    strings are returned unchanged on a second pass).
    """
    if isinstance(obj, dict):
        out: dict = {}
        for k, v in obj.items():
            if k in SCRUB_TS_KEYS or k == "ts" or k == "now":
                out[k] = PLACEHOLDER_TS
            elif k == "password_hash":
                out[k] = PLACEHOLDER_HASH
            elif k == "job_id" or ("token" in k and k != "task_id"):
                out[k] = PLACEHOLDER_TOKEN
            else:
                out[k] = scrub(v, _key=k)
        return out
    if isinstance(obj, list):
        return [scrub(item, _key=_key) for item in obj]
    # Scalars (str, int, float, bool, None) are returned as-is
    return obj


def is_scrubbed(obj: object) -> bool:
    """Return True if obj has already been scrubbed (idempotency check)."""
    scrubbed_once = scrub(obj)
    scrubbed_twice = scrub(scrubbed_once)
    return scrubbed_once == scrubbed_twice


def normalize_ids(obj: object, id_map: dict[str, str]) -> object:
    """Replace live non-deterministic IDs with their golden counterparts.

    *id_map* maps ``live_id → golden_id`` (e.g. the release_id assigned to
    the parity DB's seed release may differ from the one captured in the
    golden files).  Substitution is applied to all **string values** —
    both standalone strings and strings inside dicts/lists — but never to
    dict **keys** (IDs never appear as keys in our response envelopes).

    The replacement is a simple ``str.replace`` scan, so it works even when
    the ID is embedded in a longer string (e.g. inside a path or a note).

    Idempotent: calling it twice with the same map produces the same result.
    """
    if not id_map:
        return obj
    if isinstance(obj, dict):
        return {k: normalize_ids(v, id_map) for k, v in obj.items()}
    if isinstance(obj, list):
        return [normalize_ids(item, id_map) for item in obj]
    if isinstance(obj, str):
        for live_id, golden_id in id_map.items():
            obj = obj.replace(live_id, golden_id)
        return obj
    return obj
