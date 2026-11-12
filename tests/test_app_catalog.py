"""App catalog reads: app_info sync, dependencies, digest lookup, robots."""
from __future__ import annotations

import io
import json
import subprocess
import tarfile

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.deps import get_db, require_login
from app.integrations import gerrit as gerrit_integration
from app.main import create_app
from app.repositories import app_info_repo, apps_repo, entries_repo
from app.services import (
    catalog_service,
    cycle_service,
    lookup_service,
    notification_channels,
    qa_service,
)
from tests.conftest import (
    APP_INFO,
    close_qa,
    close_signup,
    make_app_info,
    seed_app,
    seed_cycle,
    seed_signup,
)

# ---------------------------------------------------------------------------
# app_info sync
# ---------------------------------------------------------------------------


def test_sync_stores_a_new_version_only_when_the_content_changed(temp_db, monkeypatch) -> None:
    app_id = seed_app(temp_db, app_info=None)
    raw = json.dumps(APP_INFO)
    head = {"value": "commit1"}
    monkeypatch.setattr(gerrit_integration, "head_commit", lambda *a, **k: head["value"])
    monkeypatch.setattr(gerrit_integration, "fetch_app_info_at", lambda *a, **k: raw)

    first = catalog_service.sync_app(temp_db, app_id, user="rm")
    assert first["status"] == "updated"
    assert apps_repo.get_app(temp_db, app_id)["sync_head_commit"] == "commit1"

    # Same head: no fetch, no version.
    assert catalog_service.sync_app(temp_db, app_id, user="rm")["status"] == "unchanged"
    # New head but identical content: still one version.
    head["value"] = "commit2"
    assert catalog_service.sync_app(temp_db, app_id, user="rm")["status"] == "unchanged"
    assert len(catalog_service.app_detail(temp_db, app_id)["app_info_versions"]) == 1

    head["value"] = "commit3"
    raw = json.dumps(make_app_info(app_version="2.0"))
    monkeypatch.setattr(gerrit_integration, "fetch_app_info_at", lambda *a, **k: raw)
    assert catalog_service.sync_app(temp_db, app_id, user="rm")["status"] == "updated"
    assert len(catalog_service.app_detail(temp_db, app_id)["app_info_versions"]) == 2


def test_a_sync_failure_is_recorded_on_the_app_and_never_raised(temp_db, monkeypatch) -> None:
    app_id = seed_app(temp_db)

    def boom(*_args, **_kwargs):
        raise RuntimeError("Gerrit 不可达")

    monkeypatch.setattr(gerrit_integration, "head_commit", boom)

    result = catalog_service.sync_app(temp_db, app_id, user="rm")

    assert result["status"] == "error"
    assert result["error"] == "Gerrit 不可达"
    status = {row["app_id"]: row for row in catalog_service.sync_status(temp_db)}
    assert status[app_id]["error"] == "Gerrit 不可达"
    assert status[app_id]["checked_at"]


def test_sync_all_only_visits_running_apps(temp_db, monkeypatch) -> None:
    seed_app(temp_db, "running_app", app_info=None)
    seed_app(temp_db, "stopped_app", app_info=None, cicd_status="stopped")
    visited: list[str] = []
    monkeypatch.setattr(
        catalog_service,
        "sync_app",
        lambda conn, app_id, **kwargs: visited.append(app_id)
        or {"app_id": app_id, "status": "unchanged"},
    )

    summary = catalog_service.sync_all(temp_db, user="rm")

    assert visited == ["running_app"]
    assert summary["checked"] == 1


@pytest.mark.parametrize("trigger", ["manual", "rm_all", "daily"])
def test_scope_expansion_from_any_sync_keeps_latest_and_qa_warning(
    temp_db_with_path, monkeypatch, trigger,
) -> None:
    conn, path = temp_db_with_path
    monkeypatch.setattr(settings, "db_path", path)
    app_id = seed_app(conn)
    release_id = seed_cycle(conn)
    entry = seed_signup(conn, release_id, app_id)
    close_signup(conn, release_id)
    raw = json.dumps(make_app_info(app_version="2.0", chips=["c500", "c600"]))
    monkeypatch.setattr(gerrit_integration, "head_commit", lambda *a, **k: "expanded-head")
    monkeypatch.setattr(gerrit_integration, "fetch_app_info_at", lambda *a, **k: raw)
    notices = []
    monkeypatch.setattr(notification_channels, "notify", lambda _conn, kind, message: notices.append((kind, message)))

    if trigger == "manual":
        result = catalog_service.sync_app(conn, app_id, user="owner_test", force=True)
        assert result["status"] == "updated"
    elif trigger == "rm_all":
        result = catalog_service.sync_all(conn, user="rm", force=True)
        assert (result["updated"], result["failed"]) == (1, 0)
    else:
        result = catalog_service.AppInfoSyncLoop()._run_once()
        assert (result["updated"], result["failed"]) == (1, 0)

    latest = app_info_repo.latest_version(conn, app_id)
    assert catalog_service.version_summary(latest)["app_version"] == "2.0"
    assert latest["id"] != entry["locked_version_id"]
    current = cycle_service.entry_view(conn, entries_repo.get_entry(conn, entry["id"]))
    assert current["locked_version_id"] == entry["locked_version_id"]
    assert current["behind_latest"] is True
    assert current["pending_scope_request"]["target_version_id"] == latest["id"]
    assert any("C600" in addition for addition in current["pending_scope_request"]["additions"])
    assert len(notices) == 1
    assert notices[0][0] == "scope_request"
    assert "测试范围扩大申请" in notices[0][1]
    assert apps_repo.get_app(conn, app_id)["sync_error"] == ""


def test_manifest_apps_resolve_their_project_before_fetching(temp_db, monkeypatch) -> None:
    app_id = seed_app(
        temp_db,
        "manifest_app",
        git_url="APP/lammps/master/hpc.xml",
        git_branch="master",
        app_info=None,
    )
    apps_repo.update_cicd_config(temp_db, app_id, {"cicd_repo_type": "repo"})
    temp_db.commit()
    monkeypatch.setattr(
        "app.identity.repo_to_git_identity",
        lambda repo_type, repo_name, branch, **_kw: (
            f"{settings.gerrit_hpc_base_url}/hpc_lammps",
            "maca_stable",
        ),
    )

    url, branch, source = catalog_service.app_info_fetch_target(
        apps_repo.get_app(temp_db, app_id)
    )

    assert (url, branch) == (f"{settings.gerrit_hpc_base_url}/hpc_lammps", "maca_stable")
    assert "APP/lammps/master/hpc.xml master" in source
    assert "hpc_lammps maca_stable" in source


def test_a_repo_app_without_a_manifest_path_cannot_be_fetched(temp_db) -> None:
    app_id = seed_app(temp_db, "broken_repo", git_url="hpc_broken", app_info=None)
    apps_repo.update_cicd_config(temp_db, app_id, {"cicd_repo_type": "repo"})
    temp_db.commit()

    with pytest.raises(RuntimeError, match="repo 类型缺少 manifest XML 路径"):
        catalog_service.app_info_fetch_target(apps_repo.get_app(temp_db, app_id))


def _app_info_archive(raw: str, commit_id: str) -> bytes:
    payload = raw.encode("utf-8")
    archive = io.BytesIO()
    with tarfile.open(
        fileobj=archive, mode="w", format=tarfile.PAX_FORMAT, pax_headers={"comment": commit_id}
    ) as tar:
        member = tarfile.TarInfo("app_info.json")
        member.size = len(payload)
        tar.addfile(member, io.BytesIO(payload))
    return archive.getvalue()


def test_gerrit_fetch_reuses_one_ssh_connection_and_pins_archive_commit(monkeypatch, tmp_path):
    commit_id = "a" * 40
    archive = _app_info_archive(json.dumps(APP_INFO), commit_id)
    calls: list[list[str]] = []

    def fake_run(args, **_kwargs):
        calls.append(args)
        if "ls-remote" in args:
            return subprocess.CompletedProcess(
                args, 0, stdout=f"{commit_id}\trefs/heads/maca\n".encode(), stderr=b""
            )
        return subprocess.CompletedProcess(args, 0, stdout=archive, stderr=b"")

    monkeypatch.setattr(gerrit_integration, "_run_git", fake_run)

    raw, fetched_commit = gerrit_integration.fetch_app_info(
        "hpc_lammps", "maca", project_root=tmp_path
    )

    assert json.loads(raw) == APP_INFO
    assert fetched_commit == commit_id
    assert len(calls) == 2
    assert calls[0][:2] == ["git", "-c"]
    assert calls[0][2] == calls[1][2]
    assert "ControlMaster=auto" in calls[0][2]
    assert calls[0][3:] == ["ls-remote", f"{settings.gerrit_hpc_base_url}/hpc_lammps", "maca"]
    assert calls[1][3:] == [
        "archive",
        f"--remote={settings.gerrit_hpc_base_url}/hpc_lammps",
        commit_id,
        "app_info.json",
    ]


# ---------------------------------------------------------------------------
# Dependencies and the compatibility list
# ---------------------------------------------------------------------------


def _published_app(conn, app_id: str, *, dependencies=None, digest: str = "") -> str:
    seed_app(
        conn,
        app_id,
        official_name=app_id.title(),
        app_info=make_app_info(app_name=app_id, dependencies=dependencies),
    )
    return app_id


def test_dependency_overview_groups_by_component_and_lists_undeclared_apps(temp_db) -> None:
    _published_app(temp_db, "alpha", dependencies=["mcpy", "sdk"])
    _published_app(temp_db, "beta", dependencies=["mcpy"])
    _published_app(temp_db, "gamma")  # no app_dependency field at all
    seed_app(temp_db, "stopped_app", cicd_status="stopped")

    overview = catalog_service.dependency_overview(temp_db)

    components = {group["component"]: [row["app_id"] for row in group["apps"]]
                  for group in overview["components"]}
    assert components == {"mcpy": ["alpha", "beta"], "sdk": ["alpha"]}
    assert [row["app_id"] for row in overview["undeclared"]] == ["gamma"]
    assert "stopped_app" not in {app_id for ids in components.values() for app_id in ids}
    assert "stopped_app" not in {row["app_id"] for row in overview["undeclared"]}


def test_the_compatibility_list_only_holds_apps_with_an_official_version(temp_db) -> None:
    _published_app(temp_db, "alpha", dependencies=["mcpy"])
    _published_app(temp_db, "beta", dependencies=["mcpy"])
    release_id = seed_cycle(temp_db)
    seed_signup(temp_db, release_id, "alpha", target_version="1.0")
    qa_service.set_qa_status_batch(
        temp_db,
        release_id,
        [{"app_id": "alpha", "status": "qa_passed", "deliverable_image": "hpc/alpha:1",
          "deliverable_digest": "sha256:alpha"}],
        user="qa",
        role="QA",
    )
    close_qa(temp_db, release_id)
    cycle_service.publish(temp_db, release_id, user="rm", role="RM")

    rows = catalog_service.compat_list(temp_db)
    assert [row["app_id"] for row in rows] == ["alpha"]
    assert rows[0]["official"]["digest"] == "sha256:alpha"
    assert [test["path"] for test in rows[0]["tests"]] == ["sanity"]

    csv_text = catalog_service.compat_list_csv(temp_db, "mcpy")
    assert "Alpha" in csv_text and "Beta" not in csv_text
    assert "sha256:alpha" in csv_text
    assert catalog_service.compat_list_csv(temp_db, "unknown-component").splitlines()[1:] == []


# ---------------------------------------------------------------------------
# Digest / App lookup
# ---------------------------------------------------------------------------


def test_lookup_by_digest_returns_the_cycle_environment_and_test_commands(temp_db) -> None:
    app_id = seed_app(temp_db)
    release_id = seed_cycle(temp_db)
    cycle_service.save_environment(
        temp_db, release_id, [{"component": "MACA", "version": "3.0"}], user="rm", role="RM"
    )
    image_id = cycle_service.save_base_image(
        temp_db, release_id, {"name": "base", "image": "hpc/base:1", "digest": "sha256:base"},
        user="rm", role="RM",
    )[0]["id"]
    seed_signup(temp_db, release_id, app_id, base_image_id=image_id)
    qa_service.set_qa_status_batch(
        temp_db,
        release_id,
        [{"app_id": app_id, "status": "qa_passed", "deliverable_image": "hpc/testapp:1",
          "deliverable_digest": "sha256:deadbeef", "deliverable_commit": "abc123"}],
        user="qa",
        role="QA",
    )

    found = lookup_service.by_digest(temp_db, "deadbeef")

    assert len(found["entries"]) == 1
    record = found["entries"][0]
    assert record["app_name"] == "TestApp"
    assert record["release"]["name"] == "2099-01"
    assert record["environment"] == [
        {"id": record["environment"][0]["id"], "component": "MACA", "version": "3.0",
         "note": "", "sort": 0}
    ]
    assert record["base_image"]["name"] == "base"
    assert record["entry"]["deliverable_commit"] == "abc123"
    assert [t["path"] for t in record["scope_tests"]] == ["sanity"]
    assert record["test_commands"][0]["test"] == "sanity"

    # A base image can be looked up by its own digest.
    images = lookup_service.by_digest(temp_db, "sha256:base")["base_images"]
    assert [image["name"] for image in images] == ["base"]
    assert lookup_service.by_digest(temp_db, "sha256:nothing")["entries"] == []
    with pytest.raises(ValueError, match="digest"):
        lookup_service.by_digest(temp_db, "  ")


def test_lookup_by_app_lists_every_cycle_the_app_took_part_in(temp_db) -> None:
    app_id = seed_app(temp_db)
    first = seed_cycle(temp_db, name="2099-01")
    seed_signup(temp_db, first, app_id, target_version="1.0")
    second = seed_cycle(
        temp_db, name="2099-02", signup_deadline="2099-02-01 23:59",
        qa_deadline="2099-02-23 23:59", release_date="2099-02-28",
    )
    seed_signup(temp_db, second, app_id, target_version="2.0")

    result = lookup_service.by_app(temp_db, app_id)

    assert result["cicd_status"] == "running"
    assert [record["release"]["name"] for record in result["entries"]] == ["2099-02", "2099-01"]
    with pytest.raises(KeyError):
        lookup_service.by_app(temp_db, "unknown")


# ---------------------------------------------------------------------------
# Notification channel settings
# ---------------------------------------------------------------------------


def test_channel_settings_are_masked_and_secrets_are_kept_on_partial_saves(temp_db) -> None:
    rows = {row["kind"]: row for row in notification_channels.list_settings(temp_db)}
    assert set(rows) == set(notification_channels.KINDS)
    assert rows["scope_request"]["configured"] is False
    assert rows["scope_request"]["access_token_mask"] == ""

    saved = notification_channels.save_settings(
        temp_db,
        "scope_request",
        enabled=True,
        access_token="abcdef123456",
        secret="SECtopsecret",
        at_user_ids=[" u1 ", "", "u2"],
        user="rm",
        role="RM",
    )
    assert saved["configured"] is True
    assert saved["access_token_mask"].endswith("3456")
    assert "abcdef" not in saved["access_token_mask"]
    assert saved["secret_mask"].endswith("cret")
    assert saved["at_user_ids"] == ["u1", "u2"]
    assert "access_token" not in saved and "secret" not in saved

    # Omitting token/secret keeps the stored values.
    kept = notification_channels.save_settings(
        temp_db, "scope_request", enabled=False, access_token=None, secret=None,
        at_user_ids=["u1"], user="rm", role="RM",
    )
    assert kept["enabled"] is False
    assert kept["configured"] is True
    stored = notification_channels.enabled_channel(temp_db, "scope_request")
    assert stored is None  # disabled kinds send nothing

    with pytest.raises(ValueError, match="未知的通知类型"):
        notification_channels.send_test(temp_db, "nonsense", user="rm")
    with pytest.raises(ValueError, match="尚未配置"):
        notification_channels.send_test(temp_db, "scope_decision", user="rm")


def test_http_channel_settings_are_rm_only_and_never_return_secrets(temp_db) -> None:
    app = create_app()
    current = {"user": {"username": "rm", "role": "RM", "display_name": "RM"}}

    def override_db():
        yield temp_db

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[require_login] = lambda: current["user"]
    with TestClient(app, raise_server_exceptions=False) as client:
        saved = client.post(
            "/api/notifications/channels/save",
            json={
                "kind": "scope_request",
                "enabled": True,
                "access_token": "tok-secret-value",
                "secret": "sign-secret-value",
                "at_user_ids": ["u1"],
            },
        )
        assert saved.status_code == 200
        assert "tok-secret-value" not in saved.text
        assert "sign-secret-value" not in saved.text

        listed = client.get("/api/notifications/channels")
        assert listed.status_code == 200
        assert "tok-secret-value" not in listed.text
        kinds = [row["kind"] for row in listed.json()["channels"]]
        assert kinds == list(notification_channels.KINDS)

        current["user"] = {"username": "owner_test", "role": "Owner", "display_name": "Owner"}
        assert client.get("/api/notifications/channels").status_code == 403
        assert client.post(
            "/api/notifications/channels/save", json={"kind": "scope_request", "enabled": False}
        ).status_code == 403
