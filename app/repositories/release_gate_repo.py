"""Exact release-build versions; keep disabled entries and the existing audit trail."""


def list_versions(conn):
    return [dict(row) for row in conn.execute(
        "SELECT v.*, r.name AS release_name, r.released_locked FROM release_build_versions v "
        "JOIN releases r ON r.id=v.release_id ORDER BY v.updated_at DESC, v.maca_version")]


def get_version(conn, version):
    row = conn.execute("SELECT * FROM release_build_versions WHERE maca_version=?", (version,)).fetchone()
    return dict(row) if row else None


def save_version(conn, version, release_id, enabled, note, revision, user, ts):
    conn.execute(
        "INSERT INTO release_build_versions (maca_version, release_id, enabled, note, revision, updated_at, updated_by) "
        "VALUES (?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(maca_version) DO UPDATE SET enabled=excluded.enabled, note=excluded.note, "
        "revision=excluded.revision, updated_at=excluded.updated_at, updated_by=excluded.updated_by",
        (version, release_id, int(enabled), note, revision, ts, user))
