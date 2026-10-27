"""Real SQLite + HTTP + two local app-server peers; no Jira or GPU side effects."""
import asyncio
import copy
import json
import time
import uuid

import pytest

from app.config import settings
from app.db.connection import transaction
from app.domain import jira_test_evaluation as grading
from app.repositories import jira_test_evaluation_repo as evaluations, jira_agent_repo as agent
from app.services import jira_agent_runner, jira_testing_runner
from tests.test_jira_agent import _preflight, env, FakeCodex, _wait, _done
from tests.test_jira_testing import setup, start, BASE, forbid_jira


class Evaluator(FakeCodex):
    async def _play(self, params, turn_id, plan):
        if plan == 'hold':
            await self._released(turn_id)
        cwd = self.thread_cwd[params['threadId']]
        if cwd + '/input.json' in self.files:
            self.turn_state[turn_id].update(status='completed', items=[{'type':'agentMessage', 'id':'draft', 'text':json.dumps({'criteria':['复现问题并提供运行证据']})}])
            return
        material = json.loads(self.files[cwd + '/material.json'])
        source = next(item['id'] for item in material['sources'] if item['kind'] == 'agent_artifact')
        response = {'decision': 'pass', 'summary': '完成约定分析并交付运行证据。', 'criteria': [
            {'id': item['id'], 'status': 'met', 'reason': '交付记录与标准相符', 'evidence_ids': [source]}
            for item in material['standards']['criteria']]}
        if plan == 'invalid':
            response['criteria'][0]['evidence_ids'] = ['invented']
        self.turn_state[turn_id].update(status='completed', items=[{'type': 'agentMessage', 'id': 'answer', 'text': json.dumps(response)}])


@pytest.fixture
def grading_setup(setup, monkeypatch):
    env, root, body, case = setup
    peer = Evaluator().start()
    monkeypatch.setattr(jira_testing_runner, 'SCAN_SECONDS', 0.05)
    with env.conf.open('a') as config:
        config.write(f'\n[jira_evaluation:HPC]\nCODEX_WS_URL = {peer.url}\nCODEX_WS_TOKEN = {peer.token}\nWORKSPACE_ROOT = /evaluation\nMODEL = judge-model\nREASONING_EFFORT = high\nTURN_TIMEOUT_SECONDS = 120\n')
    yield env, peer, root, body, case
    peer.stop()


def suite(env, ids, request=None):
    request = request or str(uuid.uuid4())
    cache = getattr(env, 'suite_proofs', {})
    if request not in cache:
        cache[request] = _preflight(env, context={'kind': 'suite', 'case_ids': ids})
        env.suite_proofs = cache
    response = env.client.post(BASE + '/suites', json={'case_ids': ids, 'agent_group': 'HPC',
        'model': 'tested-model', **cache[request], 'request_id': request})
    assert response.status_code == 200, response.text
    return response.json()


def wait_suite(env, sid, condition):
    deadline = time.monotonic() + 12
    while time.monotonic() < deadline:
        response = env.client.get(BASE + '/suites/' + sid)
        assert response.status_code == 200, response.text
        value = response.json()
        if condition(value):
            return value
        time.sleep(.05)
    raise AssertionError(value)


def wait_eval(env, cid, status='completed'):
    deadline = time.monotonic() + 12
    while time.monotonic() < deadline:
        response = env.client.get(BASE + f'/runs/{cid}/evaluations')
        value = response.json()['evaluations'][0]
        if value['status'] == status:
            return value
        time.sleep(.05)
    raise AssertionError(value)


def test_new_evaluation_completes_with_materialized_thread_required(grading_setup):
    env, peer, root, body, case = grading_setup
    cid = start(env, case)['runs'][0]['conversation_id']
    result = wait_eval(env, cid)
    assert result['error'] == ''
    assert result['result']['decision'] == 'pass'
    assert peer.turns == env.fake.turns == 1
    assert peer.params('thread/turns/list')


def test_batch_freezes_versions_uses_one_snapshot_and_recovers(grading_setup, monkeypatch):
    env, peer, root, body, case = grading_setup
    other = copy.deepcopy(body)
    other['title'] = 'Second case'
    other['input']['note'] = 'another question'
    second = env.client.post(BASE + '/cases', json=other).json()
    calls = forbid_jira(monkeypatch)
    env.fake.plans = ['gate', 'complete']
    key = str(uuid.uuid4())
    submitted = suite(env, [case['id'], second['id']], key)
    assert suite(env, [case['id'], second['id']], key)['id'] == submitted['id']
    changed = copy.deepcopy(body)
    changed['criteria'] = 'NEW_STANDARD_AFTER_SUBMISSION'
    changed['input']['note'] = 'NEW_INPUT_AFTER_SUBMISSION'
    env.client.post(BASE + f'/cases/{case["id"]}/versions', json=changed)
    value = wait_suite(env, submitted['id'], lambda s: all(item['batch'] for item in s['entries']))
    first, next_run = [item['batch']['runs'][0]['conversation_id'] for item in value['entries']]
    _wait(env, first, lambda d: d['conversation']['phase'] == 'running')
    assert value['entries'][1]['batch']['runs'][0]['status'] == 'queued'
    conflict = env.client.post(BASE + '/suites', json={'case_ids':[case['id']], 'agent_group':'HPC', 'model':'m', 'request_id':str(uuid.uuid4())})
    assert conflict.status_code == 409
    env.stop_site()
    env.start_site()
    env.fake.release()
    completed = wait_suite(env, submitted['id'], lambda s: not s['active'])
    assert completed['counts'] == {'pass': 2}
    assert env.fake.turns == 2 and peer.turns == 2
    assert len([p for p in env.fake.params('command/exec') if p['command'][:2] == ['python3','-c']]) == 1
    assert 'NEW_STANDARD_AFTER_SUBMISSION' not in str(peer.files)
    assert 'NEW_INPUT_AFTER_SUBMISSION' not in str(env.fake.files)
    assert 'SECRET_REFERENCE_RESULT' not in str(env.fake.requests) + str(env.fake.files)
    assert 'SECRET_REFERENCE_RESULT' in str(peer.files)
    assert all(p['sandbox'] == 'read-only' and p['approvalPolicy'] == 'never' for p in peer.params('thread/start'))
    assert all(p['model'] == 'judge-model' for p in peer.params('turn/start'))
    assert not calls
    assert _wait(env, next_run, _done)['turns'][0]['post_comment'] is False


def test_manual_review_is_optional_and_invalidated_by_new_evidence(grading_setup):
    env, peer, root, body, case = grading_setup
    result = suite(env, [case['id']])
    complete = wait_suite(env, result['id'], lambda s: not s['active'])
    run = complete['entries'][0]['batch']['runs'][0]
    cid = run['conversation_id']
    assert run['current']['source'] == 'ai' and run['reviews'] == []
    response = env.client.post(BASE + f'/runs/{cid}/review', json={'decision':'pass', 'confirm_ai':True,
        'evaluation_id':run['evaluation']['id'], 'evidence_key':run['evidence_key']})
    assert response.status_code == 200, response.text
    manual = env.client.get(BASE + '/suites/' + result['id']).json()
    assert manual['counts'] == {'pass':1}
    assert manual['ai_counts'] == {'pass':1}
    assert manual['entries'][0]['batch']['runs'][0]['current']['source'] == 'human'
    # Re-evaluate unchanged evidence: human decision remains current.
    assert env.client.post(BASE + f'/runs/{cid}/evaluate').status_code == 200
    wait_eval(env, cid)
    assert env.client.get(BASE + '/suites/' + result['id']).json()['entries'][0]['batch']['runs'][0]['current']['source'] == 'human'
    path = env.tmp / 'extra.log'
    path.write_text('additional evidence')
    with jira_agent_runner.open_db() as conn, transaction(conn):
        turn = agent.latest_turn(conn, cid)
        agent.add_file(conn, conversation_id=cid, turn_id=turn['id'], direction='output', source='artifact',
                       name='extra.log', size=path.stat().st_size, local_path=str(path), sha256='new')
    assert env.client.post(BASE + f'/runs/{cid}/review', json={'decision':'pass','note':'old page', 'evidence_key':run['evidence_key']}).status_code == 409
    changed = env.client.get(BASE + '/suites/' + result['id']).json()
    assert changed['entries'][0]['batch']['runs'][0]['current']['decision'] == 'stale'
    assert not changed['entries'][0]['batch']['runs'][0]['reviews'][0]['current']


def test_failed_grading_retries_without_running_experiment_and_resumes(grading_setup):
    env, peer, root, body, case = grading_setup
    peer.plans = ['invalid','hold']
    batch = start(env, case)
    cid = batch['runs'][0]['conversation_id']
    failed = wait_eval(env, cid, 'failed')
    assert '不存在' in failed['error']
    assert env.client.post(BASE + f'/runs/{cid}/evaluate').status_code == 200
    deadline = time.monotonic() + 10
    while peer.turns < 2 and time.monotonic() < deadline:
        time.sleep(.05)
    assert peer.turns == 2
    env.stop_site()
    env.start_site()
    peer.release()
    completed = wait_eval(env, cid)
    assert completed['result']['decision'] == 'pass'
    assert env.fake.turns == 1 and peer.turns == 2
    assert len(env.client.get(BASE + f'/runs/{cid}/evaluations').json()['evaluations']) == 2


def test_stop_only_cancels_queued_and_missing_criteria_needs_manual_evaluation(grading_setup):
    env, peer, root, body, case = grading_setup
    other = copy.deepcopy(body)
    other.update(title='no criteria', criteria='')
    second = env.client.post(BASE + '/cases', json=other).json()
    env.fake.plans = ['gate']
    submitted = suite(env, [second['id'], case['id']])
    ready = wait_suite(env, submitted['id'], lambda s: s['entries'][0]['batch'] and s['entries'][0]['batch']['runs'][0]['status'] == 'running')
    cid = ready['entries'][0]['batch']['runs'][0]['conversation_id']
    stopped = env.client.post(BASE + f'/suites/{submitted["id"]}/stop').json()
    assert stopped['entries'][0]['batch']['runs'][0]['status'] == 'running'
    assert stopped['entries'][1]['batch']['runs'][0]['status'] == 'cancelled'
    env.fake.release()
    wait_suite(env, submitted['id'], lambda s: not s['active'])
    evaluation = wait_eval(env, cid, 'skipped')
    assert evaluation['error'] == '未设置通过标准' and peer.turns == 0
    assert env.client.post(BASE + f'/runs/{cid}/review', json={'decision':'pass', 'note':'人工核对了运行和交付记录'}).status_code == 200
    final = env.client.get(BASE + f'/suites/{submitted["id"]}').json()
    assert final['counts'] == {'pass': 1, 'cancelled': 1}
    assert final['reviewed'] == 1 and not final['active']


def test_manual_evaluation_before_ai_completion_survives_and_can_be_updated(grading_setup):
    env, peer, root, body, case = grading_setup
    peer.plans = ['hold']
    submitted = suite(env, [case['id']])
    running = wait_suite(env, submitted['id'], lambda s: s['phase'] == 'evaluating')
    cid = running['entries'][0]['batch']['runs'][0]['conversation_id']
    wait_eval(env, cid, 'running')
    assert running['finished'] == 1 and running['evaluated'] == 0
    review = env.client.post(BASE + f'/runs/{cid}/review', json={'decision':'partial','note':'缺少独立验证'})
    assert review.status_code == 200, review.text
    before = env.client.get(BASE + '/suites/' + submitted['id']).json()
    assert before['counts'] == {'partial':1} and before['reviewed'] == 1
    peer.release()
    completed = wait_suite(env, submitted['id'], lambda s: not s['active'])
    assert completed['phase'] == 'completed' and completed['evaluated'] == 1
    assert completed['counts'] == {'partial':1} and completed['ai_counts'] == {'pass':1}
    assert env.client.post(BASE + f'/runs/{cid}/review', json={'decision':'fail','note':'重新检查后，结论缺少证据'}).status_code == 200
    final = env.client.get(BASE + '/suites/' + submitted['id']).json()
    assert final['counts'] == {'fail':1} and final['reviewed'] == 1
    assert len(final['entries'][0]['batch']['runs'][0]['reviews']) == 2


def test_manual_evaluation_works_when_ai_fails(grading_setup):
    env, peer, root, body, case = grading_setup
    peer.plans = ['invalid']
    submitted = suite(env, [case['id']])
    completed = wait_suite(env, submitted['id'], lambda s: not s['active'])
    cid = completed['entries'][0]['batch']['runs'][0]['conversation_id']
    assert completed['counts'] == {'evaluation_failed':1}
    assert completed['phase'] == 'completed' and completed['reviewed'] == 0
    assert env.client.post(BASE + f'/runs/{cid}/review', json={'decision':'pass','note':'已人工核对标准和交付'}).status_code == 200
    final = env.client.get(BASE + '/suites/' + submitted['id']).json()
    assert final['counts'] == {'pass':1} and final['ai_counts'] == {'evaluation_failed':1}


@pytest.mark.parametrize('repeated', [False, True])
def test_old_single_case_runs_are_visible_in_batch_records(setup, repeated):
    env, root, body, case = setup
    batch = start(env, case)
    cid = batch['runs'][0]['conversation_id']
    _wait(env, cid, _done)
    if repeated:
        other = start(env, case)
        _wait(env, other['runs'][0]['conversation_id'], _done)
        with jira_agent_runner.open_db() as conn, transaction(conn):
            conn.execute('UPDATE jira_test_runs SET batch_id=?,ordinal=2 WHERE batch_id=?', (batch['id'], other['id']))
            conn.execute('DELETE FROM jira_test_batches WHERE id=?', (other['id'],))
    records = env.client.get(BASE + '/suites').json()['suites']
    assert len(records) == 1
    assert records[0]['id'] == 'legacy:' + batch['id']
    assert records[0]['entries'][0]['batch']['runs'][0]['conversation_id'] == cid
    assert records[0]['total'] == records[0]['finished'] == (2 if repeated else 1)
    assert 'preparing' not in records[0]['counts']
    assert env.client.get(BASE + '/suites/' + records[0]['id']).status_code == 200


def test_standard_update_import_is_explicit_and_historical_snapshot_stays(setup):
    env, root, body, case = setup
    batch = start(env, case)
    cid = batch['runs'][0]['conversation_id']
    draft = copy.deepcopy(body)
    draft.update(criteria='new criterion', package_import=True)
    assert env.client.post(BASE + '/cases', json=draft).status_code == 409
    draft['import_update'] = case['id']
    updated = env.client.post(BASE + '/cases', json=draft)
    assert updated.status_code == 200, updated.text
    assert updated.json()['id'] == case['id'] and len(updated.json()['versions']) == 2
    assert env.client.get(BASE + f'/runs/{cid}/evaluations').json()['evaluations'][0]['snapshot']['criteria'][0]['text'] == body['criteria']
    env.as_user('alice')
    assert env.client.get(BASE + '/suites').status_code == 403
    assert env.client.get(BASE + f'/runs/{cid}/evaluations').status_code == 403
    assert env.client.post(BASE + f'/runs/{cid}/evaluate').status_code == 403


@pytest.mark.parametrize('change', ['missing','duplicate','invented','false_pass','incomplete','hidden_failure'])
def test_invalid_ai_verdicts_are_not_saved(change):
    snapshot = {'criteria':[{'id':'C1','text':'reproduce'}, {'id':'C2','text':'deliver'}]}
    material = {'sources':[{'id':'log','text':'measured'}], 'status':'completed', 'delivery_status':'complete'}
    value = {'decision':'pass', 'summary':'done', 'criteria':[{'id':c['id'],'status':'met','reason':'evidence','evidence_ids':['log']} for c in snapshot['criteria']]}
    if change == 'missing': value['criteria'].pop()
    if change == 'duplicate': value['criteria'][1]['id'] = 'C1'
    if change == 'invented': value['criteria'][1]['evidence_ids'] = ['absent']
    if change == 'false_pass': value['criteria'][1]['status'] = 'unknown'
    if change == 'incomplete': material['delivery_status'] = 'incomplete'
    if change == 'hidden_failure':
        value['criteria'][1]['status'] = 'unmet'
        value['decision'] = 'unknown'
    with pytest.raises(ValueError):
        grading.validate(json.dumps(value), snapshot, material)


def test_restart_during_ambiguous_turn_start_does_not_grade_twice(grading_setup):
    env, peer, root, body, case = grading_setup
    peer.turn_start_delay = .8
    cid = start(env, case)['runs'][0]['conversation_id']
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        with jira_agent_runner.open_db() as conn:
            row = evaluations.latest(conn, cid)
        if row['phase'] == 'starting':
            break
        time.sleep(.02)
    assert row['phase'] == 'starting' and row['turn_id'] == ''
    env.stop_site()
    env.start_site()
    assert wait_eval(env, cid)['result']['decision'] == 'pass'
    assert peer.turns == env.fake.turns == 1


def test_simultaneous_submission_is_idempotent_and_preparation_can_retry(setup, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    env, root, body, case = setup
    monkeypatch.setattr(jira_testing_runner, 'SCAN_SECONDS', .05)
    # Failure happens before any queued analysis is created.
    (root / 'AGENTS.md').unlink()
    key = str(uuid.uuid4())
    # Both concurrent submissions refer to the same completed preflight, as a double click does.
    env.suite_proofs = {key: _preflight(env, context={'kind': 'suite', 'case_ids': [case['id']]})}
    with ThreadPoolExecutor(max_workers=2) as pool:
        submitted = list(pool.map(lambda _: suite(env, [case['id']], key), range(2)))
    assert submitted[0]['id'] == submitted[1]['id']
    sid = submitted[0]['id']
    failed = wait_suite(env, sid, lambda s: s['status'] == 'failed')
    assert not failed['active'] and failed['entries'][0]['batch'] is None
    assert env.fake.turns == 0
    (root / 'AGENTS.md').write_text('# Restored identity')
    assert env.client.post(BASE + f'/suites/{sid}/retry').status_code == 200
    finished = wait_suite(env, sid, lambda s: not s['active'])
    assert finished['entries'][0]['batch'] and env.fake.turns == 1
    assert finished['counts'] == {'evaluation_failed':1}  # Missing evaluator config isn't an analysis failure.


def test_cancel_before_preparation_stays_cancelled_on_restart(setup, monkeypatch):
    env, root, body, case = setup
    env.stop_site()
    monkeypatch.setattr(settings, 'jira_testing_runner_enabled', False)
    env.start_site()
    submitted = suite(env, [case['id']])
    stopped = env.client.post(BASE + f'/suites/{submitted["id"]}/stop').json()
    assert stopped['finished'] == 1 and stopped['counts'] == {'cancelled':1}
    env.stop_site()
    monkeypatch.setattr(settings, 'jira_testing_runner_enabled', True)
    env.start_site()
    assert env.client.get(BASE + f'/suites/{submitted["id"]}').json()['status'] == 'stopped'
    assert env.fake.turns == 0


def test_evaluator_cannot_be_the_analysis_service(grading_setup):
    env, peer, root, body, case = grading_setup
    config = env.conf.read_text().replace(peer.url, env.fake.url)
    env.conf.write_text(config)
    cid = start(env, case)['runs'][0]['conversation_id']
    failed = wait_eval(env, cid, 'failed')
    assert '独立' in failed['error']
    assert env.fake.turns == 1 and peer.turns == 0


def test_builtin_standard_changes_are_packaged_without_overwriting_local_edits(tmp_path, monkeypatch):
    from app.domain import jira_test_catalog as catalog
    from app.services import jira_testing_builtins as builtins, jira_testing_service as service
    from tests.test_jira_test_builtins import write_case
    root = tmp_path / 'cases'
    path = write_case(root)
    monkeypatch.setattr(settings, 'jira_testing_cases_dir', root)
    monkeypatch.setattr(settings, 'jira_agent_database_url', f'sqlite:///{tmp_path / "cases.db"}')
    (path / 'review.json').write_text(json.dumps({'criteria':'C1','reference':''}))
    assert builtins.initialize() == ''
    with jira_agent_runner.open_db() as conn:
        cid = conn.execute('SELECT id FROM jira_test_cases').fetchone()[0]
    before = service.detail(cid)
    (path / 'review.json').write_text(json.dumps({'criteria':'C2','reference':'checked'}))
    assert builtins.initialize() == ''
    updated = service.detail(cid)
    assert updated['versions'][0]['input_sha256'] == before['versions'][0]['input_sha256']
    assert updated['versions'][0]['criteria'] == 'C2'
    assert catalog.load(root)[0]['body']['reference'] == 'checked'
    body = updated['versions'][0]
    body['criteria'] = 'human change'
    service.save({'username':'rm'}, body, cid)
    (path / 'review.json').write_text(json.dumps({'criteria':'C3','reference':''}))
    assert builtins.initialize() == ''
    assert service.detail(cid)['versions'][0]['criteria'] == 'human change'
    assert service.detail(cid)['builtin']['update_available']


def test_existing_manual_reviews_survive_schema_upgrade(setup):
    from app.repositories import jira_testing_repo
    env, root, body, case = setup
    cid = start(env, case)['runs'][0]['conversation_id']
    _wait(env, cid, _done)
    assert env.client.post(BASE + f'/runs/{cid}/review', json={'decision':'pass','note':'legacy review'}).status_code == 200
    env.stop_site()
    with jira_agent_runner.open_db() as conn:
        # Restore the previous table definition, then run the actual migration.
        saved = [tuple(row) for row in conn.execute('SELECT * FROM jira_test_reviews')]
        conn.executescript('''DROP TABLE jira_test_review_links;
            DROP TABLE jira_test_reviews;
            CREATE TABLE jira_test_reviews (
                id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL REFERENCES jira_test_runs(conversation_id),
                decision TEXT NOT NULL CHECK(decision IN ('pass','partial','fail','environment','platform','cancelled')),
                note TEXT NOT NULL, reviewer TEXT NOT NULL, created_at TEXT NOT NULL);
        ''')
        conn.executemany('INSERT INTO jira_test_reviews VALUES(?,?,?,?,?,?)', saved)
        conn.commit()
        jira_testing_repo.initialize(conn)
        assert [tuple(row) for row in conn.execute('SELECT * FROM jira_test_reviews')] == saved
        assert not conn.execute('PRAGMA foreign_key_check').fetchall()
        conn.execute("UPDATE jira_test_reviews SET decision='unknown'")
        conn.commit()
    env.start_site()



def test_criteria_draft_is_not_saved_or_sent_to_analysis(grading_setup):
    env, peer, root, body, case = grading_setup
    response = env.client.post(BASE + '/criteria/draft', json={'agent_group':'HPC', 'input':body['input'], 'reference':body['reference']})
    assert response.status_code == 200, response.text
    assert response.json()['criteria'] == '复现问题并提供运行证据'
    assert env.fake.turns == 0
    saved = env.client.get(BASE + '/cases/' + case['id']).json()
    assert len(saved['versions']) == 1 and saved['versions'][0]['criteria'] == body['criteria']
    assert peer.params('thread/start')[0]['sandbox'] == 'read-only'
