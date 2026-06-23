"""FastAPI application factory.

create_app():
  - lifespan: loads LDAP config into app.state, initialises DB via connect()
  - includes all routers
  - registers exception handlers via api/errors.py
  - mounts Vite build output as StaticFiles LAST (guarded so missing web_dist
    doesn't crash startup)
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.errors import register_error_handlers
from app.api.routers import admin, apps, artifacts, auth, cicd, qa, releases, state, wiki
from app.config import settings
from app.db.connection import connect
from app.integrations.ldap import load_ldap_config
from app.services import auth_service


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Load LDAP config once at startup — stored on app.state so routers can
    # access it via request.app.state.ldap_config (mirrors server.py:1512-1515).
    app.state.ldap_config = load_ldap_config(settings.ldap_conf_path)
    if app.state.ldap_config.get("enabled"):
        print(f"LDAP authentication enabled: {app.state.ldap_config['uri']}")

    # Initialise the DB (creates schema if it doesn't exist; idempotent).
    conn = connect(settings.db_path)
    auth_service.ensure_admin_user(
        conn,
        password_file=settings.admin_password_file,
    )
    conn.close()

    yield


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    app = FastAPI(title="HPC App 发布系统", lifespan=_lifespan)

    # Register exception handlers (AuthzError→403, PermissionError→401, etc.)
    register_error_handlers(app)

    # Include all routers — auth first so /api/login etc. are available
    app.include_router(auth.router)
    app.include_router(state.router)
    app.include_router(apps.router)
    app.include_router(cicd.router)
    app.include_router(qa.router)
    app.include_router(releases.router)
    app.include_router(wiki.router)
    app.include_router(artifacts.router)
    app.include_router(admin.router)

    # Mount Vite build output LAST so /api/* takes priority.
    # Guard against missing web_dist to avoid crashing startup in dev/CI.
    _mount_static(app)

    return app


def _mount_static(app: FastAPI) -> None:
    """Mount the React build output as StaticFiles if the directory exists.

    Uses an SPA-aware static handler: real files (index.html, /assets/*) are
    served normally, but any *unknown* non-/api path falls back to index.html so
    that client-side routes (e.g. /apps, /cicd) survive a hard refresh / direct
    nav instead of returning a 404.  Unknown /api/* paths keep their JSON 404.
    """
    from pathlib import Path

    web_dist = Path(__file__).resolve().parents[1] / "web_dist"
    if not web_dist.exists():
        return

    from fastapi.responses import FileResponse
    from fastapi.staticfiles import StaticFiles
    from starlette.exceptions import HTTPException as StarletteHTTPException

    index_file = web_dist / "index.html"

    class SPAStaticFiles(StaticFiles):
        async def get_response(self, path, scope):
            try:
                return await super().get_response(path, scope)
            except StarletteHTTPException as exc:
                # Missing file: rewrite client-side routes to index.html, but
                # never mask API paths (those should 404 as JSON via the router).
                if exc.status_code == 404 and not path.startswith("api"):
                    return FileResponse(index_file)
                raise

    # Mounted LAST so /api/* routers take priority; html=True serves index.html
    # at "/" and the subclass handles SPA deep-link fallback for everything else.
    app.mount("/", SPAStaticFiles(directory=str(web_dist), html=True), name="static")


app = create_app()
