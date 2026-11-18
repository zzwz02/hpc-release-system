"""Isolated account-token UI/HTTP fixture; no real accounts or background jobs."""
import argparse
import os
from pathlib import Path
import tempfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", required=True, type=int)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="api-token-preview-") as directory:
        root = Path(directory)
        os.environ.update(DB_PATH=str(root / "release.db"), RUNTIME_CONF_PATH=str(root / "missing.conf"),
                          ADMIN_PASSWORD_FILE=str(root / "admin"), APP_INFO_SYNC_ENABLED="false")
        from fastapi import FastAPI
        import uvicorn
        from app.api.errors import register_error_handlers
        from app.api.routers import api_tokens, apps, auth, cicd, release_gate, state
        from app.db.connection import connect
        from app.domain.authn import hash_password
        from app.main import _mount_static
        from app.services import release_gate_service
        from tests.conftest import seed_app, seed_cycle, seed_signup

        conn = connect(root / "release.db")
        for name, role in (("token_owner", "Owner"), ("token_spd", "SPD"), ("token_guest", "Guest")):
            conn.execute("INSERT INTO users(username,password_hash,role,display_name) VALUES (?,?,?,?)",
                         (name, hash_password("test-password"), role, name))
        conn.commit()
        release = seed_cycle(conn, name="3.10.2")
        app_id = seed_app(conn, git_url="hpc_token_fixture", commit_id="a" * 40, owners=["actual-app-owner"])
        seed_signup(conn, release, app_id)
        release_gate_service.save_version(conn, release_gate.VersionWrite(maca_version="3.10.2.11", release_id=release),
                                         {"username": "rm", "role": "RM"})
        conn.close()
        app = FastAPI()
        register_error_handlers(app)
        for router in (auth.router, api_tokens.router, apps.router, cicd.router, release_gate.router, state.router):
            app.include_router(router)
        _mount_static(app)
        uvicorn.run(app, host="0.0.0.0", port=args.port)


if __name__ == "__main__":
    main()
