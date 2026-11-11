"""Jenkins HTTP contracts using a mock transport; no live credentials or builds."""
import base64
import json
from types import SimpleNamespace

import httpx
import pytest

from app import runtime_config
from app.config import settings
from app.integrations import jenkins
from app.services.mcp_jenkins_tools import _build_errors
from app.services.mcp_tools import ToolContext, ToolError


@pytest.fixture
def configured(tmp_path, monkeypatch):
    path = tmp_path / 'test.conf'
    path.write_text('[cicd_agent]\nJENKINS_BASE_URL=https://ci.example/jenkins\n'
                    'JENKINS_USERNAME=bot\nJENKINS_API_TOKEN=test-secret\nJENKINS_TIMEOUT_SECONDS=5\n')
    monkeypatch.setattr(settings, 'runtime_conf_path', path)
    return path


def context():
    return ToolContext(SimpleNamespace(user={'username': 'alice', 'role': 'Owner'}), {}, '')


def install_transport(monkeypatch, handler):
    original = httpx.Client
    monkeypatch.setattr(jenkins.httpx, 'Client', lambda **kw: original(transport=httpx.MockTransport(handler), **kw))


def test_failed_cases_with_original_errors_and_basic_auth(configured, monkeypatch):
    requests = []
    def handler(request):
        requests.append(request)
        assert request.method == 'GET'
        assert request.headers['authorization'] == 'Basic ' + base64.b64encode(b'bot:test-secret').decode()
        if '/testReport/' in request.url.path:
            return httpx.Response(200, json={'failCount': 1, 'passCount': 1, 'skipCount': 0, 'suites': [
                {'name': 'ai models', 'cases': [
                    {'name': 'case_failed', 'status': 'FAILED', 'errorDetails': 'shape mismatch',
                     'errorStackTrace': 'line 42', 'stdout': 'test-secret'},
                    {'name': 'case_passed', 'status': 'PASSED'},
                ]}]})
        return httpx.Response(200, json={'number': 7, 'result': 'UNSTABLE'})
    install_transport(monkeypatch, handler)
    result = _build_errors(context(), {'build_url': 'https://ci.example/jenkins/job/AI/job/models/7/'})
    data = json.loads(result.content[0]['text'])
    assert data['failed_case_total'] == 1
    assert data['failed_cases'][0]['name'] == 'case_failed'
    assert data['failed_cases'][0]['errorDetails'] == 'shape mismatch'
    assert data['failed_cases'][0]['errorStackTrace'] == 'line 42'
    assert 'test-secret' not in result.content[0]['text']
    assert len(requests) == 2


@pytest.mark.parametrize('url', [
    'https://evil.example/job/models/7/', 'https://ci.example/other/job/models/7/',
    'https://bot:secret@ci.example/jenkins/job/models/7/',
    'https://ci.example/jenkins/job/../7/', 'https://ci.example/jenkins/job/%252e%252e/7/',
    'https://ci.example/jenkins/job/models/7/?url=https://evil.example',
    'https://ci.example/jenkins/script',
])
def test_rejects_urls_before_sending_credentials(configured, monkeypatch, url):
    install_transport(monkeypatch, lambda _: pytest.fail('must not make HTTP request'))
    with pytest.raises(ToolError):
        _build_errors(context(), {'build_url': url})


@pytest.mark.parametrize('status', [302, 401, 403, 500])
def test_redirects_and_errors_do_not_leak_credentials(configured, monkeypatch, status):
    requests = []
    def handler(request):
        requests.append(request)
        return httpx.Response(status, headers={'location': 'https://evil.example/'}, text='test-secret')
    install_transport(monkeypatch, handler)
    with pytest.raises(ToolError) as caught:
        _build_errors(context(), {'build_url': 'https://ci.example/jenkins/job/models/7/'})
    assert str(status) in str(caught.value) and 'test-secret' not in str(caught.value)
    assert len(requests) == 1


def test_missing_report_is_explicit(configured, monkeypatch):
    install_transport(monkeypatch, lambda request: httpx.Response(404) if '/testReport/' in request.url.path
                      else httpx.Response(200, json={'result': 'FAILURE'}))
    data = json.loads(_build_errors(context(), {'build_url': 'https://ci.example/jenkins/job/models/7/'}).content[0]['text'])
    assert data['test_report_available'] is False
    assert data['counts'] is None
    assert 'console' in data['hint']


def test_console_pages_use_consumed_bytes_without_skipping(configured, monkeypatch):
    raw = b'a' * (jenkins.CONSOLE_CHUNK_BYTES + 13)
    def handler(request):
        start = int(request.url.params['start'])
        return httpx.Response(200, content=raw[start:], headers={'X-Text-Size': str(len(raw))})
    install_transport(monkeypatch, handler)
    client = jenkins.JenkinsClient()
    url = client.build_url('https://ci.example/jenkins/job/models/7/')
    first = client.console(url, 0)
    assert first['has_more'] and first['next_start'] == jenkins.CONSOLE_CHUNK_BYTES
    second = client.console(url, first['next_start'])
    assert not second['has_more']
    assert (first['content'] + second['content']).encode() == raw


def test_credentials_refresh_and_missing_config(configured):
    assert jenkins.JenkinsClient().token == 'test-secret'
    configured.write_text(configured.read_text().replace('test-secret', 'rotated-secret'))
    assert jenkins.JenkinsClient().token == 'rotated-secret'
    configured.write_text('[cicd_agent]\n')
    with pytest.raises(ToolError, match='JENKINS_BASE_URL'):
        _build_errors(context(), {'build_url': 'https://ci.example/jenkins/job/models/7/'})


def test_failed_case_pagination(configured, monkeypatch):
    cases = [{'name': str(i), 'status': 'REGRESSION'} for i in range(23)]
    install_transport(monkeypatch, lambda request: httpx.Response(200, json={
        'suites': [{'cases': cases}], 'failCount': 23,
    } if '/testReport/' in request.url.path else {'result': 'UNSTABLE'}))
    args = {'build_url': 'https://ci.example/jenkins/job/models/7/'}
    first = json.loads(_build_errors(context(), args).content[0]['text'])
    assert first['next_case_offset'] == 20 and len(first['failed_cases']) == 20
    second = json.loads(_build_errors(context(), {**args, 'case_offset': 20}).content[0]['text'])
    assert second['next_case_offset'] is None and len(second['failed_cases']) == 3


def test_running_build_does_not_loop_over_consumed_log(configured, monkeypatch):
    install_transport(monkeypatch, lambda request: httpx.Response(200, content=b'waiting',
                      headers={'X-Text-Size': '120', 'X-More-Data': 'true'}))
    result = jenkins.JenkinsClient().console('https://ci.example/jenkins/job/models/7/', 100)
    assert result['building'] is True
    assert result['has_more'] is False
    assert result['next_start'] == 120


@pytest.mark.parametrize('path,allowed', [('results/report.json', True), ('other/report.json', False),
                                         ('../report.json', False), ('results/log.txt', False)])
def test_artifacts_must_be_listed_json_reports(configured, monkeypatch, path, allowed):
    requested = []
    def handler(request):
        requested.append(request.url.path)
        if '/artifact/' in request.url.path:
            return httpx.Response(200, json={'result': [{'case_item1': 'pangu_test', 'case_result': 'Failed'}]})
        return httpx.Response(200, json={'artifacts': [{'relativePath': 'results/report.json'}]})
    install_transport(monkeypatch, handler)
    args = {'build_url': 'https://ci.example/jenkins/job/models/7/', 'source': 'artifact', 'artifact_path': path}
    if allowed:
        data = json.loads(_build_errors(context(), args).content[0]['text'])
        assert data['report']['result'][0]['case_result'] == 'Failed'
    else:
        with pytest.raises(ToolError):
            _build_errors(context(), args)
        assert not any('/artifact/' in p for p in requested)
