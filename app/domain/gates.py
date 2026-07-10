"""Release readiness gates — missing items and final/docs qualification.

Ported from core.py:2042-2133 (missing items) and 3109-3139, 3388-3410 (gates).
Pure functions: no HTTP, no DB, unit-testable in isolation.
"""
from __future__ import annotations

from typing import Any

from app.domain.decisions import DOC_TARGETS, normalize_release_decision
from app.domain.snapshots import (
    MAX_APP_DESCRIPTION_CHARS,
    app_description_count,
    normalize_doc_target,
)


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


def missing_items_for(app: dict[str, Any], snapshot: dict[str, Any]) -> list[dict[str, str]]:
    """Readiness and final-release gate items shown to RM/owners.

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

    if not snapshot.get("owners"):
        add_doc("缺少 owner")
    if not app.get("git_url"):
        add_doc("缺少 Gerrit URL")
    if not app.get("git_branch"):
        add_doc("缺少 branch")
    if not (snapshot.get("official_name") or "").strip():
        add_doc("缺少官方名称")
    if not (snapshot.get("type") or "").strip():
        add_doc("缺少 App类型")
    description = (snapshot.get("description") or "").strip()
    if not description:
        add_doc("缺少描述（30字内）")
    elif app_description_count(description) > MAX_APP_DESCRIPTION_CHARS:
        add_doc("描述超过30字")
    if not snapshot.get("app_info"):
        add_doc("缺少可追溯 AppInfoSnapshot")
    if not snapshot.get("version"):
        add_doc("缺少 对应官方版本")
    if not snapshot.get("x86_chips"):
        add_doc("缺少 X86支持芯片系列")
    if normalize_doc_target(snapshot.get("doc_target")) in DOC_TARGETS:
        doc = snapshot.get("doc", {})
        required = {
            "intro": "基本介绍",
            "image_usage": "镜像使用方法",
            "binary_usage": "二进制包使用方法",
            "env_setup": "环境搭建",
        }
        for key, label in required.items():
            if not doc.get(key):
                add_doc(f"缺少{label}")
    for doc in snapshot.get("test_docs", []):
        if doc.get("obsolete"):
            continue
        if doc.get("owner_added") and not doc.get("command"):
            add_doc(f"{doc['path']} 缺少 owner-added 测试命令")
        for key, label in {"dataset": "测试数据集", "content": "测试内容", "result_view": "结果查看方式", "pass_criteria": "通过标准"}.items():
            if not doc.get(key):
                add_doc(f"{doc['path']} 缺少{label}")
    if not snapshot.get("owner_confirmed"):
        add_doc("Owner 未确认 doc")
    qa_status = snapshot.get("qa_status", "not_checked")
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
    if not snapshot.get("owner_confirmed"):
        return False
    if docs_gate_items(snapshot):
        return False
    if snapshot.get("qa_status") in {"qa_passed", "has_issues"}:
        return True
    return False


def qualifies_for_docs(snapshot: dict[str, Any]) -> bool:
    """True if this snapshot should be included in HPC/AI4Sci docs and the
    release note (FastAPI rule: QA status shown as a column, not a gate)."""
    if snapshot.get("release_decision") != "release":
        return False
    if not snapshot.get("owner_confirmed"):
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
        doc_items = [
            item
            for item in docs_gate_items(snapshot)
            if missing_item_text(item) != "Owner 未确认 doc"
        ]
        if doc_items:
            reasons.append("文档/发布信息未完成")
        if not snapshot.get("owner_confirmed"):
            reasons.append("Owner未确认")
        qa_status = snapshot.get("qa_status", "not_checked")
        if qa_status == "not_checked":
            reasons.append("QA未测试")
        elif qa_status == "cannot_release":
            reasons.append("QA定为不可发布")
    return "；".join(reasons) if reasons else "未满足发布条件"
