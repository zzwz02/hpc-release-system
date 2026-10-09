#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import stat
import sys
import tempfile
from contextlib import ExitStack
from pathlib import Path, PurePosixPath
from uuid import uuid4

from executor_common import UserFacingError, iso_now, load_auth_config_from_submit, load_yaml_config, resolve_submit_context, run_ssh, scp_get, validate_artifact_paths

SYSTEM_LOG_NAMES = ("stdout.log", "stderr.log")


def publish_file(root: Path, relative: str, source: Path, *, replace: bool = False) -> str:
    """Publish via directory FDs; immutable evidence may only reuse identical bytes."""
    validate_artifact_paths([relative])
    if not hasattr(os, "O_NOFOLLOW") or os.open not in os.supports_dir_fd:
        raise UserFacingError("安全结果落盘需要支持 O_NOFOLLOW/dir_fd 的 POSIX 系统")
    temporary = ".fetch-" + uuid4().hex
    with ExitStack() as stack:
        try:
            directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            stack.callback(os.close, directory)
            for part in PurePosixPath(relative).parts[:-1]:
                try:
                    os.mkdir(part, 0o700, dir_fd=directory)
                except FileExistsError:
                    pass
                directory = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
                stack.callback(os.close, directory)
            descriptor = os.open(source, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            incoming = stack.enter_context(os.fdopen(descriptor, "rb"))
            if not stat.S_ISREG(os.fstat(incoming.fileno()).st_mode):
                raise UserFacingError("回收结果不是普通文件")
            descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
            try:
                digest = hashlib.sha256()
                with os.fdopen(descriptor, "wb") as output:
                    for block in iter(lambda: incoming.read(1024 * 1024), b""):
                        digest.update(block)
                        output.write(block)
                    output.flush()
                    os.fsync(output.fileno())
                name = PurePosixPath(relative).name
                if replace:
                    try:
                        metadata = os.stat(name, dir_fd=directory, follow_symlinks=False)
                    except FileNotFoundError:
                        metadata = None
                    if metadata is not None and not stat.S_ISREG(metadata.st_mode):
                        raise UserFacingError("结果缓存目标不是普通文件，拒绝替换")
                    os.replace(temporary, name, src_dir_fd=directory, dst_dir_fd=directory)
                    return digest.hexdigest()
                try:
                    os.link(temporary, name, src_dir_fd=directory, dst_dir_fd=directory, follow_symlinks=False)
                except FileExistsError:
                    descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
                    with os.fdopen(descriptor, "rb") as existing:
                        if not stat.S_ISREG(os.fstat(existing.fileno()).st_mode):
                            raise UserFacingError("已有证据不是普通文件，拒绝覆盖")
                        previous = hashlib.sha256()
                        for block in iter(lambda: existing.read(1024 * 1024), b""):
                            previous.update(block)
                    if previous.digest() != digest.digest():
                        raise UserFacingError("已有证据内容不同，拒绝覆盖；请保留旧记录并人工核对")
                return digest.hexdigest()
            finally:
                try:
                    os.unlink(temporary, dir_fd=directory)
                except FileNotFoundError:
                    pass
        except OSError as exc:
            raise UserFacingError("无法安全保存结果：检查目录/符号链接、文件类型、权限与磁盘空间") from exc


def remote_file(host, port, user_name, base: str, relative: str, auth_config) -> str:
    """Resolve remotely before SCP; metadata paths are not permission to read elsewhere."""
    validate_artifact_paths([relative])
    candidate = str(PurePosixPath(base) / relative)
    command = (
        f"test -d {shlex.quote(base)} && realpath -e -- {shlex.quote(base)}"
        f" && realpath -e -- {shlex.quote(candidate)} && test -f {shlex.quote(candidate)}"
    )
    result = run_ssh(host, port, user_name, command, auth_config, check=False)
    paths = result.stdout.splitlines()
    if result.returncode != 0 or len(paths) != 2:
        raise UserFacingError("远端文件缺失、不是普通文件或无法核验真实路径")
    root, resolved = (PurePosixPath(path) for path in paths)
    if not root.is_absolute() or not resolved.is_absolute() or root not in resolved.parents:
        raise UserFacingError("远端文件真实路径越出批准的目录")
    if not re.fullmatch(r"/[\w./@+-]+", str(resolved)):
        raise UserFacingError("远端真实路径含不支持的字符，拒绝不明确的 SCP 路径")
    return str(resolved)


def result_artifacts(payload: object, job_id: str, allowed: list[str]) -> list[dict]:
    if not isinstance(payload, dict) or payload.get("job_id") != job_id:
        raise UserFacingError("远端结果的 job_id 与当前任务不一致")
    artifacts = payload.get("artifacts", [])
    if not isinstance(artifacts, list) or any(not isinstance(item, dict) for item in artifacts):
        raise UserFacingError("远端 artifacts 格式无效")
    paths = validate_artifact_paths([item.get("path") for item in artifacts])
    if set(paths) != set(allowed) or any(type(item.get("exists")) is not bool for item in artifacts):
        raise UserFacingError("远端产物声明与批准配置不一致，或 exists 不是布尔值")
    return artifacts


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="获取远程任务结果")
    parser.add_argument("--job-dir")
    parser.add_argument("--submit-json")
    parser.add_argument("--skip-artifacts", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    context = resolve_submit_context(args.job_dir, args.submit_json)
    data = context.submit_data

    job_id = data["job_id"]
    host = data["host"]
    port = int(data.get("port", 22))
    user_name = data["user"]
    remote_job_dir = data["remote_job_dir"]
    remote_workspace = data["remote_workspace"]
    execution_target = data["execution_target"]
    auth_config = load_auth_config_from_submit(data)

    cache_file = context.job_dir / "result.cache.json"
    artifacts_dir = context.job_dir / "artifacts"
    system_logs: list[dict[str, object]] = []

    remote_result_file = remote_file(host, port, user_name, remote_job_dir, "result.json", auth_config)
    response = run_ssh(host, port, user_name, f"cat {shlex.quote(remote_result_file)}", auth_config, check=False)
    if response.returncode != 0:
        raise UserFacingError("无法读取远程结果文件")
    try:
        result_payload = json.loads(response.stdout)
    except ValueError as exc:
        raise UserFacingError("远端结果不是有效 JSON") from exc
    if not isinstance(result_payload, dict) or result_payload.get("job_id") != job_id:
        raise UserFacingError("远端结果的 job_id 与当前任务不一致")
    config = load_yaml_config(Path(data["config_path"]))
    allowed = validate_artifact_paths(config.get("artifacts")) if not args.skip_artifacts else []
    artifacts = result_artifacts(result_payload, job_id, allowed) if not args.skip_artifacts else []
    if args.skip_artifacts and isinstance(result_payload.get("artifacts"), list):
        for artifact in result_payload["artifacts"]:
            if isinstance(artifact, dict):
                artifact.pop("sha256", None)
                artifact["downloaded"] = False
                artifact["error"] = "本次明确跳过产物回收"

    def download(base: str, relative: str, destination: str) -> str:
        remote = remote_file(host, port, user_name, base, relative, auth_config)
        with tempfile.TemporaryDirectory(prefix="remote-result-") as temporary:
            incoming = Path(temporary) / "payload"
            result = scp_get(host, port, user_name, remote, incoming, auth_config, check=False)
            if result.returncode != 0:
                raise UserFacingError("结果下载失败，未发布本次文件；可重试收集，不要重跑业务")
            return publish_file(context.job_dir, destination, incoming)

    for log_name in SYSTEM_LOG_NAMES:
        remote_log_path = f"{remote_job_dir}/{log_name}"
        entry = {"name": log_name, "remote_path": remote_log_path, "downloaded": False}
        try:
            entry["sha256"] = download(remote_job_dir, log_name, log_name)
            entry["downloaded"] = True
        except UserFacingError as exc:
            entry["error"] = str(exc)
        system_logs.append(entry)
    result_payload.update(
        {
            "fetched_at": iso_now(),
            "host": host,
            "port": port,
            "execution_target": execution_target,
            "remote_job_dir": remote_job_dir,
            "remote_workspace": remote_workspace,
            "system_logs": system_logs,
        }
    )

    downloaded_count = 0
    if not args.skip_artifacts:
        for artifact in artifacts:
            artifact.pop("sha256", None)
            artifact.pop("error", None)
            artifact["downloaded"] = False
            try:
                if not artifact["exists"]:
                    raise UserFacingError("声明的产物不存在，回收不完整")
                artifact["sha256"] = download(remote_workspace, artifact["path"], "artifacts/" + artifact["path"])
                artifact["downloaded"] = True
                downloaded_count += 1
            except UserFacingError as exc:
                artifact["error"] = str(exc)

    complete = all(item["downloaded"] for item in system_logs) and all(item["downloaded"] for item in artifacts)
    result_payload["collection"] = {"complete": complete, "artifacts_skipped": args.skip_artifacts}
    with tempfile.TemporaryDirectory(prefix="remote-result-cache-") as temporary:
        source = Path(temporary) / "cache"
        source.write_text(json.dumps(result_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        publish_file(context.job_dir, "result.cache.json", source, replace=True)

    print("远程任务结果")
    print(f"job_id: {job_id}")
    print(f"host: {host}")
    print(f"port: {port}")
    print(f"execution_target: {execution_target}")
    print(f"state: {result_payload.get('state', '')}")
    print(f"exit_code: {result_payload.get('exit_code', '')}")
    print(f"summary: {result_payload.get('summary', '')}")
    print(f"result_cache: {cache_file}")
    print(f"downloaded_system_logs: {sum(1 for item in system_logs if item.get('downloaded'))}")
    if not args.skip_artifacts:
        print(f"artifacts_dir: {artifacts_dir}")
        print(f"downloaded_artifacts: {downloaded_count}")
    print(f"collection_complete: {complete}")
    return 0 if complete else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except UserFacingError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1)
