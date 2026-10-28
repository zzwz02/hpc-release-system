"""Validation and hashes for saved, reviewer-approved test inputs."""
import base64
import binascii
import hashlib
import json
import re
from pathlib import PurePosixPath

MAX_BYTES = 20 * 1024 * 1024


def criteria_items(text):
    items = [re.sub(r'^\s*(?:[-*]|\d+[.)、])\s*', '', line).strip()
             for line in text.splitlines() if line.strip()]
    items = [item for item in items if item]
    if len(items) > 30:
        raise ValueError('通过标准最多 30 条，请每行填写一条')
    return [{'id': f'C{index + 1}', 'text': item} for index, item in enumerate(items)]


def acceptance(criteria, reference):
    return {'criteria': criteria_items(criteria), 'reference': reference.strip()}


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(',', ':')).encode()).hexdigest()


def validate(inputs):
    if not isinstance(inputs, dict) or not isinstance(inputs.get('issue'), dict):
        raise ValueError('请输入有效的工单快照')
    issue = inputs['issue']
    inputs.setdefault('note', '')
    inputs.setdefault('files', [])
    if not isinstance(issue.get('key'), str) or not re.fullmatch(r'[A-Z][A-Z0-9_]*-\d+', issue['key']):
        raise ValueError('快照需要工单来源编号，手动创建可使用 TEST-1')
    if not isinstance(issue.get('summary'), str) or not issue['summary'].strip():
        raise ValueError('工单标题不能为空')
    if not isinstance(inputs.get('note', ''), str):
        raise ValueError('任务要求必须是文本')
    for key in ('description', 'url'):
        if issue.get(key) is not None and not isinstance(issue[key], str):
            raise ValueError(f'{key} 必须是文本')
    for key in ('assignee', 'reporter', 'acquisition'):
        if issue.get(key) is not None and not isinstance(issue[key], dict):
            raise ValueError(f'{key} 格式无效')
    for key in ('comments', 'attachments', 'components', 'labels'):
        if issue.get(key) is not None and not isinstance(issue[key], list):
            raise ValueError(f'{key} 必须是数组')
    for key in ('components', 'labels'):
        if any(not isinstance(item, str) for item in issue.get(key) or []):
            raise ValueError(f'{key} 必须是文本数组')
    for item in issue.get('comments') or []:
        if not isinstance(item, dict) or not isinstance(item.get('body', ''), str):
            raise ValueError('评论格式无效')
    files = inputs.get('files', [])
    if not isinstance(files, list) or len(files) > 100:
        raise ValueError('输入文件最多 100 个')
    paths = set()
    sizes = {}
    total = 0
    for item in files:
        if not isinstance(item, dict) or not isinstance(item.get('path'), str):
            raise ValueError('输入文件格式无效')
        path = item.get('path', '')
        pure = PurePosixPath(path)
        if (pure.is_absolute() or '..' in pure.parts or str(pure) != path or '\\' in path or '\0' in path
                or len(pure.parts) != 2 or pure.parts[0] not in ('attachments', 'uploads')
                or path in paths):
            raise ValueError('输入文件路径必须唯一且位于 attachments/ 或 uploads/ 下')
        paths.add(path)
        try:
            data = base64.b64decode(item.get('content_base64', ''), validate=True)
        except (ValueError, TypeError, binascii.Error) as exc:
            raise ValueError('输入文件编码无效') from exc
        total += len(data)
        sizes[path] = len(data)
        item['sha256'] = hashlib.sha256(data).hexdigest()
    if total > MAX_BYTES or len(json.dumps(inputs).encode()) > MAX_BYTES * 2:
        raise ValueError('用例输入超过 20MB 限制')
    for attachment in issue.get('attachments') or []:
        if not isinstance(attachment, dict) or not isinstance(attachment.get('filename'), str) or not attachment.get('id'):
            raise ValueError('附件需要 id 和 filename')
        attachment['id'] = str(attachment['id'])
        if attachment.get('local_path') not in paths or not attachment['local_path'].startswith('attachments/'):
            raise ValueError('工单附件缺失，请补齐文件或明确移除该附件后保存')
        attachment['size'] = sizes[attachment['local_path']]
    listed = {item['local_path'] for item in issue.get('attachments') or []}
    if any(path.startswith('attachments/') and path not in listed for path in paths):
        raise ValueError('附件目录存在未登记的文件，请补齐快照中的附件记录或移除文件')
    normalize_snapshot(issue)
    return inputs


def normalize_snapshot(issue):
    """Keep the curated input internally consistent without upgrading source coverage."""
    for key, default in (('description', ''), ('comments', []), ('attachments', []),
                         ('raw_fields', {}), ('custom_fields', {}), ('field_definitions', [])):
        if issue.get(key) is None:
            issue[key] = default
        if not isinstance(issue[key], type(default)):
            raise ValueError(f'{key} 格式无效')
    # The raw API fields duplicate the editable fields. Removed answers must not
    # survive here or in body_raw after the reviewer has curated the case.
    for key in ('comment', 'description', 'attachment', 'summary'):
        issue['raw_fields'].pop(key, None)
    ids = set()
    for index, comment in enumerate(issue['comments']):
        comment['id'] = str(comment.get('id') or f'case-{index + 1}')
        if comment['id'] in ids:
            raise ValueError('评论 ID 重复')
        ids.add(comment['id'])
        comment.setdefault('body', '')
        comment['body_raw'] = comment['body']
    acquisition = issue.get('acquisition') or {}
    parts = acquisition.setdefault('parts', {})
    if not isinstance(parts, dict):
        raise ValueError('采集状态格式无效')
    for key in ('issue', 'description', 'custom_fields', 'field_definitions', 'comments', 'attachment_metadata'):
        part = parts.setdefault(key, {'status': 'unavailable' if acquisition.get('status') == 'partial' else 'complete'})
        if not isinstance(part, dict) or part.get('status') not in ('complete', 'partial', 'unavailable'):
            raise ValueError('采集状态格式无效')
    parts['comments'].update(collected=len(issue['comments']), reported_total=len(issue['comments']))
    parts['custom_fields']['collected'] = len(issue['custom_fields'])
    acquisition.setdefault('conflicts', [])
    if not isinstance(acquisition['conflicts'], list):
        raise ValueError('采集冲突格式无效')
    complete = (acquisition.get('status', 'complete') == 'complete'
                and all(isinstance(part, dict) and part.get('status') == 'complete' for part in parts.values())
                and not acquisition['conflicts'])
    acquisition.update(status='complete' if complete else 'partial', source='test_case',
                       scope='人工保存的测试输入；不代表 Jira 当前内容或全量采集。')
    issue['acquisition'] = acquisition


def verify(context):
    if digest(context['input']) != context['input_sha256']:
        raise ValueError('测试输入校验失败')
    if pack_digest(context['pack']) != context['config']['skill_sha256']:
        raise ValueError('测试 skill 快照校验失败')


def pack_digest(pack):
    return digest({'files': pack['files'], 'executable': pack.get('executable', [])})
