#!/usr/bin/env python3
"""Load Jira material from REST, a deployment workspace, or both."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import mimetypes
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
from datetime import datetime, timezone

ISSUE = re.compile(r"[A-Z][A-Z0-9_]*-[0-9]+")
MODULE = Path(__file__).resolve().parents[1]
DETAILS = MODULE / "skills/jira-issue-manager/scripts/get_issue_details.py"
DEFAULT_BASE_URL = os.environ.get("JIRA_BASE_URL", "http://10.2.201.98:8080")


def emit(payload: dict) -> int:
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 1 if payload.get("success") is False else 0


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path, limit: int) -> str | None:
    if path.stat().st_size > limit:
        return None
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def source_value(value):
    if not isinstance(value, str) or len(value) <= 512:
        return value
    raw = value.encode("utf-8")
    return {"length": len(value), "sha256": sha256_bytes(raw), "preview": value[:200]}


SECTIONS = (
    {"描述", "description"},
    {"附件", "attachments"},
    {"评论（按时间顺序）", "评论", "comments"},
)


def prose_lines(lines: list[str]) -> tuple[list[tuple[int, str]], bool]:
    """Return source positions outside Markdown fences and Jira code macros.

    Filtering is only for recognising boundaries; slices retain every original
    line, including code blocks and headings inside them.
    """
    fence = ""
    width = 0
    macro = ""
    visible = []
    for index, line in enumerate(lines):
        if macro:
            if line.strip() == macro:
                macro = ""
            continue
        if fence:
            closing = re.fullmatch(r" {0,3}([`~]+)[ \t]*", line)
            if closing and set(closing.group(1)) == {fence} and len(closing.group(1)) >= width:
                fence = ""
            continue
        opening = re.fullmatch(r" {0,3}(`{3,}|~{3,})(.*)", line)
        if opening and not (opening.group(1)[0] == "`" and "`" in opening.group(2)):
            fence = opening.group(1)[0]
            width = len(opening.group(1))
            continue
        if re.fullmatch(r"\{code(?::[^{}]*)?\}", line.strip()):
            macro = "{code}"
            continue
        if line.strip() == "{noformat}":
            macro = "{noformat}"
            continue
        visible.append((index, line))
    return visible, not (fence or macro)


def section_headers(lines: list[str]) -> list[tuple[int, int]]:
    headers = []
    for index, line in prose_lines(lines)[0]:
        match = re.fullmatch(r"##\s+(.+?)\s*", line)
        if match:
            for section, names in enumerate(SECTIONS):
                if match.group(1).strip().lower() in names:
                    headers.append((index, section))
                    break
    return headers


def find_section(lines: list[str], names: set[str]) -> tuple[str, bool]:
    """End only at a later snapshot section, never at an arbitrary body heading."""
    section = next(index for index, aliases in enumerate(SECTIONS) if names & aliases)
    start = None
    end = len(lines)
    for index, current in section_headers(lines):
        if start is None:
            if current == section:
                start = index + 1
            continue
        if current > section:
            end = index
            break
    if start is None:
        return "", False
    return "\n".join(lines[start:end]).strip(), True


def metadata(text: str, pattern: str) -> tuple[str, ...]:
    match = re.search(pattern, text, flags=re.M)
    return tuple(item.strip() for item in match.groups()) if match else ()


def values(text: str) -> list[str]:
    if not text or text == "无":
        return []
    return [item.strip() for item in re.split(r"[,，]", text) if item.strip()]


def parse_comments(text: str) -> tuple[list[dict], bool]:
    lines = text.splitlines()
    headers = []
    visible, balanced = prose_lines(lines)
    for index, line in visible:
        match = re.fullmatch(r"###\s+(.+?)\s+@\s+(.+?)\s*", line)
        if match:
            headers.append((index, match.group(1).strip(), match.group(2).strip()))
    comments = []
    for position, (index, author, created) in enumerate(headers):
        end = headers[position + 1][0] if position + 1 < len(headers) else len(lines)
        value = "\n".join(lines[index + 1:end]).strip()
        raw = f"{author}\0{created}\0{value}".encode("utf-8")
        comments.append({
            "id": "local-" + sha256_bytes(raw)[:20],
            "author": author,
            "body": value,
            "created": created,
            "updated": None,
            "visibility": None,
            "body_raw": value,
        })
    # A heading-less comment or a preamble must not silently disappear.
    prefix = "\n".join(lines[:headers[0][0]]).strip() if headers else text.strip()
    complete = balanced and (not prefix or (not headers and prefix == "（无评论）"))
    return comments, complete


def parse_declared(text: str) -> list[dict]:
    result = []
    for line in text.splitlines():
        match = re.fullmatch(r"-\s+(.+?)（([0-9]+)\s+字节）→\s+`([^`]+)`\s*", line)
        if match:
            result.append({"filename": match.group(1), "size": int(match.group(2)), "local_path": match.group(3)})
    return result


def scan_folder(bundle: Path, name: str, limit: int) -> tuple[list[dict], dict]:
    folder = bundle / name
    if not folder.exists():
        return [], {"status": "complete", "present": False, "files": 0}
    if folder.is_symlink() or not folder.is_dir():
        return [], {"status": "partial", "error_code": "unsafe_or_invalid_directory", "files": 0}
    records = []
    errors = []
    entries = sorted(folder.iterdir(), key=lambda item: item.name)
    if len(entries) > 2000:
        entries = entries[:2000]
        errors.append("file_count_exceeded")
    for path in entries:
        if path.is_symlink() or not path.is_file():
            errors.append("unsupported_entry:" + path.name)
            continue
        size = path.stat().st_size
        digest = sha256_file(path, limit)
        records.append({
            "filename": path.name,
            "local_path": path.relative_to(bundle).as_posix(),
            "size": size,
            "sha256": digest,
            "content_check": "sha256" if digest else "size_only",
        })
    status = "partial" if errors else "complete"
    part = {"status": status, "present": True, "files": len(records)}
    if errors:
        part["errors"] = errors
    return records, part


def safe_attachment_path(value: str) -> bool:
    path = PurePosixPath(value)
    return not path.is_absolute() and ".." not in path.parts and len(path.parts) >= 2 and path.parts[0] == "attachments"


def attachment_record(record: dict, filename: str | None = None) -> dict:
    digest = record.get("sha256")
    identity = digest or sha256_bytes(record["local_path"].encode("utf-8"))
    return {
        "id": "local-" + identity[:20],
        "filename": filename or record["filename"],
        "url": None,
        "mime_type": mimetypes.guess_type(filename or record["filename"])[0],
        "size": record["size"],
        "created": None,
        "author": None,
        "local_path": record["local_path"],
        "sha256": digest,
        "content_check": record.get("content_check"),
        "available": True,
        "source": "local_bundle",
    }



def json_snapshot(issue_id: str, bundle: Path, limit: int) -> dict:
    """Read the website's versioned snapshot without parsing body Markdown."""
    path = bundle / "issue.json"
    if path.is_symlink() or not path.is_file():
        return {"success": False, "error_code": "local_json_unsafe_or_invalid"}
    if path.stat().st_size > limit:
        return {"success": False, "error_code": "local_issue_too_large"}
    try:
        raw = path.read_bytes()
        packet = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        return {"success": False, "error_code": "local_json_unreadable"}
    if not isinstance(packet, dict) or type(packet.get("schema_version")) is not int or packet["schema_version"] != 1:
        return {"success": False, "error_code": "local_json_unsupported_schema"}
    data = packet.get("issue")
    if not isinstance(data, dict) or data.get("key") != issue_id:
        return {"success": False, "error_code": "local_issue_identity_mismatch"}
    acquisition = data.get("acquisition")
    if not isinstance(acquisition, dict) or acquisition.get("status") not in {"complete", "partial"}:
        return {"success": False, "error_code": "local_json_invalid_coverage"}
    parts = acquisition.get("parts")
    required = ("issue", "description", "custom_fields", "field_definitions", "comments", "attachment_metadata")
    if (not isinstance(parts, dict) or any(name not in parts for name in required)
            or any(not isinstance(part, dict) or part.get("status") not in {"complete", "partial", "unavailable"} for part in parts.values())
            or not isinstance(acquisition.get("conflicts"), list)):
        return {"success": False, "error_code": "local_json_invalid_coverage"}
    if (not isinstance(data.get("description"), str) or not isinstance(data.get("raw_fields"), dict)
            or not isinstance(data.get("custom_fields"), dict) or not isinstance(data.get("field_definitions"), list)
            or not isinstance(data.get("comments"), list) or not isinstance(data.get("attachments"), list)):
        return {"success": False, "error_code": "local_json_invalid_fields"}
    comments = data["comments"]
    if any(not isinstance(item, dict) or not isinstance(item.get("id"), str) or not item["id"]
           or not isinstance(item.get("body"), str) for item in comments):
        return {"success": False, "error_code": "local_json_invalid_comments"}
    ids = [item["id"] for item in comments]
    coverage = parts["comments"]
    if (len(ids) != len(set(ids)) or coverage.get("collected") != len(comments)
            or (coverage["status"] == "complete" and coverage.get("reported_total") != len(comments))
            or parts["custom_fields"].get("collected") != len(data["custom_fields"])):
        return {"success": False, "error_code": "local_json_invalid_coverage"}
    if any(not isinstance(item, dict) or not isinstance(item.get("filename"), str)
           or type(item.get("size")) is not int or item["size"] < 0 for item in data["attachments"]):
        return {"success": False, "error_code": "local_json_invalid_attachments"}
    digest = packet.get("issue_markdown_sha256")
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        return {"success": False, "error_code": "local_json_invalid_markdown_digest"}
    documents = [{"path": "issue.json", "sha256": sha256_bytes(raw), "url": data.get("url")}]
    markdown = bundle / "issue.md"
    if markdown.exists() or markdown.is_symlink():
        if markdown.is_symlink() or not markdown.is_file() or markdown.stat().st_size > limit:
            return {"success": False, "error_code": "local_markdown_unsafe_or_invalid"}
        try:
            actual = sha256_file(markdown, limit)
        except OSError:
            return {"success": False, "error_code": "local_issue_unreadable"}
        if actual != digest:
            return {"success": False, "error_code": "local_snapshot_pair_mismatch"}
        documents.append({"path": "issue.md", "sha256": actual, "url": data.get("url")})

    data = copy.deepcopy(data)
    acquisition = data["acquisition"]
    parts = acquisition["parts"]
    conflicts = acquisition["conflicts"]
    records, attachment_part = scan_folder(bundle, "attachments", limit)
    uploads, upload_part = scan_folder(bundle, "uploads", limit)
    by_path = {item["local_path"]: item for item in records}
    used = set()
    for item in data["attachments"]:
        local_path = item.get("local_path")
        item.update(url=item.get("content_url"), source="local_bundle", available=False, sha256=None)
        if not local_path:
            conflicts.append({"kind": "attachment_unavailable", "id": item.get("id"),
                              "filename": item["filename"], "error": item.get("download_error")})
            continue
        if not isinstance(local_path, str) or not safe_attachment_path(local_path):
            conflicts.append({"kind": "invalid_attachment_path", "path": local_path})
            item["local_path"] = None
            continue
        record = by_path.get(local_path)
        if record is None:
            conflicts.append({"kind": "declared_attachment_missing", "path": local_path})
            continue
        used.add(local_path)
        item.update(available=True, sha256=record["sha256"], content_check=record["content_check"])
        if item["size"] != record["size"]:
            conflicts.append({"kind": "attachment_size_mismatch", "path": local_path,
                              "declared": item["size"], "actual": record["size"]})
    data["local_only_attachments"] = [attachment_record(item) for item in records if item["local_path"] not in used]
    for item in data["local_only_attachments"]:
        conflicts.append({"kind": "unlisted_attachment_file", "path": item["local_path"]})
    data["uploads"] = []
    for record in uploads:
        item = attachment_record(record)
        item.update(id=item["id"].replace("local-", "upload-", 1), source="user_upload")
        data["uploads"].append(item)
    for person in ("assignee", "reporter"):
        value = data.get(person)
        if isinstance(value, dict):
            data[person] = value.get("display_name") or value.get("name")
    for item in data["comments"]:
        item.setdefault("body_raw", item["body"])
    parts.update(snapshot_json={"status": "complete"}, attachment_files=attachment_part, uploads=upload_part)
    acquisition["origin_source"] = acquisition.get("source")
    acquisition["source"] = "local_bundle"
    acquisition["loaded_at"] = datetime.now(timezone.utc).isoformat()
    complete = acquisition["status"] == "complete" and all(part["status"] == "complete" for part in parts.values()) and not conflicts
    acquisition["status"] = "complete" if complete else "partial"
    data["source_documents"] = documents
    return {"success": True, "completeness": acquisition["status"], "data": data}


def local_snapshot(issue_id: str, bundle: Path, limit: int) -> dict:
    structured = bundle / "issue.json"
    if structured.exists() or structured.is_symlink():
        return json_snapshot(issue_id, bundle, limit)
    started = datetime.now(timezone.utc).isoformat()
    issue = bundle / "issue.md"
    if not bundle.exists() or not bundle.is_dir() or issue.is_symlink() or not issue.is_file():
        return {"success": False, "error_code": "local_issue_not_found"}
    if issue.stat().st_size > limit:
        return {"success": False, "error_code": "local_issue_too_large"}
    try:
        raw = issue.read_bytes()
        text = raw.decode("utf-8")
    except (OSError, UnicodeDecodeError):
        return {"success": False, "error_code": "local_issue_unreadable"}
    title = re.search(r"^#\s+([A-Z][A-Z0-9_]*-[0-9]+)(?:\s+(.*))?$", text, flags=re.M)
    if not title or title.group(1) != issue_id:
        return {"success": False, "error_code": "local_issue_identity_mismatch"}

    lines = text.splitlines()
    _, balanced = prose_lines(lines)
    order = [section for _, section in section_headers(lines)]
    ambiguous = order != sorted(set(order))
    description, has_description = find_section(lines, {"描述", "description"})
    declared_text, has_attachments = find_section(lines, {"附件", "attachments"})
    comments_text, has_comments = find_section(lines, {"评论（按时间顺序）", "评论", "comments"})
    if description == "（无描述）":
        description = ""
    type_status = metadata(text, r"^-\s*类型：(.*?)[\t \u3000]+状态：(.*?)[\t \u3000]+优先级：(.*)$")
    project_meta = metadata(text, r"^-\s*项目：(.*?)[\t \u3000]+组件：(.*?)[\t \u3000]+标签：(.*)$")
    users = metadata(text, r"^-\s*assignee：(.*?)（(.*?)）[\t \u3000]+reporter：(.*)$")
    times = metadata(text, r"^-\s*创建：(.*?)[\t \u3000]+更新：(.*)$")
    link = metadata(text, r"^-\s*链接：(.*)$")
    attachments_dir, attachment_part = scan_folder(bundle, "attachments", limit)
    uploads_dir, upload_part = scan_folder(bundle, "uploads", limit)
    by_path = {item["local_path"]: item for item in attachments_dir}
    used = set()
    attachments = []
    conflicts = []
    if not balanced:
        conflicts.append({"kind": "unclosed_markup_block", "path": "issue.md",
                          "detail": "A code block is not closed; section or comment boundaries may be hidden."})
    if ambiguous:
        conflicts.append({"kind": "ambiguous_markdown_sections", "path": "issue.md",
                          "detail": "Snapshot section headings repeat or are out of order; use the full source text."})
    for item in parse_declared(declared_text):
        path = item["local_path"]
        if not safe_attachment_path(path):
            conflicts.append({"kind": "invalid_attachment_path", "path": path})
            continue
        record = by_path.get(path)
        if record is None:
            attachments.append({
                "id": "local-missing-" + sha256_bytes(path.encode("utf-8"))[:16],
                "filename": item["filename"], "url": None, "mime_type": None,
                "size": item["size"], "created": None, "author": None,
                "local_path": path, "sha256": None, "available": False,
                "source": "local_bundle",
            })
            conflicts.append({"kind": "declared_attachment_missing", "path": path})
            continue
        used.add(path)
        value = attachment_record(record, item["filename"])
        attachments.append(value)
        if item["size"] != record["size"]:
            conflicts.append({"kind": "attachment_size_mismatch", "path": path,
                              "declared": item["size"], "actual": record["size"]})
    for record in attachments_dir:
        if record["local_path"] in used:
            continue
        attachments.append(attachment_record(record))
        conflicts.append({"kind": "unlisted_attachment_file", "path": record["local_path"]})
    uploads = []
    for record in uploads_dir:
        value = attachment_record(record)
        value["id"] = value["id"].replace("local-", "upload-", 1)
        value["source"] = "user_upload"
        uploads.append(value)

    issue_type, status, priority = type_status if type_status else (None, None, None)
    project, components_raw, labels_raw = project_meta if project_meta else (None, None, None)
    assignee, assignee_name, reporter = users if users else (None, None, None)
    created, updated = times if times else (None, None)
    summary = (title.group(2) or "").strip()
    comments, comments_complete = parse_comments(comments_text)
    if has_comments and not comments_complete:
        conflicts.append({"kind": "unparsed_comment_text", "path": "issue.md",
                          "detail": "Comment boundaries are incomplete or text has no author/date heading; use the full source text."})
    parts = {
        "issue_markdown": {"status": "complete"},
        "description": {"status": "complete" if has_description else "unavailable"},
        "comments": {"status": "complete" if has_comments else "unavailable", "collected": len(comments)},
        "attachment_metadata": {"status": "complete" if has_attachments else "unavailable"},
        "attachment_files": attachment_part,
        "uploads": upload_part,
    }
    if ambiguous or not balanced:
        for name in ("description", "comments", "attachment_metadata"):
            if parts[name]["status"] == "complete":
                parts[name]["status"] = "partial"
    if has_comments and not comments_complete:
        parts["comments"]["status"] = "partial"
    complete = all(item["status"] == "complete" for item in parts.values()) and not conflicts
    acquisition = {
        "status": "complete" if complete else "partial",
        "started_at": started,
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "scope": "deployment_workspace_snapshot",
        "source": "local_bundle",
        "parts": parts,
        "conflicts": conflicts,
    }
    raw_fields = {
        "summary": summary,
        "description": description,
        "issuetype": {"name": issue_type} if issue_type else None,
        "status": {"name": status} if status else None,
        "priority": {"name": priority} if priority else None,
        "project": {"key": project} if project else None,
        "components": [{"name": item} for item in values(components_raw or "")],
        "labels": values(labels_raw or ""),
        "assignee": {"displayName": assignee, "name": assignee_name} if assignee or assignee_name else None,
        "reporter": {"displayName": reporter} if reporter else None,
        "created": created,
        "updated": updated,
    }
    data = {
        "key": issue_id,
        "acquisition": acquisition,
        "raw_fields": raw_fields,
        "field_definitions": [],
        "summary": summary,
        "description": description,
        "status": status,
        "assignee": assignee or assignee_name,
        "reporter": reporter,
        "priority": priority,
        "labels": values(labels_raw or ""),
        "created": created,
        "updated": updated,
        "issue_type": issue_type,
        "project": project,
        "components": values(components_raw or ""),
        "custom_fields": {},
        "comments": comments,
        "attachments": attachments,
        "uploads": uploads,
        "source_documents": [{"path": "issue.md", "sha256": sha256_bytes(raw),
                              "url": link[0] if link else None}],
    }
    if ambiguous or not balanced or not comments_complete or not all((has_description, has_attachments, has_comments)):
        data["source_documents"][0]["text"] = text
    return {"success": True, "completeness": acquisition["status"], "data": data}


def api_snapshot(issue_id: str, base_url: str, timeout: int) -> dict:
    command = [sys.executable, "-B", str(DETAILS), "--base-url", base_url,
               "--issue-id", issue_id, "--timeout", str(timeout)]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=timeout + 10)
        packet = json.loads(result.stdout)
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
        return {"success": False, "error_code": "jira_acquisition_failed"}
    if not isinstance(packet, dict):
        return {"success": False, "error_code": "jira_acquisition_invalid"}
    if packet.get("success") is False:
        return packet
    if result.returncode != 0 or not isinstance(packet.get("data"), dict):
        return {"success": False, "error_code": "jira_acquisition_invalid"}
    data = packet["data"]
    fields = data.get("raw_fields") or {}
    data.setdefault("issue_type", ((fields.get("issuetype") or {}).get("name")))
    data.setdefault("project", ((fields.get("project") or {}).get("key")))
    data.setdefault("components", [item.get("name") for item in fields.get("components") or [] if isinstance(item, dict)])
    data.setdefault("uploads", [])
    data.setdefault("source_documents", [])
    acquisition = data.setdefault("acquisition", {})
    acquisition["source"] = "jira_api"
    acquisition.setdefault("conflicts", [])
    return packet


def compare_snapshots(api: dict, local: dict) -> list[dict]:
    result = []
    for field in ("summary", "description", "status", "assignee", "reporter", "priority", "labels", "created", "updated"):
        left = api.get(field)
        right = local.get(field)
        if left in (None, "", []) or right in (None, "", []) or left == right:
            continue
        result.append({"kind": "field_mismatch", "field": field,
                       "jira_api": source_value(left), "local_bundle": source_value(right)})
    return result


def enrich_attachments(api: dict, local: dict, conflicts: list[dict]) -> None:
    available = [item for item in local.get("attachments", []) if item.get("available")]
    used = set()
    for attachment in api.get("attachments", []):
        candidates = [item for item in available if item["local_path"] not in used
                      and item.get("filename") == attachment.get("filename")
                      and (attachment.get("size") is None or item.get("size") == attachment.get("size"))]
        if not candidates:
            conflicts.append({"kind": "api_attachment_not_in_bundle",
                              "id": attachment.get("id"), "filename": attachment.get("filename")})
            continue
        match = candidates[0]
        used.add(match["local_path"])
        attachment.update({"local_path": match["local_path"], "sha256": match.get("sha256"),
                           "available": True, "source": "hybrid"})
    api["local_only_attachments"] = [item for item in available if item["local_path"] not in used]
    for item in api["local_only_attachments"]:
        conflicts.append({"kind": "local_attachment_not_in_api", "path": item["local_path"]})


def hybrid_snapshot(issue_id: str, bundle: Path, base_url: str, timeout: int, limit: int) -> dict:
    api = api_snapshot(issue_id, base_url, timeout)
    local = local_snapshot(issue_id, bundle, limit)
    if api.get("success"):
        data = copy.deepcopy(api["data"])
        conflicts = list(data.get("acquisition", {}).get("conflicts") or [])
        if local.get("success"):
            local_data = local["data"]
            conflicts.extend(local_data["acquisition"].get("conflicts") or [])
            conflicts.extend(compare_snapshots(data, local_data))
            enrich_attachments(data, local_data, conflicts)
            data["uploads"] = local_data.get("uploads", [])
            data["source_documents"] = local_data.get("source_documents", [])
            local_part = {"status": local["completeness"],
                          "source_documents": local_data.get("source_documents", []),
                          "parts": local_data["acquisition"].get("parts", {})}
        else:
            conflicts.append({"kind": "local_bundle_unavailable", "error_code": local.get("error_code")})
            local_part = {"status": "unavailable", "error_code": local.get("error_code")}
        acquisition = data["acquisition"]
        acquisition["source"] = "hybrid"
        acquisition["freshness"] = {"primary": "jira_api", "secondary": "local_bundle"}
        acquisition["parts"]["local_bundle"] = local_part
        acquisition["conflicts"] = conflicts
        complete = api.get("completeness") == "complete" and local.get("completeness") == "complete" and not conflicts
        acquisition["status"] = "complete" if complete else "partial"
        return {"success": True, "completeness": acquisition["status"], "data": data}
    if local.get("success"):
        data = copy.deepcopy(local["data"])
        acquisition = data["acquisition"]
        acquisition["source"] = "hybrid"
        acquisition["freshness"] = {"primary": "local_bundle", "jira_api": "unavailable"}
        acquisition["parts"]["jira_api"] = {"status": "unavailable", "error_code": api.get("error_code")}
        acquisition["conflicts"].append({"kind": "jira_api_unavailable", "error_code": api.get("error_code")})
        acquisition["status"] = "partial"
        return {"success": True, "completeness": "partial", "data": data}
    return {"success": False, "error_code": "all_sources_unavailable",
            "sources": {"jira_api": api.get("error_code"), "local_bundle": local.get("error_code")}}


def main() -> int:
    parser = argparse.ArgumentParser(description="统一读取 Jira REST 或部署工作目录中的工单材料")
    parser.add_argument("--issue-id", required=True, help="当前任务已确认的实际问题编号")
    parser.add_argument("--source", choices=("auto", "jira", "local", "hybrid"), default="auto")
    parser.add_argument("--bundle-dir", "--workspace", dest="bundle_dir", default=".")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--timeout", type=int, default=30)
    parser.add_argument("--max-file-bytes", type=int, default=32 * 1024 * 1024)
    args = parser.parse_args()
    if not ISSUE.fullmatch(args.issue_id) or args.timeout <= 0 or args.max_file_bytes <= 0:
        return emit({"success": False, "error_code": "invalid_arguments"})
    bundle = Path(args.bundle_dir).expanduser().resolve()
    source = args.source
    if source == "auto":
        source = "local" if any((bundle / name).exists() or (bundle / name).is_symlink()
                                for name in ("issue.json", "issue.md")) else "jira"
    if source == "local":
        return emit(local_snapshot(args.issue_id, bundle, args.max_file_bytes))
    if source == "jira":
        return emit(api_snapshot(args.issue_id, args.base_url, args.timeout))
    return emit(hybrid_snapshot(args.issue_id, bundle, args.base_url, args.timeout, args.max_file_bytes))


if __name__ == "__main__":
    raise SystemExit(main())
