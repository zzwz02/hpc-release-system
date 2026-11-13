"""Digest queries prefer exact published images and preserve the selected publication's evidence."""
from copy import deepcopy
import json

import pytest
from fastapi.testclient import TestClient

from app.api.errors import ApiError
from app.deps import get_db, require_login
from app.domain import delivery_images
from app.integrations import gerrit, harbor
from app.main import create_app
from app.repositories import official_repo
from app.services import catalog_service, cycle_service, lookup_service, qa_service
from tests.conftest import close_qa, make_dependency_app_info, seed_app, seed_cycle, seed_signup, store_app_info


@pytest.fixture()
def publications(temp_db, monkeypatch):
    app = seed_app(temp_db, "recorded", app_info=None)
    records = []
    for index, name in enumerate(("3.9", "3.10"), 1):
        raw = make_dependency_app_info("recorded")
        raw["app_version"] = f"{index}.0"
        raw["app_test"]["sanity"]["test_cmd"] = f"printf '%s' 'original-{index}'"
        raw["app_test"]["extra"] = {**raw["app_test"]["sanity"], "test_cmd": f"extra-{index}"}
        store_app_info(temp_db, app, raw, commit_id=str(index) * 40)
        release = seed_cycle(temp_db, name=name)
        entry = seed_signup(temp_db, release, app, target_version=f"{index}.0", excluded_tests=["extra"] if index == 1 else [])
        images = [{**build, "reference": f"{harbor.HARBOR_HOST}/hpc-release/{build['image_pattern'].partition(':')[0]}@sha256:{digit * 64}"}
                  for build, digit in zip(entry["delivery_builds"], "abcdef")]
        qa_service.set_qa_status_batch(temp_db, release, [{"app_id": app, "status": "has_issues", "issue_note": f"QA-{index}",
            "delivery_revision": entry["delivery_revision"], "deliverable_images": images}], user="qa", role="QA")
        close_qa(temp_db, release)
        cycle_service.publish(temp_db, release, user="rm", role="RM")
        records.append(official_repo.current_for_release(temp_db, release)[app])
    image = records[0]["images"][0]["reference"]
    monkeypatch.setattr(harbor, "get_artifact", lambda *args: pytest.fail("published queries must not call Harbor"))
    monkeypatch.setattr(gerrit, "fetch_app_info_at", lambda *args, **kwargs: pytest.fail("published queries must not call Gerrit"))
    return app, image, records


def test_publication_lookup_returns_all_cycle_choices_and_exact_full_manifests(temp_db, publications, monkeypatch):
    app, image, records = publications
    dev = make_dependency_app_info("recorded")
    dev["app_test"]["sanity"]["test_cmd"] = "development-command"
    store_app_info(temp_db, app, dev, commit_id="development")
    temp_db.execute("UPDATE apps SET cicd_status='stopped' WHERE id=?", (app,))
    temp_db.commit()
    monkeypatch.setattr(delivery_images, "published_commands", lambda *args: pytest.fail("sealed commands must never be regenerated"))
    before = temp_db.total_changes
    result = lookup_service.by_image(temp_db, "docker pull " + image)
    assert result["source"] == "publications"
    assert [item["release"]["name"] for item in result["publications"]] == ["3.10", "3.9"]
    assert [item["app_version"] for item in result["publications"]] == ["2.0", "1.0"]
    for item, record in zip(result["publications"], reversed(records)):
        assert item["key"] == f'official:{record["id"]}'
        assert item["test_manifest"] == record["test_manifest"]
        assert item["manifest_source"] == "sealed"
        assert {command["arch"] for command in item["test_manifest"]["commands"]} == {"amd64", "arm64"}
        assert len(item["images"]) == len(record["images"])
        assert not any("development-command" in command["command"] for command in item["test_manifest"]["commands"])
    older = result["publications"][1]["test_manifest"]
    assert older["scope"]["excluded_tests"] == ["extra"]
    assert all(command["path"] != "extra" for command in older["commands"])
    assert older["qa"]["note"] == "QA-1"
    assert result["publications"][0]["test_manifest"]["qa"]["note"] == "QA-2"
    assert temp_db.total_changes == before


@pytest.mark.parametrize("legacy", ["entry_only", "unsealed", "missing_snapshot"])
def test_existing_publication_is_never_replaced_by_upstream_fallback(temp_db, publications, legacy):
    app, image, records = publications
    older = records[0]
    if legacy == "entry_only":
        temp_db.execute("DELETE FROM official_versions WHERE id=?", (older["id"],))
    else:
        temp_db.execute("UPDATE official_versions SET test_manifest_json=NULL WHERE id=?", (older["id"],))
        if legacy == "missing_snapshot":
            temp_db.execute("UPDATE official_versions SET app_info_version_id=NULL WHERE id=?", (older["id"],))
    temp_db.commit()
    result = lookup_service.by_image(temp_db, image)
    assert result["source"] == "publications" and len(result["publications"]) == 2
    item = result["publications"][1]
    if legacy == "missing_snapshot":
        assert item["manifest_source"] == "unavailable"
        assert item["test_manifest"] is None and "快照缺失" in item["note"]
    else:
        assert item["manifest_source"] == "reconstructed" and "未在发布时封存" in item["note"]
        assert item["test_manifest"]["scope"]["excluded_tests"] == ["extra"]
        if legacy == "entry_only":
            assert item["key"] == f'entry:{older["entry_id"]}'


def test_full_image_identity_prevents_cross_repository_digest_matches(temp_db, publications, monkeypatch):
    _, image, _ = publications
    query = image.replace("/hpc-release/", "/another-project/")
    def fallback(project, repository, digest):
        assert project == "another-project"
        raise ValueError("fallback reached")
    monkeypatch.setattr(harbor, "get_artifact", fallback)
    with pytest.raises(ValueError, match="fallback reached"):
        lookup_service.by_image(temp_db, query)


@pytest.mark.parametrize("embedded", [False, True])
def test_legacy_tags_bare_uppercase_digest_and_embedded_references_match(temp_db, publications, embedded):
    _, image, records = publications
    address, digest = image.split("@")
    old_image = address + ":old-tag@" + digest.upper().replace("SHA256:", "sha256:") if embedded else address + ":old-tag"
    old_digest = "" if embedded else digest.removeprefix("sha256:").upper()
    temp_db.execute("UPDATE official_versions SET test_manifest_json=NULL, images_json='[]', image=?, digest=? WHERE id=?",
                    (old_image, old_digest, records[0]["id"]))
    temp_db.commit()
    result = lookup_service.by_image(temp_db, image)
    assert [item["release"]["name"] for item in result["publications"]] == ["3.10", "3.9"]


def test_unlock_removes_records_and_only_no_record_queries_reach_harbor(temp_db, publications, monkeypatch):
    _, image, records = publications
    cycle_service.unlock(temp_db, records[1]["release_id"], reason="correction", user="rm", role="RM")
    assert len(lookup_service.by_image(temp_db, image)["publications"]) == 1
    cycle_service.unlock(temp_db, records[0]["release_id"], reason="correction", user="rm", role="RM")
    monkeypatch.setattr(harbor, "get_artifact", lambda *args: (_ for _ in ()).throw(ValueError("fallback reached")))
    with pytest.raises(ValueError, match="fallback reached"):
        lookup_service.by_image(temp_db, image)


def test_invalid_sealed_publication_does_not_fall_back_to_external_generation(temp_db, publications):
    _, image, records = publications
    corrupted = deepcopy(records[1]["test_manifest"])
    corrupted["schema_version"] = 999
    temp_db.execute("UPDATE official_versions SET test_manifest_json=? WHERE id=?", (json.dumps(corrupted), records[1]["id"]))
    temp_db.commit()
    with pytest.raises(ApiError) as error:
        lookup_service.by_image(temp_db, image)
    assert error.value.status_code == 409


@pytest.mark.parametrize("role,status", [("RM", 200), ("Owner", 200), ("QA", 200), ("Guest", 403), ("SPD", 403), ("Admin", 403)])
def test_http_publication_and_local_source_contract_and_permissions(temp_db, publications, role, status):
    app_id, image, records = publications
    app = create_app()
    app.dependency_overrides[get_db] = lambda: temp_db
    app.dependency_overrides[require_login] = lambda: {"username": "reader", "role": role}
    older = records[0]
    params = {"image": image, "commit_id": older["test_manifest"]["app_info"]["commit_id"],
              "release_id": older["release_id"], "app_id": app_id, "publication_key": f'official:{older["id"]}'}
    with TestClient(app) as client:
        result = client.get("/api/lookup/image", params={"image": image})
        source = client.get("/api/lookup/app-info", params=params)
        assert result.status_code == source.status_code == status
        if status == 200:
            assert result.json()["source"] == "publications"
            assert result.json()["publications"][1]["test_manifest"] == older["test_manifest"]
            assert source.json() == older["test_manifest"]["app_info"]["raw"]
            assert source.headers["cache-control"] == "no-store"
            assert client.get("/api/lookup/app-info", params={**params, "commit_id": "wrong"}).status_code == 409
            assert client.get("/api/lookup/app-info", params={**params, "publication_key": "wrong"}).status_code == 409
            assert client.get("/api/lookup/app-info", params={**params, "app_id": ""}).status_code == 400
            cycle_service.unlock(temp_db, older["release_id"], reason="correction", user="rm", role="RM")
            assert client.get("/api/lookup/app-info", params=params).status_code == 409
