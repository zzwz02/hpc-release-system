#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import shlex
import sys
from pathlib import Path

from executor_common import UserFacingError, approve_action, iso_now, load_auth_config_from_submit, resolve_submit_context, run_ssh, write_json


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="取消远程任务")
    parser.add_argument("--job-dir")
    parser.add_argument("--submit-json")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    context = resolve_submit_context(args.job_dir, args.submit_json)
    data = context.submit_data
    approve_action("cancel", Path(data["config_path"]), job_id=data["job_id"])

    job_id = data["job_id"]
    host = data["host"]
    port = int(data.get("port", 22))
    user_name = data["user"]
    remote_job_dir = data["remote_job_dir"]
    tmux_session = data.get("tmux_session", "")
    remote_meta_file = f"{remote_job_dir}/meta.json"
    auth_config = load_auth_config_from_submit(data)

    remote_status_file = f"{remote_job_dir}/status.json"
    cache_file = context.job_dir / "cancel.cache.json"
    canceled_at = iso_now()

    cancel_sent = "false"
    tmux_state = "unknown"
    wrapper_pid = ""

    remote_meta = run_ssh(host, port, user_name, f"cat {shlex.quote(remote_meta_file)}", auth_config, check=False).stdout.strip()
    if remote_meta:
        try:
            wrapper_pid = str(json.loads(remote_meta).get("wrapper_pid", "")).strip()
        except json.JSONDecodeError:
            wrapper_pid = ""

    if wrapper_pid and wrapper_pid.isdigit():
        kill_result = run_ssh(host, port, user_name, f"kill -TERM -- -{wrapper_pid}", auth_config, check=False)
        if kill_result.returncode == 0:
            cancel_sent = "true"

    if tmux_session:
        has_session = run_ssh(host, port, user_name, f"tmux has-session -t {shlex.quote(tmux_session)}", auth_config, check=False)
        if has_session.returncode == 0:
            run_ssh(host, port, user_name, f"tmux kill-session -t {shlex.quote(tmux_session)}", auth_config, check=False)
            cancel_sent = "true"
            has_after = run_ssh(host, port, user_name, f"tmux has-session -t {shlex.quote(tmux_session)}", auth_config, check=False)
            tmux_state = "still_running" if has_after.returncode == 0 else "killed"
        else:
            tmux_state = "missing"

    remote_status = run_ssh(host, port, user_name, f"cat {shlex.quote(remote_status_file)}", auth_config, check=False).stdout.strip()
    if remote_status:
        cache_payload = json.loads(remote_status)
        cache_payload["cancel_request"] = {
            "job_id": job_id,
            "host": host,
            "remote_job_dir": remote_job_dir,
            "canceled_at": canceled_at,
            "tmux_session": tmux_session,
            "tmux_state": tmux_state,
            "cancel_sent": cancel_sent,
        }
    else:
        cache_payload = {
            "job_id": job_id,
            "state": "cancel_requested",
            "host": host,
            "remote_job_dir": remote_job_dir,
            "canceled_at": canceled_at,
            "tmux_session": tmux_session,
            "tmux_state": tmux_state,
            "cancel_sent": cancel_sent,
        }
    write_json(cache_file, cache_payload)

    print("已尝试取消远程任务；此原型没有后代进程/容器作业停机证明，不能据此启动下一员工")
    print(f"job_id: {job_id}")
    print(f"host: {host}")
    print(f"port: {port}")
    print(f"tmux_state: {tmux_state}")
    print(f"cancel_sent: {cancel_sent}")
    print(f"state: {cache_payload.get('state', '')}")
    print(f"cancel_cache: {cache_file}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except UserFacingError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1)
