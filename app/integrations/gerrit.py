"""Gerrit integration — git archive fetch and plan push.

Uses subprocess (blocking); Starlette puts plain `def` handlers in a thread
pool automatically, so this does not block the event loop.

Ported from server.py:1424-1461.
"""
from __future__ import annotations

import io
import shlex
import subprocess
import tarfile
import tempfile
from pathlib import Path

from app.config import settings

GERRIT_FETCH_TIMEOUT_SECONDS = 10


def gerrit_remote_url(
    git_url: str,
) -> str:
    """Resolve a (possibly short) git_url to a full Gerrit remote URL.

    Mirrors server.py:gerrit_remote_url.
    """
    git_url = (git_url or "").strip()
    if git_url.startswith(("ssh://", "http://", "https://", "git@")):
        return git_url
    project = git_url.lstrip("/")
    prefix = settings.hpc_gerrit_prefix
    root = settings.hpc_gerrit_root
    hpc_project = settings.gerrit_hpc_project.strip("/")
    if project.startswith(f"{hpc_project}/"):
        return f"{root}{project}"
    return f"{prefix}{project}"


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


def _git_prefix(control_path: Path) -> list[str]:
    ssh_command = (
        "ssh -o ControlMaster=auto -o ControlPersist=5s "
        f"-o ControlPath={shlex.quote(str(control_path))}"
    )
    return ["git", "-c", f"core.sshCommand={ssh_command}"]


def _ls_remote(git_prefix: list[str], remote_url: str, branch: str, project_root: str | Path) -> str:
    try:
        ref = _run_git([*git_prefix, "ls-remote", remote_url, branch], cwd=project_root)
        line = ref.stdout.decode("utf-8", errors="replace").splitlines()[0]
        return line.split()[0]
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, IndexError) as exc:
        raise RuntimeError(f"无法获取 Gerrit commit id: {exc}") from exc


def _archive_app_info(git_prefix: list[str], remote_url: str, commit_id: str, project_root: str | Path) -> str:
    try:
        archive = _run_git(
            [*git_prefix, "archive", f"--remote={remote_url}", commit_id, "app_info.json"],
            cwd=project_root,
            timeout=GERRIT_FETCH_TIMEOUT_SECONDS,
        )
        with tarfile.open(fileobj=io.BytesIO(archive.stdout), mode="r:*") as tar:
            member = tar.getmember("app_info.json")
            extracted = tar.extractfile(member)
            if not extracted:
                raise RuntimeError("archive 中 app_info.json 为空")
            return extracted.read().decode("utf-8")
    except (
        subprocess.CalledProcessError,
        subprocess.TimeoutExpired,
        tarfile.TarError,
        KeyError,
        UnicodeDecodeError,
    ) as exc:
        raise RuntimeError(f"无法从 Gerrit 拉取 app_info.json: {exc}") from exc


def head_commit(git_url: str, branch: str, *, project_root: str | Path) -> str:
    """Return the commit id at the head of *branch* (``git ls-remote``)."""
    if not git_url or not branch:
        raise RuntimeError("Gerrit URL 和 branch 不能为空")
    with tempfile.TemporaryDirectory(prefix="release-system-gerrit-") as temp_dir:
        return _ls_remote(_git_prefix(Path(temp_dir) / "control"), gerrit_remote_url(git_url), branch, project_root)


def fetch_app_info_at(git_url: str, commit_id: str, *, project_root: str | Path) -> str:
    """Return app_info.json at *commit_id*."""
    with tempfile.TemporaryDirectory(prefix="release-system-gerrit-") as temp_dir:
        return _archive_app_info(
            _git_prefix(Path(temp_dir) / "control"), gerrit_remote_url(git_url), commit_id, project_root
        )


def fetch_app_info(
    git_url: str,
    branch: str,
    *,
    project_root: str | Path,
) -> tuple[str, str]:
    """Fetch app_info.json from Gerrit; return (raw_json, commit_id).

    Gerrit's JGit ``git-upload-archive`` does not preserve Git's PAX commit
    header, so the exact commit id still requires ``ls-remote`` before the
    archive.  Both Git commands use the same short-lived OpenSSH ControlMaster
    socket: one SSH connection/handshake, two channels, and the archive remains
    pinned to the resolved commit even if the branch advances concurrently.
    """
    if not git_url or not branch:
        raise RuntimeError("Gerrit URL 和 branch 不能为空")
    remote_url = gerrit_remote_url(git_url)
    with tempfile.TemporaryDirectory(prefix="release-system-gerrit-") as temp_dir:
        git_prefix = _git_prefix(Path(temp_dir) / "control")
        commit_id = _ls_remote(git_prefix, remote_url, branch, project_root)
        return _archive_app_info(git_prefix, remote_url, commit_id, project_root), commit_id
