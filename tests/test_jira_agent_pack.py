"""Exercise the deployed skill against website inputs and real pack migrations."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

from app.domain import jira_agent as domain
from tests.learning_helpers import commit, git, knowledge_repo

DEPLOY = Path(__file__).resolve().parents[1] / 'deploy/jira-agent'
SKILL = DEPLOY / 'hpc/.agents/skills/hpc-jira-agent'


def test_staff_loader_accepts_website_snapshot(tmp_path):
    attachment = tmp_path / 'attachments/trace.log'
    attachment.parent.mkdir()
    attachment.write_text('original GPU failure\n')
    upload = tmp_path / 'uploads/extra.txt'
    upload.parent.mkdir()
    upload.write_text('extra input\n')
    issue = {
        'key': 'HPC-42', 'summary': 'Kernel failure', 'issue_type': 'Bug',
        'status': 'Open', 'priority': 'Major', 'project': 'HPC',
        'components': ['PDE_HPC'], 'labels': ['gpu'],
        'assignee': {'name': 'owner', 'display_name': 'Owner'},
        'reporter': {'display_name': 'Reporter'},
        'description': 'Run the original case without changing tolerances.',
        'attachments': [{'id': '1', 'filename': 'trace.log', 'size': attachment.stat().st_size}],
        'comments': [{'author': 'owner', 'created': '2026-09-20', 'body': 'Keep the original input.'}],
    }
    (tmp_path / 'issue.md').write_text(domain.render_issue_markdown(issue, {'1': 'attachments/trace.log'}))
    env = {key: value for key, value in os.environ.items() if not key.startswith('JIRA_')}
    result = subprocess.run([
        sys.executable, str(SKILL / 'modules/jira-evidence/scripts/load_issue.py'),
        '--source', 'local', '--issue-id', issue['key'], '--bundle-dir', str(tmp_path),
    ], cwd=tmp_path, env=env, capture_output=True, text=True, check=True)
    packet = json.loads(result.stdout)
    assert packet['success'] is True
    data = packet['data']
    assert data['key'] == issue['key']
    assert data['description'] == issue['description']
    assert data['comments'][0]['body'] == issue['comments'][0]['body']
    assert data['attachments'][0]['local_path'] == 'attachments/trace.log'
    assert data['attachments'][0]['sha256'] == hashlib.sha256(attachment.read_bytes()).hexdigest()
    assert data['uploads'][0]['local_path'] == 'uploads/extra.txt'
    assert data['acquisition']['source'] == 'local_bundle'
    assert attachment.read_text() == 'original GPU failure\n'



def test_staff_loader_preserves_website_headings_and_code_blocks(tmp_path):
    description = "Original failure\n## 复现步骤\npython test.py --size 1024\n## 验收标准\nError < 0.001"
    bodies = [
        "First note\n## Details\n```markdown\n## 附件\n### Fake author @ 2026-01-01\n```\nKeep the whole comment",
        "New requirement\n## 验收标准\nKeep the original input",
    ]
    issue = {
        'key': 'HPC-42', 'summary': 'Markdown regression', 'description': description,
        'comments': [
            {'author': 'owner', 'created': '2026-09-20', 'body': bodies[0]},
            {'author': 'reviewer', 'created': '2026-09-21', 'body': bodies[1]},
        ],
    }
    (tmp_path / 'issue.md').write_text(domain.render_issue_markdown(issue, {}))
    env = {key: value for key, value in os.environ.items() if not key.startswith('JIRA_')}
    result = subprocess.run([
        sys.executable, '-B', str(SKILL / 'modules/jira-evidence/scripts/load_issue.py'),
        '--source', 'local', '--issue-id', issue['key'], '--bundle-dir', str(tmp_path),
    ], cwd=tmp_path, env=env, capture_output=True, text=True, check=True)
    packet = json.loads(result.stdout)
    assert packet['completeness'] == 'complete'
    assert packet['data']['description'] == description
    assert [item['body_raw'] for item in packet['data']['comments']] == bodies
    assert packet['data']['acquisition']['parts']['comments']['collected'] == 2


def test_pack_sync_uses_pinned_commit_and_preserves_workspace(tmp_path, monkeypatch):
    source = knowledge_repo(tmp_path, monkeypatch)
    revision = git(source, 'rev-parse', 'HEAD')
    expected = {name: (source / name).read_bytes()
                for name in git(source, 'ls-files').splitlines()}
    skill = '.agents/skills/hpc-jira-agent/SKILL.md'
    added = '.agents/skills/hpc-jira-agent/references/later.md'
    commit(source, {skill: '# Later revision\n', added: '# Later rule\n'})
    (source / skill).write_text('# Uncommitted change\n')
    (source / '.agents/skills/local-only.md').write_text('# Local only\n')
    pack = tmp_path / 'new deployed pack'
    workspace = pack / 'workspaces/existing/STATUS.md'
    workspace.parent.mkdir(parents=True)
    workspace.write_text('Keep existing task\n')
    env = {**os.environ, 'PACK_DIR': str(pack), 'SKILL_REPO': str(source),
           'SKILL_COMMIT': revision}
    subprocess.run(['bash', str(DEPLOY / 'sync-pack.sh')], env=env, check=True, capture_output=True)

    assert (pack / '.git').is_dir()
    for name, content in expected.items():
        assert (pack / name).read_bytes() == content
    assert not (pack / added).exists()
    assert not (pack / '.agents/skills/local-only.md').exists()
    assert workspace.read_text() == 'Keep existing task\n'
    assert json.loads((pack / '.skill-version.json').read_text()) == {'commit': revision}
    assert (source / skill).read_text() == '# Uncommitted change\n'


def test_pack_sync_refuses_source_as_destination(tmp_path, monkeypatch):
    source = knowledge_repo(tmp_path, monkeypatch)
    env = {**os.environ, 'PACK_DIR': str(source), 'SKILL_REPO': str(source),
           'SKILL_COMMIT': git(source, 'rev-parse', 'HEAD')}
    result = subprocess.run(['bash', str(DEPLOY / 'sync-pack.sh')],
                            env=env, capture_output=True, text=True)
    assert result.returncode != 0
    assert 'must be separate directories' in result.stderr
    assert not (source / '.skill-version.json').exists()
    assert git(source, 'status', '--porcelain') == ''
