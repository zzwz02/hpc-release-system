"""Uploaded workbooks are immutable; each seven-field result keeps its latest upload."""

from __future__ import annotations

import json


def matching(conn, keys):
    result = {}
    unique = list(dict.fromkeys(keys))
    for start in range(0, len(unique), 400):
        batch = unique[start : start + 400]
        marks = ",".join("?" for _ in batch)
        for row in conn.execute(
            f"SELECT result_key, data_json FROM qa_test_results WHERE result_key IN ({marks})",
            batch,
        ):
            result[row["result_key"]] = json.loads(row["data_json"])
    return result


def save(conn, *, filename, content, environments, uploaded_at, uploaded_by, rows):
    cursor = conn.execute(
        "INSERT INTO qa_test_reports"
        "(filename, content, environments_json, uploaded_at, uploaded_by) "
        "VALUES(?,?,?,?,?)",
        (filename, content, json.dumps(environments), uploaded_at, uploaded_by),
    )
    report_id = cursor.lastrowid
    for key, row, reference in rows:
        conn.execute(
            "INSERT INTO qa_test_results(result_key, report_id, data_json, image_reference) "
            "VALUES(?,?,?,?) "
            "ON CONFLICT(result_key) DO UPDATE SET report_id=excluded.report_id, "
            "data_json=excluded.data_json, "
            "image_reference=excluded.image_reference",
            (key, report_id, json.dumps(row, ensure_ascii=False), reference),
        )
    return report_id


def search(conn, query, offset, limit):
    # instr treats %, _ and backslashes literally, unlike LIKE.
    where = " WHERE instr(lower(r.data_json), lower(?)) > 0" if query else ""
    args = [query] if query else []
    total = conn.execute("SELECT COUNT(*) FROM qa_test_results r" + where, args).fetchone()[0]
    rows = conn.execute(
        "SELECT r.*, f.filename, f.uploaded_at, f.uploaded_by, f.environments_json "
        "FROM qa_test_results r JOIN qa_test_reports f ON f.id=r.report_id"
        + where
        + " ORDER BY r.report_id DESC, r.rowid LIMIT ? OFFSET ?",
        [*args, limit, offset],
    )
    return total, [{**dict(row), "data": json.loads(row["data_json"])} for row in rows]


def workbook(conn, report_id):
    return conn.execute(
        "SELECT filename, content FROM qa_test_reports WHERE id=?", (report_id,)
    ).fetchone()
