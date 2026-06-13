"""Release decision constants and normalization — ported from core.py:495-533.

Pure functions: no HTTP, no DB, unit-testable in isolation.
"""
from __future__ import annotations

# Valid release decisions (canonical lowercase values stored in snapshots)
RELEASE_DECISIONS: frozenset[str] = frozenset({"release", "cicd_only", "stopped"})
NON_RELEASE_DECISIONS: frozenset[str] = frozenset({"cicd_only", "stopped"})

# Mapping from release_decision to CICD task status (plan §3.5 b)
# Upper-case status values per the plan's word-table.
DECISION_TO_CICD_STATUS: dict[str, str] = {
    "release": "Running",
    "cicd_only": "Running",
    "stopped": "Stopped",
}

# Valid CICD task statuses (upper-case per plan §4.1)
CICD_STATUSES: frozenset[str] = frozenset({"Running", "Stopped", "Abandoned"})

# The "running" boundary: decisions that map to a running CICD task
RUNNING_DECISIONS: frozenset[str] = frozenset({"release", "cicd_only"})
STOPPED_DECISIONS: frozenset[str] = frozenset({"stopped"})


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
    old_running = old_decision in RUNNING_DECISIONS
    new_running = new_decision in RUNNING_DECISIONS
    return old_running != new_running


def decision_to_cicd_status(decision: str) -> str:
    """Return the CICD task status that corresponds to a release decision."""
    return DECISION_TO_CICD_STATUS.get(decision, "Running")
