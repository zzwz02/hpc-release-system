"""QA validates real Harbor artifacts before writing any part of the batch."""
from copy import deepcopy

import pytest
from fastapi.testclient import TestClient

from app.api.errors import ApiError
from app.db.connection import connect
from app.deps import get_db, require_login
from app.domain import delivery_images
from app.integrations import harbor
from app.main import create_app
from app.repositories import entries_repo
from app.services import qa_service
from tests.conftest import make_app_info, seed_app, seed_cycle, seed_signup
from tests.test_delivery_images import delivery, save  # noqa: F401


def artifact(image, *, tags=None):
    return {"tags": [{"name": tag} for tag in (tags if tags is not None else [
                image["image_pattern"].partition(":")[2].replace("<hpc_version>", "abcdef1")
            ])]}


@pytest.mark.parametrize("case,message", [
    ("wrong_arch", "实际 tags 与构建配置不匹配"),
    ("wrong_os", "实际 tags 与构建配置不匹配"),
    ("wrong_python", "实际 tags 与构建配置不匹配"),
    ("wrong_version", "实际 tags 与构建配置不匹配"),
    ("no_tags", "没有可用 tag"), ("invalid_tags", "没有可用 tag"),
    ("missing_tags", "没有可用 tag"),
])
def test_bad_artifact_rejects_qa_and_preserves_images_and_audit(temp_db, delivery, monkeypatch, case, message):
    image = delivery[3][0]
    result = artifact(image)
    result["extra_attrs"] = {"architecture": "amd64", "os": "ubuntu22.04"}
    if case == "wrong_arch":
        result["tags"][0]["name"] = result["tags"][0]["name"].replace("amd64", "arm64")
    elif case == "wrong_os":
        result["tags"][0]["name"] = result["tags"][0]["name"].replace("ubuntu22.04", "ubuntu24.04")
    elif case == "wrong_python":
        result["tags"][0]["name"] = result["tags"][0]["name"].replace("py312", "py310")
    elif case == "wrong_version":
        result["tags"][0]["name"] = result["tags"][0]["name"].replace("1.0-", "2.0-")
    elif case == "no_tags":
        result["tags"] = []
    elif case == "invalid_tags":
        result["tags"] = [None, {}, {"name": 3}]
    else:
        result.pop("tags")
    monkeypatch.setattr(harbor, "get_artifact", lambda *args: result)
    before = entries_repo.get_entry(temp_db, delivery[2]["id"])
    audit = temp_db.execute("SELECT COUNT(*) FROM audit").fetchone()[0]
    values = deepcopy(delivery[3])
    # Even an explicitly entered correct tag cannot override Harbor's data.
    name, _, digest = values[0]["reference"].partition("@")
    values[0]["reference"] = name + ":" + image["image_pattern"].partition(":")[2].replace("<hpc_version>", "abcdef1") + "@" + digest
    with pytest.raises(ValueError, match=message) as error:
        save(temp_db, delivery, values=values)
    assert delivery[0] in str(error.value) and "ubuntu22.04_amd64" in str(error.value)
    assert entries_repo.get_entry(temp_db, delivery[2]["id"]) == before
    assert temp_db.execute("SELECT COUNT(*) FROM audit").fetchone()[0] == audit


@pytest.mark.parametrize("metadata", [None, {}, {"architecture": " "}, {"architecture": "arm64", "os": "windows"}, []])
@pytest.mark.parametrize("version", ["abcdef1", "3.10.0"])
def test_matching_tags_determine_platform_even_if_metadata_is_missing_or_wrong(temp_db, delivery, monkeypatch, metadata, version):
    image = delivery[3][0]
    tag = image["image_pattern"].partition(":")[2].replace("<hpc_version>", version) + "-42"
    result = artifact(image, tags=["latest", tag])
    if metadata is not None:
        result["extra_attrs"] = metadata
    monkeypatch.setattr(harbor, "get_artifact", lambda *args: result)
    values = [{**image, "reference": image["reference"].replace("/testapp-maca", "/nested/testapp-maca")}]
    save(temp_db, delivery, values=values)
    saved = entries_repo.get_entry(temp_db, delivery[2]["id"])
    assert saved["qa_status"] == "qa_passed"
    assert saved["deliverable_images"][0]["os"] == "ubuntu22.04"
    assert saved["deliverable_images"][0]["arch"] == "amd64"
    assert "/nested/testapp-maca@" in saved["deliverable_images"][0]["reference"]


def test_same_digest_in_wrong_build_is_checked_even_when_artifact_query_is_shared(temp_db, delivery, monkeypatch):
    values = deepcopy(delivery[3])
    values[1]["reference"] = values[0]["reference"]
    calls = []

    def get_artifact(*args):
        calls.append(args)
        return artifact(values[0])

    monkeypatch.setattr(harbor, "get_artifact", get_artifact)
    with pytest.raises(ValueError, match="实际 tags 与构建配置不匹配"):
        save(temp_db, delivery, values=values)
    assert len(calls) == 1
    assert entries_repo.get_entry(temp_db, delivery[2]["id"])["qa_status"] == "not_checked"


def test_failure_in_second_app_does_not_save_first_app(temp_db, delivery, monkeypatch):
    app, release, entry, values = delivery
    second = seed_app(temp_db, "second")
    second_entry = seed_signup(temp_db, release, second)
    second_image = {**second_entry["delivery_builds"][0], "reference": values[0]["reference"]}
    response = artifact(values[0])
    monkeypatch.setattr(harbor, "get_artifact", lambda *args: response)
    with pytest.raises(ValueError, match="构建配置不匹配"):
        qa_service.set_qa_status_batch(temp_db, release, [
            {"app_id": app, "status": "qa_passed", "deliverable_images": [values[0]], "delivery_revision": entry["delivery_revision"]},
            {"app_id": second, "status": "qa_passed", "deliverable_images": [second_image], "delivery_revision": second_entry["delivery_revision"]},
        ], user="qa", role="QA")
    assert entries_repo.get_entry(temp_db, entry["id"])["qa_status"] == "not_checked"
    assert entries_repo.get_entry(temp_db, second_entry["id"])["qa_status"] == "not_checked"


@pytest.mark.parametrize("change", ["qa", "version", "publish", "withdraw"])
def test_harbor_wait_does_not_lock_db_and_concurrent_changes_are_not_overwritten(temp_db, delivery, monkeypatch, change):
    app, release, entry, values = delivery
    path = temp_db.execute("PRAGMA database_list").fetchone()["file"]

    def get_artifact(*args):
        assert not temp_db.in_transaction
        other = connect(path)
        try:
            if change == "qa":
                other.execute("UPDATE entries SET qa_status='has_issues',qa_note='concurrent QA' WHERE id=?", (entry["id"],))
            elif change == "version":
                other.execute("UPDATE entries SET locked_version_id=NULL WHERE id=?", (entry["id"],))
            elif change == "publish":
                other.execute("UPDATE releases SET released_locked=1 WHERE id=?", (release,))
            else:
                other.execute("UPDATE entries SET status='withdrawn' WHERE id=?", (entry["id"],))
            other.commit()
        finally:
            other.close()
        return artifact(values[0])

    monkeypatch.setattr(harbor, "get_artifact", get_artifact)
    with pytest.raises((ValueError, RuntimeError), match="已变化|已发布|未报名"):
        save(temp_db, delivery, values=[values[0]])
    saved = entries_repo.get_entry(temp_db, entry["id"])
    assert saved["deliverable_images"] == []
    if change == "qa":
        assert saved["qa_status"] == "has_issues" and saved["qa_note"] == "concurrent QA"


@pytest.mark.parametrize("failure,expected", [("missing", 400), ("network", 502), ("denied", 502)])
def test_http_errors_are_visible_and_save_nothing(temp_db, delivery, monkeypatch, failure, expected):
    def get_artifact(*args):
        if failure == "missing":
            raise ValueError("Harbor 中未找到该镜像，请检查镜像名和 digest")
        raise ApiError(502, "Harbor 查询失败" if failure == "network" else "Harbor 拒绝读取该镜像")

    monkeypatch.setattr(harbor, "get_artifact", get_artifact)
    app = create_app()
    app.dependency_overrides[get_db] = lambda: temp_db
    app.dependency_overrides[require_login] = lambda: {"username": "qa", "role": "QA"}
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post("/api/qa/status-batch", json={"release_id": delivery[1], "items": [{
            "app_id": delivery[0], "status": "qa_passed", "delivery_revision": delivery[2]["delivery_revision"],
            "deliverable_images": delivery[3],
        }]})
    assert response.status_code == expected
    assert "Harbor" in response.json()["error"] and "ubuntu22.04_amd64" in response.json()["error"]
    assert entries_repo.get_entry(temp_db, delivery[2]["id"])["qa_status"] == "not_checked"


def test_legacy_scalar_write_cannot_bypass_platform_validation(temp_db, delivery):
    with pytest.raises(ValueError, match="按构建配置"):
        qa_service.set_qa_status_batch(temp_db, delivery[1], [{"app_id": delivery[0], "status": "qa_passed",
            "deliverable_image": "harbor.sh.mxcr.io/hpc-release/testapp-maca", "deliverable_digest": "sha256:" + "a" * 64,
        }], user="qa", role="QA")
    assert entries_repo.get_entry(temp_db, delivery[2]["id"])["deliverable_digest"] == ""


def test_every_save_rechecks_harbor_and_query_failure_preserves_previously_saved_data(temp_db, delivery, monkeypatch):
    save(temp_db, delivery)
    before = entries_repo.get_entry(temp_db, delivery[2]["id"])

    def unavailable(*args):
        raise ApiError(502, "Harbor 查询失败，请稍后重试")

    monkeypatch.setattr(harbor, "get_artifact", unavailable)
    with pytest.raises(ApiError, match="Harbor 查询失败"):
        save(temp_db, delivery, values=before["deliverable_images"], revision=delivery_images.revision(before))
    assert entries_repo.get_entry(temp_db, delivery[2]["id"]) == before


@pytest.mark.parametrize("field", ["os", "arch"])
def test_unknown_build_platform_cannot_be_saved_even_if_image_exists(temp_db, monkeypatch, field):
    raw = make_app_info()
    cfg = raw["app_build"].pop("ubuntu20.04_amd64")
    cfg.pop(field)
    raw["app_build"]["custom"] = cfg
    app = seed_app(temp_db, app_info=raw)
    release = seed_cycle(temp_db)
    entry = seed_signup(temp_db, release, app)
    image = {**entry["delivery_builds"][0], "reference": "harbor.sh.mxcr.io/hpc-release/testapp-maca@sha256:" + "a" * 64}
    monkeypatch.setattr(harbor, "get_artifact", lambda *args: artifact(image))
    with pytest.raises(ValueError, match="无法确定 OS/CPU 架构"):
        save(temp_db, (app, release, entry, [image]))
    assert entries_repo.get_entry(temp_db, entry["id"])["qa_status"] == "not_checked"


@pytest.fixture()
def runtime_delivery(temp_db):
    raw = make_app_info()
    raw["app_build"]["ubuntu20.04_amd64"].update(
        python_label="py310,py312", pytorch_label="torch2.4,torch2.8",
    )
    app = seed_app(temp_db, app_info=raw)
    release = seed_cycle(temp_db)
    entry = seed_signup(temp_db, release, app)
    build = next(build for build in entry["delivery_builds"]
                 if build["python_label"] == "py312" and build["pytorch_label"] == "torch2.8")
    image = {**build, "reference": "harbor.sh.mxcr.io/hpc-release/testapp-maca@sha256:" + "a" * 64}
    return app, release, entry, [image]


@pytest.mark.parametrize("tag,valid", [
    ("1.0-abcdef1-torch2.8-py312-ubuntu20.04-amd64", True),
    ("1.0-abcdef1-torch2.8-py310-ubuntu20.04-amd64", False),
    ("1.0-abcdef1-torch2.4-py312-ubuntu20.04-amd64", False),
    ("1.0-abcdef1-torch2.8-ubuntu20.04-amd64", False),
    ("1.0-abcdef1-py312-ubuntu20.04-amd64", False),
])
def test_runtime_versions_must_match_the_selected_build(temp_db, runtime_delivery, monkeypatch, tag, valid):
    # Incorrect runtime versions may belong to another enabled build, but must
    # never be accepted for the py312/torch2.8 row selected by QA.
    monkeypatch.setattr(harbor, "get_artifact", lambda *args: {"tags": [{"name": tag}]})
    before = entries_repo.get_entry(temp_db, runtime_delivery[2]["id"])
    if valid:
        save(temp_db, runtime_delivery)
        saved = entries_repo.get_entry(temp_db, before["id"])
        assert saved["qa_status"] == "qa_passed"
        image = saved["deliverable_images"][0]
        assert image["python_label"] == "py312" and image["pytorch_label"] == "torch2.8"
    else:
        with pytest.raises(ValueError, match="Python py312，PyTorch torch2.8"):
            save(temp_db, runtime_delivery)
        assert entries_repo.get_entry(temp_db, before["id"]) == before


def test_python_and_pytorch_must_match_together_in_one_real_tag(temp_db, runtime_delivery, monkeypatch):
    monkeypatch.setattr(harbor, "get_artifact", lambda *args: {"tags": [
        {"name": "1.0-abcdef1-torch2.8-py310-ubuntu20.04-amd64"},
        {"name": "1.0-abcdef1-torch2.4-py312-ubuntu20.04-amd64"},
    ]})
    with pytest.raises(ValueError, match="Python py312，PyTorch torch2.8"):
        save(temp_db, runtime_delivery)
    assert entries_repo.get_entry(temp_db, runtime_delivery[2]["id"])["qa_status"] == "not_checked"


@pytest.mark.parametrize("target", ["release", "dbg"])
def test_opposite_image_type_cannot_be_assigned_to_build(temp_db, delivery, monkeypatch, target):
    builds = {build["image_target"]: build for build in delivery[2]["delivery_builds"] if build["arch"] == "amd64"}
    other = "dbg" if target == "release" else "release"
    image = {**builds[target], "image_target": other,
             "reference": f'harbor.sh.mxcr.io/hpc-release/{builds[other]["image_pattern"].partition(":")[0]}@sha256:{"f" * 64}'}
    monkeypatch.setattr(harbor, "get_artifact", lambda *args: pytest.fail("wrong image type must fail before Harbor query"))
    before = entries_repo.get_entry(temp_db, delivery[2]["id"])
    with pytest.raises(ValueError, match=f"{target} 镜像名称应为"):
        save(temp_db, delivery, values=[image])
    assert entries_repo.get_entry(temp_db, before["id"]) == before


@pytest.mark.parametrize("target", ["release", "dbg"])
def test_matching_image_type_and_tag_save_to_selected_build(temp_db, delivery, monkeypatch, target):
    build = next(build for build in delivery[2]["delivery_builds"]
                 if build["arch"] == "amd64" and build["image_target"] == target)
    repository = "testapp-maca-dbg" if target == "dbg" else "testapp-maca"
    image = {**build, "reference": f'harbor.sh.mxcr.io/hpc-release/{repository}@sha256:{"f" * 64}'}

    def get_artifact(project, actual_repository, digest):
        assert (project, actual_repository, digest) == ("hpc-release", repository, "sha256:" + "f" * 64)
        return {"tags": [{"name": "1.0-abcdef1-py312-ubuntu22.04-amd64"}]}

    monkeypatch.setattr(harbor, "get_artifact", get_artifact)
    save(temp_db, delivery, values=[image])
    saved = entries_repo.get_entry(temp_db, delivery[2]["id"])
    assert saved["qa_status"] == "qa_passed"
    assert saved["deliverable_images"][0]["image_target"] == target
    assert saved["deliverable_images"][0]["reference"] == image["reference"]


@pytest.mark.parametrize("target", ["release", "dbg"])
def test_digest_existing_only_in_other_image_type_is_rejected(temp_db, delivery, harbor_artifacts, target):
    builds = {build["image_target"]: build for build in delivery[2]["delivery_builds"] if build["arch"] == "amd64"}
    other = "dbg" if target == "release" else "release"
    harbor_artifacts([{**builds[other], "reference":
        f'harbor.sh.mxcr.io/hpc-release/{builds[other]["image_pattern"].partition(":")[0]}@sha256:{"f" * 64}'}])
    image = {**builds[target], "reference":
        f'harbor.sh.mxcr.io/hpc-release/{builds[target]["image_pattern"].partition(":")[0]}@sha256:{"f" * 64}'}
    before = entries_repo.get_entry(temp_db, delivery[2]["id"])
    with pytest.raises(ValueError, match="Harbor 中未找到该镜像"):
        save(temp_db, delivery, values=[image])
    assert entries_repo.get_entry(temp_db, before["id"]) == before
