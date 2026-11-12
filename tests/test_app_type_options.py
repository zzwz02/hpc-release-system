from app.domain.app_types import APP_TYPES_BY_DOC_TARGET, list_app_types
from app.services import catalog_service, state_service
from tests.conftest import seed_app


def test_standard_categories_are_unique_and_grouped_by_doc_target():
    assert len(APP_TYPES_BY_DOC_TARGET['manual']) == 14
    assert len(APP_TYPES_BY_DOC_TARGET['ai4sci']) == 9
    assert len(list_app_types()) == len(set(list_app_types())) == 23
    assert '第一性原理与电子结构' in APP_TYPES_BY_DOC_TARGET['manual']
    assert '材料科学与机器学习势' in APP_TYPES_BY_DOC_TARGET['ai4sci']
    assert '材料科学计算' not in list_app_types()
    assert '蛋白质/生物分子模型' not in list_app_types()


def test_state_app_types_are_the_standard_list_and_ignore_stored_values(temp_db):
    """Historical / custom App types stay on the App but are never offered."""
    seed_app(temp_db, 'legacy', app_type='科学计算')
    seed_app(temp_db, 'blank', app_type='')
    seed_app(temp_db, 'standard', app_type='第一性原理与电子结构')

    state = state_service.get_state(temp_db, user={'username': 'rm', 'role': 'RM'})

    assert state['app_types'] == list_app_types()
    assert len(state['app_types']) == 23
    assert '科学计算' not in state['app_types']
    stored = {app['id']: app['type'] for app in state['apps']}
    assert stored == {'legacy': '科学计算', 'blank': '', 'standard': '第一性原理与电子结构'}
    assert catalog_service.get_app_or_raise(temp_db, 'legacy')['type'] == '科学计算'


def test_empty_state_has_all_standard_app_types(temp_db):
    state = state_service.get_state(temp_db, user={'username': 'rm', 'role': 'RM'})
    assert state['app_types'] == list_app_types()
