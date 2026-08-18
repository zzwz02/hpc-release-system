"""Contextual actions derived from the shared static access-control policy.

The role-to-capability matrix lives in ``shared/access_control.json``.  This
module adds resource state (ownership, release lock, request status) without
duplicating role names.  API read models can expose these results so clients do
not have to reproduce backend business rules.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from app.domain.permissions import has_capability


def _status_change(payload: object) -> tuple[str, str]:
    if not isinstance(payload, Mapping):
        return "", ""
    change = payload.get("status")
    if not isinstance(change, Mapping):
        return "", ""
    return (
        str(change.get("old") or "").strip(),
        str(change.get("new") or "").strip(),
    )


def is_release_decision_sync_stop_request(request: Mapping[str, Any]) -> bool:
    """Whether a request is the non-rejectable Running -> Stopped sync."""
    if (
        request.get("origin") != "release_decision_sync"
        or request.get("request_type") != "modify"
    ):
        return False
    return _status_change(request.get("payload")) == ("Running", "Stopped")


def snapshot_allowed_actions(
    *,
    role: str,
    username: str,
    owners: Iterable[str] | None,
    release_locked: bool,
) -> list[str]:
    """Return contextual App snapshot actions for one user."""
    owner_names = set(owners or ())
    can_edit = has_capability(role, "app.edit.any") or (
        has_capability(role, "app.edit.owned") and username in owner_names
    )
    can_view_audit = has_capability(role, "app.audit.view.any") or (
        has_capability(role, "app.audit.view.owned") and username in owner_names
    )

    actions: list[str] = []
    if can_edit and not release_locked:
        actions.append("app.edit")
        if has_capability(role, "app.edit.rm_fields"):
            actions.append("app.edit.rm_fields")
    if can_view_audit:
        actions.append("app.audit.view")
    return actions

def cicd_request_allowed_actions(
    request: Mapping[str, Any],
    *,
    role: str,
    username: str,
) -> list[str]:
    """Return actions currently valid for a CICD request/delivery row."""
    status = str(request.get("status") or "")
    delivery_status = str(request.get("delivery_status") or "")
    stop_sync = is_release_decision_sync_stop_request(request)
    actions: list[str] = []

    if status == "pending":
        if has_capability(role, "cicd.request.approve"):
            actions.append("cicd.request.approve")
        if has_capability(role, "cicd.request.reject") and not stop_sync:
            actions.append("cicd.request.reject")
        can_cancel_others = has_capability(role, "cicd.request.approve")
        if (
            has_capability(role, "cicd.request.cancel")
            and (request.get("submitter") == username or can_cancel_others)
            and not stop_sync
        ):
            actions.append("cicd.request.cancel")

    if delivery_status in {"pending", "returned"} and has_capability(
        role, "cicd.delivery.confirm"
    ):
        actions.append("cicd.delivery.confirm")
    if delivery_status == "pending" and has_capability(role, "cicd.delivery.return"):
        actions.append("cicd.delivery.return")
    if delivery_status == "returned":
        for capability in (
            "cicd.delivery.redispatch",
            "cicd.delivery.apply_returned",
        ):
            if has_capability(role, capability):
                actions.append(capability)
        if (
            has_capability(role, "cicd.delivery.reject_returned")
            and not stop_sync
        ):
            actions.append("cicd.delivery.reject_returned")

    return actions
