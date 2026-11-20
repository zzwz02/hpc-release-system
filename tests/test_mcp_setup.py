"""MCP setup token reuse, regeneration confirmation, and local configuration."""
import json
import os
import re
import shutil
import subprocess
import tomllib
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / 'app/resources/mcp/setup.sh'
POWERSHELL_SCRIPT = SCRIPT.with_suffix('.ps1')
ORIGIN = 'https://release.example:8443'
TOKEN = 'rsmcp_test_abcdefghijklmnopqrstuvwxyz0123456789'


@pytest.fixture(autouse=True)
def installer_profile(tmp_path, monkeypatch):
    """Keep every client lookup and write within the test user's profile."""
    profile = tmp_path / 'test profile'
    profile.mkdir()
    monkeypatch.setenv('HOME', str(profile))
    monkeypatch.setenv('USERPROFILE', str(profile))
    for key, subdir in {
        'APPDATA': 'appdata',
        'LOCALAPPDATA': 'localappdata',
        'XDG_CONFIG_HOME': '.config',
        'XDG_CACHE_HOME': '.cache',
        'XDG_DATA_HOME': '.local/share',
        'CODEX_HOME': '.codex',
        'DSH_HOME': '.dsh',
    }.items():
        monkeypatch.setenv(key, str(profile / subdir))
    for key in ('MCP_ACTION', 'MCP_CLIENTS', 'MCP_TOKEN', 'OPENCODE_CONFIG'):
        monkeypatch.delenv(key, raising=False)
    return profile


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
        assert 'granular.mcp_elicitations=true' not in result.stderr
    selected = claude if client == 'claude' else codex
    written_lines = [line for line in result.stderr.splitlines() if '配置已写入' in line]
    assert len(written_lines) == 2  # The test runs use-token twice.
    assert all(line.endswith(f'配置已写入  {selected}') for line in written_lines)
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
    if color:
        assert '\x1b[36m服务地址：' in result.stderr
        assert '\x1b[1;32m✔\x1b[0m \x1b[36mCodex' in result.stderr
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


@pytest.fixture(params=['bash', 'powershell'])
def run_installer(request, tmp_path, installer_profile):
    if request.param == 'bash':
        shell = shutil.which('bash')
        if not shell:
            pytest.skip('Bash is required to execute the Bash installer')
        source = SCRIPT.read_text(encoding='utf-8').replace('__BASE_URL__', ORIGIN)
        functions, entrypoint = source.split('action="${1:-}"', 1)
        script = tmp_path / 'setup.sh'
        script.write_text(functions + '''
api() { printf 'unexpected account operation\n' >&2; exit 70; }
server_status() { active_prefix="${MCP_TOKEN:0:12}"; active_line="$active_prefix"; }
token_works() { [ "$1" = "$MCP_TOKEN" ] && [ "$TEST_MCP_PING_STATUS" = 200 ]; }
pgrep() { return 1; }
action="${1:-}"''' + entrypoint, encoding='utf-8')
        command = [shell, str(script)]
    else:
        shell = shutil.which('pwsh') or shutil.which('powershell')
        if not shell:
            pytest.skip('PowerShell is required to execute the PowerShell installer')
        script = tmp_path / 'setup.ps1'
        script.write_text(
            POWERSHELL_SCRIPT.read_text(encoding='utf-8').replace('__BASE_URL__', ORIGIN),
            encoding='utf-8-sig',
        )
        driver = tmp_path / 'run_setup.ps1'
        driver.write_text(r'''
param([string]$Installer, [string]$Action)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
if ($HOME -ne $env:TEST_MCP_PROFILE) { throw 'PowerShell test profile is not isolated' }

function Read-Host {
    param([switch]$AsSecureString)
    if ($AsSecureString) {
        return ConvertTo-SecureString 'abc+测试=&' -AsPlainText -Force
    }
    return 'test-user'
}

function Invoke-WebRequest {
    param([switch]$UseBasicParsing, $Uri, $Method, $Headers, $ContentType, $Body)
    if ($Method -eq 'Post' -and $Uri -eq "$env:TEST_MCP_ORIGIN/mcp" -and
        $Headers.Authorization -ceq "Bearer $env:MCP_TOKEN" -and
        ($Body | ConvertFrom-Json).method -eq 'ping') {
        return @{ StatusCode = [int]$env:TEST_MCP_PING_STATUS }
    }
    if ($Method -eq 'Post' -and $Uri -eq "$env:TEST_MCP_ORIGIN/api/mcp/account" -and
        $Headers.Accept -eq 'text/plain') {
        $form = [System.Text.Encoding]::UTF8.GetString($Body)
        if ($form.StartsWith('action=status&username=test-user&') -and
            $form.EndsWith('&password=abc%2B%E6%B5%8B%E8%AF%95%3D%26')) {
            $prefix = $env:MCP_TOKEN.Substring(0, 12)
            return @{ StatusCode = 200; Content = "active`t$prefix"; RawContentStream = $null }
        }
    }
    throw 'Unexpected installer HTTP request'
}

if ($env:TEST_MCP_INLINE -eq '1') {
    & ([scriptblock]::Create([System.IO.File]::ReadAllText($Installer))) $Action
    Write-Output 'PowerShell session continued'
    return
}
$LASTEXITCODE = 0
& $Installer $Action
exit $LASTEXITCODE
''', encoding='utf-8-sig')
        command = [
            shell, '-NoLogo', '-NoProfile', '-NonInteractive', '-File', str(driver), str(script),
        ]

    def run(action, clients='all', ping_status=200, inline=False):
        return subprocess.run(
            [*command, action],
            env={
                **os.environ,
                'MCP_CLIENTS': clients,
                'MCP_TOKEN': TOKEN,
                'TEST_MCP_ORIGIN': ORIGIN,
                'TEST_MCP_PROFILE': str(installer_profile),
                'TEST_MCP_PING_STATUS': str(ping_status),
                'TEST_MCP_INLINE': '1' if inline else '0',
                'NO_COLOR': '1',
            },
            capture_output=True, text=True, encoding='utf-8', timeout=20,
        )

    return run


def test_installers_write_find_and_remove_all_supported_clients(
    run_installer, installer_profile, monkeypatch,
):
    profile = installer_profile
    endpoint = ORIGIN + '/mcp'
    headers = {'Authorization': f'Bearer {TOKEN}'}
    codebuddy_storage = (
        profile / '.config/Code/User/globalStorage/tencent-cloud.coding-copilot/settings'
    )
    opencode = profile / 'custom OpenCode/opencode.json'
    monkeypatch.setenv('OPENCODE_CONFIG', str(opencode))
    global_opencode = profile / '.config/opencode/opencode.jsonc'
    global_opencode_original = json.dumps({'mcp': {
        'other': {'command': 'other'},
        'release-system': {'type': 'remote', 'url': endpoint, 'headers': headers},
    }}).encode()
    global_opencode.parent.mkdir(parents=True)
    global_opencode.write_bytes(global_opencode_original)
    json_configs = {
        profile / '.claude.json': ('mcpServers', {'type': 'http'}),
        profile / '.cursor/mcp.json': ('mcpServers', {}),
        opencode: ('mcp', {'type': 'remote', 'enabled': True, 'oauth': False, 'timeout': 30000}),
        profile / '.codebuddy/.mcp.json': ('mcpServers', {'type': 'http'}),
        codebuddy_storage / 'codebuddy_mcp_settings.json': ('mcpServers', {'type': 'http'}),
    }
    codex = profile / '.codex/config.toml'
    dsh = profile / '.dsh/cordis.patch.yml'
    expected_writes = {
        ('Claude Code', profile / '.claude.json'),
        ('Codex', codex),
        ('Cursor', profile / '.cursor/mcp.json'),
        ('OpenCode', opencode),
        ('CodeBuddy', profile / '.codebuddy/.mcp.json'),
        ('CodeBuddy', codebuddy_storage / 'codebuddy_mcp_settings.json'),
        ('DeepSeek Harness', dsh),
    }
    initial = {
        path: json.dumps({section: {'other': {'command': 'other'}}})
        for path, (section, _) in json_configs.items()
    }
    initial[opencode] = json.dumps({'mcp': {
        'other': {'command': 'other'},
        'release-system': {'type': 'remote', 'url': endpoint, 'headers': headers},
    }})
    codex_original = 'model = "test"\n[mcp_servers.other]\ncommand = "other"\n'
    dsh_original = '- insert:\n    - id: other\n      name: other-module\n'
    initial.update({codex: codex_original, dsh: dsh_original})
    for path, content in initial.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding='utf-8')
    original_bytes = {path: path.read_bytes() for path in initial}

    for action in ('use-token', 'configure'):
        result = run_installer(action)
        output = result.stdout + result.stderr
        assert result.returncode == 0, output
        written_lines = [line for line in output.splitlines() if ' 配置已写入  ' in line]
        assert len(written_lines) == len(expected_writes), output
        assert len({line.index('配置已写入') for line in written_lines}) == 1, output
        assert {
            (label.removeprefix('✔ ').strip(), Path(path))
            for label, path in (line.split(' 配置已写入  ', 1) for line in written_lines)
        } == expected_writes
        assert 'Codex：若发评论时没有用户名弹窗' not in output
        assert TOKEN in output
        for path, (section, values) in json_configs.items():
            assert json.loads(path.read_bytes())[section] == {
                'other': {'command': 'other'},
                'release-system': {'url': endpoint, 'headers': headers, **values},
            }
        settings = tomllib.loads(codex.read_text(encoding='utf-8'))
        assert settings['model'] == 'test'
        assert settings['mcp_servers'] == {
            'other': {'command': 'other'},
            'release-system': {
                'url': endpoint,
                'http_headers': headers,
                'enabled': True,
                'startup_timeout_sec': 30,
            },
        }
        text = dsh.read_text(encoding='utf-8')
        assert text.startswith(dsh_original)
        assert text.count('id: mcp-release-system') == 1
        assert 'transport: streamable-http' in text
        assert f"url: '{endpoint}'" in text
        assert f"Authorization: 'Bearer {TOKEN}'" in text
        if action == 'use-token':
            for path, content in original_bytes.items():
                assert path.with_name(path.name + '.bak-release-mcp').read_bytes() == content
        else:
            assert '检测到本机已有有效 Token' in output

    configured = {path: path.read_bytes() for path in initial}
    result = run_installer('status')
    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert output.count('（有效）') == 6, output
    assert TOKEN in output
    assert all(path.read_bytes() == content for path, content in configured.items())
    assert global_opencode.read_bytes() == global_opencode_original

    result = run_installer('uninstall')
    assert result.returncode == 0, result.stdout + result.stderr
    for path, (section, _) in json_configs.items():
        assert json.loads(path.read_bytes()) == {section: {'other': {'command': 'other'}}}
    assert codex.read_text(encoding='utf-8') == codex_original
    assert dsh.read_text(encoding='utf-8') == dsh_original
    assert json.loads(global_opencode.read_bytes()) == {'mcp': {'other': {'command': 'other'}}}
    assert global_opencode.with_suffix('.jsonc.bak-release-mcp').read_bytes() == global_opencode_original
    for path, content in configured.items():
        assert path.with_name(path.name + '.bak-release-mcp').read_bytes() == content


def test_installers_validate_token_and_select_client_alias(run_installer, installer_profile):
    profile = installer_profile
    dsh = profile / '.dsh/cordis.patch.yml'
    result = run_installer('use-token', clients='deepseek-harness', ping_status=401)
    assert result.returncode != 0
    assert 'Token 无效' in result.stdout + result.stderr
    assert TOKEN not in result.stdout + result.stderr
    assert not dsh.exists()

    result = run_installer('use-token', clients='deepseek-harness,DSH')
    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert output.count('配置已写入') == 1, output
    assert f'DeepSeek Harness 配置已写入  {dsh}' in output
    assert TOKEN in dsh.read_text(encoding='utf-8')
    for path in ('.claude.json', '.codex', '.cursor', '.config/opencode', '.codebuddy'):
        assert not (profile / path).exists()


@pytest.mark.parametrize('highest_existing', [0, 1, 2])
def test_installers_use_priority_for_setup_and_remove_all_configs(
    run_installer, installer_profile, highest_existing,
):
    profile = installer_profile
    candidates = {
        'mcp': [profile / '.config/opencode' / name
                for name in ('opencode.jsonc', 'opencode.json', 'config.json')],
        'mcpServers': [profile / name
                       for name in ('.codebuddy/.mcp.json', '.codebuddy/mcp.json', '.codebuddy.json')],
    }
    other_line = (
        '    "other": {"url": "https://example.test/a//b/*literal*/", "values": [1, 2,],},'
        ' // Preserve this comment\r\n'
    )
    selected = {paths[highest_existing] for paths in candidates.values()}
    originals = {}
    unselected = {}
    for section, paths in candidates.items():
        target = paths[highest_existing]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((
            '{\r\n'
            '  /* Preserve the comment above the section. */\r\n'
            f'  "{section}": {{\r\n'
            + other_line +
            '  },\r\n'
            '}\r\n'
        ).encode())
        originals[target] = target.read_bytes()
        for lower in paths[highest_existing + 1:]:
            lower.parent.mkdir(parents=True, exist_ok=True)
            content = json.dumps({section: {
                'other': {'command': 'other'},
                'release-system': {'headers': {'Authorization': f'Bearer {TOKEN}'}},
            }}).encode()
            lower.write_bytes(content)
            unselected[lower] = (section, content)

    result = run_installer('status')
    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert '尚未配置 release-system' in output
    assert TOKEN not in output

    result = run_installer('use-token', clients='opencode,codebuddy')
    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert output.count('配置已写入') == 2, output
    for target in selected:
        assert str(target) in output
        updated = target.read_bytes().decode()
        assert f'"Authorization": "Bearer {TOKEN}"' in updated
        assert other_line in updated
        assert '/* Preserve the comment above the section. */\r\n' in updated
        assert target.with_name(target.name + '.bak-release-mcp').read_bytes() == originals[target]

    result = run_installer('status')
    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert output.count('（有效）') == 2, output
    assert TOKEN in output
    for path, (_, before) in unselected.items():
        assert path.read_bytes() == before
        assert not path.with_name(path.name + '.bak-release-mcp').exists()

    result = run_installer('uninstall')
    assert result.returncode == 0, result.stdout + result.stderr
    for target in selected:
        updated = target.read_bytes().decode()
        assert '"release-system"' not in updated
        assert other_line in updated
    for path, (section, before) in unselected.items():
        assert json.loads(path.read_bytes()) == {section: {'other': {'command': 'other'}}}
        assert path.with_name(path.name + '.bak-release-mcp').read_bytes() == before
    assert all(not path.exists() for paths in candidates.values() for path in paths[:highest_existing])


@pytest.mark.parametrize('lower_name', ['opencode.json', 'config.json'])
def test_installers_reuse_token_and_update_existing_opencode_file(
    run_installer, installer_profile, lower_name,
):
    directory = installer_profile / '.config/opencode'
    directory.mkdir(parents=True)
    lower = directory / lower_name
    preferred = directory / 'opencode.jsonc'
    original = {
        '$schema': 'https://opencode.ai/config.json',
        'mcp': {
            'other': {'type': 'local', 'command': ['other']},
            'release-system': {
                'type': 'remote',
                'url': ORIGIN + '/mcp',
                'headers': {'Authorization': f'Bearer {TOKEN}'},
            },
        },
    }
    lower.write_text(json.dumps(original) + '\n', encoding='utf-8')
    before = lower.read_bytes()

    result = run_installer('configure', clients='opencode')
    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert '检测到本机已有有效 Token' in output
    assert f'配置已写入  {lower}' in output
    assert not preferred.exists()
    installed = lower.read_bytes()
    entry = json.loads(installed)['mcp']['release-system']
    assert entry['headers'] == {
        'Authorization': f'Bearer {TOKEN}',
    }
    assert entry['oauth'] is False and entry['timeout'] == 30000
    backup = lower.with_name(lower.name + '.bak-release-mcp')
    assert backup.read_bytes() == before

    result = run_installer('uninstall')
    assert result.returncode == 0, result.stdout + result.stderr
    del original['mcp']['release-system']
    assert json.loads(lower.read_bytes()) == original
    assert backup.read_bytes() == installed


def test_installers_create_preferred_config_when_none_exists(run_installer, installer_profile):
    result = run_installer('use-token', clients='opencode,codebuddy')
    assert result.returncode == 0, result.stdout + result.stderr
    opencode = installer_profile / '.config/opencode/opencode.jsonc'
    codebuddy = installer_profile / '.codebuddy/.mcp.json'
    config = json.loads(opencode.read_bytes())
    assert config['$schema'] == 'https://opencode.ai/config.json'
    assert config['mcp']['release-system']['oauth'] is False
    assert json.loads(codebuddy.read_bytes())['mcpServers']['release-system']['type'] == 'http'


@pytest.mark.parametrize('run_installer', ['powershell'], indirect=True)
def test_powershell_preserves_keys_that_differ_only_by_case(run_installer, installer_profile):
    configs = {
        installer_profile / '.claude.json': 'mcpServers',
        installer_profile / '.cursor/mcp.json': 'mcpServers',
        installer_profile / '.config/opencode/opencode.jsonc': 'mcp',
        installer_profile / '.codebuddy/.mcp.json': 'mcpServers',
    }
    projects = {
        'C:/Users/example/release-system': {'setting': 1},
        'c:/Users/example/release-system': {'setting': 'quote: " UTF-8: 配置 \u0000'},
        '': {'setting': 1.25e-8},
    }
    originals = {}
    for path, section in configs.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            'projects': projects,
            section: {
                'other': {'command': 'other'},
                'release-system': {'headers': {'authorization': f'Bearer {TOKEN}'}},
            },
        }, indent=2) + '\n', encoding='utf-8')
        originals[path] = path.read_bytes()

    result = run_installer('status')
    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert output.count('（有效）') == len(configs), output
    assert TOKEN in output

    result = run_installer('configure', clients='claude,cursor,opencode,codebuddy')
    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert '检测到本机已有有效 Token' in output
    assert output.count('配置已写入') == len(configs), output
    for path, section in configs.items():
        data = json.loads(path.read_bytes())
        assert data['projects'] == projects
        assert data[section]['release-system']['headers'] == {'Authorization': f'Bearer {TOKEN}'}
        assert path.with_name(path.name + '.bak-release-mcp').read_bytes() == originals[path]

    result = run_installer('uninstall')
    assert result.returncode == 0, result.stdout + result.stderr
    for path, section in configs.items():
        assert json.loads(path.read_bytes()) == {
            'projects': projects,
            section: {'other': {'command': 'other'}},
        }


@pytest.mark.parametrize('run_installer', ['powershell'], indirect=True)
def test_powershell_inline_error_leaves_session_running(run_installer):
    result = run_installer('use-token', ping_status=401, inline=True)
    assert result.returncode == 0, result.stderr
    assert result.stderr == ''
    assert 'Token 无效' in result.stdout
    assert 'PowerShell session continued' in result.stdout
