from __future__ import annotations

import csv
import io

import pytest

from app.domain import manager_review
from app.services import artifact_service, cycle_service, qa_service
from tests.conftest import close_qa, seed_app, seed_cycle, seed_signup


def test_manager_review_field_metadata_is_authoritative() -> None:
    assert manager_review.FIELDS[0] == "app_name"
    assert manager_review.FIELD_LABELS["official_name"] == "官方名称"
    assert manager_review.DEFAULT_FIELDS == (
        "app_name",
        "target_version",
        "owners",
        "chip_support",
        "actual_delivery",
        "qa_status",
        "qa_note",
        "known_issues",
        "result",
    )
    assert set(manager_review.DEFAULT_FIELDS) <= set(manager_review.FIELDS)


def test_manager_review_lists_signed_up_apps_in_name_order_with_qa_and_result(temp_db):
    conn = temp_db
    seed_app(conn, "zulu", official_name="ZuluApp")
    seed_app(conn, "alpha", official_name="AlphaApp")
    release_id = seed_cycle(conn)
    seed_signup(conn, release_id, "zulu", target_version="2.0")
    seed_signup(conn, release_id, "alpha", target_version="1.0")
    qa_service.set_qa_status_batch(
        conn,
        release_id,
        [
            {"app_id": "alpha", "status": "has_issues", "issue_note": "alpha 有问题"},
            {"app_id": "zulu", "status": "cannot_release", "issue_note": "zulu 不可发布"},
        ],
        user="qa",
        role="QA",
    )
    close_qa(conn, release_id)
    cycle_service.publish(conn, release_id, user="rm", role="RM")

    fields = ["app_name", "owners", "chip_support", "qa_status", "qa_note", "known_issues", "result"]
    csv_text = artifact_service.generate_manager_review(conn, release_id, fields, user="rm", role="RM")
    rows = list(csv.DictReader(io.StringIO(csv_text)))

    assert [row["App"] for row in rows] == ["AlphaApp 1.0", "ZuluApp 2.0"]
    assert rows[0]["QA状态"] == "存在问题"
    assert rows[0]["QA备注"] == "alpha 有问题"
    assert rows[0]["已知问题"] == "QA 备注：alpha 有问题"
    assert rows[0]["发布结果"] == "已发布"
    assert rows[0]["支持芯片类型"]
    # cannot_release never reaches the release note and is reported as failed.
    assert rows[1]["发布结果"] == "发布失败"
    assert rows[1]["已知问题"] == ""

    artifact = conn.execute(
        "SELECT content FROM artifacts WHERE release_id = ? AND kind = 'manager_review'",
        (release_id,),
    ).fetchone()
    assert artifact["content"] == csv_text


def test_withdrawn_signups_are_left_out_and_unknown_fields_are_refused(temp_db):
    conn = temp_db
    app_id = seed_app(conn)
    release_id = seed_cycle(conn)
    entry = seed_signup(conn, release_id, app_id)
    cycle_service.withdraw(conn, entry["id"], user="rm", role="RM")

    csv_text = artifact_service.generate_manager_review(
        conn, release_id, ["app_name"], user="rm", role="RM"
    )
    assert list(csv.DictReader(io.StringIO(csv_text))) == []

    with pytest.raises(ValueError, match="未知 Manager Review 字段"):
        artifact_service.generate_manager_review(
            conn, release_id, ["release_decision"], user="rm", role="RM"
        )
