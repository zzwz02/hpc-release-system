"""Release decision constants and normalization — ported from core.py:495-533.

Pure functions: no HTTP, no DB, unit-testable in isolation.
"""
from __future__ import annotations

from app.domain.shared_metadata import mapping_section

_RELEASE_DECISION_METADATA = mapping_section("release_decisions")

# Valid release decisions (canonical lowercase values stored in snapshots)
RELEASE_DECISIONS: frozenset[str] = frozenset(_RELEASE_DECISION_METADATA)
NON_RELEASE_DECISIONS: frozenset[str] = frozenset({"cicd_only", "stopped"})

# Mapping from release_decision to CICD task status (plan §3.5 b)
# Upper-case status values per the plan's word-table.
DECISION_TO_CICD_STATUS: dict[str, str] = {
    decision: str(metadata["cicd_status"])
    for decision, metadata in _RELEASE_DECISION_METADATA.items()
}

# Valid CICD task statuses (upper-case per plan §4.1)
CICD_STATUSES: frozenset[str] = frozenset(DECISION_TO_CICD_STATUS.values())

# The "running" boundary: decisions that map to a running CICD task
RUNNING_DECISIONS: frozenset[str] = frozenset(
    decision
    for decision, status in DECISION_TO_CICD_STATUS.items()
    if status == "Running"
)
STOPPED_DECISIONS: frozenset[str] = RELEASE_DECISIONS - RUNNING_DECISIONS


def normalize_release_decision(value: str | None) -> str:
    """Normalize a release_decision value.

    Legacy 'no_release' maps to 'stopped'; everything else is returned as-is
    (defaulting to 'release' for empty/None).  Mirrors core.py:531-533.
    """
    decision = (value or "release").strip()
    return "stopped" if decision == "no_release" else decision


def crosses_running_stopped_boundary(old_decision: str, new_decision: str) -> bool:
    """True if the decision change crosses the running/stopped boundary.

    Used by the decision-sync logic (plan §3.5 b+) to decide whether to
    propagate across all unlocked releases and require owner confirmation.
    """
    return decision_to_cicd_status(old_decision) != decision_to_cicd_status(new_decision)


def decision_to_cicd_status(decision: str) -> str:
    """Return the CICD task status that corresponds to a release decision."""
    return DECISION_TO_CICD_STATUS.get(decision, "Running")
