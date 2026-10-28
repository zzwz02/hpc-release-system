"""Built-in inputs use the real catalog, SQLite transactions and site startup."""
import base64
import copy
import io
import json
import shutil
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from app.config import settings
from app.db.jira_agent_connection import connect_jira_agent
from app.domain import jira_test_catalog as catalog, jira_test_package as package
from app.repositories import jira_testing_repo as repo
from app.services import jira_testing_builtins as builtins, jira_testing_service as service
from tests.test_jira_agent import env, _wait, _done
from tests.test_jira_testing import BASE, setup, start, forbid_jira
from tools.build_jira_test_cases import build

SOURCES = Path(__file__).resolve().parents[1] / 'app/resources/jira_test_cases'


def write_case(root, key='example', note='复现问题'):
    folder = root / key
    (folder / 'uploads').mkdir(parents=True, exist_ok=True)
    (folder / 'uploads/input.bin').write_bytes(b'\x00\xff\x80input')
    body = {'title': '示例问题', 'input': {
        'issue': {'key': 'TEST-1', 'summary': '示例问题', 'description': 'good / bad images'},
        'note': note, 'files': [{'path': 'uploads/input.bin'}]}, 'provenance': {'issue_key': 'TEST-1'}}
    (folder / 'case.json').write_text(json.dumps(body, ensure_ascii=False))
    return folder


@pytest.fixture
def storage(tmp_path, monkeypatch):
    root = tmp_path / 'cases'
    root.mkdir()
    monkeypatch.setattr(settings, 'jira_testing_cases_dir', root)
    monkeypatch.setattr(settings, 'jira_agent_database_url', f'sqlite:///{tmp_path / "cases.db"}')
    return root


def rows():
    with connect_jira_agent(settings.jira_agent_database_url) as conn:
        return repo.cases(conn)


def test_packaged_repository_cases_contain_only_three_inputs(tmp_path):
    root = tmp_path / 'catalog'
    root.mkdir()
    # Start with source directories only; no previously generated packages.
    for directory in SOURCES.iterdir():
        if not directory.name.startswith('_'):
            shutil.copytree(directory, root / directory.name)
    original = catalog.load(root)
    assert len(original) == 3
    assert {entry['body']['input']['issue']['key'] for entry in original} == {'MC3-7814', 'MC3-7818', 'MC3-7798'}
    assert build(root, root / '_packages') == 3
    assert catalog.load(root) == original
    for archive in (root / '_packages').glob('*.zip'):
        with zipfile.ZipFile(archive) as files:
            assert set(files.namelist()) <= {'manifest.json', 'case.json', 'review.json', 'attachments/image-2026-09-17-10-23-10-021.png'}
            body = json.loads(files.read('case.json'))
            assert set(body) == {'title', 'input', 'provenance'}
            assert set(body['provenance']) == {'issue_key'}
        restored = package.decode(archive.read_bytes())
        assert len(restored['criteria'].splitlines()) == 3
        assert restored['reference'] == ''
    # A packaged deployment needs neither source directories nor the old site.
    for directory in root.iterdir():
        if directory.name != '_packages':
            shutil.rmtree(directory)
    assert catalog.load(root) == original


def test_missing_optional_catalog_builds_empty_packages(tmp_path):
    root = tmp_path / 'no-bundled-cases'
    assert catalog.load(root) == []
    assert build(root, root / '_packages') == 0
    assert catalog.load(root) == []


def test_restart_upgrade_and_local_edits_preserve_inputs(storage):
    folder = write_case(storage)
    assert builtins.initialize() == ''
    case = service.detail(rows()[0]['id'])
    assert len(case['versions']) == 1 and case['batches'] == []
    assert case['builtin']['key'] == 'example'
    assert builtins.initialize() == ''
    assert service.detail(case['id']) == case
    write_case(storage, note='更新后的要求')
    assert builtins.initialize() == ''
    updated = service.detail(case['id'])
    assert len(updated['versions']) == 2
    assert updated['versions'][0]['input']['note'] == '更新后的要求'
    assert updated['versions'][1] == case['versions'][0]
    edited = copy.deepcopy(updated['versions'][0])
    edited['input']['note'] = '网站修改，不能覆盖'
    service.save({'username': 'rm'}, edited, case['id'])
    write_case(storage, note='仓库又更新了')
    assert builtins.initialize() == ''
    conflict = service.detail(case['id'])
    assert len(conflict['versions']) == 3
    assert conflict['versions'][0]['input']['note'] == '网站修改，不能覆盖'
    assert conflict['builtin']['update_available']
    assert rows()[0]['builtin_update']
    assert builtins.initialize() == ''
    assert service.detail(case['id']) == conflict
    # Retirement neither deletes data nor leaves a stale update prompt.
    shutil.rmtree(folder)
    assert builtins.initialize() == ''
    retired = service.detail(case['id'])
    assert retired['versions'] == conflict['versions']
    assert not retired['builtin']['update_available']


def test_concurrent_load_and_invalid_catalog_are_atomic(storage):
    write_case(storage)
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert list(pool.map(lambda _: builtins.initialize(), range(2))) == ['', '']
    case = service.detail(rows()[0]['id'])
    assert len(rows()) == len(case['versions']) == 1
    write_case(storage, note='不能部分应用的更新')
    bad = write_case(storage, key='broken', note='bad')
    (bad / 'uploads/input.bin').unlink()
    assert builtins.initialize()
    assert service.detail(case['id']) == case
    shutil.rmtree(bad)
    assert builtins.initialize() == ''
    assert len(service.detail(case['id'])['versions']) == 2


@pytest.mark.parametrize('criteria,reference', [
    ('网站通过标准', '网站人工参考'), ('网站通过标准', ''), ('', '网站人工参考'),
])
def test_legacy_bindings_update_inputs_without_replacing_saved_review(storage, criteria, reference):
    folder = write_case(storage)
    assert builtins.initialize() == ''
    case = service.detail(rows()[0]['id'])
    edited = copy.deepcopy(case['versions'][0])
    edited.update(criteria=criteria, reference=reference)
    service.save({'username': 'rm'}, edited, case['id'])
    before = service.detail(case['id'])

    write_case(storage, note='新版任务要求')
    (folder / 'review.json').write_text(json.dumps({'criteria': '内置通过标准', 'reference': '内置参考'}))
    assert builtins.initialize() == ''
    updated = service.detail(case['id'])
    assert updated['versions'][0]['input']['note'] == '新版任务要求'
    assert updated['versions'][0]['criteria'] == (criteria or '内置通过标准')
    assert updated['versions'][0]['reference'] == (reference or '内置参考')
    assert updated['versions'][1:] == before['versions']
    assert builtins.initialize() == ''
    assert service.detail(case['id']) == updated

    # A later release must not treat retained website review data as its own.
    (folder / 'review.json').write_text(json.dumps({'criteria': '后续内置标准', 'reference': '后续内置参考'}))
    assert builtins.initialize() == ''
    assert service.detail(case['id'])['versions'] == updated['versions']


@pytest.mark.parametrize('failure', ['traversal', 'symlink', 'extra', 'review', 'duplicate'])
def test_invalid_sources_fail_build(storage, failure):
    folder = write_case(storage)
    body = json.loads((folder / 'case.json').read_text())
    if failure == 'traversal':
        body['input']['files'][0]['path'] = '../../outside'
    elif failure == 'symlink':
        (folder / 'uploads/input.bin').unlink()
        (folder / 'uploads/input.bin').symlink_to('/etc/hostname')
    elif failure == 'extra':
        (folder / 'REPORT.md').write_text('not input')
    elif failure == 'review':
        body['reference'] = 'previous answer'
    elif failure == 'duplicate':
        write_case(storage, key='duplicate')
    (folder / 'case.json').write_text(json.dumps(body))
    with pytest.raises(ValueError):
        build(storage, storage / '_packages')
    assert not (storage / '_packages/index.json').exists()


def test_corrupt_package_never_falls_back_to_sources(storage):
    write_case(storage)
    build(storage, storage / '_packages')
    (storage / '_packages/example.zip').write_bytes(b'corrupt')
    assert builtins.initialize()
    assert rows() == []


def test_site_startup_loads_inputs_without_tasks_and_keeps_rm_permissions(env, monkeypatch):
    root = env.tmp / 'builtins'
    write_case(root)
    build(root, root / '_packages')
    calls = forbid_jira(monkeypatch)
    env.stop_site()
    monkeypatch.setattr(settings, 'jira_testing_cases_dir', root)
    env.start_site()
    env.as_user('carol')
    response = env.client.get(BASE + '/cases')
    assert response.status_code == 200
    assert response.json()['builtin_warning'] == ''
    cases = response.json()['cases']
    assert len(cases) == 1 and cases[0]['builtin_key'] == 'example'
    detail = env.client.get(BASE + '/cases/' + cases[0]['id']).json()
    assert detail['batches'] == [] and len(detail['versions']) == 1
    restored = package.decode(env.client.get(BASE + f'/cases/{detail["id"]}/versions/{detail["versions"][0]["id"]}/package').content)
    assert base64.b64decode(restored['input']['files'][0]['content_base64']) == b'\x00\xff\x80input'
    assert not calls and env.fake.turns == 0
    env.as_user('alice')
    assert env.client.get(BASE + '/cases').status_code == 403
    env.stop_site()
    (root / '_packages/example.zip').write_bytes(b'broken')
    env.start_site()
    env.as_user('carol')
    failed = env.client.get(BASE + '/cases').json()
    assert failed['builtin_warning'] and failed['cases'] == cases


def test_adoption_keeps_existing_history_and_reference(setup, monkeypatch):
    env, root, body, case = setup
    batch = start(env, case)
    cid = batch['runs'][0]['conversation_id']
    _wait(env, cid, _done)
    before = service.detail(case['id'])
    source = copy.deepcopy(before['versions'][0])
    source['criteria'] = source['reference'] = ''
    folder = env.tmp / 'builtins/example'
    folder.mkdir(parents=True)
    with zipfile.ZipFile(io.BytesIO(package.encode(source))) as files:
        for name in files.namelist():
            if name == 'manifest.json':
                continue
            destination = folder / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(files.read(name))
    monkeypatch.setattr(settings, 'jira_testing_cases_dir', folder.parent)
    assert builtins.initialize() == ''
    adopted = service.detail(case['id'])
    assert len(rows()) == 1
    assert adopted['versions'] == before['versions']
    assert adopted['batches'] == before['batches']
    data = json.loads((folder / 'case.json').read_text())
    data['input']['note'] = 'updated built-in input'
    (folder / 'case.json').write_text(json.dumps(data))
    assert builtins.initialize() == ''
    updated = service.detail(case['id'])
    assert len(updated['versions']) == 1  # Locally supplied acceptance data is also a local edit.
    assert updated['builtin']['update_available']
    assert updated['versions'][0]['reference'] == before['versions'][0]['reference']
    assert updated['batches'] == before['batches']
    with connect_jira_agent(settings.jira_agent_database_url) as conn:
        assert repo.context(conn, cid)['input'] == before['versions'][0]['input']
