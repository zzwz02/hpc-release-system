import json
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx
import pytest

from tests.test_mcp_jenkins_live import configured, context, install_transport
from app.services.mcp_jenkins_discovery import _discover
from app.services.mcp_jenkins_tools import _build_errors
from app.services.mcp_evidence import correlations
from app.services.mcp_tools import ToolError


def test_job_discovery_filters_names_and_exposes_folders_and_cursor(configured, monkeypatch):
    def handler(request):
        assert request.url.path == '/jenkins/api/json'
        assert request.url.params['tree'].endswith('{0,3}')
        return httpx.Response(200,json={'jobs':[
            {'name':'Hpc_AIModels_Test','url':'https://ci.example/jenkins/job/Hpc_AIModels_Test/','_class':'WorkflowJob'},
            {'name':'folder','url':'https://ci.example/jenkins/job/folder/','_class':'Folder'},
            {'name':'other'}]})
    install_transport(monkeypatch,handler)
    result=json.loads(_discover(context(),{'query':'ai models','page_size':2},builds=False).content[0]['text'])
    assert [r['name'] for r in result['jobs']]==['Hpc_AIModels_Test']
    assert result['next_offset']==2 and result['scanned']==2
    assert result['folders'][0]['name']=='folder'


def test_build_dates_are_inclusive_beijing_and_keep_success(configured,monkeypatch):
    times=['2026-11-11T00:00:00','2026-11-11T23:59:59','2026-11-12T00:00:00']
    records=[{'number':i,'timestamp':datetime.fromisoformat(t).replace(tzinfo=ZoneInfo('Asia/Shanghai')).timestamp()*1000,
              'result':'SUCCESS','url':f'https://ci.example/jenkins/job/ai/{i}/'} for i,t in enumerate(times,1)]
    install_transport(monkeypatch,lambda r:httpx.Response(200,json={'builds':records}))
    result=json.loads(_discover(context(),{'job_url':'https://ci.example/jenkins/job/ai/',
                     'date_from':'2026-11-11','date_to':'2026-11-11'},builds=True).content[0]['text'])
    assert [r['number'] for r in result['builds']]==[1,2]
    assert all(r['started_at'].endswith('+08:00') for r in result['builds'])


@pytest.mark.parametrize('builds,args',[(True,{'job_url':'https://evil.example/job/ai/'}),
                                       (False,{'parent_url':'https://ci.example/jenkins/script'})])
def test_discovery_rejects_untrusted_endpoints(configured,monkeypatch,builds,args):
    install_transport(monkeypatch,lambda r:pytest.fail('must not send credentials'))
    with pytest.raises(ToolError):_discover(context(),args,builds=builds)


def test_case_context_exact_boundary_and_custom_report(configured,monkeypatch):
    log="Processing test case 'pangu_test'\nrun_pangu.sh\nImportError: numpy failed\nreturn test status: Failed\nProcessing test case 'other'\nother error\n"
    def handler(request):
        if '/testReport/' in request.url.path:return httpx.Response(404)
        if '/artifact/' in request.url.path:
            return httpx.Response(200,json={'jenkins_job_info':{'Jenkins_build_number':'7'},
                'execute_config':{'item1':'aimodels','item2':'0.7.4'},
                'result':[{'case_item1':'pangu_test','case_result':'Failed','extend4':'registry/ai:1','extend2':'sha256:abc'}]})
        if request.url.path.endswith('progressiveText'):
            return httpx.Response(200,text=log,headers={'X-Text-Size':str(len(log))})
        return httpx.Response(200,json={'artifacts':[{'relativePath':'cicd-test-results/result.json'}],'result':'SUCCESS'})
    install_transport(monkeypatch,handler)
    data=json.loads(_build_errors(context(),{'build_url':'https://ci.example/jenkins/job/ai/7/','case_name':'pangu_test'}).content[0]['text'])
    assert data['cases'][0]['case']['case_result']=='Failed'
    refs=data['cases'][0]['correlations']['references']
    assert any(r['kind']=='image' and r['value']=='registry/ai:1' for r in refs)
    assert 'ImportError' in data['console']['matches'][0]['content']
    assert 'other error' not in data['console']['matches'][0]['content']
    assert data['console']['matches'][0]['match']=='case_marker'


def test_case_scan_missing_is_not_claimed_globally_absent(configured,monkeypatch):
    from app.services.mcp_jenkins_cases import CASE_SCAN_BYTES
    install_transport(monkeypatch,lambda r:httpx.Response(200,content=b'a'*(CASE_SCAN_BYTES+1)))
    data=json.loads(_build_errors(context(),{'build_url':'https://ci.example/jenkins/job/ai/7/',
                    'source':'console','case_name':'missing'}).content[0]['text'])
    assert data['console']['has_more'] is True
    assert data['console']['found_in_scanned_log'] is False
    assert data['console']['next_start']==CASE_SCAN_BYTES


def test_correlations_do_not_guess_from_names():
    source='https://source.example/record/1'
    data={'official_name':'aimodels','app_version':'0.7.4','build_url':'https://ci/job/ai/7/',
          'description':'looks like MC3-123; see https://jira/browse/MC3-456',
          'raw_fields':{'issuelinks':[{'outwardIssue':{'key':'MC3-789'}}]}}
    refs=correlations(data,source)['references']
    assert all(r['source']==source and r['field'] for r in refs)
    assert not any(r['value']=='MC3-123' for r in refs)
    assert any(r['value']=='https://jira/browse/MC3-456' and r['relation']=='text_reference' for r in refs)
    assert any(r['value']=='MC3-789' and r['relation']=='jira_issue_link' for r in refs)
