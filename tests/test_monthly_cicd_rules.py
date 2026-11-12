"""The one-way coupling between CICD status and monthly signups.

Rules (app/services/cicd_service.py docstring):

1. signing up requires CICD ``running``
2. no signup while a stop request is open
3. no stop request while the App has an active signup in an unpublished cycle
4. at most one open status request per App
7. a new App row is created only when its create request takes effect
"""
from __future__ import annotations

import pytest

from app.repositories import apps_repo, entries_repo
from app.services import cicd_service, cycle_service
from app.timeutil import beijing_timestamp
from tests.conftest import close_qa, seed_app, seed_cycle, seed_signup


def status_request(conn, app_id: str, target: str, *, user: str = "rm", role: str = "RM") -> dict:
    current = apps_repo.get_app(conn, app_id)["cicd_status"]
    return cicd_service.submit_request(
        conn,
        task_id=app_id,
        request_type="modify",
        payload={"status": {"old": current, "new": target}},
        submitter=user,
        submitter_role=role,
        source="app_workbench",
    )


def approve(conn, request_id: int, **kwargs) -> dict:
    return cicd_service.approve_request(
        conn, request_id, reviewer="rm", reviewer_role="RM", **kwargs
    )


# ---------------------------------------------------------------------------
# Rule 1 and 2 — signup side
# ---------------------------------------------------------------------------


def test_rule1_a_stopped_app_cannot_sign_up_until_a_start_request_takes_effect(temp_db) -> None:
    app_id = seed_app(temp_db, cicd_status="stopped")
    release_id = seed_cycle(temp_db)

    with pytest.raises(RuntimeError, match="CICD 已停止"):
        seed_signup(temp_db, release_id, app_id)

    request = status_request(temp_db, app_id, "running")
    # Pending approval is not enough: the App is still stopped.
    with pytest.raises(RuntimeError, match="CICD 已停止"):
        seed_signup(temp_db, release_id, app_id)

    approve(temp_db, request["id"])
    assert apps_repo.get_app(temp_db, app_id)["cicd_status"] == "running"
    assert seed_signup(temp_db, release_id, app_id)["status"] == "active"


def test_rule2_an_open_stop_request_blocks_a_new_signup(temp_db) -> None:
    app_id = seed_app(temp_db)
    release_id = seed_cycle(temp_db)
    request = status_request(temp_db, app_id, "stopped")

    with pytest.raises(RuntimeError, match=f"停止 CICD 申请 #{request['id']}"):
        seed_signup(temp_db, release_id, app_id)

    cicd_service.reject_request(
        temp_db, request["id"], reviewer="rm", reviewer_role="RM", review_note="先发完这版"
    )
    assert seed_signup(temp_db, release_id, app_id)["status"] == "active"


def test_an_open_start_request_does_not_block_a_signup_of_a_running_app(temp_db) -> None:
    """Only stop requests block: a start request on a running App cannot exist."""
    app_id = seed_app(temp_db)
    release_id = seed_cycle(temp_db)
    with pytest.raises(RuntimeError, match="已是 running"):
        status_request(temp_db, app_id, "running")
    assert seed_signup(temp_db, release_id, app_id)["status"] == "active"


# ---------------------------------------------------------------------------
# Rule 3 — CICD side
# ---------------------------------------------------------------------------


def test_rule3_a_signed_up_app_cannot_open_a_stop_request(temp_db) -> None:
    app_id = seed_app(temp_db)
    release_id = seed_cycle(temp_db, name="2099-01")
    entry = seed_signup(temp_db, release_id, app_id)

    with pytest.raises(RuntimeError, match="已报名未发布的版本（2099-01）"):
        status_request(temp_db, app_id, "stopped")

    cycle_service.withdraw(temp_db, entry["id"], user="rm", role="RM")
    assert status_request(temp_db, app_id, "stopped")["status"] == "pending"


def test_rule3_only_unpublished_cycles_block_a_stop_request(temp_db) -> None:
    app_id = seed_app(temp_db)
    release_id = seed_cycle(temp_db)
    seed_signup(temp_db, release_id, app_id)
    close_qa(temp_db, release_id)
    cycle_service.publish(temp_db, release_id, user="rm", role="RM")

    assert cycle_service.has_open_signup(temp_db, app_id) == []
    assert status_request(temp_db, app_id, "stopped")["status"] == "pending"


def test_rule3_invariant_is_checked_again_when_the_stop_request_takes_effect(temp_db) -> None:
    app_id = seed_app(temp_db)
    release_id = seed_cycle(temp_db, name="2099-01")
    request = status_request(temp_db, app_id, "stopped")
    approve(temp_db, request["id"], approval_mode="dispatch_spd")

    # A signup that slipped past the submit-time check (concurrent write) must
    # not be released by the delivery: the invariant is re-checked.
    entries_repo.insert_entry(
        temp_db,
        {
            "release_id": release_id,
            "app_id": app_id,
            "target_version": "1.0",
            "created_at": beijing_timestamp(),
            "created_by": "race",
        },
    )
    temp_db.commit()

    with pytest.raises(RuntimeError, match="停止申请不能生效"):
        cicd_service.deliver_request(temp_db, request["id"], deliverer="spd", deliverer_role="SPD")
    assert apps_repo.get_app(temp_db, app_id)["cicd_status"] == "running"


# ---------------------------------------------------------------------------
# Rule 4 — one open status request per App
# ---------------------------------------------------------------------------


def test_rule4_a_second_status_request_is_refused_while_one_is_open(temp_db) -> None:
    app_id = seed_app(temp_db)
    request = status_request(temp_db, app_id, "stopped")

    with pytest.raises(RuntimeError, match=f"申请 #{request['id']} 未完成"):
        status_request(temp_db, app_id, "stopped")
    # A config change is blocked by the open status request too.
    with pytest.raises(RuntimeError, match=f"申请 #{request['id']} 未完成"):
        cicd_service.submit_request(
            temp_db,
            task_id=app_id,
            request_type="modify",
            payload={"notes": {"old": "", "new": "x"}},
            submitter="rm",
            submitter_role="RM",
            source="app_workbench",
        )

    approve(temp_db, request["id"])
    assert status_request(temp_db, app_id, "running")["status"] == "pending"


def test_rule4_counts_requests_still_waiting_for_spd_delivery(temp_db) -> None:
    app_id = seed_app(temp_db)
    request = status_request(temp_db, app_id, "stopped")
    approve(temp_db, request["id"], approval_mode="dispatch_spd")

    assert cicd_service.open_status_request(temp_db, app_id)["id"] == request["id"]
    with pytest.raises(RuntimeError, match=f"申请 #{request['id']} 未完成"):
        cicd_service.submit_request(
            temp_db,
            task_id=app_id,
            request_type="modify",
            payload={"notes": {"old": "", "new": "x"}},
            submitter="rm",
            submitter_role="RM",
            source="app_workbench",
        )

    cicd_service.deliver_request(temp_db, request["id"], deliverer="spd", deliverer_role="SPD")
    assert apps_repo.get_app(temp_db, app_id)["cicd_status"] == "stopped"
    assert cicd_service.open_status_request(temp_db, app_id) is None
    assert status_request(temp_db, app_id, "running")["status"] == "pending"


def test_a_status_request_cannot_be_mixed_with_config_changes(temp_db) -> None:
    app_id = seed_app(temp_db)
    with pytest.raises(RuntimeError, match="需单独提交"):
        cicd_service.submit_request(
            temp_db,
            task_id=app_id,
            request_type="modify",
            payload={
                "status": {"old": "running", "new": "stopped"},
                "notes": {"old": "", "new": "x"},
            },
            submitter="rm",
            submitter_role="RM",
            source="app_workbench",
        )
    with pytest.raises(ValueError, match="running 或 stopped"):
        cicd_service.submit_request(
            temp_db,
            task_id=app_id,
            request_type="modify",
            payload={"status": {"old": "running", "new": "Stopped"}},
            submitter="rm",
            submitter_role="RM",
            source="app_workbench",
        )


# ---------------------------------------------------------------------------
# Rule 7 — the App appears with the create request
# ---------------------------------------------------------------------------


def _create_request(conn, *, name: str = "NewApp", repo: str = "hpc_new_app") -> dict:
    return cicd_service.submit_new_app(
        conn,
        official_name=name,
        repo_type="git",
        repo_name=repo,
        branch="maca",
        doc_target="manual",
        app_type="分子动力学",
        config={"cicd_build_image": "hpc/new:1"},
        submitter="owner_test",
        submitter_role="Owner",
    )["request"]


def test_rule7_the_app_row_appears_only_when_the_create_request_takes_effect(temp_db) -> None:
    request = _create_request(temp_db)
    assert apps_repo.list_apps(temp_db) == []

    approve(temp_db, request["id"])

    app = apps_repo.get_app(temp_db, "newapp")
    assert app["cicd_status"] == "running"
    assert app["owners"] == ["owner_test"]
    assert app["cicd_build_image"] == "hpc/new:1"


def test_rule7_a_cancelled_create_request_leaves_the_catalog_untouched(temp_db) -> None:
    request = _create_request(temp_db)
    cicd_service.cancel_request(temp_db, request["id"], username="owner_test", role="Owner")
    assert apps_repo.list_apps(temp_db) == []


def test_a_create_request_for_an_existing_identity_is_refused(temp_db) -> None:
    approve(temp_db, _create_request(temp_db)["id"])
    with pytest.raises(RuntimeError, match="已存在 app"):
        _create_request(temp_db)


def test_stopped_apps_are_left_out_of_sync_and_dependency_reads(temp_db) -> None:
    running = seed_app(temp_db, "running_app")
    stopped = seed_app(temp_db, "stopped_app", cicd_status="stopped")

    overview = catalog_dependency_ids(temp_db)
    assert running in overview
    assert stopped not in overview


def catalog_dependency_ids(conn) -> set[str]:
    from app.services import catalog_service

    overview = catalog_service.dependency_overview(conn)
    ids = {row["app_id"] for row in overview["undeclared"]}
    for group in overview["components"]:
        ids.update(row["app_id"] for row in group["apps"])
    return ids
