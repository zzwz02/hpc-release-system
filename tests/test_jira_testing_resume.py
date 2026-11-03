"""Manual test recovery through the real queue, protocol peer and evaluation flow."""
import json

import pytest

from app.db.connection import transaction
from app.repositories import jira_agent_repo as agent
from app.services import jira_agent_runner as runner
from tests.test_jira_agent import env, _wait, _done
from tests.test_jira_testing import setup, start, BASE, forbid_jira
from tests.test_jira_testing_batch import grading_setup, suite, wait_eval, wait_suite


def resume(env, cid, turn):
    return env.client.post(BASE + f'/runs/{cid}/resume', json={'turn_id': turn['id']})


@pytest.mark.parametrize('plan', ['capacity_after_tool', 'stream_error', 'auth_error', 'invalid_result'])
def test_manual_resume_preserves_context_and_regrades_without_duplicate_runs(grading_setup, monkeypatch, plan):
    env, peer, root, body, case = grading_setup
    calls = forbid_jira(monkeypatch)
    env.fake.plans = [plan]
    batch = suite(env, [case['id']])
    value = wait_suite(env, batch['id'], lambda s: not s['active'])
    run = value['entries'][0]['batch']['runs'][0]
    cid = run['conversation_id']
    initial = _wait(env, cid, _done)
    failed = initial['turns'][-1]
    assert failed['status'] == 'failed' and run['can_resume']
    assert failed['failure_kind'] == ('response' if plan == 'invalid_result' else 'model')
    previous = wait_eval(env, cid, 'skipped')
    writes = len(env.fake.params('fs/writeFile'))
    # A deployed skill update must not replace the snapshot in this workspace.
    (root / 'AGENTS.md').write_text('NEW_DEPLOYED_RULE')
    env.fake.plans = ['complete']
    request = resume(env, cid, failed)
    assert request.status_code == 200, request.text
    assert resume(env, cid, failed).json() == request.json()
    completed = _wait(env, cid, _done)
    result = wait_eval(env, cid)
    value = wait_suite(env, batch['id'], lambda s: not s['active'])
    assert value['total'] == value['finished'] == 1
    assert value['counts'] == {'pass': 1}
    run = value['entries'][0]['batch']['runs'][0]
    assert run['resume_count'] == 1 and not run['can_resume']
    assert completed['turns'][0] == failed
    assert len(completed['turns']) == 2
    assert completed['turns'][1]['status'] == 'completed'
    assert completed['conversation']['thread_id'] == initial['conversation']['thread_id']
    assert completed['conversation']['workspace'] == initial['conversation']['workspace']
    assert env.fake.threads == 1 and env.fake.turns == 2
    assert len(env.fake.params('fs/writeFile')) == writes
    assert result['snapshot'] == previous['snapshot']
    assert result['id'] != previous['id']
    final = env.fake.params('turn/start')[-1]
    assert final['model'] == 'tested-model'
    assert final['outputSchema'] == env.fake.params('turn/start')[0]['outputSchema']
    assert '不重复启动' in json.dumps(final, ensure_ascii=False)
    assert 'REVIEW_ONLY_CRITERIA' not in json.dumps(final)
    assert 'SECRET_REFERENCE_RESULT' not in json.dumps(final)
    assert not calls and not env.jira.comments
    assert resume(env, cid, completed['turns'][-1]).status_code == 409


def test_running_remote_is_adopted_and_survives_site_restart(setup):
    env, _, _, case = setup
    env.fake.plans = ['gate']
    cid = start(env, case)['runs'][0]['conversation_id']
    active = _wait(env, cid, lambda d: d['conversation']['phase'] == 'running')
    turn = active['turns'][-1]
    # Emulate a previously exhausted transport recovery while the remote experiment lives on.
    env.stop_site()
    with runner.open_db() as conn, transaction(conn):
        agent.update_turn(conn, turn['id'], status='failed', failure_kind='model', error='Codex connection lost')
    env.start_site()
    assert resume(env, cid, turn).status_code == 200
    _wait(env, cid, lambda d: d['conversation']['phase'] == 'running')
    assert env.fake.turns == 1
    assert not env.fake.params('turn/interrupt')
    env.stop_site()
    env.start_site()
    env.fake.release()
    done = _wait(env, cid, _done)
    assert done['turns'][-1]['status'] == 'completed'
    assert env.fake.turns == 1
    assert len(env.client.get(BASE + f'/cases/{case["id"]}/batches').json()['batches'][0]['runs']) == 1


@pytest.mark.parametrize('damage', ['workspace', 'skill'])
def test_missing_or_changed_workspace_does_not_start_another_experiment(setup, damage):
    env, _, _, case = setup
    env.fake.plans = ['stream_error']
    cid = start(env, case)['runs'][0]['conversation_id']
    failed = _wait(env, cid, _done)
    root = failed['conversation']['workspace']
    if damage == 'workspace':
        env.fake.files.pop(root + '/issue.json')
    else:
        env.fake.files[root + '/AGENTS.md'] = b'changed'
    assert resume(env, cid, failed['turns'][-1]).status_code == 200
    done = _wait(env, cid, _done)
    assert done['turns'][-1]['status'] == 'failed'
    assert '请重新运行测试' in done['turns'][-1]['error']
    assert env.fake.turns == 1


def test_resume_permissions_cancellation_and_stale_requests(setup):
    env, _, _, case = setup
    env.fake.plans = ['stream_error']
    cid = start(env, case)['runs'][0]['conversation_id']
    failed = _wait(env, cid, _done)['turns'][-1]
    env.as_user('alice')
    assert resume(env, cid, failed).status_code == 403
    env.as_user('carol')
    assert resume(env, cid, {'id': 'stale'}).status_code == 409
    env.fake.plans = ['hold']
    assert resume(env, cid, failed).status_code == 200
    _wait(env, cid, lambda d: d['conversation']['phase'] == 'running')
    assert env.client.post(f'/api/jira-agent/conversations/{cid}/cancel').status_code == 200
    done = _wait(env, cid, _done)
    assert done['turns'][-1]['status'] == 'cancelled'
    assert resume(env, cid, done['turns'][-1]).status_code == 409


def test_non_model_failure_cannot_be_resumed(setup):
    env, _, _, case = setup
    cid = start(env, case)['runs'][0]['conversation_id']
    turn = _wait(env, cid, _done)['turns'][-1]
    with runner.open_db() as conn, transaction(conn):
        agent.update_turn(conn, turn['id'], status='failed', result_json='', failure_kind='', error='实验编译失败')
    assert resume(env, cid, turn).status_code == 409
    assert env.fake.turns == 1


def test_lost_resume_start_reply_does_not_start_twice(setup):
    env, _, _, case = setup
    env.fake.plans = ['stream_error', 'complete']
    cid = start(env, case)['runs'][0]['conversation_id']
    failed = _wait(env, cid, _done)['turns'][-1]
    env.fake.lose_start_reply = True
    assert resume(env, cid, failed).status_code == 200
    done = _wait(env, cid, _done)
    assert done['turns'][-1]['status'] == 'completed'
    assert env.fake.turns == 2


def test_repeated_manual_recovery_keeps_all_failures_and_one_summary(grading_setup):
    env, _, _, _, case = grading_setup
    env.fake.plans = ['stream_error', 'auth_error', 'complete']
    batch = suite(env, [case['id']])
    value = wait_suite(env, batch['id'], lambda s: not s['active'])
    cid = value['entries'][0]['batch']['runs'][0]['conversation_id']
    first = _wait(env, cid, _done)['turns'][-1]
    assert resume(env, cid, first).status_code == 200
    second = _wait(env, cid, _done)['turns'][-1]
    assert second['status'] == 'failed'
    wait_eval(env, cid, 'skipped')
    assert resume(env, cid, first).json()['turn_id'] == second['id']
    assert env.fake.turns == 2
    assert resume(env, cid, second).status_code == 200
    value = wait_suite(env, batch['id'], lambda s: not s['active'])
    assert value['total'] == 1 and value['counts'] == {'pass': 1}
    run = value['entries'][0]['batch']['runs'][0]
    assert run['resume_count'] == 2
    assert len(_wait(env, cid, _done)['turns']) == 3
