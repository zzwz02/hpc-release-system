from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
from urllib.parse import parse_qs

import httpx
import pytest

from app.config import settings
from app.integrations import dingtalk
from app.services import notification_service as service
from app.db.jira_agent_connection import connect_jira_agent


@pytest.fixture
def configured(tmp_path, monkeypatch):
    conf = tmp_path / 'runtime.conf'
    conf.write_text('[dingtalk]\nACCESS_TOKEN = test-token\nSECRET = test-secret\nUSERID = u1, u2\n'
                    '[cicd_agent]\nBASE_URL = http://agent\nTIMEOUT_SECONDS = 5\n')
    monkeypatch.setattr(settings, 'runtime_conf_path', conf)
    monkeypatch.setattr(settings, 'jira_agent_database_url', f'sqlite:///{tmp_path}/jira.db')
    return conf


def test_signed_request_and_business_failure(configured, monkeypatch, caplog):
    caplog.set_level("INFO", logger="httpx")
    real_client = httpx.AsyncClient
    monkeypatch.setattr(dingtalk.time, 'time', lambda: 1700000000)
    seen = []
    def handle(request):
        import json
        seen.append(request)
        params = parse_qs(request.url.query.decode())
        expected = base64.b64encode(hmac.new(b'test-secret', b'1700000000000\ntest-secret', hashlib.sha256).digest()).decode()
        assert params['sign'] == [expected]
        assert params['access_token'] == ['test-token']
        body = json.loads(request.content)
        assert body['at'] == {'atUserIds': ['u1', 'u2'], 'isAtAll': False}
        assert body['text']['content'] == 'PHS-1 jira agent失败'
        return httpx.Response(200, json={'errcode': 0 if len(seen) == 1 else 310000})
    monkeypatch.setattr(dingtalk.httpx, 'AsyncClient', lambda **kw: real_client(transport=httpx.MockTransport(handle), **kw))
    assert asyncio.run(dingtalk.send_text('PHS-1 jira agent失败'))
    assert 'test-token' not in caplog.text
    with pytest.raises(dingtalk.DingTalkError):
        asyncio.run(dingtalk.send_text('PHS-1 jira agent失败'))


def test_transport_error_is_sanitized(configured, monkeypatch):
    real_client = httpx.AsyncClient
    def handle(request):
        raise httpx.ConnectError(str(request.url), request=request)
    monkeypatch.setattr(dingtalk.httpx, 'AsyncClient', lambda **kw: real_client(transport=httpx.MockTransport(handle), **kw))
    with pytest.raises(dingtalk.DingTalkError) as error:
        asyncio.run(dingtalk.send_text('test'))
    assert 'test-token' not in str(error.value)
    assert 'test-secret' not in str(error.value)


def test_daily_thresholds_retry_restart_and_rollover(configured, monkeypatch):
    from app.api.routers import cicd_agent
    counts = {'Code Owner': 20, 'DevOps': 30}
    day = ['2026-10-27']
    monkeypatch.setattr(service, 'beijing_timestamp', lambda: day[0] + ' 12:00:00')
    def query(method, path, *, query):
        assert method == 'GET' and path == '/api/v1/failures/summary'
        assert parse_qs(query) == {'date_from': [day[0]], 'date_to': [day[0]], 'group_by': ['owner_role']}
        return 200, {'group_by': 'owner_role', 'groups': [{'group': k, 'failure_count': v} for k, v in counts.items()]}
    monkeypatch.setattr(cicd_agent, '_request_agent_payload', query)
    sent = []
    failing = [False]
    async def send(message):
        if failing[0]:
            raise dingtalk.DingTalkError('测试失败')
        sent.append(message)
        return True
    monkeypatch.setattr(dingtalk, 'send_text', send)
    async def scenario():
        poller = service.NotificationPoller()
        await poller.tick()
        assert sent == []
        counts.update({'Code Owner': 21, 'DevOps': 31})
        failing[0] = True
        poller._next_jenkins = None
        await poller.tick()
        assert poller.status()['errors']['jenkins'] == '测试失败'
        failing[0] = False
        poller._next_jenkins = None
        await poller.tick()
        assert len(sent) == 1
        restarted = service.NotificationPoller()
        await restarted.tick()
        assert len(sent) == 1
        day[0] = '2026-10-28'
        restarted._next_jenkins = None
        await restarted.tick()
        assert len(sent) == 2
    asyncio.run(scenario())


@pytest.mark.parametrize('conclusion,action,label', [
    ('root_cause_confirmed', 'needs_review', '已找到原因 · 需要审核'),
    ('analysis_done', 'none', '已完成分析'),
    ('fixed_pending_review', 'needs_review', '修复已验证 · 需要审核'),
])
def test_jira_labels(conclusion, action, label):
    import json
    turn = {'issue_key': 'PHS-1', 'status': 'completed', 'delivery_status': 'complete',
            'result_json': json.dumps({'conclusion': conclusion, 'action_required': action})}
    assert service.jira_message(turn) == 'PHS-1 jira agent ' + label
    turn['delivery_status'] = 'incomplete'
    assert '交付不完整' in service.jira_message(turn)
    turn['status'] = 'failed'
    assert service.jira_message(turn) == 'PHS-1 jira agent失败'


def test_config_hot_reload_and_diagnostics(configured):
    poller = service.NotificationPoller()
    assert poller.status()['enabled']
    configured.write_text('[dingtalk]\nACCESS_TOKEN = token\nUSERID = user\n')
    assert 'SECRET' in poller.status()['errors']['config']
    configured.write_text('[dingtalk]\nACCESS_TOKEN =\n')
    assert not poller.status()['enabled']


def test_jira_activation_delivery_and_durable_replay(configured, monkeypatch):
    monkeypatch.setattr(service, 'beijing_timestamp', lambda: '2026-10-27 12:00:00')
    from app.api.routers import cicd_agent
    monkeypatch.setattr(cicd_agent, '_request_agent_payload', lambda *a, **k: (200, {'group_by': 'owner_role', 'groups': []}))
    sent = []
    async def send(message):
        sent.append(message)
        return True
    monkeypatch.setattr(dingtalk, 'send_text', send)
    async def scenario():
        poller = service.NotificationPoller()
        await poller.tick()
        conn = connect_jira_agent(settings.jira_agent_database_url)
        with conn:
            conn.execute("INSERT INTO jira_agent_conversations (id, issue_key, agent_group, owner, created_by, workspace, created_at, updated_at) VALUES ('c', 'PHS-1', 'HPC', 'u', 'u', '/w', '', '')")
            for seq, (status, delivery, finished) in enumerate([
                ('completed', 'collecting', '2026-10-27 12:01:00'),
                ('failed', '', '2026-10-27 11:00:00'),
                ('cancelled', '', '2026-10-27 12:01:00'),
                ('failed', '', '2026-10-27 12:01:00'),
            ]):
                conn.execute("INSERT INTO jira_agent_turns (id, conversation_id, seq, trigger, created_by, created_at, status, delivery_status, finished_at, result_json) VALUES (?, 'c', ?, 'handover', 'u', '', ?, ?, ?, ?)",
                             (str(seq), seq, status, delivery, finished, '{"conclusion":"analysis_done","action_required":"none"}'))
        await poller.tick()
        assert sent == ['PHS-1 jira agent失败']
        with conn:
            conn.execute("UPDATE jira_agent_turns SET delivery_status='complete' WHERE id='0'")
        await service.NotificationPoller().tick()
        assert sent == ['PHS-1 jira agent失败', 'PHS-1 jira agent 已完成分析']
        await service.NotificationPoller().tick()
        assert len(sent) == 2
        conn.close()
    asyncio.run(scenario())


def test_jenkins_only_runs_at_hour_boundaries(configured, monkeypatch):
    clock = ['2026-10-27 12:34:10']
    monkeypatch.setattr(service, 'beijing_timestamp', lambda: clock[0])
    checks = []
    poller = service.NotificationPoller()
    async def check(conn):
        checks.append(clock[0])
    monkeypatch.setattr(poller, '_jenkins', check)
    async def scenario():
        await poller.tick()
        clock[0] = '2026-10-27 12:59:59'
        await poller.tick()
        assert checks == []
        clock[0] = '2026-10-27 13:00:00'
        await poller.tick()
        await poller.tick()
        clock[0] = '2026-10-27 14:00:01'
        await poller.tick()
        assert checks == ['2026-10-27 13:00:00', '2026-10-27 14:00:01']
    asyncio.run(scenario())


def test_diagnostics_require_rm(configured):
    from fastapi.testclient import TestClient
    from app.main import create_app
    from app.deps import require_login
    app = create_app()
    app.dependency_overrides[require_login] = lambda: {"username": "u", "role": "Owner"}
    client = TestClient(app)
    assert client.get('/api/notifications/status').status_code == 403
    app.dependency_overrides[require_login] = lambda: {"username": "u", "role": "RM"}
    response = client.get('/api/notifications/status')
    assert response.status_code == 200
    assert response.json()['enabled'] is True
    assert 'test-token' not in response.text and 'test-secret' not in response.text


def test_jenkins_daily_hours_include_both_boundaries(configured, monkeypatch):
    clock = ['2026-10-27 09:00:00']
    monkeypatch.setattr(service, 'beijing_timestamp', lambda: clock[0])
    checks, jira_checks = [], []
    poller = service.NotificationPoller()
    async def check(conn):
        checks.append(clock[0])
    async def check_jira(conn):
        jira_checks.append(clock[0])
    monkeypatch.setattr(poller, '_jenkins', check)
    monkeypatch.setattr(poller, '_jira', check_jira)
    async def scenario():
        for timestamp in [
            '2026-10-27 09:00:00', '2026-10-27 10:00:00',
            '2026-10-27 20:00:00', '2026-10-27 21:00:00',
            '2026-10-28 00:00:00', '2026-10-28 09:00:00',
            '2026-10-28 10:00:00',
        ]:
            clock[0] = timestamp
            await poller.tick()
        assert checks == ['2026-10-27 10:00:00', '2026-10-27 20:00:00', '2026-10-28 10:00:00']
        assert len(jira_checks) == 7
    asyncio.run(scenario())
