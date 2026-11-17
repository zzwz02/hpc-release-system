"""Pure lifecycle classification for CICD requests."""
from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any, Literal

StatusDirection = Literal["start", "stop", ""]
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


def status_direction(
    request: Mapping[str, Any],
    payload: object | None = None,
) -> StatusDirection:
    """``start`` / ``stop`` for a CICD status request, else empty."""
    if request.get("request_type") != "modify":
        return ""
    _, new = status_change(request, payload)
    if new == "running":
        return "start"
    if new == "stopped":
        return "stop"
    return ""


def is_replaceable(request: Mapping[str, Any]) -> bool:
    """Whether an ordinary no-Jira pending modify may be replaced."""
    return (
        request.get("request_type") == "modify"
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
    return {
        "is_open": is_open(request),
        "blocker_kind": blocker_kind(request),
        "replaceable": is_replaceable(request),
        "status_direction": status_direction(request),
    }
