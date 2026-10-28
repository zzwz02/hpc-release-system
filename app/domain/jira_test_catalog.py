"""Versioned case inputs, stored as readable sources or portable ZIP packages."""
import base64
import hashlib
import json
import re
from pathlib import PurePosixPath

from app.domain import jira_test_package as package, jira_testing as domain


def fingerprint(body):
    value = {'title': body['title'], 'input': body['input']}
    # Preserve fingerprints of already deployed cases without standards.
    if body.get('criteria') or body.get('reference'):
        value['review'] = domain.acceptance(body.get('criteria', ''), body.get('reference', ''))
    return domain.digest(value)


def read_file(root, name, limit):
    path = root / name
    if path.is_symlink() or any(parent.is_symlink() for parent in path.parents):
        raise ValueError(f'用例文件不能是软链接：{name}')
    if path.stat().st_size > limit:
        raise ValueError(f'用例文件过大：{name}')
    return path.read_bytes()


def source(directory):
    body = json.loads(read_file(directory, 'case.json', package.MAX_BYTES))
    if set(body) != {'title', 'input', 'provenance'}:
        raise ValueError('内置用例只保存名称、输入和来源')
    files = body['input'].get('files', [])
    expected = {'case.json'}
    if (directory / 'review.json').exists():
        review = json.loads(read_file(directory, 'review.json', package.MAX_BYTES))
        if not isinstance(review, dict) or set(review) - {'criteria', 'reference'}:
            raise ValueError('review.json 只保存通过标准和参考结果')
        body.update(review)
        expected.add('review.json')
    for item in files:
        name = item['path']
        path = PurePosixPath(name)
        if (path.is_absolute() or '..' in path.parts or str(path) != name or '\\' in name
                or '\0' in name or len(path.parts) != 2 or path.parts[0] not in ('attachments', 'uploads')):
            raise ValueError('输入文件必须位于 attachments/ 或 uploads/ 下')
        item['content_base64'] = base64.b64encode(read_file(directory, name, domain.MAX_BYTES)).decode()
        expected.add(name)
    actual = {path.relative_to(directory).as_posix() for path in directory.rglob('*') if not path.is_dir()}
    if actual != expected:
        raise ValueError('用例目录有未登记文件或缺少输入文件')
    domain.validate(body['input'])
    body['input_sha256'] = domain.digest(body['input'])
    return package.encode(body)


def sources(root):
    if not root.exists():
        return []
    entries = []
    for directory in sorted(root.iterdir()):
        if directory.name.startswith(('.', '_')):
            continue
        if not directory.is_dir() or directory.is_symlink() or not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,79}', directory.name):
            raise ValueError('内置用例目录名只能使用小写字母、数字和短横线')
        entries.append((directory.name, source(directory)))
    return entries


def load(root):
    packed = root / '_packages'
    if not packed.exists():
        entries = sources(root)
    else:
        index = json.loads(read_file(packed, 'index.json', package.MAX_MANIFEST_BYTES))
        if index.get('format') != 'jira-test-catalog' or index.get('version') != 1:
            raise ValueError('内置用例清单格式不支持')
        entries = []
        for item in index['cases']:
            key = item['key']
            if not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,79}', key):
                raise ValueError('内置用例标识无效')
            raw = read_file(packed, key + '.zip', package.MAX_BYTES)
            if hashlib.sha256(raw).hexdigest() != item['sha256']:
                raise ValueError(f'内置用例包校验失败：{key}')
            entries.append((key, raw))
    result = []
    keys = set()
    hashes = set()
    for key, raw in entries:
        body = package.decode(raw)
        digest = domain.digest(body['input'])
        if key in keys or digest in hashes:
            raise ValueError('内置用例标识或测试输入重复')
        domain.criteria_items(body['criteria'])
        keys.add(key)
        hashes.add(digest)
        result.append({'key': key, 'body': body, 'hash': fingerprint(body)})
    return result
