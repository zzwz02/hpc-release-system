"""Task-scoped, read-only evidence retrieval. Paths and URLs come only from collection."""
from __future__ import annotations

import base64
import bz2
import gzip
import hashlib
import io
import json
import lzma
import os
import re
import stat
import tarfile
import urllib.error
import zipfile
import zlib

from pypdf.errors import PyPdfError
from PIL import Image
from pathlib import Path, PurePosixPath

from app.integrations import jira
from app.timeutil import beijing_timestamp
from app.services.jira_learning_sources import _OBSERVATION_SUFFIXES, _decode
from app.services.jira_learning_archive import LearningArchive

MAX_FILE_BYTES = 256 * 1024 * 1024
MAX_EXPANDED_BYTES = 512 * 1024 * 1024
MAX_MEMBERS = 100_000
MAX_TEXT_CHARS = 48_000
MAX_IMAGE_BYTES = 8 * 1024 * 1024
ARCHIVES = ('.tar', '.tar.gz', '.tgz', '.tar.xz', '.tar.bz2', '.zip')

TOOL = {
    'type': 'function', 'name': 'read_learning_material',
    'description': '只读本任务的材料。read case:<编号> 查看案例概览；source_ids 可批量读至多五条。list 列出材料或压缩包成员，可用 query 按名称筛选；read 读取正文、图片或 PDF 页。使用返回的 ID；offset 为文本字符、列表位置或 PDF 页（从 0 起），next_offset 可继续读取。reason 说明需要核对的判断。',
    'inputSchema': {'type': 'object', 'additionalProperties': False,
        'required': ['action', 'source_id', 'offset', 'limit', 'reason'],
        'properties': {'action': {'type': 'string', 'enum': ['list', 'read']},
                       'source_id': {'type': 'string', 'description': '索引中的材料 ID；列出全部材料时留空'},
                       'offset': {'type': 'integer', 'minimum': 0},
                       'limit': {'type': 'integer', 'minimum': 1, 'maximum': MAX_TEXT_CHARS},
                       'reason': {'type': 'string', 'description': '此次读取要核对什么'},
                       'query': {'type': 'string', 'description': 'list 时按名称或 Jira 编号筛选，可留空'},
                       'source_ids': {'type':'array','items':{'type':'string'},'maxItems':5,'description':'read 时可批量读取至多五条材料，返回总量有上限'}}},
}


def catalog(issue: dict, messages: list[dict], files: list[dict]) -> list[dict]:
    uploaded = {(m['conversation_id'], m['turn_id'], n) for m in messages
                if m.get('kind', 'owner_message') == 'owner_message' for n in m.get('files', [])}
    items = []
    for record in files:
        name = record['name']
        kind = ('owner_upload' if (record['conversation_id'], record['turn_id'], name) in uploaded else 'conversation_upload') if record['source'] == 'upload' else 'agent_artifact'
        if Path(name).name.casefold() == 'analysis_path.md' and kind == 'agent_artifact':
            kind = 'agent_analysis_path'
        prefix = {'owner_upload': 'chat:file', 'conversation_upload': 'chat:file-unattributed',
                  'agent_artifact': 'agent:artifact', 'agent_analysis_path': 'agent:analysis-path'}[kind]
        path = Path(record['local_path']).resolve()
        try:
            info = path.stat()
            fingerprint = [info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns]
        except OSError:
            fingerprint = None
        items.append({'id': prefix + ':' + record['id'], 'name': name, 'kind': kind,
                      'size': record['size'], 'issue_key': issue['key'],
                      'sha256': record.get('sha256', ''),
                      'conversation_id': record['conversation_id'], 'turn_id': record['turn_id'],
                      'independent': kind == 'owner_upload' or Path(name).suffix.lower() in _OBSERVATION_SUFFIXES,
                      'origin': {'path': str(path), 'fingerprint': fingerprint}})
    groups = {}
    for item in items:
        match = re.fullmatch(r'(.+\.(?:tar(?:\.gz|\.xz|\.bz2)?|tgz|zip))\.part(\d+)', item['name'], re.IGNORECASE)
        if match:
            key = (item['conversation_id'], item['turn_id'], match[1])
            groups.setdefault(key, []).append((int(match[2]), item))
    for (_, _, name), parts in groups.items():
        parts.sort(key=lambda pair: pair[0])
        numbers = [number for number, _ in parts]
        ids = [item['id'] for _, item in parts]
        item = {**parts[0][1], 'id': 'split:' + hashlib.sha256('\n'.join(ids).encode()).hexdigest()[:24],
                'name': name, 'size': sum(part['size'] for _, part in parts), 'sha256': '',
                'origin': {'parts': ids}}
        if numbers != list(range(len(parts))):
            item['error'] = '分卷编号不连续或重复，无法读取完整压缩包'
        items.append(item)
    for attachment in issue.get('attachments', []):
        if not attachment.get('id'):
            continue
        items.append({'id': 'jira:attachment:' + str(attachment['id']), 'name': attachment.get('filename') or str(attachment['id']),
                      'kind': 'jira_attachment', 'size': attachment.get('size', 0), 'issue_key': issue['key'],
                      'independent': True, 'origin': {'url': attachment.get('content_url', '')}})
    return items


def gerrit_catalog(sources: list[dict], key: str) -> list[dict]:
    from app.services.jira_learning_sources import _gerrit_links
    return [{'id':'gerrit:change:'+link['change'], 'name':link['project']+' change '+link['change'],
             'kind':'gerrit_patch','issue_key':key,'independent':True,'origin':{'gerrit':link}}
            for link in _gerrit_links(sources)]

def public(item: dict) -> dict:
    return {k: v for k, v in item.items() if k not in ('origin', 'cache', 'error', 'failures', 'decoded', 'expanded', 'members', 'thumbnail', 'pdf_pages')}


class MaterialStore:
    def __init__(self, directory: Path):
        self.directory = directory / 'materials'
        self.archive = LearningArchive(directory)
        self.recording = True
        self.items = {}

    def _item(self, sid: str) -> dict | None:
        if sid not in self.items:
            item = self.archive.cached(sid) or self.archive.get(sid)
            if item and 'origin' in item:
                self.items[sid] = item
        return self.items.get(sid)

    def _blob(self, data: bytes) -> str:
        digest = hashlib.sha256(data).hexdigest()
        path = self.directory / digest
        if not path.exists():
            path.write_bytes(data)
            path.chmod(0o600)
        return digest

    def _bytes(self, item: dict, depth: int = 0) -> bytes:
        if depth > 3:
            raise ValueError('压缩包嵌套超过读取限制')
        if item.get('cache'):
            data = (self.directory / item['cache']).read_bytes()
            if hashlib.sha256(data).hexdigest() != item['cache']:
                raise ValueError('已保存材料校验失败')
            return data
        if item.get('error'):
            raise ValueError(item['error'])
        origin = item['origin']
        if origin.get('parts'):
            chunks = bytearray()
            for sid in origin['parts']:
                part = self._item(sid)
                if part is None:
                    raise ValueError('压缩包分卷缺失')
                if len(chunks) + part['size'] > MAX_FILE_BYTES:
                    raise ValueError('合并分卷超过单次获取上限（256 MiB）')
                data = self._bytes(part, depth + 1)
                if len(chunks) + len(data) > MAX_FILE_BYTES:
                    raise ValueError('合并分卷超过单次获取上限（256 MiB）')
                chunks.extend(data)
            data = bytes(chunks)
        elif origin.get('parent'):
            parent = self._item(origin['parent'])
            if parent is None:
                raise ValueError('压缩包来源不存在')
            with self._archive(parent, depth + 1) as archive:
                member = next((m for m in self._members(archive) if self._name(m) == origin['member']), None)
                if member is None:
                    raise ValueError('压缩包成员不存在')
                self._safe(member)
                with archive.open(member) if isinstance(archive, zipfile.ZipFile) else archive.extractfile(member) as stream:
                    data = stream.read(MAX_FILE_BYTES + 1)
        elif origin.get('path'):
            path = Path(origin['path'])
            with path.open('rb') as stream:
                before = os.fstat(stream.fileno())
                fingerprint = [before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns]
                if not stat.S_ISREG(before.st_mode) or fingerprint != origin['fingerprint']:
                    raise ValueError('材料自采集后已变化，未使用新内容替换原证据')
                data = stream.read(MAX_FILE_BYTES + 1)
                after = os.fstat(stream.fileno())
                if fingerprint != [after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns]:
                    raise ValueError('材料在读取期间变化')
        elif origin.get('gerrit'):
            from app.services.jira_learning_sources import _fetch_gerrit_change
            link=origin['gerrit']
            try:
                data, metadata=_fetch_gerrit_change(link['project'],link['change'])
            except (RuntimeError, OSError) as exc:
                raise ValueError('Gerrit 变更读取失败，请检查访问权限或连接') from exc
            item.update(metadata)
        elif origin.get('url'):
            # This existing downloader verifies that the URL belongs to configured Jira.
            for attempt in range(2):
                try:
                    data = jira.download_attachment(origin['url'], max_bytes=MAX_FILE_BYTES, timeout_seconds=30)
                    break
                except urllib.error.HTTPError as exc:
                    if attempt or exc.code not in (408, 429, 500, 502, 503, 504):
                        raise
                except (TimeoutError, urllib.error.URLError):
                    if attempt:
                        raise
        else:
            raise ValueError('材料没有可用的获取位置')
        if len(data) > MAX_FILE_BYTES:
            raise ValueError('材料超过单次获取上限（256 MiB）')
        if origin.get('path') and item.get('sha256') and hashlib.sha256(data).hexdigest() != item['sha256']:
            raise ValueError('材料与交付时的文件校验值不一致')
        item['cache'] = self._blob(data)
        return data

    def _archive(self, item: dict, depth: int):
        if item.get('expanded'):
            return tarfile.open(self.directory / item['expanded'],mode='r:')
        data = self._bytes(item, depth)
        if item['name'].lower().endswith('.zip'):
            return zipfile.ZipFile(io.BytesIO(data))
        name = item['name'].lower()
        opener = gzip.open if name.endswith(('.gz', '.tgz')) else lzma.open if name.endswith('.xz') else bz2.open if name.endswith('.bz2') else None
        if opener:
            with opener(io.BytesIO(data), 'rb') as stream:
                data = stream.read(MAX_EXPANDED_BYTES + 1)
            if len(data) > MAX_EXPANDED_BYTES:
                raise ValueError('压缩包展开超过读取上限（512 MiB）')
        item['expanded'] = self._blob(data)
        return tarfile.open(self.directory / item['expanded'], mode='r:')

    @staticmethod
    def _name(member) -> str:
        return member.filename if isinstance(member, zipfile.ZipInfo) else member.name

    @staticmethod
    def _members(archive):
        if isinstance(archive, zipfile.ZipFile):
            members = archive.infolist()
            if len(members) > MAX_MEMBERS:
                raise ValueError('压缩包成员超过读取上限')
            yield from members
            return
        for index, member in enumerate(archive):
            if index >= MAX_MEMBERS:
                raise ValueError('压缩包成员超过读取上限')
            yield member

    @staticmethod
    def _safe(member) -> None:
        name = MaterialStore._name(member)
        path = PurePosixPath(name)
        if path.is_absolute() or '..' in path.parts or '\\' in name:
            raise ValueError('压缩包成员路径无效')
        if isinstance(member, zipfile.ZipInfo):
            mode = member.external_attr >> 16
            if member.is_dir() or stat.S_ISLNK(mode) or (stat.S_IFMT(mode) and not stat.S_ISREG(mode)):
                raise ValueError('压缩包特殊文件不读取')
            if member.flag_bits & 1:
                raise ValueError('加密压缩包成员无法读取')
            size = member.file_size
        else:
            if not member.isfile() or member.issparse():
                raise ValueError('压缩包链接或特殊文件不读取')
            size = member.size

    def _list(self, item: dict | None, offset: int, limit: int, query: str = '') -> dict:
        if item is None:
            return self.archive.listing(query=query, offset=offset, limit=min(limit, 100), include_cases=True)
        if item.get('members') is not None:
            return self.archive.cache_listing(item['id'], query, offset, limit)
        else:
            if not item['name'].lower().endswith(ARCHIVES):
                raise ValueError('该材料不是支持的压缩包，请使用 read')
            entries = []
            children = {}
            seen = set()
            with self._archive(item, 0) as archive:
                for member in self._members(archive):
                    if (member.is_dir() if isinstance(member, zipfile.ZipInfo) else member.isdir()):
                        continue
                    name = self._name(member)
                    normalized = str(PurePosixPath(name))
                    if normalized in seen:
                        raise ValueError('压缩包包含重复路径，无法可靠区分成员')
                    seen.add(normalized)
                    sid = item['id'] + '!/' + hashlib.sha256(name.encode()).hexdigest()[:20]
                    child = {**{k: v for k, v in item.items() if k not in ('id', 'name', 'size', 'sha256', 'origin', 'cache', 'error', 'decoded', 'expanded', 'members', 'thumbnail', 'pdf_pages')},
                             'id': sid, 'name': name, 'size': member.file_size if isinstance(member, zipfile.ZipInfo) else member.size,
                             'origin': {'parent': item['id'], 'member': name},
                             'independent': item['kind'] in ('jira_attachment', 'owner_upload') or Path(name).suffix.lower() in _OBSERVATION_SUFFIXES}
                    children[sid] = self.items.get(sid, child)
                    entries.append(public(children[sid]))
            # A corrupt or ambiguous directory must not leave a partially usable index.
            self.archive.cache_items(children)
            item['members'] = True
        if query:
            words = query.casefold().split()
            entries = [entry for entry in entries if all(word in (entry.get('name', '') + ' ' + entry.get('issue_key', '')).casefold() for word in words)]
        end = min(len(entries), offset + min(limit, 100))
        return {'items': entries[offset:end], 'total': len(entries), 'next_offset': end if end < len(entries) else None}

    def _read(self, sid: str, item: dict | None, offset: int, limit: int) -> tuple[dict, list[dict]]:
        existing = self.archive.get(sid, body=True)
        images = []
        metadata = item or existing
        if metadata is None:
            raise ValueError('材料 ID 不属于本任务')
        name = metadata.get('name', sid)
        if existing and existing.get('range',{}).get('unit') == 'image' and existing.get('material_id'):
            parent=existing['material_id']
            return self._read(parent,self._item(parent),0,limit)
        if existing and 'text' in existing:
            text = existing['text']
        elif item:
            if name.lower().endswith(ARCHIVES):
                return self._list(item, offset, limit), []
            suffix = Path(name).suffix.lower()
            data = b'' if item.get('decoded') or item.get('thumbnail') or str(offset) in item.get('pdf_pages',{}) else self._bytes(item)
            if suffix in ('.png', '.jpg', '.jpeg', '.gif', '.webp'):
                if item.get('thumbnail'):
                    encoded=(self.directory/item['thumbnail']).read_bytes()
                else:
                    with Image.open(io.BytesIO(data)) as picture:
                        picture.seek(0)
                        picture.thumbnail((2048, 2048))
                        output = io.BytesIO()
                        picture.convert('RGB').save(output, format='PNG')
                    encoded = output.getvalue()
                    item['thumbnail']=self._blob(encoded)
                if len(encoded) > MAX_IMAGE_BYTES:
                    raise ValueError('图片超过单次返回上限')
                images = [{'type': 'inputImage', 'imageUrl': 'data:image/png;base64,' + base64.b64encode(encoded).decode()}]
                text = '图片已提供给学习服务；这是图片来源记录，不是对图片内容的验证。'
                offset = 0
            elif suffix == '.pdf':
                from pypdf import PdfReader
                pages=item.setdefault('pdf_pages',{})
                if str(offset) in pages:
                    text=(self.directory/pages[str(offset)]).read_text()
                else:
                    reader = PdfReader(io.BytesIO(data))
                    item['page_count']=len(reader.pages)
                    if offset >= len(reader.pages):
                        raise ValueError('PDF 页码超过范围')
                    text = reader.pages[offset].extract_text() or ''
                    pages[str(offset)]=self._blob(text.encode())
                if not text.strip():
                    raise ValueError('该 PDF 页没有可提取文本，尚未支持扫描页识别')
                if len(text) > MAX_TEXT_CHARS:
                    raise ValueError('PDF 单页文本超过返回上限')
                return self._evidence(sid, metadata, text, offset, offset + 1,
                                      offset + 1 if offset + 1 < item['page_count'] else None, 'page'), []
            else:
                if item.get('decoded'):
                    text=(self.directory / item['decoded']).read_text()
                    end=min(len(text),offset+limit)
                    if offset>len(text): raise ValueError('读取位置超过正文长度')
                    return self._evidence(sid,metadata,text[offset:end],offset,end,end if end<len(text) else None,'character'),[]
                if suffix in ('.gz', '.xz', '.bz2'):
                    opener = gzip.open if suffix == '.gz' else lzma.open if suffix == '.xz' else bz2.open
                    with opener(io.BytesIO(data), 'rb') as stream:
                        data = stream.read(MAX_FILE_BYTES + 1)
                    if len(data) > MAX_FILE_BYTES:
                        raise ValueError('解压文本超过单次获取上限')
                text = _decode(data)
                if text is None:
                    raise ValueError('暂不支持该文件格式或编码')
                item['decoded']=self._blob(text.encode())
        else:
            raise ValueError('材料没有可读取正文')
        end = min(len(text), offset + min(limit, MAX_TEXT_CHARS))
        if offset >= len(text) and offset != 0:
            raise ValueError('读取位置超过正文长度')
        return self._evidence(sid, metadata, text[offset:end], offset, end,
                              end if end < len(text) else None, 'image' if images else 'character'), images

    def _evidence(self, sid: str, metadata: dict, text: str, start: int, end: int, next_offset: int | None, unit: str) -> dict:
        digest = hashlib.sha256(text.encode()).hexdigest()
        eid = sid + ':excerpt:' + hashlib.sha256(f'{start}:{end}:{digest}'.encode()).hexdigest()[:20]
        evidence = {**{k: v for k, v in metadata.items() if k in ('name', 'kind', 'issue_key', 'conversation_id', 'turn_id', 'independent', 'author', 'created_at')},
                    'id': eid, 'text': text, 'sha256': digest,
                    'independent': bool(metadata.get('independent')) and bool(text.strip()),
                    'material_id': sid, 'content_sha256': metadata.get('cache', metadata.get('sha256', '')), 'range': {'start': start, 'end': end, 'unit': unit}, 'retrieved_at': beijing_timestamp()}
        if self.recording:
            self.archive.evidence(evidence)
        return {'source_id': sid, 'evidence_id': eid, 'name': metadata.get('name') or (metadata.get('issue_key','')+' '+{'description':'Jira 正文','comment':'Jira 评论','owner_message':'人工补充','agent_message':'Agent 过程说明'}.get(metadata.get('kind'),'材料')).strip(), 'text': text,
                'range': evidence['range'], 'next_offset': next_offset}

    def request(self, arguments: dict, call_id: str, *, record=True) -> dict:
        self.recording=record
        sid = arguments.get('source_id', '')
        action = arguments.get('action')
        offset, limit = arguments.get('offset', 0), arguments.get('limit', MAX_TEXT_CHARS)
        if not isinstance(sid, str) or action not in ('list', 'read') or type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= MAX_TEXT_CHARS:
            raise ValueError('材料请求参数无效')
        reason = arguments.get('reason', '')
        if not isinstance(reason, str) or not reason.strip() or len(reason) > 2000:
            raise ValueError('请说明此次读取需要核对的判断')
        batch=arguments.get('source_ids',[])
        if not isinstance(batch,list) or len(batch)>5 or any(not isinstance(s,str) or not s for s in batch):
            raise ValueError('批量读取最多五条材料')
        if batch:
            if action != 'read':
                raise ValueError('批量操作仅支持读取')
            results=[self.request({**arguments,'source_ids':[],'source_id':value,'limit':min(limit,MAX_TEXT_CHARS//len(batch))},call_id+':'+str(i),record=record) for i,value in enumerate(batch)]
            return {'success':all(r['success'] for r in results),'contentItems':[item for r in results for item in r['contentItems']]}
        if action=='read' and sid.startswith('case:'):
            overview=self.archive.overview(sid[5:])
            return {'success':True,'contentItems':[{'type':'inputText','text':json.dumps(overview,ensure_ascii=False)}]}
        item = self._item(sid)
        entry = self.archive.get(sid) if sid else None
        if sid and not item and not entry:
            case=next((c for c in self.archive.cases() if sid.startswith(c['issue']['key']+':')),None)
            if case and 'pending' in (case['local_status'],case['jira_status']):
                return {'success':True,'contentItems':[{'type':'inputText','text':json.dumps({'source_id':sid,'status':'preparing','message':'该 Jira 材料正在准备，请先读取已就绪内容，稍后重试。'})}]}
            raise ValueError('材料 ID 不属于本任务')
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        images = []
        query = arguments.get('query', '')
        if not isinstance(query, str) or len(query) > 500:
            raise ValueError('材料筛选条件无效')
        try:
            if action == 'list' and sid and item is None:
                raise ValueError('该材料是文本，请使用 read')
            result = self._list(item, offset, limit, query) if action == 'list' else None
            if result is None:
                result, images = self._read(sid, item, offset, limit)
            success = True
        except (OSError, ValueError, tarfile.TarError, zipfile.BadZipFile, EOFError, ImportError, PyPdfError, Image.DecompressionBombError, zlib.error) as exc:
            # Keep reasons actionable without disclosing server paths or credentials.
            error = ('文件已不存在' if isinstance(exc, FileNotFoundError) else '没有读取权限' if isinstance(exc, PermissionError)
                     else f'附件请求失败（HTTP {exc.code}）' if isinstance(exc, urllib.error.HTTPError)
                     else '材料获取失败' if isinstance(exc, OSError) else str(exc)[:300])
            result = {'source_id': sid, 'error': error, 'reason': reason}
            success = False
            if item and isinstance(exc, (FileNotFoundError, PermissionError, EOFError, tarfile.TarError, zipfile.BadZipFile)):
                item['error'] = error
        if record:
            self.archive.record(call_id, {'source_id': sid, 'action': action, 'offset': offset, 'limit': limit,
                'reason': reason, 'success': success, 'retrieved_at': beijing_timestamp(),
                'evidence_id': result.get('evidence_id', ''), 'error': result.get('error', '')})
        self.archive.cache_items(self.items)
        return {'success': success, 'contentItems': [{'type': 'inputText', 'text': json.dumps(result, ensure_ascii=False)}, *images]}
