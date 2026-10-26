"""Read an explicitly configured knowledge checkout without modifying it."""
from __future__ import annotations

import hashlib
import re
import subprocess
from pathlib import Path, PurePosixPath

from app.config import settings

PREFIX = ".agents/skills/hpc-jira-agent/"
MAX_FILE_BYTES = 512 * 1024
MAX_PACK_BYTES = 8 * 1024 * 1024


def allowed(path: str, *, writable: bool = False, complete: bool = False) -> bool:
    pure = PurePosixPath(path)
    if pure.is_absolute() or '..' in pure.parts or str(pure) != path or '\\' in path:
        return False
    if writable:
        return path.startswith(PREFIX) and path.endswith('.md')
    return path == 'AGENTS.md' or (
        path.startswith('.agents/skills/') and (complete or pure.suffix in ('.md', '.py', '.sh'))
    )


def git(*args: str, root: Path | None = None) -> str:
    root = (root or settings.jira_learning_repo_root).resolve()
    if not root.is_dir():
        raise ValueError("skill 仓库不存在，请检查配置的仓库路径")
    result = subprocess.run(
        ['git', '-c', f'safe.directory={root}', *args], cwd=root,
        capture_output=True, timeout=30,
    )
    if result.returncode:
        raise ValueError("无法读取知识仓库版本；请更新本地仓库缓存并核对正式分支配置")
    return result.stdout.decode('utf-8')


def resolve(ref: str, *, root: Path | None = None) -> str:
    if not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9._/\-]*', ref):
        raise ValueError("知识仓库版本格式无效")
    return git('rev-parse', '--verify', f'{ref}^{{commit}}', root=root).strip()


def snapshot(ref: str, *, root: Path | None = None, complete: bool = False) -> dict:
    commit = resolve(ref, root=root)
    entries = git('ls-tree', '-rz', commit, root=root).split('\0')
    files = {}
    executable = []
    total = 0
    for entry in entries:
        if not entry:
            continue
        header, path = entry.split('\t', 1)
        mode, kind, oid = header.split()
        included = allowed(path)
        if complete:
            pure = PurePosixPath(path)
            included = path == 'AGENTS.md' or path.startswith('.agents/skills/')
            if included and (pure.is_absolute() or '..' in pure.parts or '\\' in path
                             or str(pure) != path or mode not in ('100644', '100755')):
                raise ValueError('测试 skill 包含不支持的路径、软链接或子模块')
        if kind != 'blob' or mode not in ('100644', '100755') or not included:
            continue
        size = int(git('cat-file', '-s', oid, root=root).strip())
        total += size
        if size > MAX_FILE_BYTES or total > MAX_PACK_BYTES:
            raise ValueError('知识文件超过大小限制')
        try:
            files[path] = git('cat-file', 'blob', oid, root=root)
        except UnicodeDecodeError as exc:
            raise ValueError('skill 包含非 UTF-8 资源，当前测试快照不支持该文件') from exc
        if mode == '100755':
            executable.append(path)
    if 'AGENTS.md' not in files or PREFIX + 'SKILL.md' not in files:
        raise ValueError('独立知识仓库缺少 AGENTS.md 或 HPC SKILL.md')
    return {'commit': commit, 'ref': ref, 'files': files, 'hashes': hashes(files), 'executable': executable}


def hashes(files: dict[str, str]) -> dict[str, str]:
    return {path: hashlib.sha256(text.encode()).hexdigest() for path, text in files.items()}


def verify_commit(commit: str, ref: str, files: list[dict], base_commit: str) -> str:
    resolved = resolve(commit)
    head = resolve(ref)
    # rev-list is portable and does not require interpreting an error as success.
    if resolved not in set(git('rev-list', head).splitlines()):
        raise ValueError('该提交不在配置的正式分支上')
    if base_commit not in set(git('rev-list', resolved).splitlines()):
        raise ValueError('该提交不包含提案基线，请重新核对实际修改')
    current = snapshot(resolved)['files']
    changed = set(git('diff', '--name-only', base_commit, resolved).splitlines())
    for item in files:
        if item['path'] not in changed or current.get(item['path']) != item['updated_text']:
            raise ValueError('实际提交与已审核内容不一致；请按实际提交生成新版本并重新审核')
    return resolved
