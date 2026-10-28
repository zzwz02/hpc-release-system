"""Portable test inputs. Archives are inspected in memory, never extracted."""
import base64
import copy
import hashlib
import io
import json
import stat
import zipfile
import zlib
from pathlib import PurePosixPath

from app.domain import jira_testing as domain

FORMAT = 'jira-test-case'
VERSION = 1
# Existing inputs allow 20 MiB of files and 40 MiB of JSON. Leave room for
# the manifest and optional reviewer-only text, including ZIP overhead.
MAX_BYTES = domain.MAX_BYTES * 2 + 1024 * 1024
MAX_MANIFEST_BYTES = 128 * 1024
MAX_REVIEW_BYTES = 512 * 1024


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2).encode()


def _source(value):
    # Deployment settings and arbitrary operational metadata are not portable.
    return {key: value[key] for key in ('issue_key', 'conversation_id', 'captured_at')
            if isinstance(value.get(key), str)}


def encode(version):
    inputs = copy.deepcopy(version['input'])
    if domain.digest(inputs) != version['input_sha256']:
        raise ValueError('保存的测试输入校验失败，无法导出')
    domain.validate(inputs)
    if domain.digest(inputs) != version['input_sha256']:
        raise ValueError('保存的测试输入不完整或不一致，请重新保存后导出')
    entries = {}
    for item in inputs['files']:
        entries[item['path']] = base64.b64decode(item.pop('content_base64'), validate=True)
    entries['case.json'] = _json({
        'title': version['title'], 'input': inputs, 'provenance': _source(version['provenance']),
    })
    review = {key: version.get(key, '') for key in ('criteria', 'reference')}
    if any(review.values()):
        entries['review.json'] = _json(review)
    entries['manifest.json'] = _json({
        'format': FORMAT, 'version': VERSION, 'input_sha256': version['input_sha256'],
        'files': {path: {'size': len(data), 'sha256': hashlib.sha256(data).hexdigest()}
                  for path, data in entries.items()},
    })
    if sum(map(len, entries.values())) > MAX_BYTES:
        raise ValueError('用例包超过大小限制')
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for path, data in entries.items():
            archive.writestr(path, data)
    raw = output.getvalue()
    if len(raw) > MAX_BYTES:
        raise ValueError('用例包超过大小限制')
    return raw


def decode(raw):
    if len(raw) > MAX_BYTES:
        raise ValueError('用例包超过大小限制')
    try:
        return _decode(raw)
    except (zipfile.BadZipFile, zlib.error, EOFError, UnicodeError, RecursionError, json.JSONDecodeError,
            TypeError, AttributeError, KeyError, NotImplementedError) as exc:
        raise ValueError('用例包损坏或格式无效，请重新导出') from exc


def _decode(raw):
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        entries = archive.infolist()
        if len(entries) > 105 or sum(item.file_size for item in entries) > MAX_BYTES:
            raise ValueError('用例包解压后的大小或文件数量超过限制')
        names = set()
        files = {}
        for item in entries:
            path = item.filename
            pure = PurePosixPath(path)
            folder = path in ('attachments/', 'uploads/') and item.is_dir()
            allowed = path in ('manifest.json', 'case.json', 'review.json') or (
                len(pure.parts) == 2 and pure.parts[0] in ('attachments', 'uploads'))
            mode = stat.S_IFMT(item.external_attr >> 16)
            if (path in names or item.orig_filename != path or '\\' in path or '\0' in path
                    or pure.is_absolute() or '..' in pure.parts
                    or (str(pure) != path and not folder) or not (allowed or folder)
                    or mode not in (0, stat.S_IFREG, stat.S_IFDIR if folder else 0)
                    or item.flag_bits & 1
                    or item.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED)):
                raise ValueError(f'用例包包含不支持的文件或路径：{path[:150]}')
            names.add(path)
            if folder:
                if item.file_size:
                    raise ValueError('用例包目录条目不能包含内容')
                continue
            limit = (MAX_MANIFEST_BYTES if path == 'manifest.json' else
                     MAX_REVIEW_BYTES if path == 'review.json' else MAX_BYTES)
            if item.file_size > limit:
                raise ValueError(f'用例包文件过大：{path}')
            with archive.open(item) as stream:
                data = stream.read(limit + 1)
            if len(data) != item.file_size or len(data) > limit:
                raise ValueError(f'用例包文件大小不一致：{path}')
            files[path] = data
    if 'manifest.json' not in files or 'case.json' not in files:
        raise ValueError('不是测试用例包：缺少 manifest.json 或 case.json')
    manifest = json.loads(files.pop('manifest.json'))
    if manifest.get('format') != FORMAT or type(manifest.get('version')) is not int or manifest['version'] != VERSION:
        raise ValueError('不支持此用例包格式版本，请使用兼容版本的发布系统')
    listing = manifest.get('files')
    if not isinstance(listing, dict) or set(listing) != set(files):
        raise ValueError('用例包文件清单不完整，存在缺失或多余文件')
    for path, data in files.items():
        if (listing[path].get('size') != len(data)
                or listing[path].get('sha256') != hashlib.sha256(data).hexdigest()):
            raise ValueError(f'用例包文件校验失败：{path}')
    body = json.loads(files.pop('case.json'))
    title = body.get('title')
    if not isinstance(title, str) or not title.strip() or len(title) > 300:
        raise ValueError('用例名称不能为空且不能超过 300 字')
    review = json.loads(files.pop('review.json', b'{}'))
    for key, limit in (('criteria', 20000), ('reference', 100000)):
        if not isinstance(review.get(key, ''), str) or len(review.get(key, '')) > limit:
            raise ValueError('人工参考资料格式无效或过长')
    inputs = body['input']
    paths = set()
    for item in inputs['files']:
        path = item['path']
        if path not in files or path in paths or 'content_base64' in item:
            raise ValueError('用例包的输入文件缺失或重复')
        if item.get('sha256') != hashlib.sha256(files[path]).hexdigest():
            raise ValueError(f'输入文件校验失败：{path}')
        paths.add(path)
        item['content_base64'] = base64.b64encode(files[path]).decode()
    if paths != set(files):
        raise ValueError('用例包包含未登记的输入文件')
    domain.validate(inputs)
    if domain.digest(inputs) != manifest.get('input_sha256'):
        raise ValueError('用例输入校验失败，请重新导出')
    return {
        'title': title.strip(), 'input': inputs, 'package_import': True,
        'criteria': review.get('criteria', ''), 'reference': review.get('reference', ''),
        'provenance': _source(body.get('provenance', {})),
        'notice': '请核对正文、镜像和输入文件，保存后再开始测试。镜像仍需从对应仓库拉取。',
    }
