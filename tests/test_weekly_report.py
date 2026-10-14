from __future__ import annotations

import datetime as dt
import io
import json
import time
import urllib.error
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook

from app.config import settings
from app.db.connection import connect
from app.deps import require_login
from app.domain import weekly_report as wr
from app.domain.permissions import ALL_ROLES, roles_for_capability, roles_for_tab
from app.integrations import jira
from app.main import create_app
from app.repositories import users_repo, weekly_report_repo
from app.services import weekly_report_jobs, weekly_report_service

TZ = dt.timezone(dt.timedelta(hours=8))
NOW = dt.datetime(2026, 10, 10, 18, 0, tzinfo=TZ)
REPORT_DATE = dt.date(2026, 10, 10)
SINCE = NOW - dt.timedelta(hours=192)


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------

def workbook_bytes(sheets: dict[str, list[tuple[object, object]]]) -> bytes:
    """An .xlsx with a header row and the given (date, text) rows per sheet."""
    book = Workbook()
    book.remove(book.active)
    for name, rows in sheets.items():
        sheet = book.create_sheet(name)
        sheet.append(["日期", "周报"])
        for row in rows:
            sheet.append(list(row))
    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()


def activity(
    key: str,
    *,
    assignee: str = "alice",
    events: list[tuple[str, dt.datetime]] | None = None,
    comments: list[tuple[str, dt.datetime, str]] | None = None,
    status: str = "In Progress",
) -> dict:
    comments = comments or []
    events = (events or []) + [(author, moment) for author, moment, _body in comments]
    return {
        "key": key,
        "summary": f"{key} summary",
        "status": status,
        "assignee": {"name": assignee, "display_name": assignee.title()} if assignee else None,
        "events": [{"user": user, "display": user.title(), "time": moment} for user, moment in events],
        "changes": [],
        "comments": [
            {"author": author, "update_author": author, "time": moment, "body": body}
            for author, moment, body in comments
        ],
        "worklogs": [],
    }


class FakeLlm:
    """Answers the split prompt per report text and the consistency prompt with one verdict."""

    def __init__(self, items_by_text: dict[str, list[dict]], verdict: dict | None = None) -> None:
        self.items_by_text = items_by_text
        self.verdict = verdict or {"verdict": "一致", "reason": ""}
        self.split_calls: list[str] = []
        self.consistency_calls: list[str] = []

    def __call__(self, _system: str, prompt: str) -> str:
        if prompt.startswith("判断一位工程师"):
            self.consistency_calls.append(prompt)
            return json.dumps(self.verdict, ensure_ascii=False)
        for text, items in self.items_by_text.items():
            if f"<<<\n{text}\n>>>" in prompt:
                self.split_calls.append(text)
                return "好的：\n" + json.dumps({"items": items}, ensure_ascii=False)
        raise AssertionError(f"unexpected prompt: {prompt[-200:]}")


def fake_fetch(issues: dict[str, dict | int]):
    calls: list[str] = []

    def fetch(key: str, _since: dt.datetime) -> dict:
        calls.append(key)
        value = issues[key]
        if isinstance(value, int):
            raise urllib.error.HTTPError(f"http://jira/{key}", value, "error", None, None)  # type: ignore[arg-type]
        return value

    fetch.calls = calls  # type: ignore[attr-defined]
    return fetch


# ---------------------------------------------------------------------------
# Domain: parsing and date rules
# ---------------------------------------------------------------------------

def test_to_date_accepts_the_script_formats_only() -> None:
    assert wr.to_date(dt.datetime(2026, 10, 10, 9, 0)) == REPORT_DATE
    assert wr.to_date("2026-10-10") == REPORT_DATE
    assert wr.to_date(" 2026/10/10 ") == REPORT_DATE
    assert wr.to_date("10/10/2026") == REPORT_DATE
    assert wr.to_date("10-10-26") == REPORT_DATE
    assert wr.to_date("2026-10-10 补") is None
    assert wr.to_date(20261010) is None


def test_read_reports_takes_the_top_dated_row_and_cuts_the_plan() -> None:
    content = workbook_bytes({
        "Alice": [
            (dt.datetime(2026, 10, 10), "本周更新:\n1. PHS-1 调优\n下周计划:\n继续"),
            (None, "下周计划：不应读到"),
            (dt.datetime(2026, 10, 17), "更晚的日期写在了下面"),
            ("2026-09-26", "旧的一周"),
        ],
        "Template": [("说明", "没有日期")],
    })

    reports, undated = wr.read_reports(content)

    assert undated == ["Template"]
    assert reports == [wr.SheetReport(
        sheet="Alice", date=REPORT_DATE, newest=dt.date(2026, 10, 17), text="本周更新:\n1. PHS-1 调优",
    )]
    assert wr.cut_plan("做了 A\n下周: 做 B") == "做了 A\n"


def test_read_reports_rejects_a_non_workbook() -> None:
    with pytest.raises(ValueError, match="不是有效的 .xlsx"):
        wr.read_reports(b"not an xlsx")


def test_default_report_date_is_the_most_common_sane_top_date() -> None:
    def report(day: dt.date) -> wr.SheetReport:
        return wr.SheetReport(sheet=str(day), date=day, newest=day, text="x")

    friday, thursday = dt.date(2026, 10, 9), dt.date(2026, 10, 8)
    assert wr.default_report_date([report(friday), report(friday), report(REPORT_DATE)]) == friday
    # A tie goes to the later date; a typo year never counts.
    assert wr.default_report_date([report(thursday), report(friday), report(dt.date(2010, 1, 1))]) == friday
    assert wr.default_report_date([report(dt.date(2010, 1, 1))]) is None


def test_week_of_uses_the_iso_week() -> None:
    assert wr.week_of(REPORT_DATE) == {
        "week_key": "2026-W41",
        "week_start": "2026-10-05",
        "week_end": "2026-10-11",
        "label": "2026 W41（10月5日-10月11日）",
    }
    assert wr.week_of(dt.date(2027, 1, 1))["week_key"] == "2026-W53"


@pytest.mark.parametrize(
    ("top", "newest", "text", "expected"),
    [
        (REPORT_DATE, REPORT_DATE, "x", []),
        # Three days early is still this week; four is not.
        (dt.date(2026, 10, 7), dt.date(2026, 10, 7), "x", []),
        (dt.date(2026, 10, 6), dt.date(2026, 10, 6), "x", [(wr.T_NO_REPORT, "最上面一条日期为 2026-10-06")]),
        (dt.date(2010, 10, 10), dt.date(2010, 10, 10), "x", [(wr.T_BAD_DATE, "最上面一条日期为 2010-10-10，日期明显错误")]),
        (dt.date(2026, 10, 18), dt.date(2026, 10, 18), "x", [(wr.T_BAD_DATE, "最上面一条日期为 2026-10-18，日期明显错误")]),
        (dt.date(2026, 10, 17), dt.date(2026, 10, 17), "x", []),
        (
            dt.date(2026, 10, 3), REPORT_DATE, "x",
            [(wr.T_BAD_DATE, "最上面一条日期为 2026-10-03，不是最新（表中最新为 2026-10-10）")],
        ),
        (REPORT_DATE, REPORT_DATE, "", [(wr.T_NO_REPORT, "本周更新为空")]),
    ],
)
def test_date_problems(top: dt.date, newest: dt.date, text: str, expected: list[tuple[str, str]]) -> None:
    report = wr.SheetReport(sheet="Alice", date=top, newest=newest, text=text)
    problems = wr.date_problems(report, report_date=REPORT_DATE, today=NOW.date())
    assert [(found["problem"], found["item"]) for found in problems] == expected


# ---------------------------------------------------------------------------
# Domain: work items against JIRA
# ---------------------------------------------------------------------------

def decide(item: dict, issues: dict, username: str = "alice") -> tuple[list[tuple[str, str]], list[str]]:
    problems, updated = wr.item_problems(item, username=username, issues=issues, since=SINCE)
    return [(found["problem"], found["jira_key"]) for found in problems], [entry["key"] for entry in updated]


def test_item_without_any_id_is_missing_jira() -> None:
    assert decide({"title": "调研", "jira_ids": [], "incomplete_ids": []}, {}) == ([(wr.T_NO_JIRA, "")], [])


def test_incomplete_and_malformed_ids_are_reported() -> None:
    item = {"title": "修 bug", "jira_ids": ["phs11452"], "incomplete_ids": ["11372"]}
    assert decide(item, {}) == ([(wr.T_BAD_ID, "11372"), (wr.T_BAD_ID, "PHS11452")], [])
    # Without a JIRA username only the id problems can be decided.
    assert decide({"title": "修 bug", "jira_ids": ["PHS-1"], "incomplete_ids": ["7"]}, {}, username="") == (
        [(wr.T_BAD_ID, "7")], [],
    )


def test_missing_and_forbidden_issues_are_told_apart() -> None:
    item = {"title": "修 bug", "jira_ids": ["PHS-1", "SPD-2"]}
    issues = {"PHS-1": wr.T_NOT_FOUND, "SPD-2": wr.T_FORBIDDEN}
    assert decide(item, issues) == ([(wr.T_NOT_FOUND, "PHS-1"), (wr.T_FORBIDDEN, "SPD-2")], [])


def test_only_own_issues_are_checked_and_closed_ones_still_count() -> None:
    old = SINCE - dt.timedelta(days=3)
    issues = {
        "PHS-1": activity("PHS-1", assignee="bob", events=[("bob", old)]),
        "PHS-2": activity("PHS-2", status="Closed", events=[("bob", NOW), ("alice", old)]),
    }

    problems, updated = wr.item_problems(
        {"title": "修 bug", "jira_ids": ["PHS-1", "PHS-2"]}, username="alice", issues=issues, since=SINCE,
    )

    assert updated == []
    assert problems == [wr.problem(
        wr.T_NOT_UPDATED, "修 bug", "PHS-2",
        jira_status="Closed", assignee="Alice", last_updated="2026-10-10 18:00", last_updater="Bob",
    )]


def test_one_updated_own_issue_clears_the_others_of_the_item() -> None:
    old = SINCE - dt.timedelta(days=3)
    issues = {
        "PHS-1": activity("PHS-1", events=[("alice", old)]),
        "PHS-2": activity("PHS-2", events=[("alice", SINCE)]),
    }
    assert decide({"title": "修 bug", "jira_ids": ["PHS-1", "PHS-2"]}, issues) == ([], ["PHS-2"])


def test_jira_update_text_lists_only_own_updates_in_the_window() -> None:
    issue = activity("PHS-1", comments=[
        ("alice", NOW, " 已定位到内核参数 "),
        ("bob", NOW, "别人的评论"),
        ("alice", SINCE - dt.timedelta(hours=1), "窗口外"),
    ])
    issue["changes"] = [{
        "user": "alice", "time": NOW,
        "items": [{"field": "status", "from": "Open", "to": "In Progress"}],
    }]
    issue["worklogs"] = [{"author": "alice", "time": NOW, "time_spent": "2h", "comment": "复现"}]

    assert wr.jira_update_text(issue, "alice", SINCE) == (
        "[PHS-1] PHS-1 summary\n"
        "10-10 修改 status：Open → In Progress\n"
        "10-10 comment：已定位到内核参数\n"
        "10-10 worklog 2h：复现"
    )


# ---------------------------------------------------------------------------
# JIRA integration
# ---------------------------------------------------------------------------

def test_get_issue_activity_is_read_only_and_normalises_events(monkeypatch: pytest.MonkeyPatch) -> None:
    requests: list[tuple[str, str]] = []
    raw = {
        "key": "PHS-1",
        "fields": {
            "summary": "调优",
            "status": {"name": "In Progress"},
            "assignee": {"name": "alice", "displayName": "Alice"},
            "reporter": {"name": "bob", "displayName": "Bob"},
            "created": "2026-09-01T10:00:00.000+0800",
            "comment": {"comments": [
                {
                    "author": {"name": "alice", "displayName": "Alice"},
                    "updateAuthor": {"name": "carol", "displayName": "Carol"},
                    "created": "2026-09-02T10:00:00.000+0800",
                    "updated": "2026-10-09T11:00:00.000+0800",
                    "body": "进展",
                },
            ]},
            "worklog": {"total": 2, "worklogs": [{"author": {"name": "alice"}, "created": "2026-09-03T10:00:00.000+0800"}]},
        },
        "changelog": {"histories": [
            {
                "author": {"name": "alice", "displayName": "Alice"},
                "created": "2026-10-08T09:30:00.000+0800",
                "items": [{"field": "status", "fromString": "Open", "toString": "In Progress"}],
            },
            {"author": {"name": "alice"}, "created": "2026-09-05T09:30:00.000+0800", "items": []},
        ]},
    }
    all_worklogs = {"worklogs": [
        {"author": {"name": "alice"}, "created": "2026-09-03T10:00:00.000+0800"},
        {"author": {"name": "alice"}, "created": "2026-10-09T10:00:00.000+0800", "timeSpent": "1h", "comment": "测"},
    ]}

    def request(_base: str, _token: str, method: str, path: str, body: dict | None = None, **_kwargs) -> dict:
        requests.append((method, path))
        assert body is None
        return all_worklogs if path.endswith("/worklog") else raw

    monkeypatch.setattr(jira, "load_config", lambda: {"JIRA_BASE_URL": "http://jira", "JIRA_TOKEN": "t"})
    monkeypatch.setattr(jira, "_request", request)

    result = jira.get_issue_activity("PHS-1", SINCE)

    assert [method for method, _path in requests] == ["GET", "GET"]
    assert "expand=changelog" in requests[0][1]
    assert result["status"] == "In Progress"
    assert result["assignee"] == {"name": "alice", "display_name": "Alice"}
    # Creation, both changes, the comment and its edit, both worklogs.
    assert sorted(event["user"] for event in result["events"]) == [
        "alice", "alice", "alice", "alice", "alice", "bob", "carol",
    ]
    assert [change["items"] for change in result["changes"]] == [
        [{"field": "status", "from": "Open", "to": "In Progress"}],
    ]
    assert [(comment["author"], comment["update_author"], comment["body"]) for comment in result["comments"]] == [
        ("alice", "carol", "进展"),
    ]
    assert [(worklog["time_spent"], worklog["comment"]) for worklog in result["worklogs"]] == [("1h", "测")]


# ---------------------------------------------------------------------------
# Service: one check
# ---------------------------------------------------------------------------

@pytest.fixture()
def conn(tmp_path: Path):
    connection = connect(tmp_path / "weekly.db")
    yield connection
    connection.close()


def set_members(conn, members: list[dict], *, project_row: bool = True) -> None:
    """Store a manual table; the workbooks here carry a "Project" sheet, marked 不检查."""
    if project_row and not any(member["sheet"].lower() == "project" for member in members):
        members = [*members, {"sheet": "Project", "skip": True}]
    weekly_report_repo.replace_members(conn, [
        {"jira_username": "", "skip": False, "note": "", **member} for member in members
    ])
    conn.commit()


ALICE_TEXT = "1. PHS-1 调优\n2. 调研新方案\n3. JiraID 11372 修复"
BOB_TEXT = "1. MC3-9 适配"
SHEETS = {
    "Project": [(dt.datetime(2026, 10, 10), "不是人")],
    "bob": [(dt.datetime(2026, 10, 10), BOB_TEXT)],
    "Alice": [(dt.datetime(2026, 10, 10), ALICE_TEXT + "\n下周计划：继续")],
    "Boss": [(dt.datetime(2026, 10, 10), "1. 管理")],
}
ITEMS = {
    ALICE_TEXT: [
        {"title": "调优", "summary": "完成调优", "jira_ids": ["PHS-1"], "incomplete_ids": []},
        {"title": "调研新方案", "summary": "", "jira_ids": [], "incomplete_ids": []},
        {"title": "修复", "summary": "", "jira_ids": [], "incomplete_ids": ["11372"]},
    ],
    BOB_TEXT: [{"title": "适配", "summary": "", "jira_ids": ["MC3-9"], "incomplete_ids": []}],
}


def run(conn, llm: FakeLlm, issues: dict, **options) -> dict:
    reports, _undated = wr.read_reports(workbook_bytes(SHEETS))
    return weekly_report_service.run_check(
        conn, reports, now=NOW, llm_call=llm, fetch_activity=fake_fetch(issues), **options,
    )


def test_run_check_reports_each_person_in_name_order(conn) -> None:
    set_members(conn, [
        {"sheet": "Alice", "jira_username": "alice"},
        {"sheet": "Boss", "skip": True},
    ])
    llm = FakeLlm(ITEMS, verdict={"verdict": "部分一致", "reason": "只有状态变化"})
    issues = {"PHS-1": activity("PHS-1", comments=[("alice", NOW, "调优完成")])}

    result = run(conn, llm, issues)

    assert result["week"]["week_key"] == "2026-W41"
    assert result["report_date"] == "2026-10-10"
    assert [(row["person"], row["problem"], row["item"], row["jira_key"], row["note"]) for row in result["findings"]] == [
        ("Alice", wr.T_PARTIAL, "调优", "PHS-1", "只有状态变化"),
        ("Alice", wr.T_NO_JIRA, "调研新方案", "", ""),
        ("Alice", wr.T_BAD_ID, "修复", "11372", ""),
    ]
    assert {row["sheet_date"] for row in result["findings"]} == {"2026-10-10"}
    summary = result["summary"]
    assert summary["people"] == [
        {
            "sheet": "Alice", "date": "2026-10-10", "item_count": 3, "note": "",
            "username": "alice", "display_name": "",
        },
        {
            "sheet": "bob", "date": "2026-10-10", "item_count": 1, "note": "找不到对应的用户，只检查是否写了 JIRA ID",
            "username": "", "display_name": "",
        },
    ]
    assert summary["not_checked"] == ["Boss", "Project"]
    assert summary["unmapped_sheets"] == ["bob"]
    assert summary["window_start"] == "2026-10-02 18:00"
    assert summary["by_problem"] == [
        {"problem": wr.T_NO_JIRA, "count": 1},
        {"problem": wr.T_BAD_ID, "count": 1},
        {"problem": wr.T_PARTIAL, "count": 1},
    ]
    # The consistency prompt carries the title, the summary and the person's JIRA text.
    assert "周报工作项：调优\n周报内容：完成调优" in llm.consistency_calls[0]
    assert "10-10 comment：调优完成" in llm.consistency_calls[0]


def test_run_check_reuses_the_cached_split_and_honours_options(conn) -> None:
    set_members(conn, [
        {"sheet": "Alice", "jira_username": "alice"},
        {"sheet": "bob", "jira_username": "bob"},
        {"sheet": "Boss", "skip": True},
    ])
    issues = {
        "PHS-1": activity("PHS-1", events=[("alice", NOW - dt.timedelta(hours=30))]),
        "MC3-9": 404,
    }
    first = FakeLlm(ITEMS)
    run(conn, first, issues, consistency=False)
    assert sorted(first.split_calls) == sorted([ALICE_TEXT, BOB_TEXT])

    second = FakeLlm({})
    result = run(conn, second, issues, consistency=False, active_hours=24)

    assert second.split_calls == [] and second.consistency_calls == []
    assert result["active_hours"] == 24
    assert [(row["person"], row["problem"], row["jira_key"]) for row in result["findings"]][:2] == [
        ("Alice", wr.T_NOT_UPDATED, "PHS-1"),
        ("Alice", wr.T_NO_JIRA, ""),
    ]
    assert ("bob", wr.T_NOT_FOUND, "MC3-9") in [
        (row["person"], row["problem"], row["jira_key"]) for row in result["findings"]
    ]


def test_run_check_fails_as_a_whole_when_jira_or_the_split_fails(conn) -> None:
    set_members(conn, [{"sheet": "Alice", "jira_username": "alice"}, {"sheet": "Boss", "skip": True}])
    with pytest.raises(RuntimeError, match="查询 JIRA PHS-1 失败：HTTP 500"):
        run(conn, FakeLlm(ITEMS), {"PHS-1": 500})

    def broken(_system: str, _prompt: str) -> str:
        return "抱歉，我无法处理"

    reports, _undated = wr.read_reports(workbook_bytes({"Carol": [(dt.datetime(2026, 10, 10), "1. 新内容")]}))
    with pytest.raises(RuntimeError, match="Carol：LLM 拆分工作项失败"):
        weekly_report_service.run_check(conn, reports, now=NOW, llm_call=broken, fetch_activity=fake_fetch({}))


def test_run_check_stops_on_request_without_finishing_everyone(conn) -> None:
    set_members(conn, [{"sheet": "Alice", "jira_username": "alice"}, {"sheet": "Boss", "skip": True}])
    llm = FakeLlm(ITEMS)
    calls: list[str] = []

    def counting_llm(system: str, prompt: str) -> str:
        calls.append(prompt)
        return llm(system, prompt)

    reports, _undated = wr.read_reports(workbook_bytes(SHEETS))
    with pytest.raises(weekly_report_service.CheckCancelled):
        weekly_report_service.run_check(
            conn, reports, now=NOW, llm_call=counting_llm, fetch_activity=fake_fetch({}),
            should_stop=lambda: len(calls) >= 1,
        )
    # The call under way finished; no further LLM call was made.
    assert len(calls) == 1


def test_run_check_reports_llm_totals_and_elapsed_time(conn) -> None:
    set_members(conn, [{"sheet": "Alice", "jira_username": "alice"}, {"sheet": "Boss", "skip": True}])
    issues = {"PHS-1": activity("PHS-1", comments=[("alice", NOW, "完成")])}

    result = run(conn, FakeLlm(ITEMS), issues)

    totals = result["summary"]["llm"]
    # Two splits (Alice, bob) and one consistency check, none of them streamed.
    assert (totals["calls"], totals["finished"], totals["received"]) == (3, 3, 0)
    assert totals["sent_chars"] > 0
    assert isinstance(result["summary"]["elapsed_seconds"], int)


def test_a_failed_consistency_check_is_noted_not_fatal(conn) -> None:
    set_members(conn, [{"sheet": "Alice", "jira_username": "alice"}, {"sheet": "Boss", "skip": True}])

    class HalfBroken(FakeLlm):
        def __call__(self, system: str, prompt: str) -> str:
            if prompt.startswith("判断一位工程师"):
                return "不是 JSON"
            return super().__call__(system, prompt)

    result = run(conn, HalfBroken(ITEMS), {"PHS-1": activity("PHS-1", comments=[("alice", NOW, "完成")])})

    alice = result["summary"]["people"][0]
    assert alice["note"].startswith("一致性分析失败（调优）")
    assert wr.T_PARTIAL not in [row["problem"] for row in result["findings"]]


def add_user(conn, username: str, display_name: str) -> None:
    users_repo.insert_user(
        conn, username=username, password_hash="", role="Owner", auth_source="ldap", display_name=display_name,
    )
    conn.commit()


def test_sheets_map_only_through_the_manual_table_ignoring_case(conn) -> None:
    add_user(conn, "m00859", "Jiazhen Gong (m00859)")
    add_user(conn, "zhawu", "Zhao Wu (zhawu)")
    set_members(conn, [
        {"sheet": "jiazheng", "jira_username": "m00859"},
        {"sheet": "WUZHAO", "jira_username": "zhawu", "skip": True},
        {"sheet": "project", "skip": True},
        {"sheet": "git & HPC发布需求清单", "skip": True},
        {"sheet": "Alice", "jira_username": ""},
    ])
    sheets = {
        "Project": [(dt.datetime(2026, 10, 10), "不是人")],
        "JiaZheng": [(dt.datetime(2026, 10, 10), BOB_TEXT)],
        "WuZhao": [(dt.datetime(2026, 10, 10), "1. 管理")],
        "Alice": [(dt.datetime(2026, 10, 10), ALICE_TEXT)],
        "HaiYang": [(dt.datetime(2026, 10, 10), ALICE_TEXT)],
        "git & HPC发布需求清单": [("清单", "x")],
        "Intern": [("没有日期", "x")],
    }
    reports, undated = wr.read_reports(workbook_bytes(sheets))

    preview = weekly_report_service.preview(conn, reports, undated)

    assert [(row["sheet"], row["username"], row["display_name"], row["status"], row["reason"]) for row in preview["mapping"]] == [
        ("Alice", "", "", "check", "找不到对应的用户"),
        ("HaiYang", "", "", "check", "找不到对应的用户"),
        ("JiaZheng", "m00859", "Jiazhen Gong", "check", ""),
        ("Project", "", "", "skip", "手工对应里设置为不检查"),
        ("WuZhao", "zhawu", "Zhao Wu", "skip", "手工对应里设置为不检查"),
    ]
    assert preview["people_count"] == 3
    assert preview["unmapped_sheets"] == ["Alice", "HaiYang"]
    # The skipped sheet without dates is not reported as undated.
    assert preview["undated_sheets"] == ["Intern"]

    result = weekly_report_service.run_check(
        conn, reports, undated=tuple(undated), now=NOW, llm_call=FakeLlm(ITEMS),
        fetch_activity=fake_fetch({"MC3-9": activity("MC3-9", assignee="m00859", events=[("m00859", NOW)])}),
        consistency=False,
    )
    summary = result["summary"]
    assert summary["not_checked"] == ["git & HPC发布需求清单", "Project", "WuZhao"]
    assert [(person["sheet"], person["username"], person["note"]) for person in summary["people"]] == [
        ("Alice", "", "找不到对应的用户，只检查是否写了 JIRA ID"),
        ("HaiYang", "", "找不到对应的用户，只检查是否写了 JIRA ID"),
        ("JiaZheng", "m00859", ""),
    ]


def test_store_week_keeps_only_the_last_version(conn) -> None:
    set_members(conn, [{"sheet": "Alice", "jira_username": "alice"}, {"sheet": "Boss", "skip": True}])
    issues = {"PHS-1": activity("PHS-1", comments=[("alice", NOW, "完成")])}

    def store(result: dict, name: str, uploader: str, analyst: str) -> None:
        weekly_report_service.store_week(
            conn, result, xlsx_name=name, xlsx_size=2048,
            uploaded_by=uploader, uploaded_at="2026-10-10 17:00:00", analyzed_by=analyst, role="RM",
        )

    store(run(conn, FakeLlm(ITEMS), issues), "first.xlsx", "rm1", "rm1")
    assert len(weekly_report_service.list_findings(conn, "2026-W41") or []) == 2
    store(run(conn, FakeLlm(ITEMS), issues, consistency=False), "second.xlsx", "rm1", "rm2")

    weeks = weekly_report_service.list_weeks(conn)
    assert weeks == [{
        "week_key": "2026-W41", "label": "2026 W41（10月5日-10月11日）",
        "week_start": "2026-10-05", "week_end": "2026-10-11", "finding_count": 2,
    }]
    week = weekly_report_service.get_week(conn, "2026-W41")
    assert (week["xlsx_name"], week["uploaded_by"], week["analyzed_by"]) == ("second.xlsx", "rm1", "rm2")
    assert len(weekly_report_service.list_findings(conn, "2026-W41") or []) == 2
    assert weekly_report_service.list_findings(conn, "2026-W41", problem=wr.T_BAD_ID) == [
        {**row, "id": row["id"]}
        for row in weekly_report_service.list_findings(conn, "2026-W41") or []
        if row["problem"] == wr.T_BAD_ID
    ]
    events = [row["event"] for row in conn.execute("SELECT event FROM audit ORDER BY id")]
    assert events == ["weekly_report_analyze", "weekly_report_analyze"]


def test_exports_match_the_script_layout(conn) -> None:
    set_members(conn, [{"sheet": "Alice", "jira_username": "alice"}, {"sheet": "Boss", "skip": True}])
    old = SINCE - dt.timedelta(days=2)
    issues = {"PHS-1": activity("PHS-1", events=[("alice", old)])}
    weekly_report_service.store_week(
        conn, run(conn, FakeLlm(ITEMS), issues), xlsx_name="plan.xlsx", xlsx_size=1,
        uploaded_by="rm", uploaded_at="2026-10-10 17:00:00", analyzed_by="rm", role="RM",
    )
    week = weekly_report_service.get_week(conn, "2026-W41")
    findings = weekly_report_service.list_findings(conn, "2026-W41") or []

    csv_text = weekly_report_service.export_csv(findings)
    assert csv_text.startswith("﻿姓名,周报日期,问题,工作项,JIRA,JIRA状态,assignee,最后更新时间,最后更新人,说明\r\n")
    assert "Alice,2026-10-10,JIRA未更新,调优,PHS-1,In Progress,Alice,2026-09-30 18:00,Alice,\r\n" in csv_text

    assert weekly_report_service.export_markdown(week, findings) == (
        "# 周报 JIRA 检查 2026-10-10\n"
        "\n"
        "本期周报日期 2026-10-10；JIRA 更新窗口 2026-10-02 18:00 起（192 小时）；"
        "不检查：Boss、Project。\n"
        "\n"
        "## 每人明细\n"
        "\n"
        "### Alice\n"
        "- JIRA未更新：PHS-1（调优，In Progress；最后更新 2026-09-30 18:00 by Alice）\n"
        "- 未写JIRA：调研新方案\n"
        "- JIRA ID不完整：11372（修复）\n"
        "\n"
        "### bob\n"
        "- 备注：找不到对应的用户，只检查是否写了 JIRA ID\n"
        "- 没有发现问题\n"
        "\n"
        "## 汇总\n"
        "\n"
        "| 姓名 | 周报日期 | 工作项 | 周报日期异常 | 周报未更新 | 未写JIRA | JIRA ID不完整 | JIRA不存在 | JIRA无权限 | JIRA未更新 | 更新与周报不符 | 更新与周报部分相符 |\n"
        "|---|---|---|---|---|---|---|---|---|---|---|---|\n"
        "| Alice | 2026-10-10 | 3 |  |  | 1 | 1 |  |  | 1 |  |  |\n"
        "| bob | 2026-10-10 | 1 |  |  |  |  |  |  |  |  |  |\n"
        "\n"
    )
    assert weekly_report_service.export_filename(week, "csv") == "weekly_jira_check_2026-10-10.csv"


def test_members_save_validates_and_detects_concurrent_edits(conn) -> None:
    current = weekly_report_service.get_members(conn)
    saved = weekly_report_service.save_members(
        conn,
        [{"sheet": " Alice ", "jira_username": " alice ", "skip": False, "note": ""}, {"sheet": "Boss", "skip": True}],
        version=current["version"], user="rm", role="RM",
    )
    assert saved["members"] == [
        {"sheet": "Alice", "jira_username": "alice", "skip": False, "note": ""},
        {"sheet": "Boss", "jira_username": "", "skip": True, "note": ""},
    ]
    with pytest.raises(ValueError, match="已被其他人修改"):
        weekly_report_service.save_members(conn, [], version=current["version"], user="rm2", role="RM")
    with pytest.raises(ValueError, match="周报页名重复"):
        weekly_report_service.save_members(
            conn, [{"sheet": "A"}, {"sheet": "a"}], version=saved["version"], user="rm", role="RM",
        )
    assert len(weekly_report_service.get_members(conn)["members"]) == 2


def test_update_window_defaults_to_192_hours_and_is_checked() -> None:
    assert weekly_report_service.DEFAULT_ACTIVE_HOURS == 192
    assert weekly_report_service.validate_active_hours("48") == 48
    for bad in ("soon", 0, 24 * 60 + 1):
        with pytest.raises(ValueError, match="更新窗口"):
            weekly_report_service.validate_active_hours(bad)


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------

@pytest.fixture()
def site(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "db_path", tmp_path / "main.db")
    monkeypatch.setattr(weekly_report_jobs, "registry", weekly_report_jobs.WeeklyReportRegistry())
    from app.api.routers import weekly_report as router_module

    monkeypatch.setattr(router_module, "registry", weekly_report_jobs.registry)
    current = {"role": "RM", "username": "rm"}
    app = create_app()
    app.dependency_overrides[require_login] = lambda: {
        "username": current["username"], "display_name": current["username"], "role": current["role"],
    }
    client = TestClient(app, raise_server_exceptions=False)

    def as_user(role: str, username: str | None = None) -> None:
        current.update(role=role, username=username or role.lower())

    client.as_user = as_user  # type: ignore[attr-defined]
    return client


def upload(client: TestClient, content: bytes, filename: str = "PDE HPC Project Plan 20261010.xlsx"):
    return client.post(
        "/api/weekly-report/uploads", params={"filename": filename}, content=content,
        headers={"Content-Type": "application/octet-stream"},
    )


def wait_for_job(client: TestClient, job_id: str) -> dict:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        job = client.get(f"/api/weekly-report/jobs/{job_id}").json()
        if job["status"] != "running":
            return job
        time.sleep(0.02)
    raise AssertionError("job did not finish")


@pytest.mark.parametrize("role", ALL_ROLES)
def test_api_role_gates(site: TestClient, role: str) -> None:
    site.as_user(role)
    can_view = role in roles_for_tab("weekly-report")
    can_run = role in roles_for_capability("weekly-report.run")

    assert site.get("/api/weekly-report/weeks").status_code == (200 if can_view else 403)
    assert site.get("/api/weekly-report/weeks/2026-W41").status_code == (404 if can_view else 403)
    assert site.get("/api/weekly-report/weeks/2026-W41/findings").status_code == (404 if can_view else 403)
    assert site.get("/api/weekly-report/weeks/2026-W41/export").status_code == (404 if can_view else 403)
    assert site.get("/api/weekly-report/members").status_code == (200 if can_run else 403)
    assert site.put("/api/weekly-report/members", json={"members": [], "version": "x"}).status_code == (
        400 if can_run else 403
    )
    assert upload(site, b"").status_code == (400 if can_run else 403)
    assert site.post("/api/weekly-report/uploads/none/analyze", json={}).status_code == (400 if can_run else 403)
    assert site.get("/api/weekly-report/jobs/none").status_code == (404 if can_run else 403)
    assert site.get("/api/weekly-report/jobs").status_code == (200 if can_run else 403)


def test_permissions_match_the_design() -> None:
    assert roles_for_tab("weekly-report") == ("RM", "Owner")
    assert roles_for_capability("weekly-report.run") == ("RM",)


def test_upload_analyze_view_and_export_flow(site: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    conf = tmp_path / "release_system.conf"
    conf.write_text(
        "[jira]\nJIRA_BASE_URL = http://jira\nJIRA_TOKEN = t\n"
        "[llm]\nLLM_BASE_URL = http://llm/v1\nLLM_MODEL = m\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(settings, "runtime_conf_path", conf)
    from app.integrations import llm

    monkeypatch.setattr(llm, "chat_json", lambda system, prompt, **_kwargs: FakeLlm(ITEMS)(system, prompt))
    monkeypatch.setattr(
        jira, "get_issue_activity",
        lambda key, since: activity(key, events=[("alice", since - dt.timedelta(days=1))]),
    )

    members = site.get("/api/weekly-report/members").json()
    saved = site.put("/api/weekly-report/members", json={
        "version": members["version"],
        "members": [
            {"sheet": "Alice", "jira_username": "alice"},
            {"sheet": "Boss", "skip": True},
            {"sheet": "Project", "skip": True},
        ],
    })
    assert saved.status_code == 200

    assert upload(site, b"junk").json()["error"] == "无法读取该文件：不是有效的 .xlsx 周报"
    assert upload(site, workbook_bytes(SHEETS), filename="plan.xls").json()["error"] == "只接受 .xlsx 文件"

    content = workbook_bytes(SHEETS)
    preview = upload(site, content).json()
    assert {key: preview[key] for key in (
        "filename", "size", "uploaded_by", "report_date", "week_exists", "people_count", "unmapped_sheets", "active_hours",
    )} == {
        "filename": "PDE HPC Project Plan 20261010.xlsx", "size": len(content), "uploaded_by": "rm",
        "report_date": "2026-10-10", "week_exists": False, "people_count": 2,
        "unmapped_sheets": ["bob"], "active_hours": 192,
    }
    assert [(row["sheet"], row["username"], row["status"], row["reason"]) for row in preview["mapping"]] == [
        ("Alice", "alice", "check", ""),
        ("bob", "", "check", "找不到对应的用户"),
        ("Boss", "", "skip", "手工对应里设置为不检查"),
        ("Project", "", "skip", "手工对应里设置为不检查"),
    ]
    assert preview["week"]["label"] == "2026 W41（10月5日-10月11日）"

    site.as_user("RM", "rm2")
    assert site.post(
        f"/api/weekly-report/uploads/{preview['upload_id']}/analyze", json={"report_date": "10/10"},
    ).status_code == 400
    started = site.post(f"/api/weekly-report/uploads/{preview['upload_id']}/analyze", json={"consistency": False})
    assert started.status_code == 200
    job = wait_for_job(site, started.json()["job_id"])
    assert (job["status"], job["week_key"], job["done"], job["total"]) == ("completed", "2026-W41", 2, 2)
    # The parsed workbook is gone once its week is stored.
    again = site.post(f"/api/weekly-report/uploads/{preview['upload_id']}/analyze", json={})
    assert again.status_code == 400 and "请重新上传" in again.json()["error"]
    assert upload(site, content).json()["week_exists"] is True

    site.as_user("Owner")
    assert site.get("/api/weekly-report/weeks").json()["weeks"][0]["finding_count"] == 3
    week = site.get("/api/weekly-report/weeks/2026-W41").json()["week"]
    assert (week["uploaded_by"], week["analyzed_by"], week["xlsx_size"]) == ("rm", "rm2", len(content))
    assert week["jira_browse_url"] == "http://jira/browse/"
    assert week["problem_types"][0] == wr.T_BAD_DATE
    findings = site.get("/api/weekly-report/weeks/2026-W41/findings", params={"person": "Alice"}).json()["findings"]
    assert [row["problem"] for row in findings] == [wr.T_NOT_UPDATED, wr.T_NO_JIRA, wr.T_BAD_ID]
    assert site.get(
        "/api/weekly-report/weeks/2026-W41/findings", params={"problem": wr.T_NO_JIRA},
    ).json()["findings"][0]["item"] == "调研新方案"

    exported = site.get("/api/weekly-report/weeks/2026-W41/export", params={"format": "csv"})
    assert exported.content.startswith(b"\xef\xbb\xbf\xe5\xa7\x93\xe5\x90\x8d,")
    assert "weekly_jira_check_2026-10-10.csv" in exported.headers["content-disposition"]
    markdown = site.get("/api/weekly-report/weeks/2026-W41/export").text
    assert markdown.startswith("# 周报 JIRA 检查 2026-10-10\n")
    assert site.get("/api/weekly-report/weeks/2026-W41/export", params={"format": "pdf"}).status_code == 400

    with connect(settings.db_path) as audit_conn:
        events = [row["event"] for row in audit_conn.execute("SELECT event FROM audit WHERE event LIKE 'weekly_report_%' ORDER BY id")]
    assert events == [
        "weekly_report_members", "weekly_report_upload", "weekly_report_analyze", "weekly_report_upload",
    ]
    # Nothing was written under the data directories: the workbook only lived in memory.
    assert not list(tmp_path.rglob("*.xlsx"))


def test_failed_analysis_keeps_the_previous_version_and_the_upload(site: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    preview = upload(site, workbook_bytes(SHEETS)).json()
    started = site.post(f"/api/weekly-report/uploads/{preview['upload_id']}/analyze", json={}).json()

    job = wait_for_job(site, started["job_id"])

    assert job["status"] == "failed"
    assert "未配置 LLM" in job["error"]
    assert site.get("/api/weekly-report/weeks").json() == {"weeks": []}
    # The upload survives a failed run, so the analysis can be started again.
    retry = site.post(f"/api/weekly-report/uploads/{preview['upload_id']}/analyze", json={})
    assert retry.status_code == 200


def test_a_running_analysis_can_be_stopped(site: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import threading

    conf = tmp_path / "release_system.conf"
    conf.write_text(
        "[jira]\nJIRA_BASE_URL = http://jira\nJIRA_TOKEN = t\n"
        "[llm]\nLLM_BASE_URL = http://llm/v1\nLLM_MODEL = m\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(settings, "runtime_conf_path", conf)
    from app.integrations import llm

    entered = threading.Event()
    hang = {"on": True}

    def hanging_llm(system: str, prompt: str, cancel=None, **_kwargs) -> str:
        # Like a streamed reply that never ends unless its connection is cut.
        if hang["on"]:
            entered.set()
            cut = threading.Event()
            cancel.on_cancel(cut.set)
            if not cut.wait(30):
                raise AssertionError("the call was never cut")
            raise llm.LLMCancelled("LLM 调用已中止")
        return FakeLlm(ITEMS)(system, prompt)

    monkeypatch.setattr(llm, "chat_json", hanging_llm)
    monkeypatch.setattr(jira, "get_issue_activity", lambda key, since: activity(key))
    members = site.get("/api/weekly-report/members").json()
    site.put("/api/weekly-report/members", json={
        "version": members["version"],
        "members": [{"sheet": "Alice", "jira_username": "alice"}, {"sheet": "Boss", "skip": True},
                    {"sheet": "Project", "skip": True}],
    })
    preview = upload(site, workbook_bytes(SHEETS)).json()
    job = site.post(f"/api/weekly-report/uploads/{preview['upload_id']}/analyze", json={}).json()
    assert entered.wait(5)

    # A page reopened mid-analysis finds it again.
    [running] = site.get("/api/weekly-report/jobs").json()["jobs"]
    assert {key: running[key] for key in ("job_id", "status", "week_key", "week_label", "filename", "user")} == {
        "job_id": job["job_id"], "status": "running", "week_key": "2026-W41",
        "week_label": "2026 W41（10月5日-10月11日）", "filename": "PDE HPC Project Plan 20261010.xlsx", "user": "rm",
    }
    # The same week cannot be analysed twice at once, even from a fresh upload.
    site.as_user("RM", "rm2")
    second = upload(site, workbook_bytes(SHEETS)).json()
    refused = site.post(f"/api/weekly-report/uploads/{second['upload_id']}/analyze", json={})
    assert refused.status_code == 400
    assert refused.json()["error"] == "2026 W41（10月5日-10月11日） 正在分析中（rm 发起），请等它结束或先中止"
    # Another week can still be analysed.
    assert site.post(
        f"/api/weekly-report/uploads/{second['upload_id']}/analyze", json={"report_date": "2026-10-17"},
    ).status_code == 200
    site.as_user("RM")
    assert {item["week_key"] for item in site.get("/api/weekly-report/jobs").json()["jobs"]} == {"2026-W41", "2026-W42"}

    site.as_user("Owner")
    assert site.post(f"/api/weekly-report/jobs/{job['job_id']}/cancel").status_code == 403
    site.as_user("RM")
    started = time.monotonic()
    stopping = site.post(f"/api/weekly-report/jobs/{job['job_id']}/cancel").json()
    assert stopping["stopping"] is True
    # Splits are under way (Alice's, maybe bob's too): started, none finished.
    assert stopping["llm"]["calls"] >= 1 and stopping["llm"]["finished"] == 0

    final = wait_for_job(site, job["job_id"])
    for other in site.get("/api/weekly-report/jobs").json()["jobs"]:
        site.post(f"/api/weekly-report/jobs/{other['job_id']}/cancel")
        wait_for_job(site, other["job_id"])
    assert site.get("/api/weekly-report/jobs").json() == {"jobs": []}
    # The hanging calls were cut, not waited for.
    assert time.monotonic() - started < 3
    assert (final["status"], final["message"]) == ("cancelled", "分析已中止，已有结果没有改动")
    assert site.get("/api/weekly-report/weeks").json() == {"weeks": []}
    assert site.post(f"/api/weekly-report/jobs/{job['job_id']}/cancel").json()["error"] == "分析已经结束，无需中止"
    assert site.post("/api/weekly-report/jobs/unknown/cancel").status_code == 404
    # The upload survives the stop, so the analysis can be started again.
    hang["on"] = False
    again = site.post(f"/api/weekly-report/uploads/{preview['upload_id']}/analyze", json={})
    assert again.status_code == 200
    assert wait_for_job(site, again.json()["job_id"])["status"] == "completed"


def test_uploads_expire_and_oversized_files_are_refused(site: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    preview = upload(site, workbook_bytes(SHEETS)).json()
    monkeypatch.setattr(weekly_report_jobs, "UPLOAD_TTL_SECONDS", -1)
    expired = site.post(f"/api/weekly-report/uploads/{preview['upload_id']}/analyze", json={})
    assert expired.status_code == 400 and "上传已失效" in expired.json()["error"]

    from app.api.routers import weekly_report as router_module

    monkeypatch.setattr(router_module, "MAX_UPLOAD_BYTES", 10)
    assert upload(site, workbook_bytes(SHEETS)).status_code == 413
