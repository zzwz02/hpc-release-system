"""Automatic report acquisition, evidence coverage, review, persistence and HTTP contract."""
from __future__ import annotations

from copy import deepcopy
import datetime as dt
import io
import json
import threading
import time
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook

from app.api.errors import ApiError
from app.config import settings
from app.db.connection import connect, transaction
from app.deps import require_login
from app.domain import auto_weekly_report as rules
from app.domain.permissions import ALL_ROLES, roles_for_capability, roles_for_tab
from app.integrations import jira, llm
from app.integrations.weekly_report_jira import Reader
from app.main import create_app
from app.repositories import auto_weekly_report_repo as repo
from app.services import auto_weekly_report_service as service
from app.services.auto_weekly_report_attachments import Evidence, text_from
from app.services.auto_weekly_report_jobs import Registry

OPTS = {"group": "pde_hpc", "since": "2026-10-09 12:00", "until": "2026-10-17 00:00", "report_date": "2026-10-16"}
EPICS = [{"key": "PHS-10", "name": "平台研发"}]


def issue(key="PHS-1"):
    return {"key": key, "epic_key": "PHS-10", "linked_main": [],
            "fields": {"summary": "求解器性能", "created": "2026-10-01T10:00:00.000+0800", "status": {"name": "In Progress"},
                       "description": "研发背景", "assignee": {"name": "alice", "displayName": "测试同事"},
                       "comment": {"comments": [{"id": "1", "created": "2026-10-15T10:00:00.000+0800", "updated": "2026-10-15T10:00:00.000+0800", "body": "18 个用例已全部通过，耗时为 A100 的 1.5 倍；精度仍有卡点；下一步修复精度，需要协调跨团队修复资源"}], "total": 1},
                       "attachment": [], "issuelinks": []}, "changelog": {"histories": [], "total": 0}}


def payload():
    return {"highlights": [{"tag": tag, "text": text, "keys": ["PHS-1"]} for tag, text in [
        ("研发进展", "18 个用例通过，耗时为 A100 的 1.5 倍"), ("卡点", "精度问题待修复"), ("需支持", "需协调精度修复资源")]],
        "customers": [], "customer_support": [], "epics": [{"key": "PHS-10", "projects": [{"name": "求解器", "keys": ["PHS-1"],
            "items": [{"text": "18 个用例通过，耗时为 A100 的 1.5 倍；精度问题待修复", "keys": ["PHS-1"]}]}]}],
        "other": [], "omitted": [], "notes": ["来源为工单最新评论"]}


def saved(conn, report_id="older", **overrides):
    report = {**OPTS, "id": report_id, "created_by": "rm", "created_at": "2026-10-16 18:00:00", "markdown": "历史周报分类", "notes": "来源说明", "llm": {}, "evidence": {}, **overrides}
    with transaction(conn):
        repo.replace(conn, report)
    return report


def wait(registry, job_id):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        result = registry.get(job_id)
        if result["status"] != "running":
            return result
        time.sleep(0.01)
    raise AssertionError("job did not finish")


def test_beijing_default_window_and_options(monkeypatch):
    expected = rules.defaults(dt.datetime(2026, 10, 25, 16))
    assert expected == {"group": "pde_hpc", "since": "2026-10-16T12:00", "until": "2026-10-24T00:00", "report_date": "2026-10-23"}
    assert rules.defaults(dt.datetime(2026, 10, 23, 15))["until"] == "2026-10-23T15:00"
    monkeypatch.setattr(rules, "beijing_now", lambda: dt.datetime(2026, 10, 25, 16))
    assert rules.options(**OPTS) == OPTS
    for changes in [{"group": 'x") OR status=Done'}, {"until": "2026-10-30 00:00"}, {"report_date": "2026-10-17"}, {"since": "2026-01-01 00:00"}]:
        with pytest.raises(ValueError):
            rules.options(**{**OPTS, **changes})


def test_render_enforces_real_sources_and_covers_all_issues():
    markdown, notes = rules.render(payload(), OPTS, [issue()], EPICS)
    assert "### 平台研发（PHS-10）" in markdown
    assert "PHS-1" in notes and "2026-10-16" in markdown
    invalid = payload()
    invalid["epics"][0]["projects"][0]["items"][0]["keys"] = ["PHS-999"]
    with pytest.raises(ValueError, match="不存在的工单"):
        rules.render(invalid, OPTS, [issue()], EPICS)
    with pytest.raises(ValueError, match="工单未覆盖"):
        rules.render(payload(), OPTS, [issue(), issue("PHS-2")], EPICS)
    invalid = payload()
    invalid["customers"] = [deepcopy(invalid["epics"][0]["projects"][0])]
    invalid["customers"][0]["name"] = "某客户"
    with pytest.raises(ValueError, match="多个项目"):
        rules.render(invalid, OPTS, [issue()], EPICS)
    invalid = payload()
    invalid["highlights"][0]["text"] = "长" * 58
    with pytest.raises(ValueError, match="含标签后"):
        rules.render(invalid, OPTS, [issue()], EPICS)


def test_substantive_ignores_sorting_only():
    raw = issue()
    raw["fields"]["comment"] = {"comments": []}
    raw["changelog"] = {"histories": [{"items": [{"field": "Rank"}]}]}
    assert not rules.substantive(raw, OPTS)
    raw["changelog"]["histories"][0]["items"].append({"field": "status"})
    assert rules.substantive(raw, OPTS)


def test_paginated_collection_parent_epic_and_end_filter(monkeypatch):
    parent = issue("PHS-2")
    parent["fields"]["parent"] = {"key": "PHS-10", "fields": {"summary": "平台研发"}}
    raw = issue()
    raw["fields"]["parent"] = {"key": "PHS-2", "fields": {"summary": "求解器父单"}}
    raw["fields"]["comment"] = {"comments": [], "total": 2}
    raw["changelog"] = {"histories": [], "total": 2}
    calls = []
    def get(path):
        calls.append(path)
        if path.startswith("/rest/api/2/search?"):
            query = parse_qs(urlsplit(path).query)
            start = int(query["startAt"][0])
            if "issuetype = Epic" in query["jql"][0]:
                return {"issues": [{"key": "PHS-10", "fields": {"summary": "平台研发"}}], "total": 1}
            return {"issues": [deepcopy(raw if start == 0 else parent)], "total": 2}
        if path.endswith("/field"):
            return []
        if "/comment?" in path:
            offset = int(parse_qs(urlsplit(path).query)["startAt"][0])
            comment = deepcopy(issue()["fields"]["comment"]["comments"][0])
            if offset:
                comment.update(id="2", updated="2026-10-18T10:00:00.000+0800")
            return {"comments": [comment], "total": 2}
        if "/changelog?" in path:
            offset = int(parse_qs(urlsplit(path).query)["startAt"][0])
            return {"values": [{"created": "2026-10-15T10:00:00.000+0800" if not offset else "2026-10-18T10:00:00.000+0800", "items": [{"field": "status"}]}], "total": 2}
        if "/group/member?" in path:
            return {"values": [], "total": 0}
        raise AssertionError(path)
    reader = Reader(llm.CancelToken())
    # Keep reader.get checking cancellation in separate tests; simulate paginated API here.
    monkeypatch.setattr(reader, "get", get)
    issues, epics, _ = reader.collect(OPTS, lambda _: None)
    assert len(issues) == 2 and epics == EPICS
    assert issues[0]["epic_key"] == "PHS-10"
    assert issues[0]["linked_main"][0]["key"] == "PHS-2"
    assert len(issues[0]["fields"]["comment"]["comments"]) == 1
    assert len(issues[0]["changelog"]["histories"]) == 1
    assert any("窗口后编辑" in w for w in reader.warnings)
    assert any("startAt=1" in p for p in calls)


def test_attachment_table_preserves_empty_result_cells_and_limits(monkeypatch):
    book = Workbook()
    book.active.append(["用例", "性能", "精度"])
    book.active.append(["case1", "通过", None])
    stream = io.BytesIO()
    book.save(stream)
    text = text_from(stream.getvalue(), ".xlsx")
    assert "case1 | 通过 | " in text
    raw = issue()
    raw["fields"]["attachment"] = [{"filename": "huge.pdf", "size": 11 * 1024 * 1024}]
    warnings = []
    monkeypatch.setattr(jira, "download_attachment", lambda *_a, **_kw: pytest.fail("should not download oversized file"))
    assert Evidence(llm.CancelToken(), llm.LlmMeter(), warnings).read(raw) == []
    assert "未读取" in warnings[0]


def test_service_draft_review_repair_and_prior_classification(temp_db, monkeypatch):
    saved(temp_db, report_date="2026-10-09")
    monkeypatch.setattr(Reader, "collect", lambda *_a: ([issue()], EPICS, []))
    calls = []
    initial = payload()
    initial["epics"][0]["projects"][0]["items"][0]["text"] = "长" * 70
    def ask(system, prompt, **kwargs):
        calls.append(prompt)
        assert isinstance(kwargs["cancel"], llm.CancelToken)
        assert not kwargs["cancel"].cancelled
        if prompt.startswith("连接检查"):
            return {"ok": True}
        if "提取本周事实" in prompt:
            return {"facts": "18 个用例通过，耗时为 A100 的 1.5 倍；精度仍有卡点"}
        if "独立评审" in prompt and "原始材料片段" in prompt:
            return {"findings": ["严重 PHS-1：需支持没有来源，应改为客户进展并保留精度卡点"]}
        if "独立评审：" in prompt:
            result = payload()
            result["highlights"][2] = {"tag": "客户进展", "text": "用例验证完成", "keys": ["PHS-1"]}
            return result
        if "必须修正" in prompt:
            return payload()
        return initial
    monkeypatch.setattr(llm, "ask_json", ask)
    token = llm.CancelToken()
    result = service.generate(temp_db, OPTS, cancel=token, meter=llm.LlmMeter(), progress=lambda _: None)
    assert "18 个用例" in result["markdown"] and "需支持" not in result["markdown"]
    assert any("历史周报分类" in c for c in calls)
    assert any("原始材料片段" in c for c in calls)
    assert "独立评审 1 条" in result["notes"]
    assert result["evidence"]["previous_id"] == "older"
    assert repo.list_reports(temp_db)[0]["id"] == "older"  # service has no write side effects


@pytest.fixture()
def site(tmp_path, monkeypatch):
    from app.api.routers import auto_weekly_report as router
    monkeypatch.setattr(settings, "db_path", tmp_path / "main.db")
    conf = tmp_path / "runtime.conf"
    conf.write_text("[jira]\nJIRA_BASE_URL=https://jira.test\nJIRA_TOKEN=test-token\n[llm]\nLLM_BASE_URL=http://llm.test/v1\nLLM_MODEL=test-model\n", encoding="utf-8")
    monkeypatch.setattr(settings, "runtime_conf_path", conf)
    registry = Registry()
    monkeypatch.setattr(router, "registry", registry)
    current = {"username": "rm", "role": "RM"}
    app = create_app()
    app.dependency_overrides[require_login] = lambda: current.copy()
    return TestClient(app), current, registry


@pytest.mark.parametrize("role", ALL_ROLES)
def test_permissions(site, role):
    client, current, _ = site
    current["role"] = role
    view = role in roles_for_tab("weekly-report")
    run = role in roles_for_capability("weekly-report.run")
    assert client.get("/api/weekly-report/auto/reports").status_code == (200 if view else 403)
    assert client.get("/api/weekly-report/auto/reports/missing").status_code == (404 if view else 403)
    assert client.get("/api/weekly-report/auto/reports/missing/export").status_code == (404 if view else 403)
    assert client.get("/api/weekly-report/auto/jobs").status_code == (200 if run else 403)
    assert client.post("/api/weekly-report/auto/jobs/missing/watch", json={}).status_code == (404 if run else 403)
    assert client.post("/api/weekly-report/auto/jobs/missing/cancel", json={}).status_code == (404 if run else 403)
    assert client.put("/api/weekly-report/auto/reports/missing", json={"markdown": "draft", "notes": "", "revision": 1}).status_code == (404 if run else 403)
    if not run:
        assert client.post("/api/weekly-report/auto/generate", json=OPTS).status_code == 403


def test_generate_history_edit_conflict_and_export(site, monkeypatch):
    client, current, registry = site
    monkeypatch.setattr(service, "generate", lambda *_a, **_kw: {"markdown": "# 已生成周报", "notes": "来源 PHS-1", "evidence": {"issues": []}, "llm": {}})
    first = client.post("/api/weekly-report/auto/generate", json=OPTS)
    assert first.status_code == 200
    finished = wait(registry, first.json()["id"])
    assert finished["status"] == "completed"
    report_id = finished["report_id"]
    stream = client.post(f"/api/weekly-report/auto/jobs/{first.json()['id']}/watch", json={})
    assert json.loads(stream.text.splitlines()[-1])["report_id"] == report_id
    report = client.get(f"/api/weekly-report/auto/reports/{report_id}").json()
    assert report["markdown"] == "# 已生成周报" and report["revision"] == 1
    changes = {"markdown": "# 用户调整分类", "notes": "来源 PHS-1", "revision": 1}
    updated = client.put(f"/api/weekly-report/auto/reports/{report_id}", json=changes)
    assert updated.status_code == 200 and updated.json()["revision"] == 2
    assert client.put(f"/api/weekly-report/auto/reports/{report_id}", json={**changes, "markdown": "覆盖"}).status_code == 409
    current["role"] = "Owner"
    export = client.get(f"/api/weekly-report/auto/reports/{report_id}/export")
    assert "# 用户调整分类" in export.text and "交付说明" in export.text
    assert "filename*=UTF-8''" in export.headers["content-disposition"]
    assert len(client.get("/api/weekly-report/auto/reports").json()["reports"]) == 1


def test_cancellation_duplicate_start_and_failed_generation_preserve_history(site, monkeypatch):
    client, current, registry = site
    conn = connect(settings.db_path)
    saved(conn)
    conn.close()
    entered, release = threading.Event(), threading.Event()
    def generate(*_a, **kwargs):
        entered.set()
        assert release.wait(3)
        # Even a worker returning a result after cancellation must never save it.
        return {"markdown": "cancelled output", "notes": "", "evidence": {}, "llm": {}}
    monkeypatch.setattr(service, "generate", generate)
    job_id = client.post("/api/weekly-report/auto/generate", json=OPTS).json()["id"]
    assert entered.wait(3)
    assert client.post("/api/weekly-report/auto/generate", json=OPTS).status_code == 409
    assert client.post("/api/weekly-report/auto/generate", json={**OPTS, "group": "another_group"}).status_code == 409
    assert client.get("/api/weekly-report/auto/reports/older").json()["markdown"] == "历史周报分类"
    assert client.post(f"/api/weekly-report/auto/jobs/{job_id}/cancel", json={}).status_code == 200
    release.set()
    assert wait(registry, job_id)["status"] == "cancelled"
    def fail(*_a, **_kw):
        raise ValueError("未通过来源校验")
    monkeypatch.setattr(service, "generate", fail)
    second = client.post("/api/weekly-report/auto/generate", json=OPTS).json()["id"]
    assert wait(registry, second)["status"] == "failed"
    reports = client.get("/api/weekly-report/auto/reports").json()["reports"]
    assert [r["id"] for r in reports] == ["older"]
    assert client.get("/api/weekly-report/auto/reports/older").json()["markdown"] == "历史周报分类"


def test_missing_llm_configuration_visible_before_start(site, monkeypatch, tmp_path):
    client, _, _ = site
    config = tmp_path / "jira-only.conf"
    config.write_text("[jira]\nJIRA_BASE_URL=https://jira.test\nJIRA_TOKEN=test-token\n")
    monkeypatch.setattr(settings, "runtime_conf_path", config)
    response = client.post("/api/weekly-report/auto/generate", json=OPTS)
    assert response.status_code == 400 and "[llm]" in response.json()["error"]
    assert client.get("/api/weekly-report/auto/jobs").json()["jobs"] == []


def test_member_fallback_does_not_attribute_outsider_assignees(monkeypatch):
    reader = Reader(llm.CancelToken())
    raw = issue()
    raw["fields"]["assignee"] = {"name": "outsider", "displayName": "外部门"}
    def search(jql, fields="*all", expand=""):
        if "issuetype = Epic" in jql:
            return [{"key": "PHS-10", "fields": {"summary": "平台研发"}}]
        if "-180d" in jql:
            if fields == "creator":
                return [{"key": "PHS-5", "fields": {"creator": {"name": "alice", "displayName": "本组成员"}}}]
            return []
        return [deepcopy(raw)]
    monkeypatch.setattr(reader, "search", search)
    def get(path):
        if path.endswith("/field"):
            return [{"id": "epic", "schema": {"custom": ":gh-epic-link"}}]
        if "/group/member?" in path:
            raise RuntimeError("HTTP 403")
        raise AssertionError(path)
    raw["fields"]["epic"] = "PHS-10"
    monkeypatch.setattr(reader, "get", get)
    _, _, members = reader.collect(OPTS, lambda _: None)
    assert [m["name"] for m in members] == ["alice"]
    assert "180 天" in reader.warnings[-1]


def test_regeneration_replaces_only_its_week_and_invalidates_old_editor(site, monkeypatch):
    client, current, registry = site
    conn = connect(settings.db_path)
    saved(conn, "previous-week", report_date="2026-10-09")
    conn.close()
    def generate(_conn, opts, **_kwargs):
        return {"markdown": f"# {opts['group']} 新周报", "notes": f"{opts['since']} 交付说明",
                "evidence": {"group": opts["group"]}, "llm": {"calls": 3 if opts["group"] == "pde_hpc" else 5}}
    monkeypatch.setattr(service, "generate", generate)
    job_id = client.post("/api/weekly-report/auto/generate", json=OPTS).json()["id"]
    first = wait(registry, job_id)
    assert first["status"] == "completed"
    report_id = first["report_id"]
    assert client.put(f"/api/weekly-report/auto/reports/{report_id}", json={"markdown": "手动修改", "notes": "手工说明", "revision": 1}).status_code == 200
    current["username"] = "another-rm"
    changed = {**OPTS, "group": "another_group", "since": "2026-10-10 12:00"}
    job_id = client.post("/api/weekly-report/auto/generate", json=changed).json()["id"]
    second = wait(registry, job_id)
    assert second["status"] == "completed" and second["report_id"] == report_id
    reports = client.get("/api/weekly-report/auto/reports").json()["reports"]
    assert len(reports) == 2 and {r["report_date"] for r in reports} == {"2026-10-09", "2026-10-16"}
    detail = client.get(f"/api/weekly-report/auto/reports/{report_id}").json()
    assert detail["revision"] == 3
    assert detail["created_by"] == "another-rm" and detail["jira_group"] == "another_group"
    assert detail["since"] == changed["since"] and detail["markdown"] == "# another_group 新周报"
    assert detail["notes"] == "2026-10-10 12:00 交付说明"
    assert detail["evidence"] == {"group": "another_group"} and detail["llm"] == {"calls": 5}
    assert client.get("/api/weekly-report/auto/reports/previous-week").json()["markdown"] == "历史周报分类"
    assert client.put(f"/api/weekly-report/auto/reports/{report_id}", json={"markdown": "迟到的编辑", "notes": "", "revision": 2}).status_code == 409


@pytest.mark.parametrize("failure", [ValueError("未通过校验"), llm.LLMCancelled("已中止")])
def test_no_incomplete_report_is_saved_for_a_new_week(site, monkeypatch, failure):
    client, _, registry = site
    entered, release = threading.Event(), threading.Event()
    def generate(*_a, **_kw):
        entered.set()
        assert release.wait(3)
        raise failure
    monkeypatch.setattr(service, "generate", generate)
    job_id = client.post("/api/weekly-report/auto/generate", json=OPTS).json()["id"]
    try:
        assert entered.wait(3)
        assert client.get("/api/weekly-report/auto/reports").json()["reports"] == []
    finally:
        release.set()
    assert wait(registry, job_id)["status"] in {"cancelled", "failed"}
    assert client.get("/api/weekly-report/auto/reports").json()["reports"] == []


def test_overwrite_and_audit_rollback_together(site, monkeypatch):
    from app.services import auto_weekly_report_jobs as jobs
    client, _, registry = site
    conn = connect(settings.db_path)
    saved(conn)
    before = repo.get(conn, "older")
    conn.close()
    monkeypatch.setattr(service, "generate", lambda *_a, **_kw: {"markdown": "新内容", "notes": "新说明", "evidence": {}, "llm": {}})
    def fail_audit(*_a, **_kw):
        raise RuntimeError("模拟写入失败")
    monkeypatch.setattr(jobs, "log_audit", fail_audit)
    job_id = client.post("/api/weekly-report/auto/generate", json=OPTS).json()["id"]
    assert wait(registry, job_id)["status"] == "failed"
    conn = connect(settings.db_path)
    assert repo.get(conn, "older") == before
    assert len(repo.list_reports(conn)) == 1
    conn.close()


def test_legacy_per_run_records_migrate_to_latest_per_week(temp_db, tmp_path):
    import sqlite3
    ddl = temp_db.execute("SELECT sql FROM sqlite_master WHERE name='auto_weekly_reports'").fetchone()["sql"]
    path = tmp_path / "legacy-reports.db"
    legacy = sqlite3.connect(path)
    legacy.execute(ddl)
    rows = [("first", "2026-10-16", "2026-10-16 17:00:00"),
            ("second", "2026-10-16", "2026-10-16 18:00:00"),
            ("latest-tie", "2026-10-16", "2026-10-16 18:00:00"),
            ("previous-week", "2026-10-09", "2026-10-09 18:00:00")]
    for report_id, friday, created in rows:
        legacy.execute("""INSERT INTO auto_weekly_reports
            (id,jira_group,since,until,report_date,created_by,created_at,markdown,notes)
            VALUES (?,'pde_hpc','2026-10-01 12:00','2026-10-17 00:00',?,'rm',?,?,'source')""",
            (report_id, friday, created, report_id))
    legacy.commit()
    legacy.close()
    conn = connect(path)
    assert [r["id"] for r in repo.list_reports(conn)] == ["latest-tie", "previous-week"]
    assert repo.get(conn, "latest-tie")["markdown"] == "latest-tie"
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("""INSERT INTO auto_weekly_reports SELECT
            'duplicate',jira_group,since,until,report_date,created_by,created_at,markdown,notes,evidence_json,llm_json,revision
            FROM auto_weekly_reports WHERE id='latest-tie'""")
    conn.rollback()
    conn.close()


def test_image_timeout_reports_filename_and_skips_later_images_but_reads_text(monkeypatch):
    from PIL import Image
    buffer = io.BytesIO()
    Image.new("RGB", (16, 16), "white").save(buffer, format="PNG")
    raw = issue()
    raw["fields"]["attachment"] = [
        {"filename": "first.png", "content": "image-first", "size": len(buffer.getvalue()), "created": "2026-10-15T12:00:00+0800"},
        {"filename": "second.png", "content": "image-second", "size": len(buffer.getvalue()), "created": "2026-10-15T11:00:00+0800"},
        {"filename": "result.txt", "content": "text", "size": 4, "created": "2026-10-15T10:00:00+0800"},
    ]
    downloads, calls, progress, warnings = [], [], [], []
    def download(url, **_kwargs):
        downloads.append(url)
        return buffer.getvalue() if url.startswith("image") else "18/18 passed".encode()
    def ask(system, prompt, **kwargs):
        calls.append(kwargs)
        assert kwargs["timeout"] == 30 and kwargs["attempts"] == 1
        raise llm.LLMTimeoutError("timeout")
    monkeypatch.setattr(jira, "download_attachment", download)
    monkeypatch.setattr(llm, "ask_json", ask)
    result = Evidence(llm.CancelToken(), llm.LlmMeter(), warnings, progress=progress.append).read(raw)
    assert len(calls) == 1 and downloads == ["image-first", "text"]
    assert result == [{"filename": "result.txt", "text": "18/18 passed"}]
    assert any("识别图片 PHS-1/first.png（最多 30 秒）" in msg for msg in progress)
    assert any("second.png" in warning and "已停用" in warning for warning in warnings)
    assert any("first.png" in warning and "未识别" in warning for warning in warnings)


def test_image_cancellation_stops_the_job_instead_of_skipping_evidence(monkeypatch):
    from PIL import Image
    stream = io.BytesIO()
    Image.new("RGB", (16, 16)).save(stream, format="PNG")
    raw = issue()
    raw["fields"]["attachment"] = [{"filename": "result.png", "content": "image", "size": 10}]
    monkeypatch.setattr(jira, "download_attachment", lambda *_a, **_kw: stream.getvalue())
    def ask(*_a, **_kwargs):
        raise llm.LLMCancelled("cancelled")
    monkeypatch.setattr(llm, "ask_json", ask)
    warnings = []
    with pytest.raises(llm.LLMCancelled):
        Evidence(llm.CancelToken(), llm.LlmMeter(), warnings).read(raw)
    assert warnings == []


def test_model_preflight_failure_stops_before_jira_acquisition(temp_db, monkeypatch):
    def collect(*_a, **_kwargs):
        pytest.fail("JIRA should not be fetched after a failed model preflight")
    def ask(*_a, **kwargs):
        assert kwargs["timeout"] == 15 and kwargs["attempts"] == 1
        raise llm.LLMTimeoutError("LLM connection timed out")
    monkeypatch.setattr(Reader, "collect", collect)
    monkeypatch.setattr(llm, "ask_json", ask)
    stages = []
    with pytest.raises(llm.LLMTimeoutError):
        service.generate(temp_db, OPTS, cancel=llm.CancelToken(), meter=llm.LlmMeter(), progress=stages.append)
    assert stages == ["检查模型服务连接"]
    assert repo.list_reports(temp_db) == []


def test_parallel_sources_bounds_requests_and_preserves_source_order():
    barrier = threading.Barrier(service.LLM_WORKERS)
    lock = threading.Lock()
    active = maximum = 0
    entries = [{"key": f"PHS-{i}", "index": i} for i in range(service.LLM_WORKERS * 3)]
    def work(entry, phase_cancel):
        nonlocal active, maximum
        with lock:
            active += 1
            maximum = max(maximum, active)
        try:
            barrier.wait(timeout=3)
            time.sleep(0.005 * (service.LLM_WORKERS - entry["index"] % service.LLM_WORKERS))
            return entry["index"]
        finally:
            with lock:
                active -= 1
    result = service.parallel_sources(entries, work, label="提炼", cancel=llm.CancelToken(), progress=lambda _: None)
    assert result == list(range(len(entries)))
    assert maximum == service.LLM_WORKERS


def test_parallel_sources_failure_cancels_sibling_calls_without_cancelling_parent():
    parent = llm.CancelToken()
    barrier = threading.Barrier(service.LLM_WORKERS)
    stopped = []
    def work(entry, phase_cancel):
        barrier.wait(timeout=3)
        if entry["index"] == 0:
            raise ValueError("source failed")
        deadline = time.monotonic() + 3
        while not phase_cancel.cancelled and time.monotonic() < deadline:
            time.sleep(0.005)
        assert phase_cancel.cancelled
        stopped.append(entry["index"])
        raise llm.LLMCancelled("sibling stopped")
    with pytest.raises(ValueError, match="source failed"):
        service.parallel_sources([{"key": str(i), "index": i} for i in range(service.LLM_WORKERS)], work,
                                 label="复核", cancel=parent, progress=lambda _: None)
    assert len(stopped) == service.LLM_WORKERS - 1 and not parent.cancelled
