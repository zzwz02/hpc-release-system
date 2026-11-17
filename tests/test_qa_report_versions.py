"""One validated workbook replaces the complete QA log for one HPC cycle."""
import base64
import io
import sqlite3

import pytest
from openpyxl import Workbook

from app.api.errors import ApiError
from app.db.connection import init_db
from app.domain import qa_results
from app.integrations import harbor
from app.repositories import qa_results_repo
from app.services import lookup_service, qa_commands_service, qa_results_service
from tests.conftest import seed_cycle
from tests.test_image_lookup import IMAGE, TAGS
from tests.test_qa_commands import filled_row, missing_image, scope as scope
from tests.test_qa_results import live as live


def workbook(rows):
    book = Workbook()
    sheet = book.active
    sheet.append(qa_results.WORKSHEET_COLUMNS)
    for row in rows:
        sheet.append([row.get(column, '') for column in qa_results.WORKSHEET_COLUMNS])
    env = book.create_sheet('C500_server_1')
    env['A1'] = '环境原件'
    env['B3'] = '=1+2'
    output = io.BytesIO()
    book.save(output)
    return output.getvalue()


def save(conn, release_id, rows, **kwargs):
    return qa_results_service.upload(conn, release_id=release_id, filename='report.xlsx', user='qa', role='QA',
        content_b64=base64.b64encode(workbook(rows)).decode(), **kwargs)


@pytest.fixture()
def cycles(temp_db, scope, monkeypatch):
    base = filled_row(temp_db, scope)
    rows = qa_commands_service.query(temp_db, release_id=scope[0], hpc_version='3.10.1.2')['rows']
    other = next(row for row in rows if not missing_image(row) and row['test_name'] != base['test_name'])
    other = {**base, **other, 'qa_digest': IMAGE, 'qa_env': 'C500_server_1', 'qa_status': 'pass',
             'qa_result': 'B', 'qa_docker_cmd': other['website_docker_cmd']}
    versions = ['3.10.1.2', '3.10.0.7', '3.10.0.8', '3.9.0.20']
    monkeypatch.setattr(harbor, 'get_artifact', lambda *args: {'tags': [
        {'name': tag.replace('3.10.1.2', version)} for tag in TAGS for version in versions]})
    def at(row, version):
        return {**row, 'hpc_version': version,
                'qa_docker_cmd': row['qa_docker_cmd'].replace('3.10.1.2', version)}
    return {'current': seed_cycle(temp_db, name='3.10.0'), 'other': seed_cycle(temp_db, name='3.9.0'),
            'A': at(base, '3.10.0.7'), 'B': at(other, '3.10.0.7'),
            'patch': at(base, '3.10.0.8'), 'old': at(base, '3.9.0.20')}


def test_case_b_replaces_case_a_and_all_patches_but_preserves_another_cycle(temp_db, cycles):
    other = save(temp_db, cycles['other'], [cycles['old']])
    old_content = qa_results_service.download(temp_db, other['report_id'])[0]
    first = save(temp_db, cycles['current'], [cycles['A'], cycles['patch']])
    second = save(temp_db, cycles['current'], [cycles['B']])
    result = qa_results_service.query(temp_db, release_id=cycles['current'])
    assert result['total'] == 1 and result['rows'][0]['test_name'] == cycles['B']['test_name']
    assert result['rows'][0]['hpc_version'] == '3.10.0.7'
    assert second['replaced'] == 2
    assert temp_db.execute('SELECT count(*) FROM qa_test_reports WHERE release_id=?', (cycles['current'],)).fetchone()[0] == 1
    assert qa_results_service.query(temp_db, release_id=cycles['other'])['total'] == 1
    assert qa_results_service.download(temp_db, other['report_id'])[0] == old_content
    with pytest.raises(ApiError, match='测试报告不存在'):
        qa_results_service.download(temp_db, first['report_id'])


@pytest.mark.parametrize('change', [{'GPU': 'unsupported'}, {'hpc_version': '3.9.0.20'}])
def test_invalid_replacement_leaves_old_rows_and_workbook_intact(temp_db, cycles, change):
    saved = save(temp_db, cycles['current'], [cycles['A']])
    old_content = qa_results_service.download(temp_db, saved['report_id'])[0]
    before = qa_results_service.query(temp_db, release_id=cycles['current'])
    with pytest.raises(ValueError):
        save(temp_db, cycles['current'], [{**cycles['B'], **change}])
    assert qa_results_service.query(temp_db, release_id=cycles['current']) == before
    assert qa_results_service.download(temp_db, saved['report_id'])[0] == old_content


def test_database_failure_after_old_report_deletion_rolls_the_entire_replacement_back(temp_db, cycles):
    first = save(temp_db, cycles['current'], [cycles['A']])
    old_content = qa_results_service.download(temp_db, first['report_id'])[0]
    temp_db.execute("CREATE TRIGGER block_new_qa BEFORE INSERT ON qa_test_results BEGIN SELECT RAISE(ABORT,'blocked'); END")
    temp_db.commit()
    with pytest.raises(sqlite3.IntegrityError, match='blocked'):
        save(temp_db, cycles['current'], [cycles['B']])
    assert qa_results_service.query(temp_db, release_id=cycles['current'])['rows'][0]['test_name'] == cycles['A']['test_name']
    assert qa_results_service.download(temp_db, first['report_id'])[0] == old_content
    assert temp_db.execute('SELECT count(*) FROM qa_test_reports').fetchone()[0] == 1


def test_concurrent_upload_cannot_overwrite_a_report_committed_during_source_validation(temp_db, cycles, monkeypatch):
    save(temp_db, cycles['current'], [cycles['A']])
    original = lookup_service.resolve_images
    state = {'interleaved': False}
    concurrent = {**cycles['A'], 'qa_result': 'concurrent'}
    def resolve(conn, references):
        if not state['interleaved']:
            state['interleaved'] = True
            assert not conn.in_transaction
            save(conn, cycles['current'], [concurrent])
        return original(conn, references)
    monkeypatch.setattr(lookup_service, 'resolve_images', resolve)
    with pytest.raises(ApiError, match='已变化') as error:
        save(temp_db, cycles['current'], [cycles['B']])
    assert error.value.status_code == 409
    report = qa_results_service.query(temp_db, release_id=cycles['current'])
    assert report['total'] == 1 and report['rows'][0]['qa_result'] == 'concurrent'


def test_stale_displayed_revision_is_rejected_before_network_calls(temp_db, cycles, monkeypatch):
    save(temp_db, cycles['current'], [cycles['A']])
    revision = qa_results_service.query(temp_db, release_id=cycles['current'])['revision']
    save(temp_db, cycles['current'], [cycles['B']])
    monkeypatch.setattr(lookup_service, 'resolve_images', lambda *args: pytest.fail('Stale revision should fail before source reads'))
    with pytest.raises(ApiError, match='已变化'):
        save(temp_db, cycles['current'], [cycles['A']], revision=revision)


def test_legacy_merged_rows_and_superseded_file_are_removed_only_for_selected_cycle(temp_db, cycles):
    ids = []
    for rows in ([cycles['A']], [], [cycles['old']]):
        content_rows = rows or [cycles['B']]
        ids.append(qa_results_repo.save(temp_db, filename='legacy.xlsx', content=workbook(content_rows),
            environments=['C500_server_1'], uploaded_at='before', uploaded_by='qa',
            rows=[(qa_results.worksheet_result_key(row), row, IMAGE) for row in rows]))
    temp_db.commit()
    old_content = qa_results_service.download(temp_db, ids[2])[0]
    save(temp_db, cycles['current'], [cycles['B']])
    assert qa_results_service.query(temp_db, release_id=cycles['current'])['total'] == 1
    assert qa_results_service.query(temp_db, release_id=cycles['other'])['total'] == 1
    assert temp_db.execute('SELECT count(*) FROM qa_test_reports').fetchone()[0] == 2
    for report_id in ids[:2]:
        with pytest.raises(ApiError, match='测试报告不存在'):
            qa_results_service.download(temp_db, report_id)
    assert qa_results_service.download(temp_db, ids[2])[0] == old_content


def test_schema_migration_preserves_legacy_records_until_a_new_upload(temp_db):
    temp_db.executescript('''
        DROP TABLE qa_test_results;
        DROP TABLE qa_test_reports;
        CREATE TABLE qa_test_reports(id INTEGER PRIMARY KEY AUTOINCREMENT, filename TEXT NOT NULL,
          content BLOB NOT NULL, environments_json TEXT NOT NULL, uploaded_at TEXT NOT NULL, uploaded_by TEXT NOT NULL);
        CREATE TABLE qa_test_results(result_key TEXT PRIMARY KEY, report_id INTEGER NOT NULL REFERENCES qa_test_reports(id),
          data_json TEXT NOT NULL, image_reference TEXT NOT NULL DEFAULT '');
        INSERT INTO qa_test_reports VALUES(1,'legacy.xlsx',X'010203','[]','before','qa');
        INSERT INTO qa_test_results VALUES('key',1,'{"hpc_version":"3.10.0.7"}','');
    ''')
    init_db(temp_db)
    assert temp_db.execute('SELECT content,release_id FROM qa_test_reports').fetchone()[:] == (b'\x01\x02\x03', None)
    assert temp_db.execute('SELECT count(*) FROM qa_test_results').fetchone()[0] == 1


def test_cycle_removal_cascades_to_its_one_report_and_results(temp_db, cycles):
    save(temp_db, cycles['current'], [cycles['A']])
    temp_db.execute('DELETE FROM releases WHERE id=?', (cycles['current'],))
    temp_db.commit()
    assert temp_db.execute('SELECT count(*) FROM qa_test_reports').fetchone()[0] == 0
    assert temp_db.execute('SELECT count(*) FROM qa_test_results').fetchone()[0] == 0
