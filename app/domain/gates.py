"""Release readiness gates — missing items and final/docs qualification.

Ported from core.py:2042-2133 (missing items) and 3109-3139, 3388-3410 (gates).
Pure functions: no HTTP, no DB, unit-testable in isolation.
"""
from __future__ import annotations

from typing import Any

from app.domain import qa
from app.domain.cicd_config import normalize_community_artifacts
from app.domain.decisions import normalize_release_decision
from app.domain.snapshots import is_valid_doc_target


def missing_item_text(item: Any) -> str:
    """Display text for a missing_items entry.

    Items are stored as ``{"kind": "doc"|"qa", "text": str}``. Older snapshots
    or legacy callers may still pass bare strings; both are handled here so
    downstream display / equality checks keep working through any rolling
    migration.
    """
    if isinstance(item, dict):
        return str(item.get("text", ""))
    return str(item)


def missing_item_kind(item: Any) -> str:
    """Return the kind of a missing_items entry (``"doc"`` or ``"qa"``).

    Falls back to inspecting the text prefix when given a legacy string item,
    so an old snapshot that wasn't refreshed yet still gates correctly.
    """
    if isinstance(item, dict):
        return str(item.get("kind", "doc"))
    return "qa" if str(item).startswith("QA ") else "doc"


def community_fields_required(app: dict[str, Any]) -> bool:
    """Community metadata is required for selected image/package artifacts."""
    return bool(normalize_community_artifacts(app.get("cicd_community_artifact")))


def missing_items_for(app: dict[str, Any], snapshot: dict[str, Any]) -> list[dict[str, str]]:
    """Document classification/community requirements and QA information.

    Each entry is ``{"kind": "doc"|"qa", "text": str}``. ``doc`` entries
    block ``qualifies_for_final``; ``qa`` entries are informational and do
    not (QA status itself is the gate).
    """
    decision = normalize_release_decision(snapshot.get("release_decision"))
    if decision != "release":
        return []
    missing: list[dict[str, str]] = []

    def add_doc(text: str) -> None:
        missing.append({"kind": "doc", "text": text})

    def add_qa(text: str) -> None:
        missing.append({"kind": "qa", "text": text})

    if not is_valid_doc_target(snapshot.get("doc_target")):
        add_doc("缺少类型（HPC/AI4Sci）")
    if not (snapshot.get("type") or "").strip():
        add_doc("缺少 App类型")
    if community_fields_required(app):
        community = snapshot.get("community") or {}
        required = {
            "release_status": "开发者社区发布情况",
            "python_version": "社区包支持 Python 版本",
            "framework_version": "社区包支持框架及版本",
        }
        for key, label in required.items():
            if not (community.get(key) or "").strip():
                add_doc(f"缺少{label}")
    qa_status = snapshot.get("qa_status", qa.QA_STATUS_DEFAULT)
    if qa_status == "not_checked":
        add_qa("QA 未测试")
    elif qa_status == "cannot_release":
        add_qa("QA 标注为不可发布")
    return missing


def docs_gate_items(snapshot: dict[str, Any]) -> list[dict[str, str]]:
    """Doc-gate-blocking items from missing_items: everything except QA kind.

    Filters by ``kind`` for structured entries; falls back to the legacy
    "QA " text-prefix rule for any string entries still in stale snapshots.
    """
    return [item for item in snapshot.get("missing_items", []) if missing_item_kind(item) != "qa"]


def qualifies_for_final(snapshot: dict[str, Any]) -> bool:
    """True if this snapshot passes the QA-gated final-release bar.

    Used by the Manager Review CSV (releasable / not_releasable_reason);
    the release-note filter uses ``qualifies_for_docs`` instead (FastAPI rule).
    """
    if snapshot.get("release_decision") != "release":
        return False
    if docs_gate_items(snapshot):
        return False
    if qa.is_releasable(str(snapshot.get("qa_status") or qa.QA_STATUS_DEFAULT)):
        return True
    return False


def qualifies_for_docs(snapshot: dict[str, Any]) -> bool:
    """True if this snapshot should be included in HPC/AI4Sci docs and the
    release note (FastAPI rule: QA status shown as a column, not a gate)."""
    if snapshot.get("release_decision") != "release":
        return False
    if docs_gate_items(snapshot):
        return False
    return True


def not_releasable_reason(snapshot: dict[str, Any]) -> str:
    decision = normalize_release_decision(snapshot.get("release_decision"))
    if qualifies_for_final(snapshot):
        return ""
    reasons = []
    if decision != "release":
        reasons.append("Release决策非发布")
    else:
        doc_items = docs_gate_items(snapshot)
        if doc_items:
            reasons.append("文档/发布信息未完成")
        qa_status = snapshot.get("qa_status", qa.QA_STATUS_DEFAULT)
        if qa_status == "not_checked":
            reasons.append("QA未测试")
        elif qa_status == "cannot_release":
            reasons.append("QA定为不可发布")
    return "；".join(reasons) if reasons else "未满足发布条件"
