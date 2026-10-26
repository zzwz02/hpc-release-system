"""Collect real evidence archives without executing or extracting their contents."""
from __future__ import annotations

import hashlib
import io
import json
import tarfile
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from app.config import settings
from app.domain import jira_learning as domain
from app.services import jira_learning_sources as sources


def archive(path: Path, entries: list[tuple[str | tarfile.TarInfo, bytes]]) -> dict:
    with tarfile.open(path, 'w:gz' if path.suffix != '.tar' else 'w') as bundle:
        for name, data in entries:
            member = tarfile.TarInfo(name) if isinstance(name, str) else name
            member.size = len(data)
            bundle.addfile(member, io.BytesIO(data))
    return record(path)


def record(path: Path) -> dict:
    return {'id': path.name, 'conversation_id': 'jac_1', 'turn_id': 'jat_1',
            'source': 'artifact', 'name': path.name, 'size': path.stat().st_size,
            'local_path': str(path)}


@pytest.mark.parametrize('name', ['evidence.tar', 'evidence.tar.gz', 'evidence.tgz'])
def test_archive_logs_reach_model_with_member_provenance(tmp_path, name):
    data = b'launches=100000 rss_kB=442400\nvalidated_elements=1024\n'
    entry = archive(tmp_path / name, [
        *[(f'evidence/sample-{i}.csv', b'time,value\n1,2\n') for i in range(40)],
        ('evidence/bad/final-run.txt', data),
        ('evidence/good/final-run.txt', b'launches=100000 rss_kB=44480\n'),
        ('evidence/repro.cu', b'int main() { return 0; }\n'),
        ('evidence/ANALYSIS_PATH.md', b'# Analysis path\n'),
    ])
    before = set(tmp_path.rglob('*'))
    collected, gaps = sources.collect_sources({}, [], [], [entry])
    assert not gaps
    assert len(collected) == 5
    assert sum(len(item.get("copies", [])) for item in collected) == 39
    assert set(tmp_path.rglob('*')) == before
    by_member = {item['archive']['member']: item for item in collected}
    bad = by_member['evidence/bad/final-run.txt']
    assert bad['text'] == data.decode()
    assert bad['sha256'] == hashlib.sha256(data).hexdigest()
    assert bad['independent'] is True
    assert bad['archive'] == {
        'source_id': f'agent:artifact:{name}', 'name': name,
        'sha256': hashlib.sha256((tmp_path / name).read_bytes()).hexdigest(),
        'member': 'evidence/bad/final-run.txt',
    }
    assert bad['conversation_id'] == 'jac_1' and bad['turn_id'] == 'jat_1'
    assert by_member['evidence/good/final-run.txt']['id'] != bad['id']
    assert by_member['evidence/repro.cu']['independent'] is False
    assert by_member['evidence/ANALYSIS_PATH.md']['kind'] == 'agent_analysis_path'
    assert by_member['evidence/ANALYSIS_PATH.md']['independent'] is False
    source = domain.build_source({'key': 'MC3-1', 'description': ''}, [], set(), additional_sources=collected)
    bounded, missing = domain.model_source(source)
    assert not missing
    assert next(item for item in bounded['sources'] if item['id'] == bad['id'])['text'] == data.decode()
    proposal = {'kind': 'skill_update', 'title': '核对内存增长', 'summary': '行为差异', 'proposed_text': '记录证据',
                'applicability': '配套 SDK', 'limitations': '', 'validation': '核对日志',
                'files': [{'path': '.agents/skills/hpc-jira-agent/SKILL.md',
                           'updated_text': '# Analysis\n\n核对同等调用次数下的内存增长。\n'}], 'evidence_ids': [bad['id']]}
    baseline = {'.agents/skills/hpc-jira-agent/SKILL.md': '# Analysis\n'}
    assert domain.validate_proposals(json.dumps({'proposals': [proposal]}), bounded, baseline)[0]['evidence_ids'] == [bad['id']]


def test_referenced_good_and_bad_logs_take_priority_under_file_limit(tmp_path, monkeypatch):
    monkeypatch.setattr(sources, 'MAX_SOURCE_FILES', 3)
    report = tmp_path / 'REPORT.md'
    report.write_text('See remote/{good,bad}/final-*.txt for both conditions.')
    entry = archive(tmp_path / 'evidence.tar.gz', [
        *[(f'noise/{i}.log', b'x') for i in range(10)],
        ('remote/good/final-run.txt', b'good\n' * 20),
        ('remote/bad/final-run.txt', b'bad\n' * 20),
    ])
    collected, gaps = sources.collect_sources({}, [], [], [entry, record(report)])
    assert {item['archive']['member'] for item in collected if 'archive' in item} == {
        'remote/good/final-run.txt', 'remote/bad/final-run.txt',
    }
    assert len(collected) == 3
    assert gaps and all('限制' in gap['reason'] for gap in gaps)


def test_archive_rejects_links_unsafe_paths_duplicates_and_nontext(tmp_path, monkeypatch):
    monkeypatch.setattr(sources, 'MAX_SOURCE_FILE_BYTES', 32)
    symlink = tarfile.TarInfo('symlink.txt')
    symlink.type = tarfile.SYMTYPE
    symlink.linkname = '/etc/passwd'
    hardlink = tarfile.TarInfo('hardlink.txt')
    hardlink.type = tarfile.LNKTYPE
    hardlink.linkname = 'valid.log'
    entry = archive(tmp_path / 'evidence.tar.gz', [
        ('valid.log', b'original\n'), ('./valid.log', b'replacement\n'),
        ('../outside.txt', b'escape'), ('/absolute.txt', b'escape'),
        ('windows\\file.txt', b'escape'), (symlink, b''), (hardlink, b''),
        ('image.png', b'image'), ('nested.tar.gz', b'archive'),
        ('binary.txt', b'x\0y'), ('invalid.txt', b'\xff'), ('huge.log', b'x' * 33),
    ])
    collected, gaps = sources.collect_sources({}, [], [], [entry])
    assert len(collected) == 1 and collected[0]['text'] == 'original\n'
    assert len(gaps) == 11
    assert any('重复' in item['reason'] for item in gaps)
    assert any('UTF-8' in item['reason'] for item in gaps)
    assert list(tmp_path.iterdir()) == [tmp_path / 'evidence.tar.gz']


def test_bad_or_oversized_archive_is_a_gap_not_a_failed_job(tmp_path, monkeypatch):
    bad = tmp_path / 'broken.tar.gz'
    bad.write_bytes(b'not an archive')
    collected, gaps = sources.collect_sources({}, [], [], [record(bad)])
    assert not collected and '压缩包未完整读取' in gaps[0]['reason']
    entry = archive(tmp_path / 'large.tar.gz', [('data.txt', b'x' * 10000)])
    monkeypatch.setattr(sources, 'MAX_ARCHIVE_EXPANDED_BYTES', 2048)
    collected, gaps = sources.collect_sources({}, [], [], [entry])
    assert not collected and '展开大小超过限制' in gaps[0]['reason']


def test_archive_member_and_text_budgets_report_missing_content(tmp_path, monkeypatch):
    entry = archive(tmp_path / 'evidence.tar', [(f'{i}.log', str(i).encode() + b'x' * 9) for i in range(5)])
    monkeypatch.setattr(sources, 'MAX_ARCHIVE_MEMBERS', 2)
    collected, gaps = sources.collect_sources({}, [], [], [entry])
    assert len(collected) == 2
    assert any('成员数量超过限制' in gap['reason'] for gap in gaps)
    monkeypatch.setattr(sources, 'MAX_SOURCE_TOTAL_BYTES', 15)
    collected, gaps = sources.collect_sources({}, [], [], [entry])
    assert len(collected) == 1 and sum(item['size'] for item in collected) <= 15
    assert any('总大小超过限制' in gap['reason'] for gap in gaps)


def test_archive_compressed_budget_is_shared_across_files(tmp_path, monkeypatch):
    first = archive(tmp_path / 'first.tar', [('first.log', b'first')])
    second = archive(tmp_path / 'second.tar', [('second.log', b'second')])
    monkeypatch.setattr(sources, 'MAX_ARCHIVE_BYTES', first['size'])
    collected, gaps = sources.collect_sources({}, [], [], [first, second])
    assert [item['text'] for item in collected] == ['first']
    assert any('压缩包采集总大小超过限制' in gap['reason'] for gap in gaps)


def test_jira_attachment_archive_uses_bounded_download_and_keeps_provenance(tmp_path, monkeypatch):
    entry = archive(tmp_path / 'evidence.tgz', [('output/results.tsv', b'version\trss\ngood\t44\nbad\t442\n')])
    server = ThreadingHTTPServer(('127.0.0.1', 0), partial(SimpleHTTPRequestHandler, directory=str(tmp_path)))
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        url = f'http://127.0.0.1:{server.server_port}'
        conf = tmp_path / 'release_system.conf'
        conf.write_text(f'[jira]\nJIRA_BASE_URL={url}\nJIRA_TOKEN=local-test-token\n')
        monkeypatch.setattr(settings, 'runtime_conf_path', conf)
        issue = {'attachments': [{'id': '101', 'filename': entry['name'], 'size': entry['size'],
                                  'content_url': url + '/evidence.tgz', 'mime_type': 'application/gzip'}]}
        collected, gaps = sources.collect_sources(issue, [], [], [])
        assert not gaps and len(collected) == 1
        assert collected[0]['kind'] == 'jira_attachment'
        assert collected[0]['archive']['source_id'] == 'jira:attachment:101'
        assert collected[0]['archive']['member'] == 'output/results.tsv'
        assert 'good\t44\nbad\t442' in collected[0]['text']
        monkeypatch.setattr(sources, 'MAX_ARCHIVE_BYTES', 16)
        # An inaccurate metadata size must not bypass the actual download limit.
        issue['attachments'][0]['size'] = 1
        collected, gaps = sources.collect_sources(issue, [], [], [])
        assert not collected and '附件读取失败' in gaps[0]['reason']
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)


def test_unknown_text_and_referenced_files_survive_dependency_tree(tmp_path, monkeypatch):
    monkeypatch.setattr(sources, 'MAX_SOURCE_FILES', 4)
    report = tmp_path / 'REPORT.md'
    report.write_text('Check baseline.stdout, changed.perf and important.hpp')
    entry = archive(tmp_path / 'evidence.tar.gz', [
        *[(f'third_party/headers/{i}.hpp', f'int type{i};'.encode()) for i in range(30)],
        ('third_party/headers/important.hpp', b'int candidate();'),
        ('run/baseline.stdout', b'wall_time=10\n'),
        ('run/changed.perf', b'wall_time=12\n'),
    ])
    collected, gaps = sources.collect_sources({}, [], [], [entry, record(report)])
    assert {s['name'].split(' / ')[-1] for s in collected} == {
        'REPORT.md', 'run/baseline.stdout', 'run/changed.perf', 'third_party/headers/important.hpp'}
    assert all(g['category'] == 'skipped' for g in gaps)


def test_report_inside_archive_precedes_unreferenced_loose_files(tmp_path, monkeypatch):
    monkeypatch.setattr(sources, 'MAX_SOURCE_FILES', 2)
    noise = []
    for i in range(5):
        path = tmp_path / f'{i}.h'
        path.write_text(f'int h{i};')
        noise.append(record(path))
    entry = archive(tmp_path / 'evidence.tar.gz', [('REPORT.md', b'# Report\nsee experiment.stdout\n'), ('experiment.stdout', b'observed=42\n')])
    collected, gaps = sources.collect_sources({}, [], [], [*noise, entry])
    assert len(collected) == 2
    assert all('archive' in s for s in collected)
    assert len(gaps) == 5


def test_duplicates_keep_all_provenance_without_consuming_budget(tmp_path, monkeypatch):
    monkeypatch.setattr(sources, 'MAX_SOURCE_FILES', 2)
    entry = archive(tmp_path / 'evidence.tar.gz', [('a.log', b'same log\n'), ('b.log', b'same log\n'), ('repro.cu', b'int main(){}')])
    collected, gaps = sources.collect_sources({}, [], [], [entry])
    assert not gaps
    assert len(collected) == 2
    log = next(s for s in collected if s['text'] == 'same log\n')
    assert log['copies'][0]['archive']['member'] == 'b.log'
    assert log['copies'][0]['turn_id'] == 'jat_1'


def test_unknown_extension_plain_text_is_read_but_binary_is_not(tmp_path):
    path = tmp_path / 'input.lammps'
    path.write_text('units metal\nrun 50\n')
    binary = tmp_path / 'opaque'
    binary.write_bytes(b'\x01\x02\x03')
    collected, gaps = sources.collect_sources({}, [], [], [record(path), record(binary)])
    assert len(collected) == 1 and collected[0]['text'].startswith('units metal')
    assert len(gaps) == 1
