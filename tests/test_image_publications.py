"""Digest commands are always live; explicitly requested publication evidence stays frozen."""
from copy import deepcopy
import json

import pytest
from fastapi.testclient import TestClient

from app.deps import get_db, require_login
from app.domain import delivery_images
from app.integrations import gerrit, harbor
from app.main import create_app
from app.repositories import official_repo
from app.services import cycle_service, lookup_service, qa_service
from tests.conftest import close_qa, make_dependency_app_info, seed_app, seed_cycle, seed_signup, store_app_info


@pytest.fixture()
def publications(temp_db, monkeypatch, harbor_artifacts):
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
        images = [{**build, "reference": f"{harbor.HARBOR_HOST}/hpc-release/{build['image_pattern'].replace('<hpc_version>', 'abcdef1')}@sha256:{digit * 64}"}
                  for build, digit in zip(entry["delivery_builds"], "abcdef")]
        harbor_artifacts(images)
        qa_service.set_qa_status_batch(temp_db, release, [{"app_id": app, "status": "has_issues", "issue_note": f"QA-{index}",
            "delivery_revision": entry["delivery_revision"], "deliverable_images": images}], user="qa", role="QA")
        close_qa(temp_db, release)
        cycle_service.publish(temp_db, release, user="rm", role="RM")
        records.append(official_repo.current_for_release(temp_db, release)[app])
    selected_image = next(item for item in records[0]["images"]
                          if item["os"] == "ubuntu20.04" and item["arch"] == "amd64"
                          and item["image_target"] == "release")
    image = selected_image["reference"]
    # Even a published digest is resolved live. Its actual tag points to the
    # older commit; a later publication must not replace that identity.
    raw = deepcopy(records[0]["test_manifest"]["app_info"]["raw"])
    commit = records[0]["test_manifest"]["app_info"]["commit_id"]
    pattern = next(build["image_pattern"] for build in delivery_images.builds(raw)
                   if build["build_key"] == selected_image["build_key"]).partition(":")[2]
    calls = []
    def artifact(project, repository, digest):
        calls.append(("harbor", project, repository, digest))
        return {"digest": digest, "tags": [{"name": pattern.replace("<hpc_version>", commit)},
                                          {"name": pattern.replace("<hpc_version>", "3.9")}]}
    def fetch(url, requested_commit, **kwargs):
        assert requested_commit == commit
        calls.append(("gerrit", url, requested_commit))
        return json.dumps(raw)
    monkeypatch.setattr(harbor, "get_artifact", artifact)
    monkeypatch.setattr(gerrit, "fetch_app_info_at", fetch)
    return app, image, records, raw, calls


def test_published_digest_always_uses_live_tags_and_pinned_app_info(temp_db, publications, monkeypatch):
    app, image, records, raw, calls = publications
    dev = make_dependency_app_info("recorded")
    dev["app_test"]["sanity"]["test_cmd"] = "development-command"
    store_app_info(temp_db, app, dev, commit_id="development")
    temp_db.execute("UPDATE apps SET cicd_status='stopped' WHERE id=?", (app,))
    temp_db.commit()
    monkeypatch.setattr(official_repo, "by_digest", lambda *args: pytest.fail("digest commands must not read publication records"))
    before = temp_db.total_changes
    result = lookup_service.by_image(temp_db, "docker pull " + image)
    assert result["source"] == "historical_config"
    assert result["app_version"] == "1.0" and result["hpc_version"] == "3.9"
    assert result["commit_id"] == records[0]["test_manifest"]["app_info"]["commit_id"]
    assert {row["test"] for row in result["test_commands"]} == {"sanity", "extra"}
    assert all("development-command" not in row["command"] for row in result["test_commands"])
    assert {call[0] for call in calls} == {"harbor", "gerrit"}
    assert records[0]["test_manifest"]["scope"]["excluded_tests"] == ["extra"]
    assert temp_db.total_changes == before


@pytest.mark.parametrize("legacy", ["entry_only", "unsealed", "missing_snapshot", "corrupt_snapshot"])
def test_publication_snapshot_state_cannot_change_live_digest_commands(temp_db, publications, legacy):
    _, image, records, _, _ = publications
    older = records[0]
    if legacy == "entry_only":
        temp_db.execute("DELETE FROM official_versions WHERE id=?", (older["id"],))
    elif legacy == "corrupt_snapshot":
        corrupted = {**older["test_manifest"], "schema_version": 999}
        temp_db.execute("UPDATE official_versions SET test_manifest_json=? WHERE id=?", (json.dumps(corrupted), older["id"]))
    else:
        temp_db.execute("UPDATE official_versions SET test_manifest_json=NULL WHERE id=?", (older["id"],))
        if legacy == "missing_snapshot":
            temp_db.execute("UPDATE official_versions SET app_info_version_id=NULL WHERE id=?", (older["id"],))
    temp_db.commit()
    result = lookup_service.by_image(temp_db, image)
    assert result["source"] == "historical_config"
    assert result["app_version"] == "1.0"
    assert any(row["test"] == "extra" for row in result["test_commands"])


def test_full_repository_and_digest_are_sent_to_harbor_even_with_local_records(temp_db, publications, monkeypatch):
    _, image, _, _, _ = publications
    def failed(project, repository, digest):
        assert project == "another-project"
        raise ValueError("Harbor 中未找到该镜像")
    monkeypatch.setattr(harbor, "get_artifact", failed)
    with pytest.raises(ValueError, match="未找到"):
        lookup_service.by_image(temp_db, image.replace("/hpc-release/", "/another-project/"))


@pytest.mark.parametrize("embedded", [False, True])
def test_legacy_saved_image_strings_do_not_supply_live_commands(temp_db, publications, embedded):
    _, image, records, _, calls = publications
    address, digest = image.split("@")
    address = address.rsplit(':', 1)[0]
    old_image = address + ":old-tag@" + digest.upper().replace("SHA256:", "sha256:") if embedded else address + ":old-tag"
    old_digest = "" if embedded else digest.removeprefix("sha256:").upper()
    temp_db.execute("UPDATE official_versions SET test_manifest_json=NULL, images_json='[]', image=?, digest=? WHERE id=?",
                    (old_image, old_digest, records[0]["id"]))
    temp_db.commit()
    result = lookup_service.by_image(temp_db, image)
    assert result["source"] == "historical_config" and result["image"] == address + '@' + digest
    assert len(calls) == 2


def test_unlock_does_not_change_live_digest_generation(temp_db, publications):
    _, image, records, _, _ = publications
    before = lookup_service.by_image(temp_db, image)
    for record in records:
        cycle_service.unlock(temp_db, record["release_id"], reason="correction", user="rm", role="RM")
    assert lookup_service.by_image(temp_db, image) == before


@pytest.mark.parametrize("role,status", [("RM", 200), ("Owner", 200), ("QA", 200), ("Guest", 403), ("SPD", 403), ("Admin", 403)])
def test_live_digest_and_explicit_archived_source_contract_and_permissions(temp_db, publications, role, status):
    app_id, image, records, _, calls = publications
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
            assert result.json()["source"] == "historical_config"
            assert result.json()["app_version"] == "1.0"
            assert source.json() == older["test_manifest"]["app_info"]["raw"]
            assert source.headers["cache-control"] == "no-store"
            assert len(calls) == 2
            assert client.get("/api/lookup/app-info", params={**params, "commit_id": "wrong"}).status_code == 409
            assert client.get("/api/lookup/app-info", params={**params, "publication_key": "wrong"}).status_code == 409
            assert client.get("/api/lookup/app-info", params={**params, "app_id": ""}).status_code == 400
            cycle_service.unlock(temp_db, older["release_id"], reason="correction", user="rm", role="RM")
            assert client.get("/api/lookup/app-info", params=params).status_code == 409
        else:
            assert not calls
