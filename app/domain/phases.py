"""Release lifecycle phase logic for the FastAPI runtime.

Pure functions: no HTTP, no DB, unit-testable in isolation.
"""
from __future__ import annotations

from typing import Any

from app.domain.shared_metadata import mapping_section
from app.timeutil import is_before, parse_deadline  # noqa: F401 (re-exported for callers)

_PHASE_METADATA = mapping_section("release_phases")

PHASES = tuple(
    phase
    for phase, _ in sorted(
        _PHASE_METADATA.items(),
        key=lambda item: int(item[1].get("order", -1)),
    )
)
if len(PHASES) != 4 or {
    int(metadata.get("order", -1))
    for metadata in _PHASE_METADATA.values()
    if isinstance(metadata, dict)
} != set(range(4)):
    raise RuntimeError("release_phases must define four unique orders from 0 to 3")

# Compatibility names are derived from the explicit lifecycle order above;
# phase identifiers themselves remain defined only in shared metadata.
BEFORE_APP_FREEZE, AFTER_APP_FREEZE, AFTER_DOC_DEADLINE, RELEASED_LOCKED = PHASES

PHASE_LABELS: dict[str, str] = {
    phase: str(metadata.get("label") or phase)
    for phase, metadata in _PHASE_METADATA.items()
}

# Single source of truth for "what is allowed in each release phase".
# Entry points consult this table instead of re-deriving rules from
# is_before(...) checks; that way every action's phase gating stays
# consistent and changes only need to land here.
_PHASE_POLICY: dict[str, set[str]] = {
    BEFORE_APP_FREEZE: {
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
    AFTER_APP_FREEZE: {
        "new_app_non_release",
        "lower_decision",
        "edit_release_decision",
        "edit_cicd_config", "edit_gerrit_identity",
        "edit_release_doc_fields", "edit_app_info", "edit_owner_confirmation",
        "edit_qa_status", "upload_qa_log",
        "edit_snapshot", "qa_set_status", "qa_upload_log",
    },
    AFTER_DOC_DEADLINE: {
        "new_app_non_release",
        "lower_decision",
        "edit_release_decision",
        "edit_cicd_config", "edit_gerrit_identity",
        "edit_qa_status", "upload_qa_log",
        "qa_set_status", "qa_upload_log",
    },
    RELEASED_LOCKED: set(),
}


def _has_trait(phase: str, trait: str) -> bool:
    metadata = _PHASE_METADATA.get(phase)
    return bool(isinstance(metadata, dict) and metadata.get(trait) is True)


def phase_label(phase: str) -> str:
    """Human label for a release phase; unknown values remain visible."""
    return PHASE_LABELS.get(phase, phase)


def is_before_app_freeze_phase(phase: str) -> bool:
    return _has_trait(phase, "before_app_freeze")


def is_before_doc_deadline_phase(phase: str) -> bool:
    return _has_trait(phase, "before_doc_deadline")


def is_qa_scope_frozen_phase(phase: str) -> bool:
    return _has_trait(phase, "qa_scope_frozen")


def current_phase(release: dict[str, Any]) -> str:
    """Derive the lifecycle phase of a release from its deadlines and lock flag."""
    if release.get("released_locked"):
        return RELEASED_LOCKED
    if not is_before(release.get("doc_deadline", "")):
        return AFTER_DOC_DEADLINE
    if not is_before(release.get("app_freeze_deadline", "")):
        return AFTER_APP_FREEZE
    return BEFORE_APP_FREEZE


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
