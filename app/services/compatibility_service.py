"""Present frozen QA-log commands per GPU, with separate QA-report evidence."""
from __future__ import annotations

from app.domain import image_lookup, qa_results
from app.domain.container_image import parse_base_image_reference
from app.services import delivery_reference_service, qa_result_identity_service

def _identity(row):
    return (qa_results.result_key(row), str(row.get("py_label") or "").strip(),
            str(row.get("torch_label") or "").strip())


def _report_index(conn):
    index = {}
    for record in qa_result_identity_service.list_records(conn):
        data = qa_results.canonical_result(record["data"])
        if not data["app_id"]:
            continue
        scopes = [("release", record["release_id"])] if record["release_id"] else [
            ("family", family) for family in qa_results.hpc_families(data["hpc_version"])]
        for scope in scopes:
            index.setdefault((scope, _identity(data)), []).append(data)
    return index


def _report_notes(row, index):
    if not row.get("command"):
        return []
    identity = _identity({"app_id": row["app_id"], "git_branch": row["git_branch"],
                          "app_version": row["app_version"], "arch": row["arch"], "OS": row["os"],
                          "GPU": row["GPU"], "test_name": row["path"],
                          "py_label": row.get("python_label", ""), "torch_label": row.get("pytorch_label", "")})
    matches = {}
    for scope in [("release", row["release_id"]), ("family", row["hpc_version"])]:
        for data in index.get((scope, identity), []):
            # A QA report's digest can differ, but release/dbg still describe
            # different builds. Its repository suffix identifies that target.
            if "@" in str(data.get("qa_digest") or ""):
                try:
                    parsed = parse_base_image_reference(data["qa_digest"])
                    target = "dbg" if parsed["image"].split(":", 1)[0].endswith("-dbg") else "release"
                    if target != row.get("image_target"):
                        continue
                except ValueError:
                    pass  # Legacy bare digests carry no target information.
            matches.setdefault(str(data["hpc_version"]), data)
    notes = []
    for data in sorted(matches.values(), key=lambda item: qa_results.report_sort_key(item, "hpc_version")):
        status = qa_results.result_status(data)
        note = f'QA report：{data["hpc_version"] or "版本未记录"} · {status or "未填写 pass/fail"}'
        if status != "pass":
            note += f' · 原因：{data["qa_comment"] or "未填写"}'
        if row.get("delivery_reference") and data.get("qa_digest"):
            try:
                if image_lookup.parse_lookup_image(row["delivery_reference"])["image"] != image_lookup.parse_lookup_image(data["qa_digest"])["image"]:
                    note += "；note: 测试镜像与 QA log 交付镜像不同，仅作历史参考"
            except ValueError:
                pass
        notes.append(note)
    return notes or ["QA report：无匹配测试结果"]


def expand_rows(conn, rows):
    """Never regenerate frozen scripts or use a report's image in a command."""
    resolved = delivery_reference_service.resolve_references(rows)
    reports = _report_index(conn)
    expanded = []
    for pinned in resolved:
        if pinned.get("reference_error"):
            pinned = {**pinned, "note": "；".join(filter(None, [pinned["note"],
                      "交付镜像 tag 未能补全：" + pinned["reference_error"]]))}
        # Existing platform/test relationships already restrict the product;
        # split only supported chips, preserving images without test commands.
        for chip in pinned.get("chips") or [""]:
            item = {**pinned, "chips": [chip] if chip else [], "GPU": chip}
            item["note"] = "；".join(filter(None, [item["note"], *_report_notes(item, reports)]))
            expanded.append(item)
    return expanded
