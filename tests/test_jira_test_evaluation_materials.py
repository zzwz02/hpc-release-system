"""Evaluation reads original saved evidence, including archives, through the shared store."""
import gzip
import hashlib
import json

import pytest

from app.db.connection import transaction
from app.domain import jira_test_evaluation as grading
from app.repositories import jira_agent_repo as agent
from app.services import jira_agent_runner as execution, jira_test_evaluation as evaluation
from app.services.jira_learning_materials import MaterialStore
from app.services.jira_learning_archive import LearningArchive
from tests.test_jira_agent import env, _wait, _done
from tests.test_jira_learning_materials import create, file_sources, request
from tests.test_jira_learning_sources import archive, record
from tests.test_jira_testing import setup, start


def read_all(store, sid, limit=1700):
    pieces = []
    offset = 0
    while True:
        response, result = request(store, sid, offset=offset, limit=limit, call=f'{sid}:{offset}')
        assert response['success'], result
        pieces.append(result['text'])
        if result['next_offset'] is None:
            return ''.join(pieces)
        offset = result['next_offset']


def test_evaluation_keeps_full_code_commands_gzip_and_late_process_records(setup):
    env, _, _, case = setup
    cid = start(env, case)['runs'][0]['conversation_id']
    _wait(env, cid, _done)
    code = '// header\n' + 'double compute(double x) { return x * x; }\n' * 300 + '// verified tail\n'
    commands = json.dumps({'commands': ['compiler -O3 -g kernel.cpp'] * 4000})
    devices = json.dumps({'events': [{'kernel': 'compute', 'us': 17}] * 700})
    package = archive(env.tmp / 'compiler.tar.gz', [('replay.cpp', code.encode()),
        ('commands.json', commands.encode()), ('device-events.json.gz', gzip.compress(devices.encode()))])
    with execution.open_db() as conn, transaction(conn):
        turn = agent.latest_turn(conn, cid)
        agent.add_file(conn, conversation_id=cid, turn_id=turn['id'], direction='output', source='artifact',
            name=package['name'], size=package['size'], local_path=package['local_path'],
            sha256=hashlib.sha256((env.tmp / 'compiler.tar.gz').read_bytes()).hexdigest())
        for index in range(60):
            agent.add_event(conn, conversation_id=cid, turn_id=turn['id'], kind='command',
                payload={'command': f'command-{index}', 'output': 'x' * 90000 + f'END-{index}'})
    folder = env.tmp / 'evaluation'
    material, key = evaluation.collect(cid, folder)
    assert key and material['material_access'] and not material['collection_gaps']
    assert material['sources'] == [] and len(json.dumps(material)) < 20000
    store = MaterialStore(folder)
    inventory = store.archive.listing(query='compiler.tar.gz')['items']
    assert len(inventory) == 1 and 'origin' not in inventory[0]
    response, listing = request(store, inventory[0]['id'], action='list')
    assert response['success']
    expected = {'replay.cpp': code, 'commands.json': commands, 'device-events.json.gz': devices}
    for item in listing['items']:
        assert read_all(store, item['id']) == expected[item['name']]
    late = store.archive.listing(query='command', limit=100)['items'][-1]['id']
    assert json.loads(read_all(store, late))['output'].endswith('END-59')
    saved = evaluation.read_evidence(material, folder)
    assert not saved['collection_gaps']
    assert saved['sources'] and all(source['text'] and source['range'] for source in saved['sources'])
    source = saved['sources'][0]
    verdict = {'decision': 'pass', 'summary': 'verified', 'criteria': [
        {'id': 'C1', 'status': 'met', 'reason': 'observed', 'evidence_ids': [source['id']]}]}
    criteria = {'criteria': [{'id': 'C1', 'text': '核对源码'}]}
    assert grading.validate(json.dumps(verdict), criteria, saved)['decision'] == 'pass'
    verdict['criteria'][0]['evidence_ids'] = [inventory[0]['id']]
    with pytest.raises(ValueError, match='不存在'):
        grading.validate(json.dumps(verdict), criteria, saved)


def test_split_archive_reassembles_in_number_order_and_reads_nested_gzip(tmp_path):
    source = tmp_path / 'evidence.tar.gz'
    archive(source, [('main.cpp', b'int main() { return 0; }'),
                     ('events.json.gz', gzip.compress(b'{"observed": 42}'))])
    data = source.read_bytes()
    parts = []
    for index, start in enumerate(range(0, len(data), 25)):
        part = tmp_path / f'evidence.tar.gz.part{index:02d}'
        part.write_bytes(data[start:start + 25])
        parts.append(record(part))
    source.unlink()
    store = create(tmp_path, list(reversed(parts)))
    joined = next(item for item in file_sources(store) if item['name'] == 'evidence.tar.gz')
    response, listing = request(store, joined['id'], action='list')
    assert response['success'], listing
    assert read_all(store, listing['items'][0]['id']) == 'int main() { return 0; }'
    assert read_all(store, listing['items'][1]['id']) == '{"observed": 42}'
    for part in parts:
        (tmp_path / part['name']).unlink()
    reopened = MaterialStore(store.archive.directory)
    assert read_all(reopened, listing['items'][1]['id']) == '{"observed": 42}'


@pytest.mark.parametrize('failure', ['missing', 'truncated', 'checksum', 'changed'])
def test_invalid_split_archive_returns_read_failure_not_partial_evidence(tmp_path, failure):
    source = tmp_path / 'evidence.tar.gz'
    archive(source, [('result.log', b'confirmed result')])
    data = source.read_bytes()
    size = len(data) // 3
    parts = []
    for index, chunk in enumerate((data[:size], data[size:2 * size], data[2 * size:])):
        part = tmp_path / f'evidence.tar.gz.part{index:02d}'
        part.write_bytes(chunk)
        parts.append({**record(part), 'sha256': hashlib.sha256(chunk).hexdigest()})
    if failure == 'missing':
        parts.pop(1)
    elif failure == 'truncated':
        parts.pop()
    elif failure == 'checksum':
        parts[0]['sha256'] = '0' * 64
    store = create(tmp_path, parts)
    if failure == 'changed':
        (tmp_path / parts[0]['name']).write_bytes(b'changed')
    joined = next(item for item in file_sources(store) if item['name'] == 'evidence.tar.gz')
    response, result = request(store, joined['id'], action='list')
    assert not response['success'] and result['error']
    assert not [source for source in store.archive.validation()['sources'] if source.get('material_id')]


def test_unread_files_are_not_gaps_and_failed_reads_are_recorded(tmp_path):
    path = tmp_path / 'missing.log'
    path.write_text('original')
    store = create(tmp_path, [record(path)])
    sid = file_sources(store)[0]['id']
    path.unlink()
    assert evaluation.read_evidence({}, store.archive.directory)['collection_gaps'] == []
    response, result = request(store, sid)
    assert not response['success']
    saved = evaluation.read_evidence({}, store.archive.directory)
    assert saved['collection_gaps'] == [{'source': sid, 'reason': result['error']}]


def test_inherited_unread_split_archive_keeps_part_references(tmp_path):
    source = tmp_path / 'evidence.tar.gz'
    archive(source, [('result.log', b'original evidence')])
    data = source.read_bytes()
    parts = []
    for index, chunk in enumerate((data[:50], data[50:])):
        part = tmp_path / f'evidence.tar.gz.part{index:02d}'
        part.write_bytes(chunk)
        parts.append(record(part))
    original = create(tmp_path, parts)
    joined = next(item for item in file_sources(original) if item['name'] == 'evidence.tar.gz')
    target = LearningArchive(tmp_path / 'inherited')
    target.initialize([case['issue'] for case in original.archive.cases()])
    target.inherit(original.archive.directory, 'previous:')
    store = MaterialStore(target.directory)
    response, listing = request(store, 'previous:' + joined['id'], action='list')
    assert response['success'], listing
    assert read_all(store, listing['items'][0]['id']) == 'original evidence'


@pytest.mark.parametrize('status', ['met', 'unmet'])
def test_verdict_requires_read_evidence_for_positive_and_negative_claims(status):
    verdict = {'decision': 'pass' if status == 'met' else 'fail', 'summary': 'claim', 'criteria': [
        {'id': 'C1', 'status': status, 'reason': 'claim', 'evidence_ids': []}]}
    with pytest.raises(ValueError, match='已读取'):
        grading.validate(json.dumps(verdict), {'criteria': [{'id': 'C1'}]},
                         {'sources': [], 'status': 'completed', 'delivery_status': 'complete'})
