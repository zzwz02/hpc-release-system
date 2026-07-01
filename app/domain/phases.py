"""Release lifecycle phase logic for the FastAPI runtime.

Pure functions: no HTTP, no DB, unit-testable in isolation.
"""
from __future__ import annotations

from typing import Any

from app.timeutil import is_before, parse_deadline  # noqa: F401 (re-exported for callers)

PHASES = ("before_app_freeze", "after_app_freeze", "after_doc_deadline", "released_locked")

# Single source of truth for "what is allowed in each release phase".
# Entry points consult this table instead of re-deriving rules from
# is_before(...) checks; that way every action's phase gating stays
# consistent and changes only need to land here.
_PHASE_POLICY: dict[str, set[str]] = {
    "before_app_freeze": {
        "new_app_release", "new_app_non_release",
        "raise_to_release", "lower_decision",
        "edit_release_decision",
        "edit_cicd_config", "edit_gerrit_identity",
        "edit_release_doc_fields", "edit_app_info", "edit_owner_confirmation",
        "expand_qa_scope",
        "edit_qa_status", "upload_qa_log",
        # Legacy aliases retained for older call sites. New code should prefer
        # the granular actions above so phase policy remains explicit.
        "edit_snapshot", "qa_set_status", "qa_upload_log",
    },
    "after_app_freeze": {
        "new_app_non_release",
        "lower_decision",
        "edit_release_decision",
        "edit_cicd_config", "edit_gerrit_identity",
        "edit_release_doc_fields", "edit_app_info", "edit_owner_confirmation",
        "edit_qa_status", "upload_qa_log",
        "edit_snapshot", "qa_set_status", "qa_upload_log",
    },
    "after_doc_deadline": {
        "new_app_non_release",
        "lower_decision",
        "edit_release_decision",
        "edit_cicd_config", "edit_gerrit_identity",
        "edit_qa_status", "upload_qa_log",
        "qa_set_status", "qa_upload_log",
    },
    "released_locked": set(),
}


def current_phase(release: dict[str, Any]) -> str:
    """Derive the lifecycle phase of a release from its deadlines and lock flag."""
    if release.get("released_locked"):
        return "released_locked"
    if not is_before(release.get("doc_deadline", "")):
        return "after_doc_deadline"
    if not is_before(release.get("app_freeze_deadline", "")):
        return "after_app_freeze"
    return "before_app_freeze"


def can(release_or_phase: dict[str, Any] | str, action: str) -> bool:
    """True if *action* is allowed in the given release's current phase.

    Accepts either a release dict (phase is derived) or a phase string.
    Unknown actions return False so a typo at a call site fails closed
    rather than silently allowing the write.
    """
    if isinstance(release_or_phase, dict):
        phase = current_phase(release_or_phase)
    else:
        phase = str(release_or_phase)
    return action in _PHASE_POLICY.get(phase, set())


def require_can(release: dict[str, Any], action: str, message: str) -> None:
    """Raise RuntimeError with *message* if the release's phase forbids *action*."""
    if not can(release, action):
        raise RuntimeError(message)
