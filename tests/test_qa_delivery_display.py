"""QA-log tag completion is a read projection of the original delivered digest."""
from copy import deepcopy

import pytest
from fastapi.testclient import TestClient

from app.api.errors import ApiError
from app.deps import get_db, require_login
from app.integrations import harbor
from app.main import create_app
from app.repositories import app_info_repo, entries_repo
from app.services import cycle_service, qa_service
from tests.conftest import seed_app, seed_signup
from tests.test_delivery_images import delivery as delivery, save


def legacy_images(conn, delivery):
    save(conn, delivery)
    entry = entries_repo.get_entry(conn, delivery[2]["id"])
    images = deepcopy(entry["deliverable_images"])
    for image in images:
        image["image"] = image["image"].rsplit(":", 1)[0]
        image["reference"] = image["image"] + "@" + image["digest"]
    entries_repo.update_entry(conn, entry["id"], {"deliverable_images": images,
        "deliverable_image": images[0]["image"], "deliverable_digest": images[0]["digest"]},
        updated_at="2026-11-16 10:00:00", updated_by="test")
    conn.commit()
    return entries_repo.get_entry(conn, entry["id"])


def test_qa_log_read_picks_actual_cycle_build_tags_and_does_not_change_saved_images(temp_db, delivery, monkeypatch):
    stored = legacy_images(temp_db, delivery)
    temp_db.execute("UPDATE releases SET name='3.10.0' WHERE id=?", (delivery[1],))
    temp_db.commit()
    calls = []
    def artifact(project, repository, digest):
        calls.append((repository, digest))
        image = next(image for image in stored["deliverable_images"] if image["digest"] == digest)
        tag = image["image_pattern"].partition(":")[2].replace("<hpc_version>", "3.10.0.7")
        return {"tags": [{"name": name} for name in [tag + "-8", tag, tag.replace("3.10.0.7", "3.11.0.1")]]}
    monkeypatch.setattr(harbor, "get_artifact", artifact)
    before = temp_db.total_changes
    result = qa_service.delivery_images_view(temp_db, delivery[1])["entries"][delivery[0]]
    assert len(calls) == 2
    assert result["revision"] == cycle_service.entry_view(temp_db, stored)["delivery_revision"]
    for shown, original in zip(result["images"], stored["deliverable_images"], strict=True):
        assert "-3.10.0.7-" in shown["reference"] and "-8@" not in shown["reference"]
        assert shown["reference"].endswith("@" + original["digest"])
        assert not shown.get("reference_error")
    assert entries_repo.get_entry(temp_db, stored["id"]) == stored
    assert temp_db.total_changes == before


def test_already_recorded_tags_are_kept_without_a_harbor_lookup(temp_db, delivery, monkeypatch):
    save(temp_db, delivery)
    stored = entries_repo.get_entry(temp_db, delivery[2]["id"])
    monkeypatch.setattr(harbor, "get_artifact", lambda *_: pytest.fail("a recorded tag stays unchanged"))
    shown = qa_service.delivery_images_view(temp_db, delivery[1])["entries"][delivery[0]]["images"]
    assert [image["reference"] for image in shown] == [image["reference"] for image in stored["deliverable_images"]]


def test_legacy_artifacts_are_deduplicated_across_apps_in_one_request(temp_db, delivery, monkeypatch):
    stored = legacy_images(temp_db, delivery)
    raw = app_info_repo.get_version(temp_db, stored["locked_version_id"])["raw"]
    seed_app(temp_db, "other", app_info=raw)
    other = seed_signup(temp_db, delivery[1], "other")
    entries_repo.update_entry(temp_db, other["id"], {"deliverable_images": stored["deliverable_images"]},
                              updated_at="2026-11-16 10:00:00", updated_by="test")
    temp_db.commit()
    original = harbor.get_artifact
    calls = []
    def artifact(*args):
        calls.append(args)
        return original(*args)
    monkeypatch.setattr(harbor, "get_artifact", artifact)
    result = qa_service.delivery_images_view(temp_db, delivery[1])
    assert len(result["entries"]) == 2 and len(calls) == 2
    assert result["entries"]["other"]["images"][0]["reference"] == result["entries"][delivery[0]]["images"][0]["reference"]


@pytest.mark.parametrize("failure", [ApiError(502, "Harbor unavailable"), ValueError("artifact missing"), None])
def test_failed_or_unmatched_tags_are_explicit_and_keep_the_original_digest(temp_db, delivery, monkeypatch, failure):
    stored = legacy_images(temp_db, delivery)
    def artifact(*_):
        if failure:
            raise failure
        return {"tags": [{"name": "unrelated-platform-tag"}]}
    monkeypatch.setattr(harbor, "get_artifact", artifact)
    shown = qa_service.delivery_images_view(temp_db, delivery[1])["entries"][delivery[0]]["images"]
    assert all(image.get("reference_error") for image in shown)
    assert [image["reference"] for image in shown] == [image["reference"] for image in stored["deliverable_images"]]


@pytest.mark.parametrize("role,status", [("QA", 200), ("RM", 200), ("Owner", 200), ("Guest", 200), ("SPD", 403), ("Admin", 403)])
def test_qa_delivery_display_api_is_read_only_and_role_scoped(temp_db, delivery, role, status):
    stored = legacy_images(temp_db, delivery)
    app = create_app()
    app.dependency_overrides[get_db] = lambda: temp_db
    app.dependency_overrides[require_login] = lambda: {"username": "reader", "role": role}
    with TestClient(app) as client:
        response = client.get("/api/qa/delivery-images", params={"release_id": delivery[1]})
        assert response.status_code == status
        if status == 200:
            assert response.headers["cache-control"] == "no-store"
            assert ":1.0-abcdef1-" in response.json()["entries"][delivery[0]]["images"][0]["reference"]
            assert client.get("/api/qa/delivery-images", params={"release_id": "missing"}).status_code == 404
    assert entries_repo.get_entry(temp_db, stored["id"]) == stored
