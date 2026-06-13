"""Global exception handlers for the FastAPI app.

Mapping:
  AuthzError  → 403 Forbidden
  PermissionError → 401 Unauthorized
  ValueError | RuntimeError → 400 Bad Request
  catch-all → 500 Internal Server Error

# TODO Phase 2 — register handlers via app.add_exception_handler(...)
"""
from __future__ import annotations

from fastapi import Request
from fastapi.responses import JSONResponse


class AuthzError(Exception):
    """Raised when the authenticated user lacks the required role/permission."""


async def authz_error_handler(request: Request, exc: AuthzError) -> JSONResponse:
    return JSONResponse(status_code=403, content={"ok": False, "error": str(exc)})


async def permission_error_handler(request: Request, exc: PermissionError) -> JSONResponse:
    return JSONResponse(status_code=401, content={"ok": False, "error": str(exc)})


async def value_error_handler(request: Request, exc: ValueError) -> JSONResponse:
    return JSONResponse(status_code=400, content={"ok": False, "error": str(exc)})


async def runtime_error_handler(request: Request, exc: RuntimeError) -> JSONResponse:
    return JSONResponse(status_code=400, content={"ok": False, "error": str(exc)})


async def generic_error_handler(request: Request, exc: Exception) -> JSONResponse:
    # TODO Phase 2 — log the full traceback via structlog
    return JSONResponse(
        status_code=500,
        content={"ok": False, "error": "内部服务器错误"},
    )


def register_error_handlers(app) -> None:  # type: ignore[type-arg]
    """Register all exception handlers on the FastAPI app instance."""
    app.add_exception_handler(AuthzError, authz_error_handler)
    app.add_exception_handler(PermissionError, permission_error_handler)
    app.add_exception_handler(ValueError, value_error_handler)
    app.add_exception_handler(RuntimeError, runtime_error_handler)
    app.add_exception_handler(Exception, generic_error_handler)
