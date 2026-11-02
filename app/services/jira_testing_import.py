"""Select source material before copying it into an independent test input."""
import asyncio
import base64
import copy
import json
from contextlib import asynccontextmanager
from pathlib import Path, PurePosixPath

from app.api.errors import ApiError
from app.domain import jira_testing as domain, jira_agent
from app.integrations.codex_app_server import CodexAppServerClient, CodexAppServerError
from app.repositories import jira_agent_repo as agent
from app.services import jira_agent_runner as execution
from app.timeutil import beijing_timestamp


def _unchanged(cid, turn_id):
    with execution.open_db() as conn:
        latest = agent.latest_turn(conn, cid)
        if not latest or agent.active_turn(conn, cid) or latest['id'] != turn_id:
            raise ApiError(409, '原对话有新轮次，请重新打开材料选择')


@asynccontextmanager
async def _source(cid, expected=''):
    with execution.open_db() as conn:
        conversation = agent.get_conversation(conn, cid)
        if not conversation or conversation['source_kind'] != 'jira':
            raise ApiError(404, '原始 Jira 对话不存在')
        if agent.active_turn(conn, cid):
            raise ApiError(409, '请等待本轮结束后再保存快照')
        turns = agent.list_turns(conn, cid)
        if not turns:
            raise ApiError(409, '尚无可保存的分析输入')
        group = execution.load_groups().get(conversation['agent_group'])
        if not group:
            raise ApiError(409, '原分析服务配置不可用')
        records = agent.list_files(conn, cid)
    async with CodexAppServerClient(group.ws_url, group.ws_token) as client:
        raw = await client.read_file(conversation['workspace'] + '/issue.json')
        if len(raw) > domain.MAX_BYTES:
            raise ValueError('工单快照过大')
        issue = json.loads(raw)['issue']
        comments = [{key: item.get(key, '') for key in ('author', 'created', 'body')} |
                    {'id': str(item.get('id') or f'case-{index + 1}')}
                    for index, item in enumerate(issue.get('comments') or [])]
        rounds = {turn['id']: turn['seq'] for turn in turns}
        files = {}
        for item in issue.get('attachments') or []:
            key = 'jira:' + str(item['id'])
            files[key] = {'id': key, 'name': item['filename'], 'size': item.get('size', 0),
                          'source': 'jira', 'turn': None, 'path': item.get('local_path') or '',
                          'local': '', 'attachment_id': str(item['id'])}
        for item in records:
            if item['direction'] != 'input' or item['source'] not in ('jira', 'upload'):
                continue
            key = 'jira:' + item['source_ref'] if item['source'] == 'jira' else item['id']
            if key in files:
                files[key]['path'] = files[key]['path'] or item['remote_path']
                continue
            files[key] = {'id': key, 'name': item['name'], 'size': item['size'],
                          'source': item['source'], 'turn': rounds.get(item['turn_id']),
                          'path': item['remote_path'] or 'uploads/' + item['id'] + '-' + jira_agent.safe_filename(item['name']),
                          'local': item['local_path'] if item['source'] == 'upload' else '',
                          'attachment_id': item['source_ref']}
        snapshot = domain.digest({'issue': issue, 'files': files, 'turn': turns[-1]['id'], 'note': turns[0]['input_text']})
        if expected and snapshot != expected:
            raise ApiError(409, '原始材料已更新，请重新打开材料选择')
        _unchanged(cid, turns[-1]['id'])
        cleaned = copy.deepcopy(issue)
        cleaned.update(comments=[], attachments=[])
        draft = {'title': issue['summary'],
                 'input': domain.validate({'issue': cleaned, 'note': turns[0]['input_text'], 'files': []}),
                 'provenance': {'issue_key': conversation['issue_key'], 'conversation_id': cid,
                                'original_coverage': copy.deepcopy(issue.get('acquisition', {})),
                                'captured_at': beijing_timestamp()},
                 'notice': '已按选择整理评论和文件。请核对正文与任务要求，移除其中的答案。'}
        yield client, {'draft': draft, 'comments': comments, 'files': files, 'snapshot': snapshot,
                       'workspace': conversation['workspace']}
        _unchanged(cid, turns[-1]['id'])


async def preview(cid):
    async with _source(cid) as (_, source):
        return {**source['draft'], 'snapshot': source['snapshot'], 'comments': source['comments'],
                'files': [{key: item[key] for key in ('id', 'name', 'size', 'source', 'turn')}
                          for item in source['files'].values()]}


async def _read(client, source, item):
    path = PurePosixPath(item['path'])
    prefix = 'attachments' if item['source'] == 'jira' else 'uploads'
    if len(path.parts) != 2 or path.parts[0] != prefix or '..' in path.parts or '\\' in item['path'] or '\0' in item['path']:
        raise ValueError(f"文件路径不可用：{item['name']}。请取消勾选，或补齐文件后重新导入。")
    if item['size'] > domain.MAX_BYTES:
        raise ValueError(f"文件超过 20MB 限制：{item['name']}")
    try:
        if item['local']:
            raw = await asyncio.to_thread(Path(item['local']).read_bytes)
        else:
            raw = await asyncio.wait_for(client.read_file(source['workspace'] + '/' + item['path']), timeout=60)
    except (OSError, TimeoutError, CodexAppServerError) as exc:
        raise ApiError(409, f"无法读取选中的文件：{item['name']}。请重试，或取消勾选后继续。") from exc
    if len(raw) > domain.MAX_BYTES:
        raise ValueError(f"文件超过 20MB 限制：{item['name']}")
    return raw


async def prepare(cid, selection):
    async with _source(cid, selection['snapshot']) as (client, source):
        comments = {item['id']: item for item in source['comments']}
        selected = selection['file_ids']
        if len(set(selected)) != len(selected) or any(key not in source['files'] for key in selected):
            raise ValueError('选择的文件不属于本次材料，请重新选择')
        ids = [item['id'] for item in selection['comments']]
        if len(set(ids)) != len(ids) or any(key not in comments for key in ids):
            raise ValueError('选择的评论不属于本次材料，请重新选择')
        draft = source['draft']
        inputs = draft['input']
        edited = {item['id']: item['body'] for item in selection['comments']}
        inputs['issue']['comments'] = [{**item, 'body': edited[item['id']]}
                                       for item in source['comments'] if item['id'] in edited]
        total = 0
        for key in selected:
            item = source['files'][key]
            raw = await _read(client, source, item)
            total += len(raw)
            if total > domain.MAX_BYTES:
                raise ValueError('所选文件合计超过 20MB，请减少选择')
            inputs['files'].append({'path': item['path'], 'content_base64': base64.b64encode(raw).decode()})
            if item['source'] == 'jira':
                inputs['issue']['attachments'].append({'id': item['attachment_id'], 'filename': item['name'],
                                                       'size': len(raw), 'local_path': item['path']})
        draft['input'] = domain.validate(inputs)
        return draft


async def download(cid, file_id, snapshot):
    async with _source(cid, snapshot) as (client, source):
        item = source['files'].get(file_id)
        if not item:
            raise ApiError(404, '文件不属于本次材料')
        return await _read(client, source, item), item['name']
