"""Independent component validation records, readable alongside compatibility inventory."""
import sqlite3

from fastapi import APIRouter, Depends, Response

from app.deps import get_db, require_capability, require_tab_access
from app.services import component_compatibility_service as service

router = APIRouter(prefix="/api/component-compatibility", tags=["component-compatibility"])
reader = require_tab_access("dependencies")
writer = require_capability("qa.edit")


@router.get("/components")
def components(response: Response, user: dict = Depends(reader), conn: sqlite3.Connection = Depends(get_db)):
    response.headers["Cache-Control"] = "no-store"
    return service.components(conn, user=user["username"], role=user["role"])


@router.get("")
def reports(response: Response, user: dict = Depends(reader), conn: sqlite3.Connection = Depends(get_db)):
    response.headers["Cache-Control"] = "no-store"
    return service.list_reports(conn)


@router.post("")
def create(body: dict, user: dict = Depends(writer), conn: sqlite3.Connection = Depends(get_db)):
    return service.create(conn, body, user=user["username"], role=user["role"])


@router.get("/{report_id}")
def detail(report_id: int, response: Response, user: dict = Depends(reader), conn: sqlite3.Connection = Depends(get_db)):
    response.headers["Cache-Control"] = "no-store"
    return service.get_report(conn, report_id)


@router.post("/{report_id}/apps/{app_id}")
def save_app(report_id: int, app_id: str, body: dict, user: dict = Depends(writer),
             conn: sqlite3.Connection = Depends(get_db)):
    return service.save_app(conn, report_id, app_id, body, user=user["username"], role=user["role"])
