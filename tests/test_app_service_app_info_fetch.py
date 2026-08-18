from __future__ import annotations

import io
import json
import subprocess
import tarfile
import threading
import time
from pathlib import Path

from fastapi import Request
from fastapi.responses import StreamingResponse

from app.db.connection import connect, reset_init_state
from app import identity
from app.integrations import gerrit as gerrit_integration
from app.services import app_service


APP_INFO = {
    "app_version": "22Jul2025",
    "app_name": "lammps",
    "app_build": {
        "ubuntu20.04_amd64": {
            "build_target": "release",
            "arch": "amd64",
            "supported_chip": ["c500"],
            "enabled": True,
        }
    },
    "app_test": {},
}


def fresh_conn():
    reset_init_state()
    return connect(":memory:")


def seed_release_app(
    conn,
    *,
    git_url: str,
    git_branch: str,
    app_id: str = "lammps",
    create_release: bool = True,
) -> None:
    if create_release:
        conn.execute(
            """
            INSERT INTO releases(
                id, name, maca_version, app_freeze_deadline, doc_deadline,
                released_locked, created_at, source
            )
            VALUES ('rel-1', '3.0', '3.0', '2099-01-01 00:00:00',
                    '2099-02-01 00:00:00', 0, '2026-01-01 00:00:00', 'manual')
            """
        )
    conn.execute(
        """
        INSERT INTO apps(id, git_url, git_branch, aliases_json, created_by, created_at)
        VALUES (?, ?, ?, ?, 'test', '2026-01-01 00:00:00')
        """,
        (app_id, git_url, git_branch, json.dumps([app_id.upper()])),
    )
    snapshot = {
        "app_id": app_id,
        "official_name": app_id.upper(),
        "version": "",
        "release_decision": "release",
        "owners": ["owner"],
        "owner_confirmed": False,
        "doc": {},
        "community": {},
        "test_docs": [],
        "app_info": None,
        "app_info_diffs": [],
    }
    conn.execute(
        "INSERT INTO snapshots(release_id, app_id, data_json) VALUES (?, ?, ?)",
        ("rel-1", app_id, json.dumps(snapshot)),
    )
    conn.commit()


def test_fetch_app_info_resolves_repo_manifest_before_gerrit_fetch(monkeypatch):
    conn = fresh_conn()
    try:
        seed_release_app(
            conn,
            git_url="APP/lammps/master/hpc_22Jul2025.xml",
            git_branch="master",
        )
        monkeypatch.setattr(
            "app.identity.repo_to_git_identity",
            lambda repo_type, repo_name, branch, **_kwargs: (
                "ssh://gerrit.metax-internal.com:29418/PDE/HPC/hpc_lammps",
                "maca_stable_22Jul2025",
            ),
        )

        calls = []

        def fake_fetch(git_url, branch, **_kwargs):
            calls.append((git_url, branch))
            return json.dumps(APP_INFO), "abc123"

        monkeypatch.setattr("app.integrations.gerrit.fetch_app_info", fake_fetch)

        result = app_service.fetch_app_info(
            conn,
            release_id="rel-1",
            app_id="lammps",
            uploaded_by="owner",
            role="Owner",
        )

        assert calls == [
            (
                "ssh://gerrit.metax-internal.com:29418/PDE/HPC/hpc_lammps",
                "maca_stable_22Jul2025",
            )
        ]
        assert result["fetch_git_branch"] == "maca_stable_22Jul2025"
        assert "APP/lammps/master/hpc_22Jul2025.xml master" in result["source"]
        assert "hpc_lammps maca_stable_22Jul2025" in result["source"]
        assert result["snapshot"]["version"] == "22Jul2025"
    finally:
        conn.close()


def test_fetch_app_info_reports_manifest_resolution_failure(monkeypatch):
    conn = fresh_conn()
    try:
        seed_release_app(
            conn,
            git_url="APP/lammps/master/hpc_22Jul2025.xml",
            git_branch="master",
        )
        monkeypatch.setattr(
            "app.identity.repo_to_git_identity",
            lambda repo_type, repo_name, branch, **_kwargs: (None, None),
        )

        try:
            app_service.fetch_app_info(
                conn,
                release_id="rel-1",
                app_id="lammps",
                uploaded_by="owner",
                role="Owner",
            )
        except RuntimeError as exc:
            assert "无法解析 repo manifest" in str(exc)
        else:
            raise AssertionError("expected RuntimeError")
    finally:
        conn.close()


def test_manifest_fetch_target_observes_changed_xml_contents(monkeypatch):
    manifest_path = "APP/slurm/hpc_slurm_22.05.3.xml"
    current = {"name": "hpc_slurm", "revision": "dev"}
    archive_calls = []

    def fake_archive(remote, branch, path, dest_dir):
        archive_calls.append((remote, branch, path))
        target = Path(dest_dir) / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            "<manifest><project "
            f"name=\"{current['name']}\" revision=\"{current['revision']}\">"
            '<linkfile src="app_info.json"/></project></manifest>',
            encoding="utf-8",
        )
        return True

    monkeypatch.setattr(identity, "_git_archive_extract", fake_archive)
    identity.clear_manifest_cache()
    app = {
        "git_url": manifest_path,
        "git_branch": "master",
        "cicd_repo_type": "repo",
    }
    try:
        first = app_service._app_info_fetch_target(app)
        current.update(name="hpc_slurm_next", revision="release-next")
        second = app_service._app_info_fetch_target(app)
    finally:
        identity.clear_manifest_cache()

    assert first[:2] == (
        "ssh://gerrit.metax-internal.com:29418/PDE/HPC/hpc_slurm",
        "dev",
    )
    assert second[:2] == (
        "ssh://gerrit.metax-internal.com:29418/PDE/HPC/hpc_slurm_next",
        "release-next",
    )
    assert len(archive_calls) == 2


def _app_info_archive(raw: str, commit_id: str) -> bytes:
    payload = raw.encode("utf-8")
    archive = io.BytesIO()
    with tarfile.open(
        fileobj=archive,
        mode="w",
        format=tarfile.PAX_FORMAT,
        pax_headers={"comment": commit_id},
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
                args,
                0,
                stdout=f"{commit_id}\trefs/heads/maca\n".encode(),
                stderr=b"",
            )
        return subprocess.CompletedProcess(args, 0, stdout=archive, stderr=b"")

    monkeypatch.setattr(gerrit_integration, "_run_git", fake_run)

    raw, fetched_commit = gerrit_integration.fetch_app_info(
        "hpc_lammps",
        "maca",
        project_root=tmp_path,
    )

    assert json.loads(raw) == APP_INFO
    assert fetched_commit == commit_id
    assert len(calls) == 2
    assert calls[0][:2] == ["git", "-c"]
    assert calls[0][2] == calls[1][2]
    assert "ControlMaster=auto" in calls[0][2]
    assert calls[0][3:] == [
        "ls-remote",
        "ssh://gerrit.metax-internal.com:29418/PDE/HPC/hpc_lammps",
        "maca",
    ]
    assert calls[1][3:] == [
        "archive",
        "--remote=ssh://gerrit.metax-internal.com:29418/PDE/HPC/hpc_lammps",
        commit_id,
        "app_info.json",
    ]


def test_bulk_fetch_limits_concurrency_and_reports_every_result():
    conn = fresh_conn()
    try:
        for index in range(5):
            seed_release_app(
                conn,
                app_id=f"app{index}",
                git_url=f"hpc_app{index}",
                git_branch="maca",
                create_release=index == 0,
            )

        lock = threading.Lock()
        active = 0
        peak_active = 0

        def fake_fetch(git_url, _branch, **_kwargs):
            nonlocal active, peak_active
            with lock:
                active += 1
                peak_active = max(peak_active, active)
            time.sleep(0.03)
            with lock:
                active -= 1
            if git_url.endswith("hpc_app3"):
                raise RuntimeError("app3 fetch failed")
            return json.dumps(APP_INFO), git_url[-1] * 40

        plan = app_service.prepare_fetch_all_app_infos(
            conn,
            release_id="rel-1",
            max_workers=2,
        )
        events = list(app_service.iter_fetch_all_app_infos(
            conn,
            plan=plan,
            uploaded_by="rm",
            role="RM",
            fetch_fn=fake_fetch,
        ))

        assert events[0] == {"type": "start", "total": 5, "max_workers": 2}
        item_events = [event for event in events if event["type"] == "item"]
        assert len(item_events) == 5
        assert [event["completed"] for event in item_events] == [1, 2, 3, 4, 5]
        assert peak_active == 2

        completed = events[-1]
        assert completed["type"] == "complete"
        assert completed["succeeded"] == 4
        assert completed["failed"] == 1
        assert len(completed["results"]) == 5
        assert completed["results"][3] == {
            "app_id": "app3",
            "ok": False,
            "error": "app3 fetch failed",
        }
    finally:
        conn.close()


def test_fetch_all_route_negotiates_ndjson_streaming_contract():
    conn = fresh_conn()
    try:
        seed_release_app(
            conn,
            app_id="app1",
            git_url="hpc_app1",
            git_branch="maca",
        )
        from app.api.routers import apps as apps_router

        request = Request({
            "type": "http",
            "method": "POST",
            "path": "/api/app-info/fetch-all",
            "headers": [(b"accept", b"application/x-ndjson")],
            "query_string": b"",
            "scheme": "http",
            "server": ("testserver", 80),
            "client": ("testclient", 123),
            "http_version": "1.1",
        })
        response = apps_router.api_app_info_fetch_all(
            request=request,
            body={"release_id": "rel-1"},
            user={"username": "rm", "role": "RM"},
            conn=conn,
        )
        assert isinstance(response, StreamingResponse)
        assert response.media_type == "application/x-ndjson"
        assert response.headers["cache-control"] == "no-cache, no-transform"
        assert response.headers["x-accel-buffering"] == "no"

        encoded = list(apps_router._encode_ndjson_events([
            {"type": "start", "total": 1},
            {"type": "item", "app_id": "app1", "ok": False, "error": "错误详情"},
        ]))
        assert [json.loads(line) for line in encoded] == [
            {"type": "start", "total": 1},
            {"type": "item", "app_id": "app1", "ok": False, "error": "错误详情"},
        ]
    finally:
        conn.close()
