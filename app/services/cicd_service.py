"""CICD service — task and request management, R3 orchestration.

Key methods per plan §3.5:
  - create_task_request:  always creates a pending request (Ruling-B)
  - approve_request:      RM-only; may be self-approved
  - abandon_task:         RM-only direct action on Stopped tasks (Ruling-A)
  - sync_decision_to_cicd: called by app_service.update_snapshot when decision changes

# TODO Phase 2 — implement
"""
from __future__ import annotations

import sqlite3


def create_task_request(
    conn: sqlite3.Connection,
    *,
    submitter: str,
    submitter_role: str,
    payload: dict,
) -> dict:
    """Create a pending CICD task create/modify request (Ruling-B: always pending).

    # TODO Phase 2
    """
    raise NotImplementedError


def approve_request(
    conn: sqlite3.Connection,
    request_id: int,
    *,
    reviewer: str,
    approval_mode: str,
) -> dict:
    """Approve a pending CICD request (RM only; may self-approve).

    # TODO Phase 2
    """
    raise NotImplementedError


def abandon_task(
    conn: sqlite3.Connection,
    task_id: str,
    *,
    reviewer: str,
) -> dict:
    """RM direct action: transition a Stopped task to Abandoned.

    Only valid on Stopped tasks (Ruling-A gate).

    # TODO Phase 2
    """
    raise NotImplementedError


def sync_decision_to_cicd(
    conn: sqlite3.Connection,
    app_id: str,
    release_decision: str,
    *,
    submitter: str,
    origin: str = "release_decision_sync",
) -> dict | None:
    """Create a pending modify request to update the task's status.

    Called inside the same transaction as update_snapshot (plan §3.5 b).
    Returns None (no-op) if the task status already matches the target.

    # TODO Phase 2
    """
    raise NotImplementedError


def cicd_first_new_app(
    conn: sqlite3.Connection,
    *,
    repo_type: str,
    repo_name: str,
    branch: str,
    submitter: str,
    submitter_role: str,
    payload: dict,
) -> dict:
    """CICD-first app creation (plan §3.5 a, POST /api/cicd/apps/new).

    1. Resolve git identity via repo_to_git_identity (outside this call, caller's responsibility)
    2. find_by_identity gate
    3. One transaction: app + snapshot (cicd_only) + pending create request
    4. cicd_task row created only on RM approval

    # TODO Phase 2
    """
    raise NotImplementedError
