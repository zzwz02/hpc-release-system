"""Exercise the real API/SQLite/runner via a local app-server protocol peer."""
import asyncio
import base64
import json
import subprocess
import sys
import time

import pytest

from app.db.connection import transaction
from app.integrations import jira
from app.repositories import jira_agent_repo as repo
from app.services import jira_agent_runner
from tests.learning_helpers import knowledge_repo, commit
from tests.test_jira_agent import _preflight, env, _handover, _wait, _done, _detail, SYSTEM_MACHINE, _write_conf, _upload_key

BASE = '/api/jira-agent/testing'


@pytest.fixture
def setup(env, monkeypatch):
    root = knowledge_repo(env.tmp, monkeypatch)
    env.fake.pack_root = root
    env.as_user('carol')
    issue = env.jira.get_issue_snapshot('MC3-7814')
    issue['attachments'] = []
    body = {'title': 'Memory regression', 'input': {'issue': issue, 'note': '复现并定位问题',
            'files': [{'path': 'uploads/input.txt', 'content_base64': base64.b64encode(b'original input').decode()}]},
            'criteria': 'REVIEW_ONLY_CRITERIA', 'reference': 'SECRET_REFERENCE_RESULT', 'provenance': {'issue_key': issue['key']}}
    response = env.client.post(BASE + '/cases', json=body)
    assert response.status_code == 200, response.text
    return env, root, body, response.json()


def start(env, case, **extra):
    proof = _preflight(env, context={'kind': 'test', 'case_id': case['id'], 'version_id': case['versions'][0]['id']},
                       machine=extra.get('machine', ''), ssh_key_session_id=extra.get('ssh_key_session_id', ''))
    response = env.client.post(BASE + f'/cases/{case["id"]}/batches', json={
        'version_id': case['versions'][0]['id'], 'agent_group': 'HPC',
        'model': 'test-model', 'effort': 'xhigh', **proof, **extra})
    assert response.status_code == 200, response.text
    batch = response.json()['batches'][0]
    assert len(batch['runs']) == 1
    return batch


def forbid_jira(monkeypatch):
    calls = []
    def forbidden(*args, **kwargs):
        calls.append(args)
        raise AssertionError('test runs must never access Jira')
    for name in ('get_issue', 'get_issue_snapshot', 'download_attachment', 'search_issues', 'add_comment'):
        monkeypatch.setattr(jira, name, forbidden)
    return calls


def test_saved_case_uses_the_same_prompts_and_skill_loading_as_jira(setup, monkeypatch):
    env, root, body, case = setup
    with env.conf.open('a') as config:
        config.write('MODEL = test-model\n')
    original = _handover(env, 'MC3-7814', note='复现并定位问题',
                         files=[{'filename': 'input.txt', 'content_base64': base64.b64encode(b'original input').decode()}])['conversation']
    _wait(env, original['id'], _done)
    imported = env.client.get(BASE + '/import/' + original['id']).json()
    saved = env.client.post(BASE + '/cases', json=imported)
    assert saved.status_code == 200, saved.text
    calls = forbid_jira(monkeypatch)
    batch = start(env, saved.json(), skill_ref='')
    result = _wait(env, batch['runs'][0]['conversation_id'], _done)
    assert result['turns'][0]['status'] == 'completed'
    live, test = env.fake.params('thread/start')
    # The workspace path differs; the identity and every behavioral instruction match.
    assert test['developerInstructions'].replace(test['cwd'], live['cwd']) == live['developerInstructions']
    assert test['model'] == live['model'] == 'test-model'
    live, test = env.fake.params('turn/start')
    assert test['input'] == live['input']
    assert len(test['input']) == 1 and test['input'][0]['type'] == 'text'
    assert test['outputSchema'] == live['outputSchema']
    assert not calls
    assert len(env.jira.comments) == 1
    assert not result['turns'][0]['post_comment']


def test_default_skill_is_frozen_from_service_including_uncommitted_resources(setup):
    env, root, body, case = setup
    skill = '.agents/skills/hpc-jira-agent/'
    (root / (skill + 'SKILL.md')).write_text('# DEPLOYED_UNCOMMITTED_RULE\n')
    (root / (skill + 'config.json')).write_text('{"flag": true}')
    script = root / (skill + 'run.sh')
    script.write_text('#!/bin/sh\necho deployed\n')
    script.chmod(0o755)
    env.fake.plans = ['hold', 'complete']
    first = start(env, case)['runs'][0]['conversation_id']
    _wait(env, first, lambda detail: detail['conversation']['phase'] == 'running')
    # Each run captures the deployed files, including changes outside Git.
    response = env.client.post(BASE + f'/cases/{case["id"]}/batches', json={
        'version_id': case['versions'][0]['id'], 'agent_group': 'HPC', 'model': 'test-model',
        **_preflight(env, context={'kind': 'test', 'case_id': case['id'], 'version_id': case['versions'][0]['id']})})
    assert response.status_code == 200, response.text
    batch = response.json()['batches'][0]
    assert batch['config']['skill_source'] == 'analysis_service'
    (root / (skill + 'SKILL.md')).write_text('# CHANGED_AFTER_QUEUE\n')
    env.client.post(f'/api/jira-agent/conversations/{first}/cancel')
    _wait(env, first, _done)
    result = _wait(env, batch['runs'][0]['conversation_id'], _done)
    assert result['turns'][0]['status'] == 'completed'
    workspace = result['conversation']['workspace']
    assert env.fake.files[workspace + '/' + skill + 'SKILL.md'] == b'# DEPLOYED_UNCOMMITTED_RULE\n'
    assert env.fake.files[workspace + '/' + skill + 'config.json'] == b'{"flag": true}'
    assert ['chmod', 'u+x', '--', workspace + '/' + skill + 'run.sh'] in [p['command'] for p in env.fake.params('command/exec')]
    latest = start(env, case)
    assert latest['config']['skill_source'] == 'analysis_service'
    result = _wait(env, latest['runs'][0]['conversation_id'], _done)
    assert env.fake.files[result['conversation']['workspace'] + '/' + skill + 'SKILL.md'] == b'# CHANGED_AFTER_QUEUE\n'


def test_selecting_repository_skill_is_rejected_before_queueing(setup):
    env, root, body, case = setup
    response = env.client.post(BASE + f'/cases/{case["id"]}/batches', json={
        'version_id': case['versions'][0]['id'], 'agent_group': 'HPC', 'skill_ref': 'main'})
    assert response.status_code == 422
    assert any(error['loc'] == ['body', 'skill_ref'] for error in response.json()['detail'])
    assert env.client.get(BASE + f'/cases/{case["id"]}/batches').json()['batches'] == []
    assert env.fake.turns == 0


@pytest.mark.parametrize('failure', ['missing', 'symlink', 'binary'])
def test_invalid_service_pack_does_not_silently_fall_back_to_repository(setup, failure):
    env, root, body, case = setup
    if failure == 'missing':
        (root / 'AGENTS.md').unlink()
    elif failure == 'symlink':
        (root / '.agents/skills/link').symlink_to(root / 'AGENTS.md')
    else:
        (root / '.agents/skills/blob').write_bytes(b'\xff')
    response = env.client.post(BASE + f'/cases/{case["id"]}/batches', json={
        'version_id': case['versions'][0]['id'], 'agent_group': 'HPC', 'model': 'test-model',
        **_preflight(env, context={'kind': 'test', 'case_id': case['id'], 'version_id': case['versions'][0]['id']})})
    assert response.status_code == 400, response.text
    assert env.client.get(BASE + f'/cases/{case["id"]}/batches').json()['batches'] == []
    assert env.fake.turns == 0


@pytest.mark.parametrize('mode', ['shared', 'different_pack', 'different_identity'])
def test_shared_service_pack_requires_matching_entry_points(setup, mode):
    env, root, body, case = setup
    alias = env.tmp / 'site-alias'
    alias.mkdir()
    identity = root / 'AGENTS.md'
    if mode == 'different_identity':
        identity = root / 'custom.md'
        identity.write_text('# Alternate identity\n')
    (alias / 'AGENTS.md').symlink_to(identity)
    target = root
    if mode == 'different_pack':
        target = env.tmp / 'different-pack'
        (target / '.agents').mkdir(parents=True)
    (alias / '.agents').symlink_to(target / '.agents', target_is_directory=True)
    env.fake.pack_root = alias
    response = env.client.post(BASE + f'/cases/{case["id"]}/batches', json={
        'version_id': case['versions'][0]['id'], 'agent_group': 'HPC', 'model': 'test-model',
        **_preflight(env, context={'kind': 'test', 'case_id': case['id'], 'version_id': case['versions'][0]['id']})})
    if mode != 'shared':
        assert response.status_code == 400
        assert env.fake.turns == 0
        assert env.client.get(BASE + f'/cases/{case["id"]}/batches').json()['batches'] == []
        return
    assert response.status_code == 200, response.text
    batch = response.json()['batches'][0]
    result = _wait(env, batch['runs'][0]['conversation_id'], _done)
    assert result['turns'][0]['status'] == 'completed'
    workspace = result['conversation']['workspace']
    assert env.fake.files[workspace + '/AGENTS.md'] == (root / 'AGENTS.md').read_bytes()
    assert env.fake.files[workspace + '/.agents/skills/hpc-jira-agent/SKILL.md'] == (root / '.agents/skills/hpc-jira-agent/SKILL.md').read_bytes()


def test_auto_choice_never_adds_new_hosts_and_excludes_removed_hosts_at_dequeue(setup):
    env, root, body, case = setup
    env.fake.plans = ['hold', 'complete']
    first = start(env, case)['runs'][0]['conversation_id']
    batch = start(env, case)
    second = batch['runs'][0]['conversation_id']
    assert batch['config']['machine'] == ''
    assert [item['ssh_target'] for item in batch['config']['machines']] == [SYSTEM_MACHINE]
    _wait(env, first, lambda detail: detail['conversation']['phase'] == 'running')
    with jira_agent_runner.open_db() as conn, transaction(conn):
        conn.execute('DELETE FROM jira_agent_machines')
        repo.create_machine(conn, agent_group='HPC', ssh_target='new@10.0.0.99',
                            description='Added after queue', created_by='carol')
    env.client.post(f'/api/jira-agent/conversations/{first}/cancel')
    _wait(env, first, _done)
    result = _wait(env, second, _done)
    assert result['turns'][0]['status'] == 'completed'
    assert result['conversation']['machine_used'] == ''
    assert result['turns'][0]['execution_snapshot']['available_targets'] == []
    assert any('允许范围之外' in event['payload'].get('text', '') for event in result['events'])
    prompt = env.fake.params('turn/start')[-1]['input'][0]['text']
    assert SYSTEM_MACHINE in prompt and 'new@10.0.0.99' not in prompt
    assert '允许连接的目标：[]' in prompt.split('出队环境复核（以此为准）')[-1]


def test_custom_machine_uses_verified_session_and_empty_list_allows_only_custom(setup):
    env, root, body, case = setup
    with jira_agent_runner.open_db() as conn, transaction(conn):
        conn.execute('DELETE FROM jira_agent_machines')
    request = {'version_id': case['versions'][0]['id'], 'agent_group': 'HPC',
               'model': 'test-model'}
    url = BASE + f'/cases/{case["id"]}/batches'
    assert env.client.post(url, json=request).status_code == 409
    target = 'tester@10.0.0.9'
    request['machine'] = target
    assert env.client.post(url, json=request).status_code == 409
    # A different user's verified session cannot admit this run.
    env.as_user('alice')
    session = _upload_key(env, target)
    env.as_user('carol')
    assert env.client.post(url, json={**request, 'ssh_key_session_id': session}).status_code == 409
    session = _upload_key(env, target)
    request['ssh_key_session_id'] = session
    assert env.client.post(url, json={**request, 'machine': 'tester@10.0.0.8'}).status_code == 409
    assert env.client.post(url, json={**request, 'skill_ref': 'main'}).status_code == 422
    batch = start(env, case, machine=target, ssh_key_session_id=session)
    result = _wait(env, batch['runs'][0]['conversation_id'], _done)
    assert result['turns'][0]['status'] == 'completed'
    assert result['conversation']['machine'] == target
    assert [m['ssh_target'] for m in batch['config']['machines']] == [target]
    assert 'ssh_key_session_id' not in batch['config']
    assert env.client.post(url, json=request).status_code == 409
    prompt = env.fake.params('turn/start')[-1]['input'][0]['text']
    assert f'用户指定的机器 `ssh {target}`' in prompt and SYSTEM_MACHINE not in prompt


def test_auto_and_custom_runs_serialize_overlapping_hosts_only(setup, monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "jira_testing_max_concurrent", 5)
    env, root, body, case = setup
    _write_conf(env.conf, env.fake.url, max_concurrent=5)
    env.fake.plans = ['hold', 'complete', 'complete']
    first = start(env, case)['runs'][0]['conversation_id']
    _wait(env, first, lambda detail: detail['conversation']['phase'] == 'running')
    # A different login on the same host still conflicts with the auto run.
    target = 'tester@' + SYSTEM_MACHINE.split('@')[1]
    second = start(env, case, machine=target, ssh_key_session_id=_upload_key(env, target))['runs'][0]['conversation_id']
    other = 'tester@10.0.0.99'
    third = start(env, case, machine=other, ssh_key_session_id=_upload_key(env, other))['runs'][0]['conversation_id']
    assert _wait(env, third, _done)['turns'][0]['status'] == 'completed'
    assert _detail(env, second)['turns'][0]['status'] == 'queued'
    assert env.fake.turns == 2
    env.client.post(f'/api/jira-agent/conversations/{first}/cancel')
    _wait(env, first, _done)
    assert _wait(env, second, _done)['turns'][0]['status'] == 'completed'


def test_separate_starts_are_isolated_frozen_and_never_touch_jira(setup, monkeypatch):
    env, root, body, case = setup
    calls = forbid_jira(monkeypatch)
    batch = start(env, case)
    second = start(env, case)
    commit(root, {'.agents/skills/hpc-jira-agent/SKILL.md': 'CHANGED_AFTER_QUEUE'})
    results = [_wait(env, run['conversation_id'], _done) for run in batch['runs'] + second['runs']]
    assert all(item['turns'][0]['status'] == 'completed' for item in results), results
    assert len({item['conversation']['workspace'] for item in results}) == 2
    assert len({item['conversation']['thread_id'] for item in results}) == 2
    for detail in results:
        conversation = detail['conversation']
        workspace = conversation['workspace']
        assert not detail['turns'][0]['post_comment']
        assert detail['turns'][0]['comment_status'] == ''
        assert conversation['browse_url'] == ''
        assert not conversation['can_write']
        assert env.fake.files[workspace + '/uploads/input.txt'] == b'original input'
        assert b'CHANGED_AFTER_QUEUE' not in env.fake.files[workspace + '/.agents/skills/hpc-jira-agent/SKILL.md']
        cid = conversation['id']
        assert env.client.post(f'/api/jira-agent/conversations/{cid}/messages', json={'text': 'help'}).status_code == 409
        assert env.client.post(f'/api/jira-agent/turns/{detail["turns"][0]["id"]}/comment/retry').status_code == 409
    assert not calls
    payloads = json.dumps(env.fake.requests) + str(env.fake.files)
    assert 'SECRET_REFERENCE_RESULT' not in payloads
    assert 'REVIEW_ONLY_CRITERIA' not in payloads
    assert all(row['model'] == 'test-model' and row['effort'] == 'xhigh' for row in env.fake.params('turn/start'))
    assert env.client.get('/api/jira-agent/conversations').json()['conversations'] == []
    with jira_agent_runner.open_db() as conn:
        assert repo.handled_issues(conn) == (0, [])
    env.as_user('alice')
    assert env.client.get(BASE + '/cases').status_code == 403
    assert env.client.get(f'/api/jira-agent/conversations/{batch["runs"][0]["conversation_id"]}').status_code == 404
    assert not calls


def test_old_repeat_parameter_still_starts_only_one_run(setup):
    env, root, body, case = setup
    batch = start(env, case, repeat=5)
    assert _wait(env, batch['runs'][0]['conversation_id'], _done)['turns'][0]['status'] == 'completed'
    assert env.fake.turns == 1
    assert len(env.client.get(BASE + f'/cases/{case["id"]}/batches').json()['batches'][0]['runs']) == 1


def test_save_without_review_fields_preserves_previous_versions(setup):
    env, root, body, case = setup
    body.pop('criteria')
    body.pop('reference')
    created = env.client.post(BASE + '/cases', json=body)
    assert created.status_code == 200, created.text
    assert created.json()['versions'][0]['criteria'] == ''
    assert created.json()['versions'][0]['reference'] == ''
    updated = env.client.post(BASE + f'/cases/{case["id"]}/versions', json=body)
    assert updated.status_code == 200, updated.text
    assert updated.json()['versions'][0]['version'] == 2
    assert updated.json()['versions'][1]['criteria'] == 'REVIEW_ONLY_CRITERIA'
    assert updated.json()['versions'][1]['reference'] == 'SECRET_REFERENCE_RESULT'


def test_version_and_review_history_stay_bound_to_run(setup):
    env, root, body, case = setup
    batch = start(env, case)
    cid = batch['runs'][0]['conversation_id']
    _wait(env, cid, _done)
    body['criteria'] = 'new criteria'
    body['input']['note'] = 'changed requirements'
    response = env.client.post(BASE + f'/cases/{case["id"]}/versions', json=body)
    assert response.status_code == 200, response.text
    assert response.json()['versions'][0]['version'] == 2
    assert response.json()['batches'][0]['version'] == 1
    for decision in ('partial', 'pass'):
        response = env.client.post(BASE + f'/runs/{cid}/review', json={'decision': decision, 'note': '人工核对证据'})
        assert response.status_code == 200, response.text
    detail = env.client.get(BASE + f'/cases/{case["id"]}').json()
    assert detail['batches'][0]['counts']['pass'] == 1
    assert detail['batches'][0]['counts']['partial'] == 0
    assert len(detail['batches'][0]['runs'][0]['reviews']) == 2


def test_serial_queue_cancel_and_restart(setup, monkeypatch):
    env, root, body, case = setup
    calls = forbid_jira(monkeypatch)
    _write_conf(env.conf, env.fake.url, max_concurrent=5)
    env.fake.plans = ['hold', 'complete']
    first = start(env, case)['runs'][0]['conversation_id']
    second = start(env, case)['runs'][0]['conversation_id']
    _wait(env, first, lambda detail: detail['conversation']['phase'] == 'running')
    time.sleep(.3)
    assert env.fake.turns == 1
    assert _detail(env, second)['turns'][0]['status'] == 'queued'
    assert env.client.post(BASE + f'/runs/{first}/review', json={'decision': 'pass', 'note': 'too early'}).status_code == 409
    env.stop_site()
    env.start_site()
    _wait(env, first, lambda detail: detail['conversation']['phase'] == 'running')
    assert env.client.post(f'/api/jira-agent/conversations/{first}/cancel').status_code == 200
    _wait(env, first, _done)
    assert _wait(env, second, _done)['turns'][0]['status'] == 'completed'
    assert env.fake.turns == 2
    assert not calls


def test_import_contains_original_files_not_outputs(env):
    env.as_user('carol')
    conv = _handover(env, post_comment=False)['conversation']
    _wait(env, conv['id'], _done)
    response = env.client.get(BASE + '/import/' + conv['id'])
    assert response.status_code == 200, response.text
    draft = response.json()
    assert 'reference' not in draft and 'criteria' not in draft
    assert len(draft['input']['files']) == 1
    assert all(item['path'].startswith('attachments/') for item in draft['input']['files'])
    assert '.patch' not in json.dumps(draft)


def test_invalid_files_and_model_limits(setup):
    env, root, body, case = setup
    body['input']['files'][0]['path'] = '../secret'
    assert env.client.post(BASE + '/cases', json=body).status_code == 400
    response = env.client.post(BASE + f'/cases/{case["id"]}/batches', json={
        'version_id': case['versions'][0]['id'], 'agent_group': 'HPC', 'machine': SYSTEM_MACHINE,
        'model': 'test-model', 'effort': 'invalid'})
    assert response.status_code == 422


def test_comment_guard_even_for_corrupted_post_flag(setup, monkeypatch):
    env, root, body, case = setup
    batch = start(env, case)
    cid = batch['runs'][0]['conversation_id']
    _wait(env, cid, _done)
    calls = forbid_jira(monkeypatch)
    with jira_agent_runner.open_db() as conn, transaction(conn):
        turn = repo.latest_turn(conn, cid)
        repo.update_turn(conn, turn['id'], post_comment=1, comment_status='pending')
    with jira_agent_runner.open_db() as conn:
        result = asyncio.run(jira_agent_runner.post_turn_comment(conn, turn['id']))
    assert not result['post_comment']
    assert not calls


def test_pack_resources_and_scripts_are_preserved(setup):
    env, root, body, case = setup
    from tests.learning_helpers import git
    script = '.agents/skills/hpc-jira-agent/run.sh'
    resource = '.agents/skills/hpc-jira-agent/config.json'
    commit(root, {script: '#!/bin/sh\necho ok\n', resource: '{"required":true}'})
    (root / script).chmod(0o755)
    git(root, 'add', script)
    git(root, 'commit', '-qm', 'executable')
    batch = start(env, case)
    detail = _wait(env, batch['runs'][0]['conversation_id'], _done)
    assert detail['turns'][0]['status'] == 'completed'
    workspace = detail['conversation']['workspace']
    assert env.fake.files[f'{workspace}/{resource}'] == b'{"required":true}'
    assert ['chmod', 'u+x', '--', f'{workspace}/{script}'] in [item['command'] for item in env.fake.params('command/exec')]


def test_missing_source_files_and_unavailable_skills_fail_before_queueing(setup):
    env, root, body, case = setup
    body['input']['issue']['attachments'] = [{'id': '1', 'filename': 'required.dat', 'local_path': 'attachments/required.dat'}]
    response = env.client.post(BASE + '/cases', json=body)
    assert response.status_code == 400
    assert '附件缺失' in response.text
    (root / 'AGENTS.md').unlink()
    response = env.client.post(BASE + f'/cases/{case["id"]}/batches', json={
        'version_id': case['versions'][0]['id'], 'agent_group': 'HPC',
        'model': 'test-model', **_preflight(env, context={'kind': 'test', 'case_id': case['id'], 'version_id': case['versions'][0]['id']})})
    assert response.status_code == 400
    assert env.client.get(BASE + f'/cases/{case["id"]}/batches').json()['batches'] == []


def test_delivery_retry_is_local_and_review_waits_for_complete_files(setup, monkeypatch):
    env, root, body, case = setup
    batch = start(env, case)
    cid = batch['runs'][0]['conversation_id']
    detail = _wait(env, cid, _done)
    tid = detail['turns'][0]['id']
    with jira_agent_runner.open_db() as conn, transaction(conn):
        repo.update_turn(conn, tid, delivery_status='incomplete', delivery_errors_json='[{"error":"transfer failed"}]')
    assert env.client.post(BASE + f'/runs/{cid}/review', json={'decision': 'pass', 'note': 'too early'}).status_code == 400
    calls = forbid_jira(monkeypatch)
    response = env.client.post(f'/api/jira-agent/turns/{tid}/delivery/retry')
    assert response.status_code == 200, response.text
    assert response.json()['turn']['delivery_status'] == 'complete'
    assert not calls


def test_second_batch_on_same_machine_waits_for_first(setup):
    env, root, body, case = setup
    _write_conf(env.conf, env.fake.url, max_concurrent=5)
    env.fake.plans = ['hold', 'complete']
    first = start(env, case)['runs'][0]['conversation_id']
    second = start(env, case)['runs'][0]['conversation_id']
    _wait(env, first, lambda detail: detail['conversation']['phase'] == 'running')
    time.sleep(.3)
    assert _detail(env, second)['turns'][0]['status'] == 'queued'
    assert env.client.post(f'/api/jira-agent/conversations/{second}/cancel').status_code == 200
    assert _wait(env, second, _done)['turns'][0]['status'] == 'cancelled'
    assert env.client.post(f'/api/jira-agent/conversations/{first}/cancel').status_code == 200
    _wait(env, first, _done)
    assert env.fake.turns == 1


def test_cancel_batch_stops_its_single_run(setup):
    env, root, body, case = setup
    env.fake.plans = ['hold']
    batch = start(env, case)
    _wait(env, batch['runs'][0]['conversation_id'], lambda detail: detail['conversation']['phase'] == 'running')
    response = env.client.post(BASE + f'/batches/{batch["id"]}/cancel')
    assert response.status_code == 200, response.text
    for item in batch['runs']:
        assert _wait(env, item['conversation_id'], _done)['turns'][0]['status'] == 'cancelled'
    assert env.fake.turns == 1


def test_corrupt_saved_input_is_not_executed(setup):
    env, root, body, case = setup
    env.fake.plans = ['hold']
    first = start(env, case)['runs'][0]['conversation_id']
    second = start(env, case)['runs'][0]['conversation_id']
    _wait(env, first, lambda detail: detail['conversation']['phase'] == 'running')
    with jira_agent_runner.open_db() as conn, transaction(conn):
        conn.execute('UPDATE jira_test_versions SET input_json=? WHERE id=?',
                     (json.dumps({'issue': {'key': 'CHANGED-1'}}), case['versions'][0]['id']))
    env.client.post(f'/api/jira-agent/conversations/{first}/cancel')
    _wait(env, first, _done)
    detail = _wait(env, second, _done)
    assert detail['turns'][0]['status'] == 'failed'
    assert '输入校验失败' in detail['turns'][0]['error']
    assert env.fake.turns == 1


def test_curated_snapshot_passes_real_skill_loader_without_removed_answers(setup):
    from tests.test_jira_agent_snapshot import LOADER
    env, root, body, case = setup
    issue = body['input']['issue']
    issue['comments'] = []
    issue['raw_fields']['comment'] = {'comments': [{'body': 'REMOVED_ANSWER'}]}
    issue['raw_fields']['description'] = 'REMOVED_DESCRIPTION'
    response = env.client.post(BASE + '/cases', json=body)
    assert response.status_code == 200, response.text
    case = response.json()
    batch = start(env, case)
    detail = _wait(env, batch['runs'][0]['conversation_id'], _done)
    workspace = detail['conversation']['workspace'] + '/'
    bundle = env.tmp / 'real-loader'
    for path, data in env.fake.files.items():
        if path.startswith(workspace):
            target = bundle / path[len(workspace):]
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
    result = subprocess.run([sys.executable, str(LOADER), '--issue', issue['key'],
                             '--source', 'local', '--bundle-dir', str(bundle)], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    loaded = json.loads(result.stdout)
    assert loaded['success'] and loaded['completeness'] == 'complete', loaded
    assert loaded['data']['comments'] == []
    assert 'REMOVED_ANSWER' not in result.stdout
    assert 'REMOVED_DESCRIPTION' not in result.stdout


def test_manually_created_case_and_partial_source_coverage():
    from app.domain import jira_testing
    inputs = jira_testing.validate({'issue': {'key': 'TEST-1', 'summary': 'Manual case'}})
    assert inputs['issue']['acquisition']['parts']['comments']['collected'] == 0
    inputs['issue']['acquisition']['status'] = 'partial'
    inputs['issue']['acquisition']['parts']['custom_fields']['status'] = 'unavailable'
    again = jira_testing.validate(inputs)
    assert again['issue']['acquisition']['status'] == 'partial'
    assert again['issue']['acquisition']['parts']['custom_fields']['status'] == 'unavailable'
