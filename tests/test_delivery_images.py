"""Build-specific deliveries survive publication, history and corrections."""
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
import threading
import sqlite3

import pytest

from app.domain import delivery_images
from app.repositories import entries_repo, official_repo
from app.services import catalog_service, cycle_service, lookup_service, qa_service
from tests.conftest import close_qa, make_app_info, make_dependency_app_info, seed_app, seed_cycle, seed_signup, store_app_info


@pytest.fixture()
def delivery(temp_db):
    raw = make_app_info()
    raw['app_build'] = {
        'ubuntu22.04_amd64': {'os': 'ubuntu22.04', 'arch': 'amd64', 'build_target': 'release,dbg', 'python_label': 'py312'},
        'kylin2309a_arm64': {'os': 'kylin2309a', 'arch': 'arm64', 'build_target': 'release'},
        'disabled_amd64': {'os': 'ubuntu24.04', 'arch': 'amd64', 'enabled': False},
    }
    app = seed_app(temp_db, app_info=raw)
    release = seed_cycle(temp_db)
    entry = seed_signup(temp_db, release, app)
    values = [{**row, 'reference': f'harbor.sh.mxcr.io/hpc-release/testapp-maca@sha256:{digit * 64}'}
              for row, digit in zip(sorted([r for r in entry['delivery_builds'] if r['image_target'] == 'release'], key=lambda r: r['arch']), 'ab')]
    return app, release, entry, values


def save(conn, delivery, *, values=None, revision=None):
    app, release, entry, images = delivery
    return qa_service.set_qa_status_batch(conn, release, [{
        'app_id': app, 'status': 'qa_passed', 'deliverable_images': images if values is None else values,
        'delivery_revision': entry['delivery_revision'] if revision is None else revision,
    }], user='qa', role='QA')


def test_multiple_build_images_are_published_and_every_digest_is_searchable(temp_db, delivery):
    app, release, entry, images = delivery
    assert len(entry['delivery_builds']) == 3
    images[0]['reference'] = 'docker pull ' + images[0]['reference']
    save(temp_db, delivery)
    current = cycle_service.entry_view(temp_db, entries_repo.get_entry(temp_db, entry['id']))
    assert [row['arch'] for row in current['deliverable_images']] == ['amd64', 'arm64']
    assert current['deliverable_images'][0]['reference'].startswith('harbor.sh.mxcr.io/')
    assert len(lookup_service.by_digest(temp_db, 'b' * 64)['entries']) == 1
    close_qa(temp_db, release)
    cycle_service.publish(temp_db, release, user='rm', role='RM')
    official = official_repo.current_by_app(temp_db)[app]
    assert len(official['images']) == 2
    assert len(official_repo.by_digest(temp_db, 'sha256:' + 'b' * 64)) == 1
    assert catalog_service.app_detail(temp_db, app)['official_versions'][0]['images'] == current['deliverable_images']
    assert lookup_service.published_apps(temp_db, release)['apps'][0]['images'] == current['deliverable_images']
    cycle_service.unlock(temp_db, release, reason='更正交付镜像', user='rm', role='RM')
    fresh = cycle_service.entry_view(temp_db, entries_repo.get_entry(temp_db, entry['id']))
    changed = deepcopy(fresh['deliverable_images'])
    changed[1]['reference'] = changed[1]['reference'].replace('b' * 64, 'c' * 64)
    save(temp_db, delivery, values=changed, revision=fresh['delivery_revision'])
    cycle_service.publish(temp_db, release, user='rm', role='RM')
    history = catalog_service.app_detail(temp_db, app)['official_versions']
    assert [row['withdrawn'] for row in history] == [0, 1]
    assert history[0]['images'][1]['digest'] == 'sha256:' + 'c' * 64
    assert history[1]['images'][1]['digest'] == 'sha256:' + 'b' * 64
    with pytest.raises(RuntimeError, match='已发布'):
        save(temp_db, delivery)


def test_stale_qa_edit_cannot_overwrite_images_or_scope_version(temp_db, delivery):
    save(temp_db, delivery)
    before = entries_repo.get_entry(temp_db, delivery[2]['id'])
    with pytest.raises(ValueError, match='已变化'):
        save(temp_db, delivery, values=[])
    assert entries_repo.get_entry(temp_db, delivery[2]['id']) == before


@pytest.mark.parametrize('managed', [True, False])
def test_concurrent_qa_saves_report_one_conflict_without_losing_an_update(temp_db, delivery, managed):
    from app.db.connection import connect
    path = temp_db.execute('PRAGMA database_list').fetchone()['file']
    barrier = threading.Barrier(2)

    def writer(digit):
        conn = connect(path) if managed else sqlite3.connect(path, timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            values = deepcopy(delivery[3])
            values[0]['reference'] = values[0]['reference'].replace('a' * 64, digit * 64)
            barrier.wait(timeout=5)
            try:
                save(conn, delivery, values=values)
                return 'saved'
            except ValueError as exc:
                assert '已变化' in str(exc)
                return 'conflict'
        finally:
            conn.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(writer, ['d', 'e'])) == ['conflict', 'saved']
    stored = entries_repo.get_entry(temp_db, delivery[2]['id'])['deliverable_images']
    assert len(stored) == 2
    assert stored[0]['digest'] in ['sha256:' + digit * 64 for digit in ['d', 'e']]


@pytest.mark.parametrize('bad', ['unknown_build', 'no_digest', 'duplicate', 'wrong_image'])
def test_invalid_build_or_reference_leaves_the_batch_unchanged(temp_db, delivery, bad):
    values = deepcopy(delivery[3])
    if bad == 'unknown_build':
        values[0]['build_key'] = 'disabled_amd64'
    elif bad == 'no_digest':
        values[0]['reference'] = 'harbor.sh.mxcr.io/hpc-release/testapp-maca:1.0'
    elif bad == 'duplicate':
        values.append(values[0])
    else:
        values[0]['reference'] = values[0]['reference'].replace('testapp-maca', 'other-maca')
    with pytest.raises(ValueError):
        save(temp_db, delivery, values=values)
    assert entries_repo.get_entry(temp_db, delivery[2]['id'])['qa_status'] == 'not_checked'
    assert entries_repo.get_entry(temp_db, delivery[2]['id'])['deliverable_images'] == []


def test_scalar_clients_cannot_drop_multiple_images_and_old_records_are_retained(temp_db, delivery):
    app, release, entry, _ = delivery
    temp_db.execute('UPDATE entries SET deliverable_image=?,deliverable_digest=? WHERE id=?',
                    ('harbor.sh.mxcr.io/hpc-release/testapp-maca', 'sha256:' + 'd' * 64, entry['id']))
    temp_db.commit()
    old = cycle_service.entry_view(temp_db, entries_repo.get_entry(temp_db, entry['id']))
    assert old['deliverable_images'][0]['build_key'] == 'legacy'
    assert old['deliverable_images'][0]['os'] == ''
    save(temp_db, delivery, values=[*old['deliverable_images'], *delivery[3]], revision=old['delivery_revision'])
    with pytest.raises(ValueError, match='多镜像'):
        qa_service.set_qa_status_batch(temp_db, release, [{'app_id': app, 'status': 'qa_passed',
            'deliverable_digest': 'sha256:' + 'f' * 64}], user='qa', role='QA')
    assert len(entries_repo.get_entry(temp_db, entry['id'])['deliverable_images']) == 3


def test_build_variants_expand_frameworks_and_do_not_include_disabled_builds():
    raw = make_app_info()
    cfg = raw['app_build']['ubuntu20.04_amd64']
    cfg.update(python_label='py310,py312', pytorch_label='torch2.4,torch2.8', build_target='release,dbg')
    rows = delivery_images.builds(raw)
    assert len(rows) == len({row['build_key'] for row in rows}) == 8


def test_published_commands_match_exact_delivery_variants_and_leave_missing_images_as_templates():
    raw = make_dependency_app_info()
    values = [
        {**spec, 'reference': f'harbor.test/hpc-release/{spec["image_pattern"].partition(":")[0]}@sha256:{digit * 64}'}
        for spec, digit in zip((spec for spec in delivery_images.builds(raw) if spec['os'] != 'ubuntu24.04'), 'abcde')
    ]
    images = delivery_images.normalize(values, raw, {})
    commands = delivery_images.published_commands(raw, images)
    assert len(images) == len(commands) == 5
    for command in commands:
        if command['os'] == 'ubuntu24.04':
            assert command['delivery_reference'] == command['delivery_build_key'] == ''
            assert '<hpc_version>' in command['command']
            assert '@sha256:' not in command['command']
        else:
            image = next(image for image in images if image['build_key'] == command['delivery_build_key'])
            assert all(command[field] == image[field] for field in
                       ['build', 'os', 'arch', 'python_label', 'pytorch_label', 'image_target'])
            assert command['image'] == command['delivery_reference'] == image['reference']
            assert image['reference'] in command['command']
        assert '-v /pde_hpc/dataset:/hpc_dataset:ro' in command['command']
        assert '--device=/dev/dri' in command['command']
    assert {command['image_target'] for command in commands} == {'release', 'dbg'}
    assert not any(command['os'] == 'centos9' for command in commands)
    assert delivery_images.published_commands(raw, images, {'sanity'}) == []


def test_unassigned_legacy_image_is_not_applied_to_every_build():
    raw = make_dependency_app_info()
    images = delivery_images.recorded([], 'harbor.test/hpc-release/runtime-app-maca', 'sha256:' + 'a' * 64)
    commands = delivery_images.published_commands(raw, images)
    assert len(commands) == 5
    assert all(not command['delivery_reference'] and '@sha256:' not in command['command'] for command in commands)


def test_dependency_reads_and_csv_preserve_every_frozen_image_and_platform_command(temp_db):
    import csv
    import io

    raw = make_dependency_app_info()
    raw['app_test']['excluded'] = {**raw['app_test']['sanity'], 'test_cmd': 'must-not-be-exported'}
    app = seed_app(temp_db, app_info=raw)
    release = seed_cycle(temp_db, name='3.10.0')
    entry = seed_signup(temp_db, release, app, excluded_tests=['excluded'])
    images = [
        {**spec, 'reference': f'harbor.test/hpc-release/{spec["image_pattern"].partition(":")[0]}@sha256:{digit * 64}'}
        for spec, digit in zip((spec for spec in entry['delivery_builds'] if spec['os'] != 'ubuntu24.04'), 'abcde')
    ]
    qa_service.set_qa_status_batch(temp_db, release, [{
        'app_id': app, 'status': 'qa_passed', 'deliverable_images': images, 'delivery_revision': entry['delivery_revision'],
    }], user='qa', role='QA')
    close_qa(temp_db, release)
    cycle_service.publish(temp_db, release, user='rm', role='RM')
    # Dependency membership follows latest app_info, while released commands stay frozen.
    changed = deepcopy(raw)
    changed['app_test']['sanity']['test_cmd'] = 'new-unpublished-command'
    store_app_info(temp_db, app, changed, commit_id='unpublished')
    row = catalog_service.compat_list(temp_db, 'sdk')[0]
    assert row['official']['release_name'] == '3.10.0'
    assert len(row['official']['images']) == len(row['tests']) == 5
    assert not any('must-not-be-exported' in test['command'] or 'new-unpublished-command' in test['command'] for test in row['tests'])

    exported = list(csv.DictReader(io.StringIO(catalog_service.compat_list_csv(temp_db, 'sdk'))))
    assert len(exported) == 6  # five commands plus the CentOS image with no selected tests
    assert {item['最新hpc release版本'] for item in exported} == {'3.10.0'}
    assert '版本' not in exported[0]
    assert {item['CPU 架构'] for item in exported} == {'x86', 'ARM'}
    assert {item['镜像类型'] for item in exported} == {'release', 'dbg'}
    centos = next(item for item in exported if item['OS'] == 'centos9')
    assert centos['测试命令'] == '' and centos['镜像来源'] == '交付镜像'
    assert '@sha256:' in centos['镜像']
    missing = next(item for item in exported if item['OS'] == 'ubuntu24.04')
    assert missing['镜像来源'] == '镜像模板（未填写交付镜像）' and missing['Digest'] == ''
    assert '<hpc_version>' in missing['测试命令']
    for item in exported:
        if item['镜像来源'] == '交付镜像' and item['测试命令']:
            assert item['镜像'] in item['测试命令']
