"""Local browser-test site with temporary data and an app-server protocol peer.

Run: .venv/bin/python -m tests.run_jira_testing_site --port 18781
No Jira/GPU/real Codex credentials or services are used.
"""
import argparse
import os
import tempfile
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=18781)
    parser.add_argument('--builtin-cases', action='store_true')
    parser.add_argument('--evaluation', action='store_true')
    parser.add_argument('--learning', action='store_true')
    args = parser.parse_args()
    folder = Path(tempfile.mkdtemp(prefix='jira-testing-browser-'))
    for key, value in {
        'DB_PATH': str(folder / 'main.db'), 'ADMIN_PASSWORD_FILE': str(folder / 'admin'),
        'ASSISTANT_DATABASE_URL': f'sqlite:///{folder}/assistant.db',
        'JIRA_AGENT_DATABASE_URL': f'sqlite:///{folder}/agent.db',
        'JIRA_AGENT_DATA_DIR': str(folder / 'data'), 'RUNTIME_CONF_PATH': str(folder / 'runtime.conf'),
        'JIRA_AGENT_RUNNER_ENABLED': 'true', 'JIRA_LEARNING_RUNNER_ENABLED': 'false',
        'HPC_ADMIN_PASSWORD': 'local-test-only', 'NO_PROXY': '127.0.0.1,localhost', 'no_proxy': '127.0.0.1,localhost',
    }.items():
        os.environ[key] = value

    import pytest
    import uvicorn
    from tests.test_jira_agent import FakeCodex, FakeJira, _write_conf, SYSTEM_MACHINE, KEY_PATH, PUBKEY
    from tests.learning_helpers import knowledge_repo
    from app.config import settings
    from app.main import create_app
    from app.db.jira_agent_connection import connect_jira_agent
    from app.db.connection import transaction
    from app.repositories import jira_agent_repo as repo
    from app.integrations import jira

    if not args.builtin_cases:
        settings.jira_testing_cases_dir = folder / 'builtin-cases'
        settings.jira_testing_cases_dir.mkdir()

    fake = FakeCodex().start()
    fake.files[f'{KEY_PATH}.pub'] = PUBKEY
    _write_conf(folder / 'runtime.conf', fake.url)
    with (folder / 'runtime.conf').open('a') as config:
        config.write('MODEL = test-default\n')
    evaluator = None
    if args.evaluation:
        from tests.test_jira_testing_batch import Evaluator
        evaluator = Evaluator().start()
        with (folder / 'runtime.conf').open('a') as config:
            config.write(f'\n[jira_evaluation:HPC]\nCODEX_WS_URL = {evaluator.url}\nCODEX_WS_TOKEN = {evaluator.token}\nWORKSPACE_ROOT = /evaluation\nMODEL = judge-model\nREASONING_EFFORT = high\nTURN_TIMEOUT_SECONDS = 120\n')
    patch = pytest.MonkeyPatch()
    fake.pack_root = knowledge_repo(folder, patch)
    with connect_jira_agent(settings.jira_agent_database_url) as conn, transaction(conn):
        repo.create_machine(conn, agent_group='HPC', ssh_target=SYSTEM_MACHINE,
                            description='Local protocol test; no GPU execution', created_by='rm')
    learner = None
    if args.learning:
        from tests.test_jira_learning_joint import Learner
        from app.repositories import jira_learning_repo
        learner = Learner().start()
        settings.jira_learning_runner_enabled = True
        with (folder / 'runtime.conf').open('a') as config:
            config.write(f'\n[jira_learning:HPC]\nCODEX_WS_URL={learner.url}\nCODEX_WS_TOKEN={learner.token}\nWORKSPACE_ROOT=/learning/workspaces\nREPOSITORY_REF=refs/heads/main\nMODEL=test-learning\nTURN_TIMEOUT_SECONDS=120\n')
    source = FakeJira()
    if args.learning:
        with connect_jira_agent(settings.jira_agent_database_url) as conn, transaction(conn):
            for key, summary in [('MC3-1', '运行时版本差异的定位'), ('MC3-2', '正常条件下的对照验证')]:
                source.issue_overrides[key] = {'summary': summary, 'description': summary + '，保留可核对的现象和条件。', 'attachments': []}
                jira_learning_repo.add_issue(conn, source.get_issue(key), 'HPC', 'rm')
    for name in ('get_issue', 'get_issue_snapshot', 'download_attachment', 'search_issues', 'add_comment', 'find_agent_comment'):
        setattr(jira, name, getattr(source, name))
    app = create_app()
    faults = {'missing_artifacts': False}
    publication = {'accepted_timeout': False, 'post_calls': 0}
    def post_comment(key, body):
        publication['post_calls'] += 1
        comment_id = source.add_comment(key, body)
        if publication['accepted_timeout']:
            raise TimeoutError('Local test: JIRA accepted the comment, response lost')
        return comment_id
    jira.add_comment = post_comment
    original = fake._result

    def result(ws, method, params):
        if (faults['missing_artifacts'] and method == 'fs/readFile'
                and params.get('path', '').endswith('/artifacts/fix.patch')):
            return None, {'code': -32000, 'message': 'Test artifact unavailable'}
        return original(ws, method, params)

    fake._result = result

    @app.post('/api/_testing/plan')
    def prepare(plan: str = 'complete', read_delay: float = 0, missing_artifacts: bool = False):
        # Local fixture only; production create_app() has no such route.
        assert plan in ('complete', 'hold')
        assert 0 <= read_delay <= 5
        faults['missing_artifacts'] = missing_artifacts
        fake.plans = [plan]
        fake.read_delay = read_delay
        return {'ok': True}

    app.router.routes.insert(0, app.router.routes.pop())

    @app.post('/api/_testing/publication')
    def publication_fault(accepted_timeout: bool = False):
        publication['accepted_timeout'] = accepted_timeout
        return {**publication, 'comments': len(source.comments)}

    app.router.routes.insert(0, app.router.routes.pop())

    if args.learning:
        @app.post('/api/_testing/learning-commit')
        def commit_learning(job_id: str):
            # Only the temporary Git repository created above is writable here.
            from tests.learning_helpers import commit
            with connect_jira_agent(settings.jira_agent_database_url) as conn:
                proposals = jira_learning_repo.list_proposals(conn, job_id)
            files = {file['path']: file['updated_text'] for proposal in proposals
                     if proposal['status'] == 'approved' and not proposal['superseded_by']
                     for file in proposal['files']}
            assert files and settings.jira_learning_repo_root.is_relative_to(folder)
            return {'commit_sha': commit(settings.jira_learning_repo_root, files)}

        app.router.routes.insert(0, app.router.routes.pop())

    @app.post('/api/_testing/execution')
    def execution_fault(body: dict):
        fake.probe_results.clear()
        fake.execution_targets = body.get('targets') or [SYSTEM_MACHINE]
        for target in body.get('unreadable', []):
            fake.probe_results[target] = {'dataset': {'status': 'fail', 'code': 'permission', 'message': '执行账号无法读取共享数据'}}
        return {'ok': True}

    app.router.routes.insert(0, app.router.routes.pop())

    print(f'Isolated test data: {folder}', flush=True)
    try:
        uvicorn.run(app, host='127.0.0.1', port=args.port, log_level='warning')
    finally:
        fake.stop()
        if evaluator:
            evaluator.stop()
        if learner:
            learner.stop()
        patch.undo()


if __name__ == '__main__':
    main()
