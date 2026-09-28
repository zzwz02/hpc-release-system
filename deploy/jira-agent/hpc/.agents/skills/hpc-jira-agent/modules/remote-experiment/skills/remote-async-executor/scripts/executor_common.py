#!/usr/bin/env python3
from __future__ import annotations

import json
import hashlib
import os
import re
import secrets
import shlex
import shutil
import string
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from getpass import getpass
from pathlib import Path, PurePosixPath
from typing import Any, Iterator

try:
    import yaml  # type: ignore
except ImportError:  # pragma: no cover - dependency may be absent in local env
    yaml = None


class UserFacingError(RuntimeError):
    pass


SCRIPTS_DIR = Path(__file__).resolve().parent
WRAPPER_PATH = SCRIPTS_DIR / "wrapper.sh"


@dataclass
class SubmitContext:
    job_dir: Path
    submit_json: Path
    submit_data: dict[str, Any]


@dataclass
class AuthConfig:
    mode: str
    password: str | None = None
    password_env_var: str | None = None
    private_key_path: str | None = None
    known_hosts_path: str | None = None


@dataclass
class AuthRuntime:
    env: dict[str, str] | None
    ssh_options: list[str]
    rsync_ssh_command: str


def require_command(name: str) -> None:
    if shutil.which(name) is None:
        raise UserFacingError(f"缺少必需命令: {name}")


def require_yaml_support() -> None:
    if yaml is None:
        raise UserFacingError("缺少 Python 依赖: PyYAML。请先执行 `python -m pip install -r remote-async-executor/requirements.txt`。")


def load_yaml_config(path: Path) -> dict[str, Any]:
    require_yaml_support()
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise UserFacingError(f"配置文件格式无效: {path}")
    return data


def validate_artifact_paths(value: object) -> list[str]:
    """Only literal, unique workspace-relative files can be approved for collection."""
    if value is None:
        return []
    if not isinstance(value, list):
        raise UserFacingError("artifacts 必须是相对文件路径列表")
    paths: list[str] = []
    for item in value:
        if not isinstance(item, str) or not re.fullmatch(r"[\w./@+-]+", item):
            raise UserFacingError("artifact 仅支持字面相对路径：字母、数字、下划线及 ./@+-，不接受空白、控制符或通配符")
        path = PurePosixPath(item)
        if path.is_absolute() or str(path) != item or any(part in ("", ".", "..") for part in item.split("/")):
            raise UserFacingError("artifact 路径不能为绝对路径、空路径或包含 . / .. 分段")
        if any(path == PurePosixPath(old) or path in PurePosixPath(old).parents or PurePosixPath(old) in path.parents for old in paths):
            raise UserFacingError("artifacts 存在重复或文件/目录路径冲突")
        paths.append(item)
    return paths


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def iso_now() -> str:
    return datetime.now().astimezone().strftime("%Y-%m-%dT%H:%M:%S%z")


def slugify(value: str) -> str:
    lowered = value.lower()
    slug = re.sub(r"[^a-z0-9]+", "-", lowered).strip("-")
    return slug or "job"


def random_suffix(length: int = 6) -> str:
    alphabet = string.ascii_lowercase + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


def run_command(
    args: list[str],
    *,
    check: bool = True,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        args,
        cwd=str(cwd) if cwd else None,
        env=env,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=False,
    )
    if check and completed.returncode != 0:
        message = completed.stderr.strip() or completed.stdout.strip() or "命令执行失败"
        raise UserFacingError(message)
    return completed


def ssh_target(host: str, user_name: str) -> str:
    return f"{user_name}@{host}"


def shell_quote(value: str) -> str:
    return shlex.quote(value)


def _normalize_private_key_path(path_value: str | None) -> str | None:
    if not path_value:
        return None
    return str(Path(path_value).expanduser().resolve())


def build_auth_config(execution: dict[str, Any]) -> AuthConfig:
    auth_mode = str(execution.get("auth_mode") or "ssh_key")
    password_env_var = str(execution.get("password_env_var") or "REMOTE_ASYNC_EXECUTOR_PASSWORD")
    private_key_path = _normalize_private_key_path(execution.get("private_key_path"))
    known_hosts_path = _normalize_private_key_path(execution.get("known_hosts_path"))

    if auth_mode == "ssh_key":
        return AuthConfig(mode=auth_mode, private_key_path=private_key_path, known_hosts_path=known_hosts_path)

    if auth_mode == "password_env":
        password = os.environ.get(password_env_var)
        if not password:
            raise UserFacingError(f"缺少密码环境变量: {password_env_var}")
        return AuthConfig(
            mode=auth_mode,
            password=password,
            password_env_var=password_env_var,
            private_key_path=private_key_path,
            known_hosts_path=known_hosts_path,
        )

    if auth_mode == "password_prompt":
        password = getpass("请输入远程服务器密码: ")
        if not password:
            raise UserFacingError("未输入远程服务器密码")
        return AuthConfig(
            mode=auth_mode,
            password=password,
            password_env_var=password_env_var,
            private_key_path=private_key_path,
            known_hosts_path=known_hosts_path,
        )

    if auth_mode == "password_inline":
        raise UserFacingError("本项目不接受配置内明文密码；使用 password_env 或 SSH key 引用")

    raise UserFacingError(f"不支持的认证模式: {auth_mode}")


def load_auth_config_from_submit(submit_data: dict[str, Any]) -> AuthConfig:
    config_path_value = submit_data.get("config_path")
    if not config_path_value:
        raise UserFacingError("submit.json 缺少 config_path，无法恢复认证配置")

    config_path = Path(str(config_path_value)).expanduser().resolve()
    if not config_path.is_file():
        raise UserFacingError(f"未找到配置文件: {config_path}")

    config = load_yaml_config(config_path)
    if hashlib.sha256(config_path.read_bytes()).hexdigest() != submit_data.get("config_sha256"):
        raise UserFacingError("配置已变化或旧任务没有配置指纹，先人工核对；禁止默默换目标")
    execution = config.get("execution") or {}
    for key in ("host", "user"):
        if execution.get(key) != submit_data.get(key):
            raise UserFacingError("任务目标与批准配置不一致")
    job_id = str(submit_data.get("job_id", ""))
    if not re.fullmatch(r"[0-9]{8}-[0-9]{6}-[a-z0-9-]+-[a-z0-9]{6}", job_id):
        raise UserFacingError("任务 ID 不是本执行器的有效标识，拒绝恢复任意远端路径")
    workspace = str((config.get("sync") or {}).get("remote_workspace", ""))
    expected = {
        "port": int(execution.get("port", 22)),
        "execution_target": execution.get("target"),
        "remote_workspace": workspace,
        "remote_job_dir": f"{workspace}/.remote-test-jobs/jobs/{job_id}",
        "tmux_session": f"rjob-{job_id}",
    }
    if any(submit_data.get(key) != value for key, value in expected.items()):
        raise UserFacingError("任务端口/模式/工作目录/进程会话与批准配置和任务 ID 不一致")
    return build_auth_config(execution)


def approve_action(action: str, config_path: Path, *, target_dir: Path | None = None, job_id: str | None = None) -> None:
    filename = os.environ.get("REMOTE_EXECUTION_APPROVAL_FILE")
    if not filename:
        raise UserFacingError("需要外部提供 REMOTE_EXECUTION_APPROVAL_FILE，明确本次动作、配置指纹和任务范围")
    approval = read_json(Path(filename))
    expiry = datetime.fromisoformat(approval.get("expires_at", "1970-01-01T00:00:00+00:00"))
    if expiry.tzinfo is None or expiry <= datetime.now(timezone.utc):
        raise UserFacingError("远程动作批准已过期")
    expected = {"action": action, "config_sha256": hashlib.sha256(config_path.read_bytes()).hexdigest()}
    if target_dir is not None:
        expected["target_dir"] = str(target_dir.resolve())
    if job_id is not None:
        expected["job_id"] = job_id
    if not approval.get("approval_id") or not approval.get("approved_by") or any(approval.get(key) != value for key, value in expected.items()):
        raise UserFacingError("远程动作与批准的动作/配置/目标不一致")


@contextmanager
def prepare_auth_runtime(auth_config: AuthConfig) -> Iterator[AuthRuntime]:
    ssh_options: list[str] = ["-o", "StrictHostKeyChecking=yes"]
    if auth_config.known_hosts_path:
        ssh_options.extend(["-o", f"UserKnownHostsFile={auth_config.known_hosts_path}"])
    if auth_config.private_key_path:
        ssh_options.extend(["-i", auth_config.private_key_path])

    if auth_config.mode == "ssh_key":
        yield AuthRuntime(env=None, ssh_options=ssh_options, rsync_ssh_command=_build_rsync_ssh_command(ssh_options))
        return

    password = auth_config.password or ""
    helper = _create_askpass_helper(password)
    env = os.environ.copy()
    env["SSH_ASKPASS"] = str(helper)
    env["SSH_ASKPASS_REQUIRE"] = "force"
    env["DISPLAY"] = env.get("DISPLAY") or "remote-async-executor"
    env["REMOTE_ASYNC_EXECUTOR_ASKPASS"] = password
    ssh_options.extend(
        [
            "-o",
            "PreferredAuthentications=password",
            "-o",
            "PubkeyAuthentication=no",
        ]
    )
    try:
        yield AuthRuntime(env=env, ssh_options=ssh_options, rsync_ssh_command=_build_rsync_ssh_command(ssh_options))
    finally:
        helper.unlink(missing_ok=True)
        try:
            helper.parent.rmdir()
        except OSError:
            pass


def _build_rsync_ssh_command(ssh_options: list[str]) -> str:
    return " ".join(["ssh", *[shell_quote(item) for item in ssh_options]])


def _create_askpass_helper(password: str) -> Path:
    temp_dir = Path(tempfile.mkdtemp(prefix="remote-async-executor-"))
    python_executable = sys.executable
    if os.name == "nt":
        helper = temp_dir / "askpass.cmd"
        helper.write_text(
            "\r\n".join(
                [
                    "@echo off",
                    f"\"{python_executable}\" -c \"import os,sys;sys.stdout.write(os.environ['REMOTE_ASYNC_EXECUTOR_ASKPASS'])\"",
                    "",
                ]
            ),
            encoding="utf-8",
        )
    else:
        helper = temp_dir / "askpass.sh"
        helper.write_text(
            "\n".join(
                [
                    "#!/usr/bin/env sh",
                    f"\"{python_executable}\" -c \"import os,sys;sys.stdout.write(os.environ['REMOTE_ASYNC_EXECUTOR_ASKPASS'])\"",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        helper.chmod(0o700)
    return helper


def run_ssh(
    host: str,
    port: int,
    user_name: str,
    remote_command: str,
    auth_config: AuthConfig,
    *,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    with prepare_auth_runtime(auth_config) as runtime:
        return run_command(
            ["ssh", *runtime.ssh_options, "-p", str(port), ssh_target(host, user_name), remote_command],
            check=check,
            env=runtime.env,
        )


def scp_put(
    host: str,
    port: int,
    user_name: str,
    local_path: Path,
    remote_path: str,
    auth_config: AuthConfig,
    *,
    recursive: bool = False,
) -> None:
    with prepare_auth_runtime(auth_config) as runtime:
        args = ["scp", *runtime.ssh_options, "-P", str(port)]
        if recursive:
            args.append("-r")
        args.extend([str(local_path), f"{ssh_target(host, user_name)}:{remote_path}"])
        run_command(args, env=runtime.env)


def scp_get(
    host: str,
    port: int,
    user_name: str,
    remote_path: str,
    local_path: Path,
    auth_config: AuthConfig,
    *,
    recursive: bool = False,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    with prepare_auth_runtime(auth_config) as runtime:
        args = ["scp", *runtime.ssh_options, "-P", str(port)]
        if recursive:
            args.append("-r")
        args.extend([f"{ssh_target(host, user_name)}:{remote_path}", str(local_path)])
        return run_command(args, check=check, env=runtime.env)


def sync_workspace(
    target_dir: Path,
    host: str,
    port: int,
    user_name: str,
    remote_workspace: str,
    excludes: list[str],
    auth_config: AuthConfig,
) -> str:
    if shutil.which("rsync"):
        with prepare_auth_runtime(auth_config) as runtime:
            args = [
                "rsync",
                "-az",
                "-e",
                f"{runtime.rsync_ssh_command} -p {port}",
            ]
            for pattern in excludes:
                args.extend(["--exclude", pattern])
            args.extend([f"{target_dir}{os.sep}", f"{ssh_target(host, user_name)}:{remote_workspace}/"])
            run_command(args, env=runtime.env)
            return "rsync"

    require_command("scp")
    run_ssh(host, port, user_name, f"mkdir -p {shell_quote(remote_workspace)}", auth_config)
    for child in target_dir.iterdir():
        if child.name in set(excludes):
            continue
        scp_put(host, port, user_name, child, f"{remote_workspace}/", auth_config, recursive=child.is_dir())
    return "scp"


def resolve_submit_context(job_dir_value: str | None, submit_json_value: str | None) -> SubmitContext:
    if not job_dir_value and not submit_json_value:
        raise UserFacingError("需要提供 --job-dir 或 --submit-json。")

    if job_dir_value:
        job_dir = Path(job_dir_value).expanduser().resolve()
        submit_json = job_dir / "submit.json"
    else:
        submit_json = Path(submit_json_value or "").expanduser().resolve()
        job_dir = submit_json.parent

    if not submit_json.is_file():
        raise UserFacingError(f"未找到 submit.json: {submit_json}")

    return SubmitContext(job_dir=job_dir, submit_json=submit_json, submit_data=read_json(submit_json))


def parse_heartbeat(raw: str) -> tuple[str, int | None]:
    text = raw.strip()
    if not text:
        return "missing", None
    try:
        then = datetime.strptime(text, "%Y-%m-%dT%H:%M:%S%z")
    except ValueError:
        return "unknown", None
    age_seconds = int((datetime.now(then.tzinfo) - then).total_seconds())
    return ("fresh" if age_seconds <= 60 else "stale"), age_seconds


def build_run_script(
    execution_target: str,
    host_workdir: str,
    container_name: str,
    container_workdir: str,
    test_command: str,
) -> str:
    if execution_target == "container":
        inner = f"cd {shell_quote(container_workdir)} && {test_command}"
        return "\n".join(
            [
                "#!/usr/bin/env bash",
                "set -euo pipefail",
                "",
                f"docker exec {shell_quote(container_name)} bash -lc {shell_quote(inner)}",
                "",
            ]
        )

    return "\n".join(
        [
            "#!/usr/bin/env bash",
            "set -euo pipefail",
            "",
            f"cd {shell_quote(host_workdir)}",
            test_command,
            "",
        ]
    )
