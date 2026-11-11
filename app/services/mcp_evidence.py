"""Recorded correlation fields with provenance; never infer same-cause links."""
from __future__ import annotations

import re

_FIELDS = {
    'issue_key': 'jira_issue', 'jira_key': 'jira_issue', 'jira_issue_key': 'jira_issue',
    'jira_url': 'jira_url', 'build_url': 'jenkins_build', 'job_name': 'jenkins_job',
    'build_number': 'jenkins_build_number', 'Jenkins_build_number': 'jenkins_build_number',
    'official_name': 'application', 'app_name': 'application', 'app_version': 'application_version',
    'image': 'image', 'image_digest': 'image_digest', 'image_tag': 'image',
    'git_url': 'repository', 'git_branch': 'branch', 'git_commit': 'commit',
    'maca_version': 'sdk_version', 'failure_id': 'failure_record',
}


def correlations(data, source: str) -> dict:
    """Return exact stored values plus their JSON paths; text references are separate."""
    entries, seen = [], set()
    visited = 0
    truncated = False

    def add(kind, value, path, relation='recorded_field'):
        nonlocal truncated
        if len(entries) >= 200:
            truncated = True
            return
        if not isinstance(value, (str, int)) or isinstance(value, bool) or value == '':
            return
        key = (kind, str(value), path)
        if key not in seen:
            seen.add(key)
            entries.append({'kind': kind, 'value': value, 'source': source,
                            'field': path, 'relation': relation})

    def walk(value, path='$', depth=0):
        nonlocal visited, truncated
        visited += 1
        if visited > 10000 or depth > 12:
            truncated = True
            return
        if isinstance(value, dict):
            for key, child in value.items():
                location = f'{path}.{key}'
                if key in _FIELDS:
                    add(_FIELDS[key], child, location)
                if key in ('description', 'body') and isinstance(child, str):
                    for match in re.finditer(r'https?://[^\s<>\[\]|]+', child[:100000]):
                        link = match.group().rstrip(').,;，。')
                        if re.search(r'/browse/[A-Z][A-Z0-9_]*-\d+$', link):
                            add('jira_url', link, location, 'text_reference')
                        elif re.search(r'(?:/job/[^/]+)+/\d+/?$', link):
                            add('jenkins_build', link, location, 'text_reference')
                walk(child, location, depth+1)
        elif isinstance(value, list):
            for index, child in enumerate(value):
                if visited > 10000:
                    truncated = True
                    break
                walk(child, f'{path}[{index}]', depth+1)

    walk(data)
    if isinstance(data, dict):
        fields = data.get('raw_fields') or {}
        if isinstance(fields, dict):
            for i, link in enumerate(fields.get('issuelinks') or []):
                for direction in ('inwardIssue', 'outwardIssue'):
                    if isinstance(link.get(direction), dict):
                        add('jira_issue', link[direction].get('key'), f'$.raw_fields.issuelinks[{i}].{direction}.key', 'jira_issue_link')
            for field in ('fixVersions', 'versions'):
                for i, version in enumerate(fields.get(field) or []):
                    add('jira_version', version.get('name'), f'$.raw_fields.{field}[{i}].name')
        # This archived HPC report has a documented schema; do not apply item/extend aliases to arbitrary JSON.
        if isinstance(data.get('jenkins_job_info'), dict) and isinstance(data.get('execute_config'), dict):
            for key, kind in (('item1', 'application'), ('item2', 'application_version')):
                add(kind, data['execute_config'].get(key), f'$.execute_config.{key}')
            for i, row in enumerate(data.get('result') or []):
                if isinstance(row, dict):
                    add('image', row.get('extend4'), f'$.result[{i}].extend4')
                    add('image_digest', row.get('extend2'), f'$.result[{i}].extend2')
    return {'references': entries, 'truncated': truncated,
            'note': '仅提取来源中明确记录的字段或链接；引用不证明同一根因。没有按名称或时间推断关联，未列出也不代表关联不存在。'}
