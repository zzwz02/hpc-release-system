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
        request(payload='{"status":{"old":"stopped","new":"running"}}')
    ) == "status"
    assert cicd_requests.blocker_kind(request(payload={"status": {}})) == "status"
    assert cicd_requests.blocker_kind(request()) == "modify"
    assert cicd_requests.blocker_kind(request(status="approved")) == ""
    assert cicd_requests.is_open(request(status="approved", delivery_status="returned"))


def test_status_direction_reads_the_lowercase_target_status() -> None:
    start = request(payload={"status": {"old": "stopped", "new": "running"}})
    stop = request(payload={"status": {"old": "running", "new": "stopped"}})
    assert cicd_requests.status_direction(start) == "start"
    assert cicd_requests.status_direction(stop) == "stop"
    assert cicd_requests.status_change(stop) == ("running", "stopped")
    assert cicd_requests.lifecycle_fields(stop)["status_direction"] == "stop"
    assert cicd_requests.status_direction(request()) == ""
    assert cicd_requests.status_direction(request(request_type="create")) == ""


def test_only_plain_no_jira_pending_modify_is_replaceable() -> None:
    assert cicd_requests.is_replaceable(request())
    assert not cicd_requests.is_replaceable(request(jira_id="HPC-123"))
    assert not cicd_requests.is_replaceable(request(request_type="create"))
    assert not cicd_requests.is_replaceable(request(status="approved"))
