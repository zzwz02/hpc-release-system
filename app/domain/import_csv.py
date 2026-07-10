"""Init-CSV parsing helpers — ported from core.py:856-936.

Pure functions: no HTTP, no DB, unit-testable in isolation.
"""
from __future__ import annotations

import csv
import io

from app.domain.textutil import normalize_name


def parse_csv_text(text: str) -> list[dict[str, str]]:
    return [{k: (v or "").strip() for k, v in row.items()} for row in csv.DictReader(io.StringIO(text.lstrip("﻿")))]


def parse_alias_lines(raw: str = "") -> dict[str, str]:
    aliases: dict[str, str] = {}
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        left, right = [part.strip() for part in line.split("=", 1)]
        if left and right:
            aliases[normalize_name(left)] = normalize_name(right)
    return aliases


def infer_doc_target(category: str = "", app_type: str = "") -> str:
    """Pick the documentation target. AI-for-Science apps document into the
    AI4Sci guide; HPC apps and 工具 document into the HPC manual.

    类别 (category) is the authoritative signal. app_type is only a fallback
    for CSVs without a 类别 column — and an HPC app_type such as
    'HPC框架/工具' must NOT be misread as AI4Sci just for containing '框架'.
    """
    cat = str(category or "").strip().lower()
    typ = str(app_type or "").strip().lower()
    if "ai for science" in cat or "ai4sci" in cat:
        return "ai4sci"
    if "hpc" in cat or "工具" in cat:
        return "manual"
    # category absent / unrecognized — fall back to app_type markers
    if "ai" in typ or typ.endswith("模型"):
        return "ai4sci"
    return "manual"


def csv_value(row: dict[str, str], *names: str) -> str:
    for name in names:
        value = (row.get(name) or "").strip()
        if value:
            return value
    return ""


def csv_checkmark(value: str | None) -> bool:
    """Interpret a CSV sanity cell as a boolean pass mark.

    '✔' / '√' etc. (and a few affirmative words) count as passed; empty
    cells or descriptive notes like 'arm sanity' do not.
    """
    v = str(value or "").strip().lower()
    if not v:
        return False
    if any(mark in v for mark in ("✔", "✓", "√", "✅")):
        return True
    return v in ("pass", "passed", "ok", "yes", "y", "true", "1", "通过", "已通过")


def init_csv_official_name(row: dict[str, str]) -> str:
    return csv_value(row, "官方名称", "名称", "app_name")


def init_csv_doc_category(row: dict[str, str]) -> str:
    if "APP类型" in row:
        return csv_value(row, "类型")
    return csv_value(row, "类别", "类型")


def init_csv_app_type(row: dict[str, str]) -> str:
    return csv_value(row, "APP类型", "类型")


def canonical_id(name: str, aliases: dict[str, str] | None = None) -> str:
    normalized = normalize_name(name)
    return (aliases or {}).get(normalized, normalized)
