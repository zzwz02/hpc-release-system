"""Cross-case submission is atomic; preparation is a recoverable background step."""
import json

from app.api.errors import ApiError
from app.db.connection import transaction
from app.domain import jira_testing as domain
from app.repositories import jira_testing_repo as repo, jira_test_evaluation_repo as evaluation, jira_agent_repo as agent
from app.repositories.base import new_id, dumps_json
from app.services import jira_testing_service as service, jira_agent_runner as execution
from app.timeutil import beijing_timestamp
from app.services import jira_agent_preflight as preflight


def occupied(conn, ids):
    active = {row['case_id'] for row in conn.execute('''SELECT v.case_id FROM jira_test_runs r
        JOIN jira_test_batches b ON b.id=r.batch_id JOIN jira_test_versions v ON v.id=b.version_id
        JOIN jira_agent_turns t ON t.conversation_id=r.conversation_id
        WHERE t.status IN ('queued','running') OR t.delivery_status='collecting' ''')}
    active.update(row['case_id'] for row in conn.execute('''SELECT i.case_id FROM jira_test_suite_items i
        JOIN jira_test_suites s ON s.id=i.suite_id WHERE s.status='preparing' '''))
    return active.intersection(ids)


async def submit(user, body):
    request = {key: value for key, value in body.items() if key != 'ssh_key_session_id'}
    request['username'] = user['username']
    digest = domain.digest(request)
    with execution.open_db() as conn:
        previous = repo.one(conn, 'SELECT * FROM jira_test_suites WHERE request_id=?', (body['request_id'],))
    if previous:
        if previous['request_hash'] != digest:
            raise ApiError(409, '请求标识已被其他批次使用')
        return detail(previous['id'])
    group = execution.load_groups().get(body['agent_group'])
    if not group:
        raise ValueError('请选择有效的分析组')
    selection = await service._model_choice(user, group.name, body)
    sid = new_id('jts')
    with execution.open_db() as conn:
        conn.execute('BEGIN IMMEDIATE')
        with transaction(conn):
            previous = repo.one(conn, 'SELECT * FROM jira_test_suites WHERE request_id=?', (body['request_id'],))
            if previous:
                if previous['request_hash'] != digest:
                    raise ApiError(409, '请求标识已被其他批次使用')
                sid = previous['id']
            else:
                cases = repo.cases(conn)
                selected = body.get('case_ids') or [row['id'] for row in cases]
                if not selected or len(selected) != len(set(selected)) or len(selected) > 100:
                    raise ValueError('请选择 1 至 100 个不重复的用例')
                versions = {row['id']: row for row in cases}
                if set(selected) - versions.keys():
                    raise ApiError(404, '所选用例已不存在')
                conflicts = occupied(conn, selected)
                if conflicts:
                    raise ApiError(409, '以下用例已有测试排队或执行中：' + '、'.join(versions[key]['title'] for key in selected if key in conflicts))
                scope = preflight.test_scope(conn, 'suite', body)
                receipt = preflight.resolve(conn, user, group, body, scope)
                config = service.run_config(group, receipt, selection)
                conn.execute('INSERT INTO jira_test_suites VALUES(?,?,?,?,?,?,?,?)',
                             (sid, body['request_id'], digest, 'preparing', dumps_json(config),
                              dumps_json({'username': user['username']}), '', beijing_timestamp()))
                preflight.consume(conn, user, body, receipt, sid)
                for ordinal, case_id in enumerate(selected):
                    conn.execute('INSERT INTO jira_test_suite_items VALUES(?,?,?,?,?)',
                                 (sid, case_id, versions[case_id]['version_id'], '', ordinal))
    return detail(sid)


def detail(sid):
    with execution.open_db() as conn:
        suite = repo.one(conn, 'SELECT id,status,error,created_at FROM jira_test_suites WHERE id=?', (sid,))
        legacy = repo.one(conn, '''SELECT b.id,b.version_id,b.created_at,v.case_id,v.title
            FROM jira_test_batches b JOIN jira_test_versions v ON v.id=b.version_id WHERE b.id=?''',
            (sid.removeprefix('legacy:'),)) if sid.startswith('legacy:') else None
        if legacy:
            suite = {'id': sid, 'status': 'ready', 'error': '', 'created_at': legacy['created_at']}
        if not suite:
            raise ApiError(404, '测试批次不存在')
        entries = [dict(row) for row in conn.execute('''SELECT i.*,v.title FROM jira_test_suite_items i
            JOIN jira_test_versions v ON v.id=i.version_id WHERE i.suite_id=? ORDER BY i.ordinal''', (sid,))] if not legacy else [
                {'case_id': legacy['case_id'], 'version_id': legacy['version_id'], 'title': legacy['title'], 'batch_id': legacy['id']}]
        for entry in entries:
            batches = repo.batches(conn, entry['case_id'], entry['batch_id']) if entry['batch_id'] else []
            entry['batch'] = next(iter(batches), None)
        runs = [run for entry in entries if entry['batch'] for run in entry['batch']['runs']]
        missing = sum(not entry['batch'] or not entry['batch']['runs'] for entry in entries)
        suite.update(entries=entries, counts=evaluation.counts(runs), total=len(runs) + missing,
                     finished=sum(run['status'] not in ('queued','running') and run['delivery_status'] != 'collecting' for run in runs))
        suite['ai_counts'] = {}
        for run in runs:
            review = run['evaluation']
            key = (review['result']['decision'] if review and review['status'] == 'completed'
                   else 'evaluation_failed' if review and review['status'] == 'failed'
                   else 'skipped' if review and review['status'] == 'skipped' else 'pending')
            suite['ai_counts'][key] = suite['ai_counts'].get(key, 0) + 1
        if suite['status'] == 'stopped' and missing:
            suite['counts']['cancelled'] = suite['counts'].get('cancelled', 0) + missing
            suite['finished'] += missing
        elif missing:
            suite['counts']['preparation_failed' if suite['status'] == 'failed' else 'preparing'] = missing
        suite['evaluated'] = sum(bool(run['evaluation'] and run['evaluation']['status'] in ('completed','failed','skipped')) for run in runs)
        suite['reviewed'] = sum(run['current']['source'] == 'human' for run in runs)
        suite['active'] = suite['status'] == 'preparing' or any(
            run['status'] in ('queued','running') or run['delivery_status'] == 'collecting'
            or (run['evaluation'] and run['evaluation']['status'] in ('awaiting','queued','running')) for run in runs)
        suite['phase'] = ('preparing' if suite['status'] == 'preparing' else 'preparation_failed' if suite['status'] == 'failed'
                          else 'running' if any(run['status'] in ('queued','running') or run['delivery_status'] == 'collecting' for run in runs)
                          else 'evaluating' if suite['active'] else 'stopped' if suite['status'] == 'stopped' else 'completed')
        return suite


def listing():
    with execution.open_db() as conn:
        ids = [row['id'] for row in conn.execute('''SELECT id,created_at FROM jira_test_suites
            UNION ALL SELECT 'legacy:' || b.id,b.created_at FROM jira_test_batches b
            WHERE NOT EXISTS (SELECT 1 FROM jira_test_suite_items i WHERE i.batch_id=b.id)
            ORDER BY created_at DESC,id DESC LIMIT 50''')]
    return {'suites': [detail(sid) for sid in ids]}


def stop(sid):
    with execution.open_db() as conn:
        conn.execute('BEGIN IMMEDIATE')
        with transaction(conn):
            if not repo.one(conn, 'SELECT 1 FROM jira_test_suites WHERE id=?', (sid,)):
                raise ApiError(404, '批次不存在')
            conn.execute("UPDATE jira_test_suites SET status='stopped' WHERE id=?", (sid,))
            for row in conn.execute('''SELECT t.id,t.conversation_id FROM jira_test_suite_items i
                JOIN jira_test_runs r ON r.batch_id=i.batch_id JOIN jira_agent_turns t ON t.conversation_id=r.conversation_id
                WHERE i.suite_id=? AND t.status='queued' ''', (sid,)):
                if agent.cancel_queued_turn(conn, row['id'], '已停止后续测试'):
                    agent.add_event(conn, conversation_id=row['conversation_id'], turn_id=row['id'], kind='status',
                                    payload={'status': 'cancelled', 'text': '已停止后续测试'})
    execution.runner.wake()
    return detail(sid)


def retry(sid):
    with execution.open_db() as conn, transaction(conn):
        suite = repo.one(conn, 'SELECT * FROM jira_test_suites WHERE id=?', (sid,))
        if not suite or suite['status'] != 'failed':
            raise ApiError(409, '只有准备失败的批次可重试')
        ids = [row['case_id'] for row in conn.execute('SELECT case_id FROM jira_test_suite_items WHERE suite_id=?', (sid,))]
        if occupied(conn, ids):
            raise ApiError(409, '用例已有其他测试，请等待结束')
        conn.execute("UPDATE jira_test_suites SET status='preparing',error='' WHERE id=?", (sid,))
    return detail(sid)
