"""Pydantic models for API request/response envelopes.

Snapshot inner dicts remain loose (plain dict) per plan P1:
  "保持松散 dict（外层 envelope 校验，内层透传 mutate）"

# TODO Phase 2 — fill in full request/response models per endpoint mapping §3.2
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class OkResponse(BaseModel):
    """Generic success envelope."""

    ok: bool = True
    message: str = ""


class ErrorResponse(BaseModel):
    """Generic error envelope."""

    ok: bool = False
    error: str


class UserInfo(BaseModel):
    """Session user info returned by /api/whoami."""

    username: str
    role: str
    display_name: str = ""


class ReleaseListItem(BaseModel):
    """Minimal release list item for /api/releases."""

    id: str
    name: str
    maca_version: str = ""
    released_locked: bool = False
    created_at: str = ""

    # TODO Phase 2 — extend with phase, snapshot counts, etc.


class SnapshotData(BaseModel):
    """Outer envelope for snapshot operations; inner data_json is untyped."""

    release_id: str
    app_id: str
    data: dict[str, Any]

    # TODO Phase 2 — validate outer fields; inner 'data' remains loose
