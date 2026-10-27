"""Bounded B→execution-host probes and transactionally consumed submission receipts."""
from __future__ import annotations

import asyncio
import base64
import json
import shlex
import sqlite3
import time
from pathlib import Path

from app.api.errors import ApiError, AuthzError
from app.db.connection import transaction
from app.domain import jira_agent, jira_execution as domain
from app.integrations.codex_app_server import CodexAppServerClient, CodexAppServerError
from app.repositories import jira_agent_repo as repo, jira_testing_repo as tests
from app.repositories.base import new_id, dumps_json
from app.timeutil import beijing_timestamp

PROBE_TIMEOUT_MS = 20_000
PROBE_CONCURRENCY = 4
RECEIPT_SECONDS = 600
PROBE_SCRIPT = (Path(__file__).parents[1] / "resources/jira_agent/check_dataset.py").read_text()


def _db():
    from app.services.jira_agent_runner import open_db
    return open_db()


def policy_hash(group):
    return domain.digest({"endpoint": group.ws_url, "key": group.ssh_key_path, "dataset": group.dataset_policy})


def test_scope(conn, kind, body):
    if kind == "test":
        row = conn.execute("SELECT case_id FROM jira_test_versions WHERE id=?", (body["version_id"],)).fetchone()
        if not row or row["case_id"] != body["case_id"]:
            raise ApiError(404, "测试用例版本不存在")
        return {"kind": kind, "versions": [[body["case_id"], body["version_id"]]]}
    cases = tests.cases(conn)
    selected = body.get("case_ids") or [row["id"] for row in cases]
    available = {row["id"]: row["version_id"] for row in cases}
    if not selected or len(selected) > 100 or len(selected) != len(set(selected)):
        raise ValueError("请选择 1 至 100 个不重复用例")
    if set(selected) - available.keys():
        raise ApiError(404, "所选测试用例不存在")
    return {"kind": "suite", "versions": [[key, available[key]] for key in selected]}


async def prepare(user, body):
    from app.services import jira_agent_service as service, jira_agent_ssh_key as keys
    from app.services.jira_agent_runner import load_groups
    context = body["context"]
    kind = context.get("kind")
    group = load_groups().get(body["agent_group"])
    if not group:
        raise ApiError(404, "数字员工组不存在")
    with _db() as conn:
        if kind == "jira":
            issue = await service.fetch_issue(context.get("issue_key", ""))
            if not await service._owner_can_view_issue(user, issue, conn):
                raise ApiError(404, "JIRA 工单不存在或无权访问")
            service.check_actor(user, issue)
            owner = service.assignee_name(issue)
            if not owner:
                raise ApiError(409, "工单没有 assignee")
            if jira_agent.group_for_issue(load_groups(), issue["components"]).name != group.name:
                raise ApiError(409, "工单所属分析组已变化")
            scope = {"kind": "jira", "issue_key": issue["key"], "owner": owner.casefold()}
        elif kind in ("test", "suite"):
            if not service._is_rm(user):
                raise AuthzError("只有 RM 可以启动测试")
            scope = test_scope(conn, kind, context)
        else:
            raise ValueError("无效的预检查用途")
        config = domain.normalize(body["execution"])
        targets = config["targets"] if config["mode"] == "custom" else [
            {"target": m["ssh_target"], "purpose": m["description"]} for m in repo.list_machines(conn, group.name)]
        if not targets:
            raise ApiError(409, "系统机器列表为空，请联系 RM 添加机器，或自填 user@host")
        key_ids = body.get("key_sessions") or {}
        proofs = {}
        for item in config["targets"]:
            target = item["target"]
            session_id = key_ids.get(target, "")
            session = keys.sessions.validate(user, session_id, group, target)
            if conn.execute("SELECT 1 FROM jira_agent_key_claims WHERE session_id=?", (session_id,)).fetchone():
                raise ApiError(409, f"{target} 的公钥验证已用于其他交单，请重新验证")
            proofs[target] = {"id": session_id, "expires_at": time.time() + max(0, RECEIPT_SECONDS - (time.monotonic() - session.finished))}
    return group, scope, config, targets, proofs


async def probe(group, target, *, check_dataset=True):
    failed = {"status": "fail", "code": "ssh_failed", "message": "无法从服务器 B 免密登录"}
    unknown = {"status": "unknown", "code": "not_checked", "message": "SSH 未通过，未检查数据"}
    skipped = {"status": "skipped", "code": "system_machine", "message": "系统机器不做共享数据预检查"}
    result = {"target": target, "ssh": failed, "dataset": unknown if check_dataset else skipped, "checked_at": beijing_timestamp()}
    argv = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", "-o", "StrictHostKeyChecking=accept-new"]
    if group.ssh_key_path:
        argv += ["-i", group.ssh_key_path]
    argv += [target]
    try:
        async with CodexAppServerClient(group.ws_url, group.ws_token, open_timeout=5, request_timeout=30) as client:
            login = await client.exec_command([*argv, "true"], timeout_ms=PROBE_TIMEOUT_MS)
            if login.get("exitCode") != 0:
                result["ssh"] = {**failed, "message": (login.get("stderr") or failed["message"])[:500]}
                return result
            result["ssh"] = {"status": "pass", "code": "connected", "message": "免密登录通过"}
            if not check_dataset:
                return result
            policy = group.dataset_policy or domain.dataset_policy({})
            if not policy["path"]:
                result["dataset"] = {"status": "unknown", "code": "not_configured", "message": "未配置共享数据目录及检查标准"}
                return result
            encoded = base64.b64encode(json.dumps(policy).encode()).decode()
            # SSH runs its command through a remote shell: quote every argument separately.
            remote = shlex.join(["python3", "-c", PROBE_SCRIPT, encoded])
            output = await client.exec_command([*argv, remote], timeout_ms=PROBE_TIMEOUT_MS)
            if output.get("exitCode") == 0:
                data = json.loads(output.get("stdout", "")[:65536])
                if not isinstance(data, dict) or data.get("status") not in ("pass", "fail", "unknown") or not isinstance(data.get("message"), str):
                    raise ValueError("invalid probe output")
                result["dataset"] = data
            else:
                result["dataset"] = {"status": "unknown", "code": "probe_failed", "message": "数据检查超时或探测工具不可用"}
    except (CodexAppServerError, ValueError, OSError, asyncio.TimeoutError):
        if check_dataset and result["ssh"]["status"] == "pass":
            result["dataset"] = {"status": "unknown", "code": "probe_failed", "message": "数据检查未完成，请重试"}
    return result


async def inspect(group, config, targets, on_result=None):
    semaphore = asyncio.Semaphore(PROBE_CONCURRENCY)
    check_dataset = config["mode"] == "custom"

    async def one(item):
        async with semaphore:
            result = await probe(group, item["target"], check_dataset=check_dataset)
            result["purpose"] = item.get("purpose", "")
            if on_result:
                on_result(result)
            return result

    tasks = [asyncio.create_task(one(item)) for item in targets]
    try:
        results = await asyncio.gather(*tasks)
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
    return {"results": results, "dataset_path": group.dataset_policy.get("path", "") if check_dataset else "",
            "checked_at": beijing_timestamp(), **domain.verdict(config, results)}


async def create(user, prepared, on_result=None):
    group, scope, config, targets, proofs = prepared
    snapshot = await inspect(group, config, targets, on_result)
    expires = min([time.time() + RECEIPT_SECONDS, *[p["expires_at"] for p in proofs.values()]])
    pid = new_id("jpf")
    with _db() as conn, transaction(conn):
        conn.execute("""INSERT INTO jira_agent_preflights
            (id,username,agent_group,scope_json,execution_json,snapshot_json,key_sessions_json,policy_hash,expires_at,created_at)
            VALUES(?,?,?,?,?,?,?,?,?,?)""", (pid, user["username"].casefold(), group.name, dumps_json(scope),
            dumps_json(config), dumps_json(snapshot), dumps_json(proofs), policy_hash(group), expires, beijing_timestamp()))
    return {"id": pid, "execution": config, "snapshot": snapshot, "expires_at": expires}


def _submission_hash(body):
    return domain.digest({k: v for k, v in body.items() if k != "ssh_key_session_id"})


def previous(conn, user, body):
    request_id = body.get("request_id", "")
    if not request_id:
        return None
    row = conn.execute("SELECT * FROM jira_agent_preflights WHERE username=? AND request_id=?",
                       (user["username"].casefold(), request_id)).fetchone()
    if row and row["submission_hash"] != _submission_hash(body):
        raise ApiError(409, "请求标识已用于不同内容，请重新提交")
    return dict(row) if row else None


def resolve(conn, user, group, body, scope):
    row = conn.execute("SELECT * FROM jira_agent_preflights WHERE id=? AND username=?",
                       (body.get("preflight_id", ""), user["username"].casefold())).fetchone()
    if not row:
        raise ApiError(409, "请先完成执行机器与数据检查；旧页面请刷新")
    row = dict(row)
    if row["consumed_by"] or row["expires_at"] <= time.time():
        raise ApiError(409, "预检查已使用或过期，请重新检查")
    if row["agent_group"] != group.name or json.loads(row["scope_json"]) != scope:
        raise ApiError(409, "预检查对应的工单、负责人或测试版本已变化，请重新检查")
    if row["policy_hash"] != policy_hash(group):
        raise ApiError(409, "服务器或数据检查配置已变化，请重新检查")
    config, snapshot = json.loads(row["execution_json"]), json.loads(row["snapshot_json"])
    if body.get("machine") and body["machine"] != domain.legacy_machine(config):
        raise ApiError(409, "提交机器与预检查清单不一致")
    for proof in json.loads(row["key_sessions_json"]).values():
        if conn.execute("SELECT 1 FROM jira_agent_key_claims WHERE session_id=?", (proof["id"],)).fetchone():
            raise ApiError(409, "公钥验证已用于其他交单，请重新验证")
    if config["mode"] == "auto":
        current = {m["ssh_target"] for m in repo.list_machines(conn, group.name)}
        if current != {r["target"] for r in snapshot["results"]}:
            raise ApiError(409, "系统机器列表已变化，请重新检查")
    if not snapshot["can_submit"]:
        raise ApiError(409, "必要机器的 SSH 检查未通过，请修改机器或重试")
    if snapshot["warning_ids"] and set(body.get("acknowledged_issue_ids", [])) != set(snapshot["warning_ids"]):
        raise ApiError(409, "请确认本次检查的异常及执行限制")
    if not body.get("request_id"):
        raise ValueError("提交需要 request_id")
    row.update(execution=config, snapshot={**snapshot, "acknowledged_issue_ids": body.get("acknowledged_issue_ids", [])})
    return row


def consume(conn, user, body, receipt, resource_id):
    # Caller owns the task-creation transaction. No remote I/O or in-memory consumption here.
    if conn.execute("""UPDATE jira_agent_preflights SET consumed_by=?, request_id=?, submission_hash=?
        WHERE id=? AND consumed_by='' AND expires_at>?""", (resource_id, body["request_id"],
        _submission_hash(body), receipt["id"], time.time())).rowcount != 1:
        raise ApiError(409, "预检查已使用或过期，请重新检查")
    try:
        for proof in json.loads(receipt["key_sessions_json"]).values():
            conn.execute("INSERT INTO jira_agent_key_claims VALUES(?,?)", (proof["id"], receipt["id"]))
    except sqlite3.IntegrityError as exc:
        raise ApiError(409, "公钥验证已用于其他交单，请重新验证") from exc


async def refresh(group, conversation, *, first_turn):
    config = domain.configuration(conversation)
    old = json.loads(conversation.get("execution_snapshot_json") or "{}")
    if config["mode"] == "custom":
        targets = config["targets"]
    else:
        with _db() as conn:
            current = repo.list_machines(conn, group.name)
        original = {r["target"] for r in old.get("results", [])}
        targets = [{"target": m["ssh_target"], "purpose": m["description"]} for m in current
                   if not first_turn or not old or m["ssh_target"] in original]
    snapshot = await inspect(group, config, targets)
    snapshot["acknowledged_issue_ids"] = old.get("acknowledged_issue_ids", [])
    return snapshot
