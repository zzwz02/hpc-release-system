from __future__ import annotations

from app.domain import phases


def test_after_doc_policy_explicitly_keeps_cicd_and_qa_open() -> None:
    assert phases.can("after_doc_deadline", "edit_release_decision")
    assert phases.can("after_doc_deadline", "edit_cicd_config")
    assert phases.can("after_doc_deadline", "edit_gerrit_identity")
    assert phases.can("after_doc_deadline", "edit_qa_status")
    assert phases.can("after_doc_deadline", "upload_qa_log")

    assert not phases.can("after_doc_deadline", "edit_release_doc_fields")
    assert not phases.can("after_doc_deadline", "edit_app_info")
    assert not phases.can("after_doc_deadline", "edit_owner_confirmation")


def test_released_locked_policy_freezes_everything() -> None:
    for action in (
        "edit_release_decision",
        "edit_cicd_config",
        "edit_gerrit_identity",
        "edit_release_doc_fields",
        "edit_app_info",
        "edit_owner_confirmation",
        "edit_qa_status",
        "upload_qa_log",
    ):
        assert not phases.can("released_locked", action), action
