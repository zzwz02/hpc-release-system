from __future__ import annotations

from app.domain import cicd_requests


def request(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "request_type": "modify",
        "origin": "cicd_workbench",
        "payload": {},
        "status": "pending",
        "delivery_status": "",
        "jira_id": "",
    }
    value.update(overrides)
    return value


def test_open_request_and_blocker_classification() -> None:
    assert cicd_requests.blocker_kind(request(request_type="create")) == "create"
    assert cicd_requests.blocker_kind(request(jira_id="HPC-123")) == "jira"
    assert cicd_requests.blocker_kind(
        request(payload='{"status":{"old":"Stopped","new":"Running"}}')
    ) == "status"
    assert cicd_requests.blocker_kind(request(payload={"status": {}})) == "status"
    assert cicd_requests.blocker_kind(request()) == "modify"
    assert cicd_requests.blocker_kind(request(status="approved")) == ""
    assert cicd_requests.is_open(request(status="approved", delivery_status="returned"))


def test_status_sync_direction() -> None:
    start = request(
        origin="release_decision_sync",
        payload={"status": {"old": "Stopped", "new": "Running"}},
    )
    stop = request(
        origin="release_decision_sync",
        payload={"status": {"old": "Running", "new": "Stopped"}},
    )
    assert cicd_requests.status_sync_direction(start) == "start"
    assert cicd_requests.status_sync_direction(stop) == "stop"
    assert cicd_requests.lifecycle_fields(stop)["status_sync_direction"] == "stop"


def test_only_plain_no_jira_pending_modify_is_replaceable() -> None:
    assert cicd_requests.is_replaceable(request())
    assert not cicd_requests.is_replaceable(request(jira_id="HPC-123"))
    assert not cicd_requests.is_replaceable(request(origin="release_decision_sync"))
    assert not cicd_requests.is_replaceable(request(status="approved"))


def test_onboarding_status_reuses_open_lifecycle_rule() -> None:
    assert cicd_requests.onboarding_status(request(request_type="create")) == "pending_create"
    assert cicd_requests.onboarding_status(
        request(request_type="create", status="approved", delivery_status="pending")
    ) == "pending_create"
    assert cicd_requests.onboarding_status(request(status="rejected")) == "rejected_create"
    assert cicd_requests.onboarding_status(request(status="cancelled")) == "cancelled_create"
    assert cicd_requests.onboarding_status(request(status="approved")) == "active"
