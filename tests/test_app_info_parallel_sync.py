"""Concurrent source reads, main-thread transactions and stale-result protection."""

import datetime as dt
import json
import threading

import pytest

from app import timeutil
from app.integrations import gerrit
from app.repositories import app_info_repo, apps_repo
from app.services import catalog_service, notification_channels
from tests.conftest import (
    make_app_info,
    seed_app,
    seed_cycle,
    set_cycle_dates,
    store_app_info,
)


def test_parallel_network_reads_apply_sqlite_updates_on_calling_thread(temp_db, monkeypatch):
    release_id = seed_cycle(temp_db)
    for name in ["a", "b", "broken"]:
        seed_app(temp_db, name, app_info=None)
    rendezvous = threading.Barrier(2)
    main = threading.get_ident()
    readers, sql_threads, notices = [], [], []

    def head(url, branch, **kwargs):
        readers.append(threading.get_ident())
        if url.endswith("hpc_broken"):
            raise RuntimeError("upstream unavailable")
        rendezvous.wait(timeout=5)
        return "a" * 40

    monkeypatch.setattr(gerrit, "head_commit", head)
    monkeypatch.setattr(
        gerrit, "fetch_app_info_at", lambda *a, **kw: json.dumps(make_app_info(app_version="2.0"))
    )
    monkeypatch.setattr(notification_channels, "notify", lambda *args: notices.append(args[1]))
    temp_db.set_trace_callback(lambda sql: sql_threads.append(threading.get_ident()))
    try:
        result = catalog_service.sync_all(temp_db, release_id=release_id, user="rm")
    finally:
        temp_db.set_trace_callback(None)
    assert (result["checked"], result["updated"], result["failed"]) == (3, 2, 1)
    assert len(set(readers)) >= 2 and main not in readers
    assert set(sql_threads) == {main}
    assert app_info_repo.latest_version(temp_db, "a")["raw"]["app_version"] == "2.0"
    assert app_info_repo.latest_version(temp_db, "b")["raw"]["app_version"] == "2.0"
    assert apps_repo.get_app(temp_db, "broken")["sync_error"] == "upstream unavailable"
    assert notices == ["app_info_sync_failed"]


def test_shared_repository_is_read_once_per_batch_and_changes_are_seen_next_batch(
    temp_db, monkeypatch
):
    release_id = seed_cycle(temp_db)
    for name in ["left", "right"]:
        seed_app(temp_db, name, git_url="hpc_shared", app_info=None)
    state = {"head": "a" * 40, "version": "2.0"}
    calls = []

    def head(*args, **kwargs):
        calls.append("head")
        return state["head"]

    def content(*args, **kwargs):
        calls.append("file")
        return json.dumps(make_app_info(app_version=state["version"]))

    monkeypatch.setattr(gerrit, "head_commit", head)
    monkeypatch.setattr(gerrit, "fetch_app_info_at", content)
    assert catalog_service.sync_all(temp_db, release_id=release_id, user="rm")["updated"] == 2
    assert calls.count("head") == calls.count("file") == 1
    assert catalog_service.sync_all(temp_db, release_id=release_id, user="rm")["updated"] == 0
    assert calls.count("head") == 2 and calls.count("file") == 1
    state.update(head="b" * 40, version="3.0")
    assert catalog_service.sync_all(temp_db, release_id=release_id, user="rm")["updated"] == 2
    assert calls.count("head") == 3 and calls.count("file") == 2
    assert all(
        app_info_repo.latest_version(temp_db, app)["raw"]["app_version"] == "3.0"
        for app in ["left", "right"]
    )


@pytest.mark.parametrize(
    "change,message",
    [("branch", "仓库或分支已变化"), ("stopped", "停止运行"), ("new-head", "另一次同步")],
)
def test_stale_result_does_not_overwrite_new_configuration_or_sync_state(
    temp_db, monkeypatch, change, message
):
    app_id = seed_app(temp_db, app_info=None)
    monkeypatch.setattr(gerrit, "head_commit", lambda *a, **kw: "a" * 40)
    monkeypatch.setattr(
        gerrit, "fetch_app_info_at", lambda *a, **kw: json.dumps(make_app_info(app_version="2.0"))
    )
    prepared = catalog_service._read_sync_app(apps_repo.get_app(temp_db, app_id))
    if change == "branch":
        temp_db.execute("UPDATE apps SET git_branch='new-branch' WHERE id=?", (app_id,))
    elif change == "stopped":
        apps_repo.set_cicd_status(temp_db, app_id, "stopped")
    else:
        store_app_info(temp_db, app_id, make_app_info(app_version="3.0"), commit_id="b" * 40)
        apps_repo.update_sync_state(
            temp_db, app_id, checked_at="new-check", error="", head_commit="b" * 40
        )
    temp_db.commit()
    result = catalog_service.sync_app(temp_db, app_id, user="rm", _prepared=prepared)
    assert result["status"] == "error" and message in result["error"]
    current = apps_repo.get_app(temp_db, app_id)
    assert current["sync_error"] == ""
    latest = app_info_repo.latest_version(temp_db, app_id)
    if change == "new-head":
        assert latest["raw"]["app_version"] == "3.0"
        assert current["sync_head_commit"] == "b" * 40 and current["sync_checked_at"] == "new-check"
    else:
        assert latest is None


def test_bulk_sync_discards_inflight_results_and_stops_next_wave_at_signup_deadline(temp_db, monkeypatch):
    for name in ["a", "b", "c"]:
        seed_app(temp_db, name)
    cycle = seed_cycle(temp_db)
    set_cycle_dates(
        temp_db,
        cycle,
        signup_deadline="2026-11-13 02:00",
        qa_deadline="2026-11-26 23:59",
        release_date="2026-11-30",
    )
    now = [dt.datetime(2026, 11, 13, 1, 59, 59)]
    monkeypatch.setattr(timeutil, "beijing_now", lambda: now[0])
    monkeypatch.setattr(catalog_service, "APP_INFO_SYNC_WORKERS", 2)
    rendezvous = threading.Barrier(2)
    started = []

    def head(url, branch, **kwargs):
        started.append(url)
        rendezvous.wait(timeout=5)
        now[0] = dt.datetime(2026, 11, 13, 2, 0)
        return "a" * 40

    monkeypatch.setattr(gerrit, "head_commit", head)
    monkeypatch.setattr(gerrit, "fetch_app_info_at", lambda *a, **kw: json.dumps(make_app_info(app_version="2.0")))
    result = catalog_service.sync_all(temp_db, release_id=cycle, user="rm")
    assert result["checked"] == result["updated"] == 0 and len(started) == 2
    assert "报名截止" in result["error"]
    assert not any(url.endswith("hpc_c") for url in started)
    for name in ["a", "b", "c"]:
        assert apps_repo.get_app(temp_db, name)["sync_checked_at"] == ""
        assert app_info_repo.latest_version(temp_db, name)["raw"]["app_version"] == "1.0"
