"""Gerrit integration — git archive fetch and plan push.

Uses subprocess (blocking); Starlette puts plain `def` handlers in a thread
pool automatically, so this does not block the event loop.

Ported from server.py:1424-1461.
"""
from __future__ import annotations

import io
import logging
import shlex
import subprocess
import tarfile
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import unquote, urlsplit

from app.config import settings
from app.integrations.read_session import current_session

GERRIT_FETCH_TIMEOUT_SECONDS = 10
# Two authenticated connections, each with at most eight channels, keep a
# 16-read batch below OpenSSH's usual per-connection session limit.
READ_CONNECTIONS = 2
READ_CHANNELS = 8
logger = logging.getLogger(__name__)


def _ssh_origin(remote):
    parsed = urlsplit(remote)
    if parsed.scheme == "ssh" and parsed.hostname:
        if parsed.password is not None:
            raise RuntimeError("Gerrit SSH 地址不能包含口令字段")
        return parsed.hostname, parsed.port, unquote(parsed.username or "")
    if remote.startswith("git@") and ":" in remote:
        account, _ = remote.split(":", 1)
        user, host = account.split("@", 1)
        return host, None, user
    return None


class _ReadOrigin:
    def __init__(self, origin, directory):
        host, port, user = origin
        self.target = f"{user}@{host}" if user else host
        self.port_args = ["-p", str(port)] if port is not None else []
        self.paths = [directory / f"control-{i}" for i in range(READ_CONNECTIONS)]
        self.gates = [threading.Semaphore(READ_CHANNELS) for _ in self.paths]
        self._lock = threading.Lock()
        self._started = False
        self._error = None
        self._next = 0

    def _start(self, path):
        # A background SSH master must not inherit captured output pipes:
        # communicate() would wait until the master exits instead of returning.
        with path.with_suffix(".log").open("wb") as log:
            subprocess.run(
                ["ssh", "-o", "BatchMode=yes", "-o", "ControlMaster=yes",
                 "-o", "ControlPersist=5s", "-o", f"ControlPath={path}",
                 "-N", "-f", *self.port_args, self.target],
                stdout=subprocess.DEVNULL, stderr=log, check=True,
                timeout=GERRIT_FETCH_TIMEOUT_SECONDS,
            )

    @contextmanager
    def lease(self):
        with self._lock:
            if self._error is None and (not self._started or any(not path.exists() for path in self.paths)):
                self._started = True
                try:
                    with ThreadPoolExecutor(max_workers=READ_CONNECTIONS) as pool:
                        list(pool.map(self._start, [path for path in self.paths if not path.exists()]))
                except (OSError, subprocess.SubprocessError) as exc:
                    self._error = RuntimeError("无法建立 Gerrit SSH 读取连接")
                    self.close()
                    logger.warning("Gerrit SSH 读取连接失败：%s", type(exc).__name__)
            if self._error is not None:
                raise self._error
            slot = self._next % len(self.paths)
            self._next += 1
        with self.gates[slot]:
            yield _git_prefix(self.paths[slot])

    def close(self):
        for path in self.paths:
            if not path.exists():
                continue
            try:
                subprocess.run(
                    ["ssh", "-S", str(path), *self.port_args, "-O", "exit", self.target],
                    capture_output=True, timeout=GERRIT_FETCH_TIMEOUT_SECONDS,
                )
            except (OSError, subprocess.TimeoutExpired):
                logger.warning("关闭 Gerrit 读取连接失败")


class ReadConnections:
    """A request-owned SSH pool, isolated by host, port and SSH account."""
    def __init__(self):
        self._lock = threading.Lock()
        self._origins = {}
        self._directory = tempfile.TemporaryDirectory(prefix="release-gerrit-")

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        try:
            for origin in self._origins.values():
                origin.close()
        finally:
            self._directory.cleanup()

    @contextmanager
    def lease(self, remote):
        key = _ssh_origin(remote)
        if key is None:
            yield ["git"]
            return
        with self._lock:
            if key not in self._origins:
                directory = Path(self._directory.name) / str(len(self._origins))
                directory.mkdir()
                self._origins[key] = _ReadOrigin(key, directory)
            origin = self._origins[key]
        with origin.lease() as prefix:
            yield prefix


@contextmanager
def _read_prefix(remote):
    session = current_session()
    if session is not None:
        with session.ssh_connections().lease(remote) as prefix:
            yield prefix
    else:
        with tempfile.TemporaryDirectory(prefix="release-system-gerrit-") as directory:
            yield _git_prefix(Path(directory) / "control")


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
    remote = gerrit_remote_url(git_url)
    def read():
        with _read_prefix(remote) as prefix:
            return _ls_remote(prefix, remote, branch, project_root)
    session = current_session()
    return session.memo(("gerrit-head", remote, branch), read) if session else read()


def fetch_app_info_at(git_url: str, commit_id: str, *, project_root: str | Path) -> str:
    """Return app_info.json at *commit_id*."""
    remote = gerrit_remote_url(git_url)
    def read():
        with _read_prefix(remote) as prefix:
            return _archive_app_info(prefix, remote, commit_id, project_root)
    session = current_session()
    return session.memo(("gerrit-app-info", remote, commit_id), read) if session else read()


def fetch_files_at(git_url: str, revision: str, paths: list[str], *, project_root: str | Path) -> dict[str, bytes]:
    """Read several paths from one repository/revision in a single archive RPC."""
    if not paths or any(not path or path.startswith(("/", "-")) or any(
        part in ("", ".", "..") for part in path.split("/")
    ) for path in paths):
        raise ValueError("Gerrit 文件路径无效")
    remote = gerrit_remote_url(git_url)
    with _read_prefix(remote) as prefix:
        archive = _run_git([*prefix, "archive", f"--remote={remote}", revision, *paths], cwd=project_root)
    with tarfile.open(fileobj=io.BytesIO(archive.stdout), mode="r:*") as tar:
        result = {}
        for path in paths:
            member = tar.getmember(path)
            if not member.isfile():
                raise ValueError("Gerrit 归档路径不是普通文件")
            source = tar.extractfile(member)
            if source is None:
                raise ValueError("Gerrit 归档文件为空")
            result[path] = source.read()
        return result


def branch_heads(git_url: str, *, project_root: str | Path) -> dict[str, str]:
    """Read exact branch HEADs; this does not infer which branches contain a commit."""
    try:
        remote = gerrit_remote_url(git_url)
        with _read_prefix(remote) as prefix:
            result = _run_git([*prefix, "ls-remote", "--heads", remote], cwd=project_root)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError("无法读取 Gerrit 分支引用，不能确定历史镜像的 git_branch") from exc
    return {
        ref.removeprefix("refs/heads/"): commit
        for line in result.stdout.decode("utf-8").splitlines()
        if len(fields := line.split()) == 2
        for commit, ref in [fields]
        if ref.startswith("refs/heads/")
    }


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
    with _read_prefix(remote_url) as git_prefix:
        commit_id = _ls_remote(git_prefix, remote_url, branch, project_root)
        return _archive_app_info(git_prefix, remote_url, commit_id, project_root), commit_id
