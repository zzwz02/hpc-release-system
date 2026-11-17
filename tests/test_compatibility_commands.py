"""Compatibility commands use QA-log images; report verdicts remain separate evidence."""
from copy import deepcopy
import json

import pytest

from app.api.errors import ApiError
from app.domain import qa_results
from app.integrations import gerrit, harbor
from app.repositories import official_repo, qa_results_repo
from app.services import cycle_service, lookup_service, qa_service
from tests.conftest import close_qa, make_dependency_app_info, seed_app, seed_cycle, seed_signup, store_app_info


@pytest.fixture()
def published(temp_db, harbor_artifacts, monkeypatch):
    raw = make_dependency_app_info("matrix")
    raw["app_build"]["ubuntu20.04_amd64"]["supported_chip"].append("C500X")
    raw["app_test"]["sanity"]["supported_chip"]["C500X"] = ["ubuntu20.04_amd64"]
    raw["app_test"]["sanity"]["test_cmd"] = "printf '%s\\n' 'preserve quoted script'"
    monkeypatch.setattr(gerrit, "fetch_app_info_at", lambda *_a, **_k: json.dumps(raw))
    seed_app(temp_db, "matrix", official_name="Matrix", git_branch="maca", app_info=raw)
    release = seed_cycle(temp_db, name="3.10.0")
    entry = seed_signup(temp_db, release, "matrix")
    images = [{**build, "reference": f"harbor.sh.mxcr.io/hpc-release/{build['image_pattern'].replace('<hpc_version>', '3.10.0.7')}@sha256:{digit * 64}"}
              for build, digit in zip(entry["delivery_builds"], "abcdef")]
    harbor_artifacts(images)
    original = harbor.get_artifact
    def artifact(*args):
        value = original(*args)
        value["tags"] += [{"name": tag["name"].replace("abcdef1", "3.10.0.7")} for tag in list(value["tags"])]
        return value
    monkeypatch.setattr(harbor, "get_artifact", artifact)
    qa_service.set_qa_status_batch(temp_db, release, [{"app_id": "matrix", "status": "qa_passed",
        "deliverable_images": images, "delivery_revision": entry["delivery_revision"]}], user="qa", role="QA")
    close_qa(temp_db, release)
    cycle_service.publish(temp_db, release, user="rm", role="RM")
    return release


def report_row(row, **changes):
    return {"app_id": row["app_id"], "git_branch": row["git_branch"], "app_version": row["app_version"],
            "hpc_version": "3.10.0.7", "arch": row["arch"], "OS": row["os"], "GPU": row["GPU"],
            "py_label": row["python_label"], "torch_label": row["pytorch_label"], "test_name": row["path"],
            "qa_digest": "harbor.sh.mxcr.io/hpc-release/matrix-maca@sha256:" + "f" * 64,
            "qa_docker_cmd": "QA uploaded text must never replace the published command", "qa_result": 0,
            "qa_status": "fail", "qa_comment": "性能回退 8%", **changes}


def save_report(conn, release, rows):
    qa_results_repo.save(conn, release_id=release, filename="QA.xlsx", content=b"stored workbook",
        environments=["server"], uploaded_at="2026-11-15 23:00:00", uploaded_by="qa",
        rows=[(qa_results.worksheet_result_key(row) if release else qa_results.result_key(row), row, row["qa_digest"]) for row in rows])
    conn.commit()


def target(conn):
    return next(row for row in lookup_service.latest_running_releases(conn)["rows"]
                if row.get("GPU") == "C500X" and row.get("image_target") == "release")


def test_cartesian_rows_keep_exact_platform_test_relationships_and_frozen_images(temp_db, published, monkeypatch):
    rows = lookup_service.latest_running_releases(temp_db)["rows"]
    commands = [row for row in rows if row.get("command")]
    assert len(commands) == 7
    assert {(row["os"], row["arch"], row["GPU"], row["image_target"]) for row in commands} == {
        ("ubuntu20.04", "amd64", gpu, target) for gpu in ("C500", "C500X") for target in ("release", "dbg")
    } | {("ubuntu20.04", "arm64", "C500", "release"), ("ubuntu22.04", "amd64", "C600", "release"),
         ("ubuntu24.04", "amd64", "C588", "release")}
    assert all(row["chips"] == [row["GPU"]] for row in commands)
    assert all(":1.0-3.10.0.7-" in row["delivery_reference"] for row in commands)
    assert all(row["delivery_reference"] in row["command"] for row in commands)
    assert all("preserve quoted script" in row["command"] for row in commands)
    assert any(row["os"] == "centos9" and not row.get("command") for row in rows)


def test_report_versions_verdicts_and_reasons_never_replace_qa_log_images(temp_db, published):
    original = target(temp_db)
    save_report(temp_db, published, [report_row(original), report_row(original, hpc_version="3.10.0.8", qa_status="pass", qa_comment="回归通过")])
    row = target(temp_db)
    assert row["command"] == original["command"]
    assert row["delivery_reference"] == original["delivery_reference"]
    assert "3.10.0.7 · fail · 原因：性能回退 8%" in row["note"]
    assert "3.10.0.8 · pass；note: 测试镜像与 QA report 交付镜像不同，仅作历史参考" in row["note"]
    assert "回归通过" not in row["note"]
    assert "QA uploaded text" not in row["command"]
    other_gpu = next(row for row in lookup_service.latest_running_releases(temp_db)["rows"]
                     if row.get("GPU") == "C500" and row.get("arch") == "amd64" and row.get("os") == "ubuntu20.04")
    assert "性能回退" not in other_gpu["note"]
    assert "无匹配测试结果" in other_gpu["note"]


@pytest.mark.parametrize("change", [{"app_id": "Other"}, {"git_branch": "other"}, {"app_version": "2"},
    {"arch": "arm64"}, {"OS": "ubuntu24.04"}, {"GPU": "X301"}, {"py_label": "py310"},
    {"torch_label": "torch2.4"}, {"test_name": "other"},
    {"qa_digest": "harbor.sh.mxcr.io/hpc-release/matrix-maca-dbg@sha256:" + "f" * 64}])
def test_other_test_builds_and_branches_do_not_supply_a_verdict(temp_db, published, change):
    save_report(temp_db, published, [report_row(target(temp_db), **change)])
    assert "无匹配测试结果" in target(temp_db)["note"]


def test_other_cycles_are_excluded_and_legacy_reports_can_match_same_cycle(temp_db, published):
    original = target(temp_db)
    other = seed_cycle(temp_db, name="3.9.0")
    save_report(temp_db, other, [report_row(original, hpc_version="3.9.0.12")])
    assert "无匹配测试结果" in target(temp_db)["note"]
    save_report(temp_db, None, [report_row(original)])
    assert "3.10.0.7 · fail" in target(temp_db)["note"]
    # An assigned current report takes precedence over a legacy duplicate.
    save_report(temp_db, published, [report_row(original, qa_status="pass", qa_comment="current report")])
    note = target(temp_db)["note"]
    assert "3.10.0.7 · pass" in note and "性能回退" not in note
    assert "原因：" not in note and "current report" not in note


def strip_saved_tag(conn):
    official = official_repo.current_by_app(conn)["matrix"]
    manifest = deepcopy(official["test_manifest"])
    image = next(image for image in manifest["images"] if image["os"] == "ubuntu20.04" and image["arch"] == "amd64" and image["image_target"] == "release")
    old = image["reference"]
    bare = old.split(":1.0-", 1)[0] + "@" + image["digest"]
    image.update(reference=bare, image=bare.split("@")[0])
    for command in manifest["commands"]:
        if command["delivery_build_key"] == image["build_key"]:
            command.update(image=bare, delivery_reference=bare, command=command["command"].replace(old, bare))
    conn.execute("UPDATE official_versions SET test_manifest_json=? WHERE id=?", (json.dumps(manifest), official["id"]))
    conn.commit()
    return image, manifest


def test_legacy_tag_lookup_is_deduplicated_and_uses_actual_matching_tags_without_writes(temp_db, published, monkeypatch):
    image, frozen = strip_saved_tag(temp_db)
    base = image["image_pattern"].partition(":")[2].replace("<hpc_version>", "3.10.0.8")
    calls = []
    def artifact(*args):
        calls.append(args)
        return {"tags": [{"name": name} for name in [base + "-90", base, base.replace("3.10.0.8", "abcdef1"),
            base.replace("ubuntu20.04", "ubuntu24.04").replace("3.10.0.8", "3.10.0.20")]]}
    monkeypatch.setattr(harbor, "get_artifact", artifact)
    before = temp_db.total_changes
    rows = [row for row in lookup_service.latest_running_releases(temp_db)["rows"] if row.get("delivery_build_key") == image["build_key"]]
    assert len(rows) == 2
    assert len(calls) == len(set(calls))
    assert all(f':{base}@' in row["delivery_reference"] and row["delivery_reference"] in row["command"] for row in rows)
    assert official_repo.current_by_app(temp_db)["matrix"]["test_manifest"] == frozen
    assert temp_db.total_changes == before


@pytest.mark.parametrize("failure", [ApiError(502, "Harbor unavailable"), ValueError("artifact missing")])
def test_legacy_lookup_failure_is_explicit_and_keeps_delivery_digest(temp_db, published, monkeypatch, failure):
    image, _ = strip_saved_tag(temp_db)
    def artifact(*_):
        raise failure
    monkeypatch.setattr(harbor, "get_artifact", artifact)
    row = next(row for row in lookup_service.latest_running_releases(temp_db)["rows"]
               if row.get("delivery_build_key") == image["build_key"])
    assert "交付镜像解析失败" in row["note"] and str(failure) in row["note"]
    assert row["delivery_reference"] == image["reference"]
    assert row["command"] == "" and row["app_info_commit_id"] == ""


def test_each_delivery_uses_its_own_commit_dependencies_and_test_scope(temp_db, published, monkeypatch):
    original = harbor.get_artifact
    arm_digest = next(image["digest"] for image in official_repo.current_by_app(temp_db)["matrix"]["images"]
                      if image["arch"] == "arm64")
    def artifact(*args):
        value = original(*args)
        commit = "2222222" if args[-1] == arm_digest else "1111111"
        value["tags"] = [{"name": tag["name"].replace("abcdef1", commit)} for tag in value["tags"]]
        return value
    calls = []
    def fetch(url, commit, **_):
        calls.append((url, commit))
        raw = make_dependency_app_info("matrix")
        raw["app_dependency"] = ["sdk-arm" if commit == "2222222" else "sdk-x86"]
        raw["app_test"] = {"arm_case" if commit == "2222222" else "x86_case": {
            **raw["app_test"]["sanity"], "test_cmd": "run-" + commit,
        }}
        return json.dumps(raw)
    monkeypatch.setattr(harbor, "get_artifact", artifact)
    monkeypatch.setattr(gerrit, "fetch_app_info_at", fetch)
    frozen = deepcopy(official_repo.current_by_app(temp_db)["matrix"]["test_manifest"])
    before = temp_db.total_changes
    rows = lookup_service.latest_running_releases(temp_db)["rows"]
    arm = [row for row in rows if row.get("arch") == "arm64"]
    x86 = [row for row in rows if row.get("arch") == "amd64" and row.get("command")]
    assert arm and x86
    assert all(row["app_info_commit_id"] == "2222222" and row["path"] == "arm_case"
               and "run-2222222" in row["command"] and "sdk-arm" in row["dependencies"] for row in arm)
    assert all(row["app_info_commit_id"] == "1111111" and row["path"] == "x86_case"
               and "run-1111111" in row["command"] and "sdk-x86" in row["dependencies"] for row in x86)
    assert all(row["delivery_reference"] in row["command"] for row in [*arm, *x86])
    assert len(calls) == 2  # Shared commits are read only once within the public batch.
    assert temp_db.total_changes == before
    assert official_repo.current_by_app(temp_db)["matrix"]["test_manifest"] == frozen


def test_conflicting_commits_fail_only_the_affected_delivery_without_snapshot_fallback(temp_db, published, monkeypatch):
    original = harbor.get_artifact
    broken = official_repo.current_by_app(temp_db)["matrix"]["images"][0]
    def artifact(*args):
        value = original(*args)
        if args[-1] == broken["digest"]:
            value["tags"].append({"name": broken["image_pattern"].partition(":")[2].replace("<hpc_version>", "1234567")})
        return value
    monkeypatch.setattr(harbor, "get_artifact", artifact)
    rows = lookup_service.latest_running_releases(temp_db)["rows"]
    failed = [row for row in rows if row.get("delivery_build_key") == broken["build_key"]]
    assert len(failed) == 1
    assert "多个不同 commit" in failed[0]["note"]
    assert failed[0]["command"] == "" and failed[0]["app_info_commit_id"] == ""
    assert failed[0]["delivery_reference"] == broken["reference"]
    assert failed[0]["scope_pending"]
    assert any(row.get("command") for row in rows)


@pytest.mark.parametrize("enabled", [False, "false"])
def test_current_disabled_builds_are_not_queried_or_collected(temp_db, published, monkeypatch, enabled):
    changed = make_dependency_app_info("matrix")
    changed["app_build"]["ubuntu22.04_amd64"]["enabled"] = enabled
    store_app_info(temp_db, "matrix", changed, commit_id="current-disable")
    disabled_digest = next(image["digest"] for image in official_repo.current_by_app(temp_db)["matrix"]["images"]
                           if image["os"] == "ubuntu22.04")
    original = harbor.get_artifact
    def artifact(*args):
        assert args[-1] != disabled_digest
        return original(*args)
    monkeypatch.setattr(harbor, "get_artifact", artifact)
    rows = lookup_service.latest_running_releases(temp_db)["rows"]
    assert rows and all(row.get("os") != "ubuntu22.04" for row in rows)
    assert all("torch 2.4 + py310" not in row["dependencies"] for row in rows)


def test_traceable_legacy_image_platform_is_read_from_its_own_tags(temp_db, published):
    image = next(image for image in official_repo.current_by_app(temp_db)["matrix"]["images"]
                 if image["os"] == "ubuntu20.04" and image["arch"] == "amd64" and image["image_target"] == "release")
    legacy = {**image, "build_key": "legacy", "build": "", "os": "", "arch": "", "python_label": "", "pytorch_label": "", "image_target": ""}
    official = official_repo.current_by_app(temp_db)["matrix"]
    manifest = deepcopy(official["test_manifest"])
    manifest["images"] = [legacy]
    manifest["commands"] = []
    temp_db.execute("UPDATE official_versions SET test_manifest_json=? WHERE id=?", (json.dumps(manifest), official["id"]))
    temp_db.commit()
    rows = lookup_service.latest_running_releases(temp_db)["rows"]
    assert rows and all(row["os"] == image["os"] and row["arch"] == image["arch"] for row in rows)
    assert all(row["app_info_commit_id"] == "abcdef1" and row["command"] for row in rows)
