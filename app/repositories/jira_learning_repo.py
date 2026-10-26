"""SQLite operations for the independent Jira knowledge-learning queue."""
from __future__ import annotations

import json
import sqlite3

from app.repositories.base import dumps_json, loads_json, new_id, row_to_dict
from app.timeutil import beijing_timestamp

_JOB_FIELDS = {
    "status", "source_path", "source_sha256", "acquisition_status",
    "thread_id", "error", "started_at", "finished_at",
    "repository_only", "cancel_requested", "cancelled_at", "phase",
}
_TURN_FIELDS = {
    "status", "codex_turn_id", "response_text", "error", "started_at", "finished_at",
    "input_text", "snapshot_json", "revision_ids_json", "cancelled_at",
}


def _one(conn: sqlite3.Connection, sql: str, params: tuple) -> dict | None:
    row = conn.execute(sql, params).fetchone()
    return row_to_dict(row) if row else None


def create_job(
    conn: sqlite3.Connection,
    issue_key: str,
    group: str,
    username: str,
    *,
    parent_job_id: str = "",
    instructions: str = "",
    repository_only: bool = False,
    issue_keys: list[str] | None = None,
    title: str = "",
    request_id: str = "",
    request_hash: str = "",
) -> dict:
    job_id = new_id("jlk")
    now = beijing_timestamp()
    conn.execute(
        """INSERT INTO jira_learning_jobs
           (id, issue_key, agent_group, requested_by, parent_job_id, created_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (
            job_id, issue_key, group, username, parent_job_id, now,
        ),
    )
    update_job(conn, job_id, repository_only=int(repository_only))
    keys = list(dict.fromkeys(issue_keys or [issue_key]))
    conn.executemany('INSERT INTO jira_learning_members VALUES (?,?,?)',
                     [(job_id, key, position) for position, key in enumerate(keys)])
    conn.execute('UPDATE jira_learning_jobs SET title=?, request_id=?, request_hash=? WHERE id=?',
                 (title or (f'{keys[0]} 等 {len(keys)} 张 Jira 联合学习' if len(keys) > 1 else f'{keys[0]} 知识学习'), request_id, request_hash, job_id))
    text = instructions.strip() or "结合所选 Jira，整理可复用的 skill 改进"
    turn = create_turn(
        conn, job_id, "initial", username, text, status="queued", created_at=now,
    )
    add_event(
        conn, job_id, turn["id"], "user_message",
        {"author": username, "text": text, "mode": "initial"},
        created_at=now,
    )
    return get_job(conn, job_id)  # type: ignore[return-value]


def get_job(conn: sqlite3.Connection, job_id: str) -> dict | None:
    job = _one(conn, "SELECT * FROM jira_learning_jobs WHERE id = ?", (job_id,))
    return _members(conn, job) if job else None


def _members(conn: sqlite3.Connection, job: dict) -> dict:
    job['issue_keys'] = [row[0] for row in conn.execute(
        'SELECT issue_key FROM jira_learning_members WHERE job_id=? ORDER BY position', (job['id'],))] or [job['issue_key']]
    return job


def successor(conn: sqlite3.Connection, job_id: str) -> dict | None:
    return _one(conn, 'SELECT id FROM jira_learning_jobs WHERE parent_job_id=? ORDER BY rowid DESC LIMIT 1', (job_id,))


def requested_job(conn: sqlite3.Connection, username: str, request_id: str) -> dict | None:
    row = _one(conn, 'SELECT id FROM jira_learning_jobs WHERE requested_by=? AND request_id=?', (username, request_id))
    return get_job(conn, row['id']) if row else None


def get_turn(conn: sqlite3.Connection, turn_id: str) -> dict | None:
    return _one(conn, "SELECT * FROM jira_learning_turns WHERE id = ?", (turn_id,))


def list_turns(conn: sqlite3.Connection, job_id: str) -> list[dict]:
    return [
        row_to_dict(row) for row in conn.execute(
            "SELECT * FROM jira_learning_turns WHERE job_id = ? ORDER BY seq", (job_id,)
        ).fetchall()
    ]


def create_turn(
    conn: sqlite3.Connection,
    job_id: str,
    trigger: str,
    created_by: str,
    input_text: str,
    *,
    status: str = "queued",
    created_at: str = "",
) -> dict:
    seq = conn.execute(
        "SELECT COALESCE(MAX(seq), 0) + 1 FROM jira_learning_turns WHERE job_id = ?",
        (job_id,),
    ).fetchone()[0]
    turn_id = new_id("jlt")
    conn.execute(
        """INSERT INTO jira_learning_turns
           (id, job_id, seq, trigger, created_by, input_text, status, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            turn_id, job_id, seq, trigger, created_by, input_text, status,
            created_at or beijing_timestamp(),
        ),
    )
    return get_turn(conn, turn_id)  # type: ignore[return-value]


def queued_turn(conn: sqlite3.Connection, job_id: str) -> dict | None:
    return _one(
        conn,
        "SELECT * FROM jira_learning_turns WHERE job_id = ? AND status = 'queued'",
        (job_id,),
    )


def update_turn(conn: sqlite3.Connection, turn_id: str, **fields: str) -> None:
    if not fields or set(fields) - _TURN_FIELDS:
        raise ValueError("unknown learning turn field")
    assignments = ", ".join(f"{key} = ?" for key in fields)
    conn.execute(
        f"UPDATE jira_learning_turns SET {assignments} WHERE id = ?",
        (*fields.values(), turn_id),
    )


def claim_next_turn(conn: sqlite3.Connection) -> dict | None:
    row = conn.execute(
        """SELECT t.id, t.job_id, t.trigger
           FROM jira_learning_turns t
           WHERE t.status = 'queued'
             AND EXISTS (SELECT 1 FROM jira_learning_jobs j WHERE j.id = t.job_id AND j.cancel_requested = 0 AND j.cancelled_at = '')
             AND NOT EXISTS (
               SELECT 1 FROM jira_learning_turns active
               WHERE active.job_id = t.job_id AND active.status = 'running'
             )
           ORDER BY t.rowid LIMIT 1"""
    ).fetchone()
    if row is None:
        return None
    now = beijing_timestamp()
    changed = conn.execute(
        """UPDATE jira_learning_turns SET status = 'running', started_at = ?
           WHERE id = ? AND status = 'queued'""",
        (now, row["id"]),
    )
    if changed.rowcount != 1:
        return None
    status = "collecting" if row["trigger"] == "initial" else "running"
    conn.execute(
        """UPDATE jira_learning_jobs
           SET status = ?, error = '', finished_at = '',
               started_at = CASE WHEN started_at = '' THEN ? ELSE started_at END
           WHERE id = ?""",
        (status, now, row["job_id"]),
    )
    return {
        "job": get_job(conn, row["job_id"]),
        "turn": get_turn(conn, row["id"]),
    }


def ensure_initial_turns(conn: sqlite3.Connection) -> None:
    rows = conn.execute(
        """SELECT j.* FROM jira_learning_jobs j
           WHERE NOT EXISTS (
             SELECT 1 FROM jira_learning_turns t WHERE t.job_id = j.id
           ) ORDER BY j.rowid"""
    ).fetchall()
    for row in rows:
        status = row["status"]
        turn_status = (
            "queued" if status == "queued"
            else "running" if status in ("collecting", "running")
            else status
        )
        turn = create_turn(
            conn, row["id"], "initial", row["requested_by"], "发起知识学习",
            status=turn_status, created_at=row["created_at"],
        )
        fields = {
            "codex_turn_id": row["codex_turn_id"],
            "error": row["error"],
            "started_at": row["started_at"],
            "finished_at": row["finished_at"],
        }
        update_turn(conn, turn["id"], **fields)
        add_event(
            conn, row["id"], turn["id"], "user_message",
            {"author": row["requested_by"], "text": "发起知识学习", "mode": "initial"},
            created_at=row["created_at"],
        )
        if turn_status in ("completed", "failed"):
            text = "历史任务未保存详细对话过程"
            if turn_status == "failed" and row["error"]:
                text += "：" + row["error"]
            add_event(
                conn, row["id"], turn["id"], "error" if turn_status == "failed" else "status",
                {"text": text}, created_at=row["finished_at"] or row["created_at"],
            )


def add_event(
    conn: sqlite3.Connection,
    job_id: str,
    turn_id: str,
    kind: str,
    payload: dict,
    *,
    item_id: str = "",
    created_at: str = "",
) -> dict:
    if item_id:
        existing = _one(
            conn,
            "SELECT * FROM jira_learning_events WHERE job_id = ? AND item_id = ?",
            (job_id, item_id),
        )
        if existing is not None:
            if existing['kind'] != kind or loads_json(existing['payload_json'], {}) != payload:
                seq = conn.execute("SELECT COALESCE(MAX(seq),0)+1 FROM jira_learning_events WHERE job_id=?", (job_id,)).fetchone()[0]
                conn.execute(
                    "UPDATE jira_learning_events SET kind=?,payload_json=?,seq=? WHERE id=?",
                    (kind, dumps_json(payload), seq, existing["id"]),
                )
                existing['seq'] = seq
            existing["kind"] = kind
            existing["payload"] = payload
            return existing
    now = created_at or beijing_timestamp()
    seq = conn.execute(
        "SELECT COALESCE(MAX(seq), 0) + 1 FROM jira_learning_events WHERE job_id = ?",
        (job_id,),
    ).fetchone()[0]
    event_id = new_id("jle")
    conn.execute(
        """INSERT INTO jira_learning_events
           (id, job_id, turn_id, seq, kind, item_id, payload_json, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            event_id, job_id, turn_id, seq, kind, item_id, dumps_json(payload),
            now,
        ),
    )
    return {
        "id": event_id, "job_id": job_id, "turn_id": turn_id, "seq": seq,
        "kind": kind, "item_id": item_id, "payload": payload,
        "created_at": now,
    }


def list_events(conn: sqlite3.Connection, job_id: str, after: int = 0) -> list[dict]:
    result = []
    for row in conn.execute(
        "SELECT * FROM jira_learning_events WHERE job_id = ? AND seq > ? ORDER BY seq", (job_id, after)
    ).fetchall():
        item = row_to_dict(row)
        item["payload"] = loads_json(item.pop("payload_json"), {})
        result.append(item)
    return result


def has_queued_turn(conn: sqlite3.Connection, job_id: str) -> bool:
    return queued_turn(conn, job_id) is not None


def list_jobs(conn: sqlite3.Connection, group: str = "", limit: int = 100,
              offset: int = 0, issue_key: str = "") -> list[dict]:
    return [_members(conn, row_to_dict(row)) for row in conn.execute(
        """SELECT j.*,
            (SELECT COUNT(*) FROM jira_learning_proposals p WHERE p.job_id=j.id
             AND p.status='pending' AND p.superseded_by='' AND p.kind IN ('skill_update', 'new_skill')) AS pending_count,
            (SELECT COUNT(*) FROM jira_learning_proposals p WHERE p.job_id=j.id
             AND p.status='approved' AND p.superseded_by='' AND p.kind IN ('skill_update', 'new_skill')) AS update_count
           FROM jira_learning_jobs j
           WHERE (?='' OR agent_group=?) AND (?='' OR EXISTS (
             SELECT 1 FROM jira_learning_members m WHERE m.job_id=j.id AND m.issue_key=?))
           ORDER BY j.rowid DESC LIMIT ? OFFSET ?""",
        (group, group, issue_key, issue_key, limit, offset)).fetchall()]


def update_job(conn: sqlite3.Connection, job_id: str, **fields: str) -> None:
    if not fields or set(fields) - _JOB_FIELDS:
        raise ValueError("unknown learning job field")
    assignments = ", ".join(f"{key} = ?" for key in fields)
    conn.execute(
        f"UPDATE jira_learning_jobs SET {assignments} WHERE id = ?",
        (*fields.values(), job_id),
    )


def add_proposals(conn: sqlite3.Connection, job_id: str, proposals: list[dict], *, turn_id: str = "") -> None:
    now = beijing_timestamp()
    turn = get_turn(conn, turn_id) if turn_id else None
    snapshot = loads_json((turn or {}).get('snapshot_json') or '{}', {})
    repository = snapshot.get('repository') or {}
    requested = loads_json((turn or {}).get('revision_ids_json') or '[]', [])
    for item in proposals:
        previous = None
        if item.get('previous_id'):
            previous = get_proposal(conn, item['previous_id'])
            if previous is None or previous['job_id'] != job_id or previous.get('superseded_by'):
                raise ValueError('提案引用的旧版本无效')
        if requested and (not previous or previous['id'] not in requested):
            raise ValueError('退回整理只能修订指定提案')
        proposal_id = new_id('jlp')
        files = item.get('files') or []
        paths = {f['path'] for f in files}
        for existing in list_proposals(conn, job_id):
            if existing['superseded_by'] or existing['status'] == 'rejected' or (previous and existing['id'] == previous['id']):
                continue
            if paths.intersection(f['path'] for f in existing['files']):
                raise ValueError('同一任务的文件修改需集中在一个提案中；请修订原提案：' + existing['id'])
        # Relations are calculated from current proposal states when read.
        conn.execute(
            """INSERT INTO jira_learning_proposals
               (id, job_id, turn_id, kind, title, summary, target_path, proposed_text, patch_text,
                applicability, evidence_json, limitations, validation, created_at, updated_at,
                files_json, dependencies_json, base_commit, repository_ref, previous_id, version, material_gaps_json)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (proposal_id, job_id, turn_id, item['kind'], item['title'], item['summary'], item['target_path'],
             item['proposed_text'], item.get('patch_text') or '', item['applicability'],
             dumps_json(item['evidence_ids']), item['limitations'], item['validation'], now, now,
             dumps_json(files), dumps_json(item.get('dependencies') or {}), repository.get('commit') or '',
             repository.get('ref') or '', previous['id'] if previous else '',
             previous['version'] + 1 if previous else 1, dumps_json(item.get('material_gaps', []))))
        if previous:
            conn.execute('UPDATE jira_learning_proposals SET superseded_by=? WHERE id=?', (proposal_id, previous['id']))


def _proposal(row: sqlite3.Row) -> dict:
    item = row_to_dict(row)
    item["evidence_ids"] = loads_json(item.pop("evidence_json"), [])
    item.pop("warnings_json", None)
    for key, default in (("files", []), ("dependencies", {}), ("material_gaps", [])):
        item[key] = loads_json(item.pop(key + "_json"), default)
    return item


def get_proposal(conn: sqlite3.Connection, proposal_id: str) -> dict | None:
    row = conn.execute(
        "SELECT * FROM jira_learning_proposals WHERE id = ?", (proposal_id,)
    ).fetchone()
    return _proposal(row) if row else None


def list_proposals(conn: sqlite3.Connection, job_id: str) -> list[dict]:
    return [
        _proposal(row) for row in conn.execute(
            "SELECT * FROM jira_learning_proposals WHERE job_id = ? ORDER BY rowid",
            (job_id,),
        ).fetchall()
    ]


def review(conn: sqlite3.Connection, proposal_id: str, reviewer: str, decision: str, note: str) -> bool:
    now = beijing_timestamp()
    changed = conn.execute(
        """UPDATE jira_learning_proposals SET status = ?, updated_at = ?
           WHERE id = ? AND status = 'pending' AND superseded_by='' AND kind!='do_not_publish'""",
        (decision, now, proposal_id),
    )
    if changed.rowcount != 1:
        return False
    conn.execute(
        """INSERT INTO jira_learning_reviews
           (id, proposal_id, reviewer, decision, note, created_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (new_id("jlr"), proposal_id, reviewer, decision, note, now),
    )
    return True


def record_publication(
    conn: sqlite3.Connection, proposal_id: str, reviewer: str, commit: str, note: str
) -> bool:
    now = beijing_timestamp()
    changed = conn.execute(
        """UPDATE jira_learning_proposals
           SET status = 'published', published_commit = ?, updated_at = ?
           WHERE id = ? AND status = 'approved' AND superseded_by=''""",
        (commit, now, proposal_id),
    )
    if changed.rowcount != 1:
        return False
    conn.execute(
        """INSERT INTO jira_learning_reviews
           (id, proposal_id, reviewer, decision, note, commit_sha, created_at)
           VALUES (?, ?, ?, 'published', ?, ?, ?)""",
        (new_id("jlr"), proposal_id, reviewer, note, commit, now),
    )
    return True


def list_reviews(conn: sqlite3.Connection, proposal_id: str) -> list[dict]:
    return [
        row_to_dict(row) for row in conn.execute(
            "SELECT * FROM jira_learning_reviews WHERE proposal_id = ? ORDER BY rowid",
            (proposal_id,),
        ).fetchall()
    ]


def conversation_messages(conn: sqlite3.Connection, issue_key: str, group: str) -> list[dict]:
    rows = conn.execute(
        """SELECT c.id AS conversation_id, c.owner, e.id, e.turn_id,
                  e.created_at, e.seq, e.kind, e.payload_json, t.trigger, t.input_text, t.status AS turn_status, t.seq AS turn_seq
           FROM jira_agent_conversations c
           JOIN jira_agent_events e ON e.conversation_id = c.id
           LEFT JOIN jira_agent_turns t ON t.id = e.turn_id
           WHERE c.source_kind = 'jira' AND c.issue_key = ? AND c.agent_group = ? AND e.kind IN ('user_message', 'agent_message', 'reasoning', 'command', 'file_change', 'tool', 'error')
           ORDER BY c.created_at, e.seq""",
        (issue_key, group),
    ).fetchall()
    messages = []
    for row in rows:
        payload = json.loads(row["payload_json"])
        author = payload.get("author") or ""
        human = row['kind'] == 'user_message'
        text = payload.get("text") or ""
        files = payload.get("files") or []
        if human and payload.get("mode") == "handover" and not (row["input_text"] or "").strip():
            text = ""  # synthetic hand-over text is not a participant instruction
        if row['kind'] in ('command', 'file_change', 'tool'):
            text = json.dumps(payload, ensure_ascii=False)
        if text or files:
            messages.append({
                "id": row["id"], "conversation_id": row["conversation_id"],
                "turn_id": row["turn_id"], "created_at": row["created_at"],
                "author": author if human else 'agent', "text": text, "files": files,
                "kind": 'owner_message' if human else 'agent_tool' if row['kind'] in ('command', 'file_change', 'tool') else 'agent_message',
                "event_kind": row['kind'], "event_seq": row['seq'], "turn_seq": row['turn_seq'],
                "turn_status": row['turn_status'],
            })
    return messages


def agent_comment_ids(conn: sqlite3.Connection, issue_key: str) -> set[str]:
    return {
        row[0] for row in conn.execute(
            """SELECT t.comment_id FROM jira_agent_turns t
               JOIN jira_agent_conversations c ON c.id = t.conversation_id
               WHERE c.source_kind = 'jira' AND c.issue_key = ? AND t.comment_id != ''""",
            (issue_key,),
        ).fetchall()
    }


def agent_results(conn: sqlite3.Connection, issue_key: str, group: str) -> list[dict]:
    rows = conn.execute(
        """SELECT c.id AS conversation_id, t.id AS turn_id, t.seq AS turn_seq,
                  t.result_json, t.status, t.finished_at, t.created_at
           FROM jira_agent_conversations c
           JOIN jira_agent_turns t ON t.conversation_id = c.id
           WHERE c.source_kind = 'jira' AND c.issue_key = ? AND c.agent_group = ?
             AND t.result_json != ''
           ORDER BY c.created_at, t.seq""",
        (issue_key, group),
    ).fetchall()
    results = []
    for row in rows:
        try:
            result = json.loads(row["result_json"])
        except ValueError:
            continue
        if isinstance(result, dict):
            results.append({
                "conversation_id": row["conversation_id"],
                "turn_id": row["turn_id"], "turn_seq": row["turn_seq"],
                "result": result, "turn_status": row['status'], "created_at": row['finished_at'] or row['created_at'],
            })
    return results


def learning_files(conn: sqlite3.Connection, issue_key: str, group: str) -> list[dict]:
    return [
        row_to_dict(row) for row in conn.execute(
            """SELECT f.* FROM jira_agent_files f
               JOIN jira_agent_conversations c ON c.id = f.conversation_id
               WHERE c.source_kind = 'jira' AND c.issue_key = ? AND c.agent_group = ?
                 AND f.local_path != '' AND f.source IN ('upload', 'artifact')
               ORDER BY c.created_at, f.rowid""",
            (issue_key, group),
        ).fetchall()
    ]


def add_issue(conn: sqlite3.Connection, issue: dict, group: str, username: str) -> None:
    conn.execute("""INSERT INTO jira_learning_issues VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(issue_key, agent_group) DO UPDATE SET issue_json=excluded.issue_json""",
                 (issue['key'], group, dumps_json(issue), username, beijing_timestamp()))


def list_issues(conn: sqlite3.Connection, group: str) -> list[dict]:
    return [loads_json(row['issue_json'], {}) | {'learning_origin': 'historical'}
            for row in conn.execute('SELECT issue_json FROM jira_learning_issues WHERE agent_group=? ORDER BY added_at DESC', (group,))]


def running_work(conn: sqlite3.Connection) -> dict | None:
    turn = _one(conn, "SELECT * FROM jira_learning_turns WHERE status='running' ORDER BY rowid LIMIT 1", ())
    return {'turn': turn, 'job': get_job(conn, turn['job_id'])} if turn else None


def cancel_queued(conn: sqlite3.Connection, job_id: str) -> None:
    now = beijing_timestamp()
    conn.execute("UPDATE jira_learning_turns SET status='failed', cancelled_at=?, finished_at=? WHERE job_id=? AND status='queued'", (now, now, job_id))


def list_deployments(conn: sqlite3.Connection, proposal_id: str) -> list[dict]:
    return [row_to_dict(row) | {'verification': 'manual'} for row in conn.execute(
        '''SELECT d.* FROM jira_learning_deployment_events d
           JOIN jira_learning_proposals p ON p.published_commit=d.commit_sha
           JOIN jira_learning_jobs j ON j.id=p.job_id AND j.agent_group=d.agent_group
           WHERE p.id=? ORDER BY d.rowid DESC''', (proposal_id,))]


def add_deployment(conn: sqlite3.Connection, group: str, target: str, commit: str, result: str, note: str, username: str) -> None:
    conn.execute('INSERT INTO jira_learning_deployment_events VALUES (?,?,?,?,?,?,?,?)',
                 (new_id('jld'), group, target, commit, result, note, username, beijing_timestamp()))


def issue_overview(conn: sqlite3.Connection, group: str) -> list[dict]:
    rows = conn.execute("""
        SELECT m.issue_key, j.id, COUNT(*) OVER (PARTITION BY m.issue_key) AS participation_count,
          ROW_NUMBER() OVER (PARTITION BY m.issue_key ORDER BY j.rowid DESC) AS rank,
          SUM((SELECT COUNT(*) FROM jira_learning_proposals p WHERE p.job_id=j.id
               AND p.status='pending' AND p.superseded_by='' AND p.kind IN ('skill_update','new_skill')))
               OVER (PARTITION BY m.issue_key) AS pending_count,
          SUM((SELECT COUNT(*) FROM jira_learning_proposals p WHERE p.job_id=j.id
               AND p.status='approved' AND p.superseded_by='' AND p.kind IN ('skill_update','new_skill')))
               OVER (PARTITION BY m.issue_key) AS update_count
        FROM jira_learning_members m JOIN jira_learning_jobs j ON j.id=m.job_id WHERE j.agent_group=?
    """, (group,)).fetchall()
    return [get_job(conn, row['id']) | dict(row) for row in rows if row['rank'] == 1]


def related_proposals(conn: sqlite3.Connection, job_id: str) -> list[dict]:
    # Keep the actual files and patch, not just a potentially misleading summary.
    return [_proposal(row) for row in conn.execute("""
        SELECT p.* FROM jira_learning_proposals p JOIN jira_learning_jobs j ON j.id=p.job_id
        WHERE p.job_id != ? AND j.agent_group=(SELECT agent_group FROM jira_learning_jobs WHERE id=?)
          AND p.status IN ('pending', 'approved') AND p.superseded_by=''
          AND p.kind IN ('skill_update', 'new_skill')
        ORDER BY p.rowid DESC LIMIT 100
    """, (job_id, job_id))]
