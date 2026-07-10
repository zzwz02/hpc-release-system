"""Text/list normalization helpers — ported from core.py:224-258.

Pure functions: no HTTP, no DB, unit-testable in isolation.
"""
from __future__ import annotations

import re
from typing import Any


def normalize_name(value: str | None) -> str:
    text = (value or "").strip().lower()
    text = re.sub(r"\s+", "", text)
    return text.replace("_", "-").replace(".", "-")


def split_list(value: str | None) -> list[str]:
    if not value:
        return []
    parts = re.split(r"[,，、;；/]+", value)
    return sorted({part.strip() for part in parts if part.strip()})


def join_list(values: list[str] | set[str] | tuple[str, ...]) -> str:
    return ",".join(sorted({str(v).strip() for v in values if str(v).strip()}))


def order_chips(values: str | list[str] | set[str] | tuple[str, ...] | None) -> list[str]:
    """Order chip names alphabetically but always keep x201 last.

    Accepts a comma-separated string or any iterable; dedupes and applies the
    x201-last rule used in the app workbench and the QA release report.
    """
    if isinstance(values, str):
        items: list[Any] = re.split(r"[,，、;；/]+", values)
    else:
        items = list(values or [])
    seen: list[str] = []
    for item in items:
        text = str(item).strip()
        if text and text not in seen:
            seen.append(text)
    rest = sorted((c for c in seen if c.lower() != "x201"), key=str.lower)
    tail = [c for c in seen if c.lower() == "x201"]
    return rest + tail
