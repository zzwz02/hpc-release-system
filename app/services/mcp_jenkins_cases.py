"""Bounded case evidence extraction; preserve raw-log fallback and coverage."""
from __future__ import annotations

import re
from urllib.parse import quote

from app.integrations.jenkins import JenkinsError
from app.services.mcp_evidence import correlations

CASE_SCAN_BYTES = 1024 * 1024
CASE_OUTPUT_CHARS = 24000
CASE_ARTIFACT_LIMIT = 10
_ANSI = re.compile(r'\x1b\[[0-?]*[ -/]*[@-~]')
_NOTE = re.compile(r'\x1b\[8m.*?\x1b\[0m', re.S)
_MARKER = re.compile(r"Processing test case ['\"]([^'\"]+)['\"]")


def case_log(client, url: str, name: str, start: int) -> dict:
    data, headers, truncated = client.get(url+'logText/progressiveText', params={'start': start}, limit=CASE_SCAN_BYTES)
    text = _ANSI.sub('', _NOTE.sub('', client._redact(data.decode('utf-8', errors='replace'))))
    lines = text.splitlines()
    blocks, markers = [], []
    for index, line in enumerate(lines):
        match = _MARKER.search(line)
        if match:
            markers.append((index, match.group(1)))
    budget = CASE_OUTPUT_CHARS
    output_truncated = False
    for i, (begin, case) in enumerate(markers):
        if case != name:
            continue
        finish = markers[i+1][0] if i+1 < len(markers) else len(lines)
        # Stop at the case result when available, excluding later global summaries.
        for n in range(begin, finish):
            if 'return test status:' in lines[n]:
                finish = n+1
                break
        content = '\n'.join(lines[begin:finish])
        clipped = content[:budget]
        budget -= len(clipped)
        blocks.append({'match': 'case_marker', 'line_start_in_scan': begin+1,
                       'line_end_in_scan': finish, 'content': clipped,
                       'truncated': len(clipped) < len(content) or (finish == len(lines) and truncated)})
        output_truncated |= len(clipped) < len(content)
        if not budget:
            break
    if not blocks:
        # A text mention is evidence to inspect, not a claimed execution boundary.
        pattern = re.compile(r'(?<![\w.-])'+re.escape(name)+r'(?![\w.-])')
        last_end = 0
        for i, line in enumerate(lines):
            if i < last_end or not pattern.search(line):
                continue
            begin, end = max(0, i-8), min(len(lines), i+17)
            content = '\n'.join(lines[begin:end])
            clipped = content[:budget]
            blocks.append({'match': 'text_reference', 'line_start_in_scan': begin+1,
                           'line_end_in_scan': end, 'content': clipped, 'truncated': len(clipped) < len(content)})
            budget -= len(clipped)
            last_end = end
            if len(blocks) >= 5 or not budget:
                output_truncated = True
                break
    next_start = start+len(data)
    if not truncated:
        try:
            next_start = int(headers.get('x-text-size', next_start))
        except ValueError:
            pass
    return {'source_url': url+'console', 'scan_start': start, 'scan_bytes': len(data),
            'next_start': next_start, 'has_more': truncated, 'building': headers.get('x-more-data') == 'true',
            'matches': blocks, 'found_in_scanned_log': bool(blocks), 'output_truncated': output_truncated,
            'note': '行号相对于本次扫描文本；未找到仅表示当前扫描范围内未定位。并行日志可能交错，片段不单独证明根因。'}


def case_evidence(client, url: str, name: str, start: int, *, console_only=False) -> dict:
    result = {'case_name': name, 'build_url': url, 'cases': [], 'warnings': []}
    if not console_only:
        build = client.json(url+'api/json', params={'tree': 'number,url,result,building,artifacts[relativePath,fileName]'})
        result['build'] = build
        report = client.json(url+'testReport/api/json', params={
            'tree': 'suites[name,cases[name,className,status,errorDetails,errorStackTrace,stdout,stderr]]'}, optional=True)
        result['test_report_available'] = report is not None
        for suite in (report or {}).get('suites') or []:
            for case in suite.get('cases') or []:
                if name in (case.get('name'), f"{case.get('className', '')}.{case.get('name', '')}"):
                    result['cases'].append({'source_url': url+'testReport/', 'suite': suite.get('name'), **case})
        artifacts = [a['relativePath'] for a in build.get('artifacts', [])
                     if isinstance(a.get('relativePath'), str) and a['relativePath'].startswith('cicd-test-results/')
                     and a['relativePath'].endswith('.json') and '\\' not in a['relativePath']
                     and all(p not in ('', '.', '..') for p in a['relativePath'].split('/'))]
        result['artifacts_truncated'] = len(artifacts) > CASE_ARTIFACT_LIMIT
        for path in artifacts[:CASE_ARTIFACT_LIMIT]:
            source = url+'artifact/'+quote(path, safe='/')
            try:
                data = client.json(source)
            except JenkinsError as exc:
                result['warnings'].append({'source_url': source, 'error': str(exc)})
                continue
            rows = data.get('result')
            if not isinstance(rows, list):
                continue
            for index, row in enumerate(rows):
                if not isinstance(row, dict) or row.get('case_item1') != name:
                    continue
                result['cases'].append({'source_url': source, 'field': f'$.result[{index}]',
                                        'case': row, 'environment': data.get('execute_config'),
                                        'correlations': correlations(data, source)})
    result['console'] = case_log(client, url, name, start)
    # Bound large JUnit stdout/stacks while making each loss visible.
    result['cases_truncated'] = len(result['cases']) > 20
    result['cases'] = result['cases'][:20]
    for case in result['cases']:
        clipped = []
        for key in ('stdout', 'stderr', 'errorDetails', 'errorStackTrace'):
            value = case.get(key)
            if isinstance(value, str) and len(value) > 6000:
                case[key] = value[:6000]
                clipped.append(key)
        if clipped:
            case['truncated_fields'] = clipped
    return result
