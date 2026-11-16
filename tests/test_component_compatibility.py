"""Component reports retain their own scope and support independent, conflict-safe QA updates."""
from copy import deepcopy

import pytest
from fastapi.testclient import TestClient

from app.api.errors import ApiError, AuthzError
from app.deps import get_db, require_login
from app.main import create_app
from app.repositories import component_compatibility_repo as repo
from app.services import component_compatibility_service as service
from tests.conftest import seed_app
from tests.test_latest_running_releases import inventory as inventory


@pytest.fixture()
def source(temp_db, monkeypatch):
    base = {
        "app_id": "pyg", "app_name": "PyG", "git_branch": "maca", "release_id": "released",
        "app_version": "0.4", "hpc_version": "3.9.0", "manifest_source": "sealed",
        "dependencies": ["sdk", "torch 2.8 + py312"], "path": "run", "os": "ubuntu22.04", "arch": "amd64",
        "chips": ["C500"], "GPU": "C500", "delivery_reference": "image:tag@sha256:digest",
        "app_info_commit_id": "image-commit", "official_version_id": 5, "note": "",
    }
    rows = [base, {**base, "chips": ["C600"], "GPU": "C600"}, {**base, "path": "other"},
            {**base, "app_id": "hpl", "app_name": "HPL", "hpc_version": "3.10.0", "dependencies": ["sdk"]},
            {**base, "app_id": "pending", "release_id": "", "manifest_source": "unpublished", "path": "", "app_version": "", "hpc_version": ""}]
    monkeypatch.setattr(service.lookup_service, "latest_running_releases", lambda conn: {"rows": deepcopy(rows)})
    return rows


def create(conn, component="sdk", version="3.10.0"):
    return service.create(conn, {"component": component, "component_version": version}, user="qa", role="QA")


@pytest.mark.parametrize("component", ["driver", "sdk"])
def test_universal_components_include_undeclared_and_unpublished_apps(temp_db, source, component):
    source.append({**source[0], "app_id": "undeclared", "dependencies": []})
    report = create(temp_db, component=component)
    assert {app["app_id"] for app in report["apps"]} == {"pyg", "hpl", "pending", "undeclared"}
    pending = next(app for app in report["apps"] if app["app_id"] == "pending")
    assert pending["scope_note"] == "尚无成功发布记录" and pending["tests"] == []
    assert pending["app_version"] == pending["hpc_version"] == ""
    assert service.components(temp_db)["components"][:2] == [
        {"component": "driver", "app_count": 4, "all_apps": True},
        {"component": "sdk", "app_count": 4, "all_apps": True}]


def test_universal_choices_exist_without_dependency_declarations(temp_db, source):
    for row in source:
        row["dependencies"] = []
    assert [item["component"] for item in service.components(temp_db)["components"]] == ["driver", "sdk"]
    assert len(create(temp_db, component="SDK")["apps"]) == 3


def test_prepared_scope_saves_without_repeating_remote_reads(temp_db, source, monkeypatch):
    prepared = service.components(temp_db, user="qa", role="QA")
    def no_remote_reads(conn):
        raise AssertionError("a prepared form must not query Harbor/Gerrit again")
    monkeypatch.setattr(service.lookup_service, "latest_running_releases", no_remote_reads)
    report = service.create(temp_db, {"component": "sdk", "component_version": "3.10.0", "preview_id": prepared["preview_id"]}, user="qa", role="QA")
    assert {app["app_id"] for app in report["apps"]} == {"pyg", "hpl", "pending"}


def test_prepared_scope_rejects_changed_sources_and_other_users(temp_db, source):
    token = service.components(temp_db, user="qa", role="QA")["preview_id"]
    body = {"component": "sdk", "component_version": "3.10.0", "preview_id": token}
    with pytest.raises(ApiError, match="清单"):
        service.create(temp_db, body, user="other", role="QA")
    seed_app(temp_db, "new-app-after-preview")
    with pytest.raises(ApiError, match="发生变化"):
        service.create(temp_db, body, user="qa", role="QA")
    assert service.list_reports(temp_db) == {"reports": []}


def test_expired_or_invalid_previews_do_not_write_reports(temp_db, source, monkeypatch):
    token = service.components(temp_db, user="qa", role="QA")["preview_id"]
    now = service.time.monotonic()
    monkeypatch.setattr(service.time, "monotonic", lambda: now + service.PREVIEW_TTL_SECONDS + 1)
    body = {"component": "sdk", "component_version": "3.10.0", "preview_id": token}
    with pytest.raises(ApiError, match="清单"):
        service.create(temp_db, body, user="qa", role="QA")
    with pytest.raises(ValueError, match="凭据"):
        service.create(temp_db, {**body, "preview_id": []}, user="qa", role="QA")
    assert service.list_reports(temp_db) == {"reports": []}


def test_records_the_actual_hpc_patch_version_from_the_delivery_tag(temp_db, source):
    for row in source:
        if row["app_id"] == "pyg":
            row["image_pattern"] = "pyg:0.4-<hpc_version>-ubuntu22.04-amd64"
            row["delivery_reference"] = "example/pyg:0.4-3.9.0.12-ubuntu22.04-amd64@sha256:" + "a" * 64
    report = create(temp_db)
    pyg = next(app for app in report["apps"] if app["app_id"] == "pyg")
    assert pyg["hpc_version"] == "3.9.0.12"


def test_freezes_published_scope_and_deduplicates_supported_gpu_rows(temp_db, source):
    assert service.components(temp_db)["components"] == [
        {"component": "driver", "app_count": 3, "all_apps": True},
        {"component": "sdk", "app_count": 3, "all_apps": True},
        {"component": "torch 2.8 + py312", "app_count": 1, "all_apps": False}]
    report = create(temp_db, component="torch 2.8 + py312")
    assert [app["app_id"] for app in report["apps"]] == ["pyg"]
    pyg = report["apps"][0]
    assert pyg["hpc_version"] == "3.9.0"
    assert pyg["tests"][0] == {"path": "run", "command": "", "platforms": [
        {"os": "ubuntu22.04", "arch": "amd64", "chips": ["C500", "C600"]}]}
    assert len(pyg["tests"]) == 2 and len(pyg["baselines"]) == 1
    assert pyg["baselines"][0]["app_info_commit_id"] == "image-commit"
    assert all(app["qa_status"] == app["qa_comment"] == "" and app["revision"] == 0 for app in report["apps"])
    source.clear()
    source.append({"app_id": "new-development", "dependencies": []})
    assert service.get_report(temp_db, report["id"]) == report


def test_new_component_versions_take_new_scope_without_changing_older_records(temp_db, source):
    first = create(temp_db, "torch 2.8 + py312")
    source[0].update(app_version="0.5", hpc_version="3.10.0")
    second = create(temp_db, "torch 2.8 + py312", "3.10.1")
    assert first["id"] != second["id"]
    assert second["apps"][0]["app_version"] == "0.5, 0.4"
    assert service.get_report(temp_db, first["id"]) == first
    assert len(service.list_reports(temp_db)["reports"]) == 2


def test_duplicate_or_unknown_component_does_not_replace_any_record(temp_db, source):
    report = create(temp_db)
    with pytest.raises(ApiError, match="已存在") as exc:
        create(temp_db)
    assert exc.value.status_code == 409
    with pytest.raises(ValueError, match="暂无"):
        create(temp_db, "unknown")
    assert service.get_report(temp_db, report["id"]) == report


def test_save_is_per_app_and_per_component_version_with_optional_comment(temp_db, source):
    a = create(temp_db)
    b = create(temp_db, version="3.10.1")
    before = deepcopy(a)
    result = service.save_app(temp_db, a["id"], "pyg", {"qa_status": "fail", "qa_comment": "", "revision": 0}, user="qa", role="QA")
    assert result["app"]["qa_status"] == "fail" and result["app"]["revision"] == 1
    assert result["app"]["tests"] == next(app for app in before["apps"] if app["app_id"] == "pyg")["tests"]
    assert service.get_report(temp_db, a["id"])["apps"][0] == before["apps"][0]
    assert service.get_report(temp_db, b["id"]) == b
    changed = service.save_app(temp_db, a["id"], "pyg", {"qa_status": "pass", "qa_comment": "通过", "revision": 1}, user="rm", role="RM")
    cleared = service.save_app(temp_db, a["id"], "pyg", {"qa_status": "pass", "qa_comment": "  ", "revision": 2}, user="qa", role="QA")
    assert changed["app"]["qa_comment"] == "通过" and cleared["app"]["qa_comment"] == ""
    assert cleared["app"]["updated_by"] == "qa" and cleared["app"]["updated_at"]


def test_conflicting_update_retains_first_writer_and_reports_conflict(temp_db, source):
    report = create(temp_db)
    body = {"qa_status": "pass", "qa_comment": "first", "revision": 0}
    saved = service.save_app(temp_db, report["id"], "pyg", body, user="qa", role="QA")
    count = temp_db.execute("SELECT COUNT(*) FROM audit").fetchone()[0]
    with pytest.raises(ApiError, match="其他人修改") as exc:
        service.save_app(temp_db, report["id"], "pyg", {**body, "qa_comment": "second"}, user="rm", role="RM")
    assert exc.value.status_code == 409
    assert repo.get_app(temp_db, report["id"], "pyg") == saved["app"]
    assert temp_db.execute("SELECT COUNT(*) FROM audit").fetchone()[0] == count


@pytest.mark.parametrize("body", [
    {"qa_status": "unknown", "revision": 0}, {"qa_status": "pass"},
    {"qa_status": "pass", "revision": True}, {"qa_status": "pass", "revision": -1},
    {"qa_status": "fail", "revision": 0, "qa_comment": []},
])
def test_invalid_updates_are_not_written(temp_db, source, body):
    report = create(temp_db)
    with pytest.raises(ValueError):
        service.save_app(temp_db, report["id"], "pyg", body, user="qa", role="QA")
    assert service.get_report(temp_db, report["id"]) == report


def test_remote_failures_prevent_partial_report_creation(temp_db, source):
    source.append({**source[0], "app_id": "failed", "note": "交付镜像解析失败：Harbor unavailable", "dependencies": []})
    with pytest.raises(ApiError, match="无法生成完整") as exc:
        create(temp_db)
    assert exc.value.status_code == 502
    assert "failed：交付镜像解析失败：Harbor unavailable" in str(exc.value)
    assert "刷新组件列表重试" in str(exc.value)
    assert service.list_reports(temp_db) == {"reports": []}


def test_source_change_during_remote_lookup_requires_recreation(temp_db, monkeypatch, source):
    def changed(conn):
        seed_app(conn, "added-during-query")
        return {"rows": source}
    monkeypatch.setattr(service.lookup_service, "latest_running_releases", changed)
    with pytest.raises(ApiError, match="发生变化"):
        create(temp_db)
    assert service.list_reports(temp_db) == {"reports": []}


def test_audit_failure_rolls_back_report_and_result_writes(temp_db, monkeypatch, source):
    report = create(temp_db)
    def fail(*args, **kwargs):
        raise RuntimeError("audit failed")
    monkeypatch.setattr(service, "log_audit", fail)
    with pytest.raises(RuntimeError):
        create(temp_db, version="3.10.1")
    with pytest.raises(RuntimeError):
        service.save_app(temp_db, report["id"], "pyg", {"qa_status": "pass", "revision": 0}, user="qa", role="QA")
    assert len(service.list_reports(temp_db)["reports"]) == 1
    assert service.get_report(temp_db, report["id"]) == report


@pytest.mark.parametrize("role", ["Owner", "Guest", "SPD", "Admin"])
def test_services_reject_non_qa_writers(temp_db, role):
    with pytest.raises(AuthzError):
        service.create(temp_db, {}, user="user", role=role)
    with pytest.raises(AuthzError):
        service.save_app(temp_db, 1, "pyg", {}, user="user", role=role)


@pytest.mark.parametrize("role,read,write", [("QA", 200, 200), ("RM", 200, 200), ("Owner", 200, 403), ("Guest", 403, 403), ("SPD", 403, 403), ("Admin", 403, 403)])
def test_http_permissions_payload_and_readback(temp_db, source, role, read, write):
    app = create_app()
    app.dependency_overrides[get_db] = lambda: temp_db
    app.dependency_overrides[require_login] = lambda: {"username": "tester", "role": role}
    with TestClient(app) as client:
        response = client.get("/api/component-compatibility/components")
        assert response.status_code == read
        if read == 200:
            assert response.headers["cache-control"] == "no-store"
        preview_id = response.json().get("preview_id") if read == 200 else None
        response = client.post("/api/component-compatibility", json={"component": "sdk", "component_version": "3.10.0", "preview_id": preview_id})
        assert response.status_code == write
        if write == 200:
            report = response.json()
            assert len(report["apps"]) == 3
            response = client.post(f"/api/component-compatibility/{report['id']}/apps/pyg", json={"qa_status": "pass", "qa_comment": "<script>literal</script>", "revision": 0})
            assert response.status_code == 200
            saved_apps = client.get(f"/api/component-compatibility/{report['id']}").json()["apps"]
            assert next(app for app in saved_apps if app["app_id"] == "pyg")["qa_comment"] == "<script>literal</script>"
            assert client.get("/api/component-compatibility").json()["reports"][0]["id"] == report["id"]


def test_real_public_inventory_selects_only_latest_successfully_published_running_apps(temp_db, inventory):
    report = create(temp_db)
    assert {app["app_id"] for app in report["apps"]} == {"amber", "hpl", "skipped", "withdrawn"}
    assert {app["app_id"]: app["hpc_version"] for app in report["apps"]}["amber"] == "3.9"
    assert all(app["tests"] and app["baselines"] for app in report["apps"])
