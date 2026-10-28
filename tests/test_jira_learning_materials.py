"""Read real files/archives and drive the material tool through a local protocol peer."""
from __future__ import annotations

import asyncio
import gzip
import json
import threading
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from app.config import settings
from app.db.connection import transaction
from app.domain import jira_learning as domain
from app.integrations import knowledge_repository as knowledge
from app.repositories import jira_agent_repo as analysis, jira_learning_repo as repo
from app.services import jira_learning_sources as collection, jira_learning_runner as execution
from app.services.jira_learning_materials import MaterialStore, catalog, TOOL
from app.services.jira_learning_archive import LearningArchive
from tests.test_jira_learning_sources import record, archive
from tests.test_jira_learning_workflow import env


def request(store, sid='', *, action='read', offset=0, limit=48000, call='call1'):
    response = store.request({'action': action, 'source_id': sid, 'offset': offset, 'limit': limit,
                              'reason': '核对正常与异常条件的实际差异'}, call)
    return response, json.loads(response['contentItems'][0]['text'])


def register(store, items):
    with store.archive.connect(write=True) as conn:
        for item in items:
            store.archive.put(conn, item)


def file_sources(store):
    return [item for item in store.archive.listing(limit=100)['items'] if store.archive.get(item['id']).get('origin')]


def create(tmp_path, files):
    issue = {'key': 'CASE-1', 'description': 'compare conditions'}
    archive = LearningArchive(tmp_path / 'job')
    archive.import_legacy(domain.build_source(issue, [], set()))
    store = MaterialStore(archive.directory)
    register(store, catalog(issue, [], files))
    return store


def test_uncollected_large_log_is_read_in_ranges_and_pinned(tmp_path):
    log = tmp_path / 'bad.stdout'
    content = 'initial\n' + 'x' * (1024 * 1024) + '\nresult=20\n'
    log.write_text(content)
    files = [record(log)]
    collected, gaps = collection.collect_sources({}, [], [], files)
    assert not collected and gaps
    store = create(tmp_path, files)
    sid = file_sources(store)[0]['id']
    response, first = request(store, sid, limit=8)
    assert response['success'] and first['text'] == content[:8] and first['next_offset'] == 8
    response, last = request(store, sid, offset=len(content) - 11, call='call2')
    assert response['success'] and last['next_offset'] is None and 'result=20' in last['text']
    assert first['evidence_id'] != last['evidence_id']
    log.write_text('replaced after first read')
    reopened = MaterialStore(tmp_path / 'job')
    assert request(reopened, sid, offset=len(content) - 11, call='call3')[1]['text'] == last['text']
    assert not any('origin' in item for item in file_sources(store))
    assert {e['range']['start'] for e in store.archive.validation()['sources'] if e.get('range')} == {0, len(content) - 11}


def test_inventory_preserves_files_omitted_by_initial_budget(tmp_path, monkeypatch):
    files = []
    for i in range(7):
        path = tmp_path / f'run-{i}.log';path.write_text(f'observed={i}\n');files.append(record(path))
    monkeypatch.setattr(collection, 'MAX_SOURCE_FILES', 2)
    collected, _ = collection.collect_sources({}, [], [], files)
    assert len(collected) == 2
    store = create(tmp_path, files)
    _, page = request(store, action='list', limit=3)
    assert page['total'] == 8 and page['next_offset'] == 3  # description plus all seven logs
    sid = file_sources(store)[-1]['id']
    assert request(store, sid)[1]['text'] == 'observed=6\n'


@pytest.mark.parametrize('kind', ['tar.gz', 'zip'])
def test_archive_lists_and_reads_selected_good_bad_members(tmp_path, kind):
    path = tmp_path / f'evidence.{kind}'
    entries = [('vendor/unneeded.hpp', b'int auxiliary;'), ('good/metrics.log', b'elapsed=10\n'), ('bad/metrics.log', b'elapsed=20\n')]
    if kind == 'zip':
        with zipfile.ZipFile(path, 'w') as bundle:
            for name, data in entries:bundle.writestr(name, data)
        file = record(path)
    else:file = archive(path, entries)
    store = create(tmp_path, [file])
    sid = file_sources(store)[0]['id']
    response, index = request(store, sid, action='list')
    assert response['success'] and len(index['items']) == 3
    results = [request(store, item['id'], call=item['id'])[1] for item in index['items'][1:]]
    assert [item['text'] for item in results] == ['elapsed=10\n', 'elapsed=20\n']
    assert results[0]['evidence_id'] != results[1]['evidence_id']
    assert all(item['independent'] for item in store.archive.validation()['sources'][-2:])
    assert not (tmp_path / 'good').exists()
    with pytest.raises(ValueError, match='不属于本任务'):
        request(store, '/etc/passwd')


def test_changed_source_and_invalid_ranges_are_not_silently_accepted(tmp_path):
    file = tmp_path / 'input.log';file.write_text('original')
    store = create(tmp_path, [record(file)])
    sid = file_sources(store)[0]['id']
    file.write_text('changed after collection')
    response, result = request(store, sid)
    assert not response['success'] and '变化' in result['error']
    assert len(store.archive.validation()['sources']) == 1
    store = create(tmp_path / 'other', [record(file)])
    sid = file_sources(store)[0]['id']
    assert not request(store, sid, offset=10000)[0]['success']
    assert request(store, sid, call='valid')[1]['text'] == 'changed after collection'


def test_archive_does_not_follow_links_or_ambiguous_paths(tmp_path):
    import tarfile
    link = tarfile.TarInfo('link.log');link.type = tarfile.SYMTYPE;link.linkname = '/etc/passwd'
    file = archive(tmp_path / 'files.tar', [(link, b''), ('../escape.log', b'bad'), ('safe.log', b'good')])
    store = create(tmp_path, [file]);sid = file_sources(store)[0]['id']
    listed = request(store, sid, action='list')[1]['items']
    assert [item['name'] for item in listed] == ['link.log', '../escape.log', 'safe.log']
    assert not request(store, listed[0]['id'], call='link')[0]['success']
    assert not request(store, listed[1]['id'], call='escape')[0]['success']
    assert request(store, listed[2]['id'], call='safe')[1]['text'] == 'good'
    file = archive(tmp_path / 'duplicate.tar', [('same.log', b'first'), ('./same.log', b'second')])
    store = create(tmp_path / 'other', [file]);sid = file_sources(store)[0]['id']
    assert not request(store, sid, action='list')[0]['success']
    assert len(store.items) == 1


def test_gzip_image_and_pdf_page_are_actual_extracted_content(tmp_path):
    from PIL import Image
    from pypdf import PdfWriter
    from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject
    gz = tmp_path / 'values.json.gz';gz.write_bytes(gzip.compress(b'{"observed": 42}'))
    image = tmp_path / 'chart.png';Image.new('RGB', (10, 10), 'red').save(image)
    pdf = tmp_path / 'report.pdf'
    writer = PdfWriter();page = writer.add_blank_page(width=200, height=200)
    font = DictionaryObject({NameObject('/Type'): NameObject('/Font'), NameObject('/Subtype'): NameObject('/Type1'), NameObject('/BaseFont'): NameObject('/Helvetica')})
    page[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'): DictionaryObject({NameObject('/F1'): writer._add_object(font)})})
    stream = DecodedStreamObject();stream.set_data(b'BT /F1 12 Tf 10 100 Td (verified elapsed=20) Tj ET')
    page[NameObject('/Contents')] = writer._add_object(stream)
    writer.add_blank_page(width=200, height=200);writer.write(pdf)
    store = create(tmp_path, [record(p) for p in (gz, image, pdf)])
    ids = [i['id'] for i in file_sources(store)]
    assert request(store, ids[0])[1]['text'] == '{"observed": 42}'
    response, _ = request(store, ids[1], call='image')
    assert response['contentItems'][1]['imageUrl'].startswith('data:image/png;base64,')
    response, result = request(store, ids[2], call='pdf')
    assert response['success'] and 'verified elapsed=20' in result['text'] and result['next_offset'] == 1
    assert not request(store, ids[2], offset=1, call='scan')[0]['success']


def test_attachment_retries_transient_failure_and_uses_cache(tmp_path, monkeypatch):
    class Handler(BaseHTTPRequestHandler):
        count = 0
        def do_GET(self):
            Handler.count += 1
            self.send_response(503 if Handler.count == 1 else 200);self.end_headers()
            self.wfile.write(b'elapsed=20')
        def log_message(self, *args):pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True);thread.start()
    try:
        url = f'http://127.0.0.1:{server.server_port}'
        conf = tmp_path / 'site.conf';conf.write_text(f'[jira]\nJIRA_BASE_URL={url}\nJIRA_TOKEN=test\n')
        monkeypatch.setattr(settings, 'runtime_conf_path', conf)
        issue = {'key': 'CASE-1', 'attachments': [{'id': '1', 'filename': 'run.log', 'content_url': url + '/run.log'}]}
        archive = LearningArchive(tmp_path / 'job')
        archive.import_legacy(domain.build_source(issue, [], set()))
        store = MaterialStore(archive.directory)
        register(store, catalog(issue, [], []))
        assert request(store, 'jira:attachment:1')[1]['text'] == 'elapsed=20'
        assert Handler.count == 2
        assert request(store, 'jira:attachment:1', call='again')[0]['success'] and Handler.count == 2
    finally:
        server.shutdown();server.server_close();thread.join(timeout=2)


def test_all_participants_failed_turn_and_public_process_enter_learning(env):
    _, _, conn = env
    with transaction(conn):
        conversation = analysis.create_conversation(conn, conversation_id='jac_messages', issue_key='MC3-3', issue_summary='example', agent_group='HPC', owner='owner', created_by='rm', workspace='/tmp/example')
        turn = analysis.create_turn(conn, conversation_id=conversation['id'], trigger='handover', created_by='rm', input_text='')
        for kind, payload in [('user_message', {'author': 'rm', 'text': 'automatic handover', 'mode': 'handover'}),
                              ('user_message', {'author': 'rm', 'text': '正确的修正要求', 'mode': 'steer'}),
                              ('user_message', {'author': 'owner', 'text': '复测观察'}),
                              ('agent_message', {'text': '尝试方法后发现条件不同'}),
                              ('reasoning', {'text': '已展示的判断摘要：尚未核实异常侧条件'}),
                              ('command', {'command': 'cat result.log', 'output': 'observed=20'}),
                              ('status', {'text': 'heartbeat'})]:
            analysis.add_event(conn, conversation_id=conversation['id'], turn_id=turn['id'], kind=kind, payload=payload)
        analysis.update_turn(conn, turn['id'], status='failed', result_json=json.dumps({'summary': 'partial observation'}))
    messages = repo.conversation_messages(conn, 'MC3-3', 'HPC')
    assert len(messages) == 5
    assert [m['author'] for m in messages] == ['rm', 'owner', 'agent', 'agent', 'agent']
    assert all(m['turn_status'] == 'failed' for m in messages)
    source = domain.build_source({'key': 'MC3-3'}, messages, set())
    assert source['sources'][-1]['independent'] and not source['sources'][-2]['independent']
    assert repo.agent_results(conn, 'MC3-3', 'HPC')[0]['turn_status'] == 'failed'
    assert domain.conversation_index(source['sources'])[0]['turns'][0]['source_ids'] == [s['id'] for s in source['sources'][1:]]


def test_gaps_require_an_actual_failed_read_and_explicit_impact(tmp_path):
    file = tmp_path / 'unsupported.bin';file.write_bytes(b'\0\1\2')
    store = create(tmp_path, [record(file)]);sid = file_sources(store)[0]['id']
    proposal = {'kind': 'do_not_publish', 'title': '尚不能确认', 'summary': '缺少证据', 'proposed_text': '', 'files': [],
                'evidence_ids': [], 'applicability': '', 'limitations': '', 'validation': '',
                'material_gaps': [{'source_id': sid, 'impact': '不能核对实际执行条件'}]}
    raw = json.dumps({'proposals': [proposal]})
    with pytest.raises(ValueError, match='实际未成功'):
        domain.validate_proposals(raw, store.archive.validation(), {})
    request(store, sid)
    checked = domain.validate_proposals(raw, store.archive.validation(), {})[0]
    assert checked['material_gaps'][0]['name'] == 'unsupported.bin'
    assert '格式' in checked['material_gaps'][0]['reason']


def test_runner_material_tool_completes_in_same_turn_and_rejects_other_scope(env, tmp_path):
    import websockets
    _, conf, conn = env
    async def scenario():
        transcript = []
        response = None
        async def peer(ws):
            nonlocal response
            async for raw in ws:
                message = json.loads(raw)
                if 'method' not in message:
                    transcript.append(message)
                    if message['id'] == 'wrong-scope':
                        assert not message['result']['success']
                        await ws.send(json.dumps({'id': 'read1', 'method': 'item/tool/call', 'params': {
                            'threadId': 'thread1', 'turnId': 'turn1', 'callId': 'read1', 'tool': TOOL['name'],
                            'arguments': {'action': 'read', 'source_id': sid, 'offset': 0, 'limit': 48000, 'reason': '核对异常侧耗时'}}}))
                        continue
                    response = json.loads(message['result']['contentItems'][0]['text'])
                    proposal = {'kind': 'skill_update', 'title': '按观察条件核对差异', 'summary': '保留可核对条件',
                        'proposed_text': '核对观察', 'files': [{'path': knowledge.PREFIX + 'SKILL.md',
                            'updated_text': snapshot['files'][knowledge.PREFIX + 'SKILL.md'] + '\nCheck observed conditions.\n'}],
                        'dependencies': [], 'previous_id': '', 'applicability': '性能对照',
                        'evidence_ids': [response['evidence_id']], 'limitations': '', 'validation': '核对原始日志', 'material_gaps': []}
                    await ws.send(json.dumps({'method': 'item/completed', 'params': {'turnId': 'turn1', 'item': {'type': 'agentMessage', 'id': 'final', 'text': json.dumps({'proposals': [proposal]})}}}))
                    await ws.send(json.dumps({'method': 'turn/completed', 'params': {'turn': {'id': 'turn1', 'status': 'completed'}}}))
                    continue
                transcript.append(message)
                if 'id' not in message:continue
                method = message['method'];params = message.get('params', {})
                result = {'thread': {'id': 'thread1'}} if method == 'thread/start' else {'turn': {'id': 'turn1'}} if method == 'turn/start' else {}
                if method == 'thread/start':assert params['dynamicTools'] == [TOOL]
                if method == 'initialize':assert params['capabilities']['experimentalApi'] is True
                await ws.send(json.dumps({'id': message['id'], 'result': result}))
                if method == 'turn/start':
                    await ws.send(json.dumps({'id': 'wrong-scope', 'method': 'item/tool/call', 'params': {
                        'threadId': 'another-task', 'turnId': 'turn1', 'callId': 'bad', 'tool': TOOL['name'], 'arguments': {}}}))
        async with websockets.serve(peer, '127.0.0.1', 0) as server:
            port = server.sockets[0].getsockname()[1]
            conf.write_text(conf.read_text().replace('ws://unused:4520', f'ws://127.0.0.1:{port}'))
            file = tmp_path / 'observations.log';file.write_text('elapsed=20\n')
            source = domain.build_source({'key': 'MC3-4', 'description': 'compare observations'}, [], set())
            snapshot = knowledge.snapshot('refs/heads/main')
            source['knowledge_snapshot'] = {'repository': snapshot, 'analysis': {'files': {}}}
            with transaction(conn):
                job = repo.create_job(conn, 'MC3-4', 'HPC', 'rm', repository_only=True)
                work = repo.claim_next_turn(conn)
            archive = LearningArchive(settings.jira_agent_data_dir / 'learning' / job['id'])
            archive.import_legacy(source)
            store = MaterialStore(archive.directory)
            register(store, catalog({'key': 'MC3-4'}, [], [record(file)]))
            sid = file_sources(store)[0]['id']
            runner = execution.JiraLearningRunner();runner._persist_source(job, source)
            await runner._run(repo.get_job(conn, job['id']), work['turn'])
            completed = repo.get_job(conn, job['id'])
            assert completed['status'] == 'completed', completed['error']
            assert archive.validation()['material_reads'][0]['success']
            proposal = repo.list_proposals(conn, job['id'])[0]
            assert proposal['evidence_ids'] == [response['evidence_id']]
            assert len([m for m in transcript if m.get('method') == 'turn/start']) == 1
            assert len([m for m in transcript if m.get('method') == 'thread/start']) == 1
            assert len(repo.list_turns(conn, job['id'])) == 1
    asyncio.run(scenario())


def test_combining_proposals_keeps_pinned_materials_and_original_jobs(env, tmp_path):
    from tests.test_jira_learning_workflow import stage, RM
    from app.services import jira_learning_service as service, jira_learning_relations as relations
    _, _, conn = env
    first, p1 = stage(conn, key='MC3-1')
    second, p2 = stage(conn, key='MC3-2')
    path = tmp_path / 'observation.log';path.write_text('captured=42\n')
    source = service.get_source(first['id'])
    original = MaterialStore(settings.jira_agent_data_dir / 'learning' / first['id'])
    register(original, catalog({'key': 'MC3-1'}, [], [record(path)]))
    sid = file_sources(original)[0]['id']
    assert request(original, sid)[0]['success']
    execution.JiraLearningRunner()._persist_source(first, source)
    path.unlink()  # the saved evidence remains usable by the new consolidation task
    before = repo.get_job(conn, first['id'])['source_sha256']
    combined = relations.combine(RM, [p1['id'], p2['id']], '', 'pin-once')['job']
    asyncio.run(execution.JiraLearningRunner()._reload(repo.get_job(conn, combined['id']), {}))
    store = MaterialStore(settings.jira_agent_data_dir / 'learning' / combined['id'])
    assert request(store, first['id'] + ':' + sid)[1]['text'] == 'captured=42\n'
    assert repo.get_job(conn, first['id'])['source_sha256'] == before
    assert repo.get_proposal(conn, p1['id'])['status'] == 'pending'
