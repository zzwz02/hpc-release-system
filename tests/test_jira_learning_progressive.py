"""Exercise progressive collection against real SQLite/files and a blocked Jira read."""
import asyncio
import json
import threading
import time
from pathlib import Path

import pytest

from app.config import settings
from app.db.connection import transaction
from app.repositories import jira_agent_repo as analysis, jira_learning_repo as repo
from app.services import jira_learning_service as service, jira_learning_runner as execution
from app.services.jira_learning_archive import LearningArchive
from app.services.jira_learning_materials import MaterialStore, catalog
from tests.test_jira_learning_sources import record, archive as tar
from tests.test_jira_learning_workflow import env, RM


def read(directory, sid, *, offset=0, action='read', record=True):
    store=MaterialStore(directory)
    response=store.request({'action':action,'source_id':sid,'offset':offset,'limit':1000,'reason':'核对实际证据'},str(time.time_ns()),record=record)
    return response,json.loads(response['contentItems'][0]['text'])


def test_creation_and_start_do_not_wait_for_jira_or_attachment_parsing(env, monkeypatch, tmp_path):
    _, _, conn=env
    gate=threading.Event()
    started=threading.Event()
    def snapshot(key):
        started.set()
        assert gate.wait(15)
        return {'key':key,'description':'confirmed input','acquisition':{'status':'complete'}}
    monkeypatch.setattr(execution.jira,'get_issue_snapshot',snapshot)
    monkeypatch.setattr(service.jira,'search_issues',lambda *a,**k: pytest.fail('submission must not query Jira'))
    with transaction(conn):
        conversation=analysis.create_conversation(conn,conversation_id='jac_async',issue_key='MC3-1',issue_summary='case',agent_group='HPC',owner='owner',created_by='rm',workspace=str(tmp_path))
        turn=analysis.create_turn(conn,conversation_id=conversation['id'],trigger='handover',created_by='rm',input_text='')
        for index in range(600):
            analysis.add_event(conn,conversation_id=conversation['id'],turn_id=turn['id'],kind='user_message',payload={'author':'rm' if index%2 else 'owner','text':f'{index} correction '+'x'*20000})
    job=service.create_job(RM,'HPC',['MC3-1'],repository_only=True)['job']
    async def run():
        runner=execution.JiraLearningRunner()
        try:
            source,_,_=await asyncio.wait_for(runner._prepare(repo.get_job(conn,job['id'])),5)
            content=runner._model_content(source)
            assert started.is_set() and not gate.is_set()
            assert len(content)<15000 and source['sources']==[]
            directory=settings.jira_agent_data_dir/'learning'/job['id']
            archive=LearningArchive(directory)
            ticks=0
            while archive.cases()[0]['local_status']=='pending':
                await asyncio.sleep(.02)
                ticks+=1
                assert ticks<300
            page=service.material_index(job['id'],'MC3-1','issue',limit=20)
            assert page['total']==600 and len(page['items'])==20
            assert all('text' not in item and 'origin' not in item for item in page['items'])
            assert len(json.dumps(service.get_job(job['id'])))<15000
            assert len(json.dumps(service.source_summary(job['id'])))<15000
            assert {m['author'] for m in page['items']}=={'owner','rm'}
            response=await service.material_read(job['id'],page['items'][0]['id'],limit=80)
            body=json.loads(response['contentItems'][0]['text'])
            assert len(body['text'])==80 and body['next_offset']==80
            assert archive.validation()['sources']==[]  # UI preview is not agent evidence.
            pending=read(directory,'MC3-1:jira:description')[1]
            assert pending['status']=='preparing' and archive.validation()['material_reads']==[]
            gate.set()
            await asyncio.gather(*list(runner._collectors.values()))
            assert archive.progress()=={'total':1,'ready':1,'pending':0,'limited':0}
            assert service.material_index(job['id'],'MC3-1','issue',offset=600)['items'][0]['kind']=='description'
        finally:
            gate.set()
            if runner._collectors:
                await asyncio.gather(*list(runner._collectors.values()))
    asyncio.run(run())


def test_lazy_archives_cache_and_incremental_evidence_survive_original_removal(tmp_path):
    file=tar(tmp_path/'bundle.tar.gz',[('good/log.txt',b'observed=10'),('bad/log.txt',b'observed=20')])
    directory=tmp_path/'job'
    archive=LearningArchive(directory)
    archive.initialize([{'key':'CASE-1'}])
    archive.put_case('CASE-1',{'_materials':catalog({'key':'CASE-1'},[],[file])},local=True)
    archive.put_case('CASE-1',{'issue':{'key':'CASE-1'},'acquisition':{'status':'complete'}})
    sid=archive.listing()['items'][0]['id']
    assert not list((directory/'materials').glob('[a-f0-9]'*64))
    listing=read(directory,sid,action='list')[1]
    member=listing['items'][1]['id']
    assert all('origin' not in entry for entry in listing['items'])
    response,body=read(directory,member)
    assert response['success'] and body['text']=='observed=20'
    assert read(directory,sid,action='list',offset=1)[1]['items'][0]['id']==member
    Path(file['local_path']).unlink()
    assert read(directory,member)[1]['text']=='observed=20'
    assert len(archive.validation()['sources'])==1
    assert not (directory/'materials'/'index.json').exists()
    inherited=LearningArchive(tmp_path/'combined');inherited.initialize([{'key':'CASE-1'}])
    inherited.inherit(directory,'old:')
    assert read(inherited.directory,'old:'+member)[1]['text']=='observed=20'
    with pytest.raises(ValueError,match='不属于本任务'):
        read(directory,'another-task:log')


def test_browser_preview_and_agent_batch_read_have_separate_evidence(env):
    _,_,conn=env
    job=service.create_job(RM,'HPC',['MC3-1'],repository_only=True)['job']
    directory=settings.jira_agent_data_dir/'learning'/job['id']
    archive=LearningArchive(directory);archive.initialize([{'key':'MC3-1'}])
    archive.put_case('MC3-1',{'sources':[{'id':str(i),'kind':'comment','text':'observation '+str(i),'independent':True} for i in range(3)]})
    execution.JiraLearningRunner()._persist_source(job,archive.bundle())
    ids=[s['id'] for s in archive.listing()['items']]
    assert read(directory,ids[0],record=False)[0]['success']
    assert archive.validation()['sources']==[]
    store=MaterialStore(directory)
    response=store.request({'action':'read','source_id':'','source_ids':ids,'offset':0,'limit':100,'reason':'核对不同结果'},'batch')
    assert response['success'] and len(response['contentItems'])==3
    validation=archive.validation()
    assert len(validation['sources'])==len(validation['material_reads'])==3
    evidence=validation['sources'][0]['id']
    assert service.material_index(job['id'],ids=[evidence])['items'][0]['id']==evidence
    assert 'text' not in service.material_index(job['id'],ids=[evidence])['items'][0]
    assert json.loads(asyncio.run(service.material_read(job['id'],evidence))['contentItems'][0]['text'])['text']=='observation 0'


def test_job_poll_only_returns_new_events(env):
    _,_,conn=env
    job=service.create_job(RM,'HPC',['MC3-1'],repository_only=True)['job']
    turn=repo.list_turns(conn,job['id'])[0]
    with transaction(conn):
        repo.add_event(conn,job['id'],turn['id'],'status',{'text':'one'})
    first=service.get_job(job['id'])
    cursor=first['events'][-1]['seq']
    assert service.get_job(job['id'],cursor)['events']==[]
    with transaction(conn):
        repo.add_event(conn,job['id'],turn['id'],'status',{'text':'two'},item_id='status2')
    assert [e['payload']['text'] for e in service.get_job(job['id'],cursor)['events']]==['two']

    second=service.get_job(job['id'],cursor)['events'][0]
    with transaction(conn):
        repo.add_event(conn,job['id'],turn['id'],'status',{'text':'updated'},item_id='status2')
    updated=service.get_job(job['id'],second['seq'])['events']
    assert len(updated)==1 and updated[0]['id']==second['id'] and updated[0]['payload']['text']=='updated'
