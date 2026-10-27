"""RM-managed test cases. Import is explicit; runs never fetch or update Jira."""
import base64
import copy
import json
from pathlib import PurePosixPath

from app.api.errors import ApiError
from app.db.connection import transaction
from app.domain import jira_testing as domain, jira_agent, jira_test_package, jira_execution
from app.integrations import knowledge_service
from app.integrations.codex_app_server import CodexAppServerClient
from app.repositories import jira_testing_repo as repo, jira_agent_repo as agent, jira_test_evaluation_repo as evaluations
from app.repositories.base import dumps_json, new_id
from app.services import jira_agent_runner as execution
from app.services.jira_agent_service import assignee_name
from app.services import jira_agent_preflight as preflight
from app.timeutil import beijing_timestamp


def detail(case_id):
    with execution.open_db() as conn:
        versions = repo.versions(conn, case_id)
        if not versions:
            raise ApiError(404, '测试用例不存在')
        for version in versions:
            version['input'] = json.loads(version.pop('input_json'))
            version['provenance'] = json.loads(version.pop('provenance_json'))
        return {'id': case_id, 'versions': versions, 'batches': repo.batches(conn, case_id),
                'builtin': repo.builtin(conn, case_id)}


def listing():
    with execution.open_db() as conn:
        return {'cases': repo.cases(conn), 'groups': [
            {'name': group.name, 'display_name': group.display_name, 'model': group.model,
             'machines': agent.list_machines(conn, group.name)}
            for group in execution.load_groups().values()
        ]}


def batches(case_id):
    with execution.open_db() as conn:
        return {'batches': repo.batches(conn, case_id)}


def export_package(case_id, version_id):
    with execution.open_db() as conn:
        row = repo.one(conn, 'SELECT * FROM jira_test_versions WHERE case_id=? AND id=?', (case_id, version_id))
    if not row:
        raise ApiError(404, '测试输入不存在，请刷新后重试')
    row['input'] = json.loads(row.pop('input_json'))
    row['provenance'] = json.loads(row.pop('provenance_json'))
    return jira_test_package.encode(row)


def preview_package(raw):
    draft = jira_test_package.decode(raw)
    with execution.open_db() as conn:
        matches = repo.matching_cases(conn, domain.digest(draft['input']), draft['title'], draft['criteria'], draft['reference'])
    return {**draft, **matches}


async def import_conversation(conversation_id):
    with execution.open_db() as conn:
        conversation = agent.get_conversation(conn, conversation_id)
        if not conversation or conversation['source_kind'] != 'jira':
            raise ApiError(404, '原始 Jira 对话不存在')
        if agent.active_turn(conn, conversation_id):
            raise ApiError(409, '请等待本轮结束后再保存快照')
        turns = agent.list_turns(conn, conversation_id)
        if not turns:
            raise ApiError(409, '尚无可保存的分析输入')
        group = execution.load_groups().get(conversation['agent_group'])
        if not group:
            raise ApiError(409, '原分析服务配置不可用')
        files = agent.list_files(conn, conversation_id)
    async with CodexAppServerClient(group.ws_url, group.ws_token) as client:
        data = await client.read_file(conversation['workspace'] + '/issue.json')
        if len(data) > domain.MAX_BYTES:
            raise ValueError('工单快照过大')
        issue = json.loads(data)['issue']
        original_coverage = copy.deepcopy(issue.get('acquisition', {}))
        inputs = {'issue': issue, 'note': turns[0]['input_text'], 'files': []}
        paths = [attachment.get('local_path', '') for attachment in issue.get('attachments') or []]
        paths += [item['remote_path'] for item in files
                  if item['direction'] == 'input' and item['source'] == 'upload'
                  and item['turn_id'] == turns[0]['id']]
        total = len(data)
        for path in dict.fromkeys(paths):
            pure = PurePosixPath(path)
            if (len(pure.parts) != 2 or pure.parts[0] not in ('attachments', 'uploads')
                    or '..' in pure.parts or '\\' in path):
                raise ValueError('原始附件路径缺失或不安全，无法保存完整用例')
            raw = await client.read_file(conversation['workspace'] + '/' + path)
            total += len(raw)
            if total > domain.MAX_BYTES:
                raise ValueError('用例输入超过 20MB 限制')
            inputs['files'].append({'path': path, 'content_base64': base64.b64encode(raw).decode()})
    with execution.open_db() as conn:
        latest = agent.latest_turn(conn, conversation_id)
        if agent.active_turn(conn, conversation_id) or latest['id'] != turns[-1]['id']:
            raise ApiError(409, '读取期间对话有新轮次，请稍后重新导入')
    return {'title': issue['summary'], 'input': domain.validate(inputs),
            'provenance': {'issue_key': conversation['issue_key'], 'conversation_id': conversation_id,
                           'original_coverage': original_coverage,
                           'captured_at': beijing_timestamp()},
            'notice': '导入的是当前保存的工单快照和首轮要求、上传文件。请移除含有答案的评论、历史分析报告和已有修复。'}


def save(user, body, case_id=''):
    inputs = domain.validate(body['input'])
    domain.criteria_items(body.get('criteria', ''))
    if not body['title'].strip():
        raise ValueError('用例名称不能为空')
    if case_id:
        detail(case_id)
    if case_id and body.get('package_import') and body.get('import_update') != case_id:
        raise ApiError(409, '导入用例包只支持新建，不覆盖已有用例')
    existing = False
    with execution.open_db() as conn:
        # Serialize duplicate detection and creation, including simultaneous imports.
        conn.execute('BEGIN IMMEDIATE')
        with transaction(conn):
            if body.get('package_import'):
                matches = repo.matching_cases(conn, domain.digest(inputs), body['title'].strip(), body.get('criteria', ''), body.get('reference', ''))
                if body.get('import_update') and body['import_update'] not in {item['id'] for item in matches['duplicates']}:
                    raise ApiError(409, '选定用例的输入已变化，请重新导入并核对差异')
                if matches['duplicates']:
                    match = next((item for item in matches['duplicates'] if item['id'] == body.get('import_update')), matches['duplicates'][0])
                    case_id = match['id']
                    if match['review_differs'] and body.get('import_update') != case_id:
                        raise ApiError(409, '输入相同但通过标准或参考结果不同，请选择保留现有用例或确认更新标准')
                    existing = not match['review_differs']
                elif matches['same_name']:
                    raise ApiError(409, '已有同名但内容不同的用例，请修改名称后保存')
            if not existing:
                case_id = repo.save_version(conn, user['username'], case_id, body['title'].strip(),
                                           inputs, domain.digest(inputs), body.get('criteria', '').strip(),
                                           body.get('reference', ''), body['provenance'])
    return {**detail(case_id), 'existing': existing}


async def run(user, case_id, body):
    version = next((row for row in detail(case_id)['versions'] if row['id'] == body['version_id']), None)
    if not version:
        raise ApiError(404, '用例版本不存在')
    group = execution.load_groups().get(body['agent_group'])
    if not group:
        raise ValueError('请选择有效的分析组')
    model = body['model'].strip() or group.model
    if not model:
        raise ValueError('测试必须明确指定模型')
    with execution.open_db() as conn:
        prior = preflight.previous(conn, user, body)
        if prior:
            return {'batch_id': prior['consumed_by'], **detail(case_id)}
        scope = preflight.test_scope(conn, 'test', {**body, 'case_id': case_id})
        preflight.resolve(conn, user, group, body, scope)
    pack = await knowledge_service.capture(group, complete=True)
    pack.update(commit=pack.get('deployed_commit', ''), ref='')
    with execution.open_db() as conn, transaction(conn):
        prior = preflight.previous(conn, user, body)
        if prior:
            return {'batch_id': prior['consumed_by'], **detail(case_id)}
        receipt = preflight.resolve(conn, user, group, body, scope)
        config = run_config(group, body, receipt)
        bid, cid = enqueue(conn, user, version, group, pack, config)
        preflight.consume(conn, user, body, receipt, bid)
    execution.runner.wake()
    return {'batch_id': bid, **detail(case_id)}


def run_config(group, body, receipt):
    config, snapshot = receipt['execution'], receipt['snapshot']
    machines = [{'ssh_target': r['target'], 'description': ''} for r in snapshot['results']]
    return {'agent_group': group.name, 'endpoint': group.ws_url, 'model': body.get('model', '').strip() or group.model,
            'effort': body.get('effort', 'xhigh'), 'machine': jira_execution.legacy_machine(config), 'machines': machines,
            'execution': config, 'execution_snapshot': snapshot, 'timeout_seconds': group.turn_timeout_seconds}


def enqueue(conn, user, version, group, pack, settings):
    config = copy.deepcopy(settings)
    if "execution" not in config:
        config["execution"] = jira_execution.normalize(machine=config.get("machine", ""))
        config["execution_snapshot"] = jira_execution.legacy_snapshot(config.get("machine", ""), config.get("machines", []))
    config.update(skill_commit=pack.get('deployed_commit', ''), skill_source='analysis_service',
                  skill_sha256=domain.pack_digest(pack), input_sha256=version['input_sha256'],
                  output_schema=jira_agent.RESULT_SCHEMA,
                  instructions=jira_agent.developer_instructions(group, version['input']['issue'].get('key', 'TEST'), '/TEST_WORKSPACE'))
    config['prompt'] = jira_agent.build_turn_prompt(
        trigger='handover', seq=1, issue=version['input']['issue'],
        owner=assignee_name(version['input']['issue']) or user['username'], created_by=user['username'],
        input_text=version['input'].get('note', ''),
        uploaded=[item['path'] for item in version['input']['files'] if item['path'].startswith('uploads/')],
        machine_text=jira_execution.instructions(config['execution'], config['execution_snapshot']))
    bid, cid = new_id('jtb'), new_id('jtr')
    conn.execute('INSERT INTO jira_test_batches VALUES(?,?,?,?,?,?)',
                 (bid, version['id'], dumps_json(config), dumps_json(pack), user['username'], beijing_timestamp()))
    agent.create_conversation(conn, conversation_id=cid, issue_key=cid, issue_summary=version['title'],
                              agent_group=group.name, owner=user['username'], created_by=user['username'],
                              workspace=f'{group.workspace_root}/tests/{cid}', machine=config['machine'], source_kind='test',
                              execution_config=config.get('execution'), execution_snapshot=config.get('execution_snapshot'))
    conn.execute('INSERT INTO jira_test_runs VALUES(?,?,?)', (cid, bid, 1))
    turn = agent.create_turn(conn, conversation_id=cid, trigger='handover', created_by=user['username'],
                             input_text=version['input']['note'], post_comment=False)
    agent.add_event(conn, conversation_id=cid, turn_id=turn['id'], kind='user_message',
                    payload={'author': user['username'], 'text': version['input']['note'], 'files': [], 'post_comment': False})
    evaluations.enroll(conn, cid, version)
    return bid, cid


def review(user, conversation_id, body):
    with execution.open_db() as conn, transaction(conn):
        if not repo.one(conn, 'SELECT 1 FROM jira_test_runs WHERE conversation_id=?', (conversation_id,)):
            raise ApiError(404, '测试运行不存在')
        turn = agent.latest_turn(conn, conversation_id)
        if turn['status'] in ('queued', 'running') or turn['delivery_status'] == 'collecting':
            raise ApiError(409, '请等待测试结束后评价')
        if body['decision'] == 'pass' and (turn['status'] != 'completed' or turn['delivery_status'] != 'complete'):
            raise ValueError('执行或交付未完成，不能标记为通过')
        evaluation = evaluations.latest(conn, conversation_id)
        key = evaluations.evidence_key(conn, conversation_id)
        if body.get('evidence_key') and body['evidence_key'] != key:
            raise ApiError(409, '材料已更新，请刷新后复核')
        note = body['note'].strip()
        if body.get('confirm_ai'):
            if not evaluation or evaluation['status'] != 'completed' or evaluation['evidence_key'] != key or body.get('evaluation_id') != evaluation['id']:
                raise ApiError(409, '初评已更新，请刷新后确认')
            result = json.loads(evaluation['result_json'])
            if body['decision'] != result['decision']:
                raise ValueError('确认结果应与当前 AI 初评一致')
            note = note or '确认 AI 初评：' + result['summary']
        if not note:
            raise ValueError('请填写评价依据')
        rid = new_id('jrv')
        conn.execute('INSERT INTO jira_test_reviews VALUES(?,?,?,?,?,?)',
                     (rid, conversation_id, body['decision'], note, user['username'], beijing_timestamp()))
        conn.execute('INSERT INTO jira_test_review_links VALUES(?,?,?)', (rid, evaluation['id'] if evaluation else '', key))
    return {'ok': True}


async def cancel_batch(batch_id):
    with execution.open_db() as conn, transaction(conn):
        rows = list(conn.execute("""SELECT t.id,t.conversation_id,t.status FROM jira_test_runs r
            JOIN jira_agent_turns t ON t.conversation_id=r.conversation_id WHERE r.batch_id=?""", (batch_id,)))
        if not rows:
            raise ApiError(404, '测试批次不存在')
        # Cancel queued repetitions together before interrupting the current one.
        for row in rows:
            if agent.cancel_queued_turn(conn, row['id'], '已取消测试批次'):
                agent.add_event(conn, conversation_id=row['conversation_id'], turn_id=row['id'],
                                kind='status', payload={'status': 'cancelled', 'text': '已取消测试批次'})
    for row in rows:
        if row['status'] == 'running':
            await execution.runner.stop_turn(row['id'], 'user')
    return {'ok': True}
