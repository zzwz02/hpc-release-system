"""Run actual delivery/download CLIs against a loopback HTTP server."""
from email import policy
from email.parser import BytesParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import threading
import unittest
import zipfile

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/artifact.py'


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def send(self, status, content, mime='application/json'):
        data = content if isinstance(content, bytes) else json.dumps(content).encode()
        self.send_response(status)
        self.send_header('Content-Type', mime)
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        self.server.gets += 1
        if self.headers.get('Authorization') != 'Bearer offline-artifact-token':
            self.send(401, {})
            return
        if '/rest/api/2/issue/' in self.path:
            self.send(self.server.readstatus, {'fields': {'attachment': self.server.items}})
            return
        if self.path.startswith('/secure/attachment/'):
            if self.server.redirect and 'os_authType=basic' not in self.path:
                self.send_response(302)
                self.send_header('Location', self.server.redirect)
                self.send_header('Content-Length', '0')
                self.end_headers()
                return
            identifier = self.path.split('/')[3].split('?')[0]
            data = self.server.data[identifier]
            self.send(200, b'x' * len(data) if self.server.corrupt else data, 'application/octet-stream')
            return
        self.send(404, {})

    def do_POST(self):
        if self.headers.get('Authorization') != 'Bearer offline-artifact-token' or self.headers.get('X-Atlassian-Token') != 'no-check':
            self.send(401, {})
            return
        self.server.posts += 1
        raw = self.rfile.read(int(self.headers['Content-Length']))
        message = BytesParser(policy=policy.default).parsebytes(
            ('Content-Type: ' + self.headers['Content-Type'] + '\r\nMIME-Version: 1.0\r\n\r\n').encode() + raw)
        part = next(message.iter_parts())
        data = part.get_payload(decode=True)
        identifier = str(len(self.server.items) + 1)
        item = {'id': identifier, 'filename': part.get_filename(), 'size': len(data),
                'mimeType': 'application/octet-stream',
                'content': self.server.origin + '/secure/attachment/' + identifier}
        if self.server.commit:
            self.server.items.append(item)
            self.server.data[identifier] = data
        self.send(self.server.poststatus, [item])


class Artifacts(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.server.origin = f'http://127.0.0.1:{self.server.server_port}'
        self.server.items = []
        self.server.data = {}
        self.server.posts = 0
        self.server.gets = 0
        self.server.readstatus = 200
        self.server.poststatus = 200
        self.server.commit = True
        self.server.corrupt = False
        self.server.redirect = None
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        (self.root / 'token').write_text('offline-artifact-token')
        self.delivery = self.root / 'delivery'
        self.delivery.mkdir()
        self.file = self.delivery / 'REPORT.md'
        self.file.write_text('# Root cause\nEvidence and limitations.\n')
        self.env = {key: value for key, value in os.environ.items() if not key.startswith('JIRA_') and key != 'CODEX_THREAD_ID'}
        self.env.update(JIRA_BASE_URL=self.server.origin, JIRA_ALLOWED_ORIGIN=self.server.origin,
                        JIRA_TOKEN_FILE=str(self.root / 'token'), JIRA_ARTIFACT_ISSUE='TEST-1',
                        JIRA_ARTIFACT_ROOT=str(self.delivery),
                        NO_PROXY='127.0.0.1', no_proxy='127.0.0.1')

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.temp.cleanup()

    def call(self, prepare=False, issue='TEST-1', kind='report'):
        args = [sys.executable, '-B', str(SCRIPT), '--issue-id', issue, '--session', 'session-1',
                '--kind', kind, '--file', str(self.file)]
        result = subprocess.run(args + (['--prepare'] if prepare else []), env=self.env,
                                capture_output=True, text=True, timeout=15)
        self.assertNotIn('offline-artifact-token', result.stdout + result.stderr)
        value = json.loads(result.stdout)
        self.assertEqual(result.returncode, 0 if value['success'] else 1)
        return value

    def test_prepare_does_not_authorize_or_contact_server(self):
        value = self.call(True)
        self.assertFalse(value['uploaded'])
        self.assertNotIn('approval_request', value)
        self.assertEqual(self.server.gets + self.server.posts, 0)

    def test_missing_scope_blocks_without_poisoning_retry(self):
        self.env.pop('JIRA_ARTIFACT_ISSUE')
        self.assertFalse(self.call()['success'])
        self.assertEqual(self.server.posts, 0)
        self.env['JIRA_ARTIFACT_ISSUE'] = 'TEST-1'
        self.assertTrue(self.call()['verified'])

    def test_scoped_upload_readback_and_deduplicate_without_file_approval(self):
        prepared = self.call(True)
        first = self.call()
        self.assertTrue(first['verified'])
        self.assertEqual(first['sha256'], prepared['sha256'])
        self.assertEqual(self.server.data['1'], self.file.read_bytes())
        self.assertTrue(self.call()['reused'])
        self.assertEqual(self.server.posts, 1)

    def test_missing_and_relative_delivery_root_block(self):
        for root in ('', 'delivery'):
            self.env['JIRA_ARTIFACT_ROOT'] = root
            self.assertFalse(self.call()['success'])
        self.assertEqual(self.server.posts, 0)

    def test_changed_content_is_rechecked_without_new_approval(self):
        first = self.call()
        self.file.write_text('Changed evidence')
        second = self.call()
        self.assertTrue(second['verified'])
        self.assertNotEqual(first['sha256'], second['sha256'])
        self.assertEqual(self.server.posts, 2)

    def test_different_issue_cannot_use_scope(self):
        self.assertFalse(self.call(issue='TEST-2')['success'])
        self.assertEqual(self.server.posts, 0)

    def test_other_directory_cannot_use_scope(self):
        self.file = self.root / 'outside.md'
        self.file.write_text('Outside report')
        self.assertEqual(self.call()['error'], 'artifact_outside_delivery_root')
        self.assertEqual(self.server.gets + self.server.posts, 0)

    def test_symlink_cannot_escape_scope(self):
        outside = self.root / 'outside.md'
        outside.write_text('Outside report')
        self.file = self.delivery / 'link.md'
        self.file.symlink_to(outside)
        self.assertEqual(self.call()['error'], 'artifact_outside_delivery_root')
        self.assertEqual(self.server.posts, 0)

    def test_current_session_is_checked(self):
        self.env['CODEX_THREAD_ID'] = 'another-session'
        self.assertEqual(self.call()['error'], 'artifact_session_mismatch')
        self.env['CODEX_THREAD_ID'] = 'session-1'
        self.assertTrue(self.call()['verified'])

    def test_unapproved_origin_never_receives_credentials(self):
        self.env['JIRA_ALLOWED_ORIGIN'] = 'http://127.0.0.1:1'
        self.assertFalse(self.call()['success'])
        self.assertEqual(self.server.gets + self.server.posts, 0)

    def test_comment_scope_does_not_grant_delivery(self):
        self.env.pop('JIRA_ARTIFACT_ISSUE')
        self.env['JIRA_COMMENT_ISSUE'] = 'TEST-1'
        self.assertFalse(self.call()['success'])
        self.assertEqual(self.server.posts, 0)

    def test_generic_upload_still_requires_exact_approval(self):
        script = SCRIPT.parents[1] / 'skills/jira-issue-manager/scripts/upload_attachment.py'
        result = subprocess.run([sys.executable, '-B', str(script), '--issue-id', 'TEST-1', '--file', str(self.file)],
                                env=self.env, capture_output=True, text=True, timeout=15)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('JIRA_WRITE_APPROVAL_FILE', result.stderr)
        self.assertNotIn('offline-artifact-token', result.stdout + result.stderr)
        self.assertEqual(self.server.posts, 0)

    def test_readback_mismatch_never_reports_success(self):
        self.server.corrupt = True
        self.assertFalse(self.call()['success'])
        self.assertFalse(self.call()['success'])
        self.assertEqual(self.server.posts, 1)

    def test_committed_error_is_reconciled_without_duplicate(self):
        self.server.poststatus = 503
        self.assertFalse(self.call()['success'])
        self.assertTrue(self.call()['reused'])
        self.assertEqual(self.server.posts, 1)

    def test_unknown_uncommitted_error_is_not_blindly_retried(self):
        self.server.poststatus = 503
        self.server.commit = False
        self.assertFalse(self.call()['success'])
        self.assertEqual(self.call()['error'], 'upload_outcome_unknown_reconcile_do_not_resend')
        self.assertEqual(self.server.posts, 1)

    def test_listing_failure_prevents_write(self):
        self.server.readstatus = 403
        self.assertFalse(self.call()['success'])
        self.assertEqual(self.server.posts, 0)

    def test_duplicate_remote_names_need_review(self):
        self.call()
        self.server.items.append(dict(self.server.items[0]))
        self.assertEqual(self.call()['error'], 'duplicate_attachments_need_review')
        self.assertEqual(self.server.posts, 1)

    def test_same_origin_login_challenge_reuses_download_capability(self):
        self.server.redirect = '/login.jsp'
        self.assertTrue(self.call()['verified'])

    def test_external_download_redirect_is_not_followed(self):
        self.server.redirect = 'http://127.0.0.1:1/login.jsp'
        self.assertFalse(self.call()['success'])

    def test_plaintext_and_archived_credentials_are_blocked(self):
        self.file.write_text('offline-artifact-token')
        self.assertFalse(self.call(True)['success'])
        self.file = self.delivery / 'case.zip'
        with zipfile.ZipFile(self.file, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr('case.py', '# offline-artifact-token')
        self.assertFalse(self.call(True, kind='reproducer')['success'])
        self.assertEqual(self.server.posts, 0)

    def test_valid_reproducer_zip_roundtrip(self):
        self.file = self.delivery / 'case.zip'
        with zipfile.ZipFile(self.file, 'w') as archive:
            archive.writestr('README.md', 'Run python3 case.py; expected output 2.')
            archive.writestr('case.py', 'print(1 + 1)')
        self.assertTrue(self.call(kind='reproducer')['verified'])

    def test_handoff_evidence_bundle_roundtrip_preserves_paths_and_bytes(self):
        entries = {
            'artifacts/handoff.md': '# TEST-1 交接材料\n\ndisposition: needs_help\n日志：[记录](logs/probe.log)\n'.encode(),
            'artifacts/logs/probe.log': b'probe exited 1; required input unavailable\n',
        }
        self.file = self.delivery / 'handoff.zip'
        with zipfile.ZipFile(self.file, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
            for name, data in entries.items():
                archive.writestr(name, data)
        result = self.call(kind='evidence')
        self.assertTrue(result['verified'])
        with zipfile.ZipFile(io.BytesIO(self.server.data['1'])) as archive:
            self.assertEqual(set(archive.namelist()), set(entries))
            for name, data in entries.items():
                self.assertEqual(archive.read(name), data)
        self.assertTrue(self.call(kind='evidence')['reused'])
        self.assertEqual(self.server.posts, 1)

    def test_handoff_markdown_in_delivery_subdirectory_roundtrip(self):
        directory = self.delivery / 'artifacts'
        directory.mkdir()
        self.file = directory / 'handoff.md'
        self.file.write_text('# TEST-1 交接材料\n缺少关键输入，尚未复现；无实验日志。\n')
        self.assertTrue(self.call(kind='evidence')['verified'])
        self.assertEqual(self.server.data['1'], self.file.read_bytes())

    def test_zip_path_traversal_and_nested_archives_blocked(self):
        self.file = self.delivery / 'case.zip'
        for name in ('../case.py', '.ssh/id_rsa', 'nested.zip'):
            with zipfile.ZipFile(self.file, 'w') as archive:
                archive.writestr(name, 'data')
            self.assertFalse(self.call(True, kind='reproducer')['success'])

    def test_tar_links_are_not_followed(self):
        self.file = self.delivery / 'case.tar.gz'
        with tarfile.open(self.file, 'w:gz') as archive:
            info = tarfile.TarInfo('case')
            info.type = tarfile.SYMTYPE
            info.linkname = '/etc/passwd'
            archive.addfile(info)
        self.assertEqual(self.call(True, kind='reproducer')['error'], 'unsafe_archive_entry')

    def test_expanded_archive_budget_is_enforced(self):
        self.file = self.delivery / 'case.zip'
        with zipfile.ZipFile(self.file, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr('data.txt', b'x' * (32 * 1024 * 1024 + 1))
        self.assertEqual(self.call(True, kind='reproducer')['error'], 'archive_budget_exceeded')


if __name__ == '__main__':
    unittest.main()
