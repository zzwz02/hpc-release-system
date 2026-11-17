from __future__ import annotations

from app.domain import phases


def test_shared_phase_catalog_is_complete_and_labeled() -> None:
    assert phases.PHASES == ("signup", "in_progress", "qa_closed", "released")
    assert (phases.SIGNUP, phases.IN_PROGRESS, phases.QA_CLOSED, phases.RELEASED) == phases.PHASES
    assert phases.phase_label("qa_closed") == "QA 已截止"
    assert phases.phase_label("unknown") == "unknown"


def test_phase_is_derived_from_the_cycle_deadlines_and_lock() -> None:
    cycle = {
        "signup_deadline": "2099-01-01 23:59",
        "qa_deadline": "2099-01-26 23:59",
        "release_date": "2099-01-31",
        "released_locked": False,
    }
    assert phases.current_phase(cycle) == phases.SIGNUP
    assert phases.current_phase({**cycle, "signup_deadline": "2000-01-01 23:59"}) == phases.IN_PROGRESS
    assert (
        phases.current_phase(
            {**cycle, "signup_deadline": "2000-01-01 23:59", "qa_deadline": "2000-01-26 23:59"}
        )
        == phases.QA_CLOSED
    )
    assert phases.current_phase({**cycle, "released_locked": True}) == phases.RELEASED
    assert phases.is_open(cycle)
    assert not phases.is_open({**cycle, "released_locked": True})


def test_signup_deadline_freezes_the_plan_but_not_qa_or_delivery() -> None:
    for action in ("signup", "edit_entry_plan", "include_tests", "edit_environment"):
        assert phases.can(phases.SIGNUP, action), action
        assert not phases.can(phases.IN_PROGRESS, action), action
    for action in ("exclude_tests", "withdraw", "edit_deliverable", "edit_qa", "upload_qa_log"):
        assert phases.can(phases.IN_PROGRESS, action), action
    assert phases.can(phases.IN_PROGRESS, "decide_scope_request")
    assert not phases.can(phases.SIGNUP, "decide_scope_request")


def test_qa_closed_only_adds_publish_and_keeps_qa_open() -> None:
    assert phases.can(phases.QA_CLOSED, "publish")
    assert not phases.can(phases.IN_PROGRESS, "publish")
    assert phases.can(phases.QA_CLOSED, "edit_qa")
    assert phases.can(phases.QA_CLOSED, "edit_deliverable")
    assert not phases.can(phases.QA_CLOSED, "exclude_tests")
    assert not phases.can(phases.QA_CLOSED, "signup")


def test_released_freezes_everything_except_unlock() -> None:
    for action in (
        "signup",
        "edit_entry_plan",
        "exclude_tests",
        "withdraw",
        "edit_environment",
        "edit_deliverable",
        "edit_qa",
        "upload_qa_log",
        "decide_scope_request",
        "publish",
    ):
        assert not phases.can(phases.RELEASED, action), action
    assert phases.can(phases.RELEASED, "unlock")
    assert not phases.can(phases.QA_CLOSED, "unlock")


def test_unknown_actions_fail_closed() -> None:
    assert not phases.can(phases.SIGNUP, "edit_release_decision")
    assert not phases.can("no_such_phase", "signup")
