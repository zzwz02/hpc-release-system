"""Historical inputs and public workflows survive the unified learning pipeline."""
import asyncio
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routers.jira_learning import router
from app.db.connection import transaction
from app.db.jira_agent_connection import init_jira_agent_db
from app.deps import require_login
from app.repositories import jira_learning_repo as repo
from app.services import jira_learning_handoff as handoff, jira_learning_service as service
from app.services import jira_learning_snapshots as snapshots, jira_learning_runner as execution
from app.services.jira_learning_archive import LearningArchive
from app.services.jira_learning_materials import MaterialStore
from tests.learning_helpers import commit
from tests.test_jira_learning_workflow import env, stage, RM
from tests.test_jira_learning_joint import two_proposals


def test_legacy_import_preserves_ids_reads_and_pinned_files_atomically(env, monkeypatch):
    _, _, conn = env
    job, proposal = stage(conn)
    original = snapshots.read(job)
    original['sources'].append({'id': 'legacy:comment', 'kind': 'comment', 'text': 'verified', 'independent': True})
    with transaction(conn):
        repo.update_job(conn, job['id'], **snapshots.write(job['id'], original))
    job = repo.get_job(conn, job['id'])
    manifest = Path(job['source_path']).read_bytes()
    archive = LearningArchive(Path(job['source_path']).parent)
    archive.path.parent.mkdir()
    content = b'cached observation'
    digest = hashlib.sha256(content).hexdigest()
    (archive.path.parent / digest).write_bytes(content)
    index = {'items': {'legacy:file': {'id': 'legacy:file', 'kind': 'owner_upload', 'name': 'result.log',
        'independent': True, 'cache': digest, 'origin': {'path': '/missing/original'}}},
        'reads': {'old-read': {'source_id': 'legacy:file', 'success': True, 'action': 'read'}}}
    index_path = archive.path.parent / 'index.json'
    index_path.write_text(json.dumps(index))
    original_put = LearningArchive.put
    def interrupted(self, connection, item, **kwargs):
        if item['id'] == 'legacy:comment':
            raise RuntimeError('simulated migration interruption')
        return original_put(self, connection, item, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(LearningArchive, 'put', interrupted)
        with pytest.raises(RuntimeError, match='interruption'):
            snapshots.load(job)
    assert not archive.path.exists()
    with ThreadPoolExecutor(max_workers=2) as pool:
        loaded = list(pool.map(lambda _: snapshots.load(job), range(2)))
    assert all(source['format'] == 2 for _, source in loaded)
    assert archive.references(proposal['evidence_ids'], bodies=True)[0]['text'] == original['sources'][0]['text']
    assert len(archive.validation()['sources']) == 2
    assert len(archive.validation()['material_reads']) == 1
    response = MaterialStore(archive.directory).request({'action': 'read', 'source_id': 'legacy:file',
        'offset': 0, 'limit': 100, 'reason': '核对固定材料'}, 'new-read')
    assert json.loads(response['contentItems'][0]['text'])['text'] == content.decode()
    assert Path(job['source_path']).read_bytes() == manifest
    assert json.loads(index_path.read_text()) == index


def test_same_snapshot_has_stable_hash_and_original_revisions_remain(env):
    _, _, conn = env
    job, _ = stage(conn)
    before = Path(job['source_path']).read_bytes()
    first = snapshots.write(job['id'], {'issue': {'key': 'MC3-1'}, 'sources': []})
    again = snapshots.write(job['id'], {'sources': [], 'issue': {'key': 'MC3-1'}})
    assert first == again
    assert Path(first['source_path']).stat().st_mode & 0o777 == 0o600
    assert Path(job['source_path']).read_bytes() == before
    with pytest.raises(ValueError, match='校验失败'):
        snapshots.read({**job, **first, 'source_sha256': 'bad'})


def test_shared_deployment_preserves_legacy_events_and_group_scope(env):
    root, _, conn = env
    job, proposals = two_proposals(conn)
    sha = commit(root, {f['path']: f['updated_text'] for p in proposals for f in p['files']})
    handoff.publish(RM, job['id'], [p['id'] for p in proposals], sha, '')
    handoff.deploy(RM, job['id'], 'analysis', sha, 'succeeded', 'verified')
    first = repo.list_deployments(conn, proposals[0]['id'])
    assert first == repo.list_deployments(conn, proposals[1]['id'])
    assert len(first) == 1 and first[0]['verification'] == 'manual'
    with transaction(conn):
        other = repo.create_job(conn, 'OTHER-1', 'OTHER', 'rm')
        repo.add_proposals(conn, other['id'], [{**proposals[0], 'previous_id': ''}])
        other_proposal = repo.list_proposals(conn, other['id'])[0]
        conn.execute("UPDATE jira_learning_proposals SET status='published',published_commit=? WHERE id=?", (sha, other_proposal['id']))
        conn.execute('INSERT INTO jira_learning_deployments VALUES (?,?,?,?,?,?,?,?)',
                     ('legacy-deploy', proposals[0]['id'], 'analysis', sha, 'failed', 'old note', 'rm', '2026-01-01 00:00:00'))
    init_jira_agent_db(conn)
    init_jira_agent_db(conn)
    records = repo.list_deployments(conn, proposals[1]['id'])
    assert len(records) == 2 and len({row['id'] for row in records}) == 2
    assert next(row for row in records if row['id'] == 'legacy-deploy')['note'] == 'old note'
    assert repo.list_deployments(conn, other_proposal['id']) == []
    with pytest.raises(Exception, match='入库版本'):
        handoff.deploy(RM, job['id'], 'analysis', '0'*40, 'succeeded', '')
    assert len(repo.list_deployments(conn, proposals[0]['id'])) == 2


def test_http_creation_and_single_batch_publication_share_one_contract(env):
    root, _, conn = env
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[require_login] = lambda: RM
    with TestClient(app) as client:
        body = {'issue_keys': ['MC3-10', 'MC3-11'], 'agent_group': 'HPC', 'request_id': 'http-once', 'repository_only': True}
        result = client.post('/api/jira-agent/learning/jobs', json=body)
        assert result.status_code == 200
        assert set(result.json()) == {'job', 'reused'}
        assert result.json()['job']['issue_keys'] == body['issue_keys']
        assert client.post('/api/jira-agent/learning/jobs', json=body).json()['reused']
        job, proposals = two_proposals(conn)
        sha = commit(root, {f['path']: f['updated_text'] for p in proposals for f in p['files']})
        assert client.post(f"/api/jira-agent/learning/proposals/{proposals[0]['id']}/publication", json={'commit_sha': sha}).status_code == 200
        assert client.post(f"/api/jira-agent/learning/jobs/{job['id']}/publication", json={'proposal_ids': [proposals[1]['id']], 'commit_sha': sha}).status_code == 200
        response = client.post(f"/api/jira-agent/learning/jobs/{job['id']}/deployments", json={
            'target': 'validation', 'commit_sha': sha, 'result': 'succeeded', 'note': 'checked'})
        assert response.status_code == 200
        details = client.get(f"/api/jira-agent/learning/jobs/{job['id']}").json()
        assert len({p['deployments'][0]['id'] for p in details['proposals']}) == 1


def test_refresh_of_cancelled_import_retains_pending_snapshot_sources(env):
    from app.services import jira_learning_relations as relations
    _, _, conn = env
    first, a = stage(conn)
    _, b = stage(conn, key='MC3-2')
    combined = relations.combine(RM, [a['id'], b['id']], '', 'pending-import')['job']
    service.cancel(RM, combined['id'])
    refreshed = service.refresh(RM, combined['id'])['job']
    asyncio.run(execution.JiraLearningRunner()._reload(repo.get_job(conn, refreshed['id']), {}))
    source = service.get_source(refreshed['id'])
    assert len(source['sources']) == 2
    assert source['combination'][0]['evidence_ids'] == [first['id'] + ':' + sid for sid in a['evidence_ids']]


def test_migration_and_combination_keep_historical_collection_limits(env, tmp_path):
    _, _, conn = env
    job, _ = stage(conn)
    source = snapshots.read(job)
    source['acquisition'] = {'status': 'partial'}
    source['collection_gaps'] = [{'source': 'missing-log', 'reason': '历史日志不可读', 'category': 'blocking'}]
    with transaction(conn):
        repo.update_job(conn, job['id'], **snapshots.write(job['id'], source))
    original, converted = snapshots.load(repo.get_job(conn, job['id']))
    assert converted['progress']['limited'] == 1
    assert converted['collection_gaps'][0]['reason'] == '历史日志不可读'
    complete = LearningArchive(tmp_path / 'complete')
    complete.initialize([source['issue']])
    complete.put_case(job['issue_key'], {}, local=True)
    complete.put_case(job['issue_key'], {'issue': source['issue'], 'acquisition': {'status': 'complete'}})
    combined = LearningArchive(tmp_path / 'combined')
    combined.initialize([source['issue']])
    combined.inherit(original.directory, 'limited:')
    combined.inherit(complete.directory, 'complete:')
    assert combined.bundle()['acquisition']['status'] == 'partial'
    assert combined.bundle()['collection_gaps'][0]['reason'] == '历史日志不可读'


def test_legacy_remote_turn_finishes_before_tool_enabled_followup_starts(env):
    from tests.test_jira_learning_joint import Learner
    _, conf, conn = env
    job, proposal = stage(conn)
    initial = repo.list_turns(conn, job['id'])[0]
    source = snapshots.read(job)
    source['knowledge_snapshot'] = json.loads(initial['snapshot_json'])
    result = {'proposals': [{**proposal, 'kind': 'do_not_publish', 'files': [], 'previous_id': '',
                            'title': '保留已有规则', 'evidence_ids': [], 'dependencies': []}]}
    with transaction(conn):
        repo.update_job(conn, job['id'], status='running', phase='recovering', **snapshots.write(job['id'], source))
        repo.update_turn(conn, initial['id'], status='running', codex_turn_id='legacy-turn', started_at=initial['created_at'])
    fake = Learner().start()
    fake.thread_turns['thread1'] = ['legacy-turn']
    fake.turn_state['legacy-turn'] = {'id': 'legacy-turn', 'status': 'completed', 'items': [
        {'type': 'agentMessage', 'id': 'legacy-answer', 'text': json.dumps(result)}]}
    conf.write_text(conf.read_text().replace('ws://unused:4520', fake.url).replace('CODEX_WS_TOKEN=test', 'CODEX_WS_TOKEN=' + fake.token))
    async def exercise():
        runner = execution.JiraLearningRunner()
        await runner._run(repo.get_job(conn, job['id']), repo.get_turn(conn, initial['id']))
        recovered = repo.get_job(conn, job['id'])
        assert recovered['status'] == 'completed', recovered['error']
        assert recovered['thread_id'] == 'thread1'
        assert not fake.params('thread/start') and not fake.params('turn/start')
        service.send_message(RM, job['id'], '继续核对可比条件')
        with transaction(conn):
            work = repo.claim_next_turn(conn)
        await runner._run(work['job'], work['turn'])
        finished = repo.get_job(conn, job['id'])
        assert finished['status'] == 'completed', finished['error']
        assert finished['thread_id'] != 'thread1'
        assert len(fake.params('thread/start')) == len(fake.params('turn/start')) == 1
        assert fake.params('thread/start')[0]['dynamicTools'][0]['name'] == 'read_learning_material'
        context = json.loads(fake.files['/learning/workspaces/' + job['id'] + '/proposals.json'])
        assert context['conversation_history'][0]['input_text'] == initial['input_text']
        assert json.loads(context['conversation_history'][0]['response_text']) == result
        assert all(turn['status'] == 'completed' for turn in repo.list_turns(conn, job['id']))
        assert not snapshots.read(finished).get('legacy_thread_without_tools')
        assert repo.get_proposal(conn, proposal['id'])['superseded_by']
        await runner.stop()
    try:
        asyncio.run(exercise())
    finally:
        fake.stop()
