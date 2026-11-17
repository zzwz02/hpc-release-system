from __future__ import annotations

import argparse
import hashlib
import io
import json
import sqlite3

import pytest
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font

from app.db.connection import connect
from app.domain import delivery_images
from app.integrations import gerrit, harbor
from app.repositories import app_info_repo, apps_repo, official_repo
from tests.test_monthly_migration import app_info_doc, build_old_db
from tools import cutover_release as cutover


def summary(version, *, label="3.9.0.x"):
    book = Workbook()
    sheet = book.active
    sheet.title = "C500"
    sheet.append(["Test Environment"])
    sheet.append(["CpuName", "Intel Xeon"])
    sheet.append(["Host SDK Version", "3.9.0.29"])
    sheet.append(["HPC Version", version])
    sheet.append(["Case Name", "Branch", "Metric Golden", "Metric Unit", label, "Previous", "Ratio", "Notes"])
    sheet.append(["amber_sanity", "maca", "max", "s", 0, 2, 0, "性能回退"])
    sheet["A1"].font = Font(name="Arial", size=18, bold=True)
    sheet.merge_cells("A1:C1")
    sheet["J2"] = "=1+2"
    buf = io.BytesIO()
    book.save(buf)
    return buf.getvalue()


def legacy(path, *, pending=False):
    build_old_db(path, open_request=pending)
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE users(username TEXT PRIMARY KEY,password_hash TEXT NOT NULL,role TEXT NOT NULL)")
    conn.execute("INSERT INTO users VALUES('existing','test-only-hash','RM')")
    conn.execute("CREATE TABLE qa_logs(release_id TEXT PRIMARY KEY,filename TEXT NOT NULL,storage_path TEXT NOT NULL DEFAULT '',"
                 "uploaded_at TEXT NOT NULL DEFAULT '',uploaded_by TEXT NOT NULL DEFAULT '',content BLOB)")
    for release, hpc in [("rel-1", "3.9.0.12"), ("rel-2", "3.10.0.2")]:
        conn.execute("INSERT INTO qa_logs(release_id,filename,content) VALUES(?,?,?)",
                     (release, "original.xlsx", summary(hpc, label=hpc.rsplit('.', 1)[0] + '.x')))
    conn.commit()
    conn.close()


def test_backup_uses_wal_and_never_writes_source(tmp_path):
    source, target = tmp_path / "source.db", tmp_path / "target.db"
    legacy(source)
    live = sqlite3.connect(source)
    live.execute("PRAGMA journal_mode=WAL")
    live.execute("UPDATE releases SET name='3.9.1' WHERE id='rel-1'")
    live.commit()
    before = source.read_bytes()
    cutover.snapshot_source(source, target)
    assert source.read_bytes() == before
    with sqlite3.connect(target) as copied:
        assert copied.execute("SELECT name FROM releases WHERE id='rel-1'").fetchone()[0] == "3.9.1"
    live.close()


def test_pending_request_rejects_before_backup(tmp_path):
    source, target = tmp_path / "source.db", tmp_path / "target.db"
    legacy(source, pending=True)
    with pytest.raises(ValueError, match="未完成 CICD"):
        cutover.snapshot_source(source, target)
    assert not target.exists()


def test_migration_preserves_accounts_and_both_publications_uses_lock_order(tmp_path):
    source = tmp_path / "old.db"
    legacy(source)
    with sqlite3.connect(source) as conn:
        # A historical cycle was entered later than the actual latest cycle.
        conn.execute("UPDATE releases SET created_at='2030-01-01' WHERE id='rel-1'")
        conn.execute("UPDATE releases SET released_locked_at='2026-04-25T20:00:00+00:00' WHERE id='rel-2'")
    conn, _, _, _ = cutover.migrate(source, {"3.9.0": "3.9.0.29", "3.10.0": "3.10.0.11"})
    try:
        assert [r[0] for r in conn.execute("SELECT username FROM users")] == ["existing"]
        assert conn.execute("SELECT release_date FROM releases WHERE id='rel-2'").fetchone()[0] == "2026-04-26"
        assert official_repo.current_by_app(conn)["amber"]["release_id"] == "rel-2"
        assert conn.execute("SELECT count(*) FROM official_versions WHERE app_id='amber'").fetchone()[0] == 2
        assert apps_repo.get_app(conn, "amber")["doc"]["limitations"] == "已知限制"
        assert conn.execute("SELECT count(*) FROM component_compatibility_reports").fetchone()[0] == 0
        assert not conn.execute("PRAGMA foreign_key_check").fetchall()
    finally:
        conn.close()


def artifact(version, number, digest="1", *, os="ubuntu20.04", arch="amd64"):
    return {"digest": "sha256:" + digest * 64, "tags": [{"name": f"22-{version}-{os}-{arch}-{number}"}]}


def test_image_selection_numeric_cap_and_exact_platform():
    build = delivery_images.builds(app_info_doc("22"))[0]
    choices = [artifact("3.9.0.9", 99), artifact("3.9.0.12", 2, "2"), artifact("3.9.0.12", 10, "3"),
               artifact("3.9.0.30", 1, "4"), artifact("3.8.0.99", 2, "5"),
               artifact("3.9.0.29", 88, "6", arch="arm64"), artifact("3.9.0.29", 88, "7", os="ubuntu24.04")]
    selected = cutover.choose_artifact(build, choices, "3.9.0.29")
    assert selected["hpc_version"] == "3.9.0.12"
    assert selected["digest"] == "sha256:" + "3" * 64
    assert "-10@sha256:" in selected["reference"]
    with pytest.raises(ValueError, match="多个 digest"):
        cutover.choose_artifact(build, [artifact("3.9.0.12", 10, "1"), artifact("3.9.0.12", 10, "2")], "3.9.0.29")


def test_summary_retains_zero_uses_hpc_not_sdk_and_environment_unchanged():
    original = summary("3.10.1.2", label="3.10.0.x")
    rows = cutover.summary_rows(original, "3.10.1")
    assert len(rows) == 1
    assert rows[0]["qa_result"] == 0
    assert rows[0]["source_hpc_version"] == "3.10.1.2"
    assert rows[0]["source_result_header"] == "3.10.0.x"
    result = cutover.qa_workbook(original, [])
    book = load_workbook(io.BytesIO(result))
    assert book.sheetnames == ["QA log", "C500"]
    assert book["C500"]["J2"].value == "=1+2"
    assert book["C500"]["A1"].font.bold
    assert "A1:C1" in book["C500"].merged_cells
    target = Workbook()
    cutover.copy_sheet(book["C500"], target.active)
    buf = io.BytesIO()
    target.save(buf)
    assert load_workbook(io.BytesIO(buf.getvalue())).active["A1"].font.bold


def test_legacy_undefined_metric_is_missing_metadata_not_a_unit():
    book = load_workbook(io.BytesIO(summary("3.9.0.12")))
    book.active["D6"] = "undefined"
    content = io.BytesIO()
    book.save(content)
    row = cutover.summary_rows(content.getvalue(), "3.9.0")[0]
    assert row["source_perf_unit"] == ""
    assert row["qa_result"] == 0


@pytest.mark.parametrize("value,comment,expected", [
    (12.5, "", ""), (0, "性能回退 MC3-123", "fail"), ("功能测试pass", "", "pass"),
    ("", "测试失败", "fail"), ("功能测试pass", "历史失败已修复", ""),
])
def test_verdict_does_not_invent_pass(value, comment, expected):
    assert cutover.status_for(value, comment) == expected


def test_full_cutover_offline_proves_qa_upload_and_delivery_consistency(tmp_path, monkeypatch):
    source, output = tmp_path / "source.db", tmp_path / "out.db"
    legacy(source)
    raw = app_info_doc("22")
    raw["app_test"]["sanity"].update({"perf_golden": "max", "perf_unit": "s", "container_type": "docker"})
    raw["app_build"]["disabled"] = {**raw["app_build"]["ubuntu20.04_amd64"], "os": "ubuntu24.04", "enabled": False}
    with sqlite3.connect(source) as conn:
        conn.execute("DELETE FROM snapshots WHERE app_id<>'amber'")
        conn.execute("DELETE FROM apps WHERE id<>'amber'")
        for release, data in conn.execute("SELECT release_id,data_json FROM snapshots").fetchall():
            snapshot = json.loads(data)
            snapshot["app_info"]["parsed"]["raw"] = raw
            conn.execute("UPDATE snapshots SET data_json=? WHERE release_id=?", (json.dumps(snapshot), release))
    objects = {}
    for family, hpc, char in [("3.9.0", "3.9.0.12", "1"), ("3.10.0", "3.10.0.2", "2")]:
        item = artifact(hpc, 1, char)
        item["tags"].append({"name": "22-cccccccc-ubuntu20.04-amd64-1"})
        objects[family] = item
    monkeypatch.setattr(harbor, "list_artifacts", lambda project, repo, family: [objects[family]])
    monkeypatch.setattr(harbor, "get_artifact", lambda p, r, d: next(v for v in objects.values() if v["digest"] == d))
    monkeypatch.setattr(gerrit, "fetch_app_info_at", lambda *a, **kw: json.dumps(raw))
    monkeypatch.setattr(gerrit, "branch_heads", lambda *a, **kw: {})
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    args = argparse.Namespace(source=source, output=output, cap=["3.9.0=3.9.0.29", "3.10.0=3.10.0.11"], overrides=None)
    assert cutover.run(args) == 0
    assert hashlib.sha256(source.read_bytes()).hexdigest() == before
    conn = connect(output)
    try:
        assert conn.execute("SELECT count(*) FROM qa_test_results").fetchone()[0] == 2
        for r in conn.execute("SELECT data_json FROM qa_test_results"):
            row = json.loads(r[0])
            assert row["qa_result"] == 0 and row["qa_status"] == "fail"
            assert row["qa_digest_gerrit_commit_id"] == "cccccccc"
            assert row["qa_docker_cmd"] == ""
        for r in conn.execute("SELECT e.deliverable_images_json,o.images_json,o.test_manifest_json "
                              "FROM entries e JOIN official_versions o ON o.entry_id=e.id"):
            assert json.loads(r[0]) == json.loads(r[1]) and len(json.loads(r[0])) == 1
            assert not r[2]  # reconstructed historical record, not falsely sealed
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    finally:
        conn.close()
    assert {p.name for p in output.with_suffix("").iterdir()} == {"images.csv", "qa_log.xlsx"}
    with pytest.raises(ValueError, match="输出已存在"):
        cutover.run(args)
    args.output = source
    with pytest.raises(ValueError, match="不能是源"):
        cutover.run(args)
    args.output = tmp_path / "failed.db"
    def network_failure(*args):
        raise RuntimeError("Harbor unavailable")
    monkeypatch.setattr(harbor, "list_artifacts", network_failure)
    with pytest.raises(RuntimeError, match="Harbor unavailable"):
        cutover.run(args)
    assert not args.output.exists() and not args.output.with_suffix("").exists()
    assert hashlib.sha256(source.read_bytes()).hexdigest() == before


def test_ambiguous_builds_and_wrong_branch_are_not_assigned_to_results(monkeypatch):
    row = {"app_id": "app", "git_branch": "maca", "hpc_version": "3.9.0.12", "app_version": "1",
           "arch": "x86", "OS": "ubuntu22.04", "GPU": "C500", "py_label": "", "torch_label": "",
           "test_name": "sanity", "perf_golden": "max", "perf_unit": "s"}
    other = {**row, "py_label": "py312"}
    monkeypatch.setattr(cutover.qa_results, "worksheet_commands", lambda *a, **kw: [row, other])
    job = {"release": "3.9.0", "purpose": "qa", "source": {"test_commands": [], "image_app_name": "amber", "commit_id": "abc"},
           "selected": {"hpc_version": "3.9.0.12", "reference": "ref"}, "build": {"image_pattern": "pattern"},
           "entry": {"excluded_tests": []}, "cap": "3.9.0.12"}
    records = cutover.summary_rows(summary("3.9.0.12"), "3.9.0")
    records.append({**records[0], "source_row": 7, "source_branch": "other"})
    issues = []
    result = cutover.match_results([job], {"3.9.0": records}, {"3.9.0": {"filename": "original.xlsx"}}, issues, {})
    assert result == {"3.9.0": []}
    assert [i["kind"] for i in issues] == ["ambiguous_result", "unmatched_result"]


@pytest.mark.parametrize("platforms,expected", [
    ([("x86", "kylinv10"), ("x86", "ubuntu22.04"), ("x86", "ubuntu24.04")], ["ubuntu22.04"]),
    ([("amd64", "kylinv10"), ("amd64", "ubuntu24.04")], ["ubuntu24.04"]),
    ([("arm", "kylinv10"), ("arm", "ubuntu24.04")], ["kylinv10", "ubuntu24.04"]),
    ([("x86", "kylinv10")], ["kylinv10"]),
    ([("x86", "ubuntu20.04"), ("x86", "ubuntu24.04")], ["ubuntu20.04", "ubuntu24.04"]),
])
def test_build_preferences_only_resolve_authorized_platform_ambiguity(platforms, expected):
    candidates = [{"arch": arch, "OS": os} for arch, os in platforms]
    assert [row["OS"] for row in cutover.prefer_builds(candidates)] == expected


def matching_job(app_id="app", version="1", *, branch="maca", image_name="amber", entry=True, os="ubuntu22.04", py=""):
    row = {"app_id": app_id, "git_branch": branch, "hpc_version": "3.9.0.12", "app_version": version,
           "arch": "x86", "OS": os, "GPU": "C500", "py_label": py, "torch_label": "",
           "test_name": "sanity", "perf_golden": "max", "perf_unit": "s"}
    return {"release": "3.9.0", "purpose": "qa", "source": {"test_commands": [], "image_app_name": image_name,
            "commit_id": "abc", "fixture_rows": [row]}, "selected": {"hpc_version": "3.9.0.12",
            "reference": app_id + os + py}, "build": {"image_pattern": "pattern"},
            "entry": {"excluded_tests": []} if entry else None, "cap": "3.9.0.12"}


def match_jobs(monkeypatch, jobs, records):
    monkeypatch.setattr(cutover.qa_results, "worksheet_commands", lambda src, **kw: src["fixture_rows"])
    issues, skipped = [], []
    rows = cutover.match_results(jobs, {"3.9.0": records}, {"3.9.0": {"filename": "original.xlsx"}}, issues, {}, skipped)
    return rows["3.9.0"], issues, skipped


def test_matching_resolves_platform_prefills_failed_and_tracks_gpu_skip(monkeypatch):
    records = cutover.summary_rows(summary("3.9.0.12"), "3.9.0")
    records[0].update(qa_result="/", qa_comment="原始备注")
    records.append({**records[0], "source_row": 7, "GPU": "C500X"})
    jobs = [matching_job(os=os) for os in ("kylinv10", "ubuntu22.04", "ubuntu24.04")]
    rows, issues, skipped = match_jobs(monkeypatch, jobs, records)
    assert not issues and len(rows) == 1
    assert rows[0]["OS"] == "ubuntu22.04"
    assert (rows[0]["qa_result"], rows[0]["qa_status"], rows[0]["source_qa_result"]) == ("failed", "fail", "/")
    assert rows[0]["qa_comment"] == "原始备注"
    assert [(r["kind"], r["source_row"]) for r in skipped] == [("gpu_mismatch", 7)]


def test_empty_result_with_python_ambiguity_is_not_invented(monkeypatch):
    records = cutover.summary_rows(summary("3.9.0.12"), "3.9.0")
    records[0]["qa_result"] = ""
    rows, issues, skipped = match_jobs(monkeypatch, [matching_job(py="py310"), matching_job(py="py312")], records)
    assert not rows and not skipped
    assert issues[0]["kind"] == "ambiguous_result"


def test_ubuntu_preference_does_not_choose_between_app_versions(monkeypatch):
    records = cutover.summary_rows(summary("3.9.0.12"), "3.9.0")
    jobs = [matching_job("old-app", "1"), matching_job("new-app", "2", os="ubuntu24.04")]
    rows, issues, skipped = match_jobs(monkeypatch, jobs, records)
    assert not rows and not skipped
    assert issues[0]["kind"] == "ambiguous_result"


def test_lammps_versions_keep_separate_app_ids_and_their_own_images(monkeypatch):
    template = cutover.summary_rows(summary("3.9.0.12"), "3.9.0")[0]
    versions = ("patch_4May2022", "stable_22Jul2025")
    records = [{**template, "source_row": i + 6, "source_case": "lammps_sanity", "source_branch": version}
               for i, version in enumerate(versions)]
    jobs = [matching_job(app_id, version, branch="master", image_name="lammps", entry=i == 1)
            for i, (app_id, version) in enumerate(zip(("old-app", "new-app"), versions))]
    rows, issues, skipped = match_jobs(monkeypatch, jobs, records)
    assert not issues and not skipped
    assert [(r["app_id"], r["git_branch"], r["app_version"]) for r in rows] == [
        ("old-app", "master", versions[0]), ("new-app", "master", versions[1])]
    assert rows[0]["qa_digest"].startswith("old-app") and rows[1]["qa_digest"].startswith("new-app")
    rows, issues, skipped = match_jobs(monkeypatch, jobs[1:], records)
    assert [r["app_id"] for r in rows] == ["new-app"]
    assert issues[0]["kind"] == "unmatched_result" and versions[0] in issues[0]["message"]
    assert not skipped


def test_unregistered_stopped_app_is_planned_only_for_historical_qa(tmp_path):
    path = tmp_path / "source.db"
    legacy(path)
    conn, releases, summaries, _ = cutover.migrate(path, {"3.9.0": "3.9.0.29"})
    try:
        apps_repo.insert_app(conn, {"id": "old-lammps", "git_url": "APP/lammps/old.xml",
                                   "git_branch": "master", "cicd_status": "stopped"})
        raw = app_info_doc("patch_4May2022")
        raw["app_name"] = "lammps"
        app_info_repo.insert_version(conn, app_id="old-lammps", commit_id="old-commit", content_hash="old-hash",
            raw_json=json.dumps(raw), source="fixture", fetched_at="2026-01-01", fetched_by="test")
        book = load_workbook(io.BytesIO(summaries["3.9.0"]["content"]))
        book.active["A6"] = "lammps_sanity"
        book.active["B6"] = "patch_4May2022"
        content = io.BytesIO(); book.save(content); book.close()
        summaries["3.9.0"]["content"] = content.getvalue()
        issues = []
        jobs, _ = cutover.plan_images(conn, releases, summaries, {"3.9.0": "3.9.0.29"}, issues)
        historical = [j for j in jobs if j["app"]["id"] == "old-lammps"]
        assert historical and all(j["purpose"] == "qa" and j["entry"] is None for j in historical)
        assert all(j["cap"] == "3.9.0.12" for j in historical)
        assert any(j["purpose"] == "delivery" and j["app"]["id"] == "amber" for j in jobs)
        # A normal current-app test must not pull in unrelated historical Apps.
        summaries["3.9.0"]["content"] = summary("3.9.0.12")
        jobs, _ = cutover.plan_images(conn, releases, summaries, {"3.9.0": "3.9.0.29"}, [])
        assert all(j["app"]["id"] != "old-lammps" for j in jobs)
    finally:
        conn.close()
