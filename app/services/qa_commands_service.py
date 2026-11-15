"""Version-scoped QA worksheets from public Harbor tags and pinned Gerrit files."""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor

from app.api.errors import ApiError
from app.domain import qa_results, test_commands
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
        scope[(name, branch)] = excluded
        for command in test_commands.command_rows(version["raw"], excluded):
            key = (app["id"], command["image"])
            variant = variants.setdefault(key, {"name": name, "branch": branch,
                "version": version["raw"].get("app_version", ""), "commands": []})
            variant["commands"].append(command)
    return variants, scope


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
        available = set()
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
                available.update(versions)
            found[key] = matches
        if latest:
            if not available:
                raise ValueError(f"Harbor 中没有 {family} 系列可用镜像，请填写具体 hpc_version 查询缺失项")
            selected = max(available, key=_version)
        else:
            selected = requested
        references = list(dict.fromkeys(ref for matches in found.values() for ref, versions in matches if selected in versions))
        images, errors = [], []
        for start in range(0, len(references), lookup_service.MAX_BATCH_IMAGES):
            resolved = lookup_service.resolve_images(conn, references[start:start + lookup_service.MAX_BATCH_IMAGES])
            images.extend(resolved["images"])
            errors.extend(resolved["errors"])
    rows = []
    for image in images:
        if selected not in image.get("hpc_versions", []):
            errors.append({"image": image["image"], "error": "查询期间镜像的 HPC 版本 tag 已变化，请重新查询"})
            continue
        source = (image["app_name"], image["git_branch"])
        if source not in scope:
            errors.append({"image": image["image"], "error": "镜像对应的 App/分支与当前发布周期测试范围不匹配"})
            continue
        rows.extend(row for row in qa_results.worksheet_commands(image, hpc_version=selected)
                    if row["test_name"] not in scope[source])
    for key, variant in variants.items():
        # An artifact that failed to resolve is reported as an error, never
        # misrepresented as an absent image or replaced with current commands.
        if any(selected in versions for _, versions in found[key]):
            continue
        no_image = {"app_name": variant["name"], "git_branch": variant["branch"],
                    "app_version": variant["version"], "commit_id": "", "test_commands": variant["commands"]}
        for row in qa_results.worksheet_commands(no_image, hpc_version=selected):
            row["website_docker_cmd"] = "no_image"
            rows.append(row)
    rows.sort(key=lambda row: tuple(str(row[column]).casefold() for column in (
        "app_name", "git_branch", "app_version", "arch", "OS", "GPU", "py_label", "torch_label", "test_name", "website_docker_cmd")))
    return {"release_name": release["name"], "hpc_version": selected, "requested_version": requested,
            "columns": qa_results.WORKSHEET_COLUMNS, "rows": rows, "errors": errors,
            "generated_at": beijing_timestamp()}
