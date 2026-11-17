"""Batch live digest lookup, seven-field Excel validation, and latest QA results."""

from __future__ import annotations

import base64
import io
import json
from copy import deepcopy

import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook

from app.api.errors import ApiError
from app.deps import get_db, require_login
from app.domain import qa_results
from app.integrations import gerrit, harbor
from app.main import create_app
from app.services import lookup_service, qa_results_service
from tests.conftest import seed_app, seed_cycle
from tests.test_image_lookup import DIGEST, IMAGE, TAGS, historical_info


@pytest.fixture()
def live(temp_db, monkeypatch):
    raw = historical_info()
    # A second chip is declared for sanity only: never expand the other tests to it.
    raw["app_build"]["ubuntu22.04_amd64"]["supported_chip"] = ["C500", "X201"]
    raw["app_test"]["sanity"]["supported_chip"]["X201"] = ["ubuntu22.04_amd64"]
    seed_app(
        temp_db, git_url="hpc_actual_repo", git_branch="maca", app_info=raw, commit_id="bb46c60"
    )
    calls = []

    def artifact(project, repository, digest):
        calls.append(("harbor", repository, digest))
        return {
            "digest": digest,
            "tags": [{"name": tag} for tag in TAGS],
            "extra_attrs": {"architecture": "arm64", "os": "wrong-os"},
        }

    def fetch(url, commit, **kwargs):
        assert not temp_db.in_transaction
        calls.append(("gerrit", url, commit))
        return json.dumps(raw)

    monkeypatch.setattr(harbor, "get_artifact", artifact)
    monkeypatch.setattr(gerrit, "fetch_app_info_at", fetch)
    monkeypatch.setattr(gerrit, "branch_heads", lambda *args, **kwargs: {})
    return raw, calls


def template(temp_db):
    row = deepcopy(lookup_service.batch_images(temp_db, [IMAGE])["rows"][0])
    return {**row, "test_result": 100, "test_digest": DIGEST, "test_env": "C500_server_1"}


def workbook(rows, *, environment="C500_server_1"):
    book = Workbook()
    sheet = book.active
    sheet.title = "测试结果详表"
    columns = list(dict.fromkeys([*qa_results.COLUMNS, *(key for row in rows for key in row)]))
    sheet.append(columns)
    for row in rows:
        sheet.append([row.get(column, "") for column in columns])
    env = book.create_sheet(environment)
    env.merge_cells("A1:D3")
    env["A1"] = "原样保留，不解析的环境文档"
    env["C5"] = "=1+2"
    env["B9"] = 0
    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()


def report_release(conn, name="3.10.1"):
    row = conn.execute("SELECT id FROM releases WHERE name=?", (name,)).fetchone()
    return row[0] if row else seed_cycle(conn, name=name)


def upload(conn, rows, content=None, **kwargs):
    data = content or workbook(rows)
    return qa_results_service.upload(
        conn,
        filename="QA 测试结果.xlsx",
        release_id=kwargs.pop("release_id", None) or report_release(conn),
        content_b64=base64.b64encode(data).decode(),
        user="qa",
        role="QA",
        **kwargs,
    )


def test_batch_deduplicates_network_work_and_expands_only_declared_gpus(temp_db, live):
    _, calls = live
    other = IMAGE.replace("8d" * 32, "9d" * 32)
    batch = lookup_service.batch_images(
        temp_db, [IMAGE, "docker pull " + IMAGE, other, "bad-image"]
    )
    assert batch["duplicate_count"] == 1
    assert len([call for call in calls if call[0] == "harbor"]) == 2
    assert len([call for call in calls if call[0] == "gerrit"]) == 1
    assert len(batch["errors"]) == 1
    assert len(batch["rows"]) == 6
    for row in batch["rows"]:
        assert row["arch"] == "x86" and row["OS"] == "ubuntu22.04"
        assert row["GPU"] in ("C500", "X201")
        assert "@sha256:" in row["docker_cmd"]
        assert "<hpc_version>" not in row["docker_cmd"]
        assert all(row[column] == "" for column in qa_results.RESULT_COLUMNS)
    assert [(r["test_name"], r["GPU"]) for r in batch["rows"][:3]] == [
        ("sanity", "C500"),
        ("sanity", "X201"),
        ("nested.daily", "C500"),
    ]
    assert batch["columns"] == qa_results.COLUMNS


@pytest.mark.parametrize("value", [0, 100, 0.01, "pass", "fail", "PASS", "中文结果"])
def test_import_preserves_result_type_env_workbook_and_resolves_provenance(temp_db, live, value):
    row = template(temp_db)
    row.update(
        test_result=value,
        commit_id="forged-commit",
        hpc_version="3.10.1.2",
        docker_cmd=row["docker_cmd"] + " # QA edited",
    )
    content = workbook([row])
    result = upload(temp_db, [row], content=content)
    assert result["rows"] == 1 and result["replaced"] == 0
    assert qa_results_service.download(temp_db, result["report_id"]) == (
        content,
        "QA 测试结果.xlsx",
    )
    stored = qa_results_service.query(temp_db)["rows"][0]
    assert stored["qa_result"] == value and type(stored["qa_result"]) is type(value)
    assert stored["qa_digest_gerrit_commit_id"] == "bb46c60"
    assert stored["hpc_version"] == "3.10.1.2"
    assert stored["qa_env"] == "C500_server_1"
    assert stored["qa_docker_cmd"].endswith("# QA edited")
    assert not stored["website_docker_cmd"].endswith("# QA edited")
    assert stored["uploaded_at"] == result["uploaded_at"]
    assert stored["uploaded_by"] == "qa"
    history = lookup_service.batch_images(temp_db, [IMAGE])["rows"]
    assert history[0]["test_result"] == value
    assert history[0]["test_digest"] == IMAGE
    assert history[0]["test_env"] == "C500_server_1"
    assert history[1]["test_result"] == ""  # other chip remains untested


@pytest.mark.parametrize(
    "column,value",
    [
        ("app_id", "different-app"),
        ("git_branch", "other-branch"),
        ("app_version", "9.0"),
        ("arch", "arm"),
        ("OS", "ubuntu24.04"),
        ("GPU", "C500X"),
        ("test_name", "not-a-test"),
    ],
)
def test_every_identity_dimension_is_validated_atomically(temp_db, live, column, value):
    row = template(temp_db)
    upload(temp_db, [row])
    original = qa_results_service.query(temp_db)
    bad = {**row, column: value, "test_result": "fail"}
    with pytest.raises(ValueError, match="第 3 行.*不匹配"):
        upload(temp_db, [row, bad])
    assert qa_results_service.query(temp_db) == original
    assert temp_db.execute("SELECT COUNT(*) FROM qa_test_reports").fetchone()[0] == 1


@pytest.mark.parametrize(
    "failure", [ValueError("Harbor 中未找到该镜像"), ApiError(502, "Harbor 查询失败")]
)
def test_missing_image_and_network_failure_never_save(temp_db, live, monkeypatch, failure):
    row = template(temp_db)

    def fail(*args):
        raise failure

    monkeypatch.setattr(harbor, "get_artifact", fail)
    with pytest.raises(ValueError, match="第 2 行.*Harbor"):
        upload(temp_db, [row])
    assert temp_db.execute("SELECT COUNT(*) FROM qa_test_reports").fetchone()[0] == 0
    assert temp_db.execute("SELECT COUNT(*) FROM qa_test_results").fetchone()[0] == 0


def test_bad_digest_and_unknown_environment_are_readable_row_errors(temp_db, live):
    row = template(temp_db)
    for bad, message in [
        ({"test_digest": "123"}, "第 2 行.*digest"),
        ({"test_env": "missing"}, "第 2 行.*环境页签"),
        ({"test_digest": ""}, "第 2 行.*qa_digest"),
    ]:
        with pytest.raises(ValueError, match=message):
            upload(temp_db, [{**row, **bad}])


def test_history_ignores_commit_digest_and_env_and_new_upload_overwrites(temp_db, live):
    raw, calls = live
    row = template(temp_db)
    first = upload(temp_db, [row])
    raw["app_test"]["sanity"]["test_cmd"] = "new live command"
    other = IMAGE.replace("8d" * 32, "9d" * 32)
    current = lookup_service.batch_images(temp_db, [other])["rows"][0]
    assert current["test_result"] == 100 and current["test_digest"] == IMAGE
    assert other in current["docker_cmd"] and "new live command" in current["docker_cmd"]
    row.update(test_result="fail", test_digest=other, docker_cmd=current["docker_cmd"])
    second = upload(temp_db, [row])
    assert second["replaced"] == 1
    assert temp_db.execute("SELECT COUNT(*) FROM qa_test_results").fetchone()[0] == 1
    stored = qa_results_service.query(temp_db)["rows"][0]
    assert stored["report_id"] == second["report_id"]
    assert stored["qa_result"] == "fail"
    with pytest.raises(ApiError, match="测试报告不存在"):
        qa_results_service.download(temp_db, first["report_id"])
    assert len([c for c in calls if c[0] == "gerrit"]) >= 5  # each request really fetches


def test_last_duplicate_row_wins_and_empty_pending_rows_are_skipped(temp_db, live):
    row = template(temp_db)
    pending = {**row, "test_name": "not-yet-tested", **dict.fromkeys(qa_results.RESULT_COLUMNS, "")}
    result = upload(temp_db, [row, {**row, "test_result": "pass"}, pending])
    assert result["rows"] == 1 and result["duplicate_rows"] == 1
    assert result["total_rows"] == result["rows"] + result["skipped_rows"] + result["duplicate_rows"] == 3
    assert qa_results_service.query(temp_db)["rows"][0]["qa_result"] == "pass"


def test_results_and_original_file_remain_queryable_when_upstream_is_down(
    temp_db, live, monkeypatch
):
    row = template(temp_db)
    result = upload(temp_db, [row])

    def failed(*args):
        raise ApiError(502, "Harbor 查询失败")

    monkeypatch.setattr(harbor, "get_artifact", failed)
    report = qa_results_service.query(temp_db)
    assert report["rows"][0]["qa_result"] == 100
    assert report["rows"][0]["qa_docker_cmd"] == row["docker_cmd"]
    assert report["rows"][0]["website_docker_cmd"] == ""
    assert "Harbor" in report["rows"][0]["command_error"]
    assert qa_results_service.download(temp_db, result["report_id"])[0]


def test_sample_workbook_uploads_with_live_simulated_sources(temp_db, monkeypatch):
    # Build a stable worksheet fixture independent of user-edited local reports.
    book = Workbook()
    sheet = book.active
    columns = qa_results.WORKSHEET_COLUMNS
    sheet.append(columns)
    values = {"app_id": "testapp", "git_branch": "maca-3.9.0.27", "hpc_version": "3.10.1.2", "app_version": "3.9.0.27",
              "arch": "x86", "OS": "ubuntu22.04", "GPU": "C500", "test_name": "dataset1-pw/000_4GaAs",
              "docker_cmd": f"docker run {IMAGE}", "test_result": 100, "test_digest": IMAGE,
              "test_env": "c500_server_1"}
    values.update(qa_results.canonical_result(values))
    if "c500_server_1" not in book.sheetnames:
        book.create_sheet("c500_server_1")
    sheet.append([values.get(column, "") for column in columns])
    buffer = io.BytesIO()
    book.save(buffer)
    sample = buffer.getvalue()
    raw = historical_info()
    cfg = deepcopy(raw["app_test"]["sanity"])
    raw["app_test"] = {"dataset1-pw/000_4GaAs": cfg}
    seed_app(
        temp_db,
        app_info=raw,
        official_name="ABACUS",
        git_branch="maca-3.9.0.27",
        commit_id="bb46c60",
    )
    monkeypatch.setattr(gerrit, "fetch_app_info_at", lambda *a, **k: json.dumps(raw))
    monkeypatch.setattr(
        harbor, "get_artifact", lambda *a: {"tags": [{"name": tag} for tag in TAGS]}
    )
    saved = upload(temp_db, [], content=sample)
    row = qa_results_service.query(temp_db)["rows"][0]
    assert saved["rows"] == 1
    assert row["app_name"] == "ABACUS" and row["qa_result"] == 100
    assert row["qa_env"] == "c500_server_1"
    assert row["qa_digest_gerrit_commit_id"] == "bb46c60" and row["hpc_version"] == "3.10.1.2"
    assert qa_results_service.download(temp_db, saved["report_id"])[0] == sample


@pytest.mark.parametrize(
    "role,read,write,lookup",
    [
        ("QA", 200, 200, 200),
        ("RM", 200, 200, 200),
        ("Owner", 200, 403, 200),
        ("Guest", 200, 403, 403),
        ("SPD", 403, 403, 403),
    ],
)
def test_http_upload_query_download_and_batch_permissions(temp_db, live, role, read, write, lookup):
    row = template(temp_db)
    content = workbook([row])
    release_id = report_release(temp_db)
    app = create_app()
    app.dependency_overrides[get_db] = lambda: temp_db
    app.dependency_overrides[require_login] = lambda: {"username": "reader", "role": role}
    with TestClient(app, raise_server_exceptions=False) as client:
        batch = client.post("/api/lookup/images", json={"images": [IMAGE]})
        assert batch.status_code == lookup, batch.text
        if lookup == 200:
            assert batch.headers["cache-control"] == "no-store"
            assert batch.json()["columns"] == qa_results.COLUMNS
        result = client.post(
            "/api/qa/test-results/upload",
            json={"filename": "结果.xlsx", "content_base64": base64.b64encode(content).decode(), "release_id": release_id},
        )
        assert result.status_code == write, result.text
        report = client.get("/api/qa/test-results", params={"search": "abacus", "release_id": release_id})
        assert report.status_code == read, report.text
        assert client.get("/api/qa/test-results/export", params={"release_id": release_id}).status_code == read
        if write == 200:
            assert report.json()["rows"][0]["qa_result"] == 100
            download = client.get(f"/api/qa/test-results/{result.json()['report_id']}/download")
            assert download.content == content
            assert "filename*=UTF-8''" in download.headers["content-disposition"]
            assert client.get("/api/qa/test-results/9999/download").status_code == 404
        if read == 200:
            assert report.headers["cache-control"] == "no-store"
            assert client.get("/api/qa/test-results", params={"limit": 501}).status_code == 422


def test_new_commit_reuses_result_but_saves_newly_parsed_provenance(temp_db, live, monkeypatch):
    row = template(temp_db)
    upload(temp_db, [row])
    new_commit = "c" * 40
    new_tags = [tag.replace("bb46c60", new_commit) for tag in TAGS]
    monkeypatch.setattr(
        harbor, "get_artifact", lambda *args: {"tags": [{"name": tag} for tag in new_tags]}
    )
    current = lookup_service.batch_images(temp_db, [IMAGE])["rows"][0]
    assert current["commit_id"] == new_commit
    assert current["test_result"] == 100
    upload(temp_db, [{**current, "test_result": "fail"}])
    assert qa_results_service.query(temp_db)["rows"][0]["qa_digest_gerrit_commit_id"] == new_commit


def test_all_matching_hpc_versions_are_saved_without_cross_platform_aliases(
    temp_db, live, monkeypatch
):
    row = template(temp_db)
    tags = TAGS + [
        TAGS[2].replace("3.10.1.2", "3.10.0.7"),
        TAGS[2].replace("3.10.1.2", "9.99.0").replace("ubuntu22.04", "ubuntu24.04"),
    ]
    monkeypatch.setattr(
        harbor, "get_artifact", lambda *args: {"tags": [{"name": tag} for tag in tags]}
    )
    upload(temp_db, [row])
    assert qa_results_service.query(temp_db)["rows"][0]["hpc_version"] == "3.10.1.2"


def test_ambiguous_registered_branch_is_rejected_without_guessing(temp_db, live):
    row = template(temp_db)
    seed_app(
        temp_db,
        "other",
        git_url="hpc_actual_repo",
        git_branch="other",
        app_info=live[0],
        commit_id="bb46c60",
    )
    batch = lookup_service.batch_images(temp_db, [IMAGE])
    assert not batch["rows"] and "git_branch" in batch["errors"][0]["error"]
    with pytest.raises(ValueError, match="git_branch 无法唯一确定"):
        upload(temp_db, [row])


@pytest.mark.parametrize("heads,expected", [
    ({"maca": "bb46c60" + "a" * 33}, "maca"),
    ({"actual-history": "bb46c60" + "a" * 33}, ""),
    ({"first": "bb46c60" + "a" * 33, "second": "bb46c60" + "a" * 33}, ""),
    ({"first": "different-head"}, ""),
])
def test_ambiguous_catalog_branch_can_only_use_unique_exact_gerrit_head(temp_db, live, monkeypatch, heads, expected):
    seed_app(temp_db, "other", official_name="Other official name", git_url="hpc_actual_repo", git_branch="other", app_info=live[0], commit_id="bb46c60")
    calls = []

    def branches(url, **kwargs):
        calls.append(url)
        return heads

    monkeypatch.setattr(gerrit, "branch_heads", branches)
    # Both artifacts share a repository: one branch read per request.
    batch = lookup_service.batch_images(temp_db, [IMAGE, IMAGE.replace("8d" * 32, "9d" * 32)])
    assert len(calls) == 1
    if not expected:
        assert not batch["rows"] and all("git_branch" in error["error"] for error in batch["errors"])
        return
    assert batch["errors"] == []
    assert all(row["git_branch"] == expected for row in batch["rows"])
    row = {**batch["rows"][0], "test_result": "pass", "test_digest": IMAGE, "test_env": "C500_server_1"}
    upload(temp_db, [row])
    assert qa_results_service.query(temp_db)["rows"][0]["git_branch"] == expected


def test_bare_digest_uses_docker_image_operand_not_environment_or_shell_text(temp_db, live):
    row = template(temp_db)
    row["docker_cmd"] = row["docker_cmd"].replace(
        "docker run", "docker run -e NOTE=harbor.sh.mxcr.io/hpc-release/other@sha256:" + "a" * 64
    )
    row["test_digest"] = "8d" * 32
    upload(temp_db, [row])
    assert IMAGE.replace("@", ":" + TAGS[2] + "@") in qa_results_service.query(temp_db)["rows"][0]["website_docker_cmd"]


def test_different_build_variants_share_only_the_prescribed_seven_field_key(temp_db, live):
    row = template(temp_db)
    upload(temp_db, [row])
    altered = {
        **row,
        "Python label": "other",
        "PyTorch label": "other",
        "commit_id": "other",
        "test_digest": IMAGE.replace("8d" * 32, "9d" * 32),
        "test_env": "C500_server_2",
        "docker_cmd": row["docker_cmd"].replace("8d" * 32, "9d" * 32),
    }
    assert qa_results.result_key(altered) == qa_results.result_key(row)
    altered["Python label"] = row.get("Python label", "")
    altered["PyTorch label"] = row.get("PyTorch label", "")
    result = upload(temp_db, [altered], content=workbook([altered], environment="C500_server_2"))
    assert result["replaced"] == 1
    assert qa_results_service.query(temp_db)["rows"][0]["qa_env"] == "C500_server_2"


def test_storage_failure_rolls_back_original_workbook_and_results(temp_db, live):
    row = template(temp_db)
    temp_db.execute(
        "CREATE TRIGGER deny_qa_result BEFORE INSERT ON qa_test_results "
        "BEGIN SELECT RAISE(ABORT, 'blocked'); END"
    )
    temp_db.commit()
    import sqlite3

    with pytest.raises(sqlite3.IntegrityError, match="blocked"):
        upload(temp_db, [row])
    assert temp_db.execute("SELECT COUNT(*) FROM qa_test_reports").fetchone()[0] == 0
    assert temp_db.execute("SELECT COUNT(*) FROM qa_test_results").fetchone()[0] == 0
    assert (
        temp_db.execute(
            "SELECT COUNT(*) FROM audit WHERE event='qa_test_results_upload'"
        ).fetchone()[0]
        == 0
    )


def test_input_tag_is_ignored_and_canonical_digest_identity_is_deduplicated(temp_db, live):
    tagged = IMAGE.replace("@sha256:", ":untrusted-arm-ubuntu99@sha256:")
    batch = lookup_service.batch_images(temp_db, [IMAGE, tagged])
    assert batch["duplicate_count"] == 1
    assert batch["errors"] == []
    assert batch["images"][0]["image"] == IMAGE
    assert all(row["arch"] == "x86" and row["OS"] == "ubuntu22.04" for row in batch["rows"])
    assert len([call for call in live[1] if call[0] == "harbor"]) == 1


def test_app_id_stays_stable_when_official_display_name_changes(temp_db, live):
    row = template(temp_db)
    temp_db.execute("UPDATE apps SET official_name='ABACUS 科学计算'")
    temp_db.commit()
    assert row["app_id"] == "testapp" and "app_name" not in row
    assert lookup_service.by_image(temp_db, IMAGE)["app_name"] == "ABACUS 科学计算"
    assert lookup_service.by_image(temp_db, IMAGE)["image_app_name"] == "abacus"
    upload(temp_db, [row])
    assert qa_results_service.query(temp_db)["rows"][0]["app_id"] == "testapp"


def test_maca_and_hpcc_repositories_share_app_info_fetch_but_keep_image_identity(temp_db, live):
    hpcc = IMAGE.replace("abacus-maca", "abacus-hpcc")
    batch = lookup_service.batch_images(temp_db, [IMAGE, hpcc])
    assert batch["errors"] == []
    assert batch["duplicate_count"] == 0
    assert [image["image"] for image in batch["images"]] == [IMAGE, hpcc]
    assert len(batch["rows"]) == 6
    assert all(hpcc in row["docker_cmd"] for row in batch["rows"][3:])
    assert len([call for call in live[1] if call[0] == "harbor"]) == 2
    assert len([call for call in live[1] if call[0] == "gerrit"]) == 1
    row = {
        **batch["rows"][3],
        "test_result": "pass",
        "test_digest": hpcc,
        "test_env": "C500_server_1",
    }
    upload(temp_db, [row])
    stored = qa_results_service.query(temp_db)["rows"][0]
    assert hpcc.replace("@", ":" + TAGS[2] + "@") in stored["website_docker_cmd"]
    assert stored["qa_digest"] == hpcc
    assert stored["hpc_version"] == "3.10.1.2"
