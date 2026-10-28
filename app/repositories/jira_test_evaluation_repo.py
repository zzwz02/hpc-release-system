"""Durable batches and evaluations. Analysis inputs never join these snapshots."""
import json

from app.domain import jira_testing as domain
from app.repositories.base import dumps_json, new_id
from app.timeutil import beijing_timestamp


def initialize(conn):
    conn.executescript('''
        CREATE TABLE IF NOT EXISTS jira_test_suites (
            id TEXT PRIMARY KEY, request_id TEXT NOT NULL UNIQUE, request_hash TEXT NOT NULL,
            status TEXT NOT NULL, config_json TEXT NOT NULL, user_json TEXT NOT NULL,
            error TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS jira_test_suite_items (
            suite_id TEXT NOT NULL REFERENCES jira_test_suites(id),
            case_id TEXT NOT NULL REFERENCES jira_test_cases(id),
            version_id TEXT NOT NULL REFERENCES jira_test_versions(id),
            batch_id TEXT NOT NULL DEFAULT '', ordinal INTEGER NOT NULL,
            PRIMARY KEY(suite_id,case_id)
        );
        CREATE TABLE IF NOT EXISTS jira_test_evaluations (
            id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL REFERENCES jira_test_runs(conversation_id),
            status TEXT NOT NULL, snapshot_json TEXT NOT NULL,
            material_json TEXT NOT NULL DEFAULT '{}', material_hash TEXT NOT NULL DEFAULT '',
            evidence_key TEXT NOT NULL DEFAULT '', config_json TEXT NOT NULL DEFAULT '{}',
            result_json TEXT NOT NULL DEFAULT '{}', error TEXT NOT NULL DEFAULT '',
            thread_id TEXT NOT NULL DEFAULT '', turn_id TEXT NOT NULL DEFAULT '', phase TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL, started_at TEXT NOT NULL DEFAULT '', finished_at TEXT NOT NULL DEFAULT ''
        );
        CREATE INDEX IF NOT EXISTS jira_test_evaluation_run ON jira_test_evaluations(conversation_id);
        CREATE TABLE IF NOT EXISTS jira_test_review_links (
            review_id TEXT PRIMARY KEY REFERENCES jira_test_reviews(id),
            evaluation_id TEXT NOT NULL, evidence_key TEXT NOT NULL
        );
    ''')


def one(conn, sql, args=()):
    row = conn.execute(sql, args).fetchone()
    return dict(row) if row else None


def enroll(conn, cid, version):
    snapshot = domain.acceptance(version['criteria'], version['reference'])
    snapshot['sha256'] = domain.digest(snapshot)
    eid = new_id('jte')
    conn.execute('''INSERT INTO jira_test_evaluations(id,conversation_id,status,snapshot_json,created_at)
        VALUES(?,?,'awaiting',?,?)''', (eid, cid, dumps_json(snapshot), beijing_timestamp()))
    return eid


def latest(conn, cid):
    return one(conn, 'SELECT * FROM jira_test_evaluations WHERE conversation_id=? ORDER BY rowid DESC LIMIT 1', (cid,))


def update(conn, eid, **values):
    allowed = {'status', 'material_json', 'material_hash', 'evidence_key', 'config_json', 'result_json',
               'error', 'thread_id', 'turn_id', 'phase', 'started_at', 'finished_at'}
    if not set(values) <= allowed:
        raise ValueError('评价字段无效')
    conn.execute('UPDATE jira_test_evaluations SET ' + ','.join(key + '=?' for key in values) + ' WHERE id=?',
                 (*values.values(), eid))


def evidence_key(conn, cid):
    turn = one(conn, 'SELECT id,status,delivery_status,result_json FROM jira_agent_turns WHERE conversation_id=? ORDER BY seq DESC LIMIT 1', (cid,))
    files = [dict(row) for row in conn.execute('''SELECT id,name,size,sha256,remote_path FROM jira_agent_files
        WHERE conversation_id=? AND direction='output' ORDER BY id''', (cid,))]
    return domain.digest({'turn': turn, 'files': files})


def public(row):
    if not row:
        return None
    result = {key: row[key] for key in ('id', 'status', 'error', 'created_at', 'started_at', 'finished_at', 'material_hash', 'evidence_key')}
    result.update(snapshot=json.loads(row['snapshot_json']), result=json.loads(row['result_json']))
    config = json.loads(row['config_json'])
    result['config'] = {key: config.get(key, '') for key in ('model', 'effort', 'rule_sha256')}
    return result


def annotate(conn, item):
    cid = item['conversation_id']
    row = latest(conn, cid)
    key = evidence_key(conn, cid)
    item['evidence_key'] = key
    item['evaluation'] = public(row)
    for review in item['reviews']:
        link = one(conn, 'SELECT * FROM jira_test_review_links WHERE review_id=?', (review['id'],))
        review['current'] = link['evidence_key'] == key if link else row is None
    review = next((value for value in item['reviews'] if value['current']), None)
    if review:
        item['current'] = {'decision': review['decision'], 'source': 'human', 'note': review['note']}
    elif row and row['status'] == 'completed' and row['evidence_key'] == key:
        result = json.loads(row['result_json'])
        item['current'] = {'decision': result['decision'], 'source': 'ai', 'note': result['summary']}
    else:
        code = 'pending' if row else 'legacy'
        if row:
            code = {'failed': 'evaluation_failed', 'skipped': 'skipped'}.get(row['status'], 'pending')
            if row['status'] == 'skipped':
                code = 'no_criteria' if row['error'] == '未设置通过标准' else 'cancelled' if '取消或中断' in row['error'] else 'no_result'
            if row['status'] == 'completed':
                code = 'stale'
        item['current'] = {'decision': code, 'source': 'system', 'note': row['error'] if row else '历史运行未启用 AI 初评，可人工评价'}


def counts(runs):
    result = {}
    for run in runs:
        key = run['current']['decision']
        result[key] = result.get(key, 0) + 1
    return result
