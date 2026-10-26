"""Outcome-based grading, separated from the model under test."""
import json
from pathlib import Path

from app import runtime_config
from app.domain.jira_testing import digest

RULE = Path(__file__).resolve().parents[1] / 'resources/jira_test_evaluator/SKILL.md'


def config(name):
    section = 'jira_evaluation:' + name
    values = runtime_config.section(section)
    if not values:
        raise ValueError(f'未配置 AI 初评服务，请配置 [{section}] 后重试初评')
    result = {key: runtime_config.required(section, values, option) for key, option in (
        ('endpoint', 'CODEX_WS_URL'), ('token', 'CODEX_WS_TOKEN'), ('root', 'WORKSPACE_ROOT'), ('model', 'MODEL'))}
    if not result['endpoint'].startswith(('ws://','wss://')) or not result['root'].startswith('/'):
        raise ValueError('初评服务需要 ws/wss 地址和绝对工作目录')
    if any(result['endpoint'].rstrip('/') == value.get('CODEX_WS_URL', '').rstrip('/')
           for value in runtime_config.agent_group_sections().values()):
        raise ValueError('初评必须使用独立于分析服务的只读 app-server')
    result['timeout'] = runtime_config.required_int(section, values, 'TURN_TIMEOUT_SECONDS')
    result['effort'] = runtime_config.required(section, values, 'REASONING_EFFORT')
    if result['timeout'] < 60 or result['effort'] not in ('low','medium','high','xhigh'):
        raise ValueError('初评超时至少 60 秒，推理强度应为 low/medium/high/xhigh')
    result['rule'] = RULE.read_text()
    result['rule_sha256'] = digest(result['rule'])
    return result


def obj(fields):
    return {'type': 'object', 'properties': fields, 'required': list(fields), 'additionalProperties': False}


STR = {'type': 'string'}
SCHEMA = obj({'decision': {'type': 'string', 'enum': ['pass','partial','fail','unknown']}, 'summary': STR,
    'criteria': {'type': 'array', 'items': obj({'id': STR,
        'status': {'type': 'string', 'enum': ['met','unmet','unknown']}, 'reason': STR,
        'evidence_ids': {'type': 'array', 'items': STR}})}})


def validate(raw, snapshot, material):
    value = json.loads(raw)
    if not isinstance(value, dict) or set(value) != {'decision','summary','criteria'}:
        raise ValueError('初评返回格式不正确')
    if value['decision'] not in ('pass','partial','fail','unknown') or not isinstance(value['summary'], str) or not value['summary'].strip() or len(value['summary']) > 6000:
        raise ValueError('初评缺少有效结论')
    expected = {item['id'] for item in snapshot['criteria']}
    items = value['criteria']
    if not isinstance(items, list) or len(items) != len(expected) or not expected:
        raise ValueError('初评必须逐项覆盖所有通过标准')
    seen = set()
    sources = {item['id']: item for item in material['sources']}
    for item in items:
        if not isinstance(item, dict) or set(item) != {'id','status','reason','evidence_ids'}:
            raise ValueError('标准核对格式不正确')
        if not isinstance(item['id'], str) or item['id'] not in expected or item['id'] in seen:
            raise ValueError('初评标准重复或不在本次快照中')
        seen.add(item['id'])
        if item['status'] not in ('met','unmet','unknown') or not isinstance(item['reason'], str) or not item['reason'].strip() or len(item['reason']) > 6000:
            raise ValueError('每项标准需要有效状态与依据')
        refs = item['evidence_ids']
        if not isinstance(refs, list) or any(not isinstance(ref, str) or ref not in sources for ref in refs):
            raise ValueError('初评引用了不存在的证据')
        if item['status'] == 'met' and (not refs or not any(sources[ref].get('text', '').strip() for ref in refs)):
            raise ValueError('满足标准必须引用已读取的证据')
    statuses = {item['status'] for item in items}
    if value['decision'] == 'pass' and (statuses != {'met'} or material['delivery_status'] != 'complete' or material['status'] != 'completed'):
        raise ValueError('标准未全部满足或交付未完成，不能判定通过')
    if value['decision'] != 'pass' and statuses == {'met'} and material['delivery_status'] == 'complete' and material['status'] == 'completed':
        raise ValueError('所有标准均已满足时，总体评价不能与逐项结果冲突')
    if value['decision'] == 'unknown' and 'unmet' in statuses:
        raise ValueError('已有明确未满足项，不能整体判为无法判定')
    if value['decision'] == 'partial' and ('met' not in statuses or statuses == {'met'}):
        raise ValueError('部分符合应同时包含已完成项和缺项')
    return value
