"""Unit tests for app/repositories/*.py — Phase 1 repository layer.

Uses app.db.connection.connect() with an in-memory or temp-file DB so
impl-testbase's conftest fixtures are not required (we call connect directly).
Tests are intentionally narrow: SQL correctness and data shapes, no
business logic.
"""
from __future__ import annotations

import pytest

from app.db.connection import connect, reset_init_state
from app.repositories import (
    apps_repo,
    artifacts_repo,
    audit_repo,
    base,
    cicd_repo,
    qa_repo,
    releases_repo,
    schedule_repo,
    sessions_repo,
    snapshots_repo,
    users_repo,
    wiki_repo,
)
from app.timeutil import beijing_timestamp

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def fresh_conn():
    """Open a fresh in-memory DB with full Phase 0 schema."""
    reset_init_state()
    conn = connect(":memory:")
    return conn


# ---------------------------------------------------------------------------
# base.py
# ---------------------------------------------------------------------------

class TestBase:
    def test_loads_json_empty(self):
        assert base.loads_json(None, []) == []
        assert base.loads_json("", {}) == {}

    def test_loads_json_parses(self):
        assert base.loads_json('{"a": 1}', {}) == {"a": 1}

    def test_dumps_json_sorted(self):
        result = base.dumps_json({"z": 1, "a": 2})
        assert result == '{"a": 2, "z": 1}'

    def test_new_id_format(self):
        nid = base.new_id("sched")
        assert nid.startswith("sched_")
        assert len(nid) == 6 + 12  # "sched_" + 12 hex chars


# ---------------------------------------------------------------------------
# apps_repo.py
# ---------------------------------------------------------------------------

class TestAppsRepo:
    def setup_method(self):
        self.conn = fresh_conn()

    def teardown_method(self):
        self.conn.close()

    def _make_app(self, app_id="test-app", git_url="ssh://gerrit/repo", branch="main"):
        return {
            "id": app_id,
            "git_url": git_url,
            "git_branch": branch,
            "aliases": ["Test App"],
            "created_by": "testuser",
            "created_at": beijing_timestamp(),
        }

    def test_save_and_get_app(self):
        app = self._make_app()
        apps_repo.save_app(self.conn, app)
        self.conn.commit()
        result = apps_repo.get_app(self.conn, "test-app")
        assert result is not None
        assert result["id"] == "test-app"
        assert result["git_url"] == "ssh://gerrit/repo"
        assert result["aliases"] == ["Test App"]  # aliases_json deserialized
        assert result["cicd_repo_type"] == ""
        assert result["cicd_community_artifact"] == ""
        assert result["cicd_build_image"] == ""
        assert result["cicd_test_timeout"] == ""

    def test_get_app_missing(self):
        assert apps_repo.get_app(self.conn, "nonexistent") is None

    def test_find_by_identity(self):
        app = self._make_app(git_url="ssh://gerrit/hpc_foo", branch="maca")
        apps_repo.save_app(self.conn, app)
        self.conn.commit()
        found = apps_repo.find_by_identity(self.conn, "ssh://gerrit/hpc_foo", "maca")
        assert found is not None
        assert found["id"] == "test-app"

    def test_find_by_identity_no_match(self):
        assert apps_repo.find_by_identity(self.conn, "ssh://gerrit/other", "main") is None

    def test_find_by_identity_wrong_branch(self):
        app = self._make_app(git_url="ssh://gerrit/hpc_foo", branch="maca")
        apps_repo.save_app(self.conn, app)
        self.conn.commit()
        assert apps_repo.find_by_identity(self.conn, "ssh://gerrit/hpc_foo", "master") is None

    def test_list_apps(self):
        for i in range(3):
            apps_repo.save_app(
                self.conn,
                self._make_app(app_id=f"app-{i}", git_url=f"ssh://gerrit/repo{i}", branch="main"),
            )
        self.conn.commit()
        apps = apps_repo.list_apps(self.conn)
        assert len(apps) == 3

    def test_delete_app(self):
        apps_repo.save_app(self.conn, self._make_app())
        self.conn.commit()
        apps_repo.delete_app(self.conn, "test-app")
        self.conn.commit()
        assert apps_repo.get_app(self.conn, "test-app") is None

    def test_all_app_ids(self):
        for i in range(2):
            apps_repo.save_app(
                self.conn,
                self._make_app(app_id=f"a{i}", git_url=f"url{i}", branch="main"),
            )
        self.conn.commit()
        ids = apps_repo.all_app_ids(self.conn)
        assert {"a0", "a1"} == ids

    def test_aliases_sorted_and_deduped(self):
        app = self._make_app()
        app["aliases"] = ["Z", "A", "Z"]
        apps_repo.save_app(self.conn, app)
        self.conn.commit()
        result = apps_repo.get_app(self.conn, "test-app")
        assert result["aliases"] == ["A", "Z"]


# ---------------------------------------------------------------------------
# releases_repo.py
# ---------------------------------------------------------------------------

class TestReleasesRepo:
    def setup_method(self):
        self.conn = fresh_conn()

    def teardown_method(self):
        self.conn.close()

    def _rel(self, rid="r1", name="v1.0"):
        return {
            "id": rid,
            "name": name,
            "maca_version": "3.8",
            "app_freeze_deadline": "",
            "doc_deadline": "",
            "released_locked": 0,
            "released_locked_at": "",
            "released_locked_by": "",
            "created_at": beijing_timestamp(),
            "source": "manual",
            "cloned_from": "",
        }

    def test_save_and_get(self):
        releases_repo.save_release(self.conn, self._rel())
        self.conn.commit()
        row = releases_repo.get_release_row(self.conn, "r1")
        assert row is not None
        assert row["name"] == "v1.0"
        assert row["released_locked"] is False

    def test_get_missing(self):
        assert releases_repo.get_release_row(self.conn, "nope") is None

    def test_list_releases(self):
        for i in range(3):
            releases_repo.save_release(self.conn, self._rel(rid=f"r{i}", name=f"v{i}"))
        self.conn.commit()
        rows = releases_repo.list_release_rows(self.conn)
        assert len(rows) == 3

    def test_release_is_locked_false(self):
        releases_repo.save_release(self.conn, self._rel())
        self.conn.commit()
        assert releases_repo.release_is_locked(self.conn, "r1") is False

    def test_lock_release(self):
        releases_repo.save_release(self.conn, self._rel())
        self.conn.commit()
        releases_repo.lock_release(self.conn, "r1", locked_at="2026-01-01 00:00:00", locked_by="rm")
        self.conn.commit()
        assert releases_repo.release_is_locked(self.conn, "r1") is True
        row = releases_repo.get_release_row(self.conn, "r1")
        assert row["released_locked_by"] == "rm"

    def test_future_unlocked_release_ids(self):
        for i in range(4):
            releases_repo.save_release(self.conn, self._rel(rid=f"r{i}", name=f"v{i}"))
        self.conn.commit()
        # Lock r1
        releases_repo.lock_release(self.conn, "r1", locked_at="2026-01-01 00:00:00", locked_by="rm")
        self.conn.commit()
        result = releases_repo.future_unlocked_release_ids(self.conn, "r0")
        # r0 unlocked, r1 locked (excluded), r2 unlocked, r3 unlocked
        assert "r0" in result
        assert "r1" not in result
        assert "r2" in result

    def test_delete_release(self):
        releases_repo.save_release(self.conn, self._rel())
        self.conn.commit()
        releases_repo.delete_release(self.conn, "r1")
        self.conn.commit()
        assert releases_repo.get_release_row(self.conn, "r1") is None


# ---------------------------------------------------------------------------
# snapshots_repo.py
# ---------------------------------------------------------------------------

class TestSnapshotsRepo:
    def setup_method(self):
        self.conn = fresh_conn()
        # Insert prerequisites
        self.conn.execute(
            "INSERT INTO releases(id, name, source, created_at)"
            " VALUES ('r1', 'v1', 'manual', '2026-01-01')"
        )
        self.conn.execute(
            "INSERT INTO apps(id, git_url, git_branch, created_at)"
            " VALUES ('app1', 'url', 'main', '2026-01-01')"
        )
        self.conn.commit()

    def teardown_method(self):
        self.conn.close()

    def test_save_and_get_snapshot(self):
        data = {"release_decision": "release", "version": "1.0"}
        snapshots_repo.save_snapshot(self.conn, "r1", "app1", data)
        self.conn.commit()
        result = snapshots_repo.get_snapshot(self.conn, "r1", "app1")
        assert result == data

    def test_get_missing_snapshot(self):
        assert snapshots_repo.get_snapshot(self.conn, "r1", "missing") is None

    def test_snapshot_exists(self):
        snapshots_repo.save_snapshot(self.conn, "r1", "app1", {})
        self.conn.commit()
        assert snapshots_repo.snapshot_exists(self.conn, "r1", "app1") is True
        assert snapshots_repo.snapshot_exists(self.conn, "r1", "other") is False

    def test_get_all_for_release(self):
        snapshots_repo.save_snapshot(self.conn, "r1", "app1", {"k": "v"})
        self.conn.commit()
        result = snapshots_repo.get_all_for_release(self.conn, "r1")
        assert "app1" in result
        assert result["app1"] == {"k": "v"}

    def test_upsert_snapshot(self):
        snapshots_repo.save_snapshot(self.conn, "r1", "app1", {"v": "1"})
        self.conn.commit()
        snapshots_repo.save_snapshot(self.conn, "r1", "app1", {"v": "2"})
        self.conn.commit()
        result = snapshots_repo.get_snapshot(self.conn, "r1", "app1")
        assert result == {"v": "2"}

    def test_app_ids_in_release(self):
        # Add second app
        self.conn.execute(
            "INSERT INTO apps(id, git_url, git_branch, created_at)"
            " VALUES ('app2', 'url2', 'main', '2026-01-01')"
        )
        self.conn.commit()
        snapshots_repo.save_snapshot(self.conn, "r1", "app1", {})
        snapshots_repo.save_snapshot(self.conn, "r1", "app2", {})
        self.conn.commit()
        ids = snapshots_repo.app_ids_in_release(self.conn, "r1")
        assert sorted(ids) == ["app1", "app2"]


# ---------------------------------------------------------------------------
# artifacts_repo.py
# ---------------------------------------------------------------------------

class TestArtifactsRepo:
    def setup_method(self):
        self.conn = fresh_conn()
        self.conn.execute(
            "INSERT INTO releases(id, name, source, created_at)"
            " VALUES ('r1', 'v1', 'manual', '2026-01-01')"
        )
        self.conn.commit()

    def teardown_method(self):
        self.conn.close()

    def test_upsert_and_get(self):
        artifacts_repo.upsert_artifact(
            self.conn, "r1", "manual",
            name="manual.md", content="# Hello", generated_at="2026-01-01 00:00:00"
        )
        self.conn.commit()
        result = artifacts_repo.get_artifact(self.conn, "r1", "manual")
        assert result is not None
        assert result["content"] == "# Hello"
        assert result["final"] == 0

    def test_upsert_replaces(self):
        artifacts_repo.upsert_artifact(
            self.conn, "r1", "csv", name="a.csv", content="old", generated_at="t1"
        )
        artifacts_repo.upsert_artifact(
            self.conn, "r1", "csv", name="a.csv", content="new", generated_at="t2"
        )
        self.conn.commit()
        result = artifacts_repo.get_artifact(self.conn, "r1", "csv")
        assert result["content"] == "new"

    def test_missing_artifact(self):
        assert artifacts_repo.get_artifact(self.conn, "r1", "nope") is None

    def test_delete_draft_artifacts(self):
        artifacts_repo.upsert_artifact(
            self.conn, "r1", "draft", name="d", content="c", generated_at="t", final=0
        )
        artifacts_repo.upsert_artifact(
            self.conn, "r1", "final_art", name="f", content="c", generated_at="t", final=1
        )
        self.conn.commit()
        artifacts_repo.delete_draft_artifacts(self.conn, "r1")
        self.conn.commit()
        assert artifacts_repo.get_artifact(self.conn, "r1", "draft") is None
        assert artifacts_repo.get_artifact(self.conn, "r1", "final_art") is not None

    def test_has_final_artifacts(self):
        assert artifacts_repo.has_final_artifacts(self.conn, "r1") is False
        artifacts_repo.upsert_artifact(
            self.conn, "r1", "x", name="x", content="c", generated_at="t", final=1
        )
        self.conn.commit()
        assert artifacts_repo.has_final_artifacts(self.conn, "r1") is True


# ---------------------------------------------------------------------------
# audit_repo.py
# ---------------------------------------------------------------------------

class TestAuditRepo:
    def setup_method(self):
        self.conn = fresh_conn()

    def teardown_method(self):
        self.conn.close()

    def test_log_and_read(self):
        audit_repo.log_audit(
            self.conn, "test message",
            ts="2026-01-01 00:00:00",
            user="rm", role="RM",
            app_id="app1", release_id="r1",
            event="create_app",
            detail={"key": "value"},
        )
        self.conn.commit()
        entries = audit_repo.app_audit_log(self.conn, "app1")
        assert len(entries) == 1
        assert entries[0]["message"] == "test message"
        assert entries[0]["detail"] == {"key": "value"}  # parsed from JSON

    def test_log_with_string_detail(self):
        audit_repo.log_audit(
            self.conn, "msg",
            ts="2026-01-01 00:00:00",
            user="u", role="RM",
            detail="plain string",
        )
        self.conn.commit()
        entries = audit_repo.app_audit_log(self.conn, "")
        assert entries[0]["detail"] == []  # empty string → []

    def test_release_filter(self):
        audit_repo.log_audit(self.conn, "r1 msg", ts="t1", user="u", role="R",
                             app_id="app1", release_id="r1", event="e")
        audit_repo.log_audit(self.conn, "r2 msg", ts="t2", user="u", role="R",
                             app_id="app1", release_id="r2", event="e")
        self.conn.commit()
        entries = audit_repo.app_audit_log(self.conn, "app1", "r1")
        assert len(entries) == 1
        assert entries[0]["message"] == "r1 msg"


# ---------------------------------------------------------------------------
# users_repo.py
# ---------------------------------------------------------------------------

class TestUsersRepo:
    def setup_method(self):
        self.conn = fresh_conn()

    def teardown_method(self):
        self.conn.close()

    def test_get_seeded_user(self):
        """Default users are seeded by init_db."""
        user = users_repo.get_user(self.conn, "rm")
        assert user is not None
        assert user["role"] == "RM"

    def test_insert_and_get(self):
        users_repo.insert_user(
            self.conn, username="alice", password_hash="hx", role="Owner"
        )
        self.conn.commit()
        user = users_repo.get_user(self.conn, "alice")
        assert user["role"] == "Owner"
        assert user["auth_source"] == "local"

    def test_update_role(self):
        users_repo.insert_user(self.conn, username="bob", password_hash="hx", role="Guest")
        self.conn.commit()
        users_repo.update_user_role(self.conn, "bob", role="Owner")
        self.conn.commit()
        assert users_repo.get_user(self.conn, "bob")["role"] == "Owner"

    def test_delete_user(self):
        users_repo.insert_user(self.conn, username="charlie", password_hash="hx", role="Guest")
        self.conn.commit()
        users_repo.delete_user(self.conn, "charlie")
        self.conn.commit()
        assert users_repo.get_user(self.conn, "charlie") is None

    def test_display_names_for(self):
        users_repo.insert_user(self.conn, username="d1", password_hash="x", role="QA",
                               display_name="Alice")
        users_repo.insert_user(self.conn, username="d2", password_hash="x", role="QA",
                               display_name="Bob")
        self.conn.commit()
        result = users_repo.display_names_for(self.conn, ["d1", "d2", "unknown"])
        assert result == {"d1": "Alice", "d2": "Bob"}


# ---------------------------------------------------------------------------
# sessions_repo.py
# ---------------------------------------------------------------------------

class TestSessionsRepo:
    def setup_method(self):
        self.conn = fresh_conn()

    def teardown_method(self):
        self.conn.close()

    def test_create_and_get(self):
        sessions_repo.create_session(
            self.conn, token="tok123", username="rm", created_at="2026-01-01 00:00:00"
        )
        self.conn.commit()
        session = sessions_repo.get_session(self.conn, "tok123")
        assert session is not None
        assert session["username"] == "rm"
        assert session["role"] == "RM"  # joined from users

    def test_get_missing(self):
        assert sessions_repo.get_session(self.conn, "nope") is None

    def test_delete_session(self):
        sessions_repo.create_session(self.conn, token="t", username="rm", created_at="now")
        self.conn.commit()
        sessions_repo.delete_session(self.conn, "t")
        self.conn.commit()
        assert sessions_repo.get_session(self.conn, "t") is None


# ---------------------------------------------------------------------------
# qa_repo.py
# ---------------------------------------------------------------------------

class TestQaRepo:
    def setup_method(self):
        self.conn = fresh_conn()
        self.conn.execute(
            "INSERT INTO releases(id, name, source, created_at)"
            " VALUES ('r1', 'v1', 'manual', '2026-01-01')"
        )
        self.conn.commit()

    def teardown_method(self):
        self.conn.close()

    def test_upsert_and_get(self):
        qa_repo.upsert_qa_log(
            self.conn, "r1",
            filename="qa.pdf",
            storage_path="/qa/logs/qa.pdf",
            uploaded_at="2026-01-01 00:00:00",
            uploaded_by="qa",
        )
        self.conn.commit()
        result = qa_repo.get_qa_log(self.conn, "r1")
        assert result is not None
        assert result["filename"] == "qa.pdf"

    def test_get_missing(self):
        assert qa_repo.get_qa_log(self.conn, "r1") is None

    def test_upsert_replaces(self):
        qa_repo.upsert_qa_log(self.conn, "r1", filename="old.pdf", storage_path="/old",
                              uploaded_at="t1", uploaded_by="qa")
        qa_repo.upsert_qa_log(self.conn, "r1", filename="new.pdf", storage_path="/new",
                              uploaded_at="t2", uploaded_by="qa")
        self.conn.commit()
        result = qa_repo.get_qa_log(self.conn, "r1")
        assert result["filename"] == "new.pdf"


# ---------------------------------------------------------------------------
# schedule_repo.py
# ---------------------------------------------------------------------------

class TestScheduleRepo:
    def setup_method(self):
        self.conn = fresh_conn()

    def teardown_method(self):
        self.conn.close()

    def test_insert_and_list(self):
        schedule_repo.insert_schedule_entry(
            self.conn, entry_id="s1", version="3.8.0",
            branch_cut_at="2026-01-01", release_at="2026-02-01",
            note="Test", created_at="2026-01-01 00:00:00", created_by="rm",
        )
        self.conn.commit()
        entries = schedule_repo.list_schedule(self.conn)
        assert len(entries) == 1
        assert entries[0]["version"] == "3.8.0"

    def test_get_entry(self):
        schedule_repo.insert_schedule_entry(
            self.conn, entry_id="s1", version="3.8.0",
            branch_cut_at="", release_at="", note="",
            created_at="t", created_by="rm",
        )
        self.conn.commit()
        entry = schedule_repo.get_schedule_entry(self.conn, "s1")
        assert entry is not None
        assert entry["id"] == "s1"

    def test_delete_entry(self):
        schedule_repo.insert_schedule_entry(
            self.conn, entry_id="s1", version="3.8.0",
            branch_cut_at="", release_at="", note="",
            created_at="t", created_by="rm",
        )
        self.conn.commit()
        deleted = schedule_repo.delete_schedule_entry(self.conn, "s1")
        self.conn.commit()
        assert deleted is True
        assert schedule_repo.get_schedule_entry(self.conn, "s1") is None

    def test_delete_missing(self):
        assert schedule_repo.delete_schedule_entry(self.conn, "nope") is False


# ---------------------------------------------------------------------------
# cicd_repo.py
# ---------------------------------------------------------------------------

class TestCicdRepo:
    def setup_method(self):
        self.conn = fresh_conn()
        # Insert a user and app for FK references
        self.conn.execute(
            "INSERT INTO apps(id, git_url, git_branch, created_at)"
            " VALUES ('app1', 'url', 'main', 't')"
        )
        self.conn.commit()

    def teardown_method(self):
        self.conn.close()

    def _create_task(self, task_id="CICD-0001", app_id=None):
        ts = beijing_timestamp()
        cicd_repo.create_task(
            self.conn,
            task_id=task_id,
            app_name="test-app",
            app_id=app_id,
            repo_type="git",
            repo_name="ssh://gerrit/hpc_test",
            branch="maca",
            build_product=["maca"],
            community_artifact=["image"],
            build_image="base:latest",
            test_timeout=40,
            owner_username="rm",
            status="Running",
            notes="",
            created_at=ts,
            updated_at=ts,
        )
        self.conn.commit()

    def test_next_cicd_id_empty(self):
        assert cicd_repo.next_cicd_id(self.conn) == "CICD-0001"

    def test_next_cicd_id_increments(self):
        self._create_task("CICD-0042")
        assert cicd_repo.next_cicd_id(self.conn) == "CICD-0043"

    def test_create_and_get_task(self):
        self._create_task(app_id="app1")
        task = cicd_repo.get_task(self.conn, "CICD-0001")
        assert task is not None
        assert task["app_id"] == "app1"
        assert isinstance(task["build_product"], list)

    def test_tasks_for_app(self):
        self._create_task("CICD-0001", app_id="app1")
        tasks = cicd_repo.tasks_for_app(self.conn, "app1")
        assert len(tasks) == 1
        assert tasks[0]["id"] == "CICD-0001"

    def test_tasks_for_app_empty(self):
        self._create_task("CICD-0001", app_id=None)
        assert cicd_repo.tasks_for_app(self.conn, "app1") == []

    def test_find_tasks_by_identity(self):
        self._create_task()
        found = cicd_repo.find_tasks_by_identity(self.conn, "ssh://gerrit/hpc_test", "maca")
        assert len(found) == 1

    def test_find_tasks_by_identity_no_match(self):
        self._create_task()
        assert cicd_repo.find_tasks_by_identity(self.conn, "other", "maca") == []

    def test_partial_unique_index_multiple_nulls_allowed(self):
        """Multiple tasks with app_id=NULL are allowed (orphan tasks)."""
        self._create_task("CICD-0001", app_id=None)
        ts = beijing_timestamp()
        cicd_repo.create_task(
            self.conn, task_id="CICD-0002", app_name="other",
            app_id=None, owner_username="rm",
            created_at=ts, updated_at=ts,
        )
        self.conn.commit()
        assert len(cicd_repo.list_tasks(self.conn)) == 2

    def test_partial_unique_index_duplicate_app_id_rejected(self):
        """Two tasks with the same non-null app_id must be rejected."""
        import sqlite3
        self._create_task("CICD-0001", app_id="app1")
        ts = beijing_timestamp()
        with pytest.raises(sqlite3.IntegrityError):
            cicd_repo.create_task(
                self.conn, task_id="CICD-0002", app_name="dup",
                app_id="app1", owner_username="rm",
                created_at=ts, updated_at=ts,
            )

    def test_list_tasks_with_status_filter(self):
        self._create_task("CICD-0001", app_id=None)
        ts = beijing_timestamp()
        cicd_repo.create_task(
            self.conn, task_id="CICD-0002", app_name="stopped",
            app_id=None, owner_username="rm", status="Stopped",
            created_at=ts, updated_at=ts,
        )
        self.conn.commit()
        running = cicd_repo.list_tasks(self.conn, status_filter="Running")
        assert len(running) == 1
        assert running[0]["id"] == "CICD-0001"

    def test_insert_request_and_get(self):
        self._create_task()
        req_id = cicd_repo.insert_request(
            self.conn,
            task_id="CICD-0001",
            request_type="modify",
            payload={"notes": {"old": "", "new": "test"}},
            submitter="rm",
            submitted_at=beijing_timestamp(),
            status="pending",
        )
        self.conn.commit()
        req = cicd_repo.get_request(self.conn, req_id)
        assert req is not None
        assert req["request_type"] == "modify"
        assert req["payload"] == {"notes": {"old": "", "new": "test"}}

    def test_has_open_modify_on_field_true(self):
        self._create_task()
        cicd_repo.insert_request(
            self.conn,
            task_id="CICD-0001",
            request_type="modify",
            payload={"status": {"old": "Running", "new": "Stopped"}},
            submitter="rm",
            submitted_at=beijing_timestamp(),
            status="pending",
            origin="release_decision_sync",
        )
        self.conn.commit()
        assert cicd_repo.has_open_modify_on_field(self.conn, "CICD-0001", "status") is True
        assert cicd_repo.has_open_modify_on_field(self.conn, "CICD-0001", "notes") is False

    def test_has_open_modify_on_field_false_after_approval(self):
        self._create_task()
        req_id = cicd_repo.insert_request(
            self.conn,
            task_id="CICD-0001",
            request_type="modify",
            payload={"status": {"old": "Running", "new": "Stopped"}},
            submitter="rm",
            submitted_at=beijing_timestamp(),
            status="pending",
        )
        self.conn.commit()
        cicd_repo.update_request(self.conn, req_id, status="approved")
        self.conn.commit()
        assert cicd_repo.has_open_modify_on_field(self.conn, "CICD-0001", "status") is False

    def test_pending_task_ids(self):
        self._create_task()
        cicd_repo.insert_request(
            self.conn, task_id="CICD-0001", request_type="modify",
            payload={}, submitter="rm", submitted_at=beijing_timestamp(),
            status="pending",
        )
        self.conn.commit()
        ids = cicd_repo.pending_task_ids(self.conn)
        assert "CICD-0001" in ids

    def test_origin_column_defaults(self):
        self._create_task()
        req_id = cicd_repo.insert_request(
            self.conn, task_id="CICD-0001", request_type="create",
            payload={}, submitter="rm", submitted_at=beijing_timestamp(),
        )
        self.conn.commit()
        req = cicd_repo.get_request(self.conn, req_id)
        assert req["origin"] == "cicd_workbench"

    def test_origin_column_release_decision_sync(self):
        self._create_task()
        req_id = cicd_repo.insert_request(
            self.conn, task_id="CICD-0001", request_type="modify",
            payload={"status": {"old": "Running", "new": "Stopped"}},
            submitter="rm", submitted_at=beijing_timestamp(),
            origin="release_decision_sync",
        )
        self.conn.commit()
        req = cicd_repo.get_request(self.conn, req_id)
        assert req["origin"] == "release_decision_sync"

    def test_list_requests_since_cutoff(self):
        """since_cutoff uses pre-computed Beijing timestamp (DA C5 fix)."""
        self._create_task()
        cicd_repo.insert_request(
            self.conn, task_id="CICD-0001", request_type="modify",
            payload={}, submitter="rm",
            submitted_at="2026-01-01 00:00:00",
        )
        self.conn.commit()
        # With a cutoff after submission → empty
        results = cicd_repo.list_requests(self.conn, since_cutoff="2026-06-01 00:00:00")
        assert results == []
        # With a cutoff before submission → returns result
        results2 = cicd_repo.list_requests(self.conn, since_cutoff="2025-01-01 00:00:00")
        assert len(results2) == 1

    def test_mark_notification_visited(self):
        cicd_repo.mark_notification_visited(self.conn, "rm", "2026-01-01 00:00:00")
        self.conn.commit()
        counts = cicd_repo.notification_counts(self.conn, "rm", "Owner")
        assert counts["last_visited_at"] == "2026-01-01 00:00:00"

    def test_apply_modify_fields_status(self):
        """apply_modify_fields can write 'status' for decision-sync callers."""
        self._create_task(app_id=None)
        ts = beijing_timestamp()
        cicd_repo.apply_modify_fields(self.conn, "CICD-0001", {"status": "Stopped"}, updated_at=ts)
        self.conn.commit()
        task = cicd_repo.get_task(self.conn, "CICD-0001")
        assert task["status"] == "Stopped"

    def test_delete_task(self):
        self._create_task()
        cicd_repo.insert_request(
            self.conn, task_id="CICD-0001", request_type="create",
            payload={}, submitter="rm", submitted_at=beijing_timestamp(),
        )
        self.conn.commit()
        cicd_repo.delete_task(self.conn, "CICD-0001")
        self.conn.commit()
        assert cicd_repo.get_task(self.conn, "CICD-0001") is None
        # requests also deleted
        assert cicd_repo.list_requests(self.conn, task_id="CICD-0001") == []


# ---------------------------------------------------------------------------
# wiki_repo.py
# ---------------------------------------------------------------------------

class TestWikiRepo:
    def setup_method(self):
        self.conn = fresh_conn()

    def teardown_method(self):
        self.conn.close()

    def test_insert_and_list(self):
        wiki_repo.insert_article(
            self.conn, article_id="w1", title="Test", body_md="# body",
            pinned=0, created_by="rm", created_at="2026-01-01 00:00:00",
        )
        self.conn.commit()
        articles = wiki_repo.list_articles(self.conn)
        assert len(articles) == 1
        assert articles[0]["title"] == "Test"
        assert articles[0]["pinned"] is False

    def test_get_article(self):
        wiki_repo.insert_article(
            self.conn, article_id="w1", title="Test", body_md="body",
            pinned=0, created_by="rm", created_at="t",
        )
        self.conn.commit()
        article = wiki_repo.get_article(self.conn, "w1")
        assert article is not None
        assert article["id"] == "w1"

    def test_soft_delete(self):
        wiki_repo.insert_article(
            self.conn, article_id="w1", title="Test", body_md="",
            pinned=0, created_by="rm", created_at="t",
        )
        self.conn.commit()
        wiki_repo.soft_delete_article(self.conn, "w1", deleted_by="rm", deleted_at="t2")
        self.conn.commit()
        # not visible in normal list
        assert wiki_repo.list_articles(self.conn) == []
        # but visible when include_deleted=True
        all_arts = wiki_repo.list_articles(self.conn, include_deleted=True)
        assert len(all_arts) == 1
        assert all_arts[0]["deleted"] is True

    def test_get_deleted_returns_none(self):
        wiki_repo.insert_article(
            self.conn, article_id="w1", title="Test", body_md="",
            pinned=0, created_by="rm", created_at="t",
        )
        wiki_repo.soft_delete_article(self.conn, "w1", deleted_by="rm", deleted_at="t2")
        self.conn.commit()
        assert wiki_repo.get_article(self.conn, "w1") is None


# ---------------------------------------------------------------------------
# identity.py (domain layer)
# ---------------------------------------------------------------------------

class TestIdentity:
    def test_normalize_git_url_short(self):
        from app.identity import RESOLVED_REPO_BASE, normalize_git_url
        result = normalize_git_url("hpc_hpl")
        assert result == f"{RESOLVED_REPO_BASE}/hpc_hpl"

    def test_normalize_git_url_absolute(self):
        from app.identity import normalize_git_url
        url = "ssh://sw-gerrit-devops.metax-internal.com:29418/PDE/HPC/hpc_hpl"
        assert normalize_git_url(url) == url

    def test_normalize_git_url_empty(self):
        from app.identity import normalize_git_url
        assert normalize_git_url("") == ""

    def test_normalize_git_url_git_at(self):
        from app.identity import normalize_git_url
        url = "git@github.com:org/repo.git"
        assert normalize_git_url(url) == url

    def test_same_identity_short_vs_full(self):
        from app.identity import RESOLVED_REPO_BASE, same_identity
        short = "hpc_hpl"
        full = f"{RESOLVED_REPO_BASE}/hpc_hpl"
        assert same_identity(short, "main", full, "main") is True

    def test_same_identity_different_branch(self):
        from app.identity import same_identity
        assert same_identity("hpc_hpl", "main", "hpc_hpl", "maca") is False

    def test_same_identity_empty_url(self):
        from app.identity import same_identity
        assert same_identity("", "main", "hpc_hpl", "main") is False

    def test_repo_to_git_identity_git_type(self):
        from app.identity import RESOLVED_REPO_BASE, repo_to_git_identity
        url, branch = repo_to_git_identity("git", "hpc_test", "maca")
        assert url == f"{RESOLVED_REPO_BASE}/hpc_test"
        assert branch == "maca"

    def test_repo_to_git_identity_absolute(self):
        from app.identity import repo_to_git_identity
        full = "ssh://sw-gerrit-devops.metax-internal.com:29418/PDE/HPC/hpc_test"
        url, branch = repo_to_git_identity("git", full, "main")
        assert url == full
        assert branch == "main"
