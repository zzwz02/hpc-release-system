"""Monthly release cycle phases and the actions each phase allows.

Pure functions: no HTTP, no DB, unit-testable in isolation.

A cycle moves through four phases, derived from its dates and lock flag:

* ``signup``      — before the signup deadline
* ``in_progress`` — signup deadline .. QA deadline
* ``qa_closed``   — QA deadline .. published
* ``released``    — published (locked); RM may unlock for corrections
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
if len(PHASES) != 4:
    raise RuntimeError("release_phases must define four phases")

SIGNUP, IN_PROGRESS, QA_CLOSED, RELEASED = PHASES

PHASE_LABELS: dict[str, str] = {
    phase: str(metadata.get("label") or phase)
    for phase, metadata in _PHASE_METADATA.items()
}

# Single source of truth for "what is allowed in each phase".  Call sites ask
# can()/require_can() instead of comparing dates themselves.
_PHASE_POLICY: dict[str, set[str]] = {
    SIGNUP: {
        "signup",                # new signup or re-signup after withdrawal
        "edit_entry_plan",       # base image, target version, planned features
        "exclude_tests",         # Owner removes tests from scope
        "include_tests",         # Owner puts removed tests back
        "withdraw",
        "edit_environment",      # RM: environment rows and base images
        "edit_deliverable",      # Owner: actual delivery / known issues
        "edit_qa",               # QA: verdict, note, digest
        "upload_qa_log",
    },
    IN_PROGRESS: {
        "exclude_tests",
        "withdraw",
        "edit_deliverable",
        "edit_qa",
        "upload_qa_log",
        "decide_scope_request",
    },
    QA_CLOSED: {
        "withdraw",
        "edit_deliverable",
        "edit_qa",               # QA finalises verdicts until RM publishes
        "upload_qa_log",
        "decide_scope_request",
        "publish",
    },
    RELEASED: {"unlock"},
}


def phase_label(phase: str) -> str:
    return PHASE_LABELS.get(phase, phase)


def current_phase(release: dict[str, Any]) -> str:
    """Derive the phase of a cycle from its deadlines and lock flag."""
    if release.get("released_locked"):
        return RELEASED
    if is_before(release.get("signup_deadline", "")):
        return SIGNUP
    if is_before(release.get("qa_deadline", "")):
        return IN_PROGRESS
    return QA_CLOSED


def can(release_or_phase: dict[str, Any] | str, action: str) -> bool:
    """True if *action* is allowed in the cycle's current phase.

    Unknown actions return False so a typo fails closed.
    """
    if isinstance(release_or_phase, dict):
        phase = current_phase(release_or_phase)
    else:
        phase = str(release_or_phase)
    return action in _PHASE_POLICY.get(phase, set())


def require_can(release: dict[str, Any], action: str, message: str) -> None:
    """Raise RuntimeError with *message* if the cycle's phase forbids *action*."""
    if not can(release, action):
        raise RuntimeError(message)


def is_open(release: dict[str, Any]) -> bool:
    """True for cycles that are not yet published."""
    return current_phase(release) != RELEASED
