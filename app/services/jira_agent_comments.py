"""Versioned comment edits and reviewed publication."""
from __future__ import annotations

import asyncio
import hashlib
import json
import urllib.error

from app.api.errors import ApiError, AuthzError
from app.db.connection import transaction
from app.domain import jira_agent as domain
from app.integrations import jira
from app.repositories import jira_agent_repo as repo
from app.services import jira_agent_runner as execution
from app.services import jira_agent_service as service
from app.timeutil import beijing_timestamp

# One application worker. No database transaction is held during remote I/O.
BUSY: set[str] = set()


def visible_turn(conn, user, turn_id):
    turn = repo.get_turn(conn, turn_id)
    if not turn:
        raise ApiError(404, "轮次不存在")
    conversation = service._get_visible(conn, user, turn["conversation_id"])
    return turn, conversation


async def writable_turn(conn, user, turn_id):
    turn, conversation = visible_turn(conn, user, turn_id)
    service._check_not_deleting(conversation["id"])
    service.check_risk_lock(user, conversation["issue_key"], conn)
    if conversation["source_kind"] != "jira" or conversation["status"] != "open":
        raise ApiError(409, "仅开放的 JIRA 对话可以编辑和发送评论")
    issue = await service.fetch_issue(conversation["issue_key"])
    if not service.can_act(user, issue):
        raise AuthzError("只有当前 JIRA assignee 或 RM 可以编辑和发送评论")
    # Recheck after the network await, including a concurrently closed conversation.
    turn, conversation = visible_turn(conn, user, turn_id)
    service._check_not_deleting(conversation["id"])
    service.check_risk_lock(user, conversation["issue_key"], conn)
    if conversation["status"] != "open":
        raise ApiError(409, "对话已关闭")
    if turn["status"] != "completed" or not turn["result_json"]:
        raise ApiError(409, "请先完成本轮分析")
    if turn["delivery_status"] not in ("", "complete"):
        raise ApiError(409, "请先回收完整产物")
    return turn, conversation


def state(user, turn_id):
    conn = execution.open_db()
    try:
        turn, _ = visible_turn(conn, user, turn_id)
        row = conn.execute("SELECT * FROM jira_agent_comment_drafts WHERE turn_id=?", (turn_id,)).fetchone()
        publication = repo.comment_publication(conn, turn_id)
        uploads = [dict(r) for r in conn.execute(
            "SELECT file_id,name,state,attachment_id,error FROM jira_agent_comment_uploads WHERE turn_id=?", (turn_id,))]
        frozen = bool(publication) or bool(turn["comment_status"])
        return {"body": (publication["body"] if publication else turn["comment_body"]) if frozen else row["body"] if row else turn["comment_body"],
                "frozen": bool(frozen), "uploads": uploads,
                "revision": row["revision"] if row else 0,
                "selected_file_ids": [u["file_id"] for u in uploads] if frozen else json.loads(row["selected_files_json"]) if row else [],
                "draft_status": row["status"] if row else "", "draft_error": row["error"] if row else "",
                "turn": service._turn_view(conn, turn)}
    finally:
        conn.close()


def _selected_files(conn, user, conversation, turn_id, file_ids, *, hash_content=True):
    records = []
    for fid in dict.fromkeys(file_ids):
        record = repo.get_file(conn, fid)
        if not record or record["turn_id"] != turn_id or record["source"] != "artifact":
            raise ApiError(400, "只能上传本轮产物")
        path, _ = service.file_for_download(user, conversation["id"], fid)
        if hash_content:
            hasher = hashlib.sha256()
            with path.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    hasher.update(chunk)
            digest = hasher.hexdigest()
        else:
            digest = ""
        records.append((record, path, digest))
    return records


def _record_revision(conn, turn_id, body, file_ids, editor, source):
    row = conn.execute("SELECT revision FROM jira_agent_comment_drafts WHERE turn_id=?", (turn_id,)).fetchone()
    revision = row["revision"] + 1
    now = beijing_timestamp()
    encoded = json.dumps(list(dict.fromkeys(file_ids)))
    conn.execute("UPDATE jira_agent_comment_drafts SET body=?,selected_files_json=?,revision=?,updated_at=? WHERE turn_id=?",
                 (body, encoded, revision, now, turn_id))
    conn.execute("INSERT INTO jira_agent_comment_revisions VALUES (?,?,?,?,?,?,?)",
                 (turn_id, revision, source, body, encoded, editor, now))


def _ensure_draft(conn, turn_id):
    turn = repo.get_turn(conn, turn_id)
    inserted = conn.execute(
        "INSERT OR IGNORE INTO jira_agent_comment_drafts (turn_id,body,created_by,updated_at) VALUES (?,?,?,?)",
        (turn_id, turn["comment_body"], turn["created_by"], beijing_timestamp())).rowcount
    if inserted:
        conn.execute("INSERT INTO jira_agent_comment_revisions VALUES (?,0,'generated',?,'[]',?,?)",
                     (turn_id, turn["comment_body"], turn["created_by"], beijing_timestamp()))


def _check_revision(conn, turn_id, expected):
    _ensure_draft(conn, turn_id)
    row = conn.execute("SELECT * FROM jira_agent_comment_drafts WHERE turn_id=?", (turn_id,)).fetchone()
    if not row or row["revision"] != expected:
        raise ApiError(409, "评论已被其他窗口或用户修改，请载入最新版本后再操作；当前编辑尚未覆盖")
    if row["status"] == "running":
        raise ApiError(409, "正在生成评论，请稍后编辑")
    return row


async def edit(user, turn_id, body, file_ids, revision):
    conn = execution.open_db()
    try:
        _, conversation = await writable_turn(conn, user, turn_id)
        if turn_id in BUSY:
            raise ApiError(409, "本轮正在生成或发送评论")
        await asyncio.to_thread(_selected_files, conn, user, conversation, turn_id, file_ids, hash_content=False)
        with transaction(conn):
            turn, current = visible_turn(conn, user, turn_id)
            service._check_not_deleting(current["id"])
            service.check_risk_lock(user, current["issue_key"], conn)
            if current["status"] != "open":
                raise ApiError(409, "对话已关闭，未保存编辑")
            if turn["comment_status"] or repo.comment_publication(conn, turn_id):
                raise ApiError(409, "本轮已提交发送，不能修改评论")
            row = _check_revision(conn, turn_id, revision)
            if row["body"] != body or set(json.loads(row["selected_files_json"])) != set(file_ids):
                _record_revision(conn, turn_id, body, file_ids, user["username"], "edited")
        return state(user, turn_id)
    finally:
        conn.close()


def history(user, turn_id, before=0):
    conn = execution.open_db()
    try:
        visible_turn(conn, user, turn_id)
        rows = conn.execute("SELECT * FROM jira_agent_comment_revisions WHERE turn_id=? AND (?=0 OR revision<?) "
                            "ORDER BY revision DESC LIMIT 101", (turn_id, before, before)).fetchall()
        items = [{**dict(row), "file_ids": json.loads(row["file_ids_json"])} for row in rows[:100]]
        for item in items:
            item.pop("file_ids_json")
        return {"items": items, "next_before": items[-1]["revision"] if len(rows) > 100 else 0}
    finally:
        conn.close()


async def _reconcile_legacy(conn, turn, publication):
    """Historical uncertain sends can only be read back, never newly published."""
    if not publication and turn["comment_status"] in {"failed", "pending"}:
        with transaction(conn):
            publication = repo.prepare_comment(conn, turn["id"], turn["comment_body"], legacy=True)
    if not publication or publication["state"] not in {"sending", "uncertain"}:
        raise ApiError(409, "本轮没有可核对的历史发送请求")
    await execution.post_turn_comment(conn, turn["id"])


async def retry(user, turn_id):
    conn = execution.open_db()
    try:
        await writable_turn(conn, user, turn_id)
    finally:
        conn.close()
    current = state(user, turn_id)
    if not current["frozen"] or current["turn"]["comment_status"] != "failed":
        raise ApiError(409, "本轮没有需要核对的发送请求")
    return await send(user, turn_id, current["body"], current["selected_file_ids"], current["revision"])


async def send(user, turn_id, body, file_ids, revision=None):
    conn = execution.open_db()
    claimed = False
    try:
        turn, conversation = await writable_turn(conn, user, turn_id)
        if turn["comment_status"] == "posted":
            raise ApiError(409, "本轮已发送 JIRA 评论，不能再次发送")
        if turn_id in BUSY:
            raise ApiError(409, "本轮正在生成或发送评论")
        BUSY.add(turn_id)
        claimed = True
        publication = repo.comment_publication(conn, turn_id)
        if (publication and not publication["manual"]) or (not publication and turn["comment_status"]):
            # Historical ambiguous sends can only be reconciled, not sent again.
            if body != turn["comment_body"] or file_ids:
                raise ApiError(409, "历史发送内容不能修改，请核对原发布结果")
            await _reconcile_legacy(conn, turn, publication)
            return state(user, turn_id)
        if not publication:
            if not body.strip() or len(body) > 30000:
                raise ApiError(400, "评论不能为空且不能超过 30000 字")
            files = await asyncio.to_thread(_selected_files, conn, user, conversation, turn_id, file_ids)
            with transaction(conn):
                _check_revision(conn, turn_id, revision)
                _record_revision(conn, turn_id, body.strip(), file_ids, user["username"], "sent")
                repo.prepare_comment(conn, turn_id, body.strip())
                conn.execute("UPDATE jira_agent_comment_publications SET manual=1 WHERE turn_id=?", (turn_id,))
                for record, _, digest in files:
                    # Unique stable filename permits reconciliation after a lost upload response.
                    name = f"{turn_id}-{record['id']}-{domain.safe_filename(record['name'])}"
                    conn.execute("INSERT INTO jira_agent_comment_uploads (turn_id,file_id,name,sha256) VALUES (?,?,?,?)",
                                 (turn_id, record["id"], name, digest))
                repo.update_turn(conn, turn_id, post_comment=1, comment_status="pending", comment_error="")
        elif body != publication["body"] or set(file_ids) != {r[0] for r in conn.execute(
                "SELECT file_id FROM jira_agent_comment_uploads WHERE turn_id=?", (turn_id,))}:
            raise ApiError(409, "已提交发送的正文和附件已锁定，请刷新并核对原请求")
        else:
            with transaction(conn):
                repo.update_turn(conn, turn_id, comment_status="pending")
        uploads = [dict(r) for r in conn.execute("SELECT * FROM jira_agent_comment_uploads WHERE turn_id=?", (turn_id,))]
        for upload in uploads:
            if upload["state"] == "posted":
                continue
            fid = upload["file_id"]
            attempted = upload["state"] in {"sending", "uncertain"}
            try:
                await writable_turn(conn, user, turn_id)
                path, _ = service.file_for_download(user, conversation["id"], fid)
                data = await asyncio.to_thread(path.read_bytes)
                if hashlib.sha256(data).hexdigest() != upload["sha256"]:
                    raise ValueError("产物内容已变化，停止发送")
                if upload["state"] in {"sending", "uncertain"}:
                    attachment_id = await asyncio.to_thread(jira.find_agent_attachment, conversation["issue_key"],
                                                            upload["name"], upload["sha256"])
                    if not attachment_id:
                        raise ValueError("附件上传结果待确认，请稍后核对；未重复上传")
                else:
                    with transaction(conn):
                        conn.execute("UPDATE jira_agent_comment_uploads SET state='sending',error='' WHERE turn_id=? AND file_id=?", (turn_id, fid))
                    attempted = True
                    attachment_id = await asyncio.to_thread(jira.upload_agent_attachment, conversation["issue_key"], upload["name"], data)
                with transaction(conn):
                    conn.execute("UPDATE jira_agent_comment_uploads SET state='posted',attachment_id=?,error='' WHERE turn_id=? AND file_id=?",
                                 (attachment_id, turn_id, fid))
            except Exception as exc:
                rejected = isinstance(exc, urllib.error.HTTPError) and exc.code in {400, 401, 403, 404, 405, 413, 415, 422, 429}
                error = execution._error_text(exc)
                with transaction(conn):
                    conn.execute("UPDATE jira_agent_comment_uploads SET state=?,error=? WHERE turn_id=? AND file_id=?",
                                 ("rejected" if rejected else "uncertain" if attempted else "ready", error, turn_id, fid))
                    repo.update_turn(conn, turn_id, comment_status="failed", comment_error="附件未全部上传：" + error)
                return state(user, turn_id)
        # All selected attachments are accounted for before the comment POST.
        try:
            await writable_turn(conn, user, turn_id)
        except Exception as exc:
            with transaction(conn):
                repo.update_turn(conn, turn_id, comment_status="failed", comment_error=execution._error_text(exc))
            raise
        await execution.post_turn_comment(conn, turn_id)
        return state(user, turn_id)
    finally:
        if claimed:
            BUSY.discard(turn_id)
        conn.close()
