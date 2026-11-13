import datetime as dt

import pytest

from app import timeutil
from app.repositories import apps_repo
from app.services import catalog_service, cycle_service
from tests.conftest import close_qa, close_signup, seed_app, seed_cycle, seed_signup, set_cycle_dates


def test_daily_sync_only_checks_running_active_signups_in_qa_window_once(temp_db_with_path, monkeypatch):
    conn, path = temp_db_with_path
    for app_id in ['active', 'none', 'withdrawn', 'locked', 'stopped', 'signup', 'qa_closed']:
        seed_app(conn, app_id)
    open_a = seed_cycle(conn, name='open-a')
    open_b = seed_cycle(conn, name='open-b')
    closed = seed_cycle(conn, name='published')
    before_signup = seed_cycle(conn, name='signup-open')
    after_qa = seed_cycle(conn, name='qa-closed')
    for app_id in ['active', 'withdrawn', 'stopped']:
        entry = seed_signup(conn, open_a, app_id)
        if app_id == 'withdrawn':
            cycle_service.withdraw(conn, entry['id'], user='rm', role='RM')
    seed_signup(conn, open_b, 'active')
    seed_signup(conn, closed, 'locked')
    seed_signup(conn, before_signup, 'signup')
    seed_signup(conn, after_qa, 'qa_closed')
    close_signup(conn, open_a)
    close_signup(conn, open_b)
    close_qa(conn, after_qa)
    close_qa(conn, closed)
    cycle_service.publish(conn, closed, user='rm', role='RM')
    apps_repo.set_cicd_status(conn, 'stopped', 'stopped')
    conn.commit()
    visited = []
    monkeypatch.setattr(catalog_service.settings, 'db_path', path)
    monkeypatch.setattr(catalog_service, 'sync_app', lambda _conn, app_id, **kw: visited.append(app_id) or {'status': 'unchanged'})
    result = catalog_service.AppInfoSyncLoop()._run_once()
    assert result['checked'] == 1
    assert visited == ['active']
    visited.clear()
    catalog_service.sync_all(conn, user='rm')
    assert set(visited) == {'active', 'none', 'withdrawn', 'locked', 'signup', 'qa_closed'}


def test_no_signups_means_daily_sync_makes_no_git_calls(temp_db, monkeypatch):
    seed_app(temp_db)
    monkeypatch.setattr(catalog_service, 'sync_app', lambda *a, **kw: (_ for _ in ()).throw(AssertionError('unexpected Git access')))
    assert catalog_service.sync_all(temp_db, user='system', signed_up_only=True)['checked'] == 0


@pytest.mark.parametrize('now,expected', [
    ('2026-11-13 01:59:59', 0),
    ('2026-11-13 02:00:00', 1),
    ('2026-11-13 02:00:01', 1),
    ('2026-11-26 23:58:59', 1),
    ('2026-11-26 23:59:00', 0),
    ('2026-11-26 23:59:01', 0),
])
def test_daily_sync_obeys_both_deadline_boundaries(temp_db, monkeypatch, now, expected):
    app_id = seed_app(temp_db)
    release_id = seed_cycle(temp_db)
    seed_signup(temp_db, release_id, app_id)
    set_cycle_dates(temp_db, release_id, signup_deadline='2026-11-13 02:00',
                    qa_deadline='2026-11-26 23:59', release_date='2026-11-30')
    monkeypatch.setattr(timeutil, 'beijing_now', lambda: dt.datetime.fromisoformat(now))
    visited = []
    monkeypatch.setattr(catalog_service, 'sync_app', lambda _conn, app_id, **kw: visited.append(app_id) or {'status': 'unchanged'})
    assert catalog_service.sync_all(temp_db, user='system', signed_up_only=True)['checked'] == expected
    assert visited == ([app_id] if expected else [])


def test_daily_batch_stops_starting_new_syncs_when_qa_deadline_is_reached(temp_db, monkeypatch):
    for app_id in ['a', 'b']:
        seed_app(temp_db, app_id)
    release_id = seed_cycle(temp_db)
    for app_id in ['a', 'b']:
        seed_signup(temp_db, release_id, app_id)
    set_cycle_dates(temp_db, release_id, signup_deadline='2026-11-13 02:00',
                    qa_deadline='2026-11-26 23:59', release_date='2026-11-30')
    now = [dt.datetime(2026, 11, 26, 23, 58, 59)]
    monkeypatch.setattr(timeutil, 'beijing_now', lambda: now[0])
    visited = []

    def sync(_conn, app_id, **kwargs):
        visited.append(app_id)
        now[0] = dt.datetime(2026, 11, 26, 23, 59)
        return {'status': 'unchanged'}

    monkeypatch.setattr(catalog_service, 'sync_app', sync)
    assert catalog_service.sync_all(temp_db, user='system', signed_up_only=True)['checked'] == 1
    assert visited == ['a']
