"""Present frozen QA-log commands per GPU, with separate QA-report evidence."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from app.api.errors import ApiError
from app.domain import image_lookup, qa_results
from app.domain.container_image import parse_base_image_reference
from app.domain.test_commands import image_tag_version, replace_command_image, select_image_tag
from app.integrations import harbor
from app.integrations.read_session import read_session
from app.repositories import qa_results_repo

# Legacy deliveries need only Harbor tags, with bounded, request-local reuse.
TAG_LOOKUP_WORKERS = 16


def _tags(reference):
    try:
        image = image_lookup.parse_lookup_image(reference)
        if image["host"] != harbor.HARBOR_HOST:
            raise ValueError(f"仅支持 {harbor.HARBOR_HOST} 的镜像")
        artifact = harbor.get_artifact(image["project"], image["repository"], image["digest"])
        return [tag["name"] for tag in artifact.get("tags") or []
                if isinstance(tag, dict) and isinstance(tag.get("name"), str)]
    except (ValueError, RuntimeError, ApiError) as exc:
        return exc


def _pin_delivery(row, tags):
    reference = row.get("delivery_reference", "")
    if not reference or ":" in reference.split("@", 1)[0].rsplit("/", 1)[-1]:
        return row
    try:
        pattern = row.get("image_pattern", "").partition(":")[2]
        if not pattern:
            raise ValueError("缺少构建配置，无法确定实际 tag")
        actual = tags[reference]
        if isinstance(actual, Exception):
            raise actual
        # Prefer release aliases in this cycle. A commit alias is also valid
        # when the old artifact has no matching release alias; never invent one.
        versions = {version for tag in actual if (version := image_tag_version(pattern, tag))
                    and row["hpc_version"] in qa_results.hpc_families(version)}
        tag = select_image_tag(pattern, actual, versions)
        address, digest = reference.split("@", 1)
        tagged = f"{address}:{tag}@{digest}"
        result = {**row, "delivery_reference": tagged, "image": tagged}
        if row.get("command"):
            result["command"] = replace_command_image(row["command"], reference, tagged)
        return result
    except (ValueError, RuntimeError, ApiError) as exc:
        return {**row, "note": f'{row["note"]}；交付镜像 tag 未能补全：{exc}'.strip("；")}


def _identity(row):
    return (qa_results.result_key(row), str(row.get("py_label") or "").strip(),
            str(row.get("torch_label") or "").strip())


def _report_index(conn):
    index = {}
    for record in qa_results_repo.list_results(conn):
        data = qa_results.canonical_result(record["data"])
        scopes = [("release", record["release_id"])] if record["release_id"] else [
            ("family", family) for family in qa_results.hpc_families(data["hpc_version"])]
        for scope in scopes:
            index.setdefault((scope, _identity(data)), []).append(data)
    return index


def _report_notes(row, index):
    if not row.get("command"):
        return []
    identity = _identity({"app_name": row["app_name"], "git_branch": row["git_branch"],
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
        status = str(data["qa_status"] or "").strip()
        if not status and str(data["qa_result"]).strip().lower() in {"pass", "fail"}:
            status = str(data["qa_result"]).strip().lower()
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
    references = list(dict.fromkeys(row["delivery_reference"] for row in rows
                      if row.get("delivery_reference") and row.get("image_pattern")
                      and ":" not in row["delivery_reference"].split("@", 1)[0].rsplit("/", 1)[-1]))
    with read_session() as batch, ThreadPoolExecutor(max_workers=TAG_LOOKUP_WORKERS) as pool:
        tags = dict(zip(references, pool.map(lambda ref: batch.run(_tags, ref), references), strict=True))
    reports = _report_index(conn)
    expanded = []
    for row in rows:
        pinned = _pin_delivery(row, tags)
        # Existing platform/test relationships already restrict the product;
        # split only supported chips, preserving images without test commands.
        for chip in row.get("chips") or [""]:
            item = {**pinned, "chips": [chip] if chip else [], "GPU": chip}
            item["note"] = "；".join(filter(None, [item["note"], *_report_notes(item, reports)]))
            expanded.append(item)
    return expanded
