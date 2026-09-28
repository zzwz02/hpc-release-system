#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import sys
from datetime import datetime
from pathlib import Path

from executor_common import (
    build_auth_config,
    approve_action,
    WRAPPER_PATH,
    UserFacingError,
    build_run_script,
    iso_now,
    load_yaml_config,
    random_suffix,
    require_command,
    run_ssh,
    scp_put,
    shell_quote,
    slugify,
    sync_workspace,
    validate_artifact_paths,
    write_json,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="提交远程异步测试任务")
    parser.add_argument("--target-dir", required=True)
    parser.add_argument("--config", required=True)
    parser.add_argument("--host")
    parser.add_argument("--port", type=int)
    parser.add_argument("--user")
    parser.add_argument("--record-dir")
    parser.add_argument("--job-label")
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    require_command("ssh")
    require_command("scp")

    target_dir = Path(args.target_dir).expanduser().resolve()
    config_path = Path(args.config).expanduser().resolve()
    config = load_yaml_config(config_path)
    approve_action("submit", config_path, target_dir=target_dir)
    artifact_paths = validate_artifact_paths(config.get("artifacts"))

    execution = config.get("execution") or {}
    sync = config.get("sync") or {}
    container = config.get("container") or {}
    test = config.get("test") or {}
    if args.host or args.port or args.user:
        raise UserFacingError("自动员工模式不接受命令行偷偷覆盖连接目标；更新 YAML 后重新批准")
    if sync.get("local_paths", ["."]) != ["."]:
        raise UserFacingError("当前完整上游原型只同步整个 target-dir；请建立精确 staging 目录，不得假称支持路径白名单")

    host = args.host or execution.get("host")
    port = args.port or execution.get("port") or 22
    user_name = args.user or execution.get("user")
    execution_target = execution.get("target")
    remote_workspace = sync.get("remote_workspace")
    test_command = test.get("command")
    host_workdir = execution.get("workdir") or remote_workspace
    use_tmux = execution.get("use_tmux", True)
    container_name = container.get("name") or ""
    container_workdir = container.get("workdir") or ""
    auth_config = build_auth_config(execution)
    if execution_target not in ("container", "host"):
        raise UserFacingError("execution.target 必须明确为 container 或 host")
    if not isinstance(remote_workspace, str) or not remote_workspace.startswith("/") or len(Path(remote_workspace).parts) < 4 or ".." in Path(remote_workspace).parts:
        raise UserFacingError("remote_workspace 必须是已批准的专用绝对任务目录，不能是根目录或共享顶层目录")

    if not host or not user_name:
        raise UserFacingError("execution.host 和 execution.user 是必填项")
    if not remote_workspace:
        raise UserFacingError("sync.remote_workspace 是必填项")
    if not test_command:
        raise UserFacingError("test.command 是必填项")
    if execution_target == "container":
        if not container_name:
            raise UserFacingError("当 execution.target=container 时，container.name 是必填项")
        if not container_workdir:
            raise UserFacingError("当 execution.target=container 时，container.workdir 是必填项")

    job_label = slugify(args.job_label or target_dir.name)
    job_id = f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{job_label}-{random_suffix()}"

    record_dir = Path(args.record_dir).expanduser().resolve() if args.record_dir else target_dir / ".remote-test-jobs"
    local_job_dir = record_dir / "jobs" / job_id
    local_job_dir.mkdir(parents=True, exist_ok=True)

    run_script_local = local_job_dir / "run.sh"
    artifacts_file_local = local_job_dir / "artifacts.txt"
    submit_json_local = local_job_dir / "submit.json"

    remote_jobs_root = f"{remote_workspace}/.remote-test-jobs/jobs"
    remote_job_dir = f"{remote_jobs_root}/{job_id}"
    remote_run_script = f"{remote_job_dir}/run.sh"
    remote_wrapper_script = f"{remote_job_dir}/wrapper.sh"
    remote_artifacts_file = f"{remote_job_dir}/artifacts.txt"
    tmux_session = f"rjob-{job_id}"

    artifacts_file_local.write_text(
        "\n".join(artifact_paths) + ("\n" if artifact_paths else ""),
        encoding="utf-8",
        newline="\n",
    )

    run_script_local.write_text(
        build_run_script(
            execution_target=execution_target,
            host_workdir=host_workdir,
            container_name=container_name,
            container_workdir=container_workdir,
            test_command=test_command,
        ),
        encoding="utf-8",
        newline="\n",
    )

    submit_payload = {
        "job_id": job_id,
        "created_at": iso_now(),
        "target_dir": str(target_dir),
        "config_path": str(config_path),
        "config_sha256": hashlib.sha256(config_path.read_bytes()).hexdigest(),
        "record_dir": str(record_dir),
        "host": host,
        "port": port,
        "user": user_name,
        "execution_target": execution_target,
        "remote_workspace": remote_workspace,
        "remote_job_dir": remote_job_dir,
        "tmux_session": tmux_session,
        "auth_mode": auth_config.mode,
        "password_env_var": auth_config.password_env_var,
        "private_key_path": auth_config.private_key_path,
    }
    write_json(submit_json_local, submit_payload)

    run_ssh(host, port, user_name, f"mkdir -p {shell_quote(remote_workspace)} {shell_quote(remote_job_dir)}", auth_config)
    transfer_cmd = sync_workspace(
        target_dir=target_dir,
        host=host,
        port=port,
        user_name=user_name,
        remote_workspace=remote_workspace,
        excludes=list(dict.fromkeys([".git", "__pycache__", ".remote-test-jobs", ".env", "auth.json", "*.pem", "*.key", *sync.get("exclude", [])])),
        auth_config=auth_config,
    )
    submit_payload["transfer_cmd"] = transfer_cmd
    write_json(submit_json_local, submit_payload)

    scp_put(host, port, user_name, run_script_local, remote_run_script, auth_config)
    scp_put(host, port, user_name, artifacts_file_local, remote_artifacts_file, auth_config)
    scp_put(host, port, user_name, WRAPPER_PATH, remote_wrapper_script, auth_config)

    run_ssh(host, port, user_name, f"chmod +x {shell_quote(remote_run_script)} {shell_quote(remote_wrapper_script)}", auth_config)

    remote_start_cmd = " ".join(
        [
            "bash",
            shell_quote(remote_wrapper_script),
            shell_quote(remote_job_dir),
            shell_quote(job_id),
            shell_quote(remote_run_script),
            shell_quote(remote_artifacts_file),
            shell_quote(remote_workspace),
        ]
    )

    if use_tmux:
        run_ssh(host, port, user_name, f"tmux new-session -d -s {shell_quote(tmux_session)} {shell_quote(remote_start_cmd)}", auth_config)
    else:
        run_ssh(host, port, user_name, remote_start_cmd, auth_config)

    print("远程任务已提交")
    print(f"job_id: {job_id}")
    print(f"host: {host}")
    print(f"port: {port}")
    print(f"execution_target: {execution_target}")
    print(f"remote_job_dir: {remote_job_dir}")
    print(f"local_job_dir: {local_job_dir}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except UserFacingError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1)
