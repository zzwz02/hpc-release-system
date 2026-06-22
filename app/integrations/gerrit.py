"""Gerrit integration — git archive fetch and plan push.

Uses subprocess (blocking); Starlette puts plain `def` handlers in a thread
pool automatically, so this does not block the event loop.

Ported from server.py:1424-1461.
"""
from __future__ import annotations

import io
import subprocess
import tarfile
from pathlib import Path

# Mirrors server.py:1428-1429
HPC_GERRIT_PREFIX = "ssh://sw-gerrit-devops.metax-internal.com:29418/PDE/HPC/"
HPC_GERRIT_ROOT = "ssh://sw-gerrit-devops.metax-internal.com:29418/"
GERRIT_FETCH_TIMEOUT_SECONDS = 10


def gerrit_remote_url(
    git_url: str,
    *,
    hpc_gerrit_prefix: str = HPC_GERRIT_PREFIX,
    hpc_gerrit_root: str = HPC_GERRIT_ROOT,
) -> str:
    """Resolve a (possibly short) git_url to a full Gerrit remote URL.

    Mirrors server.py:gerrit_remote_url.
    """
    git_url = (git_url or "").strip()
    if git_url.startswith(("ssh://", "http://", "https://", "git@")):
        return git_url
    project = git_url.lstrip("/")
    if project.startswith("PDE/HPC/"):
        return f"{hpc_gerrit_root}{project}"
    return f"{hpc_gerrit_prefix}{project}"


def _run_git(
    args: list[str],
    *,
    cwd: str | Path,
    timeout: int = GERRIT_FETCH_TIMEOUT_SECONDS,
) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        args,
        cwd=cwd,
        capture_output=True,
        timeout=timeout,
        check=True,
    )


def fetch_app_info(
    git_url: str,
    branch: str,
    *,
    project_root: str | Path,
    hpc_gerrit_prefix: str = HPC_GERRIT_PREFIX,
    hpc_gerrit_root: str = HPC_GERRIT_ROOT,
) -> tuple[str, str]:
    """Fetch app_info.json from Gerrit; return (raw_json, commit_id).

    Mirrors server.py:fetch_app_info_from_gerrit.
    """
    if not git_url or not branch:
        raise RuntimeError("Gerrit URL 和 branch 不能为空")
    remote_url = gerrit_remote_url(
        git_url,
        hpc_gerrit_prefix=hpc_gerrit_prefix,
        hpc_gerrit_root=hpc_gerrit_root,
    )
    try:
        ref = _run_git(["git", "ls-remote", remote_url, branch], cwd=project_root)
        line = ref.stdout.decode("utf-8", errors="replace").splitlines()[0]
        commit_id = line.split()[0]
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, IndexError) as exc:
        raise RuntimeError(f"无法获取 Gerrit commit id: {exc}") from exc
    try:
        archive = _run_git(
            ["git", "archive", f"--remote={remote_url}", commit_id, "app_info.json"],
            cwd=project_root,
            timeout=GERRIT_FETCH_TIMEOUT_SECONDS,
        )
        with tarfile.open(fileobj=io.BytesIO(archive.stdout), mode="r:*") as tar:
            member = tar.getmember("app_info.json")
            extracted = tar.extractfile(member)
            if not extracted:
                raise RuntimeError("archive 中 app_info.json 为空")
            return extracted.read().decode("utf-8"), commit_id
    except (
        subprocess.CalledProcessError,
        subprocess.TimeoutExpired,
        tarfile.TarError,
        KeyError,
        UnicodeDecodeError,
    ) as exc:
        raise RuntimeError(f"无法从 Gerrit 拉取 app_info.json: {exc}") from exc
