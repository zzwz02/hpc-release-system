#!/usr/bin/env python3
from __future__ import annotations

import argparse
import shlex
import sys

from executor_common import UserFacingError, iso_now, load_auth_config_from_submit, resolve_submit_context, run_ssh


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="抓取远程任务日志 tail")
    parser.add_argument("--job-dir")
    parser.add_argument("--submit-json")
    parser.add_argument("--lines", type=int, default=50)
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
    auth_config = load_auth_config_from_submit(data)

    remote_stdout_file = f"{remote_job_dir}/stdout.log"
    remote_stderr_file = f"{remote_job_dir}/stderr.log"
    latest_log = context.job_dir / "latest.log"
    fetched_at = iso_now()

    stdout_tail = run_ssh(
        host,
        port,
        user_name,
        f"test -f {shlex.quote(remote_stdout_file)} && tail -n {args.lines} {shlex.quote(remote_stdout_file)}",
        auth_config,
        check=False,
    ).stdout.rstrip()
    stderr_tail = run_ssh(
        host,
        port,
        user_name,
        f"test -f {shlex.quote(remote_stderr_file)} && tail -n {args.lines} {shlex.quote(remote_stderr_file)}",
        auth_config,
        check=False,
    ).stdout.rstrip()

    latest_log.write_text(
        "\n".join(
            [
                f"[{fetched_at}]",
                f"[job_id] {job_id}",
                f"[host] {host}",
                f"[port] {port}",
                "[stdout]",
                stdout_tail,
                "",
                "[stderr]",
                stderr_tail,
                "",
            ]
        ),
        encoding="utf-8",
    )

    print("远程任务日志")
    print(f"job_id: {job_id}")
    print(f"host: {host}")
    print(f"port: {port}")
    print(f"lines: {args.lines}")
    print(f"latest_log: {latest_log}")
    print()
    latest_text = latest_log.read_text(encoding="utf-8")
    sys.stdout.buffer.write(latest_text.encode(sys.stdout.encoding or "utf-8", errors="replace"))
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except UserFacingError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1)
