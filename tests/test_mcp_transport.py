"""Exercise elicitation over TCP with an idle read timeout, not buffered TestClient SSE."""
import json
import socket
import threading
import time

import httpx
import pytest
import uvicorn

from app.services import mcp_protocol
from tests.test_mcp import site  # noqa: F401 — reuse the isolated JIRA/database fixture


@pytest.fixture()
def http_site(site):
    client, _, token_for, calls = site
    token = token_for('alice', 'Owner')
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config(client.app, lifespan='off', log_level='critical'))
        thread = threading.Thread(target=server.run, kwargs={'sockets': [sock]}, daemon=True)
        thread.start()
        try:
            deadline = time.monotonic() + 5
            while not server.started and thread.is_alive() and time.monotonic() < deadline:
                time.sleep(0.01)
            assert server.started, 'Isolated HTTP server did not start'
            yield f'http://127.0.0.1:{port}/mcp', token, calls
        finally:
            server.should_exit = True
            thread.join(5)
            assert not thread.is_alive(), 'Isolated HTTP server did not stop'


@pytest.mark.parametrize('outcome', ['username_mismatch', 'decline', 'cancel', 'timeout', 'username_matched'])
def test_delayed_confirmation_returns_reason_over_live_http(http_site, monkeypatch, outcome):
    url, token, calls = http_site
    monkeypatch.setattr(mcp_protocol, 'SSE_HEARTBEAT_SECONDS', 0.05)
    monkeypatch.setattr(mcp_protocol, 'CONFIRM_TIMEOUT_SECONDS', 1.2)
    # Waiting without heartbeats exceeds this client's idle timeout, like a proxy
    # closing the original tool stream while the user is still reading the form.
    with httpx.Client(timeout=httpx.Timeout(5, read=0.5), trust_env=False) as wire:
        headers = {'Authorization': f'Bearer {token}', 'Accept': 'application/json, text/event-stream'}
        initialized = wire.post(url, headers=headers, json={
            'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {
                'protocolVersion': '2025-06-18', 'capabilities': {'elicitation': {'form': {}}},
                'clientInfo': {'name': 'transport-test', 'version': '1'},
            },
        })
        initialized.raise_for_status()
        headers['Mcp-Session-Id'] = initialized.headers['mcp-session-id']
        request_id, heartbeats, answered, result = None, 0, False, None
        with wire.stream('POST', url, headers=headers, json={
            'jsonrpc': '2.0', 'id': 2, 'method': 'tools/call',
            'params': {'name': 'jira_add_comment', 'arguments': {'issue_key': 'MC3-1', 'body': 'test comment'}},
        }) as response:
            response.raise_for_status()
            for line in response.iter_lines():
                if line.startswith(':'):
                    heartbeats += 1
                elif line.startswith('data: '):
                    message = json.loads(line[6:])
                    if message.get('method') == 'elicitation/create':
                        request_id = message['id']
                    elif message.get('id') == 2:
                        result = message['result']
                # Delay the answer beyond the 0.5-second idle threshold.
                if heartbeats >= 12 and request_id and not answered and outcome != 'timeout':
                    answer = {'action': 'accept', 'content': {
                        'username': 'alice' if outcome == 'username_matched' else 'wrong-user',
                    }}
                    if outcome in ('decline', 'cancel'):
                        answer = {'action': outcome}
                    acknowledged = wire.post(url, headers=headers, json={
                        'jsonrpc': '2.0', 'id': request_id, 'result': answer,
                    })
                    assert acknowledged.status_code == 202 and acknowledged.content == b''
                    answered = True
            assert response.headers['x-accel-buffering'] == 'no'
        assert heartbeats >= 12
        assert result is not None
        if outcome == 'username_matched':
            assert not result['isError'] and len(calls['comments']) == 1
        else:
            assert result['isError'] and calls['comments'] == []
            assert result['structuredContent']['reason'] == outcome
            assert result['structuredContent']['status'] == 'not_executed'
            assert result['structuredContent']['retryable'] is False
            assert '不要自动重试' in result['content'][0]['text']
