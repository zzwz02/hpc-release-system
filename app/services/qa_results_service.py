"""Excel result uploads and live reconstruction of their automatic commands."""

from __future__ import annotations

import base64
import binascii
import io
import math
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
from app.repositories import qa_results_repo
from app.repositories.audit_repo import log_audit
from app.services import lookup_service
from app.timeutil import beijing_timestamp

# Bound workbook expansion and validation work for a single interactive upload.
MAX_FILE_BYTES = 20 * 1024 * 1024
MAX_EXPANDED_BYTES = 200 * 1024 * 1024
MAX_RESULT_ROWS = 20000


def _read_workbook(content: bytes) -> tuple[list[tuple[int, dict, str]], list[str]]:
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
        headers = ["GPU" if value == "支持芯片" else value for value in headers]
        if "" in headers or len(set(headers)) != len(headers):
            raise ValueError("测试结果页存在重复或空列名")
        missing = set((*qa_results.KEY_COLUMNS, "docker_cmd", *qa_results.RESULT_COLUMNS)) - set(
            headers
        )
        if missing:
            raise ValueError("第一个页签缺少列：" + ", ".join(sorted(missing)))
        rows = []
        for number, values_row in enumerate(values, 2):
            if not any(value is not None and value != "" for value in values_row):
                continue
            if len(rows) >= MAX_RESULT_ROWS:
                raise ValueError(f"测试结果不能超过 {MAX_RESULT_ROWS} 行")
            row = {
                key: value if value is not None else ""
                for key, value in zip(headers, values_row, strict=False)
            }
            if all(row.get(column, "") == "" for column in qa_results.RESULT_COLUMNS):
                continue
            for column in headers:
                value = row.get(column, "")
                if isinstance(value, float) and not math.isfinite(value):
                    raise ValueError(f"第 {number} 行 {column} 不是有效数值")
                if not isinstance(value, (str, int, float, bool)):
                    row[column] = str(value)
                if isinstance(value, str) and value.startswith("="):
                    raise ValueError(f"第 {number} 行 {column} 请填写值，不使用公式")
            for column in qa_results.KEY_COLUMNS:
                row[column] = str(row[column]).strip()
                if not row[column]:
                    raise ValueError(f"第 {number} 行 {column} 不能为空")
            env = str(row["test_env"]).strip()
            row["test_env"] = env
            if env and env not in environments:
                raise ValueError(f"第 {number} 行 test_env「{env}」没有对应环境页签")
            if not str(row["test_digest"]).strip():
                raise ValueError(f"第 {number} 行填写结果或环境后必须填写 test_digest")
            try:
                reference = qa_results.result_reference(row)
                if reference:
                    lookup_service._lookup_image(reference)
            except ValueError as exc:
                raise ValueError(f"第 {number} 行：{exc}") from exc
            rows.append((number, row, reference))
        if not rows:
            raise ValueError("第一个页签没有填写结果或 digest 的测试行")
        return rows, environments
    finally:
        book.close()


def upload(
    conn: sqlite3.Connection, *, content_b64: str, filename: str, user: str, role: str
) -> dict:
    if not has_capability(role, "qa.edit"):
        raise AuthzError("只有 QA 或 RM 可上传测试结果")
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
    rows, environments = _read_workbook(content)
    references = list(dict.fromkeys(ref for _, _, ref in rows if ref))
    # All remote I/O and validation precede the write transaction. A single bad
    # row rejects the entire upload; existing results and files remain intact.
    resolved = (
        lookup_service.resolve_images(conn, references)
        if references
        else {"images": [], "errors": []}
    )
    errors = {item["image"]: item["error"] for item in resolved["errors"]}
    candidates = {
        image["image"]: qa_results.flat_commands(image)
        for image in resolved["images"]
        if image["git_branch"]
    }
    matches = {
        reference: {qa_results.result_key(row) for row in commands}
        for reference, commands in candidates.items()
    }
    provenance = {image["image"]: image for image in resolved["images"]}
    failures = []
    for number, row, reference in rows:
        row["hpc_version"] = provenance.get(reference, {}).get("hpc_version", "")
        row["commit_id"] = provenance.get(reference, {}).get("commit_id", "")
        if not reference:
            continue
        if reference in errors:
            failures.append(f"第 {number} 行：{errors[reference]}")
        elif qa_results.result_key(row) not in matches.get(reference, set()):
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
                reason = "test_name 不匹配或 git_branch 无法唯一确定"
            failures.append(f"第 {number} 行：digest 与 {reason}")
    if failures:
        raise ValueError(
            "；".join(failures[:30])
            + (f"；另有 {len(failures) - 30} 行错误" if len(failures) > 30 else "")
        )
    # The prescribed identity excludes Python/Torch/build target. If an Excel
    # contains several rows with the same key, its final row wins.
    latest = {qa_results.result_key(row): (row, ref) for _, row, ref in rows}
    prepared = [(key, row, ref) for key, (row, ref) in latest.items()]
    ts = beijing_timestamp()
    filename = PurePath(filename.replace("\\", "/")).name
    with transaction(conn, immediate=True):
        replaced = len(qa_results_repo.matching(conn, [key for key, _, _ in prepared]))
        report_id = qa_results_repo.save(
            conn,
            filename=filename,
            content=content,
            environments=environments,
            uploaded_at=ts,
            uploaded_by=user,
            rows=prepared,
        )
        log_audit(
            conn,
            f"QA 上传测试结果：{filename}（{len(rows)} 行，覆盖 {replaced} 行）",
            ts=ts,
            user=user,
            role=role,
            event="qa_test_results_upload",
            detail={"report_id": report_id},
        )
    return {
        "ok": True,
        "report_id": report_id,
        "rows": len(prepared),
        "duplicate_rows": len(rows) - len(prepared),
        "replaced": replaced,
        "uploaded_at": ts,
    }


def query(conn: sqlite3.Connection, *, search: str = "", offset: int = 0, limit: int = 50) -> dict:
    total, records = qa_results_repo.search(conn, search.strip(), offset, limit)
    references = list(
        dict.fromkeys(row["image_reference"] for row in records if row["image_reference"])
    )
    resolved = (
        lookup_service.resolve_images(conn, references)
        if references
        else {"images": [], "errors": []}
    )
    commands = {
        image["image"]: {
            qa_results.result_key(row): row["docker_cmd"] for row in qa_results.flat_commands(image)
        }
        for image in resolved["images"]
        if image["git_branch"]
    }
    errors = {item["image"]: item["error"] for item in resolved["errors"]}
    rows = []
    for record in records:
        reference = record["image_reference"]
        command = commands.get(reference, {}).get(record["result_key"], "")
        rows.append(
            {
                **record["data"],
                "auto_command": command,
                "command_error": errors.get(
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
    return {"total": total, "rows": rows, "offset": offset, "limit": limit}


def download(conn: sqlite3.Connection, report_id: int) -> tuple[bytes, str]:
    row = qa_results_repo.workbook(conn, report_id)
    if not row:
        raise ApiError(404, "测试报告不存在")
    return row["content"], row["filename"]
