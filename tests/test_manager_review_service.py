from __future__ import annotations

import csv
import io

from app.services import artifact_service, qa_service
from release_system import core
from tests.conftest import seed_snapshot


def test_manager_review_moves_non_release_apps_last_and_masks_review_fields(release_with_app):
    conn, release_id, app_id = release_with_app
    seed_snapshot(conn, release_id, app_id, owner_confirmed=True)
    qa_service.set_qa_status_batch(
        conn,
        release_id,
        [{"app_id": app_id, "status": "has_issues", "issue_note": "release app note"}],
        user="qa",
        role="QA",
    )

    non_release_id = core.add_new_app_request(
        conn,
        release_id,
        official_name="Aardvark NonRelease",
        git_url="ssh://non-release",
        git_branch="main",
        release_decision="cicd_only",
        owner="owner_non_release",
    )

    def _fill_non_release(snapshot: dict) -> None:
        snapshot["version"] = "9.9"
        snapshot["x86_chips"] = "C500"
        snapshot["arm_chips"] = "X201"
        snapshot["qa_status"] = "has_issues"
        snapshot["qa_issue_note"] = "should be hidden"
        snapshot["doc"]["limitations"] = "should be hidden"

    core.update_snapshot(conn, release_id, non_release_id, _fill_non_release)

    fields = [
        "app_name",
        "chip_support",
        "qa_issue_note",
        "releasable",
        "not_releasable_reason",
        "known_limitations",
        "release_decision",
    ]
    csv_text = artifact_service.generate_manager_review(
        conn,
        release_id,
        fields,
        user="rm",
        role="RM",
    )
    rows = list(csv.DictReader(io.StringIO(csv_text)))

    assert rows[-1]["App"].startswith("Aardvark NonRelease")
    assert rows[-1]["支持芯片类型"] == ""
    assert rows[-1]["QA问题"] == ""
    assert rows[-1]["是否可发布"] == ""
    assert rows[-1]["不可发布原因"] == "不发布"
    assert rows[-1]["已知限制"] == ""
    assert rows[-1]["Release决策"] == "cicd_only"

    release_row = rows[0]
    assert release_row["App"].startswith("TestApp")
    assert release_row["QA问题"] == "release app note"
    assert release_row["是否可发布"] == "是"

    artifact = conn.execute(
        "SELECT content FROM artifacts WHERE release_id = ? AND kind = 'manager_review'",
        (release_id,),
    ).fetchone()
    assert artifact["content"] == csv_text
