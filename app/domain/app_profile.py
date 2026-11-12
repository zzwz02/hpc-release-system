"""Optional App profile helpers (names, document target, description limit).

Pure functions: no HTTP, no DB, unit-testable in isolation.
"""
from __future__ import annotations


from app.domain.shared_metadata import mapping_section

MAX_APP_DESCRIPTION_CHARS = 30

APP_META_LABELS = {
    "official_name": "官方名称",
    "type": "App类型",
    "official_url": "官方 URL",
    "description": "描述",
    "doc_target": "文档类型",
    "owners": "Owner",
}

_DOC_TARGET_METADATA = mapping_section("doc_targets")
DOC_TARGETS: frozenset[str] = frozenset(_DOC_TARGET_METADATA)
_DOC_TARGET_DEFAULTS = [
    target
    for target, metadata in _DOC_TARGET_METADATA.items()
    if metadata.get("default") is True
]
if len(_DOC_TARGET_DEFAULTS) != 1:
    raise RuntimeError("doc_targets must define exactly one default")
DOC_TARGET_DEFAULT = _DOC_TARGET_DEFAULTS[0]
_DOC_TARGET_ALIASES = {
    str(alias).strip().lower(): target
    for target, metadata in _DOC_TARGET_METADATA.items()
    for alias in [target, *(metadata.get("aliases") or [])]
}


def normalize_doc_target(value: str | None) -> str:
    target = (value or DOC_TARGET_DEFAULT).strip().lower()
    return _DOC_TARGET_ALIASES.get(target, DOC_TARGET_DEFAULT)


def is_valid_doc_target(value: object) -> bool:
    """Validate an explicitly selected HPC/AI4Sci target without defaulting blanks."""
    return isinstance(value, str) and value.strip().lower() in _DOC_TARGET_ALIASES


def app_description_count(value: str | None) -> int:
    text = (value or "").strip()
    count = 0
    i = 0
    while i < len(text):
        ch = text[i]
        if ch.isspace():
            i += 1
            continue
        if ch.isascii() and ch.isalnum():
            while i < len(text) and text[i].isascii() and text[i].isalnum():
                i += 1
            count += 1
            continue
        count += 1
        i += 1
    return count


def normalize_app_description(value: str | None) -> str:
    description = (value or "").strip()
    if app_description_count(description) > MAX_APP_DESCRIPTION_CHARS:
        raise ValueError(f"描述不能超过{MAX_APP_DESCRIPTION_CHARS}字")
    return description


def display_name(official_name: str | None, version: str | None = "") -> str:
    """Human-facing app name: official name plus version when known."""
    official = (official_name or "").strip()
    ver = (version or "").strip()
    return f"{official} {ver}".strip() if ver else official
