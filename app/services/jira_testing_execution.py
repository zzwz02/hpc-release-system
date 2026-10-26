"""Frozen inputs for the common runner, without a Jira client dependency."""
import base64
import json
from dataclasses import replace
from pathlib import PurePosixPath

from app.db.connection import transaction
from app.domain import jira_agent, jira_testing
from app.repositories import jira_agent_repo as agent, jira_testing_repo as tests


def group_for(conn, conversation, group):
    if conversation['source_kind'] != 'test':
        return group
    context = tests.context(conn, conversation['id'])
    jira_testing.verify(context)
    config = context['config']
    if group.ws_url != config['endpoint']:
        raise ValueError('测试批次的 Codex 地址已改变，请重新创建批次')
    return replace(group, model=config['model'], turn_timeout_seconds=config['timeout_seconds'])


async def prepare(conn, client, conversation, turn_id):
    context = tests.context(conn, conversation['id'])
    jira_testing.verify(context)
    workspace = conversation['workspace']
    # A project boundary avoids inheriting the deployed pack above this directory.
    await client.create_directory(workspace + '/.git')
    for folder in ('attachments', 'uploads', 'work', 'artifacts'):
        await client.create_directory(workspace + '/' + folder)
    for path, text in context['pack']['files'].items():
        await client.create_directory(str(PurePosixPath(workspace, path).parent))
        await client.write_file(workspace + '/' + path, text.encode())
    executable = context['pack'].get('executable', [])
    if executable:
        result = await client.exec_command(
            ['chmod', 'u+x', '--', *[workspace + '/' + path for path in executable]], timeout_ms=30000)
        if result.get('exitCode') != 0:
            raise ValueError('恢复 skill 脚本执行权限失败')
    inputs = context['input']
    existing = agent.remote_paths(conn, conversation['id'])
    for file in inputs['files']:
        path = file['path']
        data = base64.b64decode(file['content_base64'])
        await client.write_file(workspace + '/' + path, data)
        if path not in existing:
            with transaction(conn):
                agent.add_file(conn, conversation_id=conversation['id'], turn_id=turn_id,
                               direction='input', source='upload', name=PurePosixPath(path).name,
                               size=len(data), sha256=file['sha256'], remote_path=path)
    issue = inputs['issue']
    paths = {str(item['id']): item['local_path'] for item in issue.get('attachments') or []}
    markdown = jira_agent.render_issue_markdown(issue, paths)
    await client.write_file(workspace + '/issue.md', markdown.encode())
    await client.write_file(workspace + '/issue.json', json.dumps(
        jira_agent.build_issue_snapshot(issue, paths, markdown), ensure_ascii=False).encode())
    return context
