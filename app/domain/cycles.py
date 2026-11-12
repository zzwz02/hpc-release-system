"""Monthly cycle rules that need no database.

Pure functions: no HTTP, no DB, unit-testable in isolation.
"""
from __future__ import annotations

import calendar
import datetime as dt
from typing import Any

from app.domain import app_info as app_info_domain
from app.domain import qa as qa_domain
from app.timeutil import normalize_deadline, parse_deadline

ENTRY_ACTIVE = "active"
ENTRY_WITHDRAWN = "withdrawn"

RESULT_PENDING = ""
RESULT_RELEASED = "released"
RESULT_FAILED = "failed"

SCOPE_PENDING = "pending"
SCOPE_APPROVED = "approved"
SCOPE_REJECTED = "rejected"
SCOPE_SUPERSEDED = "superseded"
SCOPE_CLOSED = "closed"

CICD_RUNNING = "running"
CICD_STOPPED = "stopped"


def default_dates(year: int, month: int) -> dict[str, str]:
    """Default dates for the cycle of *year*-*month*.

    Signup closes on the 1st; QA closes five days before the month ends
    (10/26, 11/25, 12/26); publication is on the last day.
    """
    last_day = calendar.monthrange(year, month)[1]
    last = dt.date(year, month, last_day)
    return {
        "signup_deadline": f"{dt.date(year, month, 1).isoformat()} 23:59",
        "qa_deadline": f"{(last - dt.timedelta(days=5)).isoformat()} 23:59",
        "release_date": last.isoformat(),
    }


def normalize_release_date(value: str | None) -> str:
    text = (value or "").strip()
    if not text:
        return ""
    try:
        return dt.date.fromisoformat(text[:10]).isoformat()
    except ValueError as exc:
        raise ValueError(f"发布日格式错误：{value!r}，应为 YYYY-MM-DD") from exc


def normalize_cycle_dates(
    signup_deadline: str | None,
    qa_deadline: str | None,
    release_date: str | None,
) -> dict[str, str]:
    """Normalize and order-check the three cycle dates (all required)."""
    signup = normalize_deadline(signup_deadline)
    qa_close = normalize_deadline(qa_deadline)
    publish = normalize_release_date(release_date)
    if not signup or not qa_close or not publish:
        raise ValueError("报名截止、QA 截止和发布日都必须填写")
    signup_at = parse_deadline(signup)
    qa_at = parse_deadline(qa_close)
    if signup_at and qa_at and signup_at > qa_at:
        raise ValueError(f"报名截止（{signup}）不能晚于 QA 截止（{qa_close}）")
    if qa_at and qa_at.date().isoformat() > publish:
        raise ValueError(f"QA 截止（{qa_close}）不能晚于发布日（{publish}）")
    return {"signup_deadline": signup, "qa_deadline": qa_close, "release_date": publish}


def publish_result(qa_status: str) -> str:
    """Entry result at publication: releasable QA verdicts are released."""
    return RESULT_RELEASED if qa_domain.is_releasable(qa_status) else RESULT_FAILED


def known_issues_at_publish(known_issues: str, qa_status: str, qa_note: str) -> str:
    """Known issues written to the release: Owner text plus a has_issues QA note."""
    parts = [known_issues.strip()] if known_issues.strip() else []
    note = (qa_note or "").strip()
    if note and qa_status == "has_issues":
        prefix = qa_domain.issue_note_prefix(qa_status)
        parts.append(f"{prefix}：{note}" if prefix else note)
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Test scope
# ---------------------------------------------------------------------------

def scoped_parsed(parsed: dict[str, Any], excluded: list[str] | set[str]) -> dict[str, Any]:
    """A parsed app_info restricted to the tests still in scope.

    Used so scope comparisons ignore tests the Owner removed.
    """
    result = dict(parsed)
    result["tests"] = app_info_domain.scope_tests(parsed, excluded)
    return result


def follow_decision(
    *,
    before_signup_deadline: bool,
    locked_parsed: dict[str, Any] | None,
    new_parsed: dict[str, Any],
    excluded: list[str] | set[str],
) -> tuple[str, list[str]]:
    """How an entry reacts to a new app_info version.

    Returns ``("follow", [])`` when the entry should move to the new version
    and ``("request", additions)`` when the change widens scope after the
    signup deadline and needs QA approval.
    """
    if before_signup_deadline or locked_parsed is None:
        return "follow", []
    additions = app_info_domain.qa_scope_additions(
        scoped_parsed(locked_parsed, excluded),
        scoped_parsed(new_parsed, excluded),
    )
    return ("request", additions) if additions else ("follow", [])
