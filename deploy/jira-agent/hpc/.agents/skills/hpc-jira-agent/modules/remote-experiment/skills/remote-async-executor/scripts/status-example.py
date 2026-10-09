#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import shlex
import sys

from executor_common import UserFacingError, iso_now, load_auth_config_from_submit, parse_heartbeat, resolve_submit_context, run_ssh, write_json


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="查询远程任务状态")
    parser.add_argument("--job-dir")
    parser.add_argument("--submit-json")
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
    execution_target = data["execution_target"]
    tmux_session = data.get("tmux_session", "")
    auth_config = load_auth_config_from_submit(data)

    remote_status_file = f"{remote_job_dir}/status.json"
    remote_heartbeat_file = f"{remote_job_dir}/heartbeat.txt"
    cache_file = context.job_dir / "status.cache.json"

    status_result = run_ssh(host, port, user_name, f"cat {shlex.quote(remote_status_file)}", auth_config, check=False)
    status_text = status_result.stdout.strip()
    if not status_text:
        raise UserFacingError(f"未找到远程状态文件: {remote_status_file}")

    heartbeat_result = run_ssh(host, port, user_name, f"cat {shlex.quote(remote_heartbeat_file)}", auth_config, check=False)
    heartbeat_raw = heartbeat_result.stdout.strip()
    heartbeat_state, heartbeat_age_seconds = parse_heartbeat(heartbeat_raw)

    tmux_active = "unknown"
    if tmux_session:
        tmux_result = run_ssh(host, port, user_name, f"tmux has-session -t {shlex.quote(tmux_session)}", auth_config, check=False)
        tmux_active = "true" if tmux_result.returncode == 0 else "false"

    status_payload = json.loads(status_text)
    status_payload.update(
        {
            "fetched_at": iso_now(),
            "host": host,
            "port": port,
            "execution_target": execution_target,
            "remote_job_dir": remote_job_dir,
            "heartbeat": {
                "raw": heartbeat_raw,
                "state": heartbeat_state,
                "age_seconds": heartbeat_age_seconds,
            },
            "tmux": {
                "session": tmux_session,
                "active": tmux_active,
            },
        }
    )
    write_json(cache_file, status_payload)

    print("远程任务状态")
    print(f"job_id: {job_id}")
    print(f"host: {host}")
    print(f"port: {port}")
    print(f"execution_target: {execution_target}")
    print(f"state: {status_payload.get('state', '')}")
    print(f"current_stage: {status_payload.get('current_stage', '')}")
    print(f"progress: {status_payload.get('progress', '')}")
    print(f"heartbeat_state: {heartbeat_state}")
    if heartbeat_age_seconds is not None:
        print(f"heartbeat_age_seconds: {heartbeat_age_seconds}")
    print(f"tmux_active: {tmux_active}")
    print(f"status_cache: {cache_file}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except UserFacingError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1)
