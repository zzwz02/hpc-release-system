"""Compare current proposals and prepare a separate, reviewable consolidation."""
import copy
import difflib
import hashlib
import json

from app.api.errors import ApiError
from app.config import settings
from app.db.connection import transaction
from app.domain import jira_learning as domain
from app.repositories import jira_learning_repo as repo
from app.services import jira_learning_service as learning, jira_learning_snapshots as snapshots
from app.timeutil import beijing_timestamp


def candidates(conn, group):
    return [repo._proposal(row) for row in conn.execute("""
        SELECT p.* FROM jira_learning_proposals p JOIN jira_learning_jobs j ON j.id=p.job_id
        WHERE j.agent_group=? AND p.status IN ('pending','approved') AND p.superseded_by=''
          AND p.kind IN ('skill_update','new_skill') ORDER BY p.rowid DESC
    """, (group,))]


def relation(proposal, other):
    if proposal['id'] == other['id']:
        return None
    files = {f['path']: f for f in proposal['files']}
    shared = [f for f in other['files'] if f['path'] in files]
    if not shared:
        return None
    same_base = all(f['base_hash'] == files[f['path']]['base_hash'] for f in shared)
    identical = same_base and all(f['updated_text'] == files[f['path']]['updated_text'] for f in shared)
    overlap = False
    if same_base and not identical:
        for f in shared:
            ranges = []
            for version in (f, files[f['path']]):
                ranges.append([(a, b) for op, a, b, _, _ in difflib.SequenceMatcher(
                    None, (version.get('previous_text') or '').splitlines(), version['updated_text'].splitlines(),
                    autojunk=False).get_opcodes() if op != 'equal'])
            overlap |= any(max(a, c) < min(b, d) or a == b and c <= a <= d or c == d and a <= c <= b
                           for a, b in ranges[0] for c, d in ranges[1])
    reason = '共同文件的修改内容相同' if identical else '文件基线不同' if not same_base else '修改位置重叠' if overlap else '修改同一文件的不同位置'
    return {'job_id': other['job_id'], 'proposal_id': other['id'], 'title': other['title'],
            'status': other['status'], 'reason': reason, 'paths': [f['path'] for f in shared]}


def compare(proposal_id, other_id):
    with learning._db() as conn:
        first, second = repo.get_proposal(conn, proposal_id), repo.get_proposal(conn, other_id)
        if not first or not second:
            raise ApiError(404, '提案不存在')
        if repo.get_job(conn, first['job_id'])['agent_group'] != repo.get_job(conn, second['job_id'])['agent_group']:
            raise ApiError(409, '只能比较同一分析组的提案')
        link = relation(first, second)
        if not link:
            raise ApiError(409, '两份提案没有共同修改的文件')
        def view(proposal):
            return {k: proposal[k] for k in ('id', 'job_id', 'title', 'summary', 'status', 'base_commit', 'superseded_by')} | {
                'files': [{'path': f['path'], 'patch': ''.join(difflib.unified_diff(
                    (f.get('previous_text') or '').splitlines(keepends=True), f['updated_text'].splitlines(keepends=True),
                    fromfile='修改前/' + f['path'], tofile='修改后/' + f['path']))}
                    for f in proposal['files'] if f['path'] in link['paths']]}
        return {'relation': link, 'current': view(first), 'other': view(second)}


def close_duplicate(user, proposal_id, replacement_id):
    with learning._db() as conn:
        with transaction(conn):
            conn.execute('UPDATE jira_learning_jobs SET id=id WHERE 0')
            original, replacement = repo.get_proposal(conn, proposal_id), repo.get_proposal(conn, replacement_id)
            if not original or not replacement:
                raise ApiError(404, '提案不存在')
            if replacement['status'] not in ('approved', 'published') or replacement['superseded_by']:
                raise ApiError(409, '请先审核通过整理后的提案')
            selected = learning.get_source(replacement['job_id']).get('combination', [])
            if proposal_id not in {p['id'] for p in selected} or not {f['path'] for f in original['files']}.issubset({f['path'] for f in replacement['files']}):
                raise ApiError(409, '新提案未覆盖所选原提案的文件，请分别核对')
            changed = conn.execute("UPDATE jira_learning_proposals SET status='rejected',updated_at=? WHERE id=? AND status IN ('pending','approved') AND superseded_by=''",
                                   (beijing_timestamp(), proposal_id))
            if changed.rowcount != 1:
                raise ApiError(409, '原提案状态已变化，请刷新')
            from app.repositories.base import new_id
            conn.execute('INSERT INTO jira_learning_reviews (id,proposal_id,reviewer,decision,note,created_at) VALUES(?,?,?,?,?,?)',
                         (new_id('jlr'), proposal_id, user['username'], 'rejected',
                          f"人工确认内容已整理至 {replacement_id}（{replacement['title']}），关闭重复提案。", beijing_timestamp()))
            return {'proposal': repo.get_proposal(conn, proposal_id)}


def combine(user, ids, note='', request_id=''):
    if len(ids) < 2 or len(ids) > 8 or len(ids) != len(set(ids)):
        raise ValueError('请选择 2 至 8 项不重复的提案')
    if len(note) > 4000 or not request_id or len(request_id) > 100:
        raise ValueError('请填写有效请求标识，整理意见不超过 4000 字符')
    if not settings.jira_learning_runner_enabled:
        raise ApiError(503, '知识学习队列尚未启用')
    fingerprint = hashlib.sha256(json.dumps(['combine', sorted(ids), note.strip()], ensure_ascii=False).encode()).hexdigest()
    with learning._db() as conn:
        with transaction(conn):
            conn.execute('UPDATE jira_learning_jobs SET id=id WHERE 0')
            previous = repo.requested_job(conn, user['username'], request_id)
            if previous:
                if previous['request_hash'] != fingerprint:
                    raise ApiError(409, '请求标识已用于其他整理内容')
                return {'job': learning._job_view(previous)}
            proposals = [repo.get_proposal(conn, pid) for pid in ids]
            if any(not p or p['status'] not in ('pending', 'approved') or p['superseded_by']
                   or p['kind'] not in ('skill_update', 'new_skill') or not p['files'] for p in proposals):
                raise ApiError(409, '只能整理未关闭、未被替代的修改提案，请刷新后重选')
            jobs = {p['job_id']: repo.get_job(conn, p['job_id']) for p in proposals}
            groups = {j['agent_group'] for j in jobs.values()}
            if len(groups) != 1 or next(iter(groups)) not in domain.load_groups():
                raise ApiError(409, '请选择同一已配置分析组的提案')
            if any(j['status'] in ('queued', 'collecting', 'running') or j['cancel_requested'] or j['phase'] == 'recovering' for j in jobs.values()):
                raise ApiError(409, '所选提案仍在整理，请等待任务结束')
            if any(not any(relation(p, other) for other in proposals) for p in proposals):
                raise ApiError(409, '请选择修改相同文件的相关提案')
            from app.services.jira_learning_archive import LearningArchive
            originals = {jid: learning.source_summary(jid) for jid in jobs}
            cases = {c['issue']['key']: c for saved in originals.values() for c in saved['cases']}
            selected = copy.deepcopy(proposals)
            for proposal in selected:
                proposal['evidence_ids'] = [proposal['job_id'] + ':' + sid for sid in proposal['evidence_ids']]
            job = repo.create_job(conn, next(iter(cases)), next(iter(groups)), user['username'], issue_keys=list(cases),
                title=f"提案整理 · {proposals[0]['title']}"[:160], instructions='一起整理所选提案。' + note.strip(),
                repository_only=all(j['repository_only'] for j in jobs.values()), request_id=request_id, request_hash=fingerprint)
            archive = LearningArchive(settings.jira_agent_data_dir / 'learning' / job['id'])
            archive.initialize([c['issue'] for c in cases.values()])
            source = archive.bundle() | {'combination': selected, 'knowledge_pending': True,
                'imports': [{'job_id': jid, 'prefix': jid + ':', 'manifest': original['source_path'],
                             'sha256': original['source_sha256']} for jid, original in jobs.items()]}
            repo.update_job(conn, job['id'], **snapshots.write(job['id'], source))
            result = learning._job_view(repo.get_job(conn, job['id']))
    learning.runner.wake()
    return {'job': result}
