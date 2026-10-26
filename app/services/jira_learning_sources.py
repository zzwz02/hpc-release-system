"""Collect bounded text evidence for Jira knowledge-learning jobs."""
from __future__ import annotations

import fnmatch
import gzip
import hashlib
import io
import json
import os
import re
import subprocess
import tarfile
import tempfile
import zlib
from pathlib import Path, PurePosixPath
from urllib.parse import quote, unquote, urlparse

from app.integrations import jira

MAX_SOURCE_FILE_BYTES = 1024 * 1024
MAX_SOURCE_TOTAL_BYTES = 4 * 1024 * 1024
MAX_SOURCE_FILES = 256
MAX_ARCHIVE_BYTES = 16 * 1024 * 1024
MAX_ARCHIVE_EXPANDED_BYTES = 32 * 1024 * 1024
MAX_ARCHIVE_MEMBERS = 1024

_TEXT_SUFFIXES = {
    ".c", ".cc", ".cmake", ".cpp", ".csv", ".cu", ".cuh", ".diff", ".h", ".hpp",
    ".command", ".err", ".exit", ".out", ".tsv",
    ".ini", ".json", ".log", ".md", ".patch", ".py", ".sh", ".txt",
    ".yaml", ".yml",
}
_OBSERVATION_SUFFIXES = {".csv", ".err", ".exit", ".json", ".log", ".out", ".tsv", ".txt"}
_OBSERVATION_SUFFIXES.update({'.stdout', '.stderr', '.perf', '.maps', '.time', '.cmdline'})
_BINARY_SUFFIXES = {'.png', '.jpg', '.jpeg', '.gif', '.pdf', '.so', '.a', '.o', '.bin', '.zip', '.gz', '.xz', '.bz2', '.7z', '.pyc'}
_DEPENDENCY_DIRS = {'.git', '__pycache__', 'node_modules', 'site-packages', 'third_party', 'third-party', 'vendor', 'external'}
_TEXT_MIME_TYPES = {
    "application/json", "application/x-sh", "application/xml",
    "text/csv", "text/markdown", "text/plain", "text/x-c", "text/x-c++",
    "text/x-diff", "text/x-python", "text/xml",
}


def _is_archive(name: str) -> bool:
    return name.casefold().endswith((".tar", ".tar.gz", ".tgz"))


def _is_text(name: str, mime_type: str = "") -> bool:
    return Path(name).suffix.casefold() in _TEXT_SUFFIXES | _OBSERVATION_SUFFIXES or mime_type.casefold() in _TEXT_MIME_TYPES


def _decode(data: bytes) -> str | None:
    if b"\0" in data or any(byte < 32 and byte not in (9, 10, 12, 13, 27) for byte in data):
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None


def _priority(name: str, references: set[str]) -> int:
    path = PurePosixPath(name.rsplit(' / ', 1)[-1].strip())
    if path.name.casefold() in {'report.md', 'analysis_path.md'}:
        return 0
    if any(fnmatch.fnmatchcase(path.name, pattern) for pattern in references):
        return 1
    if path.suffix.casefold() in _OBSERVATION_SUFFIXES or re.search(r'repro|reproducer|case|patch', path.name, re.I):
        return 2
    return 3


def _duplicate(item: dict, seen: dict) -> bool:
    # Do not turn repeated copies into independent supporting evidence.
    key = (item['kind'], item['sha256'], item['independent'])
    previous = seen.get(key)
    if previous is None:
        seen[key] = item
        return False
    previous.setdefault('copies', []).append({k: v for k, v in item.items() if k not in ('text', 'copies')})
    previous['copies'].extend(item.get('copies', []))
    return True


def _source(
    source_id: str,
    kind: str,
    name: str,
    data: bytes,
    *,
    independent: bool,
    extra: dict | None = None,
) -> dict | None:
    text = _decode(data)
    if text is None:
        return None
    return {
        "id": source_id,
        "kind": kind,
        "name": name,
        "text": text,
        "size": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "independent": independent and bool(text.strip()),
        **(extra or {}),
    }


def _archive_sources(
    source_id: str, kind: str, name: str, data: bytes, extra: dict, references: set[str],
) -> tuple[list[dict], list[dict]]:
    """Read regular text members in memory; never unpack or execute their contents."""
    sources = []
    gaps = []
    contents = {}
    archive_hash = hashlib.sha256(data).hexdigest()
    try:
        if len(data) > MAX_ARCHIVE_BYTES:
            raise ValueError("压缩包大小超过限制")
        if name.casefold().endswith((".gz", ".tgz")):
            with gzip.GzipFile(fileobj=io.BytesIO(data)) as compressed:
                data = compressed.read(MAX_ARCHIVE_EXPANDED_BYTES + 1)
        if len(data) > MAX_ARCHIVE_EXPANDED_BYTES:
            raise ValueError("压缩包展开大小超过限制")
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:") as archive:
            candidates = []
            seen = set()
            for index, member in enumerate(archive):
                if index >= MAX_ARCHIVE_MEMBERS:
                    gaps.append({"source": source_id, "name": name, "reason": "压缩包成员数量超过限制，后续成员未读取"})
                    break
                if member.isdir():
                    continue
                path = PurePosixPath(member.name)
                label = f"{source_id}!/{quote(path.as_posix(), safe='/')}"
                reason = ""
                if path.is_absolute() or ".." in path.parts or "\\" in member.name:
                    reason = "压缩包成员路径无效"
                elif not member.isfile() or member.issparse():
                    reason = "压缩包链接或特殊文件未读取"
                elif path.as_posix() in seen:
                    reason = "压缩包成员路径重复"
                elif path.suffix.casefold() in _BINARY_SUFFIXES or _is_archive(member.name):
                    reason = "非文本或嵌套压缩包未提取正文"
                elif member.size > MAX_SOURCE_FILE_BYTES:
                    reason = "文件超过单文件限制"
                seen.add(path.as_posix())
                if reason:
                    gaps.append({"source": label, "name": f"{name} / {member.name}", "reason": reason,
                                 "category": "skipped" if reason in ("非文本或嵌套压缩包未提取正文", "未引用的依赖目录文件未展开") else "unread"})
                    continue
                candidates.append(member)
            # Reports inside the archive can identify useful files inside dependency directories.
            for member in candidates:
                if PurePosixPath(member.name).name.casefold() in {'report.md', 'analysis_path.md'}:
                    with archive.extractfile(member) as stream:
                        text = _decode(stream.read(MAX_SOURCE_FILE_BYTES + 1))
                    if text:
                        references.update(re.findall(r"(?<![\w.*?\[\]-])[\w.*?\[\]-]{1,255}\.[A-Za-z0-9]{1,32}", text))
            # Referenced evidence comes first; smaller files leave room for both comparison groups.
            candidates.sort(key=lambda member: (
                _priority(member.name, references),
                member.size, member.name,
            ))
            total = 0
            for member in candidates:
                path = PurePosixPath(member.name)
                label = f"{source_id}!/{quote(path.as_posix(), safe='/')}"
                display = f"{name} / {member.name}"
                if set(path.parts).intersection(_DEPENDENCY_DIRS) and _priority(member.name, references) > 1:
                    gaps.append({'source': label, 'name': display, 'reason': '未引用的依赖目录文件未展开', 'category': 'skipped'})
                    continue
                with archive.extractfile(member) as stream:
                    content = stream.read(MAX_SOURCE_FILE_BYTES + 1)
                member_kind = "agent_analysis_path" if kind == "agent_artifact" and path.name.casefold() == "analysis_path.md" else kind
                item = _source(
                    label, member_kind, display, content,
                    independent=kind in ("owner_upload", "jira_attachment") or (
                        member_kind == "agent_artifact" and path.suffix.casefold() in _OBSERVATION_SUFFIXES
                    ),
                    extra={**extra, "archive": {"source_id": source_id, "name": name,
                                                "sha256": archive_hash, "member": member.name}},
                )
                if item is None:
                    gaps.append({"source": label, "name": display, "reason": "文件不是 UTF-8 文本", "category": "unread" if _is_text(member.name) else "skipped"})
                    continue
                if _duplicate(item, contents):
                    continue
                if len(sources) >= MAX_SOURCE_FILES or total + item['size'] > MAX_SOURCE_TOTAL_BYTES:
                    contents.pop((item['kind'], item['sha256'], item['independent']))
                    gaps.append({"source": label, "name": display, "reason": "学习文本数量或总大小超过限制"})
                    continue
                sources.append(item)
                total += item["size"]
    except (OSError, EOFError, tarfile.TarError, ValueError, zlib.error) as exc:
        gaps.append({"source": source_id, "name": name, "reason": f"压缩包未完整读取：{exc}"})
    return sources, gaps


def collect_sources(
    issue: dict,
    owner_messages: list[dict],
    agent_results: list[dict],
    files: list[dict],
) -> tuple[list[dict], list[dict]]:
    """Return bounded text sources and explicit gaps without failing the job."""
    sources: list[dict] = []
    gaps: list[dict] = []
    archives = []
    candidates = []
    pending = {}
    contents = {}
    total = 0
    reference_text = '\n'.join([issue.get('description') or '',
                               *(str(item.get('text') or '') for item in owner_messages),
                               *(json.dumps(item['result'], ensure_ascii=False) for item in agent_results)])
    references = set(re.findall(r"(?<![\w.*?\[\]-])[\w.*?\[\]-]{1,255}\.[A-Za-z0-9]{1,32}", reference_text))

    def queue_archive(source_id: str, kind: str, name: str, data: bytes, extra: dict) -> None:
        if sum(len(item[3]) for item in archives) + len(data) > MAX_ARCHIVE_BYTES:
            gaps.append({"source": source_id, "name": name, "reason": "压缩包采集总大小超过限制"})
            return
        archives.append((source_id, kind, name, data, extra))

    def add(item: dict | None, *, label: str, name: str = '') -> None:
        if item is None:
            gaps.append({"source": label, "name": name, "reason": "文件不是 UTF-8 文本",
                         "category": "unread" if _is_text(name) or _priority(name, references) < 3 else "skipped"})
            return
        if _duplicate(item, pending):
            return
        candidates.append(item)
        candidates.sort(key=lambda item: (
            0 if item['kind'] in ('agent_result', 'agent_analysis_path', 'owner_upload') else _priority(item['name'], references),
            item['size'],
        ))
        while len(candidates) > MAX_SOURCE_FILES or sum(i['size'] for i in candidates) > MAX_SOURCE_TOTAL_BYTES:
            removed = candidates.pop()
            pending.pop((removed['kind'], removed['sha256'], removed['independent']))
            gaps.append({'source': removed['id'], 'name': removed['name'], 'reason': '学习文本数量或总大小超过限制'})

    def retain(item: dict, *, label: str) -> None:
        nonlocal total
        key = (item['kind'], item['sha256'], item['independent'])
        if key in contents:
            _duplicate(item, contents)
            return
        if len(sources) >= MAX_SOURCE_FILES:
            gaps.append({"source": label, "name": item['name'], "reason": "学习文本文件数量超过限制"})
            return
        if total + item["size"] > MAX_SOURCE_TOTAL_BYTES:
            gaps.append({"source": label, "name": item['name'], "reason": "学习文本总大小超过限制"})
            return
        sources.append(item)
        contents[key] = item
        total += item["size"]

    for result in reversed(agent_results):
        data = json.dumps(result["result"], ensure_ascii=False, sort_keys=True).encode("utf-8")
        label = f"agent:result:{result['turn_id']}"
        if len(data) > MAX_SOURCE_FILE_BYTES:
            gaps.append({"source": label, "reason": "agent 结论超过单文件限制"})
            continue
        add(_source(
            label, "agent_result", f"turn-{result['turn_seq']}-result.json", data,
            independent=False,
            extra={"conversation_id": result["conversation_id"], "turn_id": result["turn_id"]},
        ), label=label, name=f"turn-{result['turn_seq']}-result.json")

    owner_files = {
        (item["conversation_id"], item["turn_id"], name)
        for item in owner_messages for name in item.get("files") or []
    }
    # Reports and referenced evidence precede unreferenced auxiliary files.
    for record in sorted(files, key=lambda item: (_priority(item['name'], references), item['size'])):
        label = f"file:{record['id']}"
        name = record["name"]
        archived = _is_archive(name)
        limit = MAX_ARCHIVE_BYTES if archived else MAX_SOURCE_FILE_BYTES
        if not archived and Path(name).suffix.casefold() in _BINARY_SUFFIXES:
            gaps.append({"source": label, "name": name, "reason": "非文本文件未提取正文", "category": "skipped"})
            continue
        if record["size"] > limit:
            gaps.append({"source": label, "name": name, "reason": "文件超过单文件限制"})
            continue
        try:
            with Path(record["local_path"]).open("rb") as stream:
                data = stream.read(limit + 1)
        except OSError as exc:
            gaps.append({"source": label, "name": name, "reason": f"本地文件不可读：{exc}"})
            continue
        if len(data) > limit:
            gaps.append({"source": label, "name": name, "reason": "文件超过单文件限制"})
            continue
        owner_file = (record["conversation_id"], record["turn_id"], name) in owner_files
        if record["source"] == "upload":
            kind = "owner_upload" if owner_file else "conversation_upload"
        elif Path(name).name.casefold() == "analysis_path.md":
            kind = "agent_analysis_path"
        else:
            kind = "agent_artifact"
        source_id = {
            "owner_upload": f"chat:file:{record['id']}",
            "conversation_upload": f"chat:file-unattributed:{record['id']}",
            "agent_analysis_path": f"agent:analysis-path:{record['id']}",
            "agent_artifact": f"agent:artifact:{record['id']}",
        }[kind]
        extra = {"conversation_id": record["conversation_id"], "turn_id": record["turn_id"]}
        if archived:
            queue_archive(source_id, kind, name, data, extra)
            continue
        add(_source(
            source_id, kind, name, data, independent=kind == "owner_upload" or (kind == "agent_artifact" and Path(name).suffix.lower() in _OBSERVATION_SUFFIXES),
            extra=extra,
        ), label=label, name=name)
        decoded = _decode(data)
        if decoded:
            references.update(re.findall(r"(?<![\w.*?\[\]-])[\w.*?\[\]-]{1,255}\.[A-Za-z0-9]{1,32}", decoded))

    for attachment in issue.get("attachments") or []:
        aid = str(attachment.get("id") or "")
        name = attachment.get("filename") or aid
        label = f"jira:attachment:{aid}"
        if not aid:
            continue
        archived = _is_archive(name)
        limit = MAX_ARCHIVE_BYTES if archived else MAX_SOURCE_FILE_BYTES
        if not archived and Path(name).suffix.casefold() in _BINARY_SUFFIXES:
            gaps.append({"source": label, "name": name, "reason": "非文本附件未提取正文", "category": "skipped"})
            continue
        if int(attachment.get("size") or 0) > limit:
            gaps.append({"source": label, "name": name, "reason": "附件超过单文件限制"})
            continue
        try:
            data = jira.download_attachment(
                attachment.get("content_url") or "", max_bytes=limit
            )
        except Exception as exc:
            gaps.append({"source": label, "name": name, "reason": f"附件读取失败：{exc}"})
            continue
        extra = {"mime_type": attachment.get("mime_type") or ""}
        if archived:
            queue_archive(label, "jira_attachment", name, data, extra)
            continue
        add(_source(
            label, "jira_attachment", name, data, independent=True,
            extra=extra,
        ), label=label, name=name)
    references.update({
        pattern for item in candidates
        for pattern in re.findall(r"(?<![\w.*?\[\]-])[\w.*?\[\]-]{1,255}\.[A-Za-z0-9]{1,32}", item["text"])
    })
    for record in archives:
        members, missing = _archive_sources(*record, references)
        gaps.extend(missing)
        for item in members:
            add(item, label=item["id"])
    for item in sorted(candidates, key=lambda item: (
        0 if item['kind'] in ('agent_result', 'agent_analysis_path', 'owner_upload') else _priority(item['name'], references),
        item['size'],
    )):
        retain(item, label=item['id'])
    return sources, gaps


def _gerrit_links(sources: list[dict]) -> list[dict]:
    """Extract bounded Gerrit change links only from independent source text."""
    from app.config import settings

    configured = urlparse(settings.gerrit_ssh_base_url)
    host = (configured.hostname or "").casefold()
    links = []
    seen = set()
    for source in sources:
        if not source.get("independent"):
            continue
        for raw in re.findall(r"https?://[^\s<>\"']+", source.get("text") or ""):
            url = raw.rstrip(".,;:)]}>")
            parsed = urlparse(url)
            if (parsed.hostname or "").casefold() != host:
                continue
            parts = [unquote(item) for item in parsed.path.split("/") if item]
            if "+" not in parts:
                continue
            marker = parts.index("+")
            project_parts = parts[1:marker] if parts[:1] == ["c"] else parts[:marker]
            tail = parts[marker + 1:]
            if not project_parts or not tail or not tail[0].isdigit():
                continue
            project = "/".join(project_parts)
            change = tail[0]
            if not (
                project == settings.gerrit_hpc_project
                or project.startswith(settings.gerrit_hpc_project.rstrip("/") + "/")
            ):
                continue
            key = (project, change)
            if key in seen:
                continue
            seen.add(key)
            links.append({
                "project": project, "change": change, "url": url,
                "evidence_id": source["id"],
            })
            if len(links) == 3:
                return links
    return links


def _fetch_gerrit_change(project: str, change: str) -> tuple[bytes, dict]:
    """Resolve and fetch one authorized HPC Gerrit change as a patch."""
    from app.config import settings
    from app.integrations.gerrit import gerrit_remote_url

    parsed = urlparse(settings.gerrit_ssh_base_url)
    if not parsed.hostname:
        raise RuntimeError("Gerrit SSH 地址无效")
    target = f"{parsed.username}@{parsed.hostname}" if parsed.username else parsed.hostname
    command = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10"]
    if parsed.port:
        command += ["-p", str(parsed.port)]
    command += [target, "gerrit", "query", "--format=JSON", "--current-patch-set", f"change:{change}"]
    query = subprocess.run(command, capture_output=True, timeout=30, check=False)
    if query.returncode != 0:
        detail = query.stderr.decode("utf-8", errors="replace")[:500].strip()
        raise RuntimeError(f"Gerrit 查询失败：{detail or query.returncode}")
    records = []
    for line in query.stdout.decode("utf-8", errors="replace").splitlines():
        try:
            item = json.loads(line)
        except ValueError:
            continue
        if isinstance(item, dict) and item.get("project"):
            records.append(item)
    record = next((item for item in records if item.get("project") == project), None)
    if record is None:
        raise RuntimeError("Gerrit change 与链接中的 HPC 项目不匹配")
    patchset = record.get("currentPatchSet") or {}
    revision = patchset.get("revision") or ""
    ref = patchset.get("ref") or revision
    if not re.fullmatch(r"[0-9a-fA-F]{40}", revision):
        raise RuntimeError("Gerrit change 没有有效的 current revision")
    if not (
        re.fullmatch(r"refs/changes/[0-9]+/[0-9]+/[0-9]+", ref)
        or ref == revision
    ):
        raise RuntimeError("Gerrit change 返回了无效的 patch set ref")
    remote = gerrit_remote_url(project)
    with tempfile.TemporaryDirectory(prefix="jira-learning-gerrit-") as directory:
        init = subprocess.run(["git", "init", "-q"], cwd=directory, capture_output=True)
        if init.returncode != 0:
            raise RuntimeError("无法创建临时代码目录")
        env = {"GIT_TERMINAL_PROMPT": "0"}
        ssh = (
            "ssh -o BatchMode=yes -o ConnectTimeout=10 "
            + (f"-p {parsed.port} " if parsed.port else "")
        ).strip()
        fetch = subprocess.run(
            ["git", "-c", f"core.sshCommand={ssh}", "fetch", "--depth=1", remote, ref],
            cwd=directory, capture_output=True, timeout=60, env={**os.environ, **env},
        )
        if fetch.returncode != 0:
            detail = fetch.stderr.decode("utf-8", errors="replace")[:500].strip()
            raise RuntimeError(f"Gerrit patch set 读取失败：{detail or fetch.returncode}")
        show = subprocess.run(
            ["git", "show", "--format=fuller", "--stat", "--patch", "--no-ext-diff", "FETCH_HEAD"],
            cwd=directory, capture_output=True, timeout=60,
        )
        if show.returncode != 0:
            detail = show.stderr.decode("utf-8", errors="replace")[:500].strip()
            raise RuntimeError(f"Gerrit patch 生成失败：{detail or show.returncode}")
        if len(show.stdout) > MAX_SOURCE_FILE_BYTES:
            raise RuntimeError("Gerrit patch 超过单文件限制")
        return show.stdout, {
            "project": project, "change": change, "revision": revision,
            "patchset": patchset.get("number") or "", "ref": ref,
        }
