"""Read a consistent project skill snapshot from the analysis app-server."""
import json
import posixpath

from app.integrations import knowledge_repository as repository
from app.integrations.codex_app_server import CodexAppServerClient
from app.timeutil import beijing_timestamp

# A fixed read-only command. No user input is interpolated into executable text.
_READ_PACK = r'''
import json, pathlib, sys
root = pathlib.Path(sys.argv[1]).resolve()
complete = sys.argv[2] == 'complete'
# Sites may link both entry points to the same shared knowledge pack.
# Resolve that whole pack; individual resource symlinks remain unsupported.
if (root / 'AGENTS.md').is_symlink() and (root / '.agents').is_symlink():
    identity = (root / 'AGENTS.md').resolve()
    shared = identity.parent
    if identity.name == 'AGENTS.md' and (shared / '.agents').resolve() == (root / '.agents').resolve():
        root = shared
def read():
    files = {}
    executable = []
    size = 0
    paths = [root / 'AGENTS.md'] + sorted((root / '.agents/skills').rglob('*'))
    for path in paths:
        if '__pycache__' in path.parts or path.suffix == '.pyc':
            continue
        if path.is_symlink():
            if complete:
                raise RuntimeError('skill symlinks are not supported')
            continue
        if not path.is_file() or (not complete and path.suffix not in ('.md', '.py', '.sh')):
            continue
        if not path.resolve().is_relative_to(root):
            continue
        data = path.read_bytes()
        size += len(data)
        if len(data) > 524288 or size > 8388608:
            raise RuntimeError('knowledge pack too large')
        files[path.relative_to(root).as_posix()] = data.decode('utf-8')
        if path.stat().st_mode & 0o111:
            executable.append(path.relative_to(root).as_posix())
    return files, executable
for attempt in range(3):
    first = read()
    second = read()
    if first == second:
        version = root / '.skill-version.json'
        metadata = json.loads(version.read_text()) if version.is_file() and not version.is_symlink() else {}
        print(json.dumps({'files': first[0], 'executable': first[1], 'deployed_commit': metadata.get('commit', '')}, ensure_ascii=False))
        break
else:
    raise RuntimeError('knowledge pack changed during collection')
'''


async def capture(config, *, complete: bool = False) -> dict:
    root = posixpath.dirname(config.workspace_root.rstrip('/'))
    async with CodexAppServerClient(config.ws_url, config.ws_token, client_name='knowledge_snapshot') as client:
        # The script already limits pack size; the server's default output cap
        # can truncate a valid snapshot before its JSON has finished.
        result = await client.request('command/exec', {
            'command': ['python3', '-c', _READ_PACK, root, 'complete' if complete else 'reference'],
            'timeoutMs': 30000, 'disableOutputCap': True,
        }, timeout=60)
    if result.get('exitCode') != 0:
        if complete:
            raise ValueError('分析服务 skill 采集失败，请重试')
        raise ValueError('分析服务 skill 采集失败；可重试或明确选择仅参考仓库版本')
    try:
        data = json.loads(result.get('stdout') or '{}')
    except json.JSONDecodeError as exc:
        raise ValueError('分析服务 skill 快照不完整，无法使用；请重试采集') from exc
    files = data.get('files') or {}
    if 'AGENTS.md' not in files or repository.PREFIX + 'SKILL.md' not in files:
        raise ValueError('分析服务返回的知识包不完整')
    if any(not repository.allowed(path, complete=complete) for path in files):
        raise ValueError('分析服务返回了知识目录以外的文件')
    executable = data.get('executable', [])
    if any(path not in files for path in executable):
        raise ValueError('分析服务返回的脚本路径无效')
    return {
        'executable': executable, 'files': files, 'hashes': repository.hashes(files), 'server': config.ws_url,
        'captured_at': beijing_timestamp(), 'deployed_commit': data.get('deployed_commit') or '',
    }
