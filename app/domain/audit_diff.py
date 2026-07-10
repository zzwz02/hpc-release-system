"""Audit diff helpers — ported from core.py:807-853.

Pure functions: no HTTP, no DB, unit-testable in isolation.
"""
from __future__ import annotations

from typing import Any


def fmt_audit_value(v: Any) -> str:
    """Render an audit old/new value as a display string."""
    if v is None:
        return ""
    if isinstance(v, (list, tuple)):
        return ", ".join(str(x) for x in v)
    if isinstance(v, bool):
        return "是" if v else "否"
    return str(v)


def field_diff(before: dict[str, Any], after: dict[str, Any], labels: dict[str, str]) -> list[dict[str, str]]:
    """Return [{field,label,old,new}] for keys in *labels* whose value changed."""
    changes: list[dict[str, str]] = []
    for key, label in labels.items():
        old = before.get(key)
        new = after.get(key)
        if old == new:
            continue
        changes.append({"field": key, "label": label, "old": fmt_audit_value(old), "new": fmt_audit_value(new)})
    return changes


TEST_DOC_FIELD_LABELS = {
    "command": "命令",
    "dataset": "测试数据集",
    "content": "测试内容",
    "result_view": "结果查看",
    "pass_criteria": "通过标准",
}


def test_docs_diff(before: list[dict[str, Any]], after: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Return field-level [{field,label,old,new}] entries between two test-doc lists."""
    by_id = {d.get("id"): d for d in before}
    changes: list[dict[str, str]] = []
    for doc in after:
        path = doc.get("path") or doc.get("id") or "test"
        old = by_id.get(doc.get("id"))
        if old is None:
            changes.append({"field": str(doc.get("id")), "label": f"{path}（新增测试项）", "old": "", "new": "已添加"})
            continue
        for key, label in TEST_DOC_FIELD_LABELS.items():
            if (old.get(key) or "") != (doc.get(key) or ""):
                changes.append({"field": f"{path}.{key}", "label": f"{path} · {label}",
                                "old": fmt_audit_value(old.get(key)), "new": fmt_audit_value(doc.get(key))})
    return changes
