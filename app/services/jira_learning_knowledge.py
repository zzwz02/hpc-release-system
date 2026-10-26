"""Compare repository knowledge with the deployed analysis service."""
import asyncio

from app import runtime_config
from app.domain import jira_agent
from app.integrations import knowledge_repository as repository, knowledge_service
from app.timeutil import beijing_timestamp


async def capture(group: str) -> dict:
    config = jira_agent.groups_from_sections(runtime_config.agent_group_sections()).get(group)
    if config is None:
        raise ValueError('未配置对应分析服务，无法采集实际 skill')
    return await knowledge_service.capture(config)


async def collect(group: str, ref: str, repository_only: bool) -> dict:
    target = await asyncio.to_thread(repository.snapshot, ref)
    actual = {'files': {}, 'hashes': {}, 'server': '', 'captured_at': '', 'deployed_commit': ''}
    if not repository_only:
        actual = await capture(group)
    differences = [
        path for path in sorted(set(target['files']) | set(actual['files']))
        if target['files'].get(path) != actual['files'].get(path)
    ] if not repository_only else []
    return {'repository': target, 'analysis': actual, 'repository_only': repository_only,
            'differences': differences, 'captured_at': beijing_timestamp()}
