"""Exercise the real ldap3 membership extraction without an external LDAP server."""
import pytest
from ldap3 import Connection, MOCK_SYNC, Server

from app.integrations import ldap


@pytest.fixture
def directory(monkeypatch):
    server = Server('directory.example')
    conn = Connection(server, client_strategy=MOCK_SYNC)
    conn.strategy.add_entry('dc=example', {'objectClass': ['domain']})
    conn.bind()
    monkeypatch.setattr(ldap, 'LdapServer', lambda *_a, **_kw: server)
    monkeypatch.setattr(ldap, 'LdapConn', lambda *_a, **_kw: conn)
    cfg = {'enabled': True, 'uri': 'ldap://directory.example', 'timeout': 1,
           'binddn': 'cn=service,dc=example', 'bindpw': 'test-only', 'base': 'dc=example',
           'user_filter': '(sAMAccountName={uid})'}
    return conn, cfg


def test_groups_are_current_direct_memberships_and_connection_is_closed(directory):
    conn, cfg = directory
    groups = ['CN=dl.pde_hpc,DC=example', 'CN=dl.sw_qa,DC=example']
    conn.strategy.add_entry('cn=alice,dc=example', {'objectClass': ['user'],
                            'sAMAccountName': 'alice', 'memberOf': groups})
    assert set(ldap.groups_for_user('alice', ldap_config=cfg)) == set(groups)
    assert not conn.bound


def test_no_such_domain_account_has_no_groups(directory):
    conn, cfg = directory
    assert ldap.groups_for_user('missing', ldap_config=cfg) == []
    assert not conn.bound


def test_ambiguous_identity_is_never_used_for_authorization(directory):
    conn, cfg = directory
    for name in ['alice', 'other']:
        conn.strategy.add_entry(f'cn={name},dc=example', {'objectClass': ['user'],
                                'sAMAccountName': 'duplicate', 'memberOf': ['dl.sw_qa']})
    with pytest.raises(RuntimeError, match='不唯一'):
        ldap.groups_for_user('duplicate', ldap_config=cfg)
    assert not conn.bound


def test_directory_search_error_closes_connection(directory):
    conn, cfg = directory
    cfg['base'] = 'dc=missing'
    with pytest.raises(RuntimeError, match='域组查询失败'):
        ldap.groups_for_user('alice', ldap_config=cfg)
    assert not conn.bound
