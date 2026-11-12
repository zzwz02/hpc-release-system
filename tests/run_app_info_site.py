"""Isolated App detail browser checks with local Gerrit responses."""
import argparse
import json
import os
from pathlib import Path
import tempfile


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--signup", action="store_true", help="Include signup dialog scenarios")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="app-info-site-") as temporary:
        folder = Path(temporary)
        os.environ.update(
            DB_PATH=str(folder / "main.db"), ADMIN_PASSWORD_FILE=str(folder / "admin"),
            RUNTIME_CONF_PATH=str(folder / "missing.conf"),
            ASSISTANT_DATABASE_URL=f"sqlite:///{folder}/assistant.db",
            JIRA_AGENT_DATABASE_URL=f"sqlite:///{folder}/agent.db",
            JIRA_AGENT_DATA_DIR=str(folder / "data"),
            JIRA_AGENT_RUNNER_ENABLED="false", JIRA_LEARNING_RUNNER_ENABLED="false",
            JIRA_TESTING_RUNNER_ENABLED="false", APP_INFO_SYNC_ENABLED="false",
        )
        from app.config import settings
        from app.db.connection import connect
        from app.domain.authn import hash_password
        from app.integrations import gerrit
        from app.main import create_app
        from app.services import catalog_service
        from tests.conftest import close_signup, make_app_info, seed_app, seed_cycle, seed_signup
        import uvicorn

        conn = connect(settings.db_path)
        for username, role in [
            ("app_rm", "RM"), ("app_owner", "Owner"), ("app_other", "Owner"),
            ("app_qa", "QA"), ("app_guest", "Guest"), ("app_spd", "SPD"),
        ]:
            conn.execute("INSERT INTO users(username,password_hash,role,display_name) VALUES (?,?,?,?)",
                         (username, hash_password("test-password"), role, username))
        conn.commit()
        for app_id in ["info-check", "info-error"]:
            seed_app(conn, app_id, official_name=app_id, owners=["app_owner"], app_type="性能基准测试")
        catalog_service.update_profile(
            conn, "info-check", {"description": "保留的手册描述", "official_url": "https://example.test"},
            user="app_rm", role="RM",
        )
        for name in ["browser-cycle-a", "browser-cycle-b"]:
            release_id = seed_cycle(conn, name=name)
            seed_signup(conn, release_id, "info-check")
            close_signup(conn, release_id)
        if args.signup:
            from app.services import cicd_service

            for app_id in ["own-ready-a", "own-ready-b", "own-race", "own-stopped", "own-missing", "own-stopping", "other-ready"]:
                seed_app(
                    conn, app_id, official_name=app_id,
                    owners=["app_other"] if app_id == "other-ready" else ["app_owner"],
                    cicd_status="stopped" if app_id == "own-stopped" else "running",
                    app_info=None if app_id == "own-missing" else make_app_info(app_version="2.0"),
                )
            cicd_service.submit_request(
                conn, task_id="own-stopping", request_type="modify",
                payload={"status": {"old": "running", "new": "stopped"}},
                submitter="app_owner", submitter_role="Owner", submitter_display="Owner", source="app_workbench",
            )
            for index, name in enumerate(["signup-open-a", "signup-open-b"]):
                release_id = seed_cycle(conn, name=name)
                catalog = [
                    {"component": "Driver", "version": f"driver-{index}", "note": "驱动说明"},
                    {"component": "SDK", "version": f"sdk-{index}", "note": ""},
                    {"component": "PyTorch", "version": f"torch-{index}", "note": ""},
                ]
                from app.services import cycle_service

                cycle_service.save_environment(conn, release_id, catalog, user="app_rm", role="RM")
                if index == 0:
                    seed_signup(conn, release_id, "own-ready-b")
        conn.close()

        def head_commit(url, *_args, **_kwargs):
            if url.endswith("hpc_info-error"):
                raise RuntimeError("Gerrit 测试连接失败")
            return "expanded-head"

        gerrit.head_commit = head_commit
        gerrit.fetch_app_info_at = lambda *_a, **_k: json.dumps(
            make_app_info(app_version="2.0", chips=["c500", "c600"])
        )
        uvicorn.run(create_app(), host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
