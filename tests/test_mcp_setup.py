"""MCP setup token reuse, regeneration confirmation, and local configuration."""
import json
import os
import re
import subprocess
import tomllib
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / 'app/resources/mcp/setup.sh'


@pytest.mark.parametrize('client', ['claude', 'codex'])
@pytest.mark.parametrize('valid', [True, False])
@pytest.mark.parametrize('scheme', ['http', 'https'])
def test_use_existing_token_updates_only_selected_client(tmp_path, client, valid, scheme):
    functions = tmp_path / 'setup-functions.sh'
    functions.write_text(SCRIPT.read_text().split('action="${1:-}"')[0])
    claude = tmp_path / 'claude.json'
    codex = tmp_path / 'codex.toml'
    claude.write_text(json.dumps({'mcpServers': {'other': {'command': 'other'}}}))
    codex.write_text('model = "test"\nsandbox_mode = "workspace-write"\n'
                     'approval_policy = "never"\n[mcp_servers.other]\ncommand = "other"\n')
    originals = {p: p.read_bytes() for p in (claude, codex)}
    # Override only paths inside the test shell; no real HOME/config is changed.
    command = '''source "$1"
CLAUDE_JSON="$2"
CODEX_TOML="$3"
MCP_CLIENTS="$4"
MCP_TOKEN=rsmcp_test_abcdefghijklmnopqrstuvwxyz0123456789
MCP_URL="$6://example.test/mcp"
TOKEN_VALID="$5"
token_works() { [ "$TOKEN_VALID" = yes ]; }
api() { exit 77; }
pgrep() { return 1; }
do_use_token
do_use_token
'''
    result = subprocess.run(['bash', '-c', command, 'test', str(functions), str(claude), str(codex),
                             client, 'yes' if valid else 'no', scheme], capture_output=True, text=True, timeout=10)
    if not valid:
        assert 'rsmcp_test_abcdefghijklmnopqrstuvwxyz0123456789' not in result.stdout + result.stderr
        assert result.returncode != 0
        assert all(p.read_bytes() == old for p, old in originals.items())
        return
    assert result.returncode == 0, result.stderr
    assert result.stdout == ''
    assert 'rsmcp_test_abcdefghijklmnopqrstuvwxyz0123456789' in result.stderr
    assert '配置其他机器时，直接粘贴此 Token 即可复用' in result.stderr
    assert '其他机器上的旧 Token 将立即失效' in result.stderr
    assert '请重启 Claude Code / Codex' not in result.stderr
    if client == 'claude':
        data = json.loads(claude.read_text())['mcpServers']
        assert data['other'] == {'command': 'other'}
        entry = data['release-system']
        assert entry == {'type': 'http', 'url': f'{scheme}://example.test/mcp',
                         'headers': {'Authorization': 'Bearer rsmcp_test_abcdefghijklmnopqrstuvwxyz0123456789'}}
        assert codex.read_bytes() == originals[codex]
    else:
        data = tomllib.loads(codex.read_text())
        assert data['model'] == 'test' and data['mcp_servers']['other'] == {'command': 'other'}
        assert data['approval_policy'] == 'never' and data['sandbox_mode'] == 'workspace-write'
        entry = data['mcp_servers']['release-system']
        assert entry['url'] == f'{scheme}://example.test/mcp'
        assert entry['http_headers'] == {'Authorization': 'Bearer rsmcp_test_abcdefghijklmnopqrstuvwxyz0123456789'}
        assert 'command' not in entry
        assert 'required' not in entry
        assert claude.read_bytes() == originals[claude]
        assert 'granular.mcp_elicitations=true' in result.stderr
    selected = claude if client == 'claude' else codex
    assert selected.stat().st_mode & 0o777 == 0o600
    assert Path(str(selected) + '.bak-release-mcp').exists()


@pytest.mark.parametrize('mode', ['generate', 'local', 'paste'])
@pytest.mark.parametrize('color', [True, False])
def test_configure_prints_full_reusable_token_without_changing_existing_tokens(tmp_path, mode, color):
    functions = tmp_path / 'setup-functions.sh'
    functions.write_text(SCRIPT.read_text().split('action="${1:-}"')[0])
    token = 'rsmcp_test_abcdefghijklmnopqrstuvwxyz0123456789'
    calls = tmp_path / 'api-calls'
    calls.write_text('')
    command = '''source "$1"
CLAUDE_JSON="$2/claude.json"
CODEX_TOML="$2/codex.toml"
TEST_MODE="$3"
TEST_TOKEN="$4"
TEST_CALLS="$5"
server_status() {
  username="${USER:-test-user}"
  active_prefix=""; active_line=""
  if [ "$TEST_MODE" != generate ]; then
    active_prefix="${TEST_TOKEN:0:12}"
    active_line="$active_prefix (test)"
  fi
}
local_token() { if [ "$TEST_MODE" = local ]; then printf '%s' "$TEST_TOKEN"; fi; }
ask_secret() { printf '%s' "$TEST_TOKEN"; }
token_works() { [ "$1" = "$TEST_TOKEN" ]; }
api() { printf '%s\\n' "$1" >>"$TEST_CALLS"; printf '%s\\n' "$TEST_TOKEN"; }
detect_clients() { echo codex; }
pgrep() { return 1; }
do_configure
'''
    env = {**os.environ, 'FORCE_COLOR': '1'}
    if color:
        env.pop('NO_COLOR', None)
    else:
        env['NO_COLOR'] = '1'
    result = subprocess.run(['bash', '-c', command, 'test', str(functions), str(tmp_path), mode, token, str(calls)],
                            env=env, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert result.stdout == ''
    assert ('\x1b[' in result.stderr) == color
    plain = re.sub(r'\x1b\[[0-9;]*m', '', result.stderr)
    assert token in [line.strip() for line in plain.splitlines()]
    assert '重要提示' in plain
    assert plain.index('重要提示') < plain.index(token) < plain.index('配置其他机器时')
    assert '配置其他机器时，直接粘贴此 Token 即可复用' in plain
    assert '其他机器上的旧 Token 将立即失效' in plain
    assert '是否继续？[y/N]' not in plain
    assert 'Developer: Reload Window' not in plain
    assert calls.read_text().splitlines() == (['regenerate'] if mode == 'generate' else [])
    configured = tomllib.loads((tmp_path / 'codex.toml').read_text())
    assert configured['mcp_servers']['release-system']['http_headers']['Authorization'] == 'Bearer ' + token


@pytest.mark.parametrize('active', [True, False])
def test_status_shows_the_complete_token_available_in_local_configuration_without_writing(tmp_path, active):
    functions = tmp_path / 'setup-functions.sh'
    functions.write_text(SCRIPT.read_text().split('action="${1:-}"')[0])
    token = 'rsmcp_test_abcdefghijklmnopqrstuvwxyz0123456789'
    codex = tmp_path / 'codex.toml'
    codex.write_text('[mcp_servers.release-system]\nurl = "__BASE_URL__/mcp"\n'
                     f'http_headers = {{ "Authorization" = "Bearer {token}" }}\n')
    original = codex.read_bytes()
    command = '''source "$1"
TEST_TOKEN="$2"
CLAUDE_JSON="$3/claude.json"
CODEX_TOML="$3/codex.toml"
TEST_ACTIVE="$4"
server_status() {
  active_prefix=""; active_line=""
  if [ "$TEST_ACTIVE" = yes ]; then
    active_prefix="${TEST_TOKEN:0:12}"; active_line="$active_prefix (test)"
  fi
}
api() { exit 77; }
token_works() { exit 78; }
do_status
'''
    result = subprocess.run(['bash', '-c', command, 'test', str(functions), token,
                             str(tmp_path), 'yes' if active else 'no'],
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert result.stdout == ''
    assert token in result.stderr
    if active:
        assert '（有效）' in result.stderr
        assert result.stderr.index('重要提示') < result.stderr.index(token) < result.stderr.index('配置其他机器时')
        assert '其他机器上的旧 Token 将立即失效' in result.stderr
    else:
        assert '（已失效）' in result.stderr
    assert codex.read_bytes() == original
    assert not (tmp_path / 'claude.json').exists()
    assert not Path(str(codex) + '.bak-release-mcp').exists()


@pytest.mark.parametrize(('action', 'mode'), [
    ('configure', 'skip'),
    ('regenerate', 'skip'),
    ('configure', 'write-error'),
    ('regenerate', 'missing-python'),
    ('use_token', 'write-error'),
])
def test_full_token_is_displayed_when_local_configuration_is_skipped_or_fails(tmp_path, action, mode):
    functions = tmp_path / 'setup-functions.sh'
    functions.write_text(SCRIPT.read_text().split('action="${1:-}"')[0])
    token = 'rsmcp_test_abcdefghijklmnopqrstuvwxyz0123456789'
    command = '''TEST_MODE="$4"
command() {
  if [[ "${1:-}" == -v && "${2:-}" == python3 && "$TEST_MODE" != write-error ]]; then return 1; fi
  builtin command "$@"
}
python3() { return 49; }
source "$1"
CLAUDE_JSON="$2/claude.json"
CODEX_TOML="$2/codex.toml"
MCP_CLIENTS=codex
MCP_TOKEN="$5"
server_status() { username="${USER:-test-user}"; active_prefix=""; active_line=""; }
token_works() { [ "$1" = "$MCP_TOKEN" ]; }
api() { [ "$1" = regenerate ] && printf '%s\\n' "$MCP_TOKEN"; }
detect_clients() { if [ "$TEST_MODE" != skip ]; then echo codex; fi; }
ask() { if [ "$TEST_MODE" = skip ]; then printf n; else printf y; fi; }
"do_$3"
'''
    result = subprocess.run(['bash', '-c', command, 'test', str(functions), str(tmp_path), action, mode, token],
                            env={**os.environ, 'NO_COLOR': '1'}, capture_output=True, text=True, timeout=10)
    assert result.stderr.splitlines().count(f'   {token}') == 1
    assert '完整 MCP Token' in result.stderr
    assert '配置已写入' not in result.stderr
    assert not (tmp_path / 'claude.json').exists()
    assert not (tmp_path / 'codex.toml').exists()
    if mode == 'skip':
        assert result.returncode == 0, result.stderr
        assert f'Bearer {token}' in result.stdout
    else:
        assert result.returncode != 0
        assert '本机配置写入失败' in result.stderr
        assert result.stdout == ''
    if mode == 'missing-python':
        assert '未检测到 python3' in result.stderr


@pytest.mark.parametrize('action', ['configure', 'regenerate'])
@pytest.mark.parametrize('reply', ['', 'n', 'N', 'invalid', 'y', 'Y'])
def test_replacing_an_active_token_requires_confirmation_before_any_changes(tmp_path, action, reply):
    functions = tmp_path / 'setup-functions.sh'
    functions.write_text(SCRIPT.read_text().split('action="${1:-}"')[0])
    old_token = 'rsmcp_old_abcdefghijklmnopqrstuvwxyz0123456789'
    new_token = 'rsmcp_new_abcdefghijklmnopqrstuvwxyz0123456789'
    claude = tmp_path / 'claude.json'
    codex = tmp_path / 'codex.toml'
    claude.write_text(json.dumps({'mcpServers': {'release-system': {
        'type': 'http', 'url': '__BASE_URL__/mcp',
        'headers': {'Authorization': 'Bearer ' + old_token},
    }}}))
    codex.write_text('[mcp_servers.release-system]\nurl = "__BASE_URL__/mcp"\n'
                     f'http_headers = {{ "Authorization" = "Bearer {old_token}" }}\n')
    originals = {path: path.read_bytes() for path in (claude, codex)}
    calls = tmp_path / 'api-calls'
    calls.write_text('')
    command = '''source "$1"
tty_in=/dev/stdin
CLAUDE_JSON="$2/claude.json"
CODEX_TOML="$2/codex.toml"
TEST_ACTION="$3"
TEST_TOKEN="$4"
TEST_CALLS="$2/api-calls"
server_status() {
  username="${USER:-test-user}"
  active_prefix=rsmcp_active
  active_line="$active_prefix (test)"
}
token_works() { return 1; }
api() { printf '%s\\n' "$1" >>"$TEST_CALLS"; printf '%s\\n' "$TEST_TOKEN"; }
detect_clients() { echo codex; }
pgrep() { return 1; }
"do_$TEST_ACTION"
'''
    # Configure first asks for an existing token; leave it empty to request a new one.
    # After confirming regeneration, the final empty line accepts writing local config.
    answers = ('\n' if action == 'configure' else '') + reply + '\n\n'
    result = subprocess.run(['bash', '-c', command, 'test', str(functions), str(tmp_path), action, new_token],
                            input=answers, env={**os.environ, 'NO_COLOR': '1'},
                            capture_output=True, text=True, timeout=10)
    prompt = '其他机器上的旧 Token 将立即失效，是否继续？[y/N]'
    assert result.stderr.count(prompt) == 1
    assert result.stdout == ''
    assert claude.read_bytes() == originals[claude]
    assert not Path(str(claude) + '.bak-release-mcp').exists()
    if reply.lower() == 'y':
        assert result.returncode == 0, result.stderr
        assert calls.read_text().splitlines() == ['regenerate']
        configured = tomllib.loads(codex.read_text())
        assert configured['mcp_servers']['release-system']['http_headers']['Authorization'] == 'Bearer ' + new_token
        assert Path(str(codex) + '.bak-release-mcp').read_bytes() == originals[codex]
        assert result.stderr.index(prompt) < result.stderr.index('已生成新 Token')
        assert new_token in result.stderr
    else:
        assert result.returncode != 0
        assert '已取消操作' in result.stderr
        assert calls.read_text() == ''
        assert codex.read_bytes() == originals[codex]
        assert not Path(str(codex) + '.bak-release-mcp').exists()
        assert new_token not in result.stderr


@pytest.mark.parametrize('choice,action', [('1', 'configure'), ('2', 'status'), ('3', 'regenerate'), ('4', 'uninstall')])
def test_menu_has_four_actions_without_print_configuration(tmp_path, choice, action):
    prefix, menu = SCRIPT.read_text().split('action="${1:-}"', 1)
    harness = tmp_path / 'menu.sh'
    harness.write_text(prefix + '''
ask() { printf '%s' "$TEST_CHOICE"; }
do_configure() { printf 'called:configure'; }
do_status() { printf 'called:status'; }
do_regenerate() { printf 'called:regenerate'; }
do_uninstall() { printf 'called:uninstall'; }
action="${1:-}"''' + menu)
    result = subprocess.run(['bash', str(harness)], env={**os.environ, 'TEST_CHOICE': choice},
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert result.stdout == 'called:' + action
    assert '2) 查询 Token 状态' in result.stderr
    assert '显示本机完整 Token' in result.stderr
    assert '4) 卸载本机配置' in result.stderr
    assert '仅输出配置方法' not in result.stderr
    assert '5)' not in result.stderr
