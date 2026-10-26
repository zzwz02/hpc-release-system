"""Product workflow checks against real SQLite, Git and the WebSocket protocol."""
from __future__ import annotations

import asyncio
import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from app.config import settings
from app.db.connection import transaction
from app.db.jira_agent_connection import connect_jira_agent, reset_jira_agent_init_state
from app.domain import jira_learning as domain
from app.integrations import knowledge_repository as knowledge
from app.repositories import jira_learning_repo as repo
from app.services import jira_learning_service as service, jira_learning_runner as execution, jira_learning_knowledge
from tests.learning_helpers import commit, git, knowledge_repo

PREFIX = knowledge.PREFIX
RM = {'username': 'rm', 'role': 'RM'}


@pytest.fixture()
def env(tmp_path, monkeypatch):
    root = knowledge_repo(tmp_path, monkeypatch)
    conf = tmp_path / 'release_system.conf'
    conf.write_text('[jira_learning:HPC]\nCODEX_WS_URL=ws://unused:4520\nCODEX_WS_TOKEN=test\n'
                    'WORKSPACE_ROOT=/learning/workspaces\nTURN_TIMEOUT_SECONDS=120\nREPOSITORY_REF=refs/heads/main\n')
    monkeypatch.setattr(settings, 'runtime_conf_path', conf)
    monkeypatch.setattr(settings, 'jira_agent_database_url', f'sqlite:///{tmp_path / "tasks.db"}')
    monkeypatch.setattr(settings, 'jira_agent_data_dir', tmp_path / 'data')
    monkeypatch.setattr(settings, 'jira_learning_runner_enabled', True)
    reset_jira_agent_init_state()
    conn = connect_jira_agent(settings.jira_agent_database_url)
    yield root, conf, conn
    conn.close()
    execution.worker.shutdown()
    reset_jira_agent_init_state()


def stage(conn, *, key='MC3-1', files=None, kind='skill_update', dependencies=()):
    snapshot = knowledge.snapshot('refs/heads/main')
    source = domain.build_source({'key': key, 'description': '日志证明有效的排查方法', 'comments': []}, [], set())
    data = json.dumps(source).encode()
    with transaction(conn):
        job = repo.create_job(conn, key, 'HPC', 'rm', repository_only=True)
        directory = settings.jira_agent_data_dir / 'learning' / job['id']
        directory.mkdir(parents=True)
        path = directory / 'source.json'
        path.write_bytes(data)
        repo.update_job(conn, job['id'], status='completed', source_path=str(path),
                        source_sha256=hashlib.sha256(data).hexdigest(), thread_id='thread1')
        turn = repo.list_turns(conn, job['id'])[0]
        repo.update_turn(conn, turn['id'], status='completed', snapshot_json=json.dumps({'repository': snapshot, 'analysis': {'files': {}}}))
        proposal = {'kind': kind, 'title': '有效方法', 'summary': '补充规则', 'proposed_text': '补充可验证条件',
                    'applicability': '已验证版本', 'evidence_ids': ['jira:description'], 'limitations': '',
                    'validation': '核对日志', 'files': files if files is not None else [
                        {'path': PREFIX + 'SKILL.md', 'updated_text': snapshot['files'][PREFIX + 'SKILL.md'] + '\nNew rule.\n'}],
                    'dependencies': list(dependencies), 'previous_id': ''}
        checked = domain.validate_proposals(json.dumps({'proposals': [proposal]}), source, snapshot['files'])
        repo.add_proposals(conn, job['id'], checked, turn_id=turn['id'])
    return repo.get_job(conn, job['id']), repo.list_proposals(conn, job['id'])[0]


def test_associated_files_patch_applies_from_independent_repo_root(env):
    root, _, conn = env
    job, proposal = stage(conn, files=[
        {'path': PREFIX + 'SKILL.md', 'updated_text': '# HPC\n\n[对照条件](references/comparison.md)\n'},
        {'path': PREFIX + 'references/comparison.md', 'updated_text': '# 对照条件\n'},
    ])
    assert len(proposal['files']) == 2
    subprocess.run(['git', 'apply', '--check', '-'], cwd=root, input=proposal['patch_text'], text=True, check=True)
    assert knowledge.snapshot('refs/heads/main')['files'][PREFIX + 'SKILL.md'].startswith('# HPC')
    service.review(RM, proposal['id'], 'approved', 'checked both files')
    assert repo.get_proposal(conn, proposal['id'])['status'] == 'approved'
    assert service.get_job(job['id'])['proposals'][0]['reviews'][0]['note'] == 'checked both files'


def test_unrelated_changes_do_not_invalidate_review_but_dependencies_do(env):
    root, _, conn = env
    _, proposal = stage(conn, dependencies=[PREFIX + 'references/rule.md'])
    commit(root, {PREFIX + 'unrelated.md': '# unrelated\n'})
    service.review(RM, proposal['id'], 'approved', '')
    _, other = stage(conn, key='MC3-2', dependencies=[PREFIX + 'references/rule.md'])
    commit(root, {PREFIX + 'references/rule.md': '# changed dependency\n'})
    with pytest.raises(Exception, match='依赖文件已变化'):
        service.review(RM, other['id'], 'approved', '')
    assert repo.get_proposal(conn, other['id'])['status'] == 'pending'
    assert service.get_job(other['job_id'])['proposals'][0]['review_blocker']


@pytest.mark.parametrize('changed_path', ['SKILL.md', 'references/other.md'])
def test_navigation_changes_do_not_invalidate_explicit_dependencies(env, changed_path):
    root, _, conn = env
    baseline = '# Performance\n\n[Home](../SKILL.md)\n'
    commit(root, {
        PREFIX + 'SKILL.md': '# HPC\n\n[Performance](references/performance.md)\n[Other](references/other.md)\n',
        PREFIX + 'references/performance.md': baseline,
        PREFIX + 'references/other.md': '# Other\n\n[Home](../SKILL.md)\n',
    })
    job, proposal = stage(conn, files=[{
        'path': PREFIX + 'references/performance.md', 'updated_text': baseline + '\nUse the declared comparison rule.\n',
    }], dependencies=[PREFIX + 'references/rule.md'])
    commit(root, {PREFIX + changed_path: '# Unrelated navigation or rule change\n'})

    service.review(RM, proposal['id'], 'approved', '')
    saved = service.get_job(job['id'])['proposals'][0]
    assert saved['status'] == 'approved' and not saved.get('review_blocker')
    assert set(saved['dependencies']) == {PREFIX + 'references/rule.md'}


def test_return_is_one_action_preserves_old_review_and_versions(env):
    _, _, conn = env
    job, proposal = stage(conn)
    service.review(RM, proposal['id'], 'needs_revision', '缩小范围')
    queued = repo.queued_turn(conn, job['id'])
    assert queued and json.loads(queued['revision_ids_json']) == [proposal['id']]
    assert len(repo.list_jobs(conn)) == 1
    with pytest.raises(Exception, match='已审核'):
        service.review(RM, proposal['id'], 'approved', '')
    with transaction(conn):
        repo.update_turn(conn, queued['id'], snapshot_json=repo.list_turns(conn, job['id'])[0]['snapshot_json'])
        revised = {**proposal, 'previous_id': proposal['id'], 'summary': '范围已缩小'}
        repo.add_proposals(conn, job['id'], [revised], turn_id=queued['id'])
    versions = repo.list_proposals(conn, job['id'])
    assert [p['version'] for p in versions] == [1, 2]
    assert versions[0]['superseded_by'] == versions[1]['id']
    assert versions[1]['status'] == 'pending'
    assert repo.list_reviews(conn, proposal['id'])[0]['note'] == '缩小范围'


def test_commit_content_and_official_branch_verified_deployment_is_separate(env):
    root, _, conn = env
    _, proposal = stage(conn)
    service.review(RM, proposal['id'], 'approved', '')
    unrelated = commit(root, {PREFIX + 'SKILL.md': '# unrelated edit\n'})
    with pytest.raises(ValueError, match='不一致'):
        service.record_publication(RM, proposal['id'], unrelated, '')
    correct = commit(root, {f['path']: f['updated_text'] for f in proposal['files']})
    git(root, 'checkout', '-qb', 'unmerged')
    other = commit(root, {PREFIX + 'other.md': '# side branch\n'})
    git(root, 'checkout', '-q', 'main')
    with pytest.raises(ValueError, match='正式分支'):
        service.record_publication(RM, proposal['id'], other, '')
    service.record_publication(RM, proposal['id'], correct, '已验证')
    assert repo.get_proposal(conn, proposal['id'])['status'] == 'published'
    assert repo.list_deployments(conn, proposal['id']) == []
    service.record_deployment(RM, proposal['id'], 'test service', correct, 'failed', '连接失败')
    service.record_deployment(RM, proposal['id'], 'test service', correct, 'succeeded', '已人工核对')
    records = repo.list_deployments(conn, proposal['id'])
    assert {r['result'] for r in records} == {'succeeded', 'failed'}
    assert all(r['verification'] == 'manual' for r in records)


def test_adjusted_commit_creates_fresh_review(env):
    root, _, conn = env
    _, proposal = stage(conn)
    service.review(RM, proposal['id'], 'approved', '')
    actual = commit(root, {PREFIX + 'SKILL.md': '# HPC\n\nReviewed different wording.\n'})
    revised = service.publication_revision(RM, proposal['id'], actual, '收窄文字')['proposal']
    assert revised['status'] == 'pending' and revised['version'] == 2
    assert repo.list_reviews(conn, revised['id']) == []
    service.review(RM, revised['id'], 'approved', '确认实际内容')
    service.record_publication(RM, revised['id'], actual, '')
    assert repo.get_proposal(conn, revised['id'])['published_commit'] == actual


def test_history_partial_batch_and_duplicate_submission(env, monkeypatch):
    _, _, conn = env
    monkeypatch.setattr(service.jira, 'search_issues', lambda query, max_results: {
        'total': 2, 'issues': [{'key': 'MC3-1', 'summary': 'one'}, {'key': 'OTHER-2', 'summary': 'two', 'components': ['OTHER']}]})
    added = service.add_issues(RM, ['MC3-1', 'OTHER-2'], 'HPC')
    assert len(added['issues']) == 2 and repo.list_jobs(conn) == []
    pending=service.create_job(RM, 'HPC', ['MC3-1', 'OTHER-2', 'MC3-404'])
    assert pending['job']['issue_keys']==['MC3-1','OTHER-2','MC3-404']
    first = service.create_job(RM, 'HPC', ['MC3-1', 'OTHER-2'], instructions='整理方法', request_id='same-request')
    again = service.create_job(RM, 'HPC', ['MC3-1', 'OTHER-2'], instructions='整理方法', request_id='same-request')
    assert set(first) == {"job", "reused"}
    assert first['job']['issue_keys'] == ['MC3-1', 'OTHER-2']
    assert again['job']['id'] == first['job']['id'] and again['reused']
    assert len(repo.list_issues(conn, 'HPC')) == 3
    assert repo.list_turns(conn, first['job']['id'])[0]['input_text'] == '整理方法'


def test_cancel_queued_and_refresh_preserve_old_sources(env):
    _, _, conn = env
    with transaction(conn):
        queued = repo.create_job(conn, 'MC3-9', 'HPC', 'rm')
    assert service.cancel(RM, queued['id'])['status'] == 'cancelled'
    assert repo.claim_next_turn(conn) is None
    job, proposal = stage(conn)
    new = service.refresh(RM, job['id'])['job']
    assert new['parent_job_id'] == job['id'] and not new['source_available']
    assert service.get_source(job['id'])['issue']['key'] == 'MC3-1'
    assert service.get_job(job['id'])['job']['readonly']
    with pytest.raises(Exception, match='历史任务'):
        service.send_message(RM, job['id'], '不要改旧材料')
    assert repo.get_proposal(conn, proposal['id'])['status'] == 'pending'


def test_job_history_paginates_without_losing_old_tasks(env):
    _, _, conn = env
    with transaction(conn):
        for _ in range(105):
            job = repo.create_job(conn, 'MC3-1', 'HPC', 'rm')
            repo.update_job(conn, job['id'], status='completed')
    first = service.list_jobs('HPC', 100, 0, 'MC3-1')
    last = service.list_jobs('HPC', 100, 100, 'MC3-1')
    assert len(first) == 100 and len(last) == 5
    assert len({j['id'] for j in first + last}) == 105


def test_no_update_is_a_result_without_review(env):
    _, _, conn = env
    _, proposal = stage(conn, kind='do_not_publish', files=[])
    with pytest.raises(Exception, match='无需更新'):
        service.review(RM, proposal['id'], 'rejected', '')


@pytest.mark.parametrize('kind', ['do_not_publish'])
def test_same_target_is_not_a_revision_without_previous_id(env, kind):
    _, _, conn = env
    job, original = stage(conn, kind=kind, files=[] if kind == 'do_not_publish' else None)
    with transaction(conn):
        repo.add_proposals(conn, job['id'], [{**original, 'title': '另一项判断', 'previous_id': ''}])
    current = repo.list_proposals(conn, job['id'])
    assert [p['version'] for p in current] == [1, 1]
    assert all(not p['previous_id'] and not p['superseded_by'] for p in current)
    with transaction(conn):
        repo.add_proposals(conn, job['id'], [{**original, 'title': '明确修订', 'previous_id': original['id']}])
    versions = repo.list_proposals(conn, job['id'])
    assert [p['version'] for p in versions] == [1, 1, 2]
    assert versions[0]['superseded_by'] == versions[2]['id']
    assert versions[1]['superseded_by'] == ''
    assert versions[2]['previous_id'] == original['id']


def test_returned_proposal_requires_explicit_revision_reference(env):
    _, _, conn = env
    job, proposal = stage(conn)
    service.review(RM, proposal['id'], 'needs_revision', '明确适用条件')
    queued = repo.queued_turn(conn, job['id'])
    with pytest.raises(ValueError, match='退回整理只能修订指定提案'):
        with transaction(conn):
            repo.add_proposals(conn, job['id'], [{**proposal, 'previous_id': ''}], turn_id=queued['id'])
    assert len(repo.list_proposals(conn, job['id'])) == 1
    assert repo.get_proposal(conn, proposal['id'])['superseded_by'] == ''


@pytest.mark.parametrize('large', [False, True])
def test_capture_only_knowledge_files_and_symlink_exclusion(env, large):
    root, conf, _ = env
    from tests.test_jira_agent import FakeCodex
    class ReadOnlyServer(FakeCodex):
        def _result(self, ws, method, params):
            if method == 'command/exec':
                completed = subprocess.run(params['command'], text=True, capture_output=True, timeout=10)
                output = completed.stdout.encode()
                if not params.get('disableOutputCap'):
                    output = output[:1048576]
                return {'exitCode': completed.returncode, 'stdout': output.decode(), 'stderr': completed.stderr}, None
            return super()._result(ws, method, params)
    (root / 'auth.json').write_text('SECRET NOT COPIED')
    (root / '.agents/skills/hpc-jira-agent/secret.md').symlink_to(root / 'auth.json')
    if large:
        for index in range(3):
            (root / f'.agents/skills/hpc-jira-agent/large-{index}.md').write_text('Content\n' * 60000)
    fake = ReadOnlyServer().start()
    try:
        conf.write_text(conf.read_text() + f'\n[jira_agent:HPC]\nDISPLAY_NAME=HPC\nCODEX_WS_URL={fake.url}\nCODEX_WS_TOKEN={fake.token}\nWORKSPACE_ROOT={root}/workspaces\nTURN_TIMEOUT_SECONDS=120\nMAX_CONCURRENT=1\n')
        captured = asyncio.run(jira_learning_knowledge.capture('HPC'))
        assert captured['files']['AGENTS.md'] == '# Identity\n'
        assert not any('SECRET' in value for value in captured['files'].values())
        assert captured['hashes'] == knowledge.hashes(captured['files'])
        if large:
            for index in range(3):
                assert captured['files'][f'.agents/skills/hpc-jira-agent/large-{index}.md'] == 'Content\n' * 60000
    finally:
        fake.stop()


def test_running_turn_survives_website_stop_and_resumes_without_duplicate(env, monkeypatch):
    _, conf, conn = env
    from tests import test_jira_agent as protocol
    fake = protocol.FakeCodex().start()
    fake.plans = ['hold']
    conf.write_text(conf.read_text().replace('ws://unused:4520', fake.url).replace('CODEX_WS_TOKEN=test', f'CODEX_WS_TOKEN={fake.token}'))
    monkeypatch.setattr(execution.jira, 'get_issue_snapshot', lambda key: {'key': key, 'description': '已验证方法', 'acquisition': {'status': 'complete'}})
    monkeypatch.setattr(protocol, 'RESULT', {'proposals': [{'kind': 'do_not_publish', 'title': '无需更新', 'summary': '证据不足',
        'proposed_text': '', 'files': [], 'dependencies': [], 'previous_id': '', 'applicability': '', 'evidence_ids': [], 'limitations': '', 'validation': ''}]})
    with transaction(conn):
        job = repo.create_job(conn, 'MC3-5', 'HPC', 'rm', repository_only=True)
        work = repo.claim_next_turn(conn)
    async def exercise():
        first = execution.JiraLearningRunner()
        task = asyncio.create_task(first._run(work['job'], work['turn']))
        for _ in range(200):
            if repo.get_turn(conn, work['turn']['id'])['codex_turn_id']:
                break
            await asyncio.sleep(.02)
        assert repo.get_turn(conn, work['turn']['id'])['codex_turn_id']
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        assert repo.get_turn(conn, work['turn']['id'])['status'] == 'running'
        assert len(fake.params('turn/interrupt')) == 0
        second = execution.JiraLearningRunner()
        recovery = asyncio.create_task(second._run(repo.get_job(conn, job['id']), repo.get_turn(conn, work['turn']['id'])))
        await asyncio.sleep(.15)
        # Cancellation is not reported as completed until the server acknowledges it.
        assert service.cancel(RM, job['id'])['status'] == 'cancelling'
        await asyncio.wait_for(recovery, 12)
        if repo.get_job(conn, job['id'])['phase'] == 'recovering':
            await second._run(repo.get_job(conn, job['id']), repo.get_turn(conn, work['turn']['id']))
        assert service.get_job(job['id'])['job']['status'] == 'cancelled'
        assert len(fake.params('turn/start')) == 1
    try:
        asyncio.run(exercise())
    finally:
        fake.stop()


def test_sync_pack_pins_commit_and_does_not_copy_git_or_runtime(env, tmp_path):
    root, _, _ = env
    destination = tmp_path / 'deployment'
    destination.mkdir()
    (destination / 'workspaces').mkdir()
    (destination / 'workspaces/task.txt').write_text('keep')
    old = git(root, 'rev-parse', 'HEAD')
    commit(root, {PREFIX + 'SKILL.md': '# New version\n'})
    import os
    subprocess.run(['bash', 'deploy/jira-agent/sync-pack.sh'], check=True,
                   env={**os.environ, 'SKILL_REPO': str(root), 'SKILL_COMMIT': old, 'PACK_DIR': str(destination)}, capture_output=True)
    assert (destination / (PREFIX + 'SKILL.md')).read_text().startswith('# HPC')
    assert json.loads((destination / '.skill-version.json').read_text())['commit'] == old
    assert (destination / 'workspaces/task.txt').read_text() == 'keep'


def test_skill_capture_failure_preserves_jira_and_explicit_fallback_reuses_it(env, monkeypatch):
    _, _, conn = env
    monkeypatch.setattr(execution.jira, 'get_issue_snapshot', lambda key: {'key': key, 'description': 'first snapshot'})
    with transaction(conn):
        job = repo.create_job(conn, 'MC3-8', 'HPC', 'rm')
        work = repo.claim_next_turn(conn)
    runner = execution.JiraLearningRunner()
    async def run():
        await runner._run(work['job'],work['turn'])
        if runner._collectors:
            await asyncio.gather(*list(runner._collectors.values()))
    asyncio.run(run())
    failed = repo.get_job(conn, job['id'])
    assert failed['status'] == 'failed' and failed['source_path']
    from app.services.jira_learning_archive import LearningArchive
    archive=LearningArchive(Path(failed['source_path']).parent)
    assert archive.get('MC3-8:jira:description',body=True)['text']=='first snapshot'
    service.retry(RM, job['id'], repository_only=True)
    updated = repo.get_job(conn, job['id'])
    assert updated['repository_only'] == 1
    source, snapshot, _ = asyncio.run(runner._reload(updated, {}))
    assert archive.get('MC3-8:jira:description',body=True)['text']=='first snapshot'
    assert source['knowledge_snapshot']['repository_only'] is True
    assert PREFIX + 'SKILL.md' in snapshot


def test_same_request_concurrent_submissions_create_only_one_job(env, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    barrier = Barrier(2)
    def search(query, max_results):
        barrier.wait(timeout=5)
        return {'total': 1, 'issues': [{'key': 'MC3-77'}]}
    monkeypatch.setattr(service.jira, 'search_issues', search)
    with ThreadPoolExecutor(max_workers=2) as pool:
        jobs = list(pool.map(lambda _: service.create_job(RM, 'HPC', ['MC3-77'], request_id='concurrent-request'), range(2)))
    assert len({result['job']['id'] for result in jobs}) == 1
    assert sum(result['reused'] for result in jobs) == 1


def test_learning_server_must_differ_from_analysis(env):
    _, conf, _ = env
    conf.write_text(conf.read_text() + '\n[jira_agent:HPC]\nCODEX_WS_URL=ws://unused:4520\n')
    with pytest.raises(Exception, match='独立学习 app-server'):
        domain.load_groups()


def test_historical_list_rechecks_current_jira_access(env, monkeypatch):
    _, _, conn = env
    from app.services import jira_agent_service
    async def handled(user):
        return {'issues': [], 'missing': [], 'total': 0}
    monkeypatch.setattr(jira_agent_service, 'list_handled_issues', handled)
    with transaction(conn):
        repo.add_issue(conn, {'key': 'MC3-1', 'summary': 'old'}, 'HPC', 'rm')
        repo.add_issue(conn, {'key': 'MC3-2', 'summary': 'now hidden'}, 'HPC', 'rm')
    monkeypatch.setattr(service.jira, 'search_issues', lambda query, max_results, validate: {
        'total': 1, 'issues': [{'key': 'MC3-1', 'summary': 'current', 'status': 'Closed'}]})
    result = asyncio.run(service.list_issues(RM, 'HPC'))
    assert [issue['key'] for issue in result['issues']] == ['MC3-1']
    assert result['issues'][0]['summary'] == 'current'
    assert result['missing'] == ['MC3-2']
    assert len(repo.list_issues(conn, 'HPC')) == 2  # Addition/history itself remains.


def test_new_case_proposals_are_rejected(env):
    _, _, conn = env
    with pytest.raises(ValueError, match='未知提案类型'):
        stage(conn, kind='case')
    assert repo.list_jobs(conn) == []


@pytest.mark.parametrize('status', ['pending', 'approved'])
def test_legacy_case_remains_readable_without_pending_counts(env, status):
    _, _, conn = env
    job, proposal = stage(conn)
    other, current = stage(conn, key='MC3-2')
    with transaction(conn):
        # Simulate a record stored before case proposals were retired.
        conn.execute("UPDATE jira_learning_proposals SET kind='case', status=? WHERE id=?", (status, proposal['id']))
    before = dict(conn.execute('SELECT * FROM jira_learning_proposals WHERE id=?', (proposal['id'],)).fetchone())
    result = service.get_job(job['id'])['proposals'][0]
    assert result['kind'] == 'case' and result['status'] == status
    assert result['patch_text'] == proposal['patch_text']
    assert result['evidence_ids'] == ['jira:description']
    assert not result.get('review_blocker')
    for jobs in (repo.list_jobs(conn), repo.issue_overview(conn, 'HPC')):
        old = next(item for item in jobs if item['id'] == job['id'])
        assert old['pending_count'] == old['update_count'] == 0
        active = next(item for item in jobs if item['id'] == other['id'])
        assert active['pending_count'] == 1
    assert repo.related_proposals(conn, other['id']) == []
    assert [p['id'] for p in repo.related_proposals(conn, job['id'])] == [current['id']]
    assert dict(conn.execute('SELECT * FROM jira_learning_proposals WHERE id=?', (proposal['id'],)).fetchone()) == before
