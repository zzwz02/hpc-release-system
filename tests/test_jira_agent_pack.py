"""Exercise the deployed skill against website inputs and real pack migrations."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

from app.domain import jira_agent as domain

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


def test_pack_sync_copies_fresh_knowledge_pack(tmp_path):
    pack = tmp_path / 'new deployed pack'
    env = {**os.environ, 'PACK_DIR': str(pack)}
    subprocess.run(['bash', str(DEPLOY / 'sync-pack.sh')], env=env, check=True, capture_output=True)

    assert (pack / 'workspaces').is_dir()
    assert (pack / '.git').is_dir()
    assert (pack / 'AGENTS.md').read_bytes() == (DEPLOY / 'hpc/AGENTS.md').read_bytes()
    assert (pack / '.gitignore').read_bytes() == (DEPLOY / 'hpc/.gitignore').read_bytes()
    for source in SKILL.rglob('*'):
        if source.is_file() and '__pycache__' not in source.parts:
            assert (pack / '.agents/skills/hpc-jira-agent' / source.relative_to(SKILL)).read_bytes() == source.read_bytes()


def test_pack_sync_refuses_source_as_destination():
    result = subprocess.run(['bash', str(DEPLOY / 'sync-pack.sh')],
                            env={**os.environ, 'PACK_DIR': str(DEPLOY / 'hpc')},
                            capture_output=True, text=True)
    assert result.returncode != 0
    assert 'outside the source' in result.stderr
