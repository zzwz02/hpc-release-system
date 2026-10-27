"""Immutable test inputs and batches; execution stays in the agent queue."""
import json

from app.config import settings
from app.repositories import jira_test_evaluation_repo as evaluations
from app.repositories.base import dumps_json, new_id
from app.timeutil import beijing_timestamp


def initialize(conn):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS jira_test_cases (
            id TEXT PRIMARY KEY, created_by TEXT NOT NULL, created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS jira_test_versions (
            id TEXT PRIMARY KEY,
            case_id TEXT NOT NULL REFERENCES jira_test_cases(id),
            version INTEGER NOT NULL, title TEXT NOT NULL,
            input_json TEXT NOT NULL, input_sha256 TEXT NOT NULL,
            criteria TEXT NOT NULL, reference TEXT NOT NULL,
            provenance_json TEXT NOT NULL,
            created_by TEXT NOT NULL, created_at TEXT NOT NULL,
            UNIQUE(case_id, version)
        );
        CREATE TABLE IF NOT EXISTS jira_test_batches (
            id TEXT PRIMARY KEY,
            version_id TEXT NOT NULL REFERENCES jira_test_versions(id),
            config_json TEXT NOT NULL, pack_json TEXT NOT NULL,
            created_by TEXT NOT NULL, created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS jira_test_runs (
            conversation_id TEXT PRIMARY KEY REFERENCES jira_agent_conversations(id),
            batch_id TEXT NOT NULL REFERENCES jira_test_batches(id),
            ordinal INTEGER NOT NULL, UNIQUE(batch_id, ordinal)
        );
        CREATE TABLE IF NOT EXISTS jira_test_reviews (
            id TEXT PRIMARY KEY,
            conversation_id TEXT NOT NULL REFERENCES jira_test_runs(conversation_id),
            decision TEXT NOT NULL CHECK(decision IN ('pass','partial','fail','environment','platform','cancelled','unknown')),
            note TEXT NOT NULL, reviewer TEXT NOT NULL, created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS jira_test_builtins (
            key TEXT PRIMARY KEY,
            case_id TEXT NOT NULL UNIQUE REFERENCES jira_test_cases(id),
            applied_hash TEXT NOT NULL, available_hash TEXT NOT NULL
        );
    """)
    schema = conn.execute("SELECT sql FROM sqlite_master WHERE name='jira_test_reviews'").fetchone()[0]
    if "'unknown'" not in schema:
        conn.executescript("""
            BEGIN IMMEDIATE;
            CREATE TABLE jira_test_reviews_new (
                id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL REFERENCES jira_test_runs(conversation_id),
                decision TEXT NOT NULL CHECK(decision IN ('pass','partial','fail','environment','platform','cancelled','unknown')),
                note TEXT NOT NULL, reviewer TEXT NOT NULL, created_at TEXT NOT NULL
            );
            INSERT INTO jira_test_reviews_new SELECT * FROM jira_test_reviews;
            DROP TABLE jira_test_reviews;
            ALTER TABLE jira_test_reviews_new RENAME TO jira_test_reviews;
            COMMIT;
        """)
    evaluations.initialize(conn)


def one(conn, sql, args=()):
    row = conn.execute(sql, args).fetchone()
    return dict(row) if row else None


def versions(conn, case_id):
    return [dict(row) for row in conn.execute(
        "SELECT * FROM jira_test_versions WHERE case_id=? ORDER BY version DESC", (case_id,))]


def cases(conn):
    rows = [dict(row) for row in conn.execute("""
        SELECT v.case_id AS id, v.title, v.id AS version_id, v.criteria, v.version, v.created_at, v.created_by,
               COALESCE(b.key, '') AS builtin_key,
               COALESCE(b.available_hash!='' AND b.available_hash!=b.applied_hash, 0) AS builtin_update
        FROM jira_test_versions v LEFT JOIN jira_test_builtins b ON b.case_id=v.case_id
        WHERE v.version=(
            SELECT MAX(version) FROM jira_test_versions WHERE case_id=v.case_id)
        ORDER BY v.created_at DESC, v.rowid DESC
    """)]
    for row in rows:
        row['builtin_update'] = bool(row['builtin_update'])
    return rows


def builtin(conn, case_id):
    row = one(conn, """SELECT key,
        available_hash!='' AND available_hash!=applied_hash AS update_available
        FROM jira_test_builtins WHERE case_id=?""", (case_id,))
    if row:
        row['update_available'] = bool(row['update_available'])
    return row


def bind_builtin(conn, key, case_id, applied, available):
    conn.execute('''INSERT INTO jira_test_builtins VALUES(?,?,?,?)
        ON CONFLICT(key) DO UPDATE SET applied_hash=excluded.applied_hash,
        available_hash=excluded.available_hash''', (key, case_id, applied, available))


def matching_cases(conn, digest, title, criteria="", reference=""):
    rows = [dict(row) for row in conn.execute("""
        SELECT v.case_id AS id, v.title, v.input_sha256, v.criteria, v.reference FROM jira_test_versions v
        WHERE v.version=(SELECT MAX(version) FROM jira_test_versions WHERE case_id=v.case_id)
          AND (v.input_sha256=? OR v.title=?) ORDER BY v.rowid DESC
    """, (digest, title))]
    return {'duplicates': [{'id': row['id'], 'title': row['title'], 'criteria': row['criteria'], 'reference': row['reference'],
                            'review_differs': row['criteria'].strip() != criteria.strip() or row['reference'].strip() != reference.strip()} for row in rows
                           if row['input_sha256'] == digest],
            'same_name': [{'id': row['id'], 'title': row['title']} for row in rows
                          if row['title'] == title and row['input_sha256'] != digest]}


def save_version(conn, user, case_id, title, inputs, digest, criteria, reference, provenance):
    if not case_id:
        case_id = new_id('jtc')
        conn.execute('INSERT INTO jira_test_cases VALUES(?,?,?)',
                     (case_id, user, beijing_timestamp()))
    version = conn.execute('SELECT COALESCE(MAX(version),0)+1 FROM jira_test_versions WHERE case_id=?',
                           (case_id,)).fetchone()[0]
    conn.execute('INSERT INTO jira_test_versions VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                 (new_id('jtv'), case_id, version, title, dumps_json(inputs), digest,
                  criteria, reference, dumps_json(provenance), user, beijing_timestamp()))
    return case_id


def context(conn, conversation_id):
    row = one(conn, """SELECT v.input_json, v.input_sha256, b.config_json, b.pack_json
        FROM jira_test_runs r JOIN jira_test_batches b ON b.id=r.batch_id
        JOIN jira_test_versions v ON v.id=b.version_id WHERE r.conversation_id=?""", (conversation_id,))
    if row is None:
        raise ValueError('测试运行记录缺失')
    return {key.removesuffix('_json'): json.loads(value) if key.endswith('_json') else value
            for key, value in row.items()}


def _targets(row):
    config = json.loads(row['config_json'])
    if config.get('execution'):
        execution = config['execution']
        if execution['mode'] == 'custom':
            return tuple(item['target'] for item in execution['targets'])
        return tuple(item['target'] for item in config.get('execution_snapshot', {}).get('results', []))
    if row['machine']:
        return (row['machine'],)
    return tuple(item['ssh_target'] for item in json.loads(row['config_json']).get('machines', []))


def machine_targets(conn, conversation_id):
    row = one(conn, """SELECT c.machine,b.config_json FROM jira_test_runs r
        JOIN jira_test_batches b ON b.id=r.batch_id
        JOIN jira_agent_conversations c ON c.id=r.conversation_id WHERE c.id=?""", (conversation_id,))
    if row is None:
        raise ValueError('测试运行记录缺失')
    return _targets(row)


def can_schedule(conn, conversation_id):
    """Serialize overlapping candidate hosts, including delivery and recovery."""
    ahead = conn.execute("""SELECT count(*) FROM jira_agent_turns t
        JOIN jira_test_runs r ON r.conversation_id=t.conversation_id
        JOIN jira_agent_turns current ON current.conversation_id=?
        WHERE t.id!=current.id AND (t.status='running' OR t.delivery_status='collecting'
            OR (t.status='queued' AND t.rowid<current.rowid))""", (conversation_id,)).fetchone()[0]
    if ahead >= max(1, settings.jira_testing_max_concurrent):
        return False
    hosts = {target.split('@', 1)[1].lower() for target in machine_targets(conn, conversation_id)}
    rows = conn.execute("""
        SELECT c.machine,b.config_json FROM jira_agent_conversations c
        JOIN jira_agent_turns t ON t.conversation_id=c.id
        JOIN jira_test_runs r ON r.conversation_id=c.id
        JOIN jira_test_batches b ON b.id=r.batch_id
        JOIN jira_agent_turns current_turn ON current_turn.conversation_id=?
        WHERE c.id!=current_turn.conversation_id
          AND (t.status='running' OR t.delivery_status='collecting'
               OR (t.status='queued' AND t.rowid<current_turn.rowid))
    """, (conversation_id,))
    return not any(hosts.intersection(target.split('@', 1)[1].lower() for target in _targets(row))
                   for row in rows)


def batches(conn, case_id, batch_id=""):
    rows = conn.execute("""SELECT b.id,b.version_id,b.config_json,b.created_by,b.created_at,v.version
        FROM jira_test_batches b JOIN jira_test_versions v ON v.id=b.version_id
        WHERE v.case_id=? AND (?='' OR b.id=?) ORDER BY b.rowid DESC""", (case_id, batch_id, batch_id))
    result = []
    for row in rows:
        batch = dict(row)
        batch['config'] = json.loads(batch.pop('config_json'))
        for key in ('instructions', 'prompt', 'output_schema'):
            batch['config'].pop(key, None)
        batch['runs'] = []
        for run in conn.execute("""SELECT r.*,t.status,t.delivery_status,t.error,t.conclusion,
            t.started_at,t.finished_at,c.machine_used,t.machines_used_json FROM jira_test_runs r
            JOIN jira_agent_conversations c ON c.id=r.conversation_id
            JOIN jira_agent_turns t ON t.conversation_id=c.id WHERE r.batch_id=? ORDER BY r.ordinal""",
                               (batch['id'],)):
            item = dict(run)
            item["machines_used"] = json.loads(item.pop("machines_used_json", "[]"))
            item['reviews'] = [dict(review) for review in conn.execute(
                'SELECT * FROM jira_test_reviews WHERE conversation_id=? ORDER BY rowid DESC',
                (item['conversation_id'],))]
            evaluations.annotate(conn, item)
            batch['runs'].append(item)
        batch['counts'] = {key: 0 for key in ('pending','pass','partial','fail','environment','platform','cancelled','unknown')}
        for item in batch['runs']:
            key = item['reviews'][0]['decision'] if item['reviews'] else 'pending'
            batch['counts'][key] += 1
        batch['current_counts'] = evaluations.counts(batch['runs'])
        result.append(batch)
    return result
