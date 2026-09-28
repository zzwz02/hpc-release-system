#!/usr/bin/env python3
"""Publish one issue-scoped, deduplicated comment and verify its server body."""
import argparse
import json
import os
from pathlib import Path
import re
import sys

import requests
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'skills/jira-issue-manager/scripts'))
from common import DEFAULT_BASE_URL, build_headers, fetch_all_comments, fetch_json, read_token, validate_url


def publish(issue, session, event, body, origin=DEFAULT_BASE_URL, timeout=30, warn_chars=350):
    if not re.fullmatch(r'[A-Z][A-Z0-9_]*-[0-9]+', issue):
        raise ValueError('invalid_issue')
    if os.environ.get('JIRA_COMMENT_ISSUE') != issue:
        raise ValueError('comment_issue_not_authorized')
    if any(not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', value) for value in (session, event)):
        raise ValueError('invalid_session_or_event')
    if not body.strip() or '[hpc-agent:' in body:
        raise ValueError('invalid_body')
    if not isinstance(warn_chars, int) or warn_chars < 1:
        raise ValueError('invalid_warning_threshold')
    marker = f'[hpc-agent:{issue}:{session}:{event}]'
    content = body.strip() + '\n' + marker
    url = origin.rstrip('/') + f'/rest/api/2/issue/{issue}/comment'
    validate_url(url)
    token = read_token()
    if token in content:
        raise ValueError('credential_in_body')
    size = len(body.strip())
    warnings = ([{'code': 'comment_length_advisory', 'characters': size, 'threshold': warn_chars}]
                if size > warn_chars else [])
    for warning in warnings:
        # Advisory only: never include the body or alter what is sent to Jira.
        print(json.dumps({'warning': warning}), file=sys.stderr)
    comments = fetch_all_comments(origin, issue, token, timeout, allow_partial=True)
    if comments['coverage']['status'] != 'complete':
        raise ValueError('comment_history_incomplete_do_not_send')
    matches = [item for item in comments['comments'] if marker in str(item.get('body', ''))]
    if len(matches) > 1:
        raise ValueError('duplicate_existing_marker_needs_review')
    if matches and matches[0].get('body') != content:
        raise ValueError('event_body_changed_use_new_event_for_real_revision')
    identifier = str(matches[0]['id']) if matches else None
    if identifier is None:
        # Only this exact issue/comment endpoint is writable; no redirects/retries.
        response = requests.post(url, headers=build_headers(token), json={'body': content},
                                 timeout=timeout, allow_redirects=False)
        if response.status_code != 201:
            raise ValueError(f'write_http_{response.status_code}_check_same_event_before_retry')
        value = response.json()
        if not isinstance(value, dict) or not isinstance(value.get('id'), str):
            raise ValueError('write_result_unknown_check_same_event')
        identifier = value['id']
    if not re.fullmatch(r'[0-9]+', identifier):
        raise ValueError('invalid_comment_id')
    actual = fetch_json('GET', origin, f'/rest/api/2/issue/{issue}/comment/{identifier}', token, timeout=timeout)
    if not isinstance(actual, dict) or str(actual.get('id')) != identifier or actual.get('body') != content:
        raise ValueError('comment_readback_mismatch')
    return {'success': True, 'issue': issue, 'id': identifier, 'verified': True,
            'reused': bool(matches), 'url': origin.rstrip('/') + f'/browse/{issue}?focusedCommentId={identifier}',
            'warnings': warnings}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--issue-id', required=True)
    parser.add_argument('--session', required=True)
    parser.add_argument('--event', required=True)
    parser.add_argument('--body-file', required=True)
    parser.add_argument('--warn-chars', type=int, default=350,
                        help='Advisory character count excluding the marker; never truncates or blocks by length.')
    args = parser.parse_args()
    try:
        result = publish(args.issue_id, args.session, args.event, Path(args.body_file).read_text(),
                         warn_chars=args.warn_chars)
    except (OSError, ValueError, RuntimeError, requests.RequestException) as error:
        # Do not print responses, URLs containing secrets, or credential exceptions.
        safe = str(error) if isinstance(error, ValueError) and re.fullmatch(r'[a-z0-9_]+', str(error)) else 'request_or_read_failed_check_same_event'
        result = {'success': False, 'error': safe}
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result['success'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
