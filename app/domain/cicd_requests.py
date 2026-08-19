"""Pure lifecycle classification for CICD requests."""
from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any, Literal

StatusSyncDirection = Literal["start", "stop", ""]
BlockerKind = Literal["create", "jira", "status", "modify", ""]

_OPEN_DELIVERY_STATUSES = frozenset({"pending", "returned"})


def payload_object(
    request: Mapping[str, Any],
    payload: object | None = None,
) -> Mapping[str, Any]:
    """Return a request payload as a mapping, accepting DB JSON strings."""
    raw = request.get("payload") if payload is None else payload
    if isinstance(raw, Mapping):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw or "{}")
        except (TypeError, ValueError):
            return {}
        return parsed if isinstance(parsed, Mapping) else {}
    return {}


def status_change(
    request: Mapping[str, Any],
    payload: object | None = None,
) -> tuple[str, str]:
    change = payload_object(request, payload).get("status")
    if not isinstance(change, Mapping):
        return "", ""
    return (
        str(change.get("old") or "").strip(),
        str(change.get("new") or "").strip(),
    )


def has_status_change(
    request: Mapping[str, Any],
    payload: object | None = None,
) -> bool:
    return isinstance(payload_object(request, payload).get("status"), Mapping)


def is_open(request: Mapping[str, Any]) -> bool:
    """Whether approval or delivery work is still unfinished."""
    return request.get("status") == "pending" or request.get(
        "delivery_status"
    ) in _OPEN_DELIVERY_STATUSES


def status_sync_direction(
    request: Mapping[str, Any],
    payload: object | None = None,
) -> StatusSyncDirection:
    """Classify App decision-driven Running/Stopped synchronization."""
    if (
        request.get("origin") != "release_decision_sync"
        or request.get("request_type") != "modify"
    ):
        return ""
    change = status_change(request, payload)
    if change == ("Stopped", "Running"):
        return "start"
    if change == ("Running", "Stopped"):
        return "stop"
    return ""


def is_release_decision_sync_start_request(
    request: Mapping[str, Any],
    payload: object | None = None,
) -> bool:
    return status_sync_direction(request, payload) == "start"


def is_release_decision_sync_stop_request(
    request: Mapping[str, Any],
    payload: object | None = None,
) -> bool:
    return status_sync_direction(request, payload) == "stop"


def is_replaceable(request: Mapping[str, Any]) -> bool:
    """Whether an ordinary no-Jira pending modify may be replaced."""
    return (
        request.get("request_type") == "modify"
        and request.get("origin") == "cicd_workbench"
        and request.get("status") == "pending"
        and not str(request.get("jira_id") or "").strip()
    )


def blocker_kind(request: Mapping[str, Any]) -> BlockerKind:
    """Classify the App-workbench blocker represented by an open request."""
    if not is_open(request):
        return ""
    if request.get("request_type") == "create":
        return "create"
    if request.get("request_type") != "modify":
        return ""
    if str(request.get("jira_id") or "").strip():
        return "jira"
    if has_status_change(request):
        return "status"
    return "modify"


def lifecycle_fields(request: Mapping[str, Any]) -> dict[str, object]:
    """Fields exposed to clients that must not reimplement lifecycle rules."""
    direction = status_sync_direction(request)
    return {
        "is_open": is_open(request),
        "blocker_kind": blocker_kind(request),
        "replaceable": is_replaceable(request),
        "status_sync_direction": direction,
    }


def onboarding_status(request: Mapping[str, Any]) -> str:
    """Derive the App-facing state of a CICD-first create request."""
    if is_open(request):
        return "pending_create"
    if request.get("status") == "rejected":
        return "rejected_create"
    if request.get("status") == "cancelled":
        return "cancelled_create"
    return "active"
