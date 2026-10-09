from app.domain.app_types import APP_TYPES_BY_DOC_TARGET, list_app_types
from app.repositories import snapshots_repo
from app.services import app_service, release_service
from tests.conftest import seed_app, seed_release


def test_standard_categories_are_unique_and_grouped_by_doc_target():
    assert len(APP_TYPES_BY_DOC_TARGET['manual']) == 14
    assert len(APP_TYPES_BY_DOC_TARGET['ai4sci']) == 9
    assert len(list_app_types()) == len(set(list_app_types())) == 23
    assert '第一性原理与电子结构' in APP_TYPES_BY_DOC_TARGET['manual']
    assert '材料科学与机器学习势' in APP_TYPES_BY_DOC_TARGET['ai4sci']
    assert '材料科学计算' not in list_app_types()
    assert '蛋白质/生物分子模型' not in list_app_types()


def test_state_app_types_exclude_history_and_custom_values_without_reclassifying_snapshots(temp_db, tmp_dir):
    first = seed_release(temp_db, tmp_path=tmp_dir)
    app_id = temp_db.execute('SELECT id FROM apps LIMIT 1').fetchone()['id']
    second = release_service.create_release(
        temp_db, name='next', maca_version='next', app_freeze_deadline='',
        doc_deadline='', user='rm', role='RM',
    )['release_id']
    snap = snapshots_repo.get_snapshot(temp_db, second, app_id)
    snap['type'] = '科学计算'
    snapshots_repo.save_snapshot(temp_db, second, app_id, snap)
    for i, app_type in enumerate(['科学计算', '', ' \t ', None]):
        extra_id = seed_app(temp_db, second, git_url=f'repo/extra-{i}')
        extra = snapshots_repo.get_snapshot(temp_db, second, extra_id)
        extra['type'] = app_type
        snapshots_repo.save_snapshot(temp_db, second, extra_id, extra)
    temp_db.commit()

    for release_id, current_type in [(first, '分子动力学'), (second, '科学计算')]:
        state = app_service.get_state(
            temp_db, user={'username': 'rm', 'role': 'RM'}, release_id_param=release_id,
        )
        assert state['app_types'] == list_app_types()
        assert len(state['app_types']) == 23
        assert '分子动力学' not in state['app_types']
        assert '科学计算' not in state['app_types']
        assert state['release']['snapshots'][app_id]['type'] == current_type
        assert snapshots_repo.get_snapshot(temp_db, release_id, app_id)['type'] == current_type


def test_empty_state_has_all_standard_app_types(temp_db):
    state = app_service.get_state(temp_db, user={'username': 'rm', 'role': 'RM'})
    assert len(state['app_types']) == 23
    assert state['app_types'] == list_app_types()
