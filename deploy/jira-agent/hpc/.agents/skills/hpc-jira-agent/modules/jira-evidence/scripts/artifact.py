#!/usr/bin/env python3
"""Publish this run's reviewed deliverables within an operator-scoped Jira issue."""
import argparse
import fcntl
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import sys
import tarfile
import tempfile
import zipfile

import requests

SCRIPTS = Path(__file__).resolve().parents[1] / 'skills/jira-issue-manager/scripts'
sys.path.insert(0, str(SCRIPTS))
from common import DEFAULT_BASE_URL, fetch_json, read_token, validate_url
from upload_attachment import build_url

LIMIT = 32 * 1024 * 1024


def inspect_content(name, data, token):
    path = PurePosixPath(name)
    if (path.is_absolute() or '..' in path.parts or '\\' in name or ':' in name
            or any(part in ('.ssh', '.codex', '.git') for part in path.parts)
            or path.name.lower() in ('auth.json', 'token', 'pass', 'id_rsa', 'id_ed25519', '.env')):
        raise ValueError('unsafe_artifact_member')
    if token.encode() in data or re.search(rb'-----BEGIN [A-Z ]*PRIVATE KEY-----', data):
        raise ValueError('credential_in_artifact')


def inspect_package(name, data, token):
    inspect_content(name, data, token)
    total = 0
    count = 0

    def member(filename, size, reader):
        nonlocal total, count
        total += size
        count += 1
        if size < 0 or total > LIMIT or count > 2000:
            raise ValueError('archive_budget_exceeded')
        if filename.lower().endswith(('.zip', '.tar', '.tgz', '.gz', '.bz2', '.xz', '.7z')):
            raise ValueError('nested_archive_not_supported')
        content = reader.read(size + 1)
        if len(content) != size:
            raise ValueError('archive_size_mismatch')
        inspect_content(filename, content, token)

    if name.lower().endswith('.zip'):
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            for info in archive.infolist():
                inspect_content(info.filename, b'', token)
                mode = info.external_attr >> 16
                if stat.S_ISLNK(mode) or (stat.S_IFMT(mode) not in (0, stat.S_IFREG, stat.S_IFDIR)) or info.flag_bits & 1:
                    raise ValueError('unsafe_archive_entry')
                if info.is_dir():
                    continue
                with archive.open(info) as stream:
                    member(info.filename, info.file_size, stream)
    elif name.lower().endswith(('.tar.gz', '.tgz', '.tar')):
        with tarfile.open(fileobj=io.BytesIO(data), mode='r:*') as archive:
            for info in archive:
                inspect_content(info.name, b'', token)
                if info.isdir():
                    continue
                if not info.isfile():
                    raise ValueError('unsafe_archive_entry')
                with archive.extractfile(info) as stream:
                    member(info.name, info.size, stream)
    elif data.startswith((b'PK\x03\x04', b'\x1f\x8b', b'7z\xbc\xaf\x27\x1c')):
        raise ValueError('archive_extension_mismatch')


def attachments(origin, issue, token, filename):
    value = fetch_json('GET', origin, f'/rest/api/2/issue/{issue}?fields=attachment', token)
    items = value.get('fields', {}).get('attachment')
    if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
        raise ValueError('attachment_listing_incomplete')
    found = [item for item in items if item.get('filename') == filename]
    if len(found) > 1:
        raise ValueError('duplicate_attachments_need_review')
    return found


def verify(origin, issue, attachment, digest, size):
    identifier = str(attachment.get('id', ''))
    url = attachment.get('content', '')
    if not re.fullmatch(r'[0-9]+', identifier) or attachment.get('size') != size:
        raise ValueError('attachment_metadata_mismatch')
    validate_url(url)
    with tempfile.TemporaryDirectory(prefix='jira-artifact-verify-') as directory:
        result = subprocess.run([
            sys.executable, '-B', str(SCRIPTS / 'download_attachment.py'), '--base-url', origin,
            '--issue-id', issue, '--url', url, '--output-dir', directory, '--filename', 'readback',
            '--max-bytes', str(LIMIT), '--diagnose-auth-redirect',
        ], capture_output=True, text=True, timeout=180)
        value = json.loads(result.stdout)
        receipt = value.get('data', {})
        if (result.returncode or not value.get('success') or receipt.get('sha256') != digest
                or receipt.get('attachment_id') != identifier or receipt.get('size') != size):
            raise ValueError('attachment_content_verification_failed')
    return {'attachment_id': identifier, 'url': url, 'filename': attachment['filename'],
            'jira_reference': '[^' + attachment['filename'] + ']', 'size': size, 'sha256': digest}


def publish(issue, session, kind, filename, prepare=False, origin=DEFAULT_BASE_URL):
    if not re.fullmatch(r'[A-Z][A-Z0-9_]*-[0-9]+', issue):
        raise ValueError('invalid_issue')
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', session) or kind not in ('report', 'reproducer', 'evidence'):
        raise ValueError('invalid_session_or_kind')
    # Scope is configured by the launcher after user authorization, not by this tool.
    if os.environ.get('JIRA_ARTIFACT_ISSUE') != issue:
        raise ValueError('artifact_issue_not_authorized')
    root = os.environ.get('JIRA_ARTIFACT_ROOT', '')
    if not root or not Path(root).is_absolute():
        raise ValueError('artifact_root_not_configured')
    root = Path(root).resolve(strict=True)
    path = Path(filename).resolve(strict=True)
    if not root.is_dir() or not path.is_relative_to(root) or Path(filename).is_symlink():
        raise ValueError('artifact_outside_delivery_root')
    current = os.environ.get('CODEX_THREAD_ID')
    if current and session != current:
        raise ValueError('artifact_session_mismatch')
    if not path.is_file() or '.artifact-receipts' in path.parts:
        raise ValueError('invalid_artifact_file')
    with path.open('rb') as stream:
        data = stream.read(LIMIT + 1)
    if not data or len(data) > LIMIT:
        raise ValueError('artifact_size_out_of_bounds')
    token = read_token()
    inspect_package(path.name, data, token)
    if kind == 'report':
        if path.suffix.lower() not in ('.md', '.txt'):
            raise ValueError('report_requires_utf8_text')
        data.decode('utf-8')
    digest = hashlib.sha256(data).hexdigest()
    suffix = re.sub(r'[^A-Za-z0-9._-]', '_', path.name)[-60:]
    name = f'{issue}-{session}-{kind}-{digest}-{suffix}'
    url = build_url(origin, issue)
    validate_url(url)
    if prepare:
        return {'success': True, 'prepared': True, 'uploaded': False, 'issue': issue, 'kind': kind,
                'source': str(path), 'filename': name, 'sha256': digest, 'size': len(data),
                'session': session, 'destination': url}

    journal = path.parent / '.artifact-receipts'
    if journal.is_symlink():
        raise ValueError('unsafe_receipt_directory')
    journal.mkdir(mode=0o700, exist_ok=True)
    key = hashlib.sha256(name.encode()).hexdigest()
    record = journal / (key + '.json')
    with (journal / (key + '.lock')).open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        found = attachments(origin, issue, token, name)
        reused = bool(found)
        if not found:
            if record.exists():
                raise ValueError('upload_outcome_unknown_reconcile_do_not_resend')
            with open(record, 'x', opener=lambda p, flags: os.open(p, flags, 0o600)) as stream:
                json.dump({'state': 'pending', 'issue': issue, 'filename': name, 'sha256': digest}, stream)
            # This narrow delivery path does not grant the generic Jira writers permission.
            # Send precisely the bytes inspected above; never reread a mutable source file.
            response = requests.post(url, headers={
                'Authorization': f'Bearer {token}', 'X-Atlassian-Token': 'no-check',
            }, files={'file': (name, data, 'application/octet-stream')},
                timeout=90, allow_redirects=False)
            if not 200 <= response.status_code < 300:
                raise ValueError('upload_failed_reconcile_before_retry')
            found = attachments(origin, issue, token, name)
            if not found:
                raise ValueError('uploaded_attachment_not_visible_reconcile_before_retry')
        receipt = verify(origin, issue, found[0], digest, len(data))
        result = {'success': True, 'verified': True, 'issue': issue, 'kind': kind,
                  'session': session, 'reused': reused, **receipt}
        record.write_text(json.dumps(result, ensure_ascii=False, indent=2))
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--issue-id', required=True)
    parser.add_argument('--session', required=True)
    parser.add_argument('--kind', choices=('report', 'reproducer', 'evidence'), required=True)
    parser.add_argument('--file', required=True)
    parser.add_argument('--prepare', action='store_true')
    args = parser.parse_args()
    try:
        result = publish(args.issue_id, args.session, args.kind, args.file, args.prepare)
    except (OSError, ValueError, RuntimeError, requests.RequestException, subprocess.SubprocessError,
            tarfile.TarError, zipfile.BadZipFile) as error:
        safe = str(error) if isinstance(error, ValueError) and re.fullmatch(r'[a-z0-9_]+', str(error)) else 'artifact_failed_check_scope_or_reconcile'
        result = {'success': False, 'error': safe}
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result['success'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
