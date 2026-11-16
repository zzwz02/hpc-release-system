"""SQL storage for component validation snapshots and per-App conclusions."""
from __future__ import annotations

from app.repositories.base import dumps_json, loads_json


def list_reports(conn):
    return [dict(row) for row in conn.execute("""
        SELECT r.*, COUNT(a.app_id) AS app_count,
               SUM(CASE WHEN a.qa_status='pass' THEN 1 ELSE 0 END) AS passed,
               SUM(CASE WHEN a.qa_status='fail' THEN 1 ELSE 0 END) AS failed
        FROM component_compatibility_reports r
        LEFT JOIN component_compatibility_apps a ON a.report_id=r.id
        GROUP BY r.id ORDER BY r.id DESC
    """)]


def get_report(conn, report_id):
    row = conn.execute("SELECT * FROM component_compatibility_reports WHERE id=?", (report_id,)).fetchone()
    return dict(row) if row else None


def find_report(conn, component, version):
    row = conn.execute("SELECT id FROM component_compatibility_reports WHERE component=? AND component_version=?",
                       (component, version)).fetchone()
    return row["id"] if row else None


def _app(row):
    item = dict(row)
    return {**loads_json(item.pop("snapshot_json"), {}), **item}


def list_apps(conn, report_id):
    return [_app(row) for row in conn.execute(
        "SELECT * FROM component_compatibility_apps WHERE report_id=? ORDER BY app_id", (report_id,))]


def get_app(conn, report_id, app_id):
    row = conn.execute("SELECT * FROM component_compatibility_apps WHERE report_id=? AND app_id=?",
                       (report_id, app_id)).fetchone()
    return _app(row) if row else None


def create(conn, component, version, apps, *, timestamp, user):
    cursor = conn.execute("""INSERT INTO component_compatibility_reports
        (component, component_version, created_at, created_by) VALUES (?,?,?,?)""",
        (component, version, timestamp, user))
    report_id = cursor.lastrowid
    conn.executemany("INSERT INTO component_compatibility_apps(report_id,app_id,snapshot_json) VALUES (?,?,?)",
                     [(report_id, app["app_id"], dumps_json(app)) for app in apps])
    return report_id


def update_app(conn, report_id, app_id, *, status, comment, revision, timestamp, user):
    return conn.execute("""UPDATE component_compatibility_apps
        SET qa_status=?, qa_comment=?, revision=revision+1, updated_at=?, updated_by=?
        WHERE report_id=? AND app_id=? AND revision=?""",
        (status, comment, timestamp, user, report_id, app_id, revision)).rowcount
