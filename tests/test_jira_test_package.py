"""Archive portability through the real API, database and test runner."""
import base64
import copy
import io
import json
import stat
import zipfile
from concurrent.futures import ThreadPoolExecutor

import pytest

from app.config import settings
from app.db.connection import transaction
from app.domain import jira_test_package as package, jira_testing as domain
from app.repositories import jira_agent_repo as agent
from app.services import jira_agent_runner
from tests.test_jira_agent import env, _wait, _done, SYSTEM_MACHINE
from tests.test_jira_testing import setup, start, forbid_jira, BASE


def export(env, case):
    return env.client.get(BASE + f'/cases/{case["id"]}/versions/{case["versions"][0]["id"]}/package')


def preview(env, raw):
    return env.client.post(BASE + '/packages/preview', content=raw, headers={'Content-Type': 'application/zip'})


def archive(entries):
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_DEFLATED) as target:
        for name, value in entries:
            target.writestr(name, value)
    return output.getvalue()


@pytest.fixture
def sample():
    inputs = domain.validate({'issue': {'key': 'TEST-1', 'summary': '图片与输入',
        'attachments': [{'id': '1', 'filename': '图.png', 'local_path': 'attachments/图.png'}]},
        'note': '复现问题', 'files': [{'path': 'attachments/图.png',
        'content_base64': base64.b64encode(b'\x89PNG\x00\xff\xfe').decode()}]})
    return {'title': '测试用例', 'input': inputs, 'input_sha256': domain.digest(inputs), 'provenance': {}}


def test_package_moves_to_fresh_database_and_runs_without_source_jira(setup, monkeypatch):
    env, root, body, case = setup
    body['input']['issue']['description'] = 'bad: registry/example:bad\ngood: registry/example:good\n起容器：docker run registry/example:bad'
    body['input']['issue']['attachments'] = [{'id': '7', 'filename': '图.png', 'local_path': 'attachments/图.png'}]
    body['input']['files'].append({'path': 'attachments/图.png', 'content_base64': base64.b64encode(b'\x00\xffPNG').decode()})
    body['provenance'] = {'issue_key': 'MC3-7814', 'endpoint': 'DO_NOT_EXPORT_SERVER', 'token': 'DO_NOT_EXPORT_TOKEN'}
    case = env.client.post(BASE + f'/cases/{case["id"]}/versions', json=body).json()
    original = copy.deepcopy(case['versions'][0])
    calls = forbid_jira(monkeypatch)
    response = export(env, case)
    assert response.status_code == 200, response.text
    assert response.headers['content-type'] == 'application/zip'
    with zipfile.ZipFile(io.BytesIO(response.content)) as files:
        assert set(files.namelist()) == {'manifest.json', 'case.json', 'review.json', 'uploads/input.txt', 'attachments/图.png'}
        assert files.read('attachments/图.png') == b'\x00\xffPNG'
        assert b'SECRET_REFERENCE_RESULT' not in files.read('case.json')
        assert b'DO_NOT_EXPORT' not in files.read('case.json')
    env.stop_site()
    monkeypatch.setattr(settings, 'jira_agent_database_url', f'sqlite:///{env.tmp / "destination.db"}')
    env.start_site()
    with jira_agent_runner.open_db() as conn, transaction(conn):
        agent.create_machine(conn, agent_group='HPC', ssh_target=SYSTEM_MACHINE, description='test', created_by='carol')
    draft = preview(env, response.content)
    assert draft.status_code == 200, draft.text
    assert env.client.get(BASE + '/cases').json()['cases'] == []
    assert draft.json()['input'] == original['input']
    assert not draft.json()['duplicates']
    saved = env.client.post(BASE + '/cases', json=draft.json())
    assert saved.status_code == 200, saved.text
    restored = saved.json()
    assert restored['id'] != case['id']
    assert restored['versions'][0]['input_sha256'] == original['input_sha256']
    assert restored['versions'][0]['reference'] == original['reference']
    assert restored['batches'] == [] and env.fake.turns == 0
    run = start(env, restored)
    result = _wait(env, run['runs'][0]['conversation_id'], _done)
    assert result['turns'][0]['status'] == 'completed'
    assert env.fake.files[result['conversation']['workspace'] + '/attachments/图.png'] == b'\x00\xffPNG'
    assert 'SECRET_REFERENCE_RESULT' not in str(env.fake.files) + str(env.fake.requests)
    assert not calls


def test_duplicates_names_and_concurrent_confirmation_never_overwrite(setup):
    env, root, body, case = setup
    raw = export(env, case).content
    draft = preview(env, raw).json()
    assert [(item['id'], item['review_differs']) for item in draft['duplicates']] == [(case['id'], False)]
    saved = env.client.post(BASE + '/cases', json=draft).json()
    assert saved['existing'] and saved['id'] == case['id'] and len(saved['versions']) == 1
    assert env.client.post(BASE + f'/cases/{case["id"]}/versions', json=draft).status_code == 409
    draft['input']['note'] = '另一组测试要求'
    assert env.client.post(BASE + '/cases', json=draft).status_code == 409
    draft['title'] = '另一个用例'
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: env.client.post(BASE + '/cases', json=draft), range(2)))
    assert [r.status_code for r in responses] == [200, 200]
    assert len({r.json()['id'] for r in responses}) == 1
    assert sorted(r.json()['existing'] for r in responses) == [False, True]
    unchanged = env.client.get(BASE + f'/cases/{case["id"]}').json()
    assert unchanged == {key: value for key, value in case.items() if key != 'existing'}


def test_export_is_pinned_and_requires_rm(setup):
    env, root, body, case = setup
    original = export(env, case).content
    body['input']['note'] = 'new input'
    env.client.post(BASE + f'/cases/{case["id"]}/versions', json=body)
    assert package.decode(export(env, case).content)['input'] == package.decode(original)['input']
    for role in ('alice', 'dave', 'erin'):
        env.as_user(role)
        assert export(env, case).status_code == 403
        assert preview(env, original).status_code == 403


@pytest.mark.parametrize('failure', ['missing', 'changed', 'extra', 'duplicate', 'traversal', 'absolute', 'symlink', 'version', 'json', 'truncated'])
def test_invalid_packages_are_rejected(sample, failure):
    raw = package.encode(sample)
    if failure == 'truncated':
        with pytest.raises(ValueError): package.decode(raw[:50])
        return
    with zipfile.ZipFile(io.BytesIO(raw)) as source:
        entries = {name: source.read(name) for name in source.namelist()}
    if failure == 'missing': del entries['attachments/图.png']
    if failure == 'changed': entries['attachments/图.png'] = b'changed'
    if failure == 'extra': entries['uploads/extra.txt'] = b'not registered'
    if failure == 'traversal': entries['uploads/../../escape'] = b'x'
    if failure == 'absolute': entries['/tmp/escape'] = b'x'
    if failure == 'version':
        manifest = json.loads(entries['manifest.json'])
        manifest['version'] = 999
        entries['manifest.json'] = json.dumps(manifest).encode()
    if failure == 'json': entries['manifest.json'] = b'not json'
    rows = list(entries.items())
    if failure == 'duplicate': rows.append(('case.json', entries['case.json']))
    if failure == 'symlink':
        link = zipfile.ZipInfo('uploads/link')
        link.create_system = 3
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        rows.append((link, b'/etc/passwd'))
    with pytest.raises(ValueError): package.decode(archive(rows))


def test_size_limits_and_failed_preview_leave_no_records(setup, monkeypatch):
    env, root, body, case = setup
    before = env.client.get(BASE + '/cases').json()['cases']
    assert preview(env, b'not zip').status_code == 400
    monkeypatch.setattr(package, 'MAX_BYTES', 1024)
    assert preview(env, b'x' * 1025).status_code == 413
    assert preview(env, archive([('case.json', b'0' * 2048)])).status_code == 400
    assert env.client.get(BASE + '/cases').json()['cases'] == before
    assert env.fake.turns == 0


def test_missing_saved_attachment_is_not_exported(setup):
    env, root, body, case = setup
    with jira_agent_runner.open_db() as conn, transaction(conn):
        conn.execute('UPDATE jira_test_versions SET input_json=? WHERE id=?',
                     (json.dumps({'issue': {'key': 'TEST-1', 'summary': 'broken'}}), case['versions'][0]['id']))
    assert export(env, case).status_code == 400


def test_input_hash_and_metadata_survive_roundtrip(sample):
    original = copy.deepcopy(sample)
    decoded = package.decode(package.encode(sample))
    assert decoded['input'] == sample['input']
    assert sample == original
    assert not decoded['reference'] and not decoded['criteria']
