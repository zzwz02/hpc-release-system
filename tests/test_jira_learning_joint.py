"""Joint learning uses one evidence set, one Git base, and reviewable patch bundles."""
import asyncio
import io
import json
import zipfile

import pytest

from app.db.connection import transaction
from app.domain import jira_learning as domain
from app.integrations import knowledge_repository as knowledge
from app.repositories import jira_learning_repo as repo, jira_testing_repo as testing_repo
from app.services import jira_learning_service as service, jira_learning_runner as execution, jira_learning_handoff as handoff
from tests.learning_helpers import commit, git
from tests.test_jira_learning_workflow import env, stage, RM, PREFIX


def test_overlapping_members_keep_both_tasks_editable_and_searchable(env, monkeypatch):
    _, _, conn = env
    monkeypatch.setattr(service.jira, 'search_issues', lambda query, max_results: {
        'issues': [{'key': key} for key in ('MC3-1', 'MC3-2', 'MC3-3')], 'total': 3})
    first = service.create_job(RM, 'HPC', ['MC3-1', 'MC3-2'], request_id='one')['job']
    second = service.create_job(RM, 'HPC', ['MC3-2', 'MC3-3'], request_id='two')['job']
    assert len(service.list_jobs('HPC', issue_key='MC3-2')) == 2
    assert not service.get_job(first['id'])['job']['readonly']
    assert not service.get_job(second['id'])['job']['readonly']
    view = {item['issue_key']: item for item in repo.issue_overview(conn, 'HPC')}
    assert view['MC3-2']['participation_count'] == 2
    with pytest.raises(Exception, match='请求标识'):
        service.create_job(RM, 'HPC', ['MC3-3'], request_id='one')
    service.cancel(RM, first['id'])
    refreshed = service.refresh(RM, first['id'])['job']
    assert refreshed['issue_keys'] == ['MC3-1', 'MC3-2']
    assert service.get_job(first['id'])['job']['readonly']
    assert not service.get_job(second['id'])['job']['readonly']


def test_joint_runner_collects_all_cases_once_with_partial_sources_and_fixed_base(env, monkeypatch):
    root, conf, conn = env
    fake = Learner().start()
    conf.write_text(conf.read_text().replace('ws://unused:4520', fake.url).replace('CODEX_WS_TOKEN=test', 'CODEX_WS_TOKEN=' + fake.token))
    calls = []
    def snapshot(key):
        calls.append(key)
        if key == 'MC3-3':
            raise ValueError('Jira 暂不可达')
        return {'key': key, 'description': ('已复现 ' if key == 'MC3-1' else '未复现 ') + 'x' * 130000,
                'comments': [{'id': '7', 'body': '相同 ID 不同工单'}], 'acquisition': {'status': 'complete'}}
    monkeypatch.setattr(execution.jira, 'get_issue_snapshot', snapshot)
    proposal = {'kind': 'skill_update', 'title': '根据复现条件判断', 'summary': '区分有效与无效对照',
                'proposed_text': '收窄范围', 'files': [{'path': PREFIX + 'references/rule.md', 'updated_text': '# Rule\n比较可比条件。\n'}],
                'dependencies': [], 'previous_id': '', 'applicability': '仅限可比条件',
                'evidence_ids': ['MC3-1:jira:description', 'MC3-2:jira:description'],
                'limitations': '第三张材料不可达', 'validation': '正常、异常条件均验证'}
    fake.proposal = proposal
    with transaction(conn):
        job = repo.create_job(conn, 'MC3-1', 'HPC', 'rm', issue_keys=['MC3-1', 'MC3-2', 'MC3-3'], repository_only=True)
        work = repo.claim_next_turn(conn)
    worker = execution.JiraLearningRunner()
    try:
        asyncio.run(worker._run(work['job'], work['turn']))
        completed = repo.get_job(conn, job['id'])
        assert completed['status'] == 'completed', completed['error']
        assert completed['acquisition_status'] == 'partial'
        source = service.get_source(job['id'])
        assert sorted(calls) == ['MC3-1', 'MC3-2', 'MC3-3']
        assert len(source['cases']) == 3
        assert source['cases'][2]['acquisition']['status'] == 'missing'
        assert len({s['id'] for s in source['sources']}) == 2
        assert all(':excerpt:' in s['id'] for s in source['sources'])
        workspace = '/learning/workspaces/' + job['id']
        model = json.loads(fake.files[workspace + '/source.json'])
        assert len(model['cases']) == 3
        assert model['sources'] == []
        assert len(fake.files[workspace+'/source.json']) < 10000
        assert len(fake.params('thread/start')) == len(fake.params('turn/start')) == 1
        base = json.loads(repo.list_turns(conn, job['id'])[0]['snapshot_json'])['repository']['commit']
        commit(root, {PREFIX + 'references/rule.md': '# Changed independently\n'})
        _, saved, _ = asyncio.run(worker._reload(completed, {}))
        assert saved[PREFIX + 'references/rule.md'] == '# Rule\n'
        assert repo.list_proposals(conn, job['id'])[0]['base_commit'] == base
        assert 'review_blocker' in service.get_job(job['id'])['proposals'][0]
    finally:
        fake.stop()


def test_missing_jira_is_reported_in_background_without_blocking_creation(env, monkeypatch):
    _, _, conn = env
    def fail(key):
        raise ValueError('offline')
    monkeypatch.setattr(execution.jira, 'get_issue_snapshot', fail)
    job=service.create_job(RM,'HPC',['MC3-1','MC3-2'],repository_only=True)['job']
    async def scenario():
        runner=execution.JiraLearningRunner()
        await runner._prepare(repo.get_job(conn,job['id']))
        pending=list(runner._collectors.values())
        if pending:
            await asyncio.gather(*pending)
    asyncio.run(scenario())
    source=service.get_source(job['id'])
    assert len(source['cases'])==2 and source['progress']['limited']==2
    assert source['progress']['pending']==0
    assert all(case['jira_status']=='failed' for case in source['cases'])


def test_conflicting_followup_requires_revision_and_related_proposals_include_patch(env):
    _, _, conn = env
    job, proposal = stage(conn)
    with pytest.raises(ValueError, match='集中在一个提案'):
        with transaction(conn):
            repo.add_proposals(conn, job['id'], [{**proposal, 'previous_id': ''}])
    with transaction(conn):
        other = repo.create_job(conn, 'MC3-2', 'HPC', 'rm')
    related = repo.related_proposals(conn, other['id'])
    assert related[0]['files'] == proposal['files'] and related[0]['patch_text'] == proposal['patch_text']
    assert related[0]['status'] == 'pending'


def two_proposals(conn):
    from app.config import settings
    commit(settings.jira_learning_repo_root, {PREFIX + 'references/other.md': '# Other\n'})
    job, first = stage(conn)
    snapshot = knowledge.snapshot('refs/heads/main')
    raw = {**first, 'title': '独立知识点', 'files': [{'path': PREFIX + 'references/other.md', 'updated_text': '# Other\n条件与边界。\n'}], 'dependencies': [], 'previous_id': ''}
    checked = domain.validate_proposals(json.dumps({'proposals': [raw]}), service.get_source(job['id']), snapshot['files'])
    with transaction(conn):
        repo.add_proposals(conn, job['id'], checked, turn_id=repo.list_turns(conn, job['id'])[0]['id'])
    proposals = repo.list_proposals(conn, job['id'])
    for proposal in proposals:
        service.review(RM, proposal['id'], 'approved', '')
    return job, proposals


def test_bundle_applies_to_real_git_and_registers_one_commit_atomically(env, tmp_path):
    root, _, conn = env
    job, proposals = two_proposals(conn)
    ids = [p['id'] for p in proposals]
    payload = handoff.export(job['id'], ids)
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        assert set(archive.namelist()) == {'README.md', 'manifest.json', 'changes.patch'}
        manifest = json.loads(archive.read('manifest.json'))
        assert manifest['base_commit'] == git(root, 'rev-parse', 'HEAD')
        patch = tmp_path / 'changes.patch'
        patch.write_bytes(archive.read('changes.patch'))
    git(root, 'apply', '--check', str(patch))
    git(root, 'apply', str(patch))
    sha = commit(root, {})
    with pytest.raises(Exception, match='已更新'):
        handoff.export(job['id'], ids)
    handoff.publish(RM, job['id'], ids, sha, '验证完成')
    assert all(p['published_commit'] == sha for p in repo.list_proposals(conn, job['id']))
    assert all(p['status'] == 'published' for p in repo.list_proposals(conn, job['id']))
    assert len(repo.list_reviews(conn, ids[0])) == 2


@pytest.mark.parametrize('depends_on_rule', [False, True])
def test_bundle_distinguishes_navigation_from_declared_dependencies(env, tmp_path, depends_on_rule):
    root, _, conn = env
    commit(root, {PREFIX + 'references/rule.md': '# Rule\n\n[Home](../SKILL.md)\n'})
    job, proposal = stage(conn, dependencies=[PREFIX + 'references/rule.md'] if depends_on_rule else [])
    with pytest.raises(Exception, match='通过审核'):
        handoff.export(job['id'], [proposal['id']])
    service.review(RM, proposal['id'], 'approved', '')
    second = {**proposal, 'title': '修改依赖', 'files': [{'path': PREFIX + 'references/rule.md', 'updated_text': '# New\n'}], 'dependencies': [], 'previous_id': ''}
    checked = domain.validate_proposals(json.dumps({'proposals': [second]}), service.get_source(job['id']), knowledge.snapshot('main')['files'])
    with transaction(conn):
        repo.add_proposals(conn, job['id'], checked, turn_id=repo.list_turns(conn, job['id'])[0]['id'])
    pid = repo.list_proposals(conn, job['id'])[-1]['id']
    service.review(RM, pid, 'approved', '')
    if depends_on_rule:
        with pytest.raises(Exception, match='依赖的文件'):
            handoff.export(job['id'], [proposal['id'], pid])
    else:
        payload = handoff.export(job['id'], [proposal['id'], pid])
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            patch = tmp_path / 'navigation.patch'
            patch.write_bytes(archive.read('changes.patch'))
        git(root, 'apply', '--check', str(patch))


def test_validation_compares_actual_test_pack_and_marks_old_proposal_versions(env):
    root, _, conn = env
    job, proposal = stage(conn)
    service.review(RM, proposal['id'], 'approved', '')
    base = knowledge.snapshot('main')['files']
    candidate = {**base, **{f['path']: f['updated_text'] for f in proposal['files']}}
    with transaction(conn):
        cid = testing_repo.save_version(conn, 'rm', '', '验证输入', {'issue': {'summary': '输入'}, 'files': []}, 'hash', '通过条件', '', {})
        vid = testing_repo.versions(conn, cid)[0]['id']
        conn.execute('INSERT INTO jira_test_batches VALUES(?,?,?,?,?,?)', ('batch', vid, json.dumps({'skill_sha256': 'fingerprint'}), json.dumps({'files': base, 'server': 'validation'}), 'rm', '2026-10-23'))
        conn.execute('INSERT INTO jira_test_suites VALUES(?,?,?,?,?,?,?,?)', ('suite', 'request', 'hash', 'ready', '{}', '{}', '', '2026-10-23'))
        conn.execute('INSERT INTO jira_test_suite_items VALUES(?,?,?,?,?)', ('suite', cid, vid, 'batch', 0))
    linked = handoff.link_validation(RM, job['id'], [proposal['id']], 'suite', 'other', '其他场景')['validations'][0]
    assert not linked['matches']
    with transaction(conn):
        conn.execute('UPDATE jira_test_batches SET pack_json=? WHERE id=?', (json.dumps({'files': candidate, 'server': 'validation'}), 'batch'))
    assert handoff.validations(job['id'])[0]['matches']
    with transaction(conn):
        repo.add_proposals(conn, job['id'], [{**proposal, 'previous_id': proposal['id']}], turn_id=repo.list_turns(conn, job['id'])[0]['id'])
    assert handoff.validations(job['id'])[0]['superseded']


# A deterministic protocol peer used by the browser fixture, not a live model.
from tests.test_jira_agent import FakeCodex


class Learner(FakeCodex):
    async def tool(self, params, turn_id, arguments):
        if not hasattr(self,'tool_responses'):
            self.tool_responses={}
            self.tool_seq=0
        self.tool_seq+=1
        call='read-'+str(self.tool_seq)
        future=asyncio.get_running_loop().create_future()
        self.tool_responses[call]=future
        await self._send(params['threadId'],{'id':call,'method':'item/tool/call','params':{
            'threadId':params['threadId'],'turnId':turn_id,'callId':call,'tool':'read_learning_material','arguments':arguments}})
        return await asyncio.wait_for(future,20)

    async def _play(self, params, turn_id, plan):
        await self._send(params['threadId'],{'method':'item/completed','params':{'threadId':params['threadId'],'turnId':turn_id,'item':{'type':'agentMessage','id':'progress-'+turn_id,'text':'正在核对案例材料。'}}})
        cwd = self.thread_cwd[params['threadId']]
        source = json.loads(self.files[cwd + '/source.json'])
        previous = json.loads(self.files[cwd + '/proposals.json'])['current']
        evidence=[item['id'] for item in source['sources'] if item['kind']=='description']
        if source.get('format')==2:
            for _ in range(100):
                response=await self.tool(params,turn_id,{'action':'list','source_id':'','offset':0,'limit':100,'reason':'核对全部案例与准备状态'})
                listing=json.loads(response['contentItems'][0]['text'])
                if not any('pending' in (c['local_status'],c['jira_status']) for c in listing['cases']):
                    break
                await asyncio.sleep(.05)
            for item in listing['items']:
                if item['kind']=='description':
                    response=await self.tool(params,turn_id,{'action':'read','source_id':item['id'],'offset':0,'limit':48000,'reason':'核对案例证据'})
                    evidence.append(json.loads(response['contentItems'][0]['text'])['evidence_id'])

        response = {'proposals': [{
            'kind': 'skill_update', 'title': '明确可比条件与结论边界',
            'summary': '两个案例都涉及版本对照，结论需区分已验证条件和未覆盖场景。',
            'proposed_text': '说明何时可以根据对照归因，保留证据不足时的限制。',
            'files': [{'path': PREFIX + 'references/rule.md', 'updated_text': '# Rule\n\n对照条件需能支持结论，未验证的场景应说明限制。\n'}],
            'dependencies': [], 'previous_id': previous[0]['id'] if previous else '',
            'applicability': '有正常与异常对照的版本问题',
            'evidence_ids': evidence,
            'limitations': '材料只覆盖所选场景，需用其他案例复核。', 'validation': '原案例复测，并核对其他场景是否被误导。',
        }]}
        if hasattr(self,'proposal'):
            response={'proposals':[{**self.proposal,'evidence_ids':evidence}]}
        item = {'type': 'agentMessage', 'id': 'answer-' + turn_id, 'text': json.dumps(response, ensure_ascii=False)}
        self.turn_state[turn_id].update(status='completed', items=[item])
        await self._send(params['threadId'], {'method': 'item/completed', 'params': {'threadId': params['threadId'], 'turnId': turn_id, 'item': item}})
        await self._send(params['threadId'], {'method': 'turn/completed', 'params': {'threadId': params['threadId'], 'turn': {'id': turn_id, 'status': 'completed', 'error': None}}})
