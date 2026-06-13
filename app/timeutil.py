"""Time utilities for the HPC release system.

Storage convention: naive Beijing time (no tzinfo, no UTC offset).
Do NOT emit UTC.  Ported from release_system/core.py:90-131 (§5.4).
"""
from __future__ import annotations

import datetime as dt

BEIJING_TZ = dt.timezone(dt.timedelta(hours=8))


def beijing_now() -> dt.datetime:
    """Current Beijing time as a naive datetime (no tzinfo)."""
    return dt.datetime.now(BEIJING_TZ).replace(tzinfo=None, microsecond=0)


def beijing_timestamp() -> str:
    """Current Beijing time as ``YYYY-MM-DD HH:MM:SS`` string (naive)."""
    return beijing_now().strftime("%Y-%m-%d %H:%M:%S")


def normalize_deadline(value: str | None) -> str:
    """Normalize a deadline string to ``YYYY-MM-DD HH:MM`` (Beijing time).

    Accepts ``''`` (returns ``''``), ``YYYY-MM-DD``, ``YYYY-MM-DDTHH:MM[:SS]``,
    or ``YYYY-MM-DD HH:MM[:SS]``.  Empty deadline means "no deadline set".
    """
    text = (value or "").strip()
    if not text:
        return ""
    text = text.replace("T", " ")
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            parsed = dt.datetime.strptime(text, fmt)
            if fmt == "%Y-%m-%d":
                parsed = parsed.replace(hour=23, minute=59)
            return parsed.strftime("%Y-%m-%d %H:%M")
        except ValueError:
            continue
    raise ValueError(f"Invalid deadline: {value!r}; expected YYYY-MM-DD or YYYY-MM-DD HH:MM")


def parse_deadline(value: str | None) -> dt.datetime | None:
    """Parse a deadline string to a naive datetime, or None if empty."""
    if not value:
        return None
    return dt.datetime.strptime(normalize_deadline(value), "%Y-%m-%d %H:%M")


def is_before(deadline: str | None, *, ref: dt.datetime | None = None) -> bool:
    """True if the reference moment is strictly before the deadline.

    Empty/None deadline means "no deadline set" → treated as infinite future,
    so this returns True (i.e. the action is still allowed).
    """
    dl = parse_deadline(deadline)
    if dl is None:
        return True
    return (ref or beijing_now()) < dl
