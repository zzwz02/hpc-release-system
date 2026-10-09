"""Exercise real Jira HTTP collection and the shipped JSON loader end to end."""
from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest

from app.config import settings
from app.domain import jira_agent as domain
from app.integrations import jira

LOADER = Path(__file__).resolve().parents[1] / 'deploy/jira-agent/hpc/.agents/skills/hpc-jira-agent/modules/jira-evidence/scripts/load_issue.py'


@pytest.fixture()
def source(tmp_path, monkeypatch):
    comments = [{'id': str(i), 'author': {'name': 'owner', 'displayName': 'Owner'},
                 'created': f'2026-09-{10+i:02d}T01:00:00+0800', 'updated': None,
                 'body': f'Comment {i}\n## Details\nKeep the full body',
                 'visibility': {'type': 'group', 'value': 'test-group'}} for i in range(1, 6)]
    raw = {'key': 'HPC-42', 'fields': {
        'summary': 'Snapshot round trip', 'description': 'Original description\n## 附件\nThis is a body heading\n\n```\n## 评论\n```',
        'issuetype': {'name': 'Bug'}, 'status': {'name': 'Open'},
        'project': {'name': 'HPC', 'key': 'HPC'}, 'priority': {'name': 'Major'},
        'assignee': {'name': 'owner', 'displayName': 'Owner'}, 'attachment': [],
        'comment': {'comments': comments[:1], 'total': 5},
        'customfield_1': {'value': 'MACA 3.0'}, 'customfield_2': None, 'customfield_3': [],
    }}
    definitions = [{'id': f'customfield_{i}', 'name': 'Same display name', 'schema': {'type': 'string'}} for i in range(1, 4)]
    state = SimpleNamespace(mode='normal', calls=[], raw=raw, comments=comments, definitions=definitions)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            if self.headers.get('Authorization') != 'Bearer test-snapshot-token':
                self.send_error(401)
                return
            route = urlsplit(self.path)
            state.calls.append(self.path)
            if route.path == '/rest/api/2/field':
                if state.mode == 'field_failure':
                    self.send_error(503)
                    return
                data = state.definitions
            elif route.path.endswith('/comment'):
                offset = int(parse_qs(route.query)['startAt'][0])
                if state.mode == 'first_page_failure' or state.mode == 'second_page_failure' and offset == 2:
                    self.send_error(503)
                    return
                items = copy.deepcopy(state.comments[offset:offset + 2])
                data = {'startAt': offset, 'total': len(state.comments), 'comments': items}
                if offset == 2 and state.mode == 'changed_total':
                    data['total'] += 1
                if offset == 2 and state.mode == 'duplicate_ids':
                    items[0]['id'] = '1'
                if offset == 2 and state.mode == 'empty_page':
                    data['comments'] = []
                if offset == 2 and state.mode == 'wrong_offset':
                    data['startAt'] = 0
                if state.mode == 'page_budget':
                    data = {'startAt': offset, 'total': 101, 'comments': [{**state.comments[0], 'id': str(offset)}]}
            else:
                data = state.raw
            body = json.dumps(data, ensure_ascii=False).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    state.origin = f'http://127.0.0.1:{server.server_port}'
    state.conf = tmp_path / 'release_system.conf'
    state.conf.write_text(f'[jira]\nJIRA_BASE_URL = {state.origin}\nJIRA_TOKEN = test-snapshot-token\n')
    monkeypatch.setattr(settings, 'runtime_conf_path', state.conf)
    try:
        yield state
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def collect(source):
    return jira.get_issue_snapshot('HPC-42')


def write_bundle(tmp_path, issue, paths=None):
    markdown = domain.render_issue_markdown(issue, paths or {})
    packet = domain.build_issue_snapshot(issue, paths or {}, markdown)
    (tmp_path / 'issue.md').write_text(markdown)
    (tmp_path / 'issue.json').write_text(json.dumps(packet, ensure_ascii=False))
    return packet


def load_bundle(tmp_path, source='local'):
    env = {key: value for key, value in os.environ.items() if not key.startswith('JIRA_')}
    result = subprocess.run([sys.executable, '-B', str(LOADER), '--issue-id', 'HPC-42',
                             '--source', source, '--bundle-dir', str(tmp_path),
                             '--base-url', 'http://127.0.0.1:1', '--timeout', '1'],
                            env=env, capture_output=True, text=True, timeout=10)
    packet = json.loads(result.stdout)
    assert result.returncode == (0 if packet['success'] else 1)
    return packet


def test_full_collection_preserves_fields_and_all_comment_pages(source):
    issue = collect(source)
    assert issue['custom_fields']['customfield_1']['value'] == {'value': 'MACA 3.0'}
    assert issue['custom_fields']['customfield_2']['value'] is None
    assert issue['custom_fields']['customfield_3']['value'] == []
    assert len(issue['custom_fields']) == 3
    assert [item['id'] for item in issue['comments']] == ['1', '2', '3', '4', '5']
    assert issue['comments'][-1]['body_raw'] == source.comments[-1]['body']
    assert issue['comments'][-1]['visibility'] == source.comments[-1]['visibility']
    assert issue['acquisition']['status'] == 'complete'
    assert [part['startAt'] for part in issue['acquisition']['parts']['comments']['pages']] == [0, 2, 4]
    assert source.calls[0] == '/rest/api/2/issue/HPC-42'


def test_permission_lookup_remains_one_lightweight_request(source):
    jira.get_issue('HPC-42')
    assert len(source.calls) == 1
    assert '?fields=summary,' in source.calls[0]


@pytest.mark.parametrize('mode,code', [
    ('second_page_failure', 'http_503'), ('changed_total', 'comment_total_changed'),
    ('duplicate_ids', 'comment_ids_missing_or_repeated'), ('empty_page', 'comment_page_ended_early'),
    ('wrong_offset', 'invalid_comment_offset_or_total'),
])
def test_incomplete_pages_keep_collected_comments_and_reason(source, mode, code):
    source.mode = mode
    issue = collect(source)
    assert issue['acquisition']['status'] == 'partial'
    coverage = issue['acquisition']['parts']['comments']
    assert coverage['status'] == 'partial'
    assert coverage['collected'] == 2
    assert coverage['reported_total'] == 5
    assert coverage['error_code'] == code
    assert [item['id'] for item in issue['comments']] == ['1', '2']


def test_missing_field_names_keep_raw_values_and_report_partial(source):
    source.mode = 'field_failure'
    issue = collect(source)
    assert issue['acquisition']['status'] == 'partial'
    assert issue['custom_fields']['customfield_1']['name'] == 'customfield_1'
    assert issue['custom_fields']['customfield_1']['raw'] == {'value': 'MACA 3.0'}
    assert issue['acquisition']['parts']['field_definitions']['error_code'] == 'http_503'


def test_first_comment_page_failure_uses_embedded_page_as_partial_evidence(source):
    source.mode = 'first_page_failure'
    issue = collect(source)
    coverage = issue['acquisition']['parts']['comments']
    assert coverage['status'] == 'partial'
    assert coverage['fallback'] == 'issue_embedded_page'
    assert coverage['collected'] == 1 and coverage['reported_total'] == 5


def test_empty_comment_collection_is_complete(source):
    source.comments.clear()
    issue = collect(source)
    assert issue['comments'] == []
    assert issue['acquisition']['parts']['comments']['reported_total'] == 0
    assert issue['acquisition']['status'] == 'complete'


def test_page_budget_stops_collection_with_partial_result(source):
    source.mode = 'page_budget'
    issue = collect(source)
    coverage = issue['acquisition']['parts']['comments']
    assert coverage['status'] == 'partial' and coverage['collected'] == 100
    assert coverage['error_code'] == 'comment_page_budget_exceeded'


def test_expired_budget_does_not_start_requests(source):
    with pytest.raises(TimeoutError):
        jira.get_issue_snapshot('HPC-42', timeout_seconds=0)
    assert source.calls == []


def test_wrong_issue_identity_is_rejected(source):
    source.raw['key'] = 'HPC-99'
    with pytest.raises(ValueError, match='identity'):
        collect(source)
    assert len(source.calls) == 1


def test_http_to_json_to_skill_round_trip_preserves_all_material(source, tmp_path):
    issue = collect(source)
    write_bundle(tmp_path, issue)
    packet = load_bundle(tmp_path)
    assert packet['completeness'] == 'complete'
    assert packet['data']['description'] == source.raw['fields']['description']
    assert packet['data']['custom_fields'] == issue['custom_fields']
    assert packet['data']['raw_fields'] == source.raw['fields']
    assert packet['data']['comments'] == issue['comments']
    assert packet['data']['acquisition']['parts']['comments']['reported_total'] == 5
    assert packet['data']['source_documents'][0]['path'] == 'issue.json'


def test_partial_collection_stays_partial_after_loading(source, tmp_path):
    source.mode = 'second_page_failure'
    issue = collect(source)
    write_bundle(tmp_path, issue)
    packet = load_bundle(tmp_path)
    assert packet['completeness'] == 'partial'
    assert packet['data']['acquisition']['parts']['comments']['error_code'] == 'http_503'
    assert len(packet['data']['comments']) == 2


@pytest.mark.parametrize('damage,error', [
    ('json', 'local_json_unreadable'), ('version', 'local_json_unsupported_schema'),
    ('identity', 'local_issue_identity_mismatch'), ('markdown', 'local_snapshot_pair_mismatch'),
    ('coverage', 'local_json_invalid_coverage'), ('schema', 'local_json_invalid_fields'),
    ('symlink', 'local_json_unsafe_or_invalid'),
])
def test_invalid_json_does_not_silently_fall_back_to_markdown(source, tmp_path, damage, error):
    packet = write_bundle(tmp_path, collect(source))
    if damage == 'version':
        packet['schema_version'] = 99
    if damage == 'identity':
        packet['issue']['key'] = 'HPC-99'
    if damage == 'coverage':
        packet['issue']['acquisition']['parts']['comments']['reported_total'] = 500
    if damage == 'schema':
        del packet['issue']['custom_fields']
    (tmp_path / 'issue.json').write_text(json.dumps(packet))
    if damage == 'json':
        (tmp_path / 'issue.json').write_text('{broken')
    if damage == 'markdown':
        (tmp_path / 'issue.md').write_text('Newer snapshot content')
    if damage == 'symlink':
        (tmp_path / 'issue.json').rename(tmp_path / 'outside.json')
        (tmp_path / 'issue.json').symlink_to(tmp_path / 'outside.json')
    loaded = load_bundle(tmp_path, 'auto')
    assert loaded['success'] is False
    assert loaded['error_code'] == error


def test_json_only_bundle_is_read_locally(source, tmp_path):
    issue = collect(source)
    write_bundle(tmp_path, issue)
    (tmp_path / 'issue.md').unlink()
    packet = load_bundle(tmp_path, 'auto')
    assert packet['completeness'] == 'complete'
    assert packet['data']['custom_fields'] == issue['custom_fields']


def test_attachment_checks_preserve_missing_metadata_and_uploads(source, tmp_path):
    issue = collect(source)
    issue['attachments'] = [
        {'id': '1', 'filename': 'trace.txt', 'size': 5, 'content_url': 'http://jira.invalid/a/1'},
        {'id': '2', 'filename': 'missing.py', 'size': 20, 'content_url': 'http://jira.invalid/a/2'},
    ]
    (tmp_path / 'attachments').mkdir()
    (tmp_path / 'attachments/trace.txt').write_text('trace')
    (tmp_path / 'uploads').mkdir()
    (tmp_path / 'uploads/note.txt').write_text('extra input')
    write_bundle(tmp_path, issue, {'1': 'attachments/trace.txt', '2': '下载失败：HTTP 503'})
    loaded = load_bundle(tmp_path)
    assert loaded['completeness'] == 'partial'
    present, missing = loaded['data']['attachments']
    assert present['available'] and present['sha256']
    assert missing['filename'] == 'missing.py' and missing['size'] == 20
    assert missing['available'] is False and missing['download_error'] == '下载失败：HTTP 503'
    assert loaded['data']['uploads'][0]['local_path'] == 'uploads/note.txt'
