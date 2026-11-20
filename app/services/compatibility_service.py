"""Trace each QA-report delivery to its own app_info; QA logs remain separate evidence."""
from __future__ import annotations

from app.domain import app_info, image_lookup, qa_results, test_commands
from app.domain.container_image import parse_base_image_reference
from app.services import qa_result_identity_service


def _failed_delivery(row, error):
    return {**row, "command": "", "path": "", "os": "", "arch": "", "chips": [],
            "perf_golden": "", "perf_unit": "",
            "python_label": "", "pytorch_label": "", "image_target": "", "app_info_commit_id": "",
            "dependencies": [], "scope_pending": True, "note": f"交付镜像解析失败：{error}"}


def _delivery_rows(row, image):
    if (image["app_id"], image["git_branch"]) != (row["app_id"], row["git_branch"]):
        raise ValueError("交付镜像对应的 App/分支与发布记录不匹配或无法唯一确定")
    fields = ("os", "arch", "python_label", "pytorch_label", "image_target")
    builds = [build for build in image["builds"]
              if all(not row.get(key) or row[key] == build[key] for key in fields)]
    if not builds:
        raise ValueError("交付镜像对应的实际构建与发布记录的构建配置不匹配")
    result = []
    for build in builds:
        if build["build"] in row.get("_disabled_builds", []):
            continue
        pattern = build["image_pattern"].partition(":")[2]
        parsed = parse_base_image_reference(row["delivery_reference"])
        tag = parsed["image"].rsplit("/", 1)[-1].partition(":")[2]
        if tag:
            if tag not in image["tags"] or not test_commands.image_tag_matches(pattern, tag):
                raise ValueError("交付镜像填写的 tag 不属于该 digest 的实际构建 tags")
        else:
            versions = {version for version in image["hpc_versions"]
                        if row["hpc_version"] in qa_results.hpc_families(version)}
            tag = test_commands.select_image_tag(pattern, image["tags"], versions)
        address, digest = image["image"].split("@", 1)
        reference = f"{address}:{tag}@{digest}"
        dependencies = list(dict.fromkeys([*(image["declared_dependencies"] or []),
                                          *app_info.python_pytorch_dependencies({}, platforms=[build])]))
        common = {**row, **build, "delivery_build_key": row.get("delivery_build_key", ""),
                  "app_version": image["app_version"], "app_info_commit_id": image["commit_id"],
                  "dependencies": dependencies, "scope_pending": image["declared_dependencies"] is None or not dependencies,
                  "delivery_reference": reference, "image": reference, "digest": digest,
                  "perf_golden": "", "perf_unit": "",
                  "note": ("根据历史配置生成（未在发布时封存）；" if row["manifest_source"] == "reconstructed" else "")
                          + "根据交付镜像 digest 实时生成"}
        commands = [command for command in image["test_commands"]
                    if command["image_pattern"] == build["image_pattern"]
                    and all(command[key] == build[key] for key in fields)
                    and command["test"] not in row.get("_excluded_tests", [])]
        for command in commands:
            result.append({**common, **command, "path": command["test"], "image": reference,
                           "command": test_commands.replace_command_image(command["command"], image["image"], reference)})
        if not commands:
            result.append({**common, "path": "", "command": "", "note": common["note"] + "；无对应启用测试命令"})
    return result


def _generate_deliveries(conn, rows, resolve_images, batch_size):
    groups, references, errors = {}, {}, {}
    for row in rows:
        reference = row.get("delivery_reference")
        if not reference:
            continue
        key = (row["app_id"], row["official_version_id"], row.get("delivery_build_key"), reference)
        groups.setdefault(key, row)
        try:
            references[reference] = image_lookup.parse_lookup_image(reference)["image"]
        except ValueError as exc:
            errors[reference] = str(exc)
    images = {}
    unique = list(dict.fromkeys(references.values()))
    # Reuse the public batch resolver, including its connection pool and deduplication.
    for start in range(0, len(unique), batch_size):
        resolved = resolve_images(conn, unique[start:start + batch_size])
        images.update((image["image"], image) for image in resolved["images"])
        errors.update((item["image"], item["error"]) for item in resolved["errors"])
    generated = []
    for row in rows:
        if not row.get("delivery_reference"):
            generated.append(row)
            continue
        key = (row["app_id"], row["official_version_id"], row.get("delivery_build_key"), row["delivery_reference"])
        if groups.pop(key, None) is None:
            continue
        reference = row["delivery_reference"]
        canonical = references.get(reference, reference)
        if error := errors.get(reference) or errors.get(canonical):
            generated.append(_failed_delivery(row, error))
            continue
        try:
            generated.extend(_delivery_rows(row, images[canonical]))
        except (ValueError, KeyError) as exc:
            generated.append(_failed_delivery(row, str(exc)))
    return generated


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
            # A QA log's digest can differ, but release/dbg still describe
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
        note = f'QA log：{data["hpc_version"] or "版本未记录"} · {status or "未填写 pass/fail"}'
        if status != "pass":
            note += f' · 原因：{data["qa_comment"] or "未填写"}'
        if row.get("delivery_reference") and data.get("qa_digest"):
            try:
                if image_lookup.parse_lookup_image(row["delivery_reference"])["image"] != image_lookup.parse_lookup_image(data["qa_digest"])["image"]:
                    note += "；note: 测试镜像与 QA report 交付镜像不同，仅作历史参考"
            except ValueError:
                pass
        notes.append(note)
    return notes or ["QA log：无匹配测试结果"]


def expand_rows(conn, rows, resolve_images, *, batch_size):
    """Generate each delivery independently, preserving templates for undelivered builds."""
    resolved = _generate_deliveries(conn, rows, resolve_images, batch_size)
    reports = _report_index(conn)
    expanded = []
    for pinned in resolved:
        pinned = {key: value for key, value in pinned.items() if not key.startswith("_")}
        # Existing platform/test relationships already restrict the product;
        # split only supported chips, preserving images without test commands.
        for chip in pinned.get("chips") or [""]:
            item = {**pinned, "chips": [chip] if chip else [], "GPU": chip}
            item["note"] = "；".join(filter(None, [item["note"], *_report_notes(item, reports)]))
            expanded.append(item)
    return expanded
