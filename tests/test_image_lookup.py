"""Harbor digest -> commit tag -> historical Gerrit app_info -> pinned commands."""
from copy import deepcopy
import json
import shlex
import ssl

import httpx
import pytest
from fastapi.testclient import TestClient

from app.api.errors import ApiError
from app.deps import get_db, require_login
from app.domain import image_lookup
from app.integrations import gerrit, harbor
from app.main import create_app
from app.repositories import app_info_repo
from app.services import catalog_service, lookup_service, qa_service
from tests.conftest import make_app_info, seed_app, seed_cycle, seed_signup

DIGEST = "sha256:" + "8d" * 32
IMAGE = f"{harbor.HARBOR_HOST}/hpc-release/abacus-maca@{DIGEST}"
TAGS = ["3.9.0.27-bb46c60-ubuntu22.04-amd64-3012",
        "3.9.0.27-3.10.1.2-ubuntu22.04-amd64-3012",
        "3.9.0.27-3.10.1.2-ubuntu22.04-amd64"]


def historical_info():
    raw = make_app_info(app_name="abacus", app_version="3.9.0.27")
    raw["app_build"] = {
        "ubuntu22.04_amd64": {"arch": "amd64", "os": "ubuntu22.04", "build_target": "release, dbg", "supported_chip": ["C500"]},
        "ubuntu24.04_amd64": {"arch": "amd64", "os": "ubuntu24.04", "build_target": "release"},
        "ubuntu22.04_arm64": {"arch": "arm64", "os": "ubuntu22.04", "build_target": "release"},
    }
    cfg = {"img_target": "release", "test_cmd": "printf '%s' \"old command\"",
           "container_args": "--device=/dev/dri -u 0", "mount_dataset": True,
           "supported_chip": {"C500": ["ubuntu22.04_amd64"], "X201": ["ubuntu22.04_arm64"]}}
    raw["app_test"] = {
        "sanity": cfg,
        "nested": {"daily": {**deepcopy(cfg), "test_cmd": "run-nested"}},
        "arm-only": {**deepcopy(cfg), "supported_chip": {"X201": ["ubuntu22.04_arm64"]}},
        "other-os": {**deepcopy(cfg), "supported_chip": {"C500": ["ubuntu24.04_amd64"]}},
        "debug": {**deepcopy(cfg), "img_target": "dbg"},
        "disabled": {**deepcopy(cfg), "enabled": False},
        "disabled-string": {**deepcopy(cfg), "enabled": "false"},
        "weekly": {**deepcopy(cfg), "test_period": "weekly"},
        "ignored": {**deepcopy(cfg), "ignore_release": True},
    }
    return raw


@pytest.fixture()
def lookup(temp_db, monkeypatch):
    raw = historical_info()
    app_id = seed_app(temp_db, git_url="hpc_actual_repo", app_info=raw)
    calls = []
    monkeypatch.setattr(harbor, "get_artifact", lambda *args: {
        "digest": DIGEST, "tags": [{"name": tag} for tag in TAGS], "extra_attrs": {"architecture": "amd64"},
    })

    def fetch(url, commit, **kwargs):
        assert not temp_db.in_transaction
        calls.append((url, commit))
        return json.dumps(raw)

    monkeypatch.setattr(gerrit, "fetch_app_info_at", fetch)
    return raw, app_id, calls


def test_reads_the_commit_not_branch_head_and_uses_immutable_digest(temp_db, lookup):
    raw, app_id, calls = lookup
    before = (len(app_info_repo.list_versions(temp_db, app_id)), temp_db.execute("SELECT COUNT(*) FROM audit").fetchone()[0])
    result = lookup_service.by_image(temp_db, f"docker pull {IMAGE}")
    assert result["commit_id"] == "bb46c60"
    assert result["tags"] == sorted(TAGS)
    assert result["app_version"] == "3.9.0.27"
    assert calls == [(gerrit.gerrit_remote_url("hpc_actual_repo"), "bb46c60")]
    assert [row["test"] for row in result["test_commands"]] == ["sanity", "nested.daily"]
    for row in result["test_commands"]:
        assert row["arch"] == "amd64"
        assert row["build"] == "ubuntu22.04_amd64"
        assert row["chips"] == ["C500"]
        args = shlex.split(row["command"])
        assert args.count(IMAGE) == 1
        assert "/pde_hpc/dataset:/hpc_dataset:ro" in args
        assert "--device=/dev/dri" in args
        assert "<hpc_version>" not in row["command"]
    assert shlex.split(result["test_commands"][0]["command"])[-1] == raw["app_test"]["sanity"]["test_cmd"]
    assert before == (len(app_info_repo.list_versions(temp_db, app_id)), temp_db.execute("SELECT COUNT(*) FROM audit").fetchone()[0])


def test_historical_names_and_shared_repo_branches_do_not_fetch_twice(temp_db, lookup):
    raw, app_id, calls = lookup
    seed_app(temp_db, "other-branch", git_url="hpc_actual_repo", git_branch="old-release", app_info=raw)
    renamed = make_app_info(app_name="renamed", app_version="4.0")
    catalog_service.store_app_info(temp_db, app_id, renamed, commit_id="new-head", source="new", user="rm")
    temp_db.commit()
    assert lookup_service.by_image(temp_db, IMAGE)["app_name"] == "abacus"
    assert len(calls) == 1


@pytest.mark.parametrize("tags,message", [
    ([], "没有"), ([TAGS[1]], "没有"),
    ([TAGS[0], TAGS[0].replace("bb46c60", "abcdef1")], "多个"),
])
def test_missing_or_conflicting_commits_are_explicit(temp_db, lookup, monkeypatch, tags, message):
    monkeypatch.setattr(harbor, "get_artifact", lambda *args: {"tags": [{"name": tag} for tag in tags]})
    with pytest.raises(ValueError, match=message):
        lookup_service.by_image(temp_db, IMAGE)
    assert not lookup[2]


def test_commit_prefixes_resolving_to_the_same_commit_are_compatible():
    assert image_lookup.commit_tags([TAGS[0], TAGS[0].replace("bb46c60", "bb46c607321e")])[0] == "bb46c607321e"


def test_multi_value_build_fields_select_only_the_artifact_platform():
    raw = historical_info()
    raw["app_build"] = {"ubuntu22.04_amd64": {
        "arch": "amd64/arm64", "os": "ubuntu22.04/ubuntu24.04", "build_target": "release",
    }}
    result = image_lookup.historical_image_commands(
        raw, image=IMAGE, repository="abacus-maca", tags=[TAGS[0]], architecture="amd64",
    )
    assert {row["arch"] for row in result["test_commands"]} == {"amd64"}
    assert [row["test"] for row in result["test_commands"]] == ["sanity", "nested.daily"]


@pytest.mark.parametrize("reference", [
    "sha256:" + "8d" * 32, "harbor.sh.mxcr.io/hpc-release/abacus-maca", IMAGE.replace("hpc-release", ".."),
    IMAGE.replace("abacus-maca", "a/../abacus-maca"), IMAGE.replace(harbor.HARBOR_HOST, "another.example"),
])
def test_invalid_reference_fails_before_network(temp_db, monkeypatch, reference):
    monkeypatch.setattr(harbor, "get_artifact", lambda *args: pytest.fail("unexpected network call"))
    with pytest.raises(ValueError):
        lookup_service.by_image(temp_db, reference)


def test_ambiguous_repo_and_platform_mismatch_do_not_generate_guessed_commands(temp_db, lookup):
    raw, _, _ = lookup
    raw["app_build"]["ubuntu22.04_amd64"]["os"] = "ubuntu24.04"
    with pytest.raises(ValueError, match="不匹配"):
        lookup_service.by_image(temp_db, IMAGE)
    seed_app(temp_db, "impostor", git_url="other_repo", app_info=historical_info())
    with pytest.raises(ValueError, match="多个 Gerrit"):
        lookup_service.by_image(temp_db, IMAGE)


def test_image_lookup_without_signup_and_qa_report_share_command_composition(temp_db, lookup):
    raw, app_id, _ = lookup
    raw["app_test"] = {"sanity": raw["app_test"]["sanity"]}
    release_id = seed_cycle(temp_db)
    seed_signup(temp_db, release_id, app_id)
    report = qa_service.get_qa_reports(temp_db, release_id)
    command_column = report["test_cmd"]["columns"].index("docker_cmd")
    assert "sh -c" in report["test_cmd"]["rows"][0][command_column]
    args = shlex.split(report["test_cmd"]["rows"][0][command_column])
    assert args[-1] == raw["app_test"]["sanity"]["test_cmd"]


@pytest.fixture()
def api(temp_db):
    app = create_app()
    user = {"username": "reader", "role": "Owner", "display_name": "Reader"}
    app.dependency_overrides[get_db] = lambda: temp_db
    app.dependency_overrides[require_login] = lambda: user
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client, user


@pytest.mark.parametrize("role,status", [("Owner", 200), ("QA", 200), ("RM", 200), ("Guest", 403), ("SPD", 403), ("Admin", 403)])
def test_http_contract_and_permissions(temp_db, lookup, api, role, status):
    client, user = api
    user["role"] = role
    response = client.get("/api/lookup/image", params={"image": IMAGE})
    assert response.status_code == status, response.text
    if status == 200:
        assert response.json()["test_commands"][0]["command"].count(IMAGE) == 1
    else:
        assert not lookup[2]
    source = client.get("/api/lookup/app-info", params={"image": IMAGE, "commit_id": "bb46c60"})
    assert source.status_code == status, source.text
    if status == 200:
        assert source.json() == lookup[0]
        assert source.headers["content-disposition"] == 'inline; filename="app_info.json"'
        assert source.headers["cache-control"] == "no-store"
    else:
        assert not lookup[2]


def test_source_file_uses_the_pinned_commit_without_resolving_harbor_tags(lookup, api, monkeypatch):
    client, _ = api
    monkeypatch.setattr(harbor, "get_artifact", lambda *args: pytest.fail("source must not resolve mutable tags"))
    response = client.get("/api/lookup/app-info", params={"image": IMAGE, "commit_id": "bb46c60"})
    assert response.status_code == 200
    assert response.json() == lookup[0]
    assert lookup[2][-1][1] == "bb46c60"
    before = len(lookup[2])
    for image, commit in [(IMAGE, "maca"), (IMAGE.replace("abacus-maca", "unknown-maca"), "bb46c60")]:
        assert client.get("/api/lookup/app-info", params={"image": image, "commit_id": commit}).status_code == 400
    assert len(lookup[2]) == before


def test_gerrit_failure_and_invalid_json_are_readable_upstream_errors(lookup, api, monkeypatch):
    client, _ = api
    def failed(*args, **kwargs):
        raise RuntimeError("private upstream details")
    monkeypatch.setattr(gerrit, "fetch_app_info_at", failed)
    response = client.get("/api/lookup/image", params={"image": IMAGE})
    assert response.status_code == 502
    assert "Gerrit" in response.json()["error"]
    assert "private" not in response.text
    monkeypatch.setattr(gerrit, "fetch_app_info_at", lambda *args, **kwargs: "not JSON")
    response = client.get("/api/lookup/image", params={"image": IMAGE})
    assert response.status_code == 400
    assert "格式无效" in response.json()["error"]


@pytest.mark.parametrize("status,payload,message", [
    (200, {"digest": DIGEST, "tags": []}, None),
    (404, {}, "未找到"), (401, {}, "读取权限"), (503, {}, "503"),
    (200, {"digest": "wrong"}, "不一致"), (200, [], "不一致"),
])
def test_harbor_request_encoding_and_error_contract(monkeypatch, status, payload, message):
    real_client = httpx.Client
    def handler(request):
        assert request.method == "GET"
        assert request.url.params["with_tag"] == "true"
        assert "/repositories/team%252Fabacus-maca/artifacts/sha256%3A" in request.url.raw_path.decode()
        return httpx.Response(status, json=payload)
    def factory(**kwargs):
        assert kwargs["trust_env"] is False and kwargs["follow_redirects"] is False
        assert isinstance(kwargs["verify"], ssl.SSLContext)
        return real_client(transport=httpx.MockTransport(handler), **kwargs)
    monkeypatch.setattr(harbor.httpx, "Client", factory)
    if message:
        with pytest.raises((ValueError, ApiError), match=message):
            harbor.get_artifact("hpc-release", "team/abacus-maca", DIGEST)
    else:
        assert harbor.get_artifact("hpc-release", "team/abacus-maca", DIGEST) == payload


def test_harbor_timeout_is_a_readable_502(monkeypatch):
    real_client = httpx.Client
    def fail(request):
        raise httpx.ReadTimeout("timeout")
    monkeypatch.setattr(harbor.httpx, "Client", lambda **kw: real_client(transport=httpx.MockTransport(fail), **kw))
    with pytest.raises(ApiError) as error:
        harbor.get_artifact("hpc-release", "abacus-maca", DIGEST)
    assert error.value.status_code == 502
