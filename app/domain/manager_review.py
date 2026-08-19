"""Validated Manager Review field metadata shared with the React client."""
from __future__ import annotations

from app.domain.shared_metadata import mapping_section

_FIELD_METADATA = mapping_section("manager_review_fields")

FIELDS = tuple(
    field
    for field, _ in sorted(
        _FIELD_METADATA.items(),
        key=lambda item: int(item[1].get("order", -1)),
    )
)
if {
    int(metadata.get("order", -1))
    for metadata in _FIELD_METADATA.values()
    if isinstance(metadata, dict)
} != set(range(len(FIELDS))):
    raise RuntimeError("manager_review_fields must define unique contiguous orders")

FIELD_LABELS = {
    field: str(metadata.get("label") or field)
    for field, metadata in _FIELD_METADATA.items()
}
DEFAULT_FIELDS = tuple(
    field for field in FIELDS if _FIELD_METADATA[field].get("default") is True
)
NON_RELEASE_BLANK_FIELDS = frozenset(
    field
    for field in FIELDS
    if _FIELD_METADATA[field].get("blank_for_non_release") is True
)
if not DEFAULT_FIELDS:
    raise RuntimeError("manager_review_fields must define at least one default field")
