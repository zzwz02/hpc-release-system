"""Execution preflight contracts through HTTP, SQLite and a real protocol peer."""
import json
import subprocess
import uuid

import pytest

from app.db.connection import transaction
from app.domain import jira_execution as domain
from app.resources.jira_agent.check_dataset import check
from app.services import jira_agent_runner
from tests.test_jira_agent import env, _preflight, _handover, _upload_key, _wait, _done, SYSTEM_MACHINE


def inspect(env, execution=None, keys=None, **context):
    if execution is None:
        execution = domain.normalize(machine=SYSTEM_MACHINE)
        keys = {SYSTEM_MACHINE: _upload_key(env, SYSTEM_MACHINE)}
    return env.client.post('/api/jira-agent/execution-preflights', json={
        'agent_group': 'HPC', 'execution': execution, 'key_sessions': keys or {},
        'context': {'kind': 'jira', 'issue_key': 'MC3-1', **context},
    })


def submission(receipt, **extra):
    return {'issue_key': 'MC3-1', 'preflight_id': receipt['id'], 'request_id': str(uuid.uuid4()),
            'acknowledged_issue_ids': receipt['snapshot']['warning_ids'], **extra}


def test_two_custom_hosts_authorized_and_consumed_together(env):
    targets = ['tester@node-a', 'tester@node-b']
    env.fake.execution_targets = targets
    keys = {target: _upload_key(env, target) for target in targets}
    config = domain.normalize({'mode': 'custom', 'targets': [{'target': t, 'purpose': f'对照 {i}'} for i, t in enumerate(targets)]})
    first = inspect(env, config, keys).json()
    second = inspect(env, config, keys).json()
    body = submission(first)
    response = env.client.post('/api/jira-agent/conversations', json=body)
    assert response.status_code == 200, response.text
    cid = response.json()['conversation']['id']
    assert response.json()['conversation']['execution'] == config
    assert response.json()['conversation']['machine'] == ''  # never semicolon-encode targets
    assert env.client.post('/api/jira-agent/conversations', json=body).json()['conversation']['id'] == cid
    assert env.client.post('/api/jira-agent/conversations', json={**body, 'note': 'changed'}).status_code == 409
    reused = env.client.post('/api/jira-agent/conversations', json=submission(second, new_conversation=True))
    assert reused.status_code == 409
    result = _wait(env, cid, _done)
    assert result['conversation']['status'] == 'open'
    assert result['conversation']['machines_used'] == targets
    assert result['turns'][0]['result']['machines_used'] == targets
    assert result['conversation']['machine_used'] == ''
    prompt = env.fake.params('turn/start')[0]['input'][0]['text']
    assert all(target in prompt for target in targets)
    assert '按任务需要选择一台或多台' in prompt and SYSTEM_MACHINE not in prompt
    with jira_agent_runner.open_db() as conn:
        assert conn.execute('SELECT count(*) FROM jira_agent_key_claims').fetchone()[0] == 2


def test_data_warning_requires_exact_ack_and_is_saved(env):
    env.fake.probe_results[SYSTEM_MACHINE] = {'dataset': {'status': 'fail', 'code': 'source_mismatch', 'message': '实际挂载来源与预期不符'}}
    config = domain.normalize(machine=SYSTEM_MACHINE)
    receipt = inspect(env, config, {SYSTEM_MACHINE: _upload_key(env, SYSTEM_MACHINE)}).json()
    assert receipt['snapshot']['can_submit']
    assert receipt['snapshot']['warning_ids'] == [SYSTEM_MACHINE + ':dataset']
    body = submission(receipt)
    assert env.client.post('/api/jira-agent/conversations', json={**body, 'acknowledged_issue_ids': []}).status_code == 409
    assert env.client.post('/api/jira-agent/conversations', json={**body, 'acknowledged_issue_ids': ['invented']}).status_code == 409
    response = env.client.post('/api/jira-agent/conversations', json=body)
    assert response.status_code == 200, response.text
    detail = _wait(env, response.json()['conversation']['id'], _done)
    assert detail['conversation']['execution_snapshot']['acknowledged_issue_ids'] == body['acknowledged_issue_ids']
    assert detail['turns'][0]['execution_snapshot']['results'][0]['dataset']['code'] == 'source_mismatch'
    assert any(event['kind'] == 'environment_check' for event in detail['events'])
    assert '用户同意继续不代表数据可用' in env.fake.params('turn/start')[0]['input'][0]['text']


def test_expired_changed_scope_and_changed_policy_cannot_submit(env):
    receipt = inspect(env).json()
    body = submission(receipt)
    assert env.client.post('/api/jira-agent/conversations', json={**body, 'issue_key': 'MC3-2'}).status_code == 409
    with jira_agent_runner.open_db() as conn, transaction(conn):
        conn.execute('UPDATE jira_agent_preflights SET expires_at=0 WHERE id=?', (receipt['id'],))
    assert env.client.post('/api/jira-agent/conversations', json=body).status_code == 409
    receipt = inspect(env).json()
    with env.conf.open('a') as handle:
        handle.write('DATASET_PROBE_EXPECTED_VALUE = changed\n')
    # ConfigParser is strict: replace the existing value instead of duplicating it.
    text = env.conf.read_text().replace('DATASET_PROBE_EXPECTED_VALUE = test-data\n', '')
    env.conf.write_text(text)
    response = env.client.post('/api/jira-agent/conversations', json=submission(receipt))
    assert response.status_code == 409 and '配置已变化' in response.text
    assert env.fake.turns == 0


def test_foreign_receipt_and_legacy_bypass_are_rejected(env):
    receipt = inspect(env).json()
    env.as_user('carol')
    assert env.client.post('/api/jira-agent/conversations', json=submission(receipt)).status_code == 409
    env.as_user('alice')
    assert env.client.post('/api/jira-agent/conversations', json={'issue_key': 'MC3-1'}).status_code == 400
    assert env.client.post('/api/jira-agent/conversations', json={
        'issue_key': 'MC3-1', 'machine': 'u@unapproved', 'request_id': str(uuid.uuid4()),
    }).status_code == 409
    assert env.fake.turns == 0


def test_partial_connections_block_required_set_but_allow_candidate_pool(env):
    targets = ['tester@node-a', 'tester@node-b']
    keys = {target: _upload_key(env, target) for target in targets}
    env.fake.probe_results[targets[1]] = {'ssh_exit': 255}
    config = domain.normalize({'mode': 'custom', 'selection_policy': 'all_required', 'targets': [{'target': t} for t in targets]})
    required = inspect(env, config, keys).json()
    assert not required['snapshot']['can_submit']
    assert env.client.post('/api/jira-agent/conversations', json=submission(required)).status_code == 409
    config['selection_policy'] = 'as_needed'
    optional = inspect(env, config, keys).json()
    assert optional['snapshot']['available_targets'] == [targets[0]]
    assert optional['snapshot']['can_submit'] and optional['snapshot']['requires_acknowledgement']


def test_receipt_consumption_rolls_back_with_task_creation(env, monkeypatch):
    from app.repositories import jira_agent_repo as repo
    target = 'tester@node-a'
    proof = _preflight(env, 'MC3-1', machine=target, ssh_key_session_id=_upload_key(env, target))
    body = {'issue_key': 'MC3-1', **proof}
    original = repo.create_turn
    def fail(*args, **kwargs):
        raise ValueError('injected task creation failure')
    monkeypatch.setattr(repo, 'create_turn', fail)
    assert env.client.post('/api/jira-agent/conversations', json=body).status_code == 400
    with jira_agent_runner.open_db() as conn:
        assert conn.execute('SELECT consumed_by FROM jira_agent_preflights WHERE id=?', (proof['preflight_id'],)).fetchone()[0] == ''
        assert conn.execute('SELECT count(*) FROM jira_agent_key_claims').fetchone()[0] == 0
    monkeypatch.setattr(repo, 'create_turn', original)
    response = env.client.post('/api/jira-agent/conversations', json=body)
    assert response.status_code == 200, response.text
    _wait(env, response.json()['conversation']['id'], _done)


def test_stream_returns_target_progress_and_a_submission_receipt(env):
    response = env.client.post('/api/jira-agent/execution-preflights', headers={'Accept': 'application/x-ndjson'}, json={
        'agent_group': 'HPC', 'execution': domain.normalize(machine=SYSTEM_MACHINE),
        'key_sessions': {SYSTEM_MACHINE: _upload_key(env, SYSTEM_MACHINE)},
        'context': {'kind': 'jira', 'issue_key': 'MC3-1'},
    })
    assert response.status_code == 200
    events = [json.loads(line) for line in response.text.splitlines()]
    assert [event['type'] for event in events] == ['target', 'completed']
    body = submission(events[-1]['result'])
    response = env.client.post('/api/jira-agent/conversations', json=body)
    assert response.status_code == 200
    _wait(env, response.json()['conversation']['id'], _done)


def test_failed_replacement_keeps_old_conversation_and_receipt_intact(env, monkeypatch):
    from app.repositories import jira_agent_repo as repo
    old = _handover(env, 'MC3-1')['conversation']['id']
    _wait(env, old, _done)
    proof = _preflight(env, 'MC3-1')
    def fail(*args, **kwargs):
        raise ValueError('injected failure after closing old conversation')
    monkeypatch.setattr(repo, 'create_turn', fail)
    response = env.client.post('/api/jira-agent/conversations', json={
        'issue_key': 'MC3-1', 'new_conversation': True, **proof,
    })
    assert response.status_code == 400
    with jira_agent_runner.open_db() as conn:
        assert repo.get_open_conversation(conn, 'MC3-1')['id'] == old
        assert repo.get_conversation(conn, old)['close_reason'] == ''
        assert conn.execute('SELECT 1 FROM jira_agent_preflights WHERE request_id=?', (proof['request_id'],)).fetchone() is None


def test_runtime_probe_records_environment_change(env):
    proof = _preflight(env, 'MC3-1', machine=SYSTEM_MACHINE, ssh_key_session_id=_upload_key(env, SYSTEM_MACHINE))
    env.fake.probe_results[SYSTEM_MACHINE] = {'dataset': {'status': 'unknown', 'code': 'timeout', 'message': '读取超时'}}
    cid = _handover(env, 'MC3-1', **proof)['conversation']['id']
    detail = _wait(env, cid, _done)
    assert detail['conversation']['execution_snapshot']['results'][0]['dataset']['status'] == 'pass'
    assert detail['turns'][0]['execution_snapshot']['results'][0]['dataset']['code'] == 'timeout'


def test_auto_submits_and_follows_up_without_probing_system_machines(env):
    env.fake.probe_results[SYSTEM_MACHINE] = {'ssh_exit': 255}
    response = inspect(env, domain.normalize())
    assert response.status_code == 200, response.text
    assert all('ssh' not in item for item in response.json()['snapshot']['results'])
    body = {'issue_key': 'MC3-1', **_preflight(env)}
    response = env.client.post('/api/jira-agent/conversations', json=body)
    assert response.status_code == 200
    cid = response.json()['conversation']['id']
    assert env.client.post('/api/jira-agent/conversations', json=body).json()['conversation']['id'] == cid
    assert env.client.post('/api/jira-agent/conversations', json={**body, 'note': 'changed'}).status_code == 409
    _wait(env, cid, _done)
    followup = env.client.post(f'/api/jira-agent/conversations/{cid}/messages', json={'text': '继续分析'})
    assert followup.status_code == 200, followup.text
    detail = _wait(env, cid, lambda d: len(d['turns']) == 2 and _done(d))
    assert not any(event['kind'] == 'environment_check' for event in detail['events'])
    assert all(turn['execution_snapshot']['available_targets'] == [SYSTEM_MACHINE] for turn in detail['turns'])
    prompt = env.fake.params('turn/start')[-1]['input'][0]['text']
    assert SYSTEM_MACHINE in prompt and '| SSH |' not in prompt
    assert '按工单需要从下列系统机器中选择一台，' in prompt
    assert '一台或多台' not in prompt
    probes = [p['command'] for p in env.fake.params('command/exec') if p.get('command', [None])[0] == 'ssh']
    assert probes == []


@pytest.mark.parametrize('nested', [False, True])
def test_probe_reads_real_share_behind_autofs(tmp_path, monkeypatch, nested):
    (tmp_path / '.identity').write_text('dataset')
    actual = {'target': str(tmp_path), 'source': '10.0.79.212:/pde_hpc/datasets', 'fstype': 'nfs'}
    placeholder = {'target': str(tmp_path), 'source': 'auto.direct', 'fstype': 'autofs'}
    mounts = [{**placeholder, 'children': [actual]}] if nested else [placeholder, actual]
    monkeypatch.setattr('app.resources.jira_agent.check_dataset.subprocess.run', lambda *a, **k:
                        subprocess.CompletedProcess(a[0], 0, json.dumps({'filesystems': mounts})))
    policy = {'path': str(tmp_path), 'sources': [actual['source']], 'probe': '.identity', 'expected': 'dataset'}
    result = check(policy)
    assert result['status'] == 'pass'
    assert result['mount']['source'] == actual['source']
    mounts[:] = [placeholder]
    assert check(policy)['code'] == 'mount_unknown'


def test_probe_accepts_parent_mount_and_checks_source_identity_and_read(tmp_path):
    folder = tmp_path / 'dataset'
    folder.mkdir()
    (folder / '.identity').write_text('expected\n')
    mount = json.loads(subprocess.check_output(['findmnt', '-J', '-T', str(folder), '-o', 'SOURCE']))['filesystems'][0]
    policy = {'path': str(folder), 'sources': [mount['source']], 'probe': '.identity', 'expected': 'expected'}
    assert check(policy)['status'] == 'pass'  # dataset itself is not a mount point
    assert check({**policy, 'sources': ['other:/share']})['code'] == 'source_mismatch'
    assert check({**policy, 'sources': []})['status'] == 'unknown'
    assert check({**policy, 'expected': 'wrong'})['code'] == 'identity_mismatch'
    assert check({**policy, 'probe': 'absent'})['code'] == 'probe_unreadable'
    (folder / 'escape').symlink_to(tmp_path / 'outside')
    assert check({**policy, 'probe': 'escape'})['code'] == 'probe_escape'


@pytest.mark.parametrize('target', ['a@host;echo x', '-oProxyCommand=x@host', 'user@host:22', 'a b@host'])
def test_machine_lists_never_accept_shell_or_port_syntax(target):
    with pytest.raises(ValueError):
        domain.normalize({'mode': 'custom', 'targets': [{'target': target}]})


def test_configuration_preserves_legacy_and_deduplicates_host_case():
    assert domain.configuration({'machine': 'u@host'})['targets'] == [{'target': 'u@host', 'purpose': ''}]
    with pytest.raises(ValueError, match='重复'):
        domain.normalize({'mode': 'custom', 'targets': [{'target': 'u@Host'}, {'target': 'u@host'}]})
    with pytest.raises(ValueError):
        domain.dataset_policy({'DATASET_PATH': '/data', 'DATASET_PROBE_RELATIVE_PATH': '../escape'})
