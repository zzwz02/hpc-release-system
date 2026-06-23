"""Decision-sync gating — R3 reimplementation of the release_decision sync
rule, kept out of the frozen ``release_system.core``.

Pure derivation lives here (``phase_label`` + ``resolve_synced_decision``); the
transactional apply and the dry-run preview live in
``app.services.app_service`` and consume these helpers. We mirror core's phase
machine via ``core.current_phase`` rather than re-deriving deadline checks.
Running/Stopped grouping is kept here too because CICD status is global while
release decisions are per release.

Changed rule vs. ``core.sync_decision_to_later_releases``:
  For each target, unlocked release that has the app —
    * target == ``release`` AND the later release is past app-freeze OR past
      doc-deadline  →  apply ``cicd_only`` (NOT skip, NOT release): never add
      QA/test scope to a frozen release; ``cicd_only`` keeps it running without
      expanding scope.
    * otherwise  →  apply the target decision verbatim.
  Locked releases and releases without the app are skipped.
  Optional owner-selected sync still targets later releases. Running/Stopped
  boundary changes force sync to every other unlocked release.
"""
from __future__ import annotations

# Phase string (from core.current_phase) → Chinese label shown in the dialog.
PHASE_LABELS: dict[str, str] = {
    "before_app_freeze": "App 冻结前",
    "after_app_freeze": "App 冻结后",
    "after_doc_deadline": "Doc deadline 后",
    "released_locked": "已最终锁定",
}

# Phases where a release can no longer take on new QA/test scope, so an
# upgrade *to* release is downgraded to cicd_only instead of applied verbatim.
FROZEN_PHASES: frozenset[str] = frozenset({"after_app_freeze", "after_doc_deadline"})
RUNNING_DECISIONS: frozenset[str] = frozenset({"release", "cicd_only"})


def phase_label(phase: str) -> str:
    """Human label for a release phase (falls back to the raw phase string)."""
    return PHASE_LABELS.get(phase, phase)


def resolve_synced_decision(target_decision: str, phase: str) -> str:
    """The decision actually applied to a later release given its *phase*.

    See the module docstring for the rule. ``target_decision`` and the returned
    value are canonical decisions (``release`` / ``cicd_only`` / ``stopped``).
    """
    if target_decision == "release" and phase in FROZEN_PHASES:
        return "cicd_only"
    return target_decision


def runtime_group(decision: str) -> str:
    """Decision group that drives the global CICD running/stopped status."""
    return "running" if decision in RUNNING_DECISIONS else "stopped"


def crosses_runtime_boundary(old_decision: str, new_decision: str) -> bool:
    """Whether a decision change flips CICD between Running and Stopped."""
    return runtime_group(old_decision) != runtime_group(new_decision)


def is_running_upgrade(old_decision: str, new_decision: str) -> bool:
    """Stopped -> Running transition."""
    return runtime_group(old_decision) == "stopped" and runtime_group(new_decision) == "running"


def is_running_downgrade(old_decision: str, new_decision: str) -> bool:
    """Running -> Stopped transition."""
    return runtime_group(old_decision) == "running" and runtime_group(new_decision) == "stopped"
