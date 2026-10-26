"""RM-only learning, immutable proposal review and manual publication records."""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
from pathlib import Path

from app.api.errors import ApiError
from app.config import settings
from app.db.connection import transaction
from app.db.jira_agent_connection import connect_jira_agent
from app.domain import jira_learning as domain
from app.integrations import jira, knowledge_repository as knowledge
from app.repositories import jira_learning_repo as repo
from app.services.jira_learning_runner import runner
from app.services.jira_learning_archive import LearningArchive
from app.services import jira_learning_worker as worker, jira_learning_snapshots as snapshots
from app.timeutil import beijing_timestamp

_ISSUE_KEY = re.compile(r'^[A-Z][A-Z0-9_]*-[0-9]+$')
_COMMIT = re.compile(r'^[0-9a-fA-F]{7,40}$')


def _db():
    return connect_jira_agent(settings.jira_agent_database_url)


def _job_view(job: dict) -> dict:
    result = {key: value for key, value in job.items() if key not in (
        'source_path', 'source_sha256', 'thread_id', 'codex_turn_id', 'revision_feedback_json',
        'revision_of_proposal_id', 'knowledge_sha256', 'request_id', 'request_hash')}
    result.update(source_available=bool(job['source_path']),
                  can_message=bool(job['source_path'] and job['thread_id'] and not job.get('cancel_requested') and job.get('phase') != 'recovering'))
    if job.get('cancelled_at'):
        result['status'] = 'cancelled'
    elif job.get('cancel_requested'):
        result['status'] = 'cancelling'
    elif job.get('phase') == 'recovering':
        result['status'] = 'recovering'
    return result


def _editable(conn, job_id: str) -> dict:
    job = repo.get_job(conn, job_id)
    if not job:
        raise ApiError(404, '学习任务不存在')
    if repo.successor(conn, job_id):
        raise ApiError(409, '这是历史任务，请在最新任务继续操作')
    if job['phase'] == 'recovering' or job['cancel_requested']:
        raise ApiError(409, '正在核对或停止远端任务，请等待状态确认')
    return job


def preview_issues(query: str) -> dict:
    query = query.strip()
    if not query or len(query) > 4000:
        raise ValueError('请输入 Jira 编号或不超过 4000 字符的 JQL')
    if _ISSUE_KEY.fullmatch(query.upper()):
        query = 'key = "' + query.upper() + '"'
    result = jira.search_issues(query, max_results=101)
    if result['total'] > 100:
        raise ApiError(409, '查询超过 100 张 Jira，请缩小范围')
    return result


def add_issues(user: dict, keys: list[str], group: str) -> dict:
    if group not in domain.load_groups():
        raise ApiError(409, '未配置该组学习服务')
    keys = _keys(keys)
    result = preview_issues('key in (' + ','.join(keys) + ')')
    found = {item['key']: item for item in result['issues'] if item['key'] in keys}
    conn = _db()
    try:
        with transaction(conn):
            conn.execute('UPDATE jira_learning_jobs SET id=id WHERE 0')
            for issue in found.values():
                repo.add_issue(conn, issue, group, user['username'])
    finally:
        conn.close()
    return {'issues': list(found.values()), 'missing': [key for key in keys if key not in found]}


def _keys(keys: list[str]) -> list[str]:
    selected = list(dict.fromkeys(key.strip().upper() for key in keys if key.strip()))
    if not selected or len(selected) > 100 or any(not _ISSUE_KEY.fullmatch(key) for key in selected):
        raise ValueError('请选择 1 至 100 个有效 Jira 编号')
    return selected


async def list_issues(user: dict, group: str) -> dict:
    from app.services import jira_agent_service
    handled = await jira_agent_service.list_handled_issues(user)
    conn = _db()
    try:
        historical = repo.list_issues(conn, group)
        # All latest issue states are returned; task history itself is paginated.
        jobs = repo.issue_overview(conn, group)
    finally:
        conn.close()
    issues = {item['key']: item | {'learning_origin': 'handled'} for item in handled['issues']}
    history_keys = [item['key'] for item in historical if item['key'] not in issues]
    missing = list(handled['missing'])
    for start in range(0, len(history_keys), 100):
        keys = history_keys[start:start + 100]
        current = await asyncio.to_thread(jira.search_issues, 'key in (' + ','.join(keys) + ')', max_results=100, validate=False)
        found = {item['key']: item for item in current['issues'] if item['key'] in keys}
        issues.update({key: item | {'learning_origin': 'historical'} for key, item in found.items()})
        missing.extend(key for key in keys if key not in found)
    latest = {}
    counts = {}
    for job in jobs:
        latest.setdefault(job['issue_key'], _job_view(job))
        count = counts.setdefault(job['issue_key'], {'pending': 0, 'update': 0, 'participation': job['participation_count']})
        count['pending'] += job['pending_count']
        count['update'] += job['update_count']
    return {'issues': [item | {'learning': latest.get(key), 'learning_counts': counts.get(key)} for key, item in issues.items()],
            'missing': list(dict.fromkeys(missing)), 'total': len(issues)}


def create_job(user: dict, group: str, issue_keys: list[str],
                instructions: str = '', repository_only: bool = False,
                title: str = '', request_id: str = '') -> dict:
    if len(instructions) > 20000 or len(title) > 160 or len(request_id) > 100:
        raise ValueError('标题、整理要求或请求标识过长')
    if not settings.jira_learning_runner_enabled:
        raise ApiError(503, '知识学习队列尚未启用')
    if group not in domain.load_groups():
        raise ApiError(409, '未配置该组独立学习服务')
    selected = _keys(issue_keys)
    fingerprint = hashlib.sha256(json.dumps([sorted(selected), group, instructions.strip(), repository_only, title.strip()], ensure_ascii=False).encode()).hexdigest()
    conn = _db()
    try:
        # The caller's request token deduplicates retries, not overlapping Jira membership.
        if request_id:
            previous = repo.requested_job(conn, user['username'], request_id)
            if previous:
                if previous['request_hash'] != fingerprint:
                    raise ApiError(409, '请求标识已用于其他学习内容，请重新发起')
                return {'job': _job_view(previous), 'reused': True}
        cached = {item['key']: item for item in repo.list_issues(conn, group)}
        allowed = {key: cached.get(key, {'key': key, 'summary': '', 'url': ''}) for key in selected}
        with transaction(conn):
            conn.execute('UPDATE jira_learning_jobs SET id=id WHERE 0')
            previous = repo.requested_job(conn, user['username'], request_id) if request_id else None
            if previous and previous['request_hash'] != fingerprint:
                raise ApiError(409, '请求标识已用于其他学习内容，请重新发起')
            if previous:
                job = previous
            else:
                for key in selected:
                    repo.add_issue(conn, allowed[key], group, user['username'])
                job = repo.create_job(conn, selected[0], group, user['username'], issue_keys=selected,
                                      instructions=instructions, repository_only=repository_only,
                                      title=title.strip(), request_id=request_id, request_hash=fingerprint)
    finally:
        conn.close()
    runner.wake()
    return {'job': _job_view(job), 'reused': bool(previous)}


def list_jobs(group: str = '', limit: int = 50, offset: int = 0, issue_key: str = '') -> list[dict]:
    conn = _db()
    try:
        return [_job_view(job) for job in repo.list_jobs(conn, group, limit, offset, issue_key)]
    finally:
        conn.close()


def get_job(job_id: str, after: int = 0) -> dict:
    from app.services import jira_learning_relations as relations
    conn = _db()
    try:
        job = repo.get_job(conn, job_id)
        if job is None:
            raise ApiError(404, '学习任务不存在')
        replaced = repo.successor(conn, job_id)
        turns = repo.list_turns(conn, job_id)
        proposals = repo.list_proposals(conn, job_id)
        pending = [p for p in proposals if p['status'] in ('pending', 'approved') and not p['superseded_by'] and p['kind'] in {'skill_update', 'new_skill'}]
        related = relations.candidates(conn, job['agent_group']) if pending else []
        for proposal in proposals:
            proposal['warnings'] = [link for other in related if (link := relations.relation(proposal, other))] if proposal in pending else []
        if pending:
            try:
                config = domain.load_groups().get(job['agent_group'])
                if not config:
                    raise ValueError('未配置学习服务正式分支')
                current = knowledge.snapshot(config.repository_ref)
                for proposal in pending:
                    try:
                        _baseline(proposal, config.repository_ref, current)
                    except ApiError as exc:
                        proposal['review_blocker'] = str(exc)
            except (ValueError, RuntimeError) as exc:
                for proposal in pending:
                    proposal['review_blocker'] = str(exc)
        try:
            source = source_summary(job_id) if job['source_path'] else {}
            gaps = [g for g in source.get('collection_gaps', []) if g.get('category') == 'blocking']
        except ApiError as exc:
            source, gaps = {}, [{'source': 'source.json', 'reason': str(exc)}]
        if job['source_path'] and source:
            source['sources'] = LearningArchive(Path(job['source_path']).parent).references(list({sid for p in proposals for sid in p['evidence_ids']}))
        evidence = {item['id']: item.get('issue_key', source.get('issue', {}).get('key', '')) for item in source.get('sources', [])}
        for proposal in proposals:
            proposal['case_keys'] = list(dict.fromkeys(evidence[key] for key in proposal['evidence_ids'] if evidence.get(key)))
        return {'job': _job_view(job) | {'readonly': bool(replaced), 'successor_id': replaced['id'] if replaced else ''},
                'turns': [{key: value for key, value in turn.items() if key not in ('snapshot_json', 'response_text')}
                          | {'status': 'cancelled' if turn['cancelled_at'] else turn['status']} for turn in turns],
                'events': repo.list_events(conn, job_id, after),
                'proposals': [item | {'reviews': repo.list_reviews(conn, item['id']),
                                     'deployments': repo.list_deployments(conn, item['id'])} for item in proposals],
                'collection_gaps': gaps, 'progress': source.get('progress'),
                'combination': [({'id': p['id'], 'job_id': p['job_id'], 'title': p['title']}
                                 | {'status': (repo.get_proposal(conn, p['id']) or p)['status']})
                                for p in source.get('combination', [])],
                'knowledge': snapshots.knowledge_metadata(json.loads(turns[-1]['snapshot_json'])) if turns else {}}
    finally:
        conn.close()


def _enqueue(conn, job: dict, username: str, text: str, revision_id: str = '') -> dict:
    turn = repo.queued_turn(conn, job['id'])
    if turn:
        combined = (turn['input_text'].strip() + '\n\n' + text).strip()
        if len(combined) > 40000:
            raise ApiError(409, '排队要求已过长，请等待当前轮次结束')
        ids = json.loads(turn['revision_ids_json'])
        # Targeted revisions may merge with targeted revisions, never broad directions.
        if bool(ids) != bool(revision_id):
            raise ApiError(409, '已有其他类型的补充轮次，请等待该轮完成再操作')
        repo.update_turn(conn, turn['id'], input_text=combined,
                         revision_ids_json=json.dumps(list(dict.fromkeys(ids + ([revision_id] if revision_id else [])))))
        mode = 'merged'
    else:
        trigger = 'followup' if job['thread_id'] else 'initial'
        turn = repo.create_turn(conn, job['id'], trigger, username, text)
        repo.update_turn(conn, turn['id'], revision_ids_json=json.dumps([revision_id] if revision_id else []))
        if job['status'] not in ('running', 'collecting'):
            repo.update_job(conn, job['id'], status='queued', error='', finished_at='', cancelled_at='', cancel_requested=0)
        mode = 'queued'
    repo.add_event(conn, job['id'], turn['id'], 'user_message', {'author': username, 'text': text, 'mode': mode})
    return {'job': _job_view(repo.get_job(conn, job['id'])), 'turn': repo.get_turn(conn, turn['id']), 'mode': mode}


def send_message(user: dict, job_id: str, text: str) -> dict:
    text = text.strip()
    if not text or len(text) > 20000:
        raise ValueError('请输入不超过 20000 字符的补充要求')
    conn = _db()
    try:
        with transaction(conn):
            conn.execute('UPDATE jira_learning_jobs SET id=id WHERE 0')
            job = _editable(conn, job_id)
            if not job['source_path'] or not job['thread_id']:
                raise ApiError(409, '学习对话尚未建立，请等待材料采集和首轮启动')
            result = _enqueue(conn, job, user['username'], text)
    finally:
        conn.close()
    runner.wake()
    return result


def get_source(job_id: str, *, summary=False) -> dict:
    conn = _db()
    try:
        job = repo.get_job(conn, job_id)
    finally:
        conn.close()
    if job is None:
        raise ApiError(404, '学习任务不存在')
    if not job['source_path']:
        raise ApiError(409, '学习任务尚未采集材料')
    try:
        archive, result = snapshots.load(job)
    except ValueError as exc:
        raise ApiError(409, str(exc)) from exc
    if not summary:
        result.update(archive.validation())
        result['sources'] = archive.references([s['id'] for s in result['sources']], bodies=True)
    return result


def source_summary(job_id: str) -> dict:
    return get_source(job_id, summary=True)


def material_index(job_id: str, issue='', kind='', query='', offset=0, limit=20, ids: list[str] | None=None) -> dict:
    with _db() as conn:
        job=repo.get_job(conn,job_id)
    if not job:
        raise ApiError(404,'学习任务不存在')
    if not job['source_path']:
        return {'items':[], 'total':0, 'next_offset':None, 'pending':True}
    archive, _ = snapshots.load(job)
    if ids is not None:
        items = archive.references(ids)
        return {'items': items, 'total': len(items), 'next_offset': None}
    return archive.listing(issue=issue, kind=kind, query=query, offset=offset, limit=limit) | {'progress':archive.progress()}


async def material_read(job_id: str, source_id: str, offset=0, limit=12000) -> dict:
    with _db() as conn:
        job=repo.get_job(conn,job_id)
    if not job:
        raise ApiError(404,'学习任务不存在')
    if not job['source_path']:
        raise ApiError(409,'材料尚未准备好')
    await asyncio.to_thread(snapshots.load, job)
    return await worker.execute('read',Path(job['source_path']).parent,
        {'action':'read','source_id':source_id,'offset':offset,'limit':limit,'reason':'人工查看材料'},
        'preview',False)

def _baseline(proposal: dict, ref: str, current: dict | None = None) -> None:
    if not proposal['base_commit'] or not proposal['files']:
        raise ApiError(409, '旧提案没有文件基线，请重新整理后审核')
    current = current if current is not None else knowledge.snapshot(ref)
    expected = proposal['dependencies'] | {f['path']: f['base_hash'] for f in proposal['files']}
    proposed = knowledge.hashes({f['path']: f['updated_text'] for f in proposal['files']})
    changed = [path for path, digest in expected.items() if current['hashes'].get(path) != digest
               and (path not in proposed or current['hashes'].get(path) != proposed[path])]
    if changed:
        raise ApiError(409, '目标或依赖文件已变化，请重新采集并学习：' + '、'.join(changed))


def review(user: dict, proposal_id: str, decision: str, note: str) -> dict:
    if decision not in ('approved', 'rejected', 'needs_revision'):
        raise ValueError('无效的审核决定')
    note = note.strip()
    if len(note) > 4000 or (decision == 'needs_revision' and not note):
        raise ValueError('退回需填写原因，审核意见不超过 4000 字符')
    conn = _db()
    try:
        proposal = repo.get_proposal(conn, proposal_id)
        if not proposal:
            raise ApiError(404, '学习提案不存在')
        job = repo.get_job(conn, proposal['job_id'])
        if decision == 'approved':
            config = domain.load_groups().get(job['agent_group'])
            if not config:
                raise ApiError(409, '未配置学习服务正式分支')
            _baseline(proposal, config.repository_ref)
        with transaction(conn):
            conn.execute('UPDATE jira_learning_jobs SET id=id WHERE 0')
            # Historical proposals retain a review path; only execution of historical jobs is read-only.
            if proposal['kind'] == 'do_not_publish':
                raise ApiError(409, '无需更新是学习结果，不需要审核')
            if not repo.review(conn, proposal_id, user['username'], decision, note):
                raise ApiError(409, '该版本已审核或已被替代，请刷新')
            if decision == 'needs_revision':
                job = _editable(conn, proposal['job_id'])
                _enqueue(conn, job, user['username'], f"请只修订提案 {proposal_id}（{proposal['title']}）：{note}", proposal_id)
        result = repo.get_proposal(conn, proposal_id)
    finally:
        conn.close()
    runner.wake()
    return result


def record_publication(user: dict, proposal_id: str, commit: str, note: str) -> dict:
    """Compatibility adapter; all publication rules live in the batch operation."""
    from app.services import jira_learning_handoff as handoff
    with _db() as conn:
        proposal = repo.get_proposal(conn, proposal_id)
        if not proposal:
            raise ApiError(404, '提案不存在')
    handoff.publish(user, proposal['job_id'], [proposal_id], commit, note)
    with _db() as conn:
        return repo.get_proposal(conn, proposal_id)


def record_deployment(user: dict, proposal_id: str, target: str, commit: str, result: str, note: str) -> dict:
    from app.services import jira_learning_handoff as handoff
    with _db() as conn:
        proposal = repo.get_proposal(conn, proposal_id)
        if not proposal or proposal['status'] != 'published' or commit != proposal['published_commit']:
            raise ApiError(409, '请先登记该提案的入库版本')
    return handoff.deploy(user, proposal['job_id'], target, commit, result, note)


def cancel(user: dict, job_id: str) -> dict:
    conn = _db()
    try:
        with transaction(conn):
            conn.execute('UPDATE jira_learning_jobs SET id=id WHERE 0')
            job = repo.get_job(conn, job_id)
            if not job:
                raise ApiError(404, '学习任务不存在')
            if job['status'] not in ('queued', 'running', 'collecting'):
                raise ApiError(409, '任务已经结束')
            repo.cancel_queued(conn, job_id)
            running = any(t['status'] == 'running' for t in repo.list_turns(conn, job_id))
            repo.update_job(conn, job_id, cancel_requested=1 if running else 0,
                            **({} if running else {'status': 'failed', 'cancelled_at': beijing_timestamp(), 'finished_at': beijing_timestamp(), 'error': ''}))
            return _job_view(repo.get_job(conn, job_id))
    finally:
        conn.close()
        runner.wake()


def retry(user: dict, job_id: str, repository_only: bool = False) -> dict:
    conn = _db()
    try:
        with transaction(conn):
            conn.execute('UPDATE jira_learning_jobs SET id=id WHERE 0')
            job = _editable(conn, job_id)
            if job['status'] != 'failed':
                raise ApiError(409, '只有失败或取消的任务可以重试')
            repo.update_job(conn, job_id, repository_only=int(repository_only or job['repository_only']), phase='')
            result = _enqueue(conn, job, user['username'], '重试上一轮未完成的整理，保留原材料和已生成提案。')
    finally:
        conn.close()
    runner.wake()
    return result


def refresh(user: dict, job_id: str) -> dict:
    conn = _db()
    try:
        with transaction(conn):
            conn.execute('UPDATE jira_learning_jobs SET id=id WHERE 0')
            job = _editable(conn, job_id)
            if job['status'] in ('queued', 'collecting', 'running'):
                raise ApiError(409, '请等待当前学习结束再刷新材料')
            result = repo.create_job(conn, job['issue_key'], job['agent_group'], user['username'], parent_job_id=job_id,
                                     repository_only=bool(job['repository_only']), instructions=repo.list_turns(conn, job_id)[0]['input_text'],
                                     issue_keys=job['issue_keys'], title=job['title'])
            source = source_summary(job_id) if job['source_path'] else {}
            if source.get('combination'):
                source.pop('knowledge_snapshot', None)
                source['knowledge_pending'] = True
                archive = LearningArchive(settings.jira_agent_data_dir / 'learning' / result['id'])
                archive.initialize([c['issue'] for c in source['cases']])
                source.update(archive.bundle())
                source['imports'] = source.get('imports') or [{'job_id': job_id, 'prefix': ''}]
                repo.update_job(conn, result['id'], **snapshots.write(result['id'], source))
                result = repo.get_job(conn, result['id'])
    finally:
        conn.close()
    runner.wake()
    return {'job': _job_view(result)}


def publication_revision(user: dict, proposal_id: str, commit: str, note: str) -> dict:
    """Stage the actual committed text for a fresh review; never inherit approval."""
    if not _COMMIT.fullmatch(commit.strip()) or not note.strip() or len(note) > 4000:
        raise ValueError('请填写有效提交 SHA 和实际修改说明')
    conn = _db()
    try:
        proposal = repo.get_proposal(conn, proposal_id)
        if not proposal or proposal['status'] != 'approved' or proposal['superseded_by']:
            raise ApiError(409, '只有尚未入库、未被替代的已通过提案可以核对实际提交')
        job = repo.get_job(conn, proposal['job_id'])
        config = domain.load_groups().get(job['agent_group'])
        if not config or not proposal['base_commit'] or not proposal['files']:
            raise ApiError(409, '缺少正式分支或文件基线')
        resolved = knowledge.resolve(commit.strip())
        if resolved not in set(knowledge.git('rev-list', knowledge.resolve(config.repository_ref)).splitlines()):
            raise ApiError(409, '提交不在正式分支上')
        actual = knowledge.snapshot(resolved)['files']
        baseline = knowledge.snapshot(proposal['base_commit'])['files']
        paths = [f['path'] for f in proposal['files']]
        if any(path not in actual for path in paths):
            raise ApiError(409, '实际提交删除了目标文件，请人工检查')
        if all(actual[path] == f['updated_text'] for path, f in zip(paths, proposal['files'])):
            raise ApiError(409, '实际内容与已审核提案一致，可以直接登记入库')
        raw = {key: proposal[key] for key in ('kind', 'title', 'summary', 'proposed_text', 'applicability', 'evidence_ids', 'limitations', 'validation')}
        raw.update(files=[{'path': path, 'updated_text': actual[path]} for path in paths],
                   previous_id=proposal_id, dependencies=list(proposal['dependencies']),
                   summary='按实际提交 ' + resolved[:12] + ' 重新审核：' + note.strip())
        checked = domain.validate_proposals(json.dumps({'proposals': [raw]}), get_source(job['id']), baseline)
        with transaction(conn):
            conn.execute('UPDATE jira_learning_jobs SET id=id WHERE 0')
            current = repo.get_proposal(conn, proposal_id)
            if current['status'] != 'approved' or current['superseded_by']:
                raise ApiError(409, '提案状态已变化，请刷新')
            repo.add_proposals(conn, job['id'], checked, turn_id=proposal['turn_id'])
        return {'proposal': repo.list_proposals(conn, job['id'])[-1]}
    finally:
        conn.close()
