"""Isolated UI fixture: no production database, background workers or integrations."""
import argparse
import os
from pathlib import Path
import tempfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", required=True, type=int)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="release-gate-preview-") as directory:
        root = Path(directory)
        os.environ.update(DB_PATH=str(root / "release.db"), RUNTIME_CONF_PATH=str(root / "missing.conf"),
                          ADMIN_PASSWORD_FILE=str(root / "admin"), APP_INFO_SYNC_ENABLED="false")
        from fastapi import FastAPI
        import uvicorn
        from app.api.errors import register_error_handlers
        from app.api.routers import auth, cicd, release_gate, state
        from app.db.connection import connect
        from app.domain.authn import hash_password
        from app.services import release_gate_service
        from tests.conftest import seed_app, seed_cycle, seed_signup

        conn = connect(root / "release.db")
        for name, role in (("gate_rm", "RM"), ("gate_spd", "SPD")):
            conn.execute("INSERT INTO users(username,password_hash,role,display_name) VALUES (?,?,?,?)",
                         (name, hash_password("test-password"), role, name))
        conn.commit()
        release = seed_cycle(conn, name="3.10.2")
        app = seed_app(conn, git_url="hpc_testapp", commit_id="a" * 40)
        seed_signup(conn, release, app)
        release_gate_service.save_version(conn, release_gate.VersionWrite(maca_version="3.10.2.11", release_id=release,
            note="QA release baseline"), {"username": "gate_rm", "role": "RM"})
        conn.close()
        app = FastAPI()
        register_error_handlers(app)
        for router in (auth.router, cicd.router, release_gate.router, state.router):
            app.include_router(router)
        uvicorn.run(app, host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
