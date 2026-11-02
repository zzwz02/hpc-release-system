"""Exercise selection, downloads and answer exclusion through the real HTTP API."""
import base64
import json
import zipfile
from io import BytesIO

import pytest

from app.repositories import jira_agent_repo as repo
from app.services import jira_agent_runner
from tests.test_jira_agent import env, _handover, _wait, _done
from tests.test_jira_testing import setup, start, BASE, forbid_jira


@pytest.fixture
def source(setup):
    environment, _, _, _ = setup
    environment.jira.issue_overrides['MC3-9001'] = {'comments': [
        {'id': 'c1', 'author': 'qa', 'body': 'Run original input. SECRET_MIXED_ANSWER'},
        {'id': 'c2', 'author': 'owner', 'body': 'SECRET_COMMENT_ANSWER'},
    ]}
    conversation = _handover(environment, 'MC3-9001', post_comment=False, note='定位问题', files=[
        {'filename': 'first.txt', 'content_base64': base64.b64encode(b'FIRST_INPUT').decode()},
    ])['conversation']
    _wait(environment, conversation['id'], _done)
    response = environment.client.post('/api/jira-agent/conversations/' + conversation['id'] + '/messages', json={
        'text': '补充运行数据', 'post_comment': False, 'files': [
            {'filename': 'later.txt', 'content_base64': base64.b64encode(b'LATER_INPUT').decode()},
            {'filename': 'answer.txt', 'content_base64': base64.b64encode(b'SECRET_FILE_ANSWER').decode()},
        ],
    })
    assert response.status_code == 200, response.text
    _wait(environment, conversation['id'], lambda value: len(value['turns']) == 2 and _done(value))
    path = conversation['workspace'] + '/issue.json'
    snapshot = json.loads(environment.fake.files[path])
    snapshot['issue']['raw_fields']['comment'] = {'comments': [{'body': 'SECRET_RAW_ANSWER'}]}
    snapshot['issue']['comments'][0]['body_raw'] = 'SECRET_OLD_BODY'
    environment.fake.files[path] = json.dumps(snapshot).encode()
    return environment, conversation


def preview(environment, conversation):
    response = environment.client.get(BASE + '/import/' + conversation['id'])
    assert response.status_code == 200, response.text
    return response.json()


def test_candidates_are_metadata_only_and_include_later_uploads(source):
    environment, conversation = source
    before = len(environment.fake.params('fs/readFile'))
    value = preview(environment, conversation)
    assert [item['path'] for item in environment.fake.params('fs/readFile')[before:]] == [conversation['workspace'] + '/issue.json']
    assert {item['name'] for item in value['files']} == {'saxpy.cu', 'first.txt', 'later.txt', 'answer.txt'}
    assert next(item['turn'] for item in value['files'] if item['name'] == 'later.txt') == 2
    assert len(value['comments']) == 2
    assert value['input']['issue']['comments'] == value['input']['issue']['attachments'] == value['input']['files'] == []
    assert 'SECRET_' not in json.dumps(value['input'])
    assert 'local_path' not in json.dumps(value['files']) and 'content_base64' not in json.dumps(value['files'])


def test_curated_material_stays_clean_in_saved_exported_and_executed_input(source, monkeypatch):
    environment, conversation = source
    value = preview(environment, conversation)
    selected = [item['id'] for item in value['files'] if item['name'] in ('saxpy.cu', 'later.txt')]
    calls = forbid_jira(monkeypatch)
    response = environment.client.post(BASE + '/import/' + conversation['id'], json={
        'snapshot': value['snapshot'], 'comments': [{'id': 'c1', 'body': 'Run original input.'}], 'file_ids': selected,
    })
    assert response.status_code == 200, response.text
    draft = response.json()
    assert 'SECRET_' not in json.dumps(draft)
    assert 'reference' not in draft and 'criteria' not in draft
    assert draft['input']['issue']['comments'][0]['body_raw'] == 'Run original input.'
    assert {item['path'].split('/')[-1] for item in draft['input']['files']} == {'saxpy.cu', 'later.txt'}
    assert len(draft['input']['issue']['attachments']) == 1
    saved = environment.client.post(BASE + '/cases', json=draft).json()
    archive = environment.client.get(BASE + f'/cases/{saved["id"]}/versions/{saved["versions"][0]["id"]}/package')
    with zipfile.ZipFile(BytesIO(archive.content)) as package:
        for name in package.namelist():
            assert b'SECRET_' not in package.read(name)
        assert 'uploads/answer.txt' not in package.namelist()
    restored = environment.client.post(BASE + '/packages/preview', content=archive.content, headers={'Content-Type': 'application/zip'})
    assert restored.status_code == 200
    assert restored.json()['input'] == saved['versions'][0]['input']
    batch = start(environment, saved)
    result = _wait(environment, batch['runs'][0]['conversation_id'], _done)
    workspace = result['conversation']['workspace'] + '/'
    material = {path: raw for path, raw in environment.fake.files.items() if path.startswith(workspace)}
    assert all(b'SECRET_' not in raw for raw in material.values())
    assert material[workspace + 'uploads/later.txt'] == b'LATER_INPUT'
    assert workspace + 'uploads/answer.txt' not in material
    assert b'SECRET_MIXED_ANSWER' in environment.fake.files[conversation['workspace'] + '/issue.json']
    assert not calls


def test_missing_unselected_file_does_not_block_and_selected_failure_can_be_removed(source):
    environment, conversation = source
    value = preview(environment, conversation)
    path = conversation['workspace'] + '/attachments/saxpy.cu'
    raw = environment.fake.files.pop(path)
    preview(environment, conversation)
    url = BASE + '/import/' + conversation['id']
    empty = environment.client.post(url, json={'snapshot': value['snapshot']})
    assert empty.status_code == 200 and empty.json()['input']['files'] == []
    failed = environment.client.post(url, json={'snapshot': value['snapshot'], 'file_ids': ['jira:10']})
    assert failed.status_code == 409 and 'saxpy.cu' in failed.json()['error']
    assert environment.client.post(url, json={'snapshot': value['snapshot']}).status_code == 200
    environment.fake.files[path] = raw
    assert environment.client.post(url, json={'snapshot': value['snapshot'], 'file_ids': ['jira:10']}).status_code == 200


def test_download_does_not_select_files_and_is_limited_to_candidates(source):
    environment, conversation = source
    value = preview(environment, conversation)
    url = BASE + '/import/' + conversation['id']
    later = next(item for item in value['files'] if item['name'] == 'later.txt')
    download = environment.client.get(url + '/files/' + later['id'], params={'snapshot': value['snapshot']})
    assert download.status_code == 200 and download.content == b'LATER_INPUT'
    assert 'attachment;' in download.headers['content-disposition']
    prepared = environment.client.post(url, json={'snapshot': value['snapshot']})
    assert prepared.json()['input']['files'] == []
    with jira_agent_runner.open_db() as conn:
        output = next(item for item in repo.list_files(conn, conversation['id']) if item['direction'] == 'output')
    assert environment.client.get(url + '/files/' + output['id'], params={'snapshot': value['snapshot']}).status_code == 404
    assert environment.client.post(url, json={'snapshot': value['snapshot'], 'file_ids': [output['id']]}).status_code == 400
    assert environment.client.post(url, json={'snapshot': value['snapshot'], 'comments': [{'id': 'unknown', 'body': 'text'}]}).status_code == 400
    environment.as_user('alice')
    assert environment.client.get(url).status_code == 403
    assert environment.client.post(url, json={'snapshot': value['snapshot']}).status_code == 403
    assert environment.client.get(url + '/files/' + later['id'], params={'snapshot': value['snapshot']}).status_code == 403


def test_changed_snapshot_and_unsafe_paths_are_rejected(source):
    environment, conversation = source
    value = preview(environment, conversation)
    path = conversation['workspace'] + '/issue.json'
    snapshot = json.loads(environment.fake.files[path])
    snapshot['issue']['comments'][0]['body'] = 'Changed after selection'
    environment.fake.files[path] = json.dumps(snapshot).encode()
    response = environment.client.post(BASE + '/import/' + conversation['id'], json={'snapshot': value['snapshot']})
    assert response.status_code == 409
    snapshot['issue']['attachments'][0]['local_path'] = 'attachments/../secret'
    environment.fake.files[path] = json.dumps(snapshot).encode()
    value = preview(environment, conversation)
    before = len(environment.fake.params('fs/readFile'))
    response = environment.client.post(BASE + '/import/' + conversation['id'], json={'snapshot': value['snapshot'], 'file_ids': ['jira:10']})
    assert response.status_code == 400
    assert all('secret' not in item['path'] for item in environment.fake.params('fs/readFile')[before:])
