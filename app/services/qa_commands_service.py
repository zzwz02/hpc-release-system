"""Version-scoped QA worksheets from public Harbor tags and pinned Gerrit files."""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor

from app.api.errors import ApiError
from app.domain import qa_commands_table, qa_results, test_commands
from app.integrations import harbor
from app.integrations.read_session import read_session
from app.repositories import app_info_repo, apps_repo
from app.services import catalog_service, cycle_service, lookup_service
from app.timeutil import beijing_timestamp

# Release artifacts use this fixed public project; never query the CI database.
HARBOR_PROJECT = "hpc-release"
VERSION_PATTERN = r"\d+\.\d+\.\d+(?:\.\d+)?"


def _version(value):
    return tuple(int(part) for part in value.split("."))


def _scope(conn, release_id):
    apps = {app["id"]: app for app in apps_repo.list_apps(conn)}
    variants, scope = {}, {}
    for entry in cycle_service.list_entry_views(conn, release_id, include_withdrawn=False):
        app = apps.get(entry["app_id"])
        version = app_info_repo.get_version(conn, entry["locked_version_id"])
        if not app or not version:
            continue
        name, branch = catalog_service.app_name(app), app.get("git_branch", "")
        excluded = set(entry.get("excluded_tests") or [])
        scope[(app["id"], branch)] = excluded
        for command in test_commands.command_rows(version["raw"], excluded):
            key = (app["id"], command["image"])
            variant = variants.setdefault(key, {"app_id": app["id"], "name": name, "branch": branch,
                "version": version["raw"].get("app_version", ""), "commands": []})
            variant["commands"].append(command)
    return variants, scope


def count_scope_tests(conn, release_id):
    """Count the cycle's expanded test scope, including combinations without images."""
    variants, _ = _scope(conn, release_id)
    return sum(len(command["chips"]) or 1
               for variant in variants.values() for command in variant["commands"])


def export_csv(conn, *, release_id, hpc_version="latest", template=False):
    result = query(conn, release_id=release_id, hpc_version=hpc_version)
    if result["errors"]:
        details = "；".join(f'{item["image"]}：{item["error"]}' for item in result["errors"])
        raise ApiError(502, f"测试命令生成不完整，未导出 CSV，请重试。{details}")
    suffix = "_template" if template else ""
    filename = f'test_commands_{result["hpc_version"]}{suffix}.csv'
    return qa_commands_table.export_csv(result, template=template), filename


def query(conn, *, release_id, hpc_version):
    release = cycle_service.get_release(conn, release_id)
    requested = str(hpc_version or "").strip()
    latest = requested.lower() == "latest"
    if latest:
        cycle = re.search(r"(?<!\d)(\d+\.\d+\.\d+)(?:\.\d+)?(?!\d)", release["name"])
        if not cycle:
            raise ValueError("当前发布周期名称没有 HPC 版本号，请填写具体 hpc_version")
        family = cycle[1]
    elif re.fullmatch(VERSION_PATTERN, requested):
        family = requested
    else:
        raise ValueError("hpc_version 请填写具体版本（如 3.10.0.7）或 latest")
    variants, scope = _scope(conn, release_id)
    # app_info image generation already names the MACA release/dbg repositories.
    # Version worksheets query these exact repositories without compiler aliases.
    repositories = sorted({pattern.partition(":")[0] for _, pattern in variants})
    with read_session() as batch:
        with ThreadPoolExecutor(max_workers=lookup_service.LOOKUP_WORKERS) as pool:
            artifacts = dict(zip(repositories, pool.map(
                lambda repo: batch.run(harbor.list_artifacts, HARBOR_PROJECT, repo, family), repositories,
            ), strict=True))
        found = {}
        selected_versions = {}
        for key in variants:
            _, pattern = key
            name, _, tag_pattern = pattern.partition(":")
            matches = []
            for artifact in artifacts[name]:
                versions = {value for tag in artifact.get("tags") or []
                            if (value := test_commands.image_tag_version(tag_pattern, str(tag.get("name", ""))))
                            and re.fullmatch(VERSION_PATTERN, value)
                            and (value == family or latest and value.startswith(family + "."))}
                if not versions:
                    continue
                digest = artifact.get("digest", "")
                if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
                    raise ApiError(502, f"Harbor 返回无效镜像 digest：{name}")
                ref = f"{harbor.HARBOR_HOST}/{HARBOR_PROJECT}/{name}@{digest}"
                matches.append((ref, versions))
            found[key] = matches
            available = {version for _, versions in matches for version in versions}
            selected_versions[key] = max(available, key=_version, default="") if latest else requested
        references = list(dict.fromkeys(ref for key, matches in found.items()
                                        for ref, versions in matches if selected_versions[key] in versions))
        images, errors = [], []
        for start in range(0, len(references), lookup_service.MAX_BATCH_IMAGES):
            resolved = lookup_service.resolve_images(conn, references[start:start + lookup_service.MAX_BATCH_IMAGES])
            images.extend(resolved["images"])
            errors.extend(resolved["errors"])
    rows = []
    resolved_images = {image["image"]: image for image in images}
    for key, variant in variants.items():
        selected = selected_versions[key]
        references = [ref for ref, versions in found[key] if selected in versions]
        # An artifact that failed to resolve is reported as an error, never
        # misrepresented as an absent image or replaced with current commands.
        if references:
            for reference in references:
                image = resolved_images.get(reference)
                if image is None:
                    continue
                if selected not in image.get("hpc_versions", []):
                    errors.append({"image": reference, "error": "查询期间镜像的 HPC 版本 tag 已变化，请重新查询"})
                    continue
                source = (image["app_id"], image["git_branch"])
                if source != (variant["app_id"], variant["branch"]):
                    errors.append({"image": reference, "error": "镜像对应的 App/分支与当前发布周期测试范围不匹配"})
                    continue
                # One digest may carry tags for several platforms or patches.
                # Keep only this build's commands and its independently chosen version.
                commands = [command for command in image["test_commands"] if command["image_pattern"] == key[1]]
                if not commands:
                    errors.append({"image": reference, "error": "镜像历史 app_info 与当前报名构建不匹配"})
                    continue
                try:
                    generated = qa_results.worksheet_commands({**image, "test_commands": commands}, hpc_version=selected)
                except ValueError as exc:
                    errors.append({"image": reference, "error": str(exc)})
                    continue
                rows.extend(row for row in generated if row["test_name"] not in scope[source])
            continue
        missing_image = {"app_id": variant["app_id"], "git_branch": variant["branch"],
                         "app_version": variant["version"], "commit_id": "", "test_commands": variant["commands"]}
        rows.extend(qa_results.worksheet_commands(missing_image, hpc_version=selected))
    rows.sort(key=lambda row: tuple(str(row[column]).casefold() for column in (
        "app_id", "git_branch", "app_version", "arch", "OS", "GPU", "py_label", "torch_label", "test_name", "hpc_version", "website_docker_cmd")))
    errors = list({(item["image"], item["error"]): item for item in errors}.values())
    return {"release_name": release["name"], "hpc_version": "latest" if latest else requested,
            "hpc_versions": sorted(set(selected_versions.values()) - {""}, key=_version), "requested_version": requested,
            "columns": qa_results.WORKSHEET_COLUMNS, "rows": rows, "errors": errors,
            "generated_at": beijing_timestamp()}
