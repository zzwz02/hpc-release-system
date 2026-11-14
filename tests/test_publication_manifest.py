"""Publication evidence survives generator changes, corrections and source changes."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
import threading

import pytest
from fastapi.testclient import TestClient

from app.api.errors import ApiError
from app.db.connection import connect, init_db
from app.deps import get_db, require_login
from app.domain import delivery_images, publication_manifest
from app.main import create_app
from app.repositories import app_info_repo, entries_repo, official_repo
from app.services import catalog_service, cycle_service, lookup_service, qa_service
from tests.conftest import close_qa, make_app_info, make_dependency_app_info, seed_app, seed_cycle, seed_signup, store_app_info


@pytest.fixture()
def publication(temp_db, harbor_artifacts):
    raw = make_dependency_app_info("archived")
    raw["app_test"]["sanity"]["test_cmd"] = "printf '%s\\n' \"original command\"\n./run-test --flag='a b'"
    raw["app_test"]["excluded"] = {**raw["app_test"]["sanity"], "test_cmd": "must-not-be-published"}
    app = seed_app(temp_db, "archived", app_info=raw, commit_id="published-commit")
    release = seed_cycle(temp_db, name="publication-cycle")
    cycle_service.save_environment(temp_db, release, [{"component": "SDK", "version": "R1", "note": "original environment"}], user="rm", role="RM")
    entry = seed_signup(temp_db, release, app, target_version="release-version", excluded_tests=["excluded"])
    images = [{**build, "reference": f"harbor.sh.mxcr.io/hpc-release/{build['image_pattern'].partition(':')[0]}@sha256:{digit * 64}"}
              for build, digit in zip((build for build in entry["delivery_builds"] if build["os"] != "ubuntu24.04"), "abcde")]
    harbor_artifacts(images)
    qa_service.set_qa_status_batch(temp_db, release, [{"app_id": app, "status": "has_issues", "issue_note": "original QA note",
        "delivery_revision": entry["delivery_revision"], "deliverable_images": images}], user="qa", role="QA")
    close_qa(temp_db, release)
    return app, release, entry, raw


def publish(conn, publication):
    cycle_service.publish(conn, publication[1], user="rm", role="RM")
    return official_repo.current_by_app(conn)[publication[0]]


def test_capture_contains_complete_publication_scope_platforms_images_and_qa(temp_db, publication):
    official = publish(temp_db, publication)
    manifest = official["test_manifest"]
    assert manifest["schema_version"] == 1
    assert manifest["official_version_id"] == official["id"]
    assert manifest["published_at"] == official["released_at"]
    assert manifest["published_by"] == "rm"
    assert manifest["app"]["id"] == publication[0]
    assert manifest["release"]["id"] == publication[1]
    assert manifest["release"]["environment"][0]["version"] == "R1"
    assert manifest["entry"]["target_version"] == "release-version"
    assert manifest["app_info"]["commit_id"] == "published-commit"
    assert manifest["app_info"]["raw"] == publication[3]
    assert manifest["qa"] == {"level": "app", "status": "has_issues", "note": "original QA note"}
    assert "original QA note" in manifest["entry"]["known_issues"]
    assert manifest["scope"]["excluded_tests"] == ["excluded"]
    assert [test["path"] for test in manifest["scope"]["tests"]] == ["sanity"]
    commands = manifest["commands"]
    assert len(commands) == 5
    assert {command["arch"] for command in commands} == {"amd64", "arm64"}
    assert {command["image_target"] for command in commands} == {"release", "dbg"}
    assert all("must-not-be-published" not in command["command"] for command in commands)
    assert all("-v /pde_hpc/dataset:/hpc_dataset:ro" in command["command"] for command in commands)
    assert all("--device=/dev/dri" in command["command"] for command in commands)
    assert all(command["delivery_reference"] in command["command"] for command in commands if command["delivery_reference"])
    assert any(not command["delivery_reference"] and "<hpc_version>" in command["command"] for command in commands)
    assert any(image["os"] == "centos9" for image in manifest["images"])  # image with no test stays archived
    assert manifest["dependencies"] == ["sdk", "torch 2.4 + py310", "torch 2.8 + py312"]


def test_reads_and_csv_keep_exact_commands_when_generators_and_live_records_change(temp_db, publication, monkeypatch):
    official = publish(temp_db, publication)
    frozen = deepcopy(official["test_manifest"])
    baseline = lookup_service.latest_running_releases(temp_db)
    exported = catalog_service.compat_list_csv(temp_db)
    assert "发布时封存" in exported
    changed = make_app_info(app_name="archived", dependencies=["development-sdk"])
    store_app_info(temp_db, publication[0], changed, commit_id="development-commit")
    # Even mutable entry data and a missing source pointer cannot rewrite sealed evidence.
    temp_db.execute("UPDATE entries SET excluded_tests_json='[]', qa_note='changed QA' WHERE id=?", (publication[2]["id"],))
    temp_db.execute("UPDATE official_versions SET app_info_version_id=NULL WHERE id=?", (official["id"],))
    temp_db.execute("UPDATE app_info_versions SET raw_json='{}', commit_id='mutated-source' WHERE id=?", (frozen["app_info"]["id"],))
    temp_db.commit()
    monkeypatch.setattr(delivery_images, "published_commands", lambda *_args: pytest.fail("sealed reads must not regenerate commands"))
    assert lookup_service.latest_running_releases(temp_db) == baseline
    assert catalog_service.compat_list_csv(temp_db) == exported
    assert catalog_service.app_detail(temp_db, publication[0])["official_versions"][0]["test_manifest"] == frozen
    history = lookup_service.by_app(temp_db, publication[0])["entries"][0]
    assert history["test_commands"] == frozen["commands"]
    assert history["entry"]["qa_note"] == "original QA note"
    assert [test["path"] for test in history["scope_tests"]] == ["sanity"]
    assert lookup_service.published_apps(temp_db, publication[1])["apps"][0]["qa_note"] == "original QA note"
    app = create_app()
    app.dependency_overrides[get_db] = lambda: temp_db
    app.dependency_overrides[require_login] = lambda: {"username": "qa", "role": "QA"}
    with TestClient(app) as client:
        response = client.get("/api/lookup/latest-running")
        assert response.status_code == 200
        assert response.json() == baseline
        archived_detail = client.get("/api/apps/detail", params={"app_id": publication[0]})
        assert archived_detail.status_code == 200
        assert archived_detail.json()["official_versions"][0]["test_manifest"] == frozen
        csv_response = client.get("/api/dependencies/compat.csv")
        assert csv_response.status_code == 200
        assert csv_response.text.lstrip('\ufeff') == exported


def test_unlock_deletes_original_manifest_and_republication_saves_only_replacement(temp_db, publication):
    original = publish(temp_db, publication)
    cycle_service.unlock(temp_db, publication[1], reason="correct scope and QA", user="rm", role="RM")
    assert temp_db.execute("SELECT COUNT(*) FROM official_versions WHERE release_id=?", (publication[1],)).fetchone()[0] == 0
    assert catalog_service.app_detail(temp_db, publication[0])["official_versions"] == []
    entries_repo.update_entry(temp_db, publication[2]["id"], {"excluded_tests": [], "qa_status": "qa_passed", "qa_note": ""},
                              updated_at="2026-01-01 00:00:00", updated_by="qa")
    temp_db.commit()
    current = publish(temp_db, publication)
    assert current["id"] != original["id"]
    assert current["test_manifest"]["qa"]["status"] == "qa_passed"
    assert {command["path"] for command in current["test_manifest"]["commands"]} == {"sanity", "excluded"}
    history = official_repo.history_for_app(temp_db, publication[0])
    assert len(history) == 1 and history[0]["id"] == current["id"]
    assert temp_db.execute("SELECT test_manifest_json FROM official_versions WHERE id=?", (original["id"],)).fetchone() is None


def test_legacy_reconstruction_is_labeled_and_never_backfilled(temp_db, publication):
    official = publish(temp_db, publication)
    temp_db.execute("UPDATE official_versions SET test_manifest_json=NULL WHERE id=?", (official["id"],))
    temp_db.commit()
    before = temp_db.total_changes
    rows = lookup_service.latest_running_releases(temp_db)["rows"]
    assert all(row["manifest_source"] == "reconstructed" and "未在发布时封存" in row["note"] for row in rows)
    assert any(row.get("command") for row in rows)
    assert temp_db.total_changes == before
    assert official_repo.current_by_app(temp_db)[publication[0]]["test_manifest"] is None
    assert "根据历史配置生成" in catalog_service.compat_list_csv(temp_db)


@pytest.mark.parametrize("damage", ["schema", "identity"])
def test_unreadable_archive_refuses_silent_reconstruction(temp_db, publication, damage):
    official = publish(temp_db, publication)
    manifest = official["test_manifest"]
    if damage == "schema":
        manifest["schema_version"] = 999
    else:
        manifest["app"]["id"] = "different-app"
    temp_db.execute("UPDATE official_versions SET test_manifest_json=? WHERE id=?", (json.dumps(manifest), official["id"]))
    temp_db.commit()
    with pytest.raises(ApiError) as error:
        lookup_service.latest_running_releases(temp_db)
    assert error.value.status_code == 409


@pytest.mark.parametrize("invalid_json", ["", "null", "{"])
def test_corrupted_archive_json_does_not_become_legacy_reconstruction(temp_db, publication, invalid_json):
    official = publish(temp_db, publication)
    temp_db.execute("UPDATE official_versions SET test_manifest_json=? WHERE id=?", (invalid_json, official["id"]))
    temp_db.commit()
    with pytest.raises(ValueError, match="发布测试清单 JSON 损坏"):
        lookup_service.latest_running_releases(temp_db)


def test_capture_failure_rolls_back_all_publication_results(temp_db, monkeypatch):
    release = seed_cycle(temp_db)
    for name in ("a", "b"):
        seed_app(temp_db, name)
        seed_signup(temp_db, release, name)
    qa_service.set_qa_status_batch(temp_db, release, [{"app_id": name, "status": "qa_passed"} for name in ("a", "b")], user="qa", role="QA")
    close_qa(temp_db, release)
    capture = publication_manifest.capture
    def fail_second(**kwargs):
        if kwargs["app"]["id"] == "b":
            raise ValueError("capture failed")
        return capture(**kwargs)
    monkeypatch.setattr(publication_manifest, "capture", fail_second)
    with pytest.raises(ValueError, match="capture failed"):
        cycle_service.publish(temp_db, release, user="rm", role="RM")
    assert official_repo.current_by_app(temp_db) == {}
    assert all(entry["result"] == "" for entry in entries_repo.list_for_release(temp_db, release))
    assert not cycle_service.get_release(temp_db, release)["released_locked"]
    assert temp_db.execute("SELECT COUNT(*) FROM artifacts WHERE release_id=?", (release,)).fetchone()[0] == 0
    assert temp_db.execute("SELECT COUNT(*) FROM audit WHERE release_id=? AND event='publish'", (release,)).fetchone()[0] == 0


@pytest.mark.parametrize("missing", [True, False])
def test_publish_requires_an_owned_app_info_snapshot(temp_db, publication, missing):
    version_id = None
    if not missing:
        other = seed_app(temp_db, "other")
        version_id = app_info_repo.latest_version(temp_db, other)["id"]
    temp_db.execute("UPDATE entries SET locked_version_id=? WHERE id=?", (version_id, publication[2]["id"]))
    temp_db.commit()
    with pytest.raises(ValueError, match="快照缺失或归属不符"):
        publish(temp_db, publication)
    assert official_repo.current_by_app(temp_db) == {}
    assert entries_repo.get_entry(temp_db, publication[2]["id"])["result"] == ""


def test_schema_upgrade_keeps_existing_publications_unsealed(temp_db, publication):
    official = publish(temp_db, publication)
    temp_db.execute("ALTER TABLE official_versions DROP COLUMN test_manifest_json")
    temp_db.commit()
    init_db(temp_db)
    init_db(temp_db)
    row = official_repo.current_by_app(temp_db)[publication[0]]
    assert row["id"] == official["id"] and row["test_manifest"] is None
    assert row["images"] == official["images"]


def test_simultaneous_publications_create_only_one_manifest(temp_db, publication):
    path = temp_db.execute("PRAGMA database_list").fetchone()["file"]
    barrier = threading.Barrier(2)
    def attempt(_):
        conn = connect(path)
        try:
            barrier.wait(timeout=5)
            try:
                cycle_service.publish(conn, publication[1], user="rm", role="RM")
                return "published"
            except RuntimeError:
                return "already-published"
        finally:
            conn.close()
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(attempt, range(2))) == ["already-published", "published"]
    assert temp_db.execute("SELECT COUNT(*) FROM official_versions").fetchone()[0] == 1
    assert official_repo.current_by_app(temp_db)[publication[0]]["test_manifest"] is not None
