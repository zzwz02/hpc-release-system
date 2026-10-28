"""Per-job material index. Listing never reads source bodies or reparses archives."""
from __future__ import annotations

import contextlib
import fcntl
import json
import os
import shutil
import sqlite3
import tempfile
from pathlib import Path

from app.timeutil import beijing_timestamp


def category(item: dict) -> str:
    if item['kind'] in ('description', 'comment', 'custom_field', 'owner_message', 'agent_message', 'case_overview'):
        return 'issue'
    if item['kind'] in ('agent_comment', 'agent_result', 'agent_analysis_path') or Path(item.get('name', '')).name.lower() == 'report.md':
        return 'analysis'
    return 'files'


class LearningArchive:
    def __init__(self, directory: Path):
        self.directory = directory
        self.path = directory / 'materials' / 'sources.db'

    @contextlib.contextmanager
    def connect(self, *, write=False):
        if write:
            self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.path) if write else 'file:' + str(self.path) + '?mode=ro', uri=not write, timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            if write:
                conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    def initialize(self, issues: list[dict]) -> None:
        with self.connect(write=True) as conn:
            conn.executescript('''
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS cases (key TEXT PRIMARY KEY, issue TEXT NOT NULL,
                    local_status TEXT NOT NULL DEFAULT 'pending', jira_status TEXT NOT NULL DEFAULT 'pending',
                    acquisition TEXT NOT NULL DEFAULT '{}', error TEXT NOT NULL DEFAULT '', captured_at TEXT NOT NULL DEFAULT '');
                CREATE TABLE IF NOT EXISTS entries (id TEXT PRIMARY KEY, issue_key TEXT NOT NULL,
                    category TEXT NOT NULL, kind TEXT NOT NULL, search TEXT NOT NULL, metadata TEXT NOT NULL,
                    text TEXT, origin TEXT, evidence INTEGER NOT NULL DEFAULT 0);
                CREATE INDEX IF NOT EXISTS entry_case ON entries(issue_key,category);
                CREATE TABLE IF NOT EXISTS cache (id TEXT PRIMARY KEY, data TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS cache_parent ON cache(json_extract(data,'$.origin.parent'));
                CREATE TABLE IF NOT EXISTS reads (id TEXT PRIMARY KEY, data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS knowledge (id TEXT PRIMARY KEY, data TEXT NOT NULL);
            ''')
            conn.executemany('INSERT OR IGNORE INTO cases(key,issue) VALUES (?,?)',
                             [(issue['key'], json.dumps(issue, ensure_ascii=False)) for issue in issues])
        self.path.chmod(0o600)

    def put(self, conn, item: dict, *, evidence=False) -> None:
        metadata = {k: v for k, v in item.items() if k not in ('text', 'origin')}
        conn.execute('''INSERT OR IGNORE INTO entries VALUES (?,?,?,?,?,?,?,?,?)''',
            (item['id'], item.get('issue_key', ''), category(item), item['kind'],
             ' '.join(str(item.get(k, '')) for k in ('id', 'name', 'author', 'issue_key', 'kind')).casefold(),
             json.dumps(metadata, ensure_ascii=False), item.get('text'),
             json.dumps(item['origin']) if 'origin' in item else None, int(evidence)))

    def put_case(self, key: str, source: dict, *, local=False, error='') -> None:
        prefix = key + ':'
        with self.connect(write=True) as conn:
            for original in source.get('sources', []) + source.get('_materials', []):
                item = {**original, 'id': prefix + original['id'], 'issue_key': key}
                self.put(conn, item)
            if local:
                conn.execute("UPDATE cases SET local_status=?,error=CASE WHEN ? != '' THEN ? ELSE error END,captured_at=? WHERE key=?",
                             ('failed' if error else 'ready', error, error, beijing_timestamp(), key))
            else:
                issue = source.get('issue') or {'key': key}
                acquisition = source.get('acquisition') or {'status': 'missing'}
                conn.execute('UPDATE cases SET issue=?,jira_status=?,acquisition=?,error=CASE WHEN ? != \'\' THEN ? ELSE error END,captured_at=? WHERE key=?',
                    (json.dumps(issue, ensure_ascii=False), 'failed' if error else 'ready', json.dumps(acquisition), error, error, beijing_timestamp(), key))

    def import_legacy(self, source: dict) -> None:
        """Atomically import a legacy manifest/index, preserving IDs and pinned blobs."""
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with (self.path.parent / 'migration.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            if self.path.exists():
                return
            cases = source.get('cases') or [{'issue': source['issue'], 'acquisition': source.get('acquisition', {})}]
            membership = {sid: case['issue']['key'] for case in cases for sid in case.get('source_ids', [])}
            index_path = self.path.parent / 'index.json'
            index = json.loads(index_path.read_text()) if index_path.exists() else {}
            with tempfile.TemporaryDirectory(prefix='.migration-', dir=self.path.parent) as folder:
                target = LearningArchive(Path(folder))
                target.initialize([case['issue'] for case in cases])
                with target.connect(write=True) as conn:
                    for item in source.get('sources', []):
                        target.put(conn, {**item, 'issue_key': item.get('issue_key') or membership.get(item['id'], cases[0]['issue']['key'])}, evidence=True)
                    for item in index.get('items', {}).values():
                        item = {**item, 'issue_key': item.get('issue_key') or cases[0]['issue']['key']}
                        target.put(conn, item)
                        conn.execute('INSERT INTO cache VALUES (?,?)', (item['id'], json.dumps(item, ensure_ascii=False)))
                    reads = index.get('reads') or {f'legacy:{i}': r for i, r in enumerate(source.get('material_reads', []))}
                    conn.executemany('INSERT INTO reads VALUES (?,?)', [(key, json.dumps(value, ensure_ascii=False)) for key, value in reads.items()])
                    for case in cases:
                        key = case['issue']['key']
                        gaps = case.get('collection_gaps', []) + [gap for gap in source.get('collection_gaps', [])
                            if (gap.get('issue_key') or membership.get(gap.get('source')) or cases[0]['issue']['key']) == key]
                        errors = [case.get('error', '')] + [gap.get('reason', '') for gap in gaps
                            if gap.get('category', 'blocking') not in ('skipped', 'indexed')]
                        error = '\n'.join(dict.fromkeys(text for text in errors if text))
                        conn.execute("UPDATE cases SET local_status='ready',jira_status=?,acquisition=?,error=?,captured_at=? WHERE key=?",
                                     ('failed' if error else 'ready', json.dumps(case.get('acquisition', {})), error,
                                      case.get('captured_at') or source.get('captured_at', ''), key))
                if source.get('knowledge_snapshot'):
                    target.set_knowledge(source['knowledge_snapshot'])
                # All SQLite connections have closed, so the staged WAL is checkpointed.
                os.replace(target.path, self.path)

    def cases(self) -> list[dict]:
        with self.connect() as conn:
            return self._cases(conn)

    @staticmethod
    def _cases(conn) -> list[dict]:
        return [{'issue': json.loads(row['issue']), 'local_status': row['local_status'], 'jira_status': row['jira_status'],
                 'acquisition': json.loads(row['acquisition']), 'error': row['error'], 'captured_at': row['captured_at'],
                 'source_ids': [], 'collection_gaps': []} for row in conn.execute('SELECT * FROM cases ORDER BY rowid')]

    def progress(self, cases=None) -> dict:
        cases = self.cases() if cases is None else cases
        return {'total': len(cases), 'ready': sum(c['local_status'] == 'ready' and c['jira_status'] == 'ready' for c in cases),
                'pending': sum('pending' in (c['local_status'], c['jira_status']) for c in cases),
                'limited': sum(bool({'failed','cancelled'} & {c['local_status'], c['jira_status']}) or c['acquisition'].get('status') in ('partial', 'missing') for c in cases)}

    def get(self, sid: str, *, body=False) -> dict | None:
        with self.connect() as conn:
            row = conn.execute('SELECT metadata,origin' + (',text' if body else '') + ' FROM entries WHERE id=?', (sid,)).fetchone()
        if not row:
            return None
        item = json.loads(row['metadata'])
        if row['origin']:
            item['origin'] = json.loads(row['origin'])
        if body and row['text'] is not None:
            item['text'] = row['text']
        return item

    def listing(self, *, issue='', kind='', query='', offset=0, limit=20, include_cases=False) -> dict:
        # Previously supplied inline sources remain visible after migration;
        # read excerpts carry material_id and are shown only through citations.
        clauses = ["json_extract(metadata,'$.material_id') IS NULL"];params = []
        if issue:
            clauses.append('issue_key=?');params.append(issue)
        if kind:
            clauses.append('category=?');params.append(kind)
        for word in query.casefold().split():
            clauses.append('instr(search,?)>0');params.append(word)
        where = ' AND '.join(clauses)
        with self.connect() as conn:
            conn.execute('BEGIN')
            total = conn.execute('SELECT COUNT(*) FROM entries WHERE '+where, params).fetchone()[0]
            rows = conn.execute('SELECT metadata FROM entries WHERE '+where+' ORDER BY rowid LIMIT ? OFFSET ?', [*params, min(limit,100), offset]).fetchall()
            result = {'items': [json.loads(row[0]) for row in rows], 'total': total,
                      'next_offset': offset+len(rows) if offset+len(rows)<total else None}
            if include_cases:
                result['cases'] = [{'id': 'case:'+case['issue']['key'], **case} for case in self._cases(conn)]
        return result

    def overview(self, key: str) -> dict:
        case = next((case for case in self.cases() if case['issue']['key'] == key), None)
        if case is None:
            raise ValueError('Jira 不属于本任务')
        with self.connect() as conn:
            rows = conn.execute("SELECT metadata,substr(text,1,400) AS text FROM entries WHERE issue_key=? AND kind IN ('agent_result','owner_message') AND json_extract(metadata,'$.material_id') IS NULL ORDER BY rowid",(key,)).fetchall()
            messages = [json.loads(row['metadata']) | {'preview': (row['text'] or '')[:400]} for row in rows]
            counts = {row[0]: row[1] for row in conn.execute("SELECT kind,COUNT(*) FROM entries WHERE issue_key=? AND json_extract(metadata,'$.material_id') IS NULL GROUP BY kind",(key,))}
        return {**case, 'kind': 'case_overview', 'id': 'case:'+key, 'independent': False,
                'message_count': len(messages), 'recent_results_and_feedback': messages[-20:], 'counts': counts,
                'note': '概览是检索入口；结果和补充的片段不是已核实证据。使用 list 按 Jira 查询全部轮次和消息，必要时 read 原文。'}

    def evidence(self, item: dict) -> None:
        with self.connect(write=True) as conn:
            self.put(conn, item, evidence=True)

    def record(self, call_id: str, entry: dict) -> None:
        with self.connect(write=True) as conn:
            conn.execute('INSERT OR REPLACE INTO reads VALUES (?,?)', (call_id,json.dumps(entry,ensure_ascii=False)))

    def validation(self) -> dict:
        with self.connect() as conn:
            sources = [json.loads(row[0]) for row in conn.execute('SELECT metadata FROM entries WHERE evidence=1')]
            reads = [json.loads(row[0]) for row in conn.execute('SELECT data FROM reads ORDER BY rowid')]
            failures = {entry['source_id'] for entry in reads if not entry['success']}
            materials = [item for sid in failures if (item := self.cached(sid) or self.get(sid))]
        return {'sources': sources, 'material_reads': reads, 'materials': materials, 'cases': self.cases(), 'format': 2, 'progress': self.progress()}

    def set_knowledge(self, knowledge: dict) -> None:
        with self.connect(write=True) as conn:
            conn.execute('INSERT OR REPLACE INTO knowledge VALUES (?,?)',('snapshot', json.dumps(knowledge,ensure_ascii=False)))
            for kind in ('repository','analysis'):
                for path,text in knowledge.get(kind,{}).get('files',{}).items():
                    self.put(conn, {'id': 'skill:'+kind+':'+path, 'kind':'skill', 'name':path, 'text':text,
                                    'independent':False,'version':knowledge[kind].get('commit','')})

    def knowledge(self) -> dict:
        with self.connect() as conn:
            row=conn.execute("SELECT data FROM knowledge WHERE id='snapshot'").fetchone()
        return json.loads(row[0]) if row else {}

    def bundle(self) -> dict:
        cases=self.cases()
        progress=self.progress(cases)
        return {'format':2, 'issue':cases[0]['issue'], 'cases':cases, 'sources':[], 'material_access':True,
                'progress':progress, 'acquisition':{'status':'pending' if progress['pending'] else 'partial' if progress['limited'] else 'complete'},
                'collection_gaps':[{'source':'case:'+c['issue']['key'],'issue_key':c['issue']['key'],'category':'blocking','reason':c['error']}
                                   for c in cases if c['error']]}

    def cancel_pending(self) -> None:
        with self.connect(write=True) as conn:
            conn.execute("UPDATE cases SET local_status=CASE WHEN local_status='pending' THEN 'cancelled' ELSE local_status END, jira_status=CASE WHEN jira_status='pending' THEN 'cancelled' ELSE jira_status END")

    def references(self, ids: list[str], *, bodies=False) -> list[dict]:
        if not ids:
            return []
        with self.connect() as conn:
            rows = conn.execute('SELECT metadata' + (',text' if bodies else '') +
                ' FROM entries WHERE id IN (' + ','.join('?' for _ in ids) + ')', ids).fetchall()
        items = {json.loads(row['metadata'])['id']: json.loads(row['metadata']) |
                 ({'text': row['text']} if bodies and row['text'] is not None else {}) for row in rows}
        return [items[sid] for sid in ids if sid in items]

    def cached(self, sid: str) -> dict | None:
        with self.connect() as conn:
            row=conn.execute('SELECT data FROM cache WHERE id=?',(sid,)).fetchone()
        return json.loads(row[0]) if row else None

    def cache_items(self, items: dict) -> None:
        with self.connect(write=True) as conn:
            conn.executemany('INSERT OR REPLACE INTO cache VALUES (?,?)',
                [(sid,json.dumps(item,ensure_ascii=False)) for sid,item in items.items()])

    def cache_listing(self, parent: str, query: str, offset: int, limit: int) -> dict:
        from app.services.jira_learning_materials import public
        clauses=["json_extract(data,'$.origin.parent')=?"]
        params=[parent]
        for word in query.casefold().split():
            clauses.append("instr(lower(json_extract(data,'$.name') || ' ' || coalesce(json_extract(data,'$.issue_key'),'')),?)>0")
            params.append(word)
        where=' AND '.join(clauses)
        with self.connect() as conn:
            total=conn.execute('SELECT COUNT(*) FROM cache WHERE '+where,params).fetchone()[0]
            rows=conn.execute('SELECT data FROM cache WHERE '+where+' ORDER BY rowid LIMIT ? OFFSET ?',[*params,min(limit,100),offset]).fetchall()
        return {'items':[public(json.loads(row[0])) for row in rows],'total':total,
                'next_offset':offset+len(rows) if offset+len(rows)<total else None}

    def inherit(self, directory: Path, prefix: str = '', manifest: str = '', digest: str = '') -> dict:
        """Import fixed snapshots through the same store, including legacy evidence."""
        import re
        from app.services import jira_learning_snapshots as snapshots
        original = LearningArchive(directory)
        if not original.path.exists():
            original.import_legacy(snapshots.read({'id': directory.name, 'source_path': manifest, 'source_sha256': digest}))
        aliases = {}

        def remap(item):
            item['id'] = prefix + item['id']
            for field in ('material_id', 'evidence_id', 'source_id'):
                if item.get(field):
                    item[field] = prefix + item[field]
            if item.get('origin', {}).get('parent'):
                item['origin']['parent'] = prefix + item['origin']['parent']
            if item.get('origin', {}).get('parts'):
                item['origin']['parts'] = [prefix + sid for sid in item['origin']['parts']]
            if isinstance(item.get('members'), list):
                item['members'] = [prefix + sid for sid in item['members']]
            return item

        with self.connect(write=True) as conn, original.connect() as source:
            for row in source.execute("SELECT * FROM entries WHERE kind != 'skill' ORDER BY rowid"):
                item = json.loads(row['metadata'])
                if row['text'] is not None:
                    item['text'] = row['text']
                if row['origin']:
                    item['origin'] = json.loads(row['origin'])
                item = remap(item)
                evidence = bool(row['evidence'])
                if evidence and 'text' in item and not item.get('material_id') and not item.get('origin'):
                    matches = conn.execute('SELECT id,metadata FROM entries WHERE issue_key=? AND kind=? AND text=? AND evidence=1',
                                           (item.get('issue_key',''),item['kind'],item['text'])).fetchall()
                    match = next((r for r in matches if bool(json.loads(r['metadata']).get('independent')) == bool(item.get('independent'))), None)
                    if match and match['id'] != item['id']:
                        aliases[item['id']] = match['id']
                        saved = json.loads(match['metadata'])
                        copies = saved.setdefault('copies', [])
                        if item['id'] not in {c['id'] for c in copies}:
                            copies.append({k:v for k,v in item.items() if k not in ('text','copies')})
                        conn.execute('UPDATE entries SET metadata=? WHERE id=?',(json.dumps(saved,ensure_ascii=False),match['id']))
                        evidence = False
                self.put(conn, item, evidence=evidence)
            for row in source.execute('SELECT data FROM cache'):
                item = remap(json.loads(row[0]))
                conn.execute('INSERT OR IGNORE INTO cache VALUES (?,?)',(item['id'],json.dumps(item,ensure_ascii=False)))
            for row in source.execute('SELECT id,data FROM reads'):
                item = json.loads(row['data'])
                for field in ('source_id','evidence_id'):
                    if item.get(field): item[field] = prefix + item[field]
                conn.execute('INSERT OR IGNORE INTO reads VALUES (?,?)',(prefix+row['id'],json.dumps(item,ensure_ascii=False)))
            for case in original._cases(source):
                failed = bool(case['error']) or case['jira_status'] in ('pending','deferred','failed','cancelled') or case['local_status'] in ('pending','deferred','failed','cancelled')
                previous = conn.execute('SELECT acquisition,error FROM cases WHERE key=?', (case['issue']['key'],)).fetchone()
                acquisition = dict(case['acquisition'])
                if json.loads(previous['acquisition']).get('status') in ('partial', 'missing'):
                    acquisition['status'] = 'partial'
                error = '\n'.join(dict.fromkeys(text for text in (previous['error'], case['error']) if text))
                conn.execute("UPDATE cases SET issue=?,local_status='ready',jira_status=CASE WHEN jira_status='failed' OR ? THEN 'failed' ELSE 'ready' END,acquisition=?,error=?,captured_at=? WHERE key=?",
                    (json.dumps(case['issue'],ensure_ascii=False),failed,json.dumps(acquisition),error,
                     case['captured_at'],case['issue']['key']))
        folder = directory / 'materials'
        for blob in folder.iterdir():
            if re.fullmatch('[0-9a-f]{64}',blob.name) and blob.is_file():
                target = self.path.parent / blob.name
                if not target.exists():
                    try:
                        os.link(blob,target)
                    except FileExistsError:
                        pass
                    except OSError:
                        shutil.copyfile(blob,target)
                    target.chmod(0o600)
        return aliases
