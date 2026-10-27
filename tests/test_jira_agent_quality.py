"""Comment quality, publication uncertainty and bounded same-turn recovery.

Cases below are redacted from the 2026-10-27 assessment. They test preservation
and rendering of known facts, not whether a live model can discover those facts.
"""
from __future__ import annotations

import asyncio
import copy
import json
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from app import runtime_config
from app.config import settings
from app.db.connection import transaction
from app.domain import jira_agent as domain
from app.integrations import jira
from app.repositories import jira_agent_repo as repo
from app.services import jira_agent_runner as runner
from tests.test_jira_agent import (RESULT, env, _preflight, _handover, _wait, _done, _latest,
                                   _detail, _db_turn, _phase, _until)

GROUP = domain.AgentGroup("HPC", "HPC 数字员工", "ws://b:4510", "t", "/w", (), "", 600, 1)


def render(result):
    return domain.render_jira_comment(group=GROUP, result=result, turn_seq=2, owner="owner",
                                     conversation_url="https://site/jira-agent?conversation=example", patches=[])


def projection(outcome, **kwargs):
    return {"kind": "analysis", "outcome": outcome, "evidence": [], "limitations": "",
            "correction": "", "commands": [], **kwargs}


def narrative(outcome, body, **kwargs):
    return {"kind": "analysis", "outcome": outcome, "body": body,
            "evidence_indices": [], "commands": [], "link_text": "验证记录", **kwargs}


@pytest.mark.parametrize("comment,action", [
    (narrative("内存增长已定位到运行时分配路径，已取得无需应用的独立复现。",
        "同一程序执行 10 万次，异常版 RSS 39520→94080 KiB，正常版及普通写对照稳定。\n\n"
        "分配栈相同，内部释放机制尚未确认；异常原案由 RSS 保护停止，未等待自然 OOM。\n\n"
        "请 owner 协调运行时维护者检查该路径，修复后用独立案例及原始 250000 步验证。",
        evidence_indices=[0]), "needs_handoff"),
    (narrative("panel 释放发生在 LU 分解收尾，之后仍有三角求解和通信。",
        "第 521–523 行释放 panel，第 528 行调用 {{HPL_pdtrsv}}，后续通信使用仍存活的 {{AMAT->dW}}。\n\n"
        "设备同步不能代替注销；失效注册如何影响内部传输尚未确认。沿用第 3 轮验证，本轮未新增修改或重跑。",
        kind="explanation"), "none"),
    (narrative("已完成 N300 基线和 profile，等待 C600 对照。",
        "等待权重加载后取得完整训练耗时：\n* 1.0／4 卡：833.47s。\n* 1.0／8 卡：439.05s。\n"
        "* 1.5／8 卡：441.78s、440.55s。\n\n"
        "1.5／4 卡命令与 1.0／4 卡相同，未独立执行。当前不能判断跨硬件差异原因，提供 C600 后继续同口径对照。",
        kind="status"), "needs_help"),
    (narrative("清理异常定位到子项删除返回 EBUSY，现场触发原因尚未确认。",
        "已找到数据，两次原脚本均退出 0，未复现；清理 traceback 不必然导致非零退出。\n\n"
        "请 owner 协调原报告人在失败环境运行诊断 hook，采集首条 UNLINK_EBUSY 路径、占用信息和退出码。"), "needs_info"),
    (narrative("当前未复现原 CI 失败。",
        "指定镜像的两次 smoke 均退出 0 并输出 PASS；Routing 错误未导致本轮验收失败。\n\n"
        "原 CI 失败原因尚未确认；再次出现时请保留首个错误、完整日志，并核对镜像 digest 和超时规则。"), "none"),
])
def test_narrative_preserves_case_facts_without_appending_full_report(comment, action):
    result = {**copy.deepcopy(RESULT), "comment": comment, "action_required": action,
              "assistance": {"current_issue": "完整报告中的状态", "owner": "owner", "request": "完整报告中的协助请求"},
              "ownership": {"belongs_to_us": "no", "target_group": "完整报告中的受理组", "reasoning": "完整归属依据"},
              "next_steps": ["完整报告中的详细步骤一", "完整报告中的详细步骤二"]}
    original = copy.deepcopy(result)
    body = render(result)
    assert f"*{comment['outcome']}*\n\n{comment['body']}" in body
    assert "[验证记录|https://site/jira-agent?conversation=example]" in body
    assert all(label not in body for label in ("*依据*", "*待办*", "*尚未确认*", "*待审核*", "完整报告中的"))
    assert result == original  # Full details and action routing remain available on the site.


@pytest.mark.parametrize("comment", [None, projection("旧版结论。"), narrative("新版结论。", "正文说明。")])
def test_auto_generated_notice_remains_at_end_for_all_comment_formats(comment):
    result = {**RESULT, "comment": comment}
    body = render(result)
    notice = "本评论由 JIRA 数字员工根据本轮分析自动生成，不会修改 assignee、状态或代码；后续操作由 assignee 审核决定。"
    assert body.endswith("\n\n----\n\n_" + notice + "_")
    assert body.count(notice) == 1
    assert body.startswith("HPC 数字员工 · 第 2 轮\n")
    assert "[HPC 数字员工" not in body


@pytest.mark.parametrize("bad", [{"body": 12}, {"evidence_indices": [99]},
                                  {"evidence_indices": [True]}, {"commands": [1]}, {"link_text": None}])
def test_invalid_narrative_preserves_blocker_via_legacy_fallback(bad):
    result = {**RESULT, "action_required": "needs_help",
              "assistance": {"current_issue": "Docker 无权限", "owner": "管理员", "request": "恢复 Docker 权限"},
              "comment": {**narrative("无效正文", "不能发布"), **bad}}
    body = render(result)
    assert "无效正文" not in body and "Docker 无权限" in body and "管理员：恢复 Docker 权限" in body
    assert domain.comment_quality_warnings(result, body)


@pytest.mark.parametrize("post", [False, True])
def test_narrative_is_preserved_through_runner_and_publication_choice(env, post):
    env.fake.comment_override = narrative(
        "释放 panel 后仍有三角求解和通信。",
        "后续使用 {{AMAT->dW}}。\n\n本轮解释调用顺序，沿用上轮验证，没有新增修改或重跑。",
        kind="explanation", link_text="调用顺序与已有验证")
    cid = _handover(env, post_comment=post)["conversation"]["id"]
    turn = _latest(_wait(env, cid, _done))
    body = turn["comment_body"]
    assert env.fake.comment_override["body"] in body
    assert "请 owner" not in body and "*待办*" not in body and "*待审核*" not in body
    assert "调用顺序与已有验证|" in body
    assert len(env.jira.comments) == int(post)
    if post:
        assert env.jira.comments[0][1] == body
        assert body.splitlines()[-1] == f"_{domain._COMMENT_NOTICE}_ _记录编号：{turn['id']}_"
    else:
        assert turn["comment_status"] == ""


@pytest.mark.parametrize("name,comment,required,forbidden", [
    ("Darwin", projection("N300 profile 尚未启动：Docker 权限不足。", kind="blocked",
        limitations="模型目录可读，尚无训练计时或 profile 数据。"),
        ["尚未启动", "尚无训练计时"], ["已完成基线"]),
    ("Amber", projection("已定位到 mcLaunchKernel 调用路径。",
        limitations="内部机制未确认，仍需保留调用边界结论。"),
        ["mcLaunchKernel", "内部机制未确认"], ["SDK 内部根因已确认"]),
    ("HPL", projection("修复应用缺少 cudaHostUnregister 的操作，原案例验证通过。", kind="fix",
        correction="此前将 GPU MPI 异常归为外部组件缺陷的判断不成立；独立对照支持应用生命周期问题。"),
        ["此前", "不成立", "cudaHostUnregister"], ["通信库缺陷已确认"]),
    ("MatterGen", projection("本轮补齐操作和七组前后日志。", kind="fix",
        limitations="七项修复后通过沿用上一轮；本轮直接降级仅验证 Evaluating。",
        commands=["python -m pip install --no-deps 'setuptools<82'"]),
        ["沿用上一轮", "仅验证 Evaluating", "setuptools<82"], ["本轮七项全部通过"]),
    ("MONAI", projection("已在 /pde_hpc/dataset 找到输入；两组原脚本均退出 0，无 EBUSY。",
        limitations="人为挂载对照仅解释错误栈，不能证明原案例根因；历史性能尚未验收。"),
        ["/pde_hpc/dataset", "不能证明", "尚未验收"], ["请提供数据集"]),
    ("HPL explanation", projection("同步不能代替注销；释放前仍需解除 host memory 注册。", kind="explanation",
        limitations="引用上一轮验证，本轮没有新增实验。"),
        ["不能代替注销", "本轮没有新增实验"], ["待审核", "修复补丁", "复现步骤"]),
])
def test_redacted_comment_cases(name, comment, required, forbidden):
    result = {**copy.deepcopy(RESULT), "comment": comment, "action_required": "none", "next_steps": []}
    body = render(result)
    assert len(body) < 600, name
    assert all(text in body for text in required)
    assert all(text not in body for text in forbidden)
    assert body.count("不会修改 assignee、状态或代码；后续操作由 assignee 审核决定。") == 1
    assert result["reproduction"] == RESULT["reproduction"]


def test_selected_evidence_is_not_first_two_and_duplicate_request_is_removed():
    result = {**RESULT, "action_required": "needs_help", "evidence": [
        {"description": "采集 20 个附件", "command": "sha256sum ...", "result": "材料齐全"},
        {"description": "发布目录核验", "command": "catalog", "result": "找到应用"},
        {"description": "权限检查", "command": "docker info", "result": "permission denied"},
    ], "assistance": {"current_issue": "Docker 权限不足。", "owner": "容器管理员", "request": "恢复执行账号的 Docker 权限。"},
        "next_steps": ["恢复执行账号的 Docker 权限。"],
        "comment": projection("Docker 权限不足。", kind="blocked",
                              evidence=[{"index": 2, "summary": "Docker info 返回 permission denied，尚未开始实验。"}])}
    body = render(result)
    assert body.count("Docker 权限不足") == 1
    assert body.count("恢复执行账号的 Docker 权限") == 1
    assert "容器管理员" in body and "permission denied" in body
    assert "sha256sum" not in body and "发布目录" not in body
    assert len(result["evidence"]) == 3


def test_long_required_commands_are_not_cut_or_rejected():
    command = "python run.py --input '" + "a" * 850 + "' --check"
    result = {**RESULT, "comment": projection("按以下命令运行。", kind="explanation", commands=[command])}
    body = render(result)
    assert command in body and body.count("{noformat}") == 2
    assert domain.comment_quality_warnings(result, body)
    assert domain.parse_result(json.dumps(result))["comment"]["commands"] == [command]


@pytest.mark.parametrize("projection_kind", ["valid", "missing", "invalid"])
def test_handover_status_uses_curated_comment_or_legacy_fallback(projection_kind):
    outcome = "N300 基线及 profile 已完成，等待 C600 对照。"
    status = "当前待办已转为 C600 对照，新增摘要仍明确等待该条件。"
    result = {**copy.deepcopy(RESULT), "conclusion": "scope_narrowed", "action_required": "needs_help",
              "summary": "已整理现有材料。", "next_steps": [],
              "assistance": {"current_issue": status, "owner": "资源管理员",
                             "request": "请确认 C600 可用卡数和时间窗口。"}}
    if projection_kind == "valid":
        result["comment"] = projection(outcome, kind="status")
    elif projection_kind == "invalid":
        result["comment"] = projection(outcome, evidence=[{"index": 999, "summary": "无效引用"}])
    else:
        result.pop("comment", None)
    body = render(result)
    assert "资源管理员：请确认 C600 可用卡数和时间窗口。" in body
    if projection_kind == "valid":
        assert outcome in body and status not in body
    else:
        assert status in body
    assert result["assistance"]["current_issue"] == status


def test_invalid_projection_falls_back_without_losing_analysis():
    result = {**RESULT, "comment": projection("不能使用的摘要", evidence=[{"index": 50, "summary": "伪引用"}])}
    body = render(result)
    assert "伪引用" not in body and RESULT["summary"] in body
    assert RESULT["root_cause"] in body
    assert domain.comment_quality_warnings(result, body)
    assert domain.parse_stored_result(json.dumps(result)) == result


@pytest.mark.parametrize("url", ["http://host:4510", "host:4510", "ws://", "ws://host:bad", "ws://host:70000",
                                  "ws://user:secret@host", "ws://host/#secret", "ws:// host"])
def test_invalid_endpoint_is_rejected_without_echoing_secrets(url):
    with pytest.raises(runtime_config.ConfigError) as error:
        domain.groups_from_sections({"HPC": {"DISPLAY_NAME": "HPC", "WORKSPACE_ROOT": "/w",
            "CODEX_WS_URL": url, "CODEX_WS_TOKEN": "secret", "TURN_TIMEOUT_SECONDS": "60", "MAX_CONCURRENT": "1"}})
    assert "CODEX_WS_URL" in str(error.value) and "secret" not in str(error.value)


def test_invalid_config_creates_no_turn(env):
    env.conf.write_text(env.conf.read_text().replace(env.fake.url, "http://bad:4510"))
    response = env.client.post("/api/jira-agent/conversations", json={"issue_key": "MC3-7672", "text": "检查"})
    assert response.status_code >= 400
    assert "CODEX_WS_URL" in response.text
    with runner.open_db() as conn:
        assert conn.execute("SELECT count(*) FROM jira_agent_turns").fetchone()[0] == 0


@pytest.mark.parametrize("restart", [False, True])
def test_accepted_post_with_lost_response_is_reconciled_once(env, monkeypatch, restart):
    def accepted_then_timeout(key, body):
        env.jira.add_comment(key, body)
        raise TimeoutError("response lost")
    monkeypatch.setattr(jira, "add_comment", accepted_then_timeout)
    cid = _handover(env)["conversation"]["id"]
    turn = _latest(_wait(env, cid, _done))
    assert turn["comment_publication_state"] == "uncertain"
    assert len(env.jira.comments) == 1
    if restart:
        env.stop_site()
        env.start_site()
        _until(lambda: _db_turn(turn["id"])["comment_status"] == "posted")
    else:
        response = env.client.post(f"/api/jira-agent/turns/{turn['id']}/comment/retry")
        assert response.status_code == 200
        assert response.json()["turn"]["comment_status"] == "posted"
    assert len(env.jira.comments) == 1 and env.fake.turns == 1


def test_uncertain_absent_or_unreadable_is_never_reposted(env, monkeypatch):
    calls = []
    def timed_out(key, body):
        calls.append(body)
        raise TimeoutError("unknown")
    monkeypatch.setattr(jira, "add_comment", timed_out)
    cid = _handover(env)["conversation"]["id"]
    turn = _latest(_wait(env, cid, _done))
    url = f"/api/jira-agent/turns/{turn['id']}/comment/retry"
    assert env.client.post(url).json()["turn"]["comment_publication_state"] == "uncertain"
    def incomplete(*args):
        raise ValueError("partial read")
    monkeypatch.setattr(jira, "find_agent_comment", incomplete)
    assert "partial read" in env.client.post(url).json()["turn"]["comment_error"]
    assert len(calls) == 1 and env.jira.comments == []


def test_concurrent_publication_retries_only_send_once(env, monkeypatch):
    env.jira.fail_comment = True
    cid = _handover(env)["conversation"]["id"]
    turn = _latest(_wait(env, cid, _done))
    env.jira.fail_comment = False
    entered, release = threading.Event(), threading.Event()
    def slow_send(key, body):
        entered.set()
        assert release.wait(5)
        return env.jira.add_comment(key, body)
    monkeypatch.setattr(jira, "add_comment", slow_send)
    url = f"/api/jira-agent/turns/{turn['id']}/comment/retry"
    with ThreadPoolExecutor(2) as pool:
        first = pool.submit(env.client.post, url)
        assert entered.wait(5)
        second = pool.submit(env.client.post, url)
        second.result(timeout=5)
        release.set()
        assert first.result(timeout=5).json()["turn"]["comment_status"] == "posted"
    assert len(env.jira.comments) == 1


def test_legacy_ambiguous_comment_is_checked_without_adding_marker_or_sending(env):
    env.stop_site()
    with runner.open_db() as conn, transaction(conn):
        repo.create_conversation(conn, conversation_id="jac_legacy", issue_key="MC3-1", issue_summary="",
                                 agent_group="HPC", owner="alice", created_by="alice", workspace="/w")
        turn = repo.create_turn(conn, conversation_id="jac_legacy", trigger="handover", created_by="alice", input_text="")
        repo.update_turn(conn, turn["id"], status="completed", comment_status="pending", comment_body="旧评论")
    env.jira.comments.append(("MC3-1", "旧评论"))
    env.start_site()
    _until(lambda: _db_turn(turn["id"])["comment_status"] == "posted")
    assert env.jira.comments == [("MC3-1", "旧评论")]


@pytest.mark.parametrize("post", [False, True])
def test_capacity_retry_stays_in_one_business_turn(env, post):
    env.fake.plans = ["capacity", "complete"]
    cid = _handover(env, post_comment=post)["conversation"]["id"]
    detail = _wait(env, cid, _done)
    turn = _latest(detail)
    assert turn["status"] == "completed" and turn["recovery_attempts"] == 1
    assert len(detail["turns"]) == 1 and env.fake.turns == 2
    assert len(env.jira.comments) == int(post)
    assert turn["post_comment"] is post


@pytest.mark.parametrize("plans,expected_attempts,expected_remote,expected_status", [
    (["capacity"] * 3, 2, 3, "failed"),
    (["capacity_after_tool"], 0, 1, "failed"),
    (["capacity_will_retry"], 0, 1, "completed"),
])
def test_retry_budget_tools_and_server_retry(env, plans, expected_attempts, expected_remote, expected_status):
    env.fake.plans = plans
    cid = _handover(env)["conversation"]["id"]
    turn = _latest(_wait(env, cid, _done))
    assert (turn["recovery_attempts"], env.fake.turns, turn["status"]) == (expected_attempts, expected_remote, expected_status)


def test_disconnect_reattaches_to_running_experiment(env):
    env.fake.plans = ["disconnect"]
    cid = _handover(env)["conversation"]["id"]
    _until(lambda: len(env.fake.params("thread/resume")) >= 1)
    env.fake.release()
    detail = _wait(env, cid, _done)
    assert _latest(detail)["status"] == "completed"
    assert env.fake.turns == 1 and len(detail["turns"]) == 1 and len(env.jira.comments) == 1
    assert len([e for e in detail["events"] if e["kind"] == "command"]) == 1


def test_restart_during_capacity_backoff_preserves_budget(env, monkeypatch):
    monkeypatch.setattr(runner, "_RECOVERY_BACKOFF_SECONDS", .5)
    env.fake.plans = ["capacity", "complete"]
    cid = _handover(env)["conversation"]["id"]
    detail = _wait(env, cid, lambda d: _latest(d)["retry_pending"] == 1)
    started = _latest(detail)["started_at"]
    env.stop_site()
    env.start_site()
    turn = _latest(_wait(env, cid, _done))
    assert turn["status"] == "completed" and turn["recovery_attempts"] == 1
    assert turn["started_at"] == started and env.fake.turns == 2


def test_cancel_does_not_enter_recovery(env):
    env.fake.plans = ["hold"]
    cid = _handover(env)["conversation"]["id"]
    _wait(env, cid, _phase("running"))
    response = env.client.post(f"/api/jira-agent/conversations/{cid}/cancel")
    assert response.status_code == 200
    turn = _latest(_wait(env, cid, _done))
    assert turn["status"] == "cancelled" and turn["recovery_attempts"] == 0
    assert not env.jira.comments


@pytest.mark.parametrize("mode", ["normal", "changed_total", "wrong_author", "edited", "empty_page"])
@pytest.mark.parametrize("separator", ["\n", " "])
def test_comment_lookup_requires_complete_pages_and_identity(monkeypatch, mode, separator):
    monkeypatch.setattr(jira, "_require_config", lambda: {"JIRA_BASE_URL": "http://jira", "JIRA_TOKEN": "test"})
    body = "结论" + separator + "_记录编号：jat_lookup_"
    calls = []
    def request(base, token, method, path, **kwargs):
        calls.append(path)
        assert method == "GET"
        if path.endswith("myself"):
            return {"name": "bot"}
        offset = 1 if "startAt=1&" in path else 0
        if offset == 0:
            return {"startAt": 0, "total": 2, "comments": [{"id": "a", "author": {"name": "qa"}, "body": "old"}]}
        return {"startAt": 1, "total": 3 if mode == "changed_total" else 2, "comments": [] if mode == "empty_page" else [{
            "id": "b", "author": {"name": "human" if mode == "wrong_author" else "bot"},
            "body": body + ("edited" if mode == "edited" else "")} ]}
    monkeypatch.setattr(jira, "_request", request)
    if mode == "normal":
        assert jira.find_agent_comment("MC3-1", body, "jat_lookup") == "b"
    else:
        with pytest.raises(ValueError):
            jira.find_agent_comment("MC3-1", body, "jat_lookup")
    assert len(calls) == 3


def test_recovery_does_not_extend_deadline(env, monkeypatch):
    monkeypatch.setattr(runner, "_RECOVERY_BACKOFF_SECONDS", 2)
    env.conf.write_text(env.conf.read_text().replace("TURN_TIMEOUT_SECONDS = 600", "TURN_TIMEOUT_SECONDS = 1"))
    env.fake.plans = ["capacity", "complete"]
    cid = _handover(env)["conversation"]["id"]
    turn = _latest(_wait(env, cid, _done))
    assert turn["status"] == "failed" and turn["recovery_attempts"] == 0
    assert env.fake.turns == 1 and not env.jira.comments


def test_restart_after_post_before_local_ack_preserves_analysis(env, monkeypatch):
    original = repo.set_comment_publication
    failed = False
    def lose_ack(conn, turn_id, state, **kwargs):
        nonlocal failed
        if state == "posted" and not failed:
            failed = True
            raise RuntimeError("local commit unavailable after accepted POST")
        return original(conn, turn_id, state, **kwargs)
    monkeypatch.setattr(repo, "set_comment_publication", lose_ack)
    cid = _handover(env)["conversation"]["id"]
    _until(lambda: failed and runner.runner.active_for_conversation(cid) is None)
    turn = _latest(_detail(env, cid))
    assert turn["status"] == "completed" and turn["comment_status"] == "pending"
    assert turn["comment_publication_state"] == "sending" and turn["result"]
    env.stop_site()
    env.start_site()
    _until(lambda: _db_turn(turn["id"])["comment_status"] == "posted")
    assert len(env.jira.comments) == 1 and env.fake.turns == 1


def test_additive_migration_preserves_old_results_and_comments(tmp_path):
    from app.db.jira_agent_connection import connect_jira_agent, init_jira_agent_db
    with connect_jira_agent(f"sqlite:///{tmp_path / 'migration.db'}") as conn, transaction(conn):
        repo.create_conversation(conn, conversation_id="jac_migration", issue_key="MC3-1", issue_summary="",
                                 agent_group="HPC", owner="owner", created_by="owner", workspace="/w")
        turn = repo.create_turn(conn, conversation_id="jac_migration", trigger="handover", created_by="owner", input_text="")
        repo.update_turn(conn, turn["id"], status="completed", result_json=json.dumps(RESULT), comment_body="历史评论",
                         comment_status="posted", comment_id="42", post_comment=0)
        conn.execute("DROP TABLE jira_agent_comment_publications")
        conn.execute("ALTER TABLE jira_agent_turns DROP COLUMN recovery_attempts")
        conn.execute("ALTER TABLE jira_agent_turns DROP COLUMN retry_pending")
    with connect_jira_agent(f"sqlite:///{tmp_path / 'migration.db'}") as conn:
        init_jira_agent_db(conn)
        init_jira_agent_db(conn)
        restored = repo.get_turn(conn, turn["id"])
        assert (restored["comment_body"], restored["comment_status"], restored["comment_id"], restored["post_comment"]) == ("历史评论", "posted", "42", 0)
        assert restored["result_json"] == json.dumps(RESULT)
        assert restored["recovery_attempts"] == 0 and restored["retry_pending"] == 0
        assert repo.comment_publication(conn, turn["id"]) is None


def test_prestart_reconnect_keeps_early_cancellation_available(env, monkeypatch):
    proof = _preflight(env)
    original_connect = runner.CodexAppServerClient.connect
    connections = 0
    async def connect(client):
        nonlocal connections
        connections += 1
        if connections == 1:
            raise runner.CodexTransportError("temporary connection loss before starting")
        return await original_connect(client)
    monkeypatch.setattr(runner.CodexAppServerClient, "connect", connect)
    entered, release = threading.Event(), threading.Event()
    def snapshot(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return env.jira.get_issue_snapshot(*args, **kwargs)
    monkeypatch.setattr(jira, "get_issue_snapshot", snapshot)
    cid = _handover(env, **proof)["conversation"]["id"]
    try:
        assert entered.wait(5)
        assert runner.runner.active_for_conversation(cid).phase == "preparing"
        assert env.client.post(f"/api/jira-agent/conversations/{cid}/cancel").status_code == 200
        turn = _latest(_wait(env, cid, _done))
        assert turn["status"] == "cancelled" and turn["recovery_attempts"] == 1
        assert env.fake.turns == 0 and not env.jira.comments
    finally:
        release.set()
