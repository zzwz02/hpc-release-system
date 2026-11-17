from __future__ import annotations

from app.api.routers.cicd import get_requests
from app.domain.access_actions import cicd_request_allowed_actions
from app.domain.permissions import (
    ALL_ROLES,
    capabilities_for_role,
    has_capability,
    roles_for_capability,
    roles_for_tab,
)
from app.services import state_service
from tests.conftest import seed_app, seed_cicd_request, seed_cycle


def test_shared_policy_contains_all_roles_and_critical_tab_rules() -> None:
    assert ALL_ROLES == ("RM", "Owner", "QA", "Guest", "Admin", "SPD")
    assert roles_for_tab("admin") == ("Admin",)
    assert roles_for_tab("jenkins-failures") == ("RM", "Owner", "SPD", "QA")
    assert roles_for_tab("cicd-assistant") == ("RM", "Owner", "SPD")
    assert roles_for_tab("jira-agent") == ("RM", "Owner")


def test_shared_capabilities_cover_operation_level_exceptions() -> None:
    assert roles_for_capability("qa.edit") == ("RM", "QA")
    assert roles_for_capability("wiki.edit") == ("RM",)
    assert roles_for_capability("artifact.generate") == ("RM", "Owner")
    assert roles_for_capability("cicd.delivery.return") == ("SPD",)
    assert roles_for_capability("app.app_info.sync") == ("RM", "Owner")
    assert roles_for_capability("app.app_info.expand_qa_scope") == ("QA",)
    assert roles_for_capability("notification.manage") == ("RM",)
    assert has_capability("RM", "cicd.request.approve")
    assert not has_capability("Admin", "cicd.request.approve")
    assert "app.edit.owned" in capabilities_for_role("Owner")


def test_cicd_actions_compose_capability_status_and_submitter() -> None:
    pending = {
        "status": "pending",
        "delivery_status": "",
        "submitter": "alice",
        "request_type": "modify",
        "payload": {},
    }
    assert cicd_request_allowed_actions(pending, role="Owner", username="alice") == [
        "cicd.request.cancel"
    ]
    assert cicd_request_allowed_actions(pending, role="Owner", username="bob") == []
    assert cicd_request_allowed_actions(pending, role="RM", username="rm") == [
        "cicd.request.approve",
        "cicd.request.reject",
        "cicd.request.cancel",
    ]


def test_returned_delivery_offers_the_rm_delivery_actions() -> None:
    returned = {
        "status": "approved",
        "delivery_status": "returned",
        "submitter": "owner_test",
        "request_type": "modify",
        "payload": {"status": {"old": "running", "new": "stopped"}},
    }
    assert cicd_request_allowed_actions(returned, role="RM", username="rm") == [
        "cicd.delivery.confirm",
        "cicd.delivery.redispatch",
        "cicd.delivery.apply_returned",
        "cicd.delivery.reject_returned",
    ]
    assert cicd_request_allowed_actions(returned, role="SPD", username="spd") == [
        "cicd.delivery.confirm"
    ]


def test_state_allowed_actions_are_opt_in(temp_db) -> None:
    seed_app(temp_db, "owned", owners=["owner_test"])
    seed_app(temp_db, "foreign", owners=["someone_else"])
    seed_cycle(temp_db)
    user = {"username": "owner_test", "role": "Owner", "display_name": ""}

    plain = state_service.get_state(temp_db, user=user)
    assert all("allowed_actions" not in app for app in plain["apps"])

    state = state_service.get_state(temp_db, user=user, include_allowed_actions=True)
    actions = {app["id"]: app["allowed_actions"] for app in state["apps"]}
    assert actions["owned"] == ["app.edit", "app.audit.view"]
    assert actions["foreign"] == []

    rm_state = state_service.get_state(
        temp_db,
        user={"username": "rm", "role": "RM", "display_name": ""},
        include_allowed_actions=True,
    )
    assert rm_state["apps"][0]["allowed_actions"] == [
        "app.edit",
        "app.edit.rm_fields",
        "app.audit.view",
    ]


def test_cicd_request_context_fields_are_opt_in(temp_db) -> None:
    app_id = seed_app(temp_db)
    seed_cicd_request(temp_db, app_id)
    user = {"username": "owner_test", "role": "Owner", "display_name": ""}

    plain = get_requests(
        only_mine="",
        task_id=None,
        status=None,
        since_days=None,
        include_allowed_actions=False,
        user=user,
        conn=temp_db,
    )["requests"][0]
    assert "blocker_kind" not in plain

    enriched = get_requests(
        only_mine="",
        task_id=None,
        status=None,
        since_days=None,
        include_allowed_actions=True,
        user=user,
        conn=temp_db,
    )["requests"][0]
    assert enriched["is_open"] is True
    assert enriched["blocker_kind"] == "modify"
    assert enriched["replaceable"] is True
    assert enriched["status_direction"] == ""
    assert enriched["allowed_actions"] == ["cicd.request.cancel"]
