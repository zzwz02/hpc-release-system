from __future__ import annotations

from app.domain.access_actions import (
    cicd_request_allowed_actions,
    snapshot_allowed_actions,
)
from app.domain.permissions import (
    ALL_ROLES,
    capabilities_for_role,
    has_capability,
    roles_for_capability,
    roles_for_tab,
)
from app.services.app_service import get_state
from tests.conftest import seed_release


def test_shared_policy_contains_all_roles_and_critical_tab_rules() -> None:
    assert ALL_ROLES == ("RM", "Owner", "QA", "Guest", "Admin", "SPD")
    assert roles_for_tab("admin") == ("Admin",)
    assert roles_for_tab("jenkins-failures") == ("RM", "Owner", "SPD", "QA")
    assert roles_for_tab("cicd-assistant") == ("RM", "Owner", "SPD")


def test_shared_capabilities_cover_operation_level_exceptions() -> None:
    assert roles_for_capability("qa.edit") == ("RM", "QA")
    assert roles_for_capability("wiki.edit") == ("RM",)
    assert roles_for_capability("artifact.generate") == ("RM", "Owner")
    assert roles_for_capability("cicd.delivery.return") == ("SPD",)
    assert has_capability("RM", "cicd.request.approve")
    assert not has_capability("Admin", "cicd.request.approve")
    assert "app.edit.owned" in capabilities_for_role("Owner")


def test_snapshot_actions_compose_capability_ownership_and_lock() -> None:
    assert snapshot_allowed_actions(
        role="Owner",
        username="alice",
        owners=["alice"],
        release_locked=False,
    ) == ["app.edit", "app.audit.view"]
    assert snapshot_allowed_actions(
        role="Owner",
        username="bob",
        owners=["alice"],
        release_locked=False,
    ) == []
    assert snapshot_allowed_actions(
        role="RM",
        username="rm",
        owners=[],
        release_locked=True,
    ) == ["app.audit.view"]


def test_cicd_actions_compose_capability_status_and_submitter() -> None:
    pending = {
        "status": "pending",
        "delivery_status": "",
        "submitter": "alice",
        "request_type": "modify",
        "origin": "cicd_workbench",
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


def test_stop_sync_action_cannot_be_rejected_or_cancelled() -> None:
    request = {
        "status": "pending",
        "delivery_status": "returned",
        "submitter": "owner",
        "request_type": "modify",
        "origin": "release_decision_sync",
        "payload": {"status": {"old": "Running", "new": "Stopped"}},
    }
    assert cicd_request_allowed_actions(request, role="RM", username="rm") == [
        "cicd.request.approve",
        "cicd.delivery.confirm",
        "cicd.delivery.redispatch",
        "cicd.delivery.apply_returned",
    ]


def test_state_allowed_actions_are_opt_in(temp_db, tmp_dir) -> None:
    seed_release(temp_db, tmp_path=tmp_dir)
    user = {"username": "test_owner", "role": "Owner", "display_name": ""}

    legacy_state = get_state(temp_db, user=user)
    assert all(
        "allowed_actions" not in snapshot
        for snapshot in legacy_state["release"]["snapshots"].values()
    )

    state = get_state(temp_db, user=user, include_allowed_actions=True)
    assert all(
        snapshot["allowed_actions"] == ["app.edit", "app.audit.view"]
        for snapshot in state["release"]["snapshots"].values()
    )
