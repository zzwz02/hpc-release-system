"""Configure only the requested HTTP client using an existing token."""
import json
import subprocess
import tomllib
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / 'app/resources/mcp/setup.sh'


@pytest.mark.parametrize('client', ['claude', 'codex'])
@pytest.mark.parametrize('valid', [True, False])
def test_use_existing_token_updates_only_selected_client(tmp_path, client, valid):
    functions = tmp_path / 'setup-functions.sh'
    functions.write_text(SCRIPT.read_text().split('action="${1:-}"')[0])
    claude = tmp_path / 'claude.json'
    codex = tmp_path / 'codex.toml'
    claude.write_text(json.dumps({'mcpServers': {'other': {'command': 'other'}}}))
    codex.write_text('model = "test"\n[mcp_servers.other]\ncommand = "other"\n')
    originals = {p: p.read_bytes() for p in (claude, codex)}
    # Override only paths inside the test shell; no real HOME/config is changed.
    command = '''source "$1"
CLAUDE_JSON="$2"
CODEX_TOML="$3"
MCP_CLIENTS="$4"
MCP_TOKEN=rsmcp_test_abcdefghijklmnopqrstuvwxyz0123456789
MCP_URL=https://example.test/mcp
TOKEN_VALID="$5"
token_works() { [ "$TOKEN_VALID" = yes ]; }
api() { exit 77; }
pgrep() { return 1; }
do_use_token
do_use_token
'''
    result = subprocess.run(['bash', '-c', command, 'test', str(functions), str(claude), str(codex),
                             client, 'yes' if valid else 'no'], capture_output=True, text=True, timeout=10)
    assert 'rsmcp_test_abcdefghijklmnopqrstuvwxyz0123456789' not in result.stdout + result.stderr
    if not valid:
        assert result.returncode != 0
        assert all(p.read_bytes() == old for p, old in originals.items())
        return
    assert result.returncode == 0, result.stderr
    if client == 'claude':
        data = json.loads(claude.read_text())['mcpServers']
        assert data['other'] == {'command': 'other'}
        entry = data['release-system']
        assert entry == {'type': 'http', 'url': 'https://example.test/mcp',
                         'headers': {'Authorization': 'Bearer rsmcp_test_abcdefghijklmnopqrstuvwxyz0123456789'}}
        assert codex.read_bytes() == originals[codex]
    else:
        data = tomllib.loads(codex.read_text())
        assert data['model'] == 'test' and data['mcp_servers']['other'] == {'command': 'other'}
        entry = data['mcp_servers']['release-system']
        assert entry['url'] == 'https://example.test/mcp'
        assert entry['http_headers'] == {'Authorization': 'Bearer rsmcp_test_abcdefghijklmnopqrstuvwxyz0123456789'}
        assert 'command' not in entry
        assert claude.read_bytes() == originals[claude]
    selected = claude if client == 'claude' else codex
    assert selected.stat().st_mode & 0o777 == 0o600
    assert Path(str(selected) + '.bak-release-mcp').exists()
