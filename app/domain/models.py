"""Pydantic models for API request/response envelopes.

Snapshot inner dicts remain loose (plain dict) per plan P1:
  "保持松散 dict（外层 envelope 校验，内层透传 mutate）"

All timestamps are naive Beijing strings ("%Y-%m-%d %H:%M:%S") per §5.4.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, field_validator


# ---------------------------------------------------------------------------
# Generic envelopes
# ---------------------------------------------------------------------------

class OkResponse(BaseModel):
    """Generic success envelope."""

    ok: bool = True
    message: str = ""


class ErrorResponse(BaseModel):
    """Generic error envelope."""

    ok: bool = False
    error: str


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

class LoginRequest(BaseModel):
    username: str
    password: str


class UserInfo(BaseModel):
    """Session user info returned by /api/whoami."""

    username: str
    role: str
    display_name: str = ""
    auth_source: str = "local"


class ChangePasswordRequest(BaseModel):
    old_password: str
    new_password: str


# ---------------------------------------------------------------------------
# Releases
# ---------------------------------------------------------------------------

class ReleaseListItem(BaseModel):
    """Minimal release list item for /api/releases (R2 per-section refresh)."""

    id: str
    name: str
    maca_version: str = ""
    app_freeze_deadline: str = ""
    doc_deadline: str = ""
    released_locked: bool = False
    released_locked_at: str = ""
    released_locked_by: str = ""
    created_at: str = ""
    source: str = ""
    cloned_from: str = ""
    phase: str = ""  # derived, not stored


class NewReleaseRequest(BaseModel):
    name: str
    maca_version: str = ""
    app_freeze_deadline: str = ""
    doc_deadline: str = ""
    source: str = "manual"


class CloneReleaseRequest(BaseModel):
    source_release_id: str
    name: str


class UpdateDeadlinesRequest(BaseModel):
    release_id: str
    name: str | None = None
    app_freeze_deadline: str | None = None
    doc_deadline: str | None = None


# ---------------------------------------------------------------------------
# Apps / Snapshots
# ---------------------------------------------------------------------------

class NewAppRequest(BaseModel):
    """Body for POST /api/apps/new."""

    release_id: str
    official_name: str
    git_url: str
    git_branch: str
    release_decision: str = "release"
    doc_target: str = "manual"
    owner: str  # submitting owner username


class UpdateSnapshotRequest(BaseModel):
    """Body for POST /api/apps/update.

    Inner 'fields' dict is kept loose per plan P1 — only outer envelope validated.
    """

    release_id: str
    app_id: str
    fields: dict[str, Any]


class SnapshotData(BaseModel):
    """Snapshot envelope for read responses."""

    release_id: str
    app_id: str
    data: dict[str, Any]
    git_url: str = ""
    git_branch: str = ""


class TransferOwnerRequest(BaseModel):
    release_id: str
    app_id: str
    new_owner: str


# ---------------------------------------------------------------------------
# QA
# ---------------------------------------------------------------------------

class SetQaStatusRequest(BaseModel):
    release_id: str
    app_id: str
    qa_status: str

    @field_validator("qa_status")
    @classmethod
    def valid_qa_status(cls, v: str) -> str:
        allowed = {"not_checked", "qa_passed", "has_issues", "cannot_release"}
        if v not in allowed:
            raise ValueError(f"qa_status must be one of {sorted(allowed)}")
        return v


# ---------------------------------------------------------------------------
# CICD
# ---------------------------------------------------------------------------

class CicdCreateRequest(BaseModel):
    """Body for CICD task create request (Ruling-B: always becomes pending)."""

    app_name: str
    app_version: str = ""
    repo_type: str = "git"
    repo_name: str
    branch: str
    build_product: list[Any] = []
    community_artifact: list[Any] = []
    build_image: str = ""
    test_timeout: int = 40
    owner_username: str
    notes: str = ""


class CicdModifyRequest(BaseModel):
    """Body for CICD task modify request.

    payload is {field: {old: ..., new: ...}}.
    'status' is rejected by the service layer (Ruling-A/V3).
    """

    task_id: str
    payload: dict[str, Any]
    notes: str = ""


class CicdFirstNewAppRequest(BaseModel):
    """Body for POST /api/cicd/apps/new (R3 CICD-first).

    git_url and git_branch are NOT in the body — they are derived from
    repo_type/repo_name/branch by the service via repo_to_git_identity().
    """

    repo_type: str = "git"
    repo_name: str
    branch: str
    app_name: str
    build_product: list[Any] = []
    community_artifact: list[Any] = []
    build_image: str = ""
    test_timeout: int = 40
    owner_username: str
    notes: str = ""
    release_id: str  # which release to create the initial snapshot in


class ApproveRequestBody(BaseModel):
    request_id: int
    review_note: str = ""
    approval_mode: str = "immediate"  # "immediate" | "dispatch_spd"
    jira_id: str = ""
    jira_auto_created: int = 0


class RejectRequestBody(BaseModel):
    request_id: int
    review_note: str


class CancelRequestBody(BaseModel):
    request_id: int


class AbandonTaskRequest(BaseModel):
    """Body for POST /api/cicd/tasks/abandon (Ruling-A, RM only)."""

    task_id: str


# ---------------------------------------------------------------------------
# Release schedule
# ---------------------------------------------------------------------------

class UpsertScheduleRequest(BaseModel):
    entry_id: str | None = None
    version: str
    branch_cut_at: str = ""
    release_at: str = ""
    note: str = ""


# ---------------------------------------------------------------------------
# Admin
# ---------------------------------------------------------------------------

class CreateUserRequest(BaseModel):
    username: str
    password: str
    role: str


class UpdateUserRoleRequest(BaseModel):
    username: str
    role: str


# ---------------------------------------------------------------------------
# Wiki
# ---------------------------------------------------------------------------

class WikiArticleRequest(BaseModel):
    title: str
    body_md: str = ""
    pinned: bool = False


class WikiArticleUpdateRequest(BaseModel):
    article_id: str
    title: str
    body_md: str = ""
    pinned: bool = False
