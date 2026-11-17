"""One current workbook per release, with its test configurations and legacy readers."""

from __future__ import annotations

import json


def matching(conn, keys, *, include_history=False):
    result = {}
    unique = list(dict.fromkeys(keys))
    for start in range(0, len(unique), 400):
        batch = unique[start : start + 400]
        batch_keys = set(batch)
        marks = ",".join("?" for _ in batch)
        history = f" OR json_extract(data_json, '$.historical_result_key') IN ({marks})" if include_history else ""
        for row in conn.execute(
            f"SELECT result_key, data_json FROM qa_test_results WHERE result_key IN ({marks})"
            + history + " ORDER BY report_id DESC, rowid DESC",
            batch + batch if include_history else batch,
        ):
            data = json.loads(row["data_json"])
            for key in (row["result_key"], data.get("historical_result_key")):
                if key in batch_keys:
                    result.setdefault(key, data)
    return result


def remove_keys(conn, keys):
    keys = list(keys)
    for start in range(0, len(keys), 400):
        batch = keys[start:start + 400]
        conn.execute(f"DELETE FROM qa_test_results WHERE result_key IN ({','.join('?' for _ in batch)})", batch)


def save(conn, *, filename, content, environments, uploaded_at, uploaded_by, rows, release_id=None):
    cursor = conn.execute(
        "INSERT INTO qa_test_reports"
        "(filename, content, environments_json, uploaded_at, uploaded_by, release_id) "
        "VALUES(?,?,?,?,?,?)",
        (filename, content, json.dumps(environments), uploaded_at, uploaded_by, release_id),
    )
    report_id = cursor.lastrowid
    for key, row, reference in rows:
        conflict = (" ON CONFLICT(result_key) DO UPDATE SET report_id=excluded.report_id, "
                    "data_json=excluded.data_json, image_reference=excluded.image_reference") if release_id is None else ''
        conn.execute(
            "INSERT INTO qa_test_results(result_key, report_id, data_json, image_reference) "
            "VALUES(?,?,?,?)" + conflict,
            (key, report_id, json.dumps(row, ensure_ascii=False), reference),
        )
    return report_id


def list_results(conn, *, release_id=None, legacy_only=False):
    where = ' WHERE f.release_id IS NULL' if legacy_only else (
        ' WHERE f.release_id=? OR f.release_id IS NULL' if release_id else '')
    args = (release_id,) if release_id and not legacy_only else ()
    rows = conn.execute(
        "SELECT r.*, f.release_id, f.filename, f.uploaded_at, f.uploaded_by, f.environments_json "
        "FROM qa_test_results r JOIN qa_test_reports f ON f.id=r.report_id"
        + where + " ORDER BY r.report_id DESC, r.rowid", args,
    )
    return [{**dict(row), "data": json.loads(row["data_json"])} for row in rows]


def legacy_workbooks(conn):
    return [dict(row) for row in conn.execute('SELECT id FROM qa_test_reports WHERE release_id IS NULL')]


def clear_release(conn, release_id, *, keys, legacy_report_ids):
    remove_keys(conn, keys)
    conn.execute('DELETE FROM qa_test_reports WHERE release_id=?', (release_id,))
    for report_id in legacy_report_ids:
        conn.execute('DELETE FROM qa_test_reports WHERE id=? AND release_id IS NULL '
                     'AND NOT EXISTS (SELECT 1 FROM qa_test_results WHERE report_id=?)', (report_id, report_id))


def workbook(conn, report_id):
    return conn.execute(
        "SELECT filename, content FROM qa_test_reports WHERE id=?", (report_id,)
    ).fetchone()
