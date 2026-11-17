"""Per-App/cycle build locks, final publication and upgrades use the same stored flag."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from fastapi.testclient import TestClient

from app.api.errors import ApiError, AuthzError
from app.db.connection import connect, init_db
from app.deps import get_db, require_login
from app.main import create_app
from app.repositories import audit_repo, entries_repo
from app.services import artifact_service, cycle_service, qa_service, state_service
from tests.conftest import close_qa, seed_app, seed_cycle, seed_signup


@pytest.fixture()
def signup(temp_db):
    app = seed_app(temp_db)
    release = seed_cycle(temp_db, name="3.9.0")
    return seed_signup(temp_db, release, app)


def change(conn, entry, locked=True, role="QA", revision=None):
    return qa_service.set_build_lock(conn, entry["release_id"], entry["app_id"], locked=locked,
                                    expected_revision=entry["build_lock_revision"] if revision is None else revision,
                                    user="reviewer", role=role)


def test_lock_is_independent_per_app_and_cycle_and_keeps_qa_fields(temp_db, signup):
    second_cycle = seed_cycle(temp_db, name="3.10.0")
    other_cycle = seed_signup(temp_db, second_cycle, signup["app_id"])
    other_app = seed_app(temp_db, "other")
    other = seed_signup(temp_db, signup["release_id"], other_app)
    change(temp_db, signup)
    stored = entries_repo.get_entry(temp_db, signup["id"])
    assert stored["build_locked"] and stored["build_lock_revision"] == 1
    assert stored["build_lock_updated_at"] and stored["build_lock_updated_by"] == "reviewer"
    for field in ("qa_status", "qa_note", "deliverable_images", "locked_version_id"):
        assert stored[field] == signup[field]
    assert not entries_repo.get_entry(temp_db, other_cycle["id"])["build_locked"]
    assert not entries_repo.get_entry(temp_db, other["id"])["build_locked"]
    state = state_service.get_state(temp_db, user={"username": "qa", "role": "QA"}, release_id_param=signup["release_id"])
    assert "qa.build_lock" in state["release"]["entries"][0]["allowed_actions"]
    audit = state["qa_audit_logs"][signup["app_id"]][0]
    assert audit["event"] == "qa_build_lock" and audit["detail"][0]["new"] is True
    change(temp_db, stored, False, "RM")
    assert not entries_repo.get_entry(temp_db, signup["id"])["build_locked"]


def test_stale_edits_and_aba_changes_are_rejected_without_extra_audit(temp_db, signup):
    change(temp_db, signup)
    with pytest.raises(ApiError, match="已变化") as error:
        change(temp_db, signup, False)
    assert error.value.status_code == 409
    current = entries_repo.get_entry(temp_db, signup["id"])
    change(temp_db, current)  # Same desired state is an idempotent no-op.
    assert entries_repo.get_entry(temp_db, signup["id"])["build_lock_revision"] == 1
    change(temp_db, current, False)
    with pytest.raises(ApiError, match="已变化"):
        change(temp_db, signup)
    assert temp_db.execute("SELECT count(*) FROM audit WHERE event='qa_build_lock'").fetchone()[0] == 2


@pytest.mark.parametrize("role,expected", [("QA", 200), ("RM", 200), ("Owner", 403), ("Guest", 403), ("SPD", 403), ("Admin", 403)])
def test_http_permissions_and_database_readback(temp_db, signup, role, expected):
    app = create_app()
    app.dependency_overrides[get_db] = lambda: temp_db
    app.dependency_overrides[require_login] = lambda: {"username": "reviewer", "role": role}
    with TestClient(app) as client:
        response = client.post("/api/qa/build-lock", json={"release_id": signup["release_id"],
            "app_id": signup["app_id"], "locked": True, "expected_revision": 0})
        assert response.status_code == expected, response.text
        assert entries_repo.get_entry(temp_db, signup["id"])["build_locked"] == (expected == 200)
        payload = state_service.get_state(temp_db, user={"username": "reviewer", "role": role}, release_id_param=signup["release_id"])
        assert bool(payload["release"]["entries"][0]["allowed_actions"]) == (expected == 200)
    if expected != 200:
        with pytest.raises(AuthzError):
            change(temp_db, signup, role=role)


@pytest.mark.parametrize("invalid", [{"locked": "false"}, {"locked": 1}, {"expected_revision": True},
                                    {"expected_revision": -1}, {"app_id": " "}, {"unexpected": True}])
def test_http_rejects_ambiguous_or_invalid_writes(temp_db, signup, invalid):
    app = create_app()
    app.dependency_overrides[get_db] = lambda: temp_db
    app.dependency_overrides[require_login] = lambda: {"username": "qa", "role": "QA"}
    with TestClient(app) as client:
        response = client.post("/api/qa/build-lock", json={"release_id": signup["release_id"],
            "app_id": signup["app_id"], "locked": True, "expected_revision": 0, **invalid})
    assert response.status_code == 422
    assert not entries_repo.get_entry(temp_db, signup["id"])["build_locked"]


def test_withdrawal_and_resign_cannot_clear_a_version_lock(temp_db, signup):
    change(temp_db, signup)
    cycle_service.withdraw(temp_db, signup["id"], user="rm", role="RM")
    with pytest.raises(ApiError, match="已撤回"):
        change(temp_db, signup, False, revision=1)
    again = seed_signup(temp_db, signup["release_id"], signup["app_id"])
    assert again["build_locked"] and again["build_lock_revision"] == 1


def test_publication_locks_all_entries_and_unlock_preserves_build_locks(temp_db, signup):
    failed = seed_signup(temp_db, signup["release_id"], seed_app(temp_db, "failed"))
    withdrawn = seed_signup(temp_db, signup["release_id"], seed_app(temp_db, "withdrawn"))
    cycle_service.withdraw(temp_db, withdrawn["id"], user="rm", role="RM")
    qa_service.set_qa_status_batch(temp_db, signup["release_id"], [{"app_id": signup["app_id"], "status": "qa_passed"}], user="qa", role="QA")
    close_qa(temp_db, signup["release_id"])
    cycle_service.publish(temp_db, signup["release_id"], user="rm", role="RM")
    for entry in (signup, failed, withdrawn):
        stored = entries_repo.get_entry(temp_db, entry["id"])
        assert stored["build_locked"] and stored["build_lock_updated_by"] == "rm"
    with pytest.raises(RuntimeError, match="已发布"):
        change(temp_db, signup, False, revision=1)
    cycle_service.unlock(temp_db, signup["release_id"], reason="更正", user="rm", role="RM")
    stored = entries_repo.get_entry(temp_db, signup["id"])
    assert stored["build_locked"]
    change(temp_db, stored, False)
    assert not entries_repo.get_entry(temp_db, signup["id"])["build_locked"]


def test_publication_failure_rolls_back_locks_and_audit(temp_db, signup, monkeypatch):
    close_qa(temp_db, signup["release_id"])
    def fail(*args):
        assert entries_repo.get_entry(temp_db, signup["id"])["build_locked"]
        raise RuntimeError("artifact failure")
    monkeypatch.setattr(artifact_service, "generate_final_artifacts", fail)
    with pytest.raises(RuntimeError, match="artifact failure"):
        cycle_service.publish(temp_db, signup["release_id"], user="rm", role="RM")
    assert not entries_repo.get_entry(temp_db, signup["id"])["build_locked"]
    assert not cycle_service.get_release(temp_db, signup["release_id"])["released_locked"]
    assert temp_db.execute("SELECT count(*) FROM audit WHERE event='qa_build_lock'").fetchone()[0] == 0


def test_existing_schema_upgrade_backfills_only_final_cycles_and_is_idempotent(temp_db, signup):
    open_cycle = seed_cycle(temp_db, name="3.10.0")
    current = seed_signup(temp_db, open_cycle, signup["app_id"])
    close_qa(temp_db, signup["release_id"])
    cycle_service.publish(temp_db, signup["release_id"], user="rm", role="RM")
    for field in ("build_locked", "build_lock_revision", "build_lock_updated_at", "build_lock_updated_by"):
        temp_db.execute(f"ALTER TABLE entries DROP COLUMN {field}")
    temp_db.commit()
    init_db(temp_db)
    stored = entries_repo.get_entry(temp_db, signup["id"])
    assert stored["build_locked"] and stored["build_lock_revision"] == 1
    assert stored["build_lock_updated_by"] == "system"
    assert not entries_repo.get_entry(temp_db, current["id"])["build_locked"]
    before = temp_db.execute("SELECT count(*) FROM audit").fetchone()[0]
    init_db(temp_db)
    assert entries_repo.get_entry(temp_db, signup["id"]) == stored
    assert temp_db.execute("SELECT count(*) FROM audit").fetchone()[0] == before


def test_two_concurrent_reviewers_cannot_overwrite_the_same_revision(temp_db, signup, tmp_dir):
    barrier = Barrier(2)
    def write():
        conn = connect(tmp_dir / "test.db")
        try:
            barrier.wait(timeout=10)
            try:
                change(conn, signup)
                return 200
            except ApiError as error:
                return error.status_code
        finally:
            conn.close()
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: write(), range(2)))
    assert sorted(results) == [200, 409]
    assert entries_repo.get_entry(temp_db, signup["id"])["build_lock_revision"] == 1


def batch(conn, release_id, locked=True, *, items=None, role="QA"):
    if items is None:
        items = [{"app_id": entry["app_id"], "expected_revision": entry["build_lock_revision"]}
                 for entry in entries_repo.list_for_release(conn, release_id)]
    return qa_service.set_build_locks_batch(conn, release_id, locked=locked, items=items, user="reviewer", role=role)


def test_batch_locks_and_unlocks_only_current_active_signups(temp_db, signup):
    second = seed_signup(temp_db, signup["release_id"], seed_app(temp_db, "second"))
    withdrawn = seed_signup(temp_db, signup["release_id"], seed_app(temp_db, "withdrawn"))
    cycle_service.withdraw(temp_db, withdrawn["id"], user="rm", role="RM")
    other_cycle = seed_cycle(temp_db, name="3.10.0")
    other = seed_signup(temp_db, other_cycle, signup["app_id"])
    assert batch(temp_db, signup["release_id"])["updated"] == 2
    assert all(entries_repo.get_entry(temp_db, entry["id"])["build_locked"] for entry in (signup, second))
    assert not entries_repo.get_entry(temp_db, withdrawn["id"])["build_locked"]
    assert not entries_repo.get_entry(temp_db, other["id"])["build_locked"]
    assert batch(temp_db, signup["release_id"])["updated"] == 0
    assert batch(temp_db, signup["release_id"], False, role="RM")["updated"] == 2
    assert all(not entries_repo.get_entry(temp_db, entry["id"])["build_locked"] for entry in (signup, second))
    assert temp_db.execute("SELECT count(*) FROM audit WHERE event='qa_build_lock'").fetchone()[0] == 0
    logs = audit_repo.release_audit_log(temp_db, signup["release_id"], "qa_build_lock_batch")
    assert len(logs) == 2
    assert all(log["app_id"] == "" and len(log["detail"]) == 2 for log in logs)
    assert {item["app_id"] for item in logs[0]["detail"]} == {signup["app_id"], second["app_id"]}
    assert all(item["old"] is True and item["new"] is False for item in logs[0]["detail"])
    for entry in (signup, second):
        history = [log for log in audit_repo.app_audit_log(temp_db, entry["app_id"], signup["release_id"])
                   if log["event"] == "qa_build_lock_batch"]
        assert len(history) == 2
        assert all(len(log["detail"]) == 1 and log["detail"][0]["app_id"] == entry["app_id"] for log in history)
    assert not any(log["event"] == "qa_build_lock_batch"
                   for log in audit_repo.app_audit_log(temp_db, withdrawn["app_id"]))
    assert not any(log["event"] == "qa_build_lock_batch"
                   for log in audit_repo.app_audit_log(temp_db, signup["app_id"], other_cycle))
    state = state_service.get_state(temp_db, user={"username": "qa", "role": "QA"}, release_id_param=signup["release_id"])
    assert len(state["qa_batch_audit_logs"]) == 2
    assert all(not app_logs for app_logs in state["qa_audit_logs"].values())


@pytest.mark.parametrize("change_kind", ["revision", "signup", "withdraw"])
def test_stale_batch_rejects_the_entire_request(temp_db, signup, change_kind):
    second = seed_signup(temp_db, signup["release_id"], seed_app(temp_db, "second"))
    items = [{"app_id": entry["app_id"], "expected_revision": 0} for entry in (signup, second)]
    if change_kind == "revision":
        change(temp_db, second)
    elif change_kind == "signup":
        seed_signup(temp_db, signup["release_id"], seed_app(temp_db, "new"))
    else:
        cycle_service.withdraw(temp_db, second["id"], user="rm", role="RM")
    before = [dict(row) for row in temp_db.execute("SELECT * FROM entries")]
    audit_count = temp_db.execute("SELECT count(*) FROM audit").fetchone()[0]
    with pytest.raises(ApiError, match="未保存任何改动") as error:
        batch(temp_db, signup["release_id"], items=items)
    assert error.value.status_code == 409
    assert [dict(row) for row in temp_db.execute("SELECT * FROM entries")] == before
    assert temp_db.execute("SELECT count(*) FROM audit").fetchone()[0] == audit_count


def test_batch_rolls_back_all_locks_and_audit_on_write_failure(temp_db, signup, monkeypatch):
    seed_signup(temp_db, signup["release_id"], seed_app(temp_db, "second"))
    def fail(*args, **kwargs):
        assert kwargs["event"] == "qa_build_lock_batch" and len(kwargs["detail"]) == 2
        assert all(entry["build_locked"] for entry in entries_repo.list_for_release(temp_db, signup["release_id"]))
        raise RuntimeError("audit storage failed")
    monkeypatch.setattr(qa_service, "log_audit", fail)
    with pytest.raises(RuntimeError, match="audit storage failed"):
        batch(temp_db, signup["release_id"])
    assert all(not entry["build_locked"] for entry in entries_repo.list_for_release(temp_db, signup["release_id"]))
    assert temp_db.execute("SELECT count(*) FROM audit WHERE event='qa_build_lock'").fetchone()[0] == 0
    assert temp_db.execute("SELECT count(*) FROM audit WHERE event='qa_build_lock_batch'").fetchone()[0] == 0


def test_batch_audit_contains_only_changed_apps_and_survives_withdrawal(temp_db, signup):
    second = seed_signup(temp_db, signup["release_id"], seed_app(temp_db, "second"))
    change(temp_db, signup)
    assert batch(temp_db, signup["release_id"])["updated"] == 1
    logs = audit_repo.release_audit_log(temp_db, signup["release_id"], "qa_build_lock_batch")
    assert len(logs) == 1 and [item["app_id"] for item in logs[0]["detail"]] == [second["app_id"]]
    for entry in (signup, second):
        cycle_service.withdraw(temp_db, entry["id"], user="rm", role="RM")
    state = state_service.get_state(temp_db, user={"username": "qa", "role": "QA"}, release_id_param=signup["release_id"])
    assert len(state["qa_batch_audit_logs"]) == 1
    assert state["qa_audit_logs"] == {}


@pytest.mark.parametrize("role,expected", [("QA", 200), ("RM", 200), ("Owner", 403), ("Guest", 403), ("SPD", 403), ("Admin", 403)])
def test_batch_http_permission_and_published_cycle_guard(temp_db, signup, role, expected):
    app = create_app()
    app.dependency_overrides[get_db] = lambda: temp_db
    app.dependency_overrides[require_login] = lambda: {"username": "reviewer", "role": role}
    body = {"release_id": signup["release_id"], "locked": True,
            "items": [{"app_id": signup["app_id"], "expected_revision": 0}]}
    with TestClient(app) as client:
        response = client.post("/api/qa/build-lock-batch", json=body)
        assert response.status_code == expected, response.text
        assert entries_repo.get_entry(temp_db, signup["id"])["build_locked"] == (expected == 200)
        if expected == 200:
            close_qa(temp_db, signup["release_id"])
            cycle_service.publish(temp_db, signup["release_id"], user="rm", role="RM")
            body.update(locked=False, items=[{"app_id": signup["app_id"], "expected_revision": 1}])
            assert client.post("/api/qa/build-lock-batch", json=body).status_code == 400
            assert entries_repo.get_entry(temp_db, signup["id"])["build_locked"]


def test_batch_rejects_duplicate_apps(temp_db, signup):
    item = {"app_id": signup["app_id"], "expected_revision": 0}
    with pytest.raises(ValueError, match="不能重复"):
        batch(temp_db, signup["release_id"], items=[item, item])
    assert not entries_repo.get_entry(temp_db, signup["id"])["build_locked"]
