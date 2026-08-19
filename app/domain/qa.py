"""QA status vocabulary and behavior shared with the React client."""
from __future__ import annotations

from app.domain.shared_metadata import mapping_section

_QA_STATUS_METADATA = mapping_section("qa_statuses")

QA_STATUS_OPTIONS = tuple(
    status
    for status, _ in sorted(
        _QA_STATUS_METADATA.items(),
        key=lambda item: int(item[1].get("order", -1)),
    )
)
QA_STATUSES: frozenset[str] = frozenset(_QA_STATUS_METADATA)
_DEFAULTS = [
    status
    for status, metadata in _QA_STATUS_METADATA.items()
    if metadata.get("default") is True
]
if len(_DEFAULTS) != 1:
    raise RuntimeError("qa_statuses must define exactly one default")
QA_STATUS_DEFAULT = _DEFAULTS[0]


def qa_status_label(status: str) -> str:
    metadata = _QA_STATUS_METADATA.get(status)
    return str(metadata.get("label") or status) if metadata else status


def issue_note_required(status: str) -> bool:
    metadata = _QA_STATUS_METADATA.get(status)
    return bool(metadata and metadata.get("issue_note_required") is True)


def is_releasable(status: str) -> bool:
    metadata = _QA_STATUS_METADATA.get(status)
    return bool(metadata and metadata.get("releasable") is True)


def issue_note_prefix(status: str) -> str:
    metadata = _QA_STATUS_METADATA.get(status)
    return str(metadata.get("issue_note_prefix") or "") if metadata else ""
