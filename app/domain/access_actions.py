"""Contextual actions derived from the shared static access-control policy.

The role-to-capability matrix lives in ``shared/access_control.json``.  This
module adds resource state (request status, submitter) without duplicating
role names.  API read models can expose these results so clients do
not have to reproduce backend business rules.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.domain.permissions import has_capability


def cicd_request_allowed_actions(
    request: Mapping[str, Any],
    *,
    role: str,
    username: str,
) -> list[str]:
    """Return actions currently valid for a CICD request/delivery row."""
    status = str(request.get("status") or "")
    delivery_status = str(request.get("delivery_status") or "")
    actions: list[str] = []

    if status == "pending":
        if has_capability(role, "cicd.request.approve"):
            actions.append("cicd.request.approve")
        if has_capability(role, "cicd.request.reject"):
            actions.append("cicd.request.reject")
        can_cancel_others = has_capability(role, "cicd.request.approve")
        if has_capability(role, "cicd.request.cancel") and (
            request.get("submitter") == username or can_cancel_others
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
        if has_capability(role, "cicd.delivery.reject_returned"):
            actions.append("cicd.delivery.reject_returned")

    return actions
