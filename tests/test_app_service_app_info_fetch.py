from __future__ import annotations

import json

from app.db.connection import connect, reset_init_state
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


def seed_release_app(conn, *, git_url: str, git_branch: str) -> None:
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
        VALUES ('lammps', ?, ?, '["LAMMPS"]', 'test', '2026-01-01 00:00:00')
        """,
        (git_url, git_branch),
    )
    snapshot = {
        "app_id": "lammps",
        "official_name": "LAMMPS",
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
        ("rel-1", "lammps", json.dumps(snapshot)),
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
            lambda repo_type, repo_name, branch: (
                "ssh://sw-gerrit-devops.metax-internal.com:29418/PDE/HPC/hpc_lammps",
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
                "ssh://sw-gerrit-devops.metax-internal.com:29418/PDE/HPC/hpc_lammps",
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
            lambda repo_type, repo_name, branch: (None, None),
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
