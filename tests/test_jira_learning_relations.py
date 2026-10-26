"""Proposal comparisons and consolidation retain evidence and require fresh review."""
import asyncio
import json

import pytest

from app.db.connection import transaction
from app.integrations import knowledge_repository as knowledge
from app.repositories import jira_learning_repo as repo
from app.services import jira_learning_service as service, jira_learning_relations as relations, jira_learning_runner as execution
from tests.learning_helpers import commit
from tests.test_jira_learning_workflow import env, stage, RM, PREFIX


def test_relations_follow_current_status_and_compare_actual_shared_changes(env):
    _, _, conn = env
    first, a = stage(conn)
    second, b = stage(conn, key='MC3-2')
    detail = service.get_job(first['id'])
    assert detail['proposals'][0]['warnings'][0]['status'] == 'pending'
    compared = relations.compare(a['id'], b['id'])
    assert compared['relation']['reason'] == '共同文件的修改内容相同'
    assert '+New rule.' in compared['current']['files'][0]['patch']
    service.review(RM, b['id'], 'approved', '')
    assert service.get_job(first['id'])['proposals'][0]['warnings'][0]['status'] == 'approved'
    with transaction(conn):
        repo.add_proposals(conn, second['id'], [{**b, 'previous_id': b['id']}], turn_id=repo.list_turns(conn, second['id'])[0]['id'])
    latest = service.get_job(first['id'])['proposals'][0]['warnings']
    assert len(latest) == 1 and latest[0]['proposal_id'] != b['id']
    service.review(RM, latest[0]['proposal_id'], 'rejected', '不采用')
    assert service.get_job(first['id'])['proposals'][0]['warnings'] == []


def test_consolidation_keeps_originals_and_evidence_and_pins_one_new_base(env, monkeypatch):
    root, conf, conn = env
    from tests import test_jira_agent as protocol
    first, a = stage(conn)
    second, b = stage(conn, key='MC3-2')
    service.review(RM, a['id'], 'approved', '')
    result = relations.combine(RM, [a['id'], b['id']], '整合重复条件', 'combine-one')
    job = result['job']
    assert job['issue_keys'] == ['MC3-1', 'MC3-2']
    assert relations.combine(RM, [b['id'], a['id']], '整合重复条件', 'combine-one')['job']['id'] == job['id']
    with pytest.raises(Exception, match='标识'):
        relations.combine(RM, [a['id'], b['id']], 'different', 'combine-one')
    newbase = commit(root, {PREFIX + 'unrelated.md': '# independently added\n'})
    asyncio.run(execution.JiraLearningRunner()._reload(repo.get_job(conn,job['id']), {}))
    saved = service.get_source(job['id'])
    assert len(saved['cases']) == 2
    assert len({s['id'] for s in saved['sources']}) == 2
    assert saved['combination'][0]['evidence_ids'][0] in {s['id'] for s in saved['sources']}
    assert repo.get_proposal(conn, a['id'])['status'] == 'approved'
    assert repo.get_proposal(conn, b['id'])['status'] == 'pending'
    assert not service.get_job(first['id'])['job']['readonly']
    fake = protocol.FakeCodex().start()
    conf.write_text(conf.read_text().replace('ws://unused:4520', fake.url).replace('CODEX_WS_TOKEN=test', 'CODEX_WS_TOKEN=' + fake.token))
    raw = {**a, 'previous_id': '', 'dependencies': [],
           'evidence_ids': [s['id'] for s in saved['sources']],
           'files': [{'path': f['path'], 'updated_text': f['updated_text']} for f in a['files']]}
    monkeypatch.setattr(protocol, 'RESULT', {'proposals': [raw]})
    with transaction(conn):
        work = repo.claim_next_turn(conn)
    try:
        asyncio.run(execution.JiraLearningRunner()._run(work['job'], work['turn']))
        done = service.get_job(job['id'])
        assert done['job']['status'] == 'completed', done['job']['error']
        generated = done['proposals'][0]
        assert generated['base_commit'] == newbase
        assert generated['status'] == 'pending'
        workspace = '/learning/workspaces/' + job['id']
        model = json.loads(fake.files[workspace + '/source.json'])
        assert 'files' not in model['combination'][0]
        old = json.loads(fake.files[workspace + '/related/' + a['id'] + '.json'])
        assert old['files'] == a['files']
        assert model['sources'] == []
        assert old['evidence_ids'][0] in {s['id'] for s in service.get_source(job['id'])['sources']}
        with pytest.raises(Exception, match='先审核'):
            relations.close_duplicate(RM, a['id'], generated['id'])
        service.review(RM, generated['id'], 'approved', '已核对两份修改')
        relations.close_duplicate(RM, a['id'], generated['id'])
        assert repo.get_proposal(conn, a['id'])['status'] == 'rejected'
        assert repo.get_proposal(conn, b['id'])['status'] == 'pending'
        assert len(repo.list_reviews(conn, a['id'])) == 2
        assert service.get_job(job['id'])['combination'][0]['status'] == 'rejected'
        # Neither the model nor an approval has written to the skill repository.
        assert 'New rule.' not in knowledge.snapshot('main')['files'][PREFIX + 'SKILL.md']
    finally:
        fake.stop()


def test_invalid_selection_and_cancel_preserve_originals(env):
    _, _, conn = env
    _, a = stage(conn)
    _, b = stage(conn, key='MC3-2')
    with pytest.raises(ValueError):
        relations.combine(RM, [a['id'], a['id']], '', 'duplicate')
    job = relations.combine(RM, [a['id'], b['id']], '', 'cancelled')['job']
    service.cancel(RM, job['id'])
    assert all(repo.get_proposal(conn, p['id'])['status'] == 'pending' for p in [a, b])
    service.review(RM, b['id'], 'rejected', '')
    with pytest.raises(Exception, match='未关闭'):
        relations.combine(RM, [a['id'], b['id']], '', 'closed')


def test_same_jira_snapshots_deduplicate_and_refresh_retains_selected_proposals(env):
    _, _, conn = env
    _, a = stage(conn)
    _, b = stage(conn)
    job = relations.combine(RM, [a['id'], b['id']], '', 'same-case')['job']
    asyncio.run(execution.JiraLearningRunner()._reload(repo.get_job(conn,job['id']), {}))
    source = service.get_source(job['id'])
    assert len(source['sources']) == 1
    assert len(source['cases']) == 1
    assert len(source['sources'][0]['copies']) == 1
    assert source['combination'][0]['evidence_ids'] == source['combination'][1]['evidence_ids']
    service.cancel(RM, job['id'])
    refreshed = service.refresh(RM, job['id'])['job']
    asyncio.run(execution.JiraLearningRunner()._reload(repo.get_job(conn,refreshed['id']), {}))
    reloaded = service.get_source(refreshed['id'])
    assert reloaded['combination'] == source['combination']
    assert reloaded['knowledge_pending']
    assert reloaded['sources'] == source['sources']
    assert service.get_job(job['id'])['job']['readonly']
    assert all(repo.get_proposal(conn, p['id'])['status'] == 'pending' for p in [a, b])


def test_equal_outputs_on_different_bases_are_not_identical_changes(env):
    root, _, conn = env
    _, first = stage(conn)
    commit(root, {PREFIX + 'SKILL.md': '# HPC\n\nIndependent change.\n'})
    _, second = stage(conn, key='MC3-2', files=[{'path': f['path'], 'updated_text': f['updated_text']} for f in first['files']])
    comparison = relations.compare(first['id'], second['id'])
    assert comparison['relation']['reason'] == '文件基线不同'
    assert comparison['current']['files'][0]['patch'] != comparison['other']['files'][0]['patch']
