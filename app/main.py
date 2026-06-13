"""FastAPI application factory.

create_app():
  - lifespan: loads LDAP config into app.state, initialises DB
  - includes all routers
  - mounts Vite build output as StaticFiles (last, so /api/* takes priority)
  - registers exception handlers via api/errors.py

# TODO Phase 2 — implement fully
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    # TODO Phase 2 — load LDAP config, call connect() to init DB, etc.
    yield


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    # TODO Phase 2 — include routers, register error handlers, mount StaticFiles
    app = FastAPI(title="HPC App 发布系统", lifespan=_lifespan)
    return app


app = create_app()
