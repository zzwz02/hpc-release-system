"""Load packaged inputs without starting runs or replacing local edits."""
import json
import logging
import sqlite3

from app.config import settings
from app.db.connection import transaction
from app.db.jira_agent_connection import connect_jira_agent
from app.domain import jira_test_catalog as catalog, jira_testing as domain
from app.repositories import jira_testing_repo as repo

log = logging.getLogger(__name__)


def synchronize(conn, entries):
    conn.execute('BEGIN IMMEDIATE')
    with transaction(conn):
        # Retired inputs and all their runs stay in the database.
        conn.execute("UPDATE jira_test_builtins SET available_hash=''")
        for entry in entries:
            body = entry['body']
            binding = repo.one(conn, 'SELECT * FROM jira_test_builtins WHERE key=?', (entry['key'],))
            if not binding:
                matches = repo.matching_cases(conn, domain.digest(body['input']), body['title'])
                # Existing imported inputs keep their IDs, notes and run history.
                case_id = next((item['id'] for item in matches['duplicates']
                                if not repo.builtin(conn, item['id'])), '')
                if not case_id:
                    case_id = repo.save_version(conn, 'system', '', body['title'], body['input'],
                                                domain.digest(body['input']), body['criteria'], body['reference'], body['provenance'])
                current = repo.versions(conn, case_id)[0]
                current['input'] = json.loads(current['input_json'])
                applied = entry['hash'] if catalog.fingerprint(current) == entry['hash'] else ''
                repo.bind_builtin(conn, entry['key'], case_id, applied, entry['hash'])
                continue
            current = repo.versions(conn, binding['case_id'])[0]
            current['input'] = json.loads(current['input_json'])
            digest = catalog.fingerprint(current)
            applied = binding['applied_hash']
            legacy = domain.digest({'title': current['title'], 'input': current['input']})
            if digest == entry['hash']:
                applied = entry['hash']
            elif applied in (digest, legacy):
                # Older bindings tracked only title/input. Keep their existing
                # review data when upgrading otherwise unchanged inputs.
                updated = dict(body)
                if digest != applied:
                    for field in ('criteria', 'reference'):
                        updated[field] = current[field] or body[field]
                if catalog.fingerprint(updated) != digest:
                    repo.save_version(conn, 'system', binding['case_id'], updated['title'], updated['input'],
                                      domain.digest(updated['input']), updated['criteria'], updated['reference'],
                                      updated['provenance'])
                applied = entry['hash']
            repo.bind_builtin(conn, entry['key'], binding['case_id'], applied, entry['hash'])


def initialize():
    try:
        entries = catalog.load(settings.jira_testing_cases_dir)
        with connect_jira_agent(settings.jira_agent_database_url) as conn:
            synchronize(conn, entries)
    except (OSError, ValueError, TypeError, KeyError, AttributeError, sqlite3.Error):
        log.exception('内置测试用例加载失败，已保留现有用例')
        return '内置测试用例加载失败，现有用例不受影响；请管理员检查用例资源并重启服务。'
    return ''
