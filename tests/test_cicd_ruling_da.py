"""App-backed release_decision → CICD status request tests."""
from __future__ import annotations

import json

import pytest

from app.db.connection import transaction
from app.repositories import apps_repo, audit_repo
from app.services import app_service, cicd_service
from app.timeutil import beijing_timestamp
from tests.conftest import seed_release


APP_ID = "cicd_da_app"


def seed_app(conn, app_id: str = APP_ID) -> str:
    apps_repo.save_app(conn, {
        "id": app_id,
        "git_url": "ssh://gerrit/PDE/HPC/hpc_da",
        "git_branch": "main",
        "aliases": ["DecisionApp"],
        "created_by": "test",
        "created_at": beijing_timestamp(),
    })
    conn.commit()
    return app_id


def sync(conn, decision: str, *, old_status: str = "Running", app_id: str = APP_ID):
    with transaction(conn):
        return cicd_service.sync_decision_to_cicd(
            conn,
            app_id,
            decision,
            submitter="rm",
            current_status_override=old_status,
        )


def pending_status_requests(conn, app_id: str = APP_ID) -> list[dict]:
    rows = conn.execute(
        """
        SELECT * FROM cicd_task_requests
        WHERE app_id = ? AND task_id = ? AND status = 'pending'
          AND request_type = 'modify'
        ORDER BY id
        """,
        (app_id, app_id),
    ).fetchall()
    result = []
    for row in rows:
        d = dict(row)
        d["_payload"] = json.loads(d["payload"] or "{}")
        result.append(d)
    return result


class TestDecisionMapping:
    @pytest.mark.parametrize(
        "decision,old_status,new_status",
        [
            ("release", "Stopped", "Running"),
            ("cicd_only", "Stopped", "Running"),
            ("stopped", "Running", "Stopped"),
        ],
    )
    def test_decision_creates_pending_status_request(self, temp_db, decision, old_status, new_status):
        seed_app(temp_db)
        req = sync(temp_db, decision, old_status=old_status)
        assert req is not None
        assert req["task_id"] == APP_ID
        assert req["app_id"] == APP_ID
        assert req["origin"] == "release_decision_sync"
        assert req["request_type"] == "modify"
        payload = req["payload"] if isinstance(req["payload"], dict) else json.loads(req["payload"])
        assert payload["status"] == {"old": old_status, "new": new_status}

    def test_same_status_is_noop(self, temp_db):
        seed_app(temp_db)
        assert sync(temp_db, "release", old_status="Running") is None

    def test_missing_app_is_noop(self, temp_db):
        assert sync(temp_db, "stopped", old_status="Running", app_id="missing") is None

    def test_pending_status_request_blocks_duplicates(self, temp_db):
        seed_app(temp_db)
        first = sync(temp_db, "stopped", old_status="Running")
        second = sync(temp_db, "stopped", old_status="Running")
        assert first is not None
        assert second is None
        assert len(pending_status_requests(temp_db)) == 1

    def test_dispatched_status_request_blocks_duplicates_without_jira(self, temp_db):
        seed_app(temp_db)
        first = sync(temp_db, "release", old_status="Stopped")
        assert first is not None
        cicd_service.approve_request(
            temp_db,
            first["id"],
            reviewer="rm",
            reviewer_role="RM",
            approval_mode="dispatch_spd",
        )

        second = sync(temp_db, "release", old_status="Stopped")

        assert second is None
        rows = temp_db.execute(
            """
            SELECT status, delivery_status, jira_id
            FROM cicd_task_requests
            WHERE app_id = ? AND request_type = 'modify'
            """,
            (APP_ID,),
        ).fetchall()
        assert len(rows) == 1
        assert rows[0]["status"] == "approved"
        assert rows[0]["delivery_status"] == "pending"
        assert rows[0]["jira_id"] == ""


class TestStatusLock:
    def test_user_modify_cannot_set_status(self, temp_db):
        seed_app(temp_db)
        with pytest.raises(RuntimeError):
            cicd_service.submit_request(
                temp_db,
                task_id=APP_ID,
                request_type="modify",
                payload={"status": {"old": "Running", "new": "Stopped"}},
                submitter="owner",
                submitter_role="Owner",
                source="app_workbench",
            )

    def test_user_modify_app_cicd_fields_succeeds(self, temp_db):
        seed_app(temp_db)
        req = cicd_service.submit_request(
            temp_db,
            task_id=APP_ID,
            request_type="modify",
            payload={"notes": {"old": "", "new": "owner tweak"}},
            submitter="owner",
            submitter_role="Owner",
            source="app_workbench",
        )
        assert req["status"] == "pending"
        assert req["task_id"] == APP_ID

    def test_pending_status_sync_blocks_app_cicd_config_modify(self, temp_db):
        seed_app(temp_db)
        status_req = sync(temp_db, "stopped", old_status="Running")

        with pytest.raises(RuntimeError, match=rf"#{status_req['id']}"):
            cicd_service.submit_request(
                temp_db,
                task_id=APP_ID,
                request_type="modify",
                payload={"notes": {"old": "", "new": "new config"}},
                submitter="owner",
                submitter_role="Owner",
                source="app_workbench",
            )

        rows = temp_db.execute(
            "SELECT id, status FROM cicd_task_requests WHERE app_id = ? ORDER BY id",
            (APP_ID,),
        ).fetchall()
        assert [(row["id"], row["status"]) for row in rows] == [(status_req["id"], "pending")]

    def test_status_sync_cannot_be_replaced_by_app_cicd_config_modify(self, temp_db):
        seed_app(temp_db)
        status_req = sync(temp_db, "stopped", old_status="Running")

        with pytest.raises(RuntimeError, match="运行/停止状态同步申请"):
            cicd_service.submit_request(
                temp_db,
                task_id=APP_ID,
                request_type="modify",
                payload={"notes": {"old": "", "new": "new config"}},
                submitter="owner",
                submitter_role="Owner",
                source="app_workbench",
                replace_open=True,
            )

        old = temp_db.execute(
            "SELECT status FROM cicd_task_requests WHERE id = ?",
            (status_req["id"],),
        ).fetchone()
        assert old["status"] == "pending"

    def test_create_with_initial_status_is_allowed_for_valid_app(self, temp_db):
        seed_app(temp_db)
        req = cicd_service.submit_request(
            temp_db,
            task_id=APP_ID,
            request_type="create",
            payload={
                "app_name": "DecisionApp",
                "repo_type": "git",
                "repo_name": "ssh://gerrit/PDE/HPC/hpc_da",
                "branch": "main",
                "owner_username": "owner",
                "status": "Running",
            },
            submitter="owner",
            submitter_role="Owner",
        )
        assert req["request_type"] == "create"
        assert req["task_id"] == APP_ID

    def test_pending_create_blocks_modify(self, temp_db):
        seed_app(temp_db)
        cicd_service.submit_request(
            temp_db,
            task_id=APP_ID,
            request_type="create",
            payload={
                "app_name": "DecisionApp",
                "repo_type": "git",
                "repo_name": "ssh://gerrit/PDE/HPC/hpc_da",
                "branch": "main",
                "owner_username": "owner",
                "status": "Running",
            },
            submitter="owner",
            submitter_role="Owner",
        )

        with pytest.raises(RuntimeError, match="CICD 新建申请"):
            cicd_service.submit_request(
                temp_db,
                task_id=APP_ID,
                request_type="modify",
                payload={"notes": {"old": "", "new": "blocked"}},
                submitter="owner",
                submitter_role="Owner",
                source="app_workbench",
            )

    def test_jira_delivery_modify_blocks_new_modify(self, temp_db):
        seed_app(temp_db)
        req = cicd_service.submit_request(
            temp_db,
            task_id=APP_ID,
            request_type="modify",
            payload={"notes": {"old": "", "new": "jira flow"}},
            submitter="owner",
            submitter_role="Owner",
            source="app_workbench",
        )
        cicd_service.approve_request(
            temp_db,
            req["id"],
            reviewer="rm",
            reviewer_role="RM",
            approval_mode="dispatch_spd",
            jira_id="SPD-1",
        )

        with pytest.raises(RuntimeError, match="SPD-1"):
            cicd_service.submit_request(
                temp_db,
                task_id=APP_ID,
                request_type="modify",
                payload={"notes": {"old": "", "new": "new change"}},
                submitter="owner",
                submitter_role="Owner",
                source="app_workbench",
            )

    def test_pending_modify_requires_replace_confirmation(self, temp_db):
        seed_app(temp_db)
        first = cicd_service.submit_request(
            temp_db,
            task_id=APP_ID,
            request_type="modify",
            payload={"notes": {"old": "", "new": "first"}},
            submitter="owner",
            submitter_role="Owner",
            source="app_workbench",
        )

        with pytest.raises(RuntimeError, match="请确认后重试"):
            cicd_service.submit_request(
                temp_db,
                task_id=APP_ID,
                request_type="modify",
                payload={"notes": {"old": "", "new": "second"}},
                submitter="owner",
                submitter_role="Owner",
                source="app_workbench",
            )

        second = cicd_service.submit_request(
            temp_db,
            task_id=APP_ID,
            request_type="modify",
            payload={"notes": {"old": "", "new": "second"}},
            submitter="owner",
            submitter_role="Owner",
            source="app_workbench",
            replace_open=True,
        )
        assert second["status"] == "pending"
        assert second["cancelled_request_ids"] == [first["id"]]
        old = temp_db.execute(
            "SELECT status, review_note FROM cicd_task_requests WHERE id = ?",
            (first["id"],),
        ).fetchone()
        assert old["status"] == "cancelled"
        assert f"#{second['id']}" in old["review_note"]
        pending = temp_db.execute(
            """
            SELECT COUNT(*)
            FROM cicd_task_requests
            WHERE app_id = ? AND request_type = 'modify' AND status = 'pending'
            """,
            (APP_ID,),
        ).fetchone()[0]
        assert pending == 1

    def test_rm_rejects_returned_delivery_without_applying_payload(self, temp_db):
        seed_app(temp_db)
        req = cicd_service.submit_request(
            temp_db,
            task_id=APP_ID,
            request_type="modify",
            payload={"notes": {"old": "", "new": "do not apply"}},
            submitter="owner",
            submitter_role="Owner",
            source="app_workbench",
        )
        cicd_service.approve_request(
            temp_db,
            req["id"],
            reviewer="rm",
            reviewer_role="RM",
            approval_mode="dispatch_spd",
            jira_id="SPD-2",
        )
        cicd_service.return_delivery(
            temp_db,
            req["id"],
            returner="spd",
            returner_role="SPD",
            reason="needs change",
        )

        rejected = cicd_service.reject_returned_request(
            temp_db,
            req["id"],
            reviewer="rm",
            reviewer_role="RM",
            review_note="obsolete",
        )

        assert rejected["status"] == "rejected"
        assert rejected["delivery_status"] == ""
        assert rejected["jira_id"] == "SPD-2"
        assert rejected["returned_reason"] == "needs change"
        assert cicd_service.list_deliveries(temp_db, status_filter="pending_or_returned") == []
        assert apps_repo.get_app(temp_db, APP_ID)["cicd_notes"] == ""

    def test_reject_returned_validation(self, temp_db):
        seed_app(temp_db)
        req = cicd_service.submit_request(
            temp_db,
            task_id=APP_ID,
            request_type="modify",
            payload={"notes": {"old": "", "new": "flow"}},
            submitter="owner",
            submitter_role="Owner",
            source="app_workbench",
        )
        with pytest.raises(PermissionError):
            cicd_service.reject_returned_request(
                temp_db,
                req["id"],
                reviewer="owner",
                reviewer_role="Owner",
                review_note="no",
            )
        with pytest.raises(ValueError):
            cicd_service.reject_returned_request(
                temp_db,
                req["id"],
                reviewer="rm",
                reviewer_role="RM",
                review_note="",
            )
        with pytest.raises(RuntimeError, match="已退回"):
            cicd_service.reject_returned_request(
                temp_db,
                req["id"],
                reviewer="rm",
                reviewer_role="RM",
                review_note="no",
            )


class TestUpdateSnapshotIntegration:
    def test_decision_change_adds_app_backed_cicd_sync(self, temp_db, tmp_dir):
        release_id = seed_release(temp_db, tmp_path=tmp_dir)
        import release_system.core as core

        rel = core.get_release(temp_db, release_id)
        app_id = next(iter(rel["snapshots"]))
        response = app_service.update_snapshot(
            temp_db,
            release_id,
            app_id,
            user="rm",
            role="RM",
            fields={"snapshot": {"release_decision": "stopped"}},
        )
        assert response["cicd_sync"]["created"] is True
        req = response["cicd_sync"]["request"]
        assert req["task_id"] == app_id
        assert req["app_id"] == app_id
        payload = req["payload"] if isinstance(req["payload"], dict) else json.loads(req["payload"])
        assert payload["status"]["new"] == "Stopped"

    def test_stopped_to_running_defers_release_decision_until_cicd_apply(self, temp_db, tmp_dir):
        release_id = seed_release(temp_db, tmp_path=tmp_dir)
        import release_system.core as core

        app_id = core.add_new_app_request(
            temp_db,
            release_id,
            official_name="DeferredRun",
            git_url="ssh://gerrit/deferred-run",
            git_branch="main",
            release_decision="stopped",
            owner="rm",
        )
        response = app_service.update_snapshot(
            temp_db,
            release_id,
            app_id,
            user="rm",
            role="RM",
            fields={"snapshot": {"release_decision": "cicd_only"}},
        )

        assert response["snapshot"]["release_decision"] == "stopped"
        req = response["cicd_sync"]["request"]
        assert req is not None
        payload = req["payload"] if isinstance(req["payload"], dict) else json.loads(req["payload"])
        assert payload["status"] == {"old": "Stopped", "new": "Running"}
        assert core.get_release(temp_db, release_id)["snapshots"][app_id]["release_decision"] == "stopped"

        cicd_service.approve_request(
            temp_db,
            req["id"],
            reviewer="rm",
            reviewer_role="RM",
        )

        assert core.get_release(temp_db, release_id)["snapshots"][app_id]["release_decision"] == "cicd_only"

    def test_deferred_release_decision_apply_writes_app_audit(self, temp_db, tmp_dir):
        release_id = seed_release(temp_db, tmp_path=tmp_dir)
        import release_system.core as core

        app_id = core.add_new_app_request(
            temp_db,
            release_id,
            official_name="DeferredAudit",
            git_url="ssh://gerrit/deferred-audit",
            git_branch="main",
            release_decision="stopped",
            owner="rm",
        )
        response = app_service.update_snapshot(
            temp_db,
            release_id,
            app_id,
            user="rm",
            role="RM",
            fields={"snapshot": {"release_decision": "cicd_only"}},
        )
        req_id = response["cicd_sync"]["request"]["id"]

        assert not [
            row
            for row in audit_repo.app_audit_log(temp_db, app_id, release_id)
            if row["event"] == "apply_deferred_release_decision"
        ]

        cicd_service.approve_request(
            temp_db,
            req_id,
            reviewer="rm",
            reviewer_role="RM",
        )

        entries = [
            row
            for row in audit_repo.app_audit_log(temp_db, app_id, release_id)
            if row["event"] == "apply_deferred_release_decision"
        ]
        assert len(entries) == 1
        assert "stopped -> cicd_only" in entries[0]["message"]
        detail = {item["field"]: item for item in entries[0]["detail"]}
        assert detail["request_id"]["new"] == req_id
        assert detail["release_decision"]["old"] == "stopped"
        assert detail["release_decision"]["new"] == "cicd_only"
        assert detail["applied_by"]["new"] == "rm"

    def test_pending_create_blocks_stopped_to_running_decision(self, temp_db, tmp_dir):
        release_id = seed_release(temp_db, tmp_path=tmp_dir)
        import release_system.core as core

        result = cicd_service.cicd_first_new_app(
            temp_db,
            official_name="CreateBlockedDecision",
            repo_type="git",
            repo_name="hpc_create_blocked",
            branch="main",
            submitter="owner",
            submitter_role="Owner",
            payload={
                "cicd_repo_type": "git",
                "cicd_community_artifact": "",
                "cicd_build_image": "",
                "cicd_test_timeout": "40",
                "cicd_notes": "",
            },
        )

        with pytest.raises(RuntimeError, match="CICD 新建申请"):
            app_service.update_snapshot(
                temp_db,
                release_id,
                result["app_id"],
                user="rm",
                role="RM",
                fields={"snapshot": {"release_decision": "release"}},
            )

        snap = core.get_release(temp_db, release_id)["snapshots"][result["app_id"]]
        assert snap["release_decision"] == "stopped"

    def test_jira_backed_sync_delivery_blocks_new_running_decision(self, temp_db, tmp_dir):
        release_id = seed_release(temp_db, tmp_path=tmp_dir)
        import release_system.core as core

        rel = core.get_release(temp_db, release_id)
        app_id = next(iter(rel["snapshots"]))
        snap = rel["snapshots"][app_id]
        snap["release_decision"] = "stopped"
        core.save_snapshot(temp_db, release_id, app_id, snap)

        old_req = cicd_service.sync_decision_to_cicd(
            temp_db,
            app_id,
            "release",
            submitter="rm",
            current_status_override="Stopped",
            release_id=release_id,
            apply_release_decision_on_delivery=True,
        )
        assert old_req is not None
        assert old_req["origin"] == "release_decision_sync"
        cicd_service.approve_request(
            temp_db,
            old_req["id"],
            reviewer="rm",
            reviewer_role="RM",
            approval_mode="dispatch_spd",
            jira_id="HPC-222",
        )

        with pytest.raises(RuntimeError, match="HPC-222"):
            app_service.update_snapshot(
                temp_db,
                release_id,
                app_id,
                user="rm",
                role="RM",
                fields={"snapshot": {"release_decision": "release"}},
            )

        requests = temp_db.execute(
            """
            SELECT id, status, delivery_status, jira_id, origin
            FROM cicd_task_requests
            WHERE app_id = ? AND request_type = 'modify'
            ORDER BY id
            """,
            (app_id,),
        ).fetchall()
        assert len(requests) == 1
        assert requests[0]["id"] == old_req["id"]
        assert requests[0]["delivery_status"] == "pending"
        assert requests[0]["jira_id"] == "HPC-222"
        snap = core.get_release(temp_db, release_id)["snapshots"][app_id]
        assert snap["release_decision"] == "stopped"

    def test_jira_delivery_modify_blocks_stopped_decision(self, temp_db, tmp_dir):
        release_id = seed_release(temp_db, tmp_path=tmp_dir)
        import release_system.core as core

        rel = core.get_release(temp_db, release_id)
        app_id = next(iter(rel["snapshots"]))

        old_req = cicd_service.submit_request(
            temp_db,
            task_id=app_id,
            request_type="modify",
            payload={
                "repo_name": {"old": "hpc_abacus2", "new": "hpc_abacus"},
                "branch": {"old": "maca2", "new": "maca"},
            },
            submitter="owner",
            submitter_role="Owner",
            source="app_workbench",
        )
        cicd_service.approve_request(
            temp_db,
            old_req["id"],
            reviewer="rm",
            reviewer_role="RM",
            approval_mode="dispatch_spd",
            jira_id="HPC-222",
        )

        with pytest.raises(RuntimeError, match="HPC-222"):
            app_service.update_snapshot(
                temp_db,
                release_id,
                app_id,
                user="rm",
                role="RM",
                fields={"snapshot": {"release_decision": "stopped"}},
            )

        requests = temp_db.execute(
            """
            SELECT id, status, delivery_status, jira_id, origin
            FROM cicd_task_requests
            WHERE app_id = ? AND request_type = 'modify'
            ORDER BY id
            """,
            (app_id,),
        ).fetchall()
        assert len(requests) == 1
        assert requests[0]["id"] == old_req["id"]
        assert requests[0]["delivery_status"] == "pending"
        assert requests[0]["jira_id"] == "HPC-222"
        snap = core.get_release(temp_db, release_id)["snapshots"][app_id]
        assert snap["release_decision"] == "release"

    def test_doc_deadline_allows_release_decision_and_cicd_config(self, temp_db, tmp_dir):
        release_id = seed_release(temp_db, tmp_path=tmp_dir)
        import release_system.core as core

        rel = core.get_release(temp_db, release_id)
        app_id = next(iter(rel["snapshots"]))
        temp_db.execute(
            "UPDATE releases SET doc_deadline = '2000-01-01 00:00:00' WHERE id = ?",
            (release_id,),
        )
        temp_db.commit()

        response = app_service.update_snapshot(
            temp_db,
            release_id,
            app_id,
            user="rm",
            role="RM",
            fields={
                "snapshot": {"release_decision": "stopped"},
                "app": {"cicd_build_image": "hpc/new-image:latest"},
            },
        )

        assert response["snapshot"]["release_decision"] == "stopped"
        app = core.get_app(temp_db, app_id)
        assert app["cicd_build_image"] == "hpc/new-image:latest"

    @pytest.mark.parametrize(
        "app_freeze_deadline,doc_deadline",
        [
            ("2000-01-01 00:00:00", "2999-01-01 00:00:00"),
            ("2000-01-01 00:00:00", "2000-01-01 00:00:00"),
        ],
    )
    def test_after_freeze_or_doc_deadline_blocks_raise_to_release(
        self,
        temp_db,
        tmp_dir,
        app_freeze_deadline,
        doc_deadline,
    ):
        release_id = seed_release(temp_db, tmp_path=tmp_dir)
        import release_system.core as core

        rel = core.get_release(temp_db, release_id)
        app_id = next(iter(rel["snapshots"]))
        core.update_snapshot(
            temp_db,
            release_id,
            app_id,
            lambda snap: snap.update({"release_decision": "cicd_only"}),
            skip_doc_deadline=True,
        )
        temp_db.execute(
            """
            UPDATE releases
            SET app_freeze_deadline = ?, doc_deadline = ?
            WHERE id = ?
            """,
            (app_freeze_deadline, doc_deadline, release_id),
        )
        temp_db.commit()

        with pytest.raises(RuntimeError, match="不可.*release"):
            app_service.update_snapshot(
                temp_db,
                release_id,
                app_id,
                user="rm",
                role="RM",
                fields={"snapshot": {"release_decision": "release"}},
            )

    def test_same_decision_does_not_add_cicd_sync(self, temp_db, tmp_dir):
        release_id = seed_release(temp_db, tmp_path=tmp_dir)
        import release_system.core as core

        rel = core.get_release(temp_db, release_id)
        app_id = next(iter(rel["snapshots"]))
        response = app_service.update_snapshot(
            temp_db,
            release_id,
            app_id,
            user="rm",
            role="RM",
            fields={"snapshot": {"release_decision": "release"}},
        )
        assert "cicd_sync" not in response
