"""Small real Git fixtures shared by knowledge workflow tests."""
import subprocess
from pathlib import Path

from app.config import settings


def git(root: Path, *args: str) -> str:
    return subprocess.check_output(['git', '-C', str(root), *args], text=True).strip()


def commit(root: Path, files: dict[str, str], message: str = 'test') -> str:
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    git(root, 'add', '.')
    git(root, 'commit', '-qm', message)
    return git(root, 'rev-parse', 'HEAD')


def knowledge_repo(tmp_path, monkeypatch):
    root = tmp_path / 'knowledge'
    root.mkdir()
    git(root, 'init', '-q', '-b', 'main')
    git(root, 'config', 'user.name', 'Learning Test')
    git(root, 'config', 'user.email', 'learning@example.invalid')
    commit(root, {'AGENTS.md': '# Identity\n', '.gitignore': 'workspaces/\n',
                  '.agents/skills/hpc-jira-agent/SKILL.md': '# HPC\n\n[Reference](references/rule.md)\n',
                  '.agents/skills/hpc-jira-agent/references/rule.md': '# Rule\n'})
    monkeypatch.setattr(settings, 'jira_learning_repo_root', root)
    return root
