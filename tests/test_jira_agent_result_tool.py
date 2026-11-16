"""Exercise MetaX result submission through the website and real WebSockets."""
import asyncio
import copy
import json

import pytest

from app.db.connection import transaction
from app.domain import jira_agent as domain
from app.repositories import jira_testing_repo
from app.services.jira_agent_runner import load_groups, open_db
from tests.test_jira_agent import env, _handover, _wait, _done, _latest, _enable_deepseek, RESULT, PATCH
from tests.test_jira_testing import setup, start as start_test

COMMENT = {'kind': 'fix', 'outcome': '修复已验证', 'body': '请审核修改。',
           'evidence_indices': [0], 'commands': [], 'link_text': '验证记录'}
FLAT_RESULT = {
    **{key: RESULT[key] for key in ('conclusion', 'action_required', 'issue_category', 'summary',
                                  'root_cause', 'machine', 'machines_used', 'evidence', 'artifacts',
                                  'next_steps', 'skills_used')},
    'assistance_current_issue': '', 'assistance_owner': '', 'assistance_request': '',
    'ownership_belongs_to_us': 'yes', 'ownership_target_group': '',
    'ownership_reasoning': RESULT['ownership']['reasoning'],
    'reproduction_reproduced': True, 'reproduction_environment': 'A100',
    'reproduction_steps': ['make', './saxpy 16777217'],
    'fix_description': '改为向上取整',
    'comment_kind': 'fix', 'comment_outcome': '修复已验证', 'comment_body': '请审核修改。',
    'comment_evidence_indices': [0], 'comment_commands': [], 'comment_link_text': '验证记录',
}


def test_flat_arguments_restore_every_business_field_without_mutating_input():
    original = copy.deepcopy(FLAT_RESULT)
    assert domain.parse_submitted_result(original, domain.RESULT_SCHEMA, flat=True) == {**RESULT, 'comment': COMMENT}
    assert original == FLAT_RESULT
    # Older frozen test schemas without comment keep their original shape.
    schema = copy.deepcopy(domain.RESULT_SCHEMA)
    del schema['properties']['comment']
    schema['required'].remove('comment')
    arguments = {k: v for k, v in original.items() if not k.startswith('comment_')}
    assert domain.parse_submitted_result(arguments, schema, flat=True) == RESULT


@pytest.fixture
def metax(env, monkeypatch):
    path = _enable_deepseek(env)
    env.conf.write_text(env.conf.read_text().replace('"deepseek":', '"metax":'))
    env.fake.config['model_providers']['metax'] = env.fake.config['model_providers'].pop('deepseek')
    env.fake.files[path] = env.fake.files[path].replace(b'deepseek-flash', b'GLM-5.3')
    env.as_user('carol')
    env.fake.tool_responses = {}
    env.replies = []
    env.submissions = []
    env.result_gate = False
    env.result_disconnect = False
    env.legacy_result = False
    original = env.fake._play

    async def play(params, turn_id, plan):
        if plan != 'result_tool':
            return await original(params, turn_id, plan)
        fake = env.fake
        thread_id = params['threadId']
        state = fake.turn_state[turn_id]
        result = ({**copy.deepcopy(RESULT), 'comment': copy.deepcopy(COMMENT)}
                  if env.legacy_result else copy.deepcopy(FLAT_RESULT))
        fake.files[f'{fake.thread_cwd[thread_id]}/artifacts/fix.patch'] = PATCH
        command = {'type': 'commandExecution', 'id': 'cmd_' + turn_id, 'command': 'make && ./saxpy',
                   'status': 'completed', 'exitCode': 0, 'aggregatedOutput': 'PASS'}
        state['items'].append(command)
        await fake._send(thread_id, {'method': 'item/completed', 'params': {
            'threadId': thread_id, 'turnId': turn_id, 'item': command}})
        for index, override in enumerate([*env.submissions, {}]):
            request_id = f'call_{turn_id}_{index}'
            args = {'threadId': thread_id, 'turnId': turn_id, 'callId': request_id,
                    'tool': 'submit_result', 'arguments': result, **override}
            future = asyncio.get_running_loop().create_future()
            fake.tool_responses[request_id] = future
            await fake._send(thread_id, {'id': request_id, 'method': 'item/tool/call', 'params': args})
            response = await asyncio.wait_for(future, 5)
            env.replies.append(response)
        if env.result_disconnect:
            await fake._subscriber[thread_id].close()
        status = 'completed'
        if env.result_gate and not await fake._released(turn_id):
            status = 'interrupted'
        # Ordinary final text must not overwrite the accepted structured result.
        final = {'type': 'agentMessage', 'id': 'final_' + turn_id, 'text': '已提交结果。', 'phase': 'final_answer'}
        state['items'].append(final)
        await fake._send(thread_id, {'method': 'item/completed', 'params': {
            'threadId': thread_id, 'turnId': turn_id, 'item': final}})
        state['status'] = status
        await fake._send(thread_id, {'method': 'turn/completed', 'params': {'threadId': thread_id, 'turn': dict(state)}})

    monkeypatch.setattr(env.fake, '_play', play)
    env.fake.plans = ['result_tool']
    return env


def start(env):
    return _handover(env, model_provider='metax', model='GLM-5.3', effort='max', post_comment=False)['conversation']['id']


def test_metax_site_default_preserves_existing_conversation(metax):
    metax.fake.plans = ['complete']
    old = _handover(metax, post_comment=False)['conversation']['id']
    original = _wait(metax, old, _done)
    path = dict(load_groups()['HPC'].model_catalogs)['metax']
    catalog = json.loads(metax.fake.files[path])
    catalog['models'][0]['default_reasoning_level'] = 'max'
    metax.fake.files[path] = json.dumps(catalog).encode()
    metax.fake.config.update(model='default-model', model_reasoning_effort='xhigh')
    metax.conf.write_text(metax.conf.read_text().replace(
        '[jira_agent:HPC]\n', '[jira_agent:HPC]\nMODEL_PROVIDER = metax\nMODEL = GLM-5.3\n'))
    response = metax.client.get('/api/jira-agent/models?group=HPC')
    assert response.status_code == 200, response.text
    defaults = response.json()
    assert tuple(defaults[k] for k in ('default_provider', 'default_model', 'default_effort')) == ('metax', 'GLM-5.3', 'max')
    metax.as_user('alice')
    response = metax.client.post(f'/api/jira-agent/conversations/{old}/messages', json={'text': '沿用原模型'})
    assert response.status_code == 200, response.text
    detail = _wait(metax, old, _done)
    assert detail['conversation']['thread_id'] == original['conversation']['thread_id']
    assert detail['conversation']['model_provider'] == 'openai'
    assert (_latest(detail)['model'], _latest(detail)['effort']) == ('default-model', 'medium')
    metax.fake.plans = ['result_tool']
    new = _handover(metax, new_conversation=True, post_comment=False)['conversation']['id']
    detail = _wait(metax, new, _done)
    assert detail['conversation']['model_provider'] == 'metax'
    assert (_latest(detail)['model'], _latest(detail)['effort']) == ('GLM-5.3', 'max')
    assert _latest(detail)['status'] == 'completed' and _latest(detail)['delivery_status'] == 'complete'
    assert metax.fake.params('thread/start')[-1]['modelProvider'] == 'metax'
    assert metax.replies[-1]['success'] is True
    assert 'outputSchema' not in metax.fake.params('turn/start')[-1]
    assert not metax.jira.comments


@pytest.mark.parametrize('legacy', [False, True], ids=['flat', 'legacy_nested'])
def test_metax_result_and_followup_survive_restart(metax, legacy):
    cid = start(metax)
    detail = _wait(metax, cid, _done)
    thread_id = detail['conversation']['thread_id']
    assert _latest(detail)['status'] == 'completed'
    assert _latest(detail)['delivery_status'] == 'complete'
    tool = metax.fake.params('thread/start')[-1]['dynamicTools'][0]
    assert tool['name'] == 'submit_result'
    assert tool['inputSchema']['properties']['fix_description'] == {'type': 'string'}
    assert 'fix' not in tool['inputSchema']['properties']
    assert _latest(detail)['result'] == {**RESULT, 'comment': COMMENT}
    if legacy:
        # Recreate the preceding version's persisted metadata and registered tool.
        conn = open_db()
        try:
            with transaction(conn):
                conn.execute('UPDATE jira_agent_events SET payload_json=? WHERE conversation_id=? AND item_id=?',
                             (json.dumps({'status': 'running', 'text': '结果工具已注册'}), cid, 'result-tool:' + thread_id))
        finally:
            conn.close()
        tool['inputSchema'] = domain.RESULT_SCHEMA
        metax.legacy_result = True
    metax.stop_site()
    metax.start_site()
    metax.fake.plans = ['result_tool']
    response = metax.client.post(f'/api/jira-agent/conversations/{cid}/messages', json={'text': '继续核对'})
    assert response.status_code == 200
    detail = _wait(metax, cid, _done)
    assert _latest(detail)['seq'] == 2 and _latest(detail)['status'] == 'completed'
    assert _latest(detail)['delivery_status'] == 'complete'
    assert _latest(detail)['result'] == {**RESULT, 'comment': COMMENT}
    assert detail['conversation']['thread_id'] == thread_id and len(metax.fake.params('thread/start')) == 1
    assert all('outputSchema' not in p for p in metax.fake.params('turn/start'))
    assert not metax.jira.comments


@pytest.mark.parametrize('bad', [
    {'arguments': {}}, {'arguments': {**FLAT_RESULT, 'conclusion': 'made_up'}},
    {'arguments': {k: v for k, v in FLAT_RESULT.items() if k != 'fix_description'}},
    {'arguments': {**FLAT_RESULT, 'fix_description': {}}},
    {'arguments': {**FLAT_RESULT, 'fix': {'description': 'ambiguous nested value'}}},
    {'arguments': {**FLAT_RESULT, 'assistance_request': 'invented help request'}},
    {'arguments': {**FLAT_RESULT, 'next_steps': ['one', 'two', 'three']}},
    {'arguments': {**FLAT_RESULT, 'action_required': 'none'}},
    {'arguments': {**FLAT_RESULT, 'comment_evidence_indices': [True]}},
    {'arguments': {**FLAT_RESULT, 'comment_evidence_indices': [-1]}},
    {'arguments': {**RESULT, 'comment': COMMENT}},
    {'threadId': 'wrong'}, {'turnId': 'wrong'}, {'tool': 'other'}, {'namespace': 'other'},
])
def test_invalid_submission_is_rejected_and_model_can_correct(metax, bad):
    metax.submissions = [bad]
    cid = start(metax)
    detail = _wait(metax, cid, _done)
    assert [r['success'] for r in metax.replies] == [False, True]
    assert _latest(detail)['status'] == 'completed'


def test_duplicate_submission_is_idempotent(metax):
    metax.submissions = [{}]
    cid = start(metax)
    detail = _wait(metax, cid, _done)
    assert [r['success'] for r in metax.replies] == [True, True]
    assert len([e for e in detail['events'] if e['payload'].get('type') == 'submit_result']) == 1


def test_different_second_submission_cannot_replace_accepted_result(metax):
    metax.submissions = [{}, {'arguments': {**FLAT_RESULT, 'summary': 'different'}}]
    cid = start(metax)
    detail = _wait(metax, cid, _done)
    assert [r['success'] for r in metax.replies] == [True, False, True]
    assert _latest(detail)['result']['summary'] == RESULT['summary']


def test_capacity_retry_preserves_result_tool_contract(metax):
    metax.fake.plans = ['capacity', 'result_tool']
    cid = start(metax)
    detail = _wait(metax, cid, _done)
    assert _latest(detail)['status'] == 'completed'
    assert len(metax.fake.params('turn/start')) == 2
    assert all('outputSchema' not in p for p in metax.fake.params('turn/start'))


@pytest.mark.parametrize('disconnect', [False, True])
def test_result_acknowledgement_survives_connection_or_site_loss(metax, disconnect):
    metax.result_gate = True
    metax.result_disconnect = disconnect
    cid = start(metax)
    _wait(metax, cid, lambda d: any(e['payload'].get('type') == 'submit_result' for e in d['events']))
    if not disconnect:
        metax.stop_site()
    metax.fake.release()
    if not disconnect:
        metax.start_site()
    detail = _wait(metax, cid, _done)
    assert _latest(detail)['status'] == 'completed'
    assert _latest(detail)['delivery_status'] == 'complete'
    assert len(metax.fake.params('turn/start')) == 1


def test_cancel_after_submission_does_not_deliver(metax):
    metax.result_gate = True
    cid = start(metax)
    _wait(metax, cid, lambda d: any(e['payload'].get('type') == 'submit_result' for e in d['events']))
    assert metax.client.post(f'/api/jira-agent/conversations/{cid}/cancel').status_code == 200
    assert _latest(_wait(metax, cid, _done))['status'] == 'cancelled'


def test_plain_json_without_submission_is_not_accepted(metax):
    metax.fake.plans = ['complete']
    cid = start(metax)
    turn = _latest(_wait(metax, cid, _done))
    assert turn['status'] == 'failed' and turn['failure_kind'] == 'response'
    assert 'submit_result' in turn['error']
    assert len(metax.fake.params('turn/start')) == 2  # Only one automatic repair.


@pytest.mark.parametrize('restart', [False, True])
def test_missing_submission_repairs_only_result_in_the_same_business_turn(metax, monkeypatch, restart):
    from app.services import jira_agent_runner
    metax.fake.plans = ['invalid_result', 'result_tool']
    if restart:
        monkeypatch.setattr(jira_agent_runner, '_RECOVERY_BACKOFF_SECONDS', 2)
    cid = start(metax)
    if restart:
        _wait(metax, cid, lambda d: any(e['item_id'].startswith('result-repair:') for e in d['events']))
        metax.stop_site()
        metax.start_site()
    detail = _wait(metax, cid, _done)
    assert _latest(detail)['status'] == 'completed'
    assert _latest(detail)['delivery_status'] == 'complete'
    assert len(detail['turns']) == 1 and len(metax.fake.params('thread/start')) == 1
    turns = metax.fake.params('turn/start')
    assert len(turns) == 2 and all('outputSchema' not in p for p in turns)
    assert '不要重新调查、实验、下载或修改报告' in turns[-1]['input'][0]['text']


def test_old_metax_thread_requires_new_conversation(metax):
    cid = start(metax)
    detail = _wait(metax, cid, _done)
    conn = open_db()
    try:
        with transaction(conn):
            conn.execute('DELETE FROM jira_agent_events WHERE conversation_id=? AND item_id=?',
                         (cid, 'result-tool:' + detail['conversation']['thread_id']))
    finally:
        conn.close()
    assert metax.client.post(f'/api/jira-agent/conversations/{cid}/messages', json={'text': '继续'}).status_code == 200
    turn = _latest(_wait(metax, cid, _done))
    assert turn['status'] == 'failed' and '新建对话' in turn['error']
    assert len(metax.fake.params('turn/start')) == 1


def test_saved_test_case_uses_its_frozen_result_schema(metax, setup):
    _, _, _, case = setup
    batch = start_test(metax, case, model_provider='metax', model='GLM-5.3', effort='max')
    cid = batch['runs'][0]['conversation_id']
    detail = _wait(metax, cid, _done)
    assert _latest(detail)['status'] == 'completed'
    conn = open_db()
    try:
        schema = jira_testing_repo.context(conn, cid)['config']['output_schema']
    finally:
        conn.close()
    assert metax.fake.params('thread/start')[-1]['dynamicTools'][0]['inputSchema'] == domain.result_tool_schema(schema)
    assert 'outputSchema' not in metax.fake.params('turn/start')[-1]
    assert not metax.jira.comments


@pytest.mark.parametrize('failure', ['stream_error', 'invalid_result'])
def test_saved_case_resume_keeps_result_tool_and_original_thread(metax, setup, failure):
    from tests.test_jira_testing_resume import resume
    _, _, _, case = setup
    metax.fake.plans = [failure, *([failure] if failure == 'invalid_result' else []), 'result_tool']
    batch = start_test(metax, case, model_provider='metax', model='GLM-5.3', effort='max')
    cid = batch['runs'][0]['conversation_id']
    failed = _latest(_wait(metax, cid, _done))
    assert failed['status'] == 'failed'
    assert resume(metax, cid, failed).status_code == 200
    completed = _latest(_wait(metax, cid, _done))
    assert completed['status'] == 'completed' and completed['delivery_status'] == 'complete'
    assert len(metax.fake.params('thread/start')) == 1
    assert all('outputSchema' not in p for p in metax.fake.params('turn/start'))
