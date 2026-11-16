"""Excel result uploads and live reconstruction of their automatic commands."""

from __future__ import annotations

import base64
import binascii
import csv
import hashlib
import io
import json
import math
import re
import sqlite3
import zipfile
from pathlib import PurePath
from xml.etree.ElementTree import ParseError

from openpyxl import load_workbook
from openpyxl.utils.exceptions import InvalidFileException

from app.api.errors import ApiError, AuthzError
from app.db.connection import transaction
from app.domain import qa_results
from app.domain.permissions import has_capability
from app.integrations.read_session import read_session
from app.repositories import apps_repo, entries_repo, qa_results_repo, releases_repo
from app.repositories.audit_repo import log_audit
from app.services import catalog_service, lookup_service, qa_commands_service, qa_result_identity_service
from app.timeutil import beijing_timestamp

# Bound workbook expansion and validation work for a single interactive upload.
MAX_FILE_BYTES = 20 * 1024 * 1024
MAX_EXPANDED_BYTES = 200 * 1024 * 1024
MAX_RESULT_ROWS = 20000


def _release(conn, release_id):
    release = releases_repo.get_release_row(conn, release_id)
    if not release:
        raise ApiError(400, '请选择有效的 QA report 发布周期')
    if not re.fullmatch(r'\d+\.\d+\.\d+', release['name']):
        raise ApiError(400, 'QA report 发布周期须为 3.10.0 形式的 HPC 版本')
    return release


def _records(conn, release=None):
    records = qa_result_identity_service.list_records(conn, release_id=release['id'] if release else None)
    if release is None:
        return records
    return [record for record in records if record['release_id'] == release['id'] or (
        record['release_id'] is None and qa_results.hpc_families(record['data'].get('hpc_version', '')) == {release['name']})]


def _revision(records):
    values = sorted((record['result_key'], record['report_id'], record['data_json']) for record in records)
    return hashlib.sha256(json.dumps(values, ensure_ascii=False).encode()).hexdigest()


def failure_summary(conn, release_id):
    """Summarize saved report failures without regenerating commands or inferring performance."""
    release = releases_repo.get_release_row(conn, release_id)
    if not release:
        raise ApiError(404, "发布周期不存在")
    records = _records(conn, release)
    apps = {app["id"]: app for app in apps_repo.list_apps(conn)}
    signed_up = {entry["app_id"] for entry in entries_repo.list_for_release(conn, release_id)}
    groups = {}
    for record in records:
        data = qa_results.canonical_result(record["data"])
        if qa_results.result_status(data) != "fail":
            continue
        app_id, branch = str(data["app_id"]).strip(), str(data["git_branch"]).strip()
        app = apps.get(app_id)
        key = (bool(app_id), app_id or data.get("app_name", ""), branch)
        matched = app_id in signed_up and app and app["git_branch"].strip() == branch
        group = groups.setdefault(key, {"app_id": app_id if matched else None,
                                       "reported_app_id": app_id,
                                       "app_name": catalog_service.app_name(app) if app else data.get("app_name") or app_id or "未知 App",
                                       "git_branch": branch, "failures": []})
        group["failures"].append({column: data[column] for column in (
            "app_version", "hpc_version", "arch", "OS", "GPU", "py_label", "torch_label",
            "test_name", "qa_result", "qa_comment", "qa_env")})
    for group in groups.values():
        group["failures"].sort(key=lambda row: tuple(str(row[key]).casefold()
            for key in ("GPU", "test_name", "hpc_version", "app_version", "OS", "arch", "py_label", "torch_label")))
    return {"release_id": release_id, "release_name": release["name"], "total_rows": len(records),
            "failed_rows": sum(len(group["failures"]) for group in groups.values()),
            "groups": sorted(groups.values(), key=lambda group: (str(group["app_name"]).casefold(), group["git_branch"]))}


def _legacy_reports_to_remove(conn, release):
    """Legacy files can contain several cycles; retain any file still used elsewhere."""
    families = {}
    for record in qa_results_repo.list_results(conn, legacy_only=True):
        families.setdefault(record['report_id'], set()).update(qa_results.hpc_families(record['data'].get('hpc_version', '')))
    selected = []
    for report in qa_results_repo.legacy_workbooks(conn):
        scope = families.get(report['id'])
        if scope is None:
            # Superseded old files may have no rows left. Read only their test
            # sheet's version column; environment sheets stay opaque.
            book = None
            scope = set()
            try:
                original = qa_results_repo.workbook(conn, report['id'])
                if original is None:
                    continue
                book = load_workbook(io.BytesIO(original['content']), read_only=True, data_only=False)
                rows = book.worksheets[0].iter_rows(values_only=True)
                header = list(next(rows, ()))
                if 'hpc_version' in header:
                    column = header.index('hpc_version')
                    for number, row in enumerate(rows):
                        if number >= MAX_RESULT_ROWS:
                            break
                        if column < len(row):
                            scope.update(qa_results.hpc_families(row[column]))
            except (OSError, ValueError, KeyError, zipfile.BadZipFile, ParseError, InvalidFileException):
                pass
            finally:
                if book is not None:
                    book.close()
        if scope == {release['name']}:
            selected.append(report['id'])
    return selected


def _read_workbook(content: bytes) -> tuple[list[tuple[int, dict, str]], list[str], dict]:
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            if sum(info.file_size for info in archive.infolist()) > MAX_EXPANDED_BYTES:
                raise ValueError("Excel 解压后过大")
        book = load_workbook(io.BytesIO(content), read_only=True, data_only=False)
    except (zipfile.BadZipFile, OSError, KeyError, ParseError, InvalidFileException) as exc:
        raise ValueError("无法读取 Excel，请上传有效的 .xlsx 文件") from exc
    try:
        environments = book.sheetnames[1:]  # Keep pages untouched in the original BLOB.
        sheet = book.worksheets[0]
        if sheet.max_column and sheet.max_column > 256:
            raise ValueError("测试结果页不能超过 256 列")
        if sheet.max_row and sheet.max_row > MAX_RESULT_ROWS + 1:
            raise ValueError(f"测试结果页不能超过 {MAX_RESULT_ROWS} 行")
        values = sheet.iter_rows(values_only=True)
        headers = [str(value or "").strip() for value in next(values, ())]
        new_format = "website_docker_cmd" in headers or any(
            column in headers for column in qa_results.WORKSHEET_QA_COLUMNS)
        headers = [qa_results.WORKSHEET_ALIASES.get(value, value) for value in headers]
        if "" in headers or len(set(headers)) != len(headers):
            raise ValueError("测试结果页存在重复或空列名")
        metadata = [column for column in qa_results.WORKSHEET_METADATA_COLUMNS
                    if new_format or column != "hpc_version"]
        required = (*metadata, "qa_docker_cmd", "qa_result", "qa_digest", "qa_env")
        missing = set(required) - set(headers)
        if missing:
            if "app_id" in missing and "app_name" in headers:
                raise ValueError("第一个页签缺少 app_id；app_name 已改为系统唯一 app_id，请重新下载测试命令填写后上传")
            raise ValueError("第一个页签缺少列：" + ", ".join(sorted(missing)))
        rows, skipped_apps = [], {}
        total_rows = 0
        for number, values_row in enumerate(values, 2):
            if not any(value is not None and value != "" for value in values_row):
                continue
            total_rows += 1
            if total_rows > MAX_RESULT_ROWS:
                raise ValueError(f"测试结果不能超过 {MAX_RESULT_ROWS} 行")
            row = {
                key: value if value is not None else ""
                for key, value in zip(headers, values_row, strict=False)
            }
            fill_columns = qa_results.WORKSHEET_QA_COLUMNS if new_format else (
                "qa_result", "qa_digest", "qa_env", "qa_status", "qa_comment")
            if all(row.get(column, "") == "" for column in fill_columns):
                key = (str(row.get("app_id") or "").strip(), str(row.get("git_branch") or "").strip())
                skipped_apps[key] = skipped_apps.get(key, 0) + 1
                continue
            for column in headers:
                if column == "website_docker_cmd":
                    # Display-only spreadsheet content is never a command
                    # source. Rebuild the result from the validated digest.
                    continue
                value = row.get(column, "")
                if isinstance(value, float) and not math.isfinite(value):
                    raise ValueError(f"第 {number} 行 {column} 不是有效数值")
                if not isinstance(value, (str, int, float, bool)):
                    row[column] = str(value)
                if isinstance(value, str) and value.startswith("="):
                    raise ValueError(f"第 {number} 行 {column} 请填写值，不使用公式")
            for column in qa_results.KEY_COLUMNS:
                row[column] = str(row[column]).strip()
                if not row[column] and column != "GPU":
                    raise ValueError(f"第 {number} 行 {column} 不能为空")
            row["qa_status"] = str(row.get("qa_status", "")).strip()
            row["qa_comment"] = str(row.get("qa_comment", ""))
            if row["qa_status"] not in ("", "pass", "fail"):
                raise ValueError(f"第 {number} 行 qa_status 只能填写 pass 或 fail，也可留空")
            env = str(row["qa_env"]).strip()
            row["qa_env"] = env
            if new_format and not env:
                raise ValueError(f"第 {number} 行 qa_env 不能为空，请填写环境页签名称")
            if env and env not in environments:
                raise ValueError(f"第 {number} 行 qa_env「{env}」没有对应环境页签")
            if not str(row["qa_digest"]).strip():
                raise ValueError(f"第 {number} 行填写 QA 结果后必须填写 qa_digest")
            if new_format and not str(row.get("hpc_version", "")).strip():
                raise ValueError(f"第 {number} 行 hpc_version 不能为空")
            try:
                reference = qa_results.result_reference(row)
                if reference:
                    lookup_service._lookup_image(reference)
            except ValueError as exc:
                raise ValueError(f"第 {number} 行：{exc}") from exc
            rows.append((number, row, reference))
        summary = {"total_rows": total_rows, "skipped_rows": sum(skipped_apps.values()),
                   "skipped_apps": [{"app_id": name, "git_branch": branch, "rows": count}
                                    for (name, branch), count in sorted(skipped_apps.items())]}
        return rows, environments, summary
    finally:
        book.close()


def upload(
    conn: sqlite3.Connection, *, content_b64: str, filename: str, user: str, role: str,
    release_id: str, revision: str | None = None,
) -> dict:
    if not has_capability(role, "qa.edit"):
        raise AuthzError("只有 QA 或 RM 可上传测试结果")
    release = _release(conn, release_id)
    before = _records(conn, release)
    expected_revision = _revision(before)
    if revision is not None and revision != expected_revision:
        raise ApiError(409, '该版本 QA report 已变化，请刷新后重新上传')
    if not isinstance(filename, str) or not filename.lower().endswith(".xlsx"):
        raise ValueError("请上传 .xlsx 文件")
    if not isinstance(content_b64, str) or len(content_b64) > MAX_FILE_BYTES * 4 // 3 + 4:
        raise ValueError("Excel 文件不能超过 20 MB")
    try:
        content = base64.b64decode(content_b64, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ValueError("Excel 文件编码无效") from exc
    if len(content) > MAX_FILE_BYTES:
        raise ValueError("Excel 文件不能超过 20 MB")
    rows, environments, summary = _read_workbook(content)
    if not rows:
        total_tests = qa_commands_service.count_scope_tests(conn, release_id)
        raise ValueError(
            f"第一个页签没有填写 QA 结果的测试行：{release['name']} 版本共 {total_tests} 条测试；"
            f"文件含 {summary['total_rows']} 条；保存 0 条；跳过 {summary['skipped_rows']} 条"
            f"（QA 列全空跳过 {summary['skipped_rows']} 行）。未保存，原报告保留。"
        )
    for number, row, _ in rows:
        version = str(row.get('hpc_version', '')).strip()
        if version and qa_results.hpc_families(version) != {release['name']}:
            raise ValueError(f"第 {number} 行 hpc_version {version} 不属于所选发布周期 {release['name']}")
    references = list(dict.fromkeys(ref for _, _, ref in rows if ref))
    # All remote I/O and validation precede the write transaction. A single bad
    # row rejects the entire upload; existing results and files remain intact.
    resolved = _resolve_images(conn, references)
    errors = {item["image"]: item["error"] for item in resolved["errors"]}
    candidates = {
        image["image"]: qa_results.worksheet_commands(image)
        for image in resolved["images"]
        if image.get("app_id") and image["git_branch"]
    }
    provenance = {image["image"]: image for image in resolved["images"]}
    version_commands = {}
    failures = []
    for number, row, reference in rows:
        source = provenance.get(reference, {})
        choices = candidates.get(reference, [])
        matching = [candidate for candidate in choices
                    if qa_results.result_key(candidate) == qa_results.result_key(row)]
        if reference in errors:
            failures.append(f"第 {number} 行：{errors[reference]}")
        elif not matching:
            values = qa_results.identity_values(row)
            choices = [
                qa_results.identity_values(command) for command in candidates.get(reference, [])
            ]
            if choices:
                closest = min(
                    choices,
                    key=lambda choice: sum(a != b for a, b in zip(values, choice, strict=True)),
                )
                fields = [
                    column
                    for column, actual, expected in zip(
                        qa_results.KEY_COLUMNS, values, closest, strict=True
                    )
                    if actual != expected
                ]
                reason = "/".join(fields) + " 不匹配"
            else:
                reason = "test_name 不匹配或 app_id / git_branch 无法唯一确定"
            failures.append(f"第 {number} 行：digest 与 {reason}")
        else:
            try:
                labels = ("py_label", "torch_label")
                candidate = next((candidate for candidate in matching if all(
                    str(row.get(label, candidate[label])).strip() == str(candidate[label])
                    for label in labels)), None)
                if candidate is None:
                    raise ValueError("qa_digest 与 py_label / torch_label 不匹配")
                mismatched = [column for column in ("perf_golden", "perf_unit")
                              if not qa_results.performance_metadata_matches(row, candidate, column)]
                if mismatched:
                    raise ValueError("qa_digest 对应的历史 app_info 与 " + " / ".join(mismatched) + " 不匹配")
                requested = str(row.get("hpc_version", "")).strip()
                versions = source.get("hpc_versions", [])
                if requested and requested not in versions:
                    raise ValueError("qa_digest 与 hpc_version 不匹配")
                qa_results.validate_command_images(row, reference, source["tags"])
                scoped_versions = [version for version in versions if qa_results.hpc_families(version) == {release['name']}]
                row["hpc_version"] = requested or ', '.join(scoped_versions)
                if not row['hpc_version']:
                    raise ValueError(f"qa_digest 没有所选发布周期 {release['name']} 的 HPC 版本 tag")
                cache_key = (reference, row["hpc_version"])
                if cache_key not in version_commands:
                    version_commands[cache_key] = qa_results.worksheet_commands(source, hpc_version=row["hpc_version"])
                row["website_docker_cmd"] = next(item["website_docker_cmd"] for item in version_commands[cache_key]
                    if qa_results.result_key(item) == qa_results.result_key(row)
                    and all(item[label] == candidate[label] for label in labels))
                row["qa_digest"] = reference
                # Persist canonical source values after checking all twelve
                # metadata columns; preserve QA-entered result/command text.
                row.update({column: candidate[column] for column in qa_results.WORKSHEET_METADATA_COLUMNS
                            if column != "hpc_version"})
                row["qa_digest_gerrit_commit_id"] = source["commit_id"]
                row["app_name"] = source["app_name"]  # Display metadata only; never an identity key.
                row["historical_result_key"] = qa_results.result_key(row)
                row.pop("commit_id", None)
            except ValueError as exc:
                failures.append(f"第 {number} 行：{exc}")
    if failures:
        raise ValueError(
            "；".join(failures[:30])
            + (f"；另有 {len(failures) - 30} 行错误" if len(failures) > 30 else "")
        )
    # Distinct configurations coexist inside this file. The file replaces the
    # complete cycle, including tests absent from this upload.
    latest = {qa_results.worksheet_result_key(row): (row, ref) for _, row, ref in rows}
    prepared = [(key, row, ref) for key, (row, ref) in latest.items()]
    ts = beijing_timestamp()
    filename = PurePath(filename.replace("\\", "/")).name
    legacy_report_ids = _legacy_reports_to_remove(conn, release)
    with transaction(conn, immediate=True):
        if _release(conn, release_id)['name'] != release['name'] or _revision(_records(conn, release)) != expected_revision:
            raise ApiError(409, '该版本 QA report 已变化，请刷新后重新上传')
        release_test_count = qa_commands_service.count_scope_tests(conn, release_id)
        replaced = len(before)
        qa_results_repo.clear_release(conn, release_id, keys=[record['result_key'] for record in before],
                                      legacy_report_ids=legacy_report_ids)
        report_id = qa_results_repo.save(
            conn,
            filename=filename,
            content=content,
            environments=environments,
            uploaded_at=ts,
            uploaded_by=user,
            rows=prepared,
            release_id=release_id,
        )
        log_audit(
            conn,
            f"QA 上传测试结果：{filename}（{len(rows)} 行，覆盖 {replaced} 行）",
            ts=ts,
            user=user,
            role=role,
            release_id=release_id,
            event="qa_test_results_upload",
            detail={"report_id": report_id, 'mode': 'replace_release', 'removed_rows': replaced,
                    "release_test_count": release_test_count, "saved_rows": len(prepared),
                    "duplicate_rows": len(rows) - len(prepared),
                    "total_rows": summary["total_rows"], "skipped_rows": summary["skipped_rows"]},
        )
    return {
        "ok": True,
        "report_id": report_id,
        "rows": len(prepared),
        "duplicate_rows": len(rows) - len(prepared),
        "replaced": replaced,
        "uploaded_at": ts,
        'release_id': release_id,
        'release_name': release['name'],
        'release_test_count': release_test_count,
        **summary,
    }


def _resolve_images(conn, references):
    result = {"images": [], "errors": []}
    with read_session():
        for start in range(0, len(references), lookup_service.MAX_BATCH_IMAGES):
            batch = lookup_service.resolve_images(conn, references[start:start + lookup_service.MAX_BATCH_IMAGES])
            result["images"].extend(batch["images"])
            result["errors"].extend(batch["errors"])
    return result


def _live_rows(conn, records):
    references = list(
        dict.fromkeys(row["image_reference"] for row in records if row["image_reference"])
    )
    resolved = _resolve_images(conn, references)
    sources = {image["image"]: image for image in resolved["images"] if image.get("app_id") and image["git_branch"]}
    commands = {}
    command_errors = {}
    errors = {item["image"]: item["error"] for item in resolved["errors"]}
    rows = []
    for record in records:
        reference = record["image_reference"]
        data = record["data"]
        cache_key = (reference, str(data.get("hpc_version", "")))
        if cache_key not in commands:
            commands[cache_key] = {}
            try:
                generated = qa_results.worksheet_commands(sources[reference], hpc_version=cache_key[1]) if reference in sources else []
                commands[cache_key] = {qa_results.worksheet_result_key(row): row["website_docker_cmd"] for row in generated}
            except ValueError as exc:
                command_errors[cache_key] = str(exc)
        command = commands[cache_key].get(qa_results.worksheet_result_key(data), "")
        rows.append(
            {
                **data,
                "website_docker_cmd": command,
                "command_error": data.get("app_id_error") or command_errors.get(cache_key) or errors.get(
                    reference,
                    ""
                    if command
                    else "未填写 digest"
                    if not reference
                    else "当前配置无法匹配该测试行",
                ),
                **{
                    key: record[key]
                    for key in ("report_id", "filename", "uploaded_at", "uploaded_by")
                },
            }
        )
    return rows


def query(conn: sqlite3.Connection, *, search: str = "", table_filter: str = "",
          column_filters: dict | None = None, sort_by: str = "", sort_direction: str = "asc",
          offset: int = 0, limit: int | None = 50, release_id: str = '') -> dict:
    release = _release(conn, release_id) if release_id else None
    if column_filters is None:
        column_filters = {}
    if (not isinstance(column_filters, dict) or any(
            column not in qa_results.REPORT_COLUMNS or not isinstance(value, str)
            for column, value in column_filters.items())):
        raise ApiError(400, "列筛选须为有效列名与文字组成的对象")
    if sort_by and sort_by not in qa_results.REPORT_COLUMNS:
        raise ApiError(400, "无效的排序列")
    if sort_direction not in ("asc", "desc"):
        raise ApiError(400, "排序方向须为 asc 或 desc")
    filters = {column: value for column, value in column_filters.items() if value.strip()}
    stored_filters = {column: value for column, value in filters.items() if column != "website_docker_cmd"}
    records = _records(conn, release)
    revision = _revision(records)
    for record in records:
        record["data"] = {
            **qa_results.canonical_result(record["data"]),
            **{key: record[key] for key in ("report_id", "filename", "uploaded_at", "uploaded_by")},
            **{column: record[key] for column, key in qa_results.REPORT_METADATA_COLUMNS.items()},
        }
    records = [record for record in records if qa_results.report_matches(
        record["data"], column_filters=stored_filters)]
    # Metadata filters/sorts need live commands only for the requested page.
    # Searching command text must use freshly generated commands, never JSON saved at upload.
    needs_live = bool(search.strip() or table_filter.strip() or "website_docker_cmd" in filters
                      or sort_by == "website_docker_cmd")
    if needs_live:
        live_rows = _live_rows(conn, records)
        records = [{**record, "data": row} for record, row in zip(records, live_rows, strict=True)
                   if qa_results.report_matches(row, searches=(search, table_filter), column_filters=filters)]
    if sort_by:
        records.sort(key=lambda record: qa_results.report_sort_key(record["data"], sort_by),
                     reverse=sort_direction == "desc")
    total = len(records)
    page = records[offset:] if limit is None else records[offset:offset + limit]
    rows = [record["data"] for record in page] if needs_live else _live_rows(conn, page)
    return {"total": total, "rows": rows, "offset": offset, "limit": limit,
            "columns": qa_results.WORKSHEET_COLUMNS, 'revision': revision,
            'release_id': release_id, 'release_name': release['name'] if release else ''}


def export_csv(conn: sqlite3.Connection, **filters) -> str:
    result = query(conn, **filters, limit=None)
    output = io.StringIO(newline="")
    output.write("\ufeff")
    writer = csv.writer(output)
    writer.writerow(qa_results.REPORT_COLUMNS)
    writer.writerows([row.get(column, "") for column in qa_results.REPORT_COLUMNS] for row in result["rows"])
    return output.getvalue()


def download(conn: sqlite3.Connection, report_id: int) -> tuple[bytes, str]:
    row = qa_results_repo.workbook(conn, report_id)
    if not row:
        raise ApiError(404, "测试报告不存在")
    return row["content"], row["filename"]
