"""Isolated website for the MCP setup browser test; no external integrations."""
import argparse
import os
from pathlib import Path
import tempfile


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, required=True)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='mcp-setup-site-') as temporary:
        folder = Path(temporary)
        os.environ.update(DB_PATH=str(folder / 'main.db'), ADMIN_PASSWORD_FILE=str(folder / 'admin'),
                          RUNTIME_CONF_PATH=str(folder / 'site.conf'),
                          ASSISTANT_DATABASE_URL=f'sqlite:///{folder}/assistant.db',
                          JIRA_AGENT_DATABASE_URL=f'sqlite:///{folder}/agent.db',
                          JIRA_AGENT_DATA_DIR=str(folder / 'data'), JIRA_AGENT_RUNNER_ENABLED='false',
                          JIRA_LEARNING_RUNNER_ENABLED='false', JIRA_TESTING_RUNNER_ENABLED='false')
        (folder / "site.conf").write_text("[site]\nPUBLIC_BASE_URL = https://different.example\n")
        from app.config import settings
        from app.db.connection import connect
        from app.domain.authn import hash_password
        from app.main import create_app
        import uvicorn
        conn = connect(settings.db_path)
        conn.execute('INSERT INTO users(username,password_hash,role,display_name) VALUES (?,?,?,?)',
                     ('mcp_setup_test', hash_password('test-password'), 'Owner', 'MCP setup test'))
        conn.commit()
        conn.close()
        uvicorn.run(create_app(), host='127.0.0.1', port=args.port)


if __name__ == '__main__':
    main()
