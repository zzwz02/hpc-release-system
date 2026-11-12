"""Monthly cycles: dates, signups, test scope, QA scope requests, publication."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.api.errors import AuthzError
from app.deps import get_db, require_login
from app.domain import cycles as cycles_domain
from app.domain import phases
from app.main import create_app
from app.repositories import entries_repo, official_repo
from app.services import artifact_service, catalog_service, cycle_service, qa_service
from tests.conftest import (
    APP_INFO,
    close_qa,
    close_signup,
    make_app_info,
    seed_app,
    seed_cycle,
    seed_signup,
    store_app_info,
)

# ---------------------------------------------------------------------------
# Dates
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "year,month,expected",
    [
        (2026, 10, ("2026-10-01 23:59", "2026-10-26 23:59", "2026-10-31")),
        (2026, 11, ("2026-11-01 23:59", "2026-11-25 23:59", "2026-11-30")),
        (2026, 12, ("2026-12-01 23:59", "2026-12-26 23:59", "2026-12-31")),
        (2028, 2, ("2028-02-01 23:59", "2028-02-24 23:59", "2028-02-29")),
    ],
)
def test_default_dates_close_qa_five_days_before_the_month_ends(year, month, expected) -> None:
    defaults = cycles_domain.default_dates(year, month)
    assert (
        defaults["signup_deadline"],
        defaults["qa_deadline"],
        defaults["release_date"],
    ) == expected


def test_cycle_dates_must_be_complete_and_in_order() -> None:
    assert cycles_domain.normalize_cycle_dates(
        "2026-11-01 23:59", "2026-11-25 23:59", "2026-11-30"
    ) == {
        "signup_deadline": "2026-11-01 23:59",
        "qa_deadline": "2026-11-25 23:59",
        "release_date": "2026-11-30",
    }
    with pytest.raises(ValueError, match="都必须填写"):
        cycles_domain.normalize_cycle_dates("2026-11-01 23:59", "", "2026-11-30")
    with pytest.raises(ValueError, match="不能晚于 QA 截止"):
        cycles_domain.normalize_cycle_dates(
            "2026-11-26 23:59", "2026-11-25 23:59", "2026-11-30"
        )
    with pytest.raises(ValueError, match="不能晚于发布日"):
        cycles_domain.normalize_cycle_dates(
            "2026-11-01 23:59", "2026-11-25 23:59", "2026-11-20"
        )
    with pytest.raises(ValueError, match="发布日格式错误"):
        cycles_domain.normalize_cycle_dates("2026-11-01 23:59", "2026-11-25 23:59", "30/11")


def test_create_cycle_is_rm_only_names_are_unique_and_environment_is_inherited(temp_db) -> None:
    first = seed_cycle(temp_db, name="2099-01")
    cycle_service.save_environment(
        temp_db,
        first,
        [{"component": "MACA", "version": "3.0", "note": ""}],
        user="rm",
        role="RM",
    )

    with pytest.raises(AuthzError):
        seed_cycle(temp_db, name="2099-02", user="owner_test", role="Owner")
    with pytest.raises(ValueError, match="已存在"):
        seed_cycle(temp_db, name="2099-01")

    second = seed_cycle(
        temp_db,
        name="2099-02",
        signup_deadline="2099-02-01 23:59",
        qa_deadline="2099-02-23 23:59",
        release_date="2099-02-28",
    )
    detail = cycle_service.release_detail(temp_db, second)
    assert [row["component"] for row in detail["environment"]] == ["MACA"]
    assert detail["phase"] == phases.SIGNUP
    # A new cycle starts empty: nothing but the environment is inherited.
    assert cycle_service.list_entry_views(temp_db, second) == []


# ---------------------------------------------------------------------------
# Signup, withdraw, re-signup
# ---------------------------------------------------------------------------


def test_signup_locks_the_latest_app_info_and_validates_scope(cycle_with_app) -> None:
    conn, release_id, app_id = cycle_with_app
    image_id = cycle_service.save_base_image(
        conn, release_id, {"name": "base", "image": "hpc/base:1"}, user="rm", role="RM"
    )[0]["id"]

    entry = seed_signup(
        conn,
        release_id,
        app_id,
        base_image_id=image_id,
        target_version="1.0",
        excluded_tests=["sanity"],
    )

    assert entry["status"] == "active"
    assert entry["target_version"] == "1.0"
    assert entry["base_image_id"] == image_id
    assert entry["locked_app_info"]["app_version"] == "1.0"
    assert [t["excluded"] for t in entry["scope_tests"]] == [True]
    assert entry["behind_latest"] is False

    with pytest.raises(RuntimeError, match="已报名"):
        seed_signup(conn, release_id, app_id)


def test_signup_requires_a_target_version_app_info_and_a_known_base_image(temp_db) -> None:
    release_id = seed_cycle(temp_db)
    bare = seed_app(temp_db, "bare", app_info=None)
    with pytest.raises(RuntimeError, match="还没有 app_info"):
        seed_signup(temp_db, release_id, bare)

    app_id = seed_app(temp_db)
    with pytest.raises(ValueError, match="目标版本"):
        seed_signup(temp_db, release_id, app_id, target_version=" ")
    with pytest.raises(ValueError, match="基础镜像"):
        seed_signup(temp_db, release_id, app_id, base_image_id=999)
    with pytest.raises(ValueError, match="未知的测试项"):
        seed_signup(temp_db, release_id, app_id, excluded_tests=["nope"])


def test_withdraw_then_sign_up_again_reuses_the_same_entry_row(cycle_with_entry) -> None:
    conn, release_id, app_id = cycle_with_entry
    entry = entries_repo.find_entry(conn, release_id, app_id)
    qa_service.set_qa_status_batch(
        conn, release_id, [{"app_id": app_id, "status": "qa_passed"}], user="qa", role="QA"
    )

    withdrawn = cycle_service.withdraw(conn, entry["id"], user="rm", role="RM")
    assert withdrawn["status"] == "withdrawn"
    assert cycle_service.list_entry_views(conn, release_id, include_withdrawn=False) == []

    again = seed_signup(conn, release_id, app_id, target_version="2.0")
    assert again["id"] == entry["id"]
    assert again["status"] == "active"
    assert again["target_version"] == "2.0"
    # The previous round's QA verdict does not carry over.
    assert again["qa_status"] == "not_checked"
    assert again["result"] == ""
    assert conn.execute("SELECT COUNT(*) FROM entries").fetchone()[0] == 1


def test_signup_closes_with_the_deadline_but_scope_can_still_shrink(cycle_with_entry) -> None:
    conn, release_id, app_id = cycle_with_entry
    entry = entries_repo.find_entry(conn, release_id, app_id)
    close_signup(conn, release_id)
    assert cycle_service.get_release(conn, release_id)["phase"] == phases.IN_PROGRESS

    with pytest.raises(RuntimeError, match="报名已截止"):
        seed_signup(conn, release_id, seed_app(conn, "late"))
    with pytest.raises(RuntimeError, match="报名已截止"):
        cycle_service.update_entry(
            conn, entry["id"], {"target_version": "9.9"}, user="rm", role="RM"
        )

    shrunk = cycle_service.update_entry(
        conn, entry["id"], {"excluded_tests": ["sanity"]}, user="rm", role="RM"
    )
    assert shrunk["excluded_tests"] == ["sanity"]
    with pytest.raises(RuntimeError, match="只能删减"):
        cycle_service.update_entry(conn, entry["id"], {"excluded_tests": []}, user="rm", role="RM")

    delivered = cycle_service.update_entry(
        conn, entry["id"], {"actual_delivery": "交付了 1.0", "known_issues": "无"},
        user="rm", role="RM",
    )
    assert delivered["actual_delivery"] == "交付了 1.0"


# ---------------------------------------------------------------------------
# Scope following and scope requests
# ---------------------------------------------------------------------------


def test_entries_follow_every_new_app_info_before_the_signup_deadline(cycle_with_entry) -> None:
    conn, release_id, app_id = cycle_with_entry
    entry = entries_repo.find_entry(conn, release_id, app_id)

    version_id = store_app_info(
        conn, app_id, make_app_info(app_version="2.0", chips=["c500", "n300"])
    )

    updated = cycle_service.entry_view(conn, entries_repo.get_entry(conn, entry["id"]))
    assert updated["locked_version_id"] == version_id
    assert updated["locked_app_info"]["app_version"] == "2.0"
    assert updated["pending_scope_request"] is None
    assert updated["behind_latest"] is False


def test_a_narrower_app_info_is_followed_after_the_deadline_without_asking_qa(
    cycle_with_entry,
) -> None:
    conn, release_id, app_id = cycle_with_entry
    entry = entries_repo.find_entry(conn, release_id, app_id)
    close_signup(conn, release_id)

    version_id = store_app_info(
        conn, app_id, make_app_info(app_version="1.1", tests={"sanity": ["c500"]})
    )

    updated = entries_repo.get_entry(conn, entry["id"])
    assert updated["locked_version_id"] == version_id
    assert entries_repo.pending_scope_request(conn, entry["id"]) is None


def test_widening_after_the_deadline_asks_qa_and_only_approval_moves_the_entry(
    cycle_with_entry,
) -> None:
    conn, release_id, app_id = cycle_with_entry
    entry = entries_repo.find_entry(conn, release_id, app_id)
    close_signup(conn, release_id)

    wider = store_app_info(
        conn, app_id, make_app_info(tests={"sanity": ["c500"], "perf": ["c500"]})
    )

    pending = entries_repo.pending_scope_request(conn, entry["id"])
    assert pending["additions"] == ["新增测试 perf"]
    assert pending["target_version_id"] == wider
    still = entries_repo.get_entry(conn, entry["id"])
    assert still["locked_version_id"] == entry["locked_version_id"]
    assert cycle_service.entry_view(conn, still)["behind_latest"]

    listed = cycle_service.list_scope_requests(conn, release_id)
    assert [row["id"] for row in listed] == [pending["id"]]
    assert listed[0]["app_name"] == "TestApp"

    with pytest.raises(AuthzError):
        cycle_service.decide_scope_request(
            conn, pending["id"], approve=True, note="", user="rm", role="RM"
        )
    decided = cycle_service.decide_scope_request(
        conn, pending["id"], approve=True, note="同意", user="qa", role="QA"
    )
    assert decided["status"] == "approved"
    assert entries_repo.get_entry(conn, entry["id"])["locked_version_id"] == wider
    with pytest.raises(RuntimeError, match="已处理"):
        cycle_service.decide_scope_request(
            conn, pending["id"], approve=True, note="", user="qa", role="QA"
        )


def test_a_rejected_widening_is_not_asked_again_and_needs_a_reason(cycle_with_entry) -> None:
    conn, release_id, app_id = cycle_with_entry
    entry = entries_repo.find_entry(conn, release_id, app_id)
    close_signup(conn, release_id)
    wider = make_app_info(tests={"sanity": ["c500"], "perf": ["c500"]})
    store_app_info(conn, app_id, wider, commit_id="c2")
    request_id = entries_repo.pending_scope_request(conn, entry["id"])["id"]

    with pytest.raises(ValueError, match="理由"):
        cycle_service.decide_scope_request(
            conn, request_id, approve=False, note="  ", user="qa", role="QA"
        )
    cycle_service.decide_scope_request(
        conn, request_id, approve=False, note="本月不测新用例", user="qa", role="QA"
    )
    assert entries_repo.get_entry(conn, entry["id"])["locked_version_id"] != wider

    # The same widening in a later app_info does not reopen the question.
    store_app_info(conn, app_id, {**wider, "app_version": "1.2"}, commit_id="c3")
    assert entries_repo.pending_scope_request(conn, entry["id"]) is None


def test_a_newer_app_info_supersedes_a_pending_request_and_a_narrow_one_closes_it(
    cycle_with_entry,
) -> None:
    conn, release_id, app_id = cycle_with_entry
    entry = entries_repo.find_entry(conn, release_id, app_id)
    close_signup(conn, release_id)

    wider = make_app_info(tests={"sanity": ["c500"], "perf": ["c500"]})
    store_app_info(conn, app_id, wider, commit_id="c2")
    first = entries_repo.pending_scope_request(conn, entry["id"])
    store_app_info(
        conn,
        app_id,
        make_app_info(tests={"sanity": ["c500"], "perf": ["c500"], "regression": ["c500"]}),
        commit_id="c3",
    )
    second = entries_repo.pending_scope_request(conn, entry["id"])
    assert second["id"] != first["id"]
    assert second["additions"] == ["新增测试 perf, regression"]
    assert entries_repo.get_scope_request(conn, first["id"])["status"] == "superseded"

    # Back to the original scope: the entry follows and the request auto-closes.
    store_app_info(conn, app_id, make_app_info(app_version="1.3"), commit_id="c4")
    assert entries_repo.pending_scope_request(conn, entry["id"]) is None
    assert entries_repo.get_scope_request(conn, second["id"])["status"] == "closed"


def test_withdrawing_closes_a_pending_scope_request(cycle_with_entry) -> None:
    conn, release_id, app_id = cycle_with_entry
    entry = entries_repo.find_entry(conn, release_id, app_id)
    close_signup(conn, release_id)
    wider = make_app_info(tests={"sanity": ["c500"], "perf": ["c500"]})
    store_app_info(conn, app_id, wider, commit_id="c2")
    request_id = entries_repo.pending_scope_request(conn, entry["id"])["id"]

    cycle_service.withdraw(conn, entry["id"], user="rm", role="RM")

    assert entries_repo.get_scope_request(conn, request_id)["status"] == "closed"


# ---------------------------------------------------------------------------
# Publication and unlock
# ---------------------------------------------------------------------------


def _release_cycle(conn, verdicts: dict[str, tuple[str, str]]) -> str:
    release_id = seed_cycle(conn)
    for app_id in verdicts:
        seed_signup(conn, release_id, app_id, target_version=f"{app_id}-1.0")
    qa_service.set_qa_status_batch(
        conn,
        release_id,
        [
            {"app_id": app_id, "status": status, "issue_note": note,
             "deliverable_image": f"hpc/{app_id}:1", "deliverable_digest": f"sha256:{app_id}"}
            for app_id, (status, note) in verdicts.items()
        ],
        user="qa",
        role="QA",
    )
    close_qa(conn, release_id)
    return release_id


def test_publish_records_results_and_official_versions(temp_db) -> None:
    conn = temp_db
    for app_id in ("passed", "issues", "blocked", "untested"):
        seed_app(conn, app_id, official_name=app_id.title())
    release_id = _release_cycle(
        conn,
        {
            "passed": ("qa_passed", ""),
            "issues": ("has_issues", "偶发超时"),
            "blocked": ("cannot_release", "崩溃"),
            "untested": ("not_checked", ""),
        },
    )

    with pytest.raises(AuthzError):
        cycle_service.publish(conn, release_id, user="owner_test", role="Owner")

    result = cycle_service.publish(conn, release_id, user="rm", role="RM")

    assert sorted(result["released"]) == ["issues", "passed"]
    assert sorted(result["failed"]) == ["blocked", "untested"]
    assert cycle_service.get_release(conn, release_id)["phase"] == phases.RELEASED

    officials = official_repo.current_by_app(conn)
    assert set(officials) == {"passed", "issues"}
    assert officials["issues"]["version"] == "issues-1.0"
    assert officials["issues"]["digest"] == "sha256:issues"
    # The QA note of a has_issues App becomes a known issue of the release.
    entries = {e["app_id"]: e for e in entries_repo.list_for_release(conn, release_id)}
    assert entries["issues"]["known_issues"] == "QA 备注：偶发超时"
    assert entries["blocked"]["known_issues"] == ""
    assert "release_note" in result["artifacts"]

    with pytest.raises(RuntimeError, match="已发布"):
        qa_service.set_qa_status_batch(
            conn, release_id, [{"app_id": "passed", "status": "not_checked"}], user="qa", role="QA"
        )


def test_a_failed_app_keeps_its_previous_official_version(temp_db) -> None:
    conn = temp_db
    seed_app(conn, "app1")
    first = _release_cycle(conn, {"app1": ("qa_passed", "")})
    cycle_service.publish(conn, first, user="rm", role="RM")
    assert official_repo.current_by_app(conn)["app1"]["release_id"] == first

    second = seed_cycle(
        conn,
        name="2099-02",
        signup_deadline="2099-02-01 23:59",
        qa_deadline="2099-02-23 23:59",
        release_date="2099-02-28",
    )
    seed_signup(conn, second, "app1", target_version="2.0")
    qa_service.set_qa_status_batch(
        conn, second, [{"app_id": "app1", "status": "cannot_release", "issue_note": "崩溃"}],
        user="qa", role="QA",
    )
    close_qa(conn, second)
    cycle_service.publish(conn, second, user="rm", role="RM")

    current = official_repo.current_by_app(conn)["app1"]
    assert current["release_id"] == first
    assert current["version"] == "app1-1.0"


def test_unlock_withdraws_the_results_and_lets_rm_publish_again(temp_db) -> None:
    conn = temp_db
    seed_app(conn, "app1")
    release_id = _release_cycle(conn, {"app1": ("qa_passed", "")})
    cycle_service.publish(conn, release_id, user="rm", role="RM")

    with pytest.raises(ValueError, match="原因"):
        cycle_service.unlock(conn, release_id, reason="", user="rm", role="RM")
    cycle_service.unlock(conn, release_id, reason="版本号写错了", user="rm", role="RM")

    assert cycle_service.get_release(conn, release_id)["phase"] == phases.QA_CLOSED
    assert official_repo.current_by_app(conn) == {}
    assert entries_repo.list_for_release(conn, release_id)[0]["result"] == ""
    # The final documents are withdrawn with the results.
    with pytest.raises(KeyError):
        artifact_service.get_artifact(conn, release_id, "release_note", role="RM")

    cycle_service.publish(conn, release_id, user="rm", role="RM")
    assert official_repo.current_by_app(conn)["app1"]["release_id"] == release_id


def test_a_published_cycle_is_read_only(temp_db) -> None:
    conn = temp_db
    seed_app(conn, "app1")
    release_id = _release_cycle(conn, {"app1": ("qa_passed", "")})
    cycle_service.publish(conn, release_id, user="rm", role="RM")
    entry_id = entries_repo.list_for_release(conn, release_id)[0]["id"]

    with pytest.raises(RuntimeError, match="已发布"):
        cycle_service.update_entry(
            conn, entry_id, {"actual_delivery": "x"}, user="rm", role="RM"
        )
    with pytest.raises(RuntimeError, match="只读"):
        cycle_service.update_cycle(
            conn, release_id, name="2099-01", signup_deadline="2099-01-01 23:59",
            qa_deadline="2099-01-26 23:59", release_date="2099-01-31", user="rm", role="RM",
        )
    with pytest.raises(RuntimeError, match="QA 截止后"):
        cycle_service.publish(conn, release_id, user="rm", role="RM")


# ---------------------------------------------------------------------------
# HTTP layer
# ---------------------------------------------------------------------------


@pytest.fixture()
def api(temp_db):
    """(client, set_user) against temp_db; RM by default."""
    app = create_app()
    current = {"user": {"username": "rm", "role": "RM", "display_name": "RM"}}

    def override_db():
        yield temp_db

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[require_login] = lambda: current["user"]
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client, lambda user: current.update(user=user)


def test_http_cycle_signup_and_publish_round_trip(temp_db, api) -> None:
    client, set_user = api
    app_id = seed_app(temp_db)

    defaults = client.get("/api/releases/defaults", params={"month": "2099-11"}).json()
    assert defaults["qa_deadline"] == "2099-11-25 23:59"

    created = client.post("/api/releases/create", json={"name": "2099-11", **defaults})
    assert created.status_code == 200
    release_id = created.json()["release_id"]

    signed = client.post(
        "/api/entries/signup",
        json={"release_id": release_id, "app_id": app_id, "target_version": "1.0"},
    )
    assert signed.status_code == 200
    entry = signed.json()["entry"]
    assert entry["app_id"] == app_id

    state = client.get("/api/state", params={"release_id": release_id}).json()
    assert state["release"]["phase"] == "signup"
    assert [e["app_id"] for e in state["release"]["entries"]] == [app_id]
    assert state["apps"][0]["open_signups"] == ["2099-11"]

    set_user({"username": "qa", "role": "QA", "display_name": "QA"})
    graded = client.post(
        "/api/qa/status-batch",
        json={
            "release_id": release_id,
            "items": [{"app_id": app_id, "status": "qa_passed", "issue_note": ""}],
        },
    )
    assert graded.status_code == 200

    set_user({"username": "rm", "role": "RM", "display_name": "RM"})
    close_qa(temp_db, release_id)
    published = client.post("/api/releases/publish", json={"release_id": release_id})
    assert published.status_code == 200
    assert published.json()["released"] == [app_id]

    unlocked = client.post(
        "/api/releases/unlock", json={"release_id": release_id, "reason": "改交付内容"}
    )
    assert unlocked.status_code == 200


def test_http_signup_and_publish_are_refused_for_the_wrong_role(temp_db, api) -> None:
    client, set_user = api
    app_id = seed_app(temp_db, owners=["owner_test"])
    release_id = seed_cycle(temp_db)

    set_user({"username": "qa", "role": "QA", "display_name": "QA"})
    denied = client.post(
        "/api/entries/signup",
        json={"release_id": release_id, "app_id": app_id, "target_version": "1.0"},
    )
    assert denied.status_code == 403

    set_user({"username": "owner_test", "role": "Owner", "display_name": "Owner"})
    assert client.post("/api/releases/publish", json={"release_id": release_id}).status_code == 403
    assert (
        client.post(
            "/api/entries/signup",
            json={"release_id": release_id, "app_id": app_id, "target_version": "1.0"},
        ).status_code
        == 200
    )


def test_http_scope_request_is_decided_by_qa(temp_db, api) -> None:
    client, set_user = api
    app_id = seed_app(temp_db)
    release_id = seed_cycle(temp_db)
    seed_signup(temp_db, release_id, app_id)
    close_signup(temp_db, release_id)
    store_app_info(
        temp_db, app_id, make_app_info(tests={"sanity": ["c500"], "perf": ["c500"]}), commit_id="c2"
    )
    request_id = client.get("/api/state", params={"release_id": release_id}).json()[
        "release"
    ]["scope_requests"][0]["id"]

    denied = client.post(
        "/api/scope-requests/decide", json={"request_id": request_id, "approve": True}
    )
    assert denied.status_code == 403

    set_user({"username": "qa", "role": "QA", "display_name": "QA"})
    decided = client.post(
        "/api/scope-requests/decide",
        json={"request_id": request_id, "approve": True, "note": "ok"},
    )
    assert decided.status_code == 200
    assert decided.json()["request"]["status"] == "approved"


def test_entry_test_commands_follow_the_locked_scope(cycle_with_entry) -> None:
    conn, release_id, app_id = cycle_with_entry
    entry = entries_repo.find_entry(conn, release_id, app_id)

    commands = qa_service.entry_test_commands(conn, entry)
    assert [row["test"] for row in commands] == ["sanity"]
    assert commands[0]["command"].startswith("docker run")

    cycle_service.update_entry(
        conn, entry["id"], {"excluded_tests": ["sanity"]}, user="rm", role="RM"
    )
    assert qa_service.entry_test_commands(conn, entries_repo.get_entry(conn, entry["id"])) == []


def test_storing_the_same_app_info_twice_adds_no_version(cycle_with_app) -> None:
    conn, _release_id, app_id = cycle_with_app
    assert store_app_info(conn, app_id, APP_INFO, commit_id="same") is None
    assert len(catalog_service.app_detail(conn, app_id)["app_info_versions"]) == 1
