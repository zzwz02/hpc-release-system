"""Jenkins site permissions across MCP discovery, calls and live LDAP changes."""
import json

import httpx
import pytest

from app.config import settings
from app.db.connection import connect
from app.integrations import cicd_agent, ldap
from tests.test_mcp import call, connect_client, connect_verified, rpc, site  # noqa: F401
from tests.test_mcp_jenkins_live import configured  # noqa: F401

PDE = {'jenkins_search_failures', 'jenkins_get_failure', 'jenkins_get_build_errors',
       'jenkins_list_jobs', 'jenkins_list_builds'}
CICD = {'cicd_jenkins_list_jobs', 'cicd_jenkins_list_builds', 'cicd_jenkins_get_build_errors'}


def set_identity(username, role, source='ldap'):
    conn = connect(settings.db_path)
    conn.execute('UPDATE users SET role=?, auth_source=? WHERE username=?', (role, source, username))
    conn.commit()
    conn.close()


def membership(monkeypatch, groups):
    monkeypatch.setattr(ldap, 'current_ldap_config', lambda: {'enabled': True})
    monkeypatch.setattr(ldap, 'groups_for_user', lambda username, **_: groups)


def listed(client, token, session):
    return {tool['name'] for tool in rpc(client, token, 'tools/list', session=session).json()['result']['tools']}


@pytest.mark.parametrize('role,groups,expected', [
    ('Guest', [], set()),
    ('Guest', ['CN=dl.pde_hpc,OU=Groups,DC=example'], PDE),
    ('Guest', ['CN=DL.SW_QA,OU=Groups,DC=example'], CICD),
    ('Guest', ['dl.pde_hpc', 'dl.sw_compiler'], PDE | CICD),
    ('Guest', ['dl.C500_sw', 'dl.pde', 'dl.sw', 'xdl.sw_qa'], set()),
    ('Owner', [], PDE),
    ('RM', [], PDE | CICD),
    ('QA', [], PDE),
    ('SPD', [], PDE),
    ('Owner', ['dl.sw_runtime'], PDE | CICD),
])
def test_site_grants_match_listing_info_and_calls(site, monkeypatch, role, groups, expected):
    client, _, token_for, _ = site
    set_identity('guest', role)
    membership(monkeypatch, groups)
    token = token_for('guest', role)
    session, initialized = connect_client(client, token)
    assert listed(client, token, session) & (PDE | CICD) == expected
    assert {t['name'] for t in client.get('/api/mcp/info').json()['tools']} & (PDE | CICD) == expected
    if role == 'Guest':
        assert '不能读取其他项目、调用 Jenkins' not in initialized['instructions']
        assert 'cicd_jenkins_' in initialized['instructions']
        assert not listed(client, token, session) & {'jira_add_comment', 'jira_upload_attachment'}
        assert call(client, token, session, 'jira_pull_issue', {'issue_key': 'PHS-1'})['isError']
    # Invalid arguments on an authorized tool reach validation; unauthorized calls
    # are rejected before any handler or integration is entered.
    for name in PDE | CICD:
        arguments = {'unknown_argument': True}
        reply = rpc(client, token, 'tools/call', {'name': name, 'arguments': arguments}, session=session).json()
        if name in expected:
            assert reply['result']['isError']
        else:
            assert reply['error']['code'] == -32602


def test_local_account_does_not_inherit_same_named_domain_account(site, monkeypatch):
    client, _, token_for, _ = site
    def forbidden(*_args, **_kwargs):
        pytest.fail('local users must not query a same-named domain identity')
    monkeypatch.setattr(ldap, 'groups_for_user', forbidden)
    set_identity('guest', 'Guest', 'local')
    token = token_for('guest', 'Guest')
    session, _ = connect_client(client, token)
    assert not listed(client, token, session) & (PDE | CICD)


def test_existing_token_grants_and_revocations_apply_on_every_request(site, monkeypatch):
    client, _, token_for, _ = site
    set_identity('guest', 'Guest')
    groups = []
    membership(monkeypatch, groups)
    monkeypatch.setattr(cicd_agent, '_request_agent_payload', lambda *_a, **_kw: (200, {'total': 0, 'items': []}))
    token = token_for('guest', 'Guest')
    session, _ = connect_client(client, token)
    assert not listed(client, token, session) & PDE
    groups.append('dl.pde_hpc')
    assert not call(client, token, session, 'jenkins_search_failures', {})['isError']
    assert listed(client, token, session) & PDE == PDE
    assert call(client, token, session, 'jenkins_search_failures', {'record_state': 'trashed'})['isError']
    groups.clear()
    reply = rpc(client, token, 'tools/call', {'name': 'jenkins_search_failures'}, session=session).json()
    assert reply['error']['code'] == -32602
    assert not listed(client, token, session) & PDE


def test_ldap_failure_is_closed_and_visible_without_breaking_role_grants_or_jira(site, monkeypatch):
    client, _, token_for, _ = site
    set_identity('alice', 'Owner')
    membership(monkeypatch, [])
    def broken(*_a, **_kw):
        raise RuntimeError('private-bind-secret')
    monkeypatch.setattr(ldap, 'groups_for_user', broken)
    token = token_for('alice', 'Owner')
    session = connect_verified(client, token)
    response = rpc(client, token, 'tools/list', session=session).json()['result']
    assert {t['name'] for t in response['tools']} & (PDE | CICD) == PDE
    assert 'LDAP' in response['_meta']['access_warnings'][0]
    info = client.get('/api/mcp/info').json()
    assert 'LDAP' in info['access_warnings'][0]
    result = call(client, token, session, 'cicd_jenkins_list_jobs', {})
    assert result['isError'] and 'LDAP' in result['content'][0]['text']
    assert 'private-bind-secret' not in json.dumps([response, info, result])
    assert not call(client, token, session, 'jira_pull_issue', {'issue_key': 'PHS-1'})['isError']


def test_cicd_tool_http_contract_and_live_group_revocation(site, configured, monkeypatch):
    client, _, token_for, _ = site
    set_identity('guest', 'Guest')
    groups = ['dl.sw_tools']
    membership(monkeypatch, groups)
    configured.write_text(configured.read_text() + '\n[jenkins_cicd]\nBASE_URL=https://cicd.example\n'
                          'USERNAME=bot\nAPI_TOKEN=cicd-secret\nTIMEOUT_SECONDS=5\n')
    requests = []
    original = httpx.Client
    def handler(request):
        requests.append(request)
        assert request.url.host == 'cicd.example'
        return httpx.Response(200, json={'jobs': [{'name': 'compiler', 'url': 'https://cicd.example/job/compiler/'}]})
    # Patch only the integration's client constructor; retain the TestClient transport.
    from app.integrations import jenkins
    from types import SimpleNamespace
    monkeypatch.setattr(jenkins, 'httpx', SimpleNamespace(
        Client=lambda **kw: original(transport=httpx.MockTransport(handler), **kw), HTTPError=httpx.HTTPError))
    token = token_for('guest', 'Guest')
    session, _ = connect_client(client, token)
    result = call(client, token, session, 'cicd_jenkins_list_jobs', {})
    assert not result['isError']
    assert json.loads(result['content'][0]['text'])['jobs'][0]['name'] == 'compiler'
    groups.clear()
    denied = rpc(client, token, 'tools/call', {'name': 'cicd_jenkins_list_jobs'}, session=session).json()
    assert denied['error']['code'] == -32602
    assert len(requests) == 1


@pytest.mark.parametrize('source', ['local', 'ldap'])
def test_rm_has_all_jenkins_access_without_ldap_and_role_change_is_immediate(site, monkeypatch, source):
    client, current, token_for, _ = site
    set_identity('rm', 'RM', source)
    def forbidden(*_a, **_kw):
        pytest.fail('RM must not depend on LDAP availability')
    monkeypatch.setattr(ldap, 'groups_for_user', forbidden)
    monkeypatch.setattr(ldap, 'current_ldap_config', forbidden)
    token = token_for('rm', 'RM')
    session, _ = connect_client(client, token)
    assert listed(client, token, session) & (PDE | CICD) == PDE | CICD
    info = client.get('/api/mcp/info').json()
    assert {t['name'] for t in info['tools']} & (PDE | CICD) == PDE | CICD
    assert not info['access_warnings']
    for name in CICD:
        # Reaching input validation proves this is callable without LDAP.
        assert call(client, token, session, name, {'unknown_argument': True})['isError']
    # An existing token loses the all-sites grant after an RM demotion.
    set_identity('rm', 'Owner', 'local')
    current['role'] = 'Owner'
    assert listed(client, token, session) & (PDE | CICD) == PDE
    assert rpc(client, token, 'tools/call', {'name': 'cicd_jenkins_list_jobs'}, session=session).json()['error']['code'] == -32602
