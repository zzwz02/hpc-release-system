"""Rehearse the legacy release DB cutover without ever writing the source.

Run from the repository root: python -m tools.cutover_release --help.
Only the public Harbor/Gerrit/manifest archive read chain is used. No server,
background runner, notification or CI database is started by this tool.
"""
from __future__ import annotations

import argparse
import base64
import csv
import io
import json
import os
import re
import shutil
import sqlite3
import sys
import tempfile
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from copy import copy
from datetime import datetime
from pathlib import Path

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db import connection
from app.domain import delivery_images, qa_results, test_commands
from app.integrations import harbor
from app.integrations.read_session import read_session
from app.repositories import app_info_repo, apps_repo, entries_repo, official_repo, releases_repo
from app.repositories.audit_repo import log_audit
from app.services import delivery_validation_service, lookup_service, qa_results_service
from app.timeutil import BEIJING_TZ, beijing_timestamp

DEFAULT_CAPS = {"3.9.0": "3.9.0.29", "3.10.1": "3.10.1.11"}
PROJECT = "hpc-release"
DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
VERSION = re.compile(r"\d+\.\d+\.\d+(?:\.\d+)?")
EXTRA_COLUMNS = ["source_file", "source_sheet", "source_row", "source_case", "source_hpc_version",
                 "source_branch", "source_result_header", "source_qa_result", "source_qa_comment", "migration_note"]
IMAGE_COLUMNS = ["release", "purpose", "app_id", "build", "OS", "arch", "py_label", "torch_label",
                 "image_target", "hpc_version", "image", "digest", "commit_id", "reference", "status", "note"]
REVIEW_KINDS = {"unmatched_result", "ambiguous_result", "metric_mismatch", "duplicate_result",
                "image_provenance", "image_identity", "missing_app_info", "empty_qa_log"}


def text(value):
    return "" if value is None else str(value).strip()


def metric_text(value):
    """Legacy Jenkins reports render an absent metric field as 'undefined'."""
    return "" if text(value).casefold() in {"undefined", "null", "none"} else text(value)


def version_key(value):
    if not VERSION.fullmatch(value):
        raise ValueError(f"无效 HPC 版本：{value}")
    return tuple(map(int, value.split(".")))


def locked_date(value):
    stamp = datetime.fromisoformat(text(value).replace("Z", "+00:00"))
    if stamp.tzinfo is not None:
        stamp = stamp.astimezone(BEIJING_TZ)
    return stamp.date().isoformat()


def readonly(path):
    path = Path(path).resolve(strict=True)
    if path.stat().st_size < 100:
        raise ValueError(f"源数据库为空或不完整：{path}")
    conn = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    return conn


def tables(conn):
    return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def snapshot_source(source, destination):
    """SQLite backup includes committed WAL contents; the source stays read-only."""
    with closing(readonly(source)) as src:
        if "snapshots" not in tables(src):
            raise ValueError("源库必须是切换前业务库（包含 snapshots）；不能拿演练输出再次迁移")
        if src.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise ValueError("源库完整性检查失败")
        pending = src.execute("SELECT id FROM cicd_task_requests WHERE status='pending' "
                              "OR delivery_status IN ('pending','returned')").fetchall()
        if pending:
            raise ValueError("仍有未完成 CICD 申请：" + ", ".join(str(r[0]) for r in pending))
        dst = sqlite3.connect(destination)
        try:
            src.backup(dst)
        finally:
            dst.close()


def migrate(database, caps):
    """Use the application migration, then apply cutover-specific date/history rules."""
    with closing(readonly(database)) as old:
        releases = [dict(r) for r in old.execute("SELECT id,name,released_locked,released_locked_at FROM releases")]
        for name in caps:
            matches = [r for r in releases if r["name"] == name and r["released_locked"]]
            if len(matches) != 1:
                raise ValueError(f"必须存在唯一已锁定周期 {name}")
        usernames = {r[0] for r in old.execute("SELECT username FROM users")}
        # Count preserved business tables without reading credentials or token contents.
        counts = {name: old.execute(f'SELECT count(*) FROM "{name}"').fetchone()[0]
                  for name in tables(old) - {"snapshots", "sqlite_sequence", "audit", "qa_ai_suggestions"}}
        summaries = {}
        for r in releases:
            if r["name"] not in caps:
                continue
            row = old.execute("SELECT filename,content FROM qa_logs WHERE release_id=?", (r["id"],)).fetchone()
            if row is None or not row["content"]:
                raise ValueError(f'{r["name"]} 缺少数据库内的原始 QA Excel')
            summaries[r["name"]] = dict(row)
    conn = connection.connect(database)
    # init_db is also used for new installations. A production conversion must
    # not introduce its missing built-in development accounts.
    for row in conn.execute("SELECT username FROM users").fetchall():
        if row[0] not in usernames:
            conn.execute("DELETE FROM users WHERE username=?", (row[0],))
    for release in releases:
        if release["released_locked"]:
            if not release["released_locked_at"]:
                raise ValueError(f'{release["name"]} 已锁定但没有锁定时间')
            conn.execute("UPDATE releases SET release_date=? WHERE id=?",
                         (locked_date(release["released_locked_at"]), release["id"]))
    # Retain every successful historical publication, including older cycles.
    # Leave the manifest NULL: historical reconstruction is not a release-time seal.
    for release in releases:
        if not release["released_locked"]:
            continue
        for entry in entries_repo.list_for_release(conn, release["id"]):
            if entry["result"] != "released":
                continue
            if conn.execute("SELECT 1 FROM official_versions WHERE app_id=? AND release_id=?",
                            (entry["app_id"], release["id"])).fetchone():
                continue
            official_repo.insert_official(conn, {"app_id": entry["app_id"], "release_id": release["id"],
                "entry_id": entry["id"], "version": entry["target_version"],
                "app_info_version_id": entry["locked_version_id"], "released_at": release["released_locked_at"]})
    for table in ("component_compatibility_apps", "component_compatibility_reports"):
        if conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]:
            raise ValueError("旧业务库转换后不应存在兼容性测试结果，拒绝混入演练数据")
    conn.commit()
    for table, count in counts.items():
        if conn.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0] != count:
            raise ValueError(f"迁移改变了应保留表 {table} 的记录数")
    return conn, releases, summaries, counts


def summary_rows(content, family):
    """Read both performance and functional blocks, retaining source coordinates."""
    book = load_workbook(io.BytesIO(content), data_only=True)
    result = []
    for sheet in book:
        environment = {text(row[0]).casefold(): text(row[1]) for row in sheet.iter_rows(
            min_row=1, max_row=min(15, sheet.max_row), max_col=2, values_only=True) if row[0]}
        hpc = environment.get("hpc version", "")
        cpu = environment.get("cpuname", "").casefold()
        arch = "arm" if re.search(r"aarch64|arm|kunpeng|鲲鹏|飞腾", cpu) else (
            "x86" if re.search(r"intel|xeon|hygon|amd|海光", cpu) else "")
        headers = None
        for number, values in enumerate(sheet.iter_rows(values_only=True), 1):
            a, b = (text(v) for v in (*values[:2], "", "")[:2])
            # Some historical sheets accidentally label the functional header Notes/Notes.
            is_header = a.casefold() == "case name" or (a == b == "Notes" and any(
                re.search(r"\d+\.\d+\.", text(v)) for v in values[2:4]))
            if is_header:
                metric = next((i for i, v in enumerate(values) if text(v).casefold() == "metric golden"), None)
                value_col = 4 if metric is not None else 2
                label = text(values[value_col])
                # The original 3.10.1 sheet uses the family heading 3.10.x.x.
                if not re.search(r"\d+\.\d+\.[\dx]", label):
                    raise ValueError(f"{family}/{sheet.title}!{number} 结果列版本不符：{label}")
                headers = (value_col, next((i for i, v in enumerate(values)
                                           if text(v).casefold() == "notes" and i >= 3), None), metric, label)
                continue
            if headers is None or not a or a.casefold() in {"max", "min", "notes", "functional test"}:
                continue
            value_col, note_col, metric, label = headers
            value = values[value_col] if value_col < len(values) else None
            comment = text(values[note_col]) if note_col is not None and note_col < len(values) else ""
            if not b and value is None and not comment:
                continue
            result.append({"source_sheet": sheet.title, "source_row": number, "source_case": a,
                "source_branch": b, "source_hpc_version": hpc, "GPU": sheet.title.upper(),
                "source_result_header": label,
                "host_arch": arch, "qa_result": "" if value is None else value, "qa_comment": comment,
                "source_perf_golden": metric_text(values[metric]) if metric is not None else "",
                "source_perf_unit": metric_text(values[metric + 1]) if metric is not None else ""})
    book.close()
    return result


def choose_artifact(build, artifacts, cap):
    """Exact complete build tag; numeric version and build number, never lexicographic."""
    pattern = build["image_pattern"].partition(":")[2]
    family = ".".join(cap.split(".")[:3])
    matches = []
    for artifact in artifacts:
        for tag in artifact.get("tags") or []:
            name = text(tag.get("name"))
            value = test_commands.image_tag_version(pattern, name)
            if not value or not VERSION.fullmatch(value) or qa_results.hpc_families(value) != {family}:
                continue
            if version_key(value) > version_key(cap):
                continue
            if not DIGEST.fullmatch(text(artifact.get("digest"))):
                raise ValueError("Harbor 返回非法 digest")
            base = pattern.replace("<hpc_version>", value)
            suffix = name[len(base):]
            build_number = int(suffix[1:]) if re.fullmatch(r"-\d+", suffix) else -1
            matches.append(((version_key(value), build_number, text(artifact.get("push_time"))), value, artifact))
    if not matches:
        return None
    rank = max(item[0] for item in matches)
    top = [item for item in matches if item[0] == rank]
    if len({item[2]["digest"] for item in top}) != 1:
        raise ValueError("同版本和构建号存在多个 digest，无法唯一选择最新镜像")
    _, value, artifact = top[0]
    tag = test_commands.select_image_tag(pattern, [t["name"] for t in artifact.get("tags") or []], {value})
    repo = build["image_pattern"].partition(":")[0]
    image = f"{harbor.HARBOR_HOST}/{PROJECT}/{repo}:{tag}"
    return {**build, "image": image, "digest": artifact["digest"], "reference": image + "@" + artifact["digest"],
            "hpc_version": value, "commit_id": ""}


def case_keys(name):
    # Jenkins summary joins application/test paths with underscores. Normalizing
    # separators is safe only if the resulting candidate is still unique.
    return re.sub(r"[^a-z0-9]", "", str(name).casefold())


def status_for(result, comment):
    value = text(result).casefold()
    combined = value + " " + comment.casefold()
    if re.search(r"已修复|已恢复|不再失败", combined):
        return ""  # Mixed historical wording needs review; never guess a verdict.
    if re.search(r"\bfail(?:ed)?\b|测试失败|性能回[退归]|概率性失败|不通过", combined):
        return "fail"
    if re.search(r"\bpass(?:ed)?\b|测试pass|测试通过", value):
        return "pass"
    return ""  # A measured number is not itself QA's pass verdict.


def report_table(sheet, columns, rows):
    sheet.append(columns)
    for row in rows:
        sheet.append([row.get(column, "") for column in columns])
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = sheet.dimensions
    for cell in sheet[1]:
        cell.font = Font(color="FFFFFF", bold=True)
        cell.fill = PatternFill("solid", fgColor="193958")
    for row in sheet.iter_rows(min_row=2):
        for cell in row:
            if isinstance(cell.value, str):
                cell.data_type = "s"  # Treat source text as data, never a formula.
            cell.alignment = Alignment(vertical="top", wrap_text=True)
    for column in sheet.columns:
        title = text(column[0].value)
        sheet.column_dimensions[column[0].column_letter].width = 65 if any(
            key in title for key in ("command", "docker_cmd", "digest", "note", "comment", "reference")) else 22


def qa_workbook(original, rows):
    # Loading and saving the original book keeps environment pages, formatting,
    # merged cells and formulas; no environment schema is imposed.
    book = load_workbook(io.BytesIO(original))
    title = "QA log"
    while title in book.sheetnames:
        title += "_"
    sheet = book.create_sheet(title, 0)
    report_table(sheet, [*qa_results.WORKSHEET_COLUMNS, *EXTRA_COLUMNS], rows)
    output = io.BytesIO()
    book.save(output)
    book.close()
    return output.getvalue()


def issue(issues, release, kind, message, **details):
    issues.append({"release": release, "kind": kind, "message": message, **details})


def lammps_version_label(record):
    """Legacy LAMMPS Branch cells contain an App version, not the manifest branch."""
    branch = record["source_branch"]
    return branch if (case_keys(record["source_case"]).startswith("lammps")
                      and re.fullmatch(r"(?:patch|stable)_.+", branch)) else ""


def historical_qa_profile(app, raw, records):
    aliases = {case_keys(alias) for test in raw.get("app_test", {})
               for alias in (test, text(raw.get("app_name")) + "_" + test, app["id"] + "_" + test)}
    for record in records:
        if case_keys(record["source_case"]) not in aliases:
            continue
        version = lammps_version_label(record)
        if version:
            if (raw.get("app_name") == "lammps" and text(raw.get("app_version")) == version
                    and app["git_branch"] == "master"):
                return True
        elif app["git_branch"] == record["source_branch"]:
            return True
    return False


def plan_images(conn, releases, summaries, caps, issues):
    """Delivery scope is signup-only; historical QA may include an unregistered App."""
    apps = {a["id"]: a for a in apps_repo.list_apps(conn)}
    jobs, source_rows = [], {}
    for release in releases:
        family = release["name"]
        if family not in caps:
            continue
        source_rows[family] = summary_rows(summaries[family]["content"], family)
        for sheet, label in sorted({(r["source_sheet"], r["source_result_header"]) for r in source_rows[family]}):
            if family not in label:
                issue(issues, family, "legacy_column_label", f"原表结果列标题仍为 {label}；按环境区 HPC Version 追溯", source_sheet=sheet)
        test_caps = {row["source_hpc_version"] for row in source_rows[family]}
        for cap in test_caps:
            if not VERSION.fullmatch(cap) or qa_results.hpc_families(cap) != {family}:
                raise ValueError(f"{family} 原表 HPC Version 无效或不属于该周期：{cap!r}")
        entries = entries_repo.list_for_release(conn, release["id"])
        profiles = []
        for entry in entries:
            version = app_info_repo.get_version(conn, entry["locked_version_id"])
            if not version:
                issue(issues, family, "missing_app_info", "报名没有有效 app_info", app_id=entry["app_id"])
                continue
            profiles.append((apps[entry["app_id"]], version, entry))
        signed_up = {entry["app_id"] for entry in entries}
        # Do not introduce another App with the same display/test name when the
        # original row already identifies a signed-up profile. Version-labelled
        # LAMMPS rows only match the profile for that exact App version.
        historical_records = [record for record in source_rows[family] if not any(
            historical_qa_profile(app, version["raw"], [record]) for app, version, _ in profiles)]
        for app in apps.values():
            if app["id"] in signed_up:
                continue
            version = app_info_repo.latest_version(conn, app["id"])
            if version and historical_qa_profile(app, version["raw"], historical_records):
                profiles.append((app, version, None))
        for app, version, entry in profiles:
            purposes = [("qa", c) for c in sorted(test_caps)]
            if entry is not None:
                purposes.insert(0, ("delivery", caps[family]))
            for build in delivery_images.builds(version["raw"]):
                for purpose, cap in purposes:
                    jobs.append({"release": family, "release_id": release["id"], "entry": entry,
                        "app": app, "build": build, "purpose": purpose, "cap": cap})
    return jobs, source_rows


def collect_images(conn, releases, summaries, caps, issues):
    jobs, source_rows = plan_images(conn, releases, summaries, caps, issues)
    keys = sorted({(j["release"], j["build"]["image_pattern"].partition(":")[0]) for j in jobs})
    print(f"读取 {len(keys)} 个周期/镜像仓库，匹配 {len(jobs)} 个构建/用途", flush=True)
    with read_session() as batch:
        def listing(key):
            family, repo = key
            return harbor.list_artifacts(PROJECT, repo, family)
        with ThreadPoolExecutor(max_workers=lookup_service.LOOKUP_WORKERS) as pool:
            values = list(pool.map(lambda key: batch.run(listing, key), keys))
        listings = dict(zip(keys, values, strict=True))
        chosen = []
        for job in jobs:
            repo = job["build"]["image_pattern"].partition(":")[0]
            selected = choose_artifact(job["build"], listings[(job["release"], repo)], job["cap"])
            if selected is None:
                if job["purpose"] == "delivery":
                    issue(issues, job["release"], "missing_image", f'没有 <= {job["cap"]} 的匹配 MACA 镜像',
                          app_id=job["app"]["id"], build=job["build"]["build"], target=job["build"]["image_target"])
                continue
            chosen.append({**job, "selected": selected})
        refs = sorted({qa_results.result_reference({"qa_digest": j["selected"]["reference"]}) for j in chosen})
        resolved = {}
        print(f"从公共链路追溯 {len(refs)} 个 digest 的 commit/app_info", flush=True)
        for start in range(0, len(refs), lookup_service.MAX_BATCH_IMAGES):
            result = lookup_service.resolve_images(conn, refs[start:start + lookup_service.MAX_BATCH_IMAGES])
            resolved.update({image["image"]: image for image in result["images"]})
            for error in result["errors"]:
                issue(issues, "", "image_provenance", error["error"], reference=error["image"])
    valid, artifacts = [], {}
    for job in chosen:
        selected = job["selected"]
        ref = qa_results.result_reference({"qa_digest": selected["reference"]})
        source = resolved.get(ref)
        if not source:
            continue
        if (source["app_id"], source["git_branch"]) != (job["app"]["id"], job["app"]["git_branch"]):
            issue(issues, job["release"], "image_identity", "镜像追溯的 App/分支不匹配", app_id=job["app"]["id"], reference=ref)
            continue
        selected["commit_id"] = source["commit_id"]
        repo = job["build"]["image_pattern"].partition(":")[0]
        # Reuse this run's live listing for the exact same validation as QA save.
        artifacts.update({(PROJECT, repo, a["digest"]): a for a in listings[(job["release"], repo)]})
        delivery_validation_service.validate([selected], artifacts=artifacts)
        valid.append({**job, "source": source})
    return valid, source_rows


def prefer_builds(candidates):
    if len(candidates) <= 1:
        return candidates
    if len({(c.get("app_id"), c.get("app_version")) for c in candidates}) > 1:
        return candidates  # An OS preference cannot decide App identity/version.
    kept = [c for c in candidates if not (
        test_commands.denormalize_arch(c["arch"]) == "x86" and "kylin" in c["OS"].lower())]
    return [c for c in kept if c["OS"].lower().startswith("ubuntu22")] or kept


def match_results(jobs, source_rows, summaries, issues, overrides, skipped=None):
    skipped = [] if skipped is None else skipped
    output = {}
    for family, records in source_rows.items():
        candidates = []
        for job in jobs:
            if job["release"] != family or job["purpose"] != "qa":
                continue
            src, selected = job["source"], job["selected"]
            excluded = set(job["entry"]["excluded_tests"]) if job["entry"] is not None else set()
            commands = [row for row in src["test_commands"]
                        if row["image_pattern"] == job["build"]["image_pattern"]
                        and row["test"] not in excluded]
            for row in qa_results.worksheet_commands({**src, "test_commands": commands}, hpc_version=selected["hpc_version"]):
                aliases = {row["test_name"], src.get("image_app_name", "") + "_" + row["test_name"],
                           row["app_id"] + "_" + row["test_name"]}
                candidates.append({**row, "qa_digest": selected["reference"], "_keys": {case_keys(a) for a in aliases},
                                   "_cap": job["cap"], "_commit": src["commit_id"],
                                   "_image_app_name": src.get("image_app_name", "")})
        accepted = []
        for record in records:
            location = f'{family}/{record["source_sheet"]}/{record["source_row"]}'
            details = {k: record[k] for k in ("source_sheet", "source_row", "source_case", "source_branch")}
            issue_details = {**details, "source_hpc_version": record["source_hpc_version"],
                             "source_qa_result": record["qa_result"], "source_qa_comment": record["qa_comment"]}
            named = [c for c in candidates if case_keys(record["source_case"]) in c["_keys"]]
            matching = named
            reason = "没有对应的历史镜像/测试项"
            app_version = lammps_version_label(record)
            branch = record["source_branch"]
            if app_version:
                matching = [c for c in matching if c["_image_app_name"] == "lammps"
                            and c["app_version"] == app_version]
                branch = "master"
                reason = f"LAMMPS {app_version} 没有对应的历史镜像/测试项，不能使用另一 App 版本的镜像"
            for key, expected in [("git_branch", branch), ("_cap", record["source_hpc_version"]),
                                  ("arch", record["host_arch"])]:
                if not matching or not expected:
                    continue
                narrowed = [c for c in matching if c[key] == expected]
                if not narrowed:
                    reason = f'{key} 不匹配：原表 {expected}；候选 {", ".join(sorted({c[key] for c in matching}))}'
                matching = narrowed
            if matching and record["GPU"]:
                supported = sorted({c["GPU"] for c in matching})
                matching = [c for c in matching if c["GPU"] == record["GPU"]]
                if not matching:
                    issue(skipped, family, "gpu_mismatch", f'GPU 不匹配：原表 {record["GPU"]}；候选 {", ".join(supported)}',
                          **issue_details)
                    continue
            override = overrides.get(location)
            if override:
                if not text(override.get("reason")):
                    raise ValueError(f"{location} 人工映射必须填写 reason")
                matching = [c for c in matching if all(text(c.get(k)) == text(v) for k, v in override.items() if k != "reason")]
            unique = {(qa_results.worksheet_result_key(c), c["qa_digest"]): c for c in matching}
            before_preference = len(unique)
            preferred = prefer_builds(list(unique.values()))
            unique = {(qa_results.worksheet_result_key(c), c["qa_digest"]): c for c in preferred}
            if before_preference and not unique:
                reason = "x86 排除 Kylin 后没有可用构建"
            if len(unique) != 1:
                issue(issues, family, "unmatched_result" if not unique else "ambiguous_result",
                      reason if not unique else "原表缺少容器 OS 或 Python/PyTorch 信息，存在多个匹配构建",
                      **issue_details, candidates=json.dumps([{k: c[k] for k in ("app_id", "git_branch", "OS", "arch", "GPU", "py_label", "torch_label", "qa_digest")}
                                                       for c in (list(unique.values()) or named)][:12], ensure_ascii=False))
                continue
            candidate = next(iter(unique.values()))
            mismatch = [k for k in ("perf_golden", "perf_unit") if record["source_" + k]
                        and not qa_results.performance_metadata_matches({k: record["source_" + k]}, candidate, k)]
            if mismatch:
                values = "；".join(f'{k}: 原表 {record["source_" + k]} / app_info {candidate[k]}' for k in mismatch)
                issue(issues, family, "metric_mismatch", values, **issue_details)
                continue
            note = "原表未记录 digest；按原表 HPC 版本上限和唯一匹配构建追溯补齐，非原始测试 digest 证据"
            if app_version:
                note += f"；按原表版本 {app_version} 区分 app_id，git_branch 使用 master"
            if before_preference > 1:
                note += "；x86 排除 Kylin，优先 Ubuntu 22 后唯一匹配"
            if override:
                note += "；人工映射：" + override["reason"]
            empty_result = text(record["qa_result"]).lower() in {"", "/", "-", "n/a", "na", "未测", "未测试", "not tested"}
            if empty_result:
                note += "；唯一匹配的空测试结果按确认规则填写 failed"
            row = {k: v for k, v in candidate.items() if not k.startswith("_")}
            row.update({**issue_details,
                        "source_result_header": record["source_result_header"],
                        "source_file": summaries[family]["filename"], "migration_note": note,
                        "qa_env": record["source_sheet"], "qa_result": "failed" if empty_result else record["qa_result"],
                        "qa_status": "fail" if empty_result else status_for(record["qa_result"], record["qa_comment"]),
                        "qa_comment": record["qa_comment"], "qa_docker_cmd": ""})
            accepted.append(row)
        # Do not silently let the upload service discard conflicting duplicates.
        grouped = defaultdict(list)
        for row in accepted:
            grouped[qa_results.worksheet_result_key(row)].append(row)
        kept = []
        for group in grouped.values():
            if len(group) == 1:
                kept.extend(group)
            else:
                for row in group:
                    issue(issues, family, "duplicate_result", "多条原表记录映射到同一结果键，需人工确认", **{k: row[k] for k in EXTRA_COLUMNS})
        output[family] = kept
    return output


def save_deliveries(conn, jobs):
    groups = defaultdict(list)
    for job in jobs:
        if job["purpose"] == "delivery":
            groups[job["entry"]["id"]].append(job["selected"])
    for entry_id, images in groups.items():
        entry = entries_repo.get_entry(conn, entry_id)
        encoded = json.dumps(images, ensure_ascii=False)
        conn.execute("UPDATE entries SET deliverable_images_json=? WHERE id=?", (encoded, entry_id))
        conn.execute("UPDATE official_versions SET images_json=? WHERE entry_id=?", (encoded, entry_id))
        log_audit(conn, "切换迁移：从 Harbor 补齐交付镜像", ts=beijing_timestamp(), user="cutover", role="RM",
                  release_id=entry["release_id"], app_id=entry["app_id"], event="cutover_delivery",
                  detail={"images": len(images), "source": "public_harbor_gerrit"})
    conn.commit()


def copy_sheet(source, target):
    for row in source:
        for cell in row:
            dest = target.cell(cell.row, cell.column, cell.value)
            if cell.has_style:
                # Style arrays reference a workbook-local registry. Copy the
                # actual objects when exporting sheets into another workbook.
                dest.font = copy(cell.font)
                dest.fill = copy(cell.fill)
                dest.border = copy(cell.border)
                dest.alignment = copy(cell.alignment)
                dest.protection = copy(cell.protection)
            if cell.number_format:
                dest.number_format = cell.number_format
            if cell.comment:
                dest.comment = copy(cell.comment)
    for key, dimension in source.column_dimensions.items():
        target.column_dimensions[key] = copy(dimension)
    for key, dimension in source.row_dimensions.items():
        target.row_dimensions[key] = copy(dimension)
    for merged in source.merged_cells.ranges:
        target.merge_cells(str(merged))
    target.freeze_panes = source.freeze_panes


def export_files(directory, jobs, rows, summaries, issues, overview, skipped=()):
    image_rows = []
    for job in jobs:
        s = job["selected"]
        image_rows.append({"release": job["release"], "purpose": job["purpose"], "app_id": job["app"]["id"],
            "OS": s["os"], "py_label": s["python_label"], "torch_label": s["pytorch_label"],
            **{key: s[key] for key in ("build", "arch", "image_target", "hpc_version", "image", "digest", "commit_id", "reference")},
            "status": "validated", "note": "QA 候选镜像" if job["purpose"] == "qa" else "交付镜像"})
    with (directory / "images.csv").open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=IMAGE_COLUMNS)
        writer.writeheader()
        writer.writerows(image_rows)
    book = Workbook()
    book.remove(book.active)
    for family, results in rows.items():
        exported = [{**row, "qa_env": family + "_" + row["qa_env"]} for row in results]
        report_table(book.create_sheet(family), [*qa_results.WORKSHEET_COLUMNS, *EXTRA_COLUMNS], exported)
    report_table(book.create_sheet("迁移汇总"), ["item", "value"],
                 [{"item": key, "value": json.dumps(value, ensure_ascii=False)} for key, value in overview.items()])
    issue_columns = ["release", "kind", "message", "app_id", "build", "target", "source_sheet", "source_row",
                     "source_case", "source_branch", "source_hpc_version", "source_qa_result", "source_qa_comment",
                     "reference", "candidates"]
    report_table(book.create_sheet("待确认问题"), issue_columns, issues)
    report_table(book.create_sheet("已跳过记录"), issue_columns, skipped)
    for family, summary in summaries.items():
        original = load_workbook(io.BytesIO(summary["content"]))
        for sheet in original:
            copy_sheet(sheet, book.create_sheet((family + "_" + sheet.title)[:31]))
        original.close()
    book.save(directory / "qa_log.xlsx")
    book.close()


def run(args):
    source, output = args.source.resolve(), args.output.resolve()
    if source == output or (output.exists() and os.path.samefile(source, output)):
        raise ValueError("输出数据库不能是源数据库")
    exports = output.with_suffix("")
    if output.exists() or exports.exists():
        raise ValueError(f"输出已存在，请指定新的 --output：{output} / {exports}")
    caps = dict(item.split("=", 1) for item in args.cap) if args.cap else DEFAULT_CAPS
    for family, cap in caps.items():
        version_key(cap)
        if qa_results.hpc_families(cap) != {family}:
            raise ValueError(f"周期 {family} 与上限 {cap} 不匹配")
    overrides = json.loads(args.overrides.read_text()) if args.overrides else {}
    output.parent.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    # All work is staged. Exceptions never replace a usable output or source DB.
    with tempfile.TemporaryDirectory(prefix=".cutover-", dir=output.parent) as staging:
        directory = Path(staging)
        database = directory / "main.db"
        snapshot_source(source, database)
        conn, releases, summaries, preserved = migrate(database, caps)
        issues, skipped = [], []
        try:
            jobs, source_rows = collect_images(conn, releases, summaries, caps, issues)
            rows = match_results(jobs, source_rows, summaries, issues, overrides, skipped)
            imports = {}
            for release in releases:
                family = release["name"]
                if family not in caps:
                    continue
                if not rows[family]:
                    issue(issues, family, "empty_qa_log", "没有可可靠导入的结果，保留原 summary")
                    continue
                content = qa_workbook(summaries[family]["content"], rows[family])
                print(f'{family}：校验并导入 {len(rows[family])}/{len(source_rows[family])} 条原表记录', flush=True)
                imports[family] = qa_results_service.upload(conn, content_b64=base64.b64encode(content).decode(),
                    filename=f"qa_log_{family}.xlsx", user="cutover", role="RM", release_id=release["id"])
                if imports[family]["rows"] != len(rows[family]):
                    raise ValueError(f"{family} 实际入库条数与匹配结果不一致")
            save_deliveries(conn, jobs)
            if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok" or conn.execute("PRAGMA foreign_key_check").fetchall():
                raise ValueError("迁移输出未通过数据库完整性/外键检查")
            needs_review = any(i["kind"] in REVIEW_KINDS for i in issues)
            overview = {"source": str(source), "created_at": beijing_timestamp(), "delivery_caps": caps,
                "delivery_image_rule": "只选择当前周期、不超过指定上限的最新匹配镜像；无镜像则留空",
                "qa_image_rule": "每个构建在原表 HPC Version 上限内选最新同周期镜像；不使用 Host SDK Version",
                "qa_match_rule": "x86 多构建候选排除 Kylin、优先 Ubuntu 22；LAMMPS 按原版本区分 app_id 后用 master；GPU 不匹配跳过",
                "qa_verdict_rule": "唯一匹配的空结果写 failed/fail；其余只转换明确 pass/fail/失败/性能回退；数值不自动推断通过",
                "export_usage": "合并 Excel 用于核对；每周期可单独上传的原始 QA log 可从新库网站下载",
                "preserved_table_counts": preserved,
                "release_dates": {r["name"]: locked_date(r["released_locked_at"]) for r in releases if r["released_locked"]},
                "source_rows": {family: len(records) for family, records in source_rows.items()},
                "imported_rows": {family: len(records) for family, records in rows.items()},
                "skipped_rows": {family: sum(r["release"] == family for r in skipped) for family in rows},
                "filled_failed_rows": {family: sum(r["qa_result"] == "failed" and
                    "空测试结果" in r["migration_note"] for r in records) for family, records in rows.items()},
                "delivery_images": dict(Counter(j["release"] for j in jobs if j["purpose"] == "delivery")),
                "issues": dict(Counter(i["kind"] for i in issues)), "component_compatibility_results": 0,
                "ready": not needs_review, "elapsed_seconds": round(time.monotonic() - started, 2)}
            log_audit(conn, "旧业务库切换演练完成", ts=beijing_timestamp(), user="cutover", role="RM", event="cutover_complete", detail=overview)
            conn.commit()
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            conn.execute("PRAGMA journal_mode=DELETE")
            export_files(directory, jobs, rows, summaries, issues, overview, skipped)
        finally:
            conn.close()
        # Two supplementary files only; issue details and run summary live in Excel.
        exports.mkdir(mode=0o700)
        os.chmod(database, 0o600)
        try:
            for name in ("images.csv", "qa_log.xlsx"):
                os.replace(directory / name, exports / name)
            # The temporary DB is on the output filesystem. link() publishes
            # atomically without replacing a file created by another process
            # while this run was querying Harbor/Gerrit.
            os.link(database, output)
        except BaseException:
            shutil.rmtree(exports)
            raise
        print(json.dumps({"database": str(output), "exports": str(exports), **overview}, ensure_ascii=False, indent=2))
        return 2 if needs_review else 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path("release_system.db"))
    parser.add_argument("--output", type=Path, default=Path("release_system_cutover.db"))
    parser.add_argument("--cap", action="append", help="周期=交付镜像版本上限；可重复，默认处理 3.9.0 和 3.10.1")
    parser.add_argument("--overrides", type=Path, help="人工确认的唯一构建映射 JSON：周期/页签/行号 -> 字段筛选及 reason")
    args = parser.parse_args()
    try:
        return run(args)
    except Exception as exc:
        print(f"切换未完成：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
