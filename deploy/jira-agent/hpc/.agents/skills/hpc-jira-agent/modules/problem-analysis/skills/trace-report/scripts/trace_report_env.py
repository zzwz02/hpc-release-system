#!/usr/bin/env python3
"""Validate trace-report env YAML and run commands in the selected target."""

from __future__ import annotations

import argparse
import errno
import json
import os
import random
import re
import shutil
import shlex
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from trace_report.env_config import (
    ACTIVE_CONFIG_REL_PATH,
    CONFIG_PATH,
    ConfigError,
    KNOWN_KEYS,
    bool_text,
    derive_run_env,
    format_config_text,
    local_active_config_path,
    load_config,
    target_mode,
)
from trace_report.env_validate import validate_config
from trace_report.errors import format_error
from trace_report.target import (
    command_for_run,
    rsync_command_prefix,
    run_checked,
    run_in_target_workdir,
    shell_join,
    ssh_command_prefix,
    target_process_env,
    target_test_command,
)

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
TEMPLATE_CONFIG_PATH = REPO_ROOT / CONFIG_PATH
SYNC_EXCLUDES = {"__pycache__"}


def emit_exports(config: dict[str, str]) -> None:
    from trace_report.env_config import KNOWN_KEYS

    for key in KNOWN_KEYS:
        if key in config:
            print(f"export {key}={shlex.quote(config[key])}")


def emit_derived_run_exports(run_dir: str) -> None:
    for key, value in derive_run_env(run_dir).items():
        print(f"export {key}={shlex.quote(value)}")

def load_bootstrap_config() -> dict[str, str]:
    return load_config(str(TEMPLATE_CONFIG_PATH))

def find_active_config(explicit: str | None = None) -> Path:
    if not explicit:
        raise ConfigError(
            "active config must be passed explicitly with "
            "`--config \"$ACTIVE_CONFIG\"`; set "
            f"`ACTIVE_CONFIG=\"$LOCAL_WORKDIR/{ACTIVE_CONFIG_REL_PATH}\"` after init-config"
        )

    candidate = Path(explicit)
    if candidate.is_file():
        return candidate
    raise ConfigError(f"active config not found: {explicit}")

def load_active_config(explicit: str | None = None) -> dict[str, str]:
    config_path = find_active_config(explicit)
    return load_config(str(config_path))

def parse_set_arg(item: str) -> tuple[str, str]:
    if "=" not in item:
        raise ConfigError(f"--set requires KEY=VALUE: {item}")
    key, value = item.split("=", 1)
    key = key.strip()
    if not key:
        raise ConfigError(f"--set requires a non-empty KEY: {item}")
    if key not in KNOWN_KEYS:
        raise ConfigError(f"unknown config key: {key}")
    return key, value

def build_init_config(set_items: list[str]) -> dict[str, str]:
    config = load_bootstrap_config()
    for item in set_items:
        key, value = parse_set_arg(item)
        config[key] = value
    return config

def emit_errors(errors: list[str]) -> None:
    for err in errors:
        print(f"[trace_report_env] error: {format_error(err)}", file=sys.stderr)

def _last_error_line(result: subprocess.CompletedProcess[str]) -> str:
    for stream in (result.stderr, result.stdout):
        if not stream:
            continue
        for line in reversed(stream.strip().splitlines()):
            clean = line.strip()
            if clean:
                return clean
    return ""

def _last_error_code(text: str) -> str | None:
    matches = re.findall(r"\[(TR-[A-Z]+-\d+)\]", text)
    return matches[-1] if matches else None

def _emit_stage_error(stage: str, message: str) -> None:
    formatted = format_error(f"collect-run failed at {stage}: {message}")
    stage_code = _last_error_code(formatted)
    cause_code = _last_error_code(message) or stage_code or "unknown"
    print(f"[trace_report_env] error: {formatted}", file=sys.stderr)
    print("[trace_report_env] workflow_stage: collect", file=sys.stderr)
    print("[trace_report_env] workflow_state: failed", file=sys.stderr)
    print(f"[trace_report_env] failed_stage: {stage}", file=sys.stderr)
    if stage_code:
        print(f"[trace_report_env] error_code: {stage_code}", file=sys.stderr)
    print(f"[trace_report_env] cause_code: {cause_code}", file=sys.stderr)
    if cause_code != "unknown":
        print("[trace_report_env] troubleshooting_lookup:", file=sys.stderr)
        print(
            f"  rg -n \"^## {cause_code}$\" -A 12 reference/09-error-troubleshooting.md",
            file=sys.stderr,
        )

def _bool_enabled(config: dict[str, str], key: str) -> bool:
    return bool_text(config, key) == "true"

def _free_gpu_ids(config: dict[str, str]) -> list[int]:
    try:
        busy_threshold = float(os.environ.get("MACA_MEM_BUSY_PCT", "3.0"))
    except ValueError as exc:
        raise ConfigError("MACA_MEM_BUSY_PCT must be a number") from exc

    outputs: dict[str, str] = {}
    for name, argv in (
        ("inventory", ["mx-smi"]),
        ("process", ["mx-smi", "--show-process"]),
        ("memory", ["mx-smi", "--show-memory"]),
    ):
        result = run_checked(
            target_test_command(config, argv),
            capture=True,
            env=target_process_env(config),
        )
        if result.returncode != 0:
            detail = _last_error_line(result) or f"exit={result.returncode}"
            raise ConfigError(f"automatic GPU selection failed during {name}: {detail}")
        outputs[name] = result.stdout or ""

    total_match = re.search(
        r"(?m)^\s*Attached GPUs\s*:\s*(\d+)\s*$",
        outputs["inventory"],
    )
    if not total_match or int(total_match.group(1)) <= 0:
        raise ConfigError("automatic GPU selection could not parse Attached GPUs from mx-smi")
    total = int(total_match.group(1))

    busy: set[int] = {
        int(match.group(1))
        for line in outputs["process"].splitlines()
        if (match := re.match(r"^\|\s*(\d+)\s+\d+\s+", line))
    }
    current_gpu: int | None = None
    for line in outputs["memory"].splitlines():
        gpu_match = re.match(r"^\s*GPU#(\d+)\b", line)
        if gpu_match:
            current_gpu = int(gpu_match.group(1))
            continue
        usage_match = re.match(
            r"^\s*vram usage\s*:\s*([0-9]+(?:\.[0-9]+)?)\s*%",
            line,
        )
        if (
            current_gpu is not None
            and usage_match
            and float(usage_match.group(1)) > busy_threshold
        ):
            busy.add(current_gpu)

    return [device for device in range(total) if device not in busy]

def _select_collect_device(config: dict[str, str]) -> tuple[str, str, bool]:
    requested = str(config.get("MACA_VISIBLE_DEVICES", "") or "").strip()
    if requested != "-1":
        return requested, requested, False

    timeout_text = str(config.get("AUTO_SELECT_DEVICE_TIMEOUT_SECONDS", "") or "").strip()
    try:
        timeout_seconds = int(timeout_text)
    except ValueError as exc:
        raise ConfigError("AUTO_SELECT_DEVICE_TIMEOUT_SECONDS must be a positive integer number of seconds") from exc
    if timeout_seconds <= 0:
        raise ConfigError("AUTO_SELECT_DEVICE_TIMEOUT_SECONDS must be a positive integer number of seconds")

    deadline = time.monotonic() + timeout_seconds
    while True:
        free_devices = _free_gpu_ids(config)
        if free_devices:
            selected = str(random.choice(free_devices))
            config["MACA_VISIBLE_DEVICES"] = selected
            print(
                f"[trace_report_env] auto-selected free GPU: {selected} "
                f"(requested MACA_VISIBLE_DEVICES=-1)",
                flush=True,
            )
            return requested, selected, True
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ConfigError(
                "automatic GPU selection timed out after "
                f"{timeout_seconds} seconds; all GPUs are busy"
            )
        sleep_seconds = min(5.0, remaining)
        print(
            "[trace_report_env] all GPUs are busy; retrying automatic selection "
            f"in {sleep_seconds:.0f} seconds",
            file=sys.stderr,
            flush=True,
        )
        time.sleep(sleep_seconds)

def _result_log_text(label: str, command: list[str], result: subprocess.CompletedProcess[str]) -> str:
    parts = [
        f"# {label}",
        "",
        f"command: {shell_join(command)}",
        f"exit: {result.returncode}",
        "",
        "## stdout",
        result.stdout or "",
        "",
        "## stderr",
        result.stderr or "",
        "",
    ]
    return "\n".join(parts)

def _stream_summary(text: str | None) -> tuple[int, int, str, str | None]:
    if not text:
        return 0, 0, "", None
    lines = text.splitlines()
    last_line = ""
    for line in reversed(lines):
        clean = line.strip()
        if clean:
            last_line = clean
            break
    return len(lines), len(text.encode("utf-8", errors="replace")), last_line, _last_error_code(text)

def _emit_result_summary(
    label: str,
    result: subprocess.CompletedProcess[str],
    *,
    log_path: str | None = None,
) -> None:
    stream = sys.stderr if result.returncode != 0 else sys.stdout
    print(f"[trace_report_env] {label}: exit={result.returncode}", file=stream)
    if log_path:
        print(f"[trace_report_env] {label} log: {log_path}", file=stream)
    for name, text in (("stdout", result.stdout), ("stderr", result.stderr)):
        line_count, byte_count, last_line, error_code = _stream_summary(text)
        if line_count == 0 and byte_count == 0:
            continue
        suffix = f" last_error_code={error_code}" if error_code else ""
        print(
            f"[trace_report_env] {label} {name}: lines={line_count} bytes={byte_count}{suffix}",
            file=stream,
        )
        if result.returncode != 0 and last_line:
            print(f"[trace_report_env] {label} last_{name}: {last_line}", file=stream)

def _emit_stage_start(stage: str, detail: str | None = None) -> None:
    message = f"[trace_report_env] stage_start: {stage}"
    if detail:
        message += f" {detail}"
    print(message, flush=True)

def _emit_stage_ok(stage: str) -> None:
    print(f"[trace_report_env] stage_ok: {stage}", flush=True)

def _emit_auth_context(config: dict[str, str]) -> None:
    mode = target_mode(config)
    print(f"[trace_report_env] target_mode: {mode}")
    if mode not in {"ssh", "ssh+docker"}:
        return
    if os.environ.get("TRACE_REPORT_SSH_PASSWORD"):
        print("[trace_report_env] auth_mode: password-env")
        print("[trace_report_env] required_env_for_next_commands: TRACE_REPORT_SSH_PASSWORD")
    else:
        print("[trace_report_env] auth_mode: system-ssh")

def _write_target_text(config: dict[str, str], path: str, content: str) -> None:
    command = [
        "python3",
        "-c",
        (
            "from pathlib import Path\n"
            "import sys\n"
            "path = Path(sys.argv[1])\n"
            "path.parent.mkdir(parents=True, exist_ok=True)\n"
            "path.write_text(sys.stdin.read(), encoding='utf-8')\n"
        ),
        path,
    ]
    with tempfile.TemporaryFile("w+", encoding="utf-8") as stdin:
        stdin.write(content)
        stdin.seek(0)
        result = run_in_target_workdir(config, command, capture=True, stdin=stdin)
    if result.returncode != 0:
        detail = _last_error_line(result) or f"exit={result.returncode}"
        print(f"[trace_report_env] warning: could not write log {path}: {detail}", file=sys.stderr)

def _write_target_command_log(
    config: dict[str, str],
    run_env: dict[str, str],
    name: str,
    label: str,
    command: list[str],
    result: subprocess.CompletedProcess[str],
) -> str:
    log_path = f"{run_env['PROFILE_RUN_DIR']}/logs/{name}"
    _write_target_text(
        config,
        log_path,
        _result_log_text(label, command, result),
    )
    return log_path

def init_local_config(config: dict[str, str], *, force: bool) -> str:
    active_config = local_active_config_path(config)
    if active_config.exists() and not force:
        raise ConfigError(
            f"active config already exists: {active_config}; "
            "use --force to overwrite it"
        )
    try:
        active_config.parent.mkdir(parents=True, exist_ok=True)
        active_config.write_text(format_config_text(config), encoding="utf-8")
    except OSError as exc:
        if exc.errno in {errno.EACCES, errno.EROFS, errno.EPERM}:
            raise ConfigError(
                "cannot write active config at "
                f"{active_config}; grant write access to LOCAL_WORKDIR or rerun with "
                "the required filesystem permission. Do not edit the repository "
                "template config as a fallback."
            ) from exc
        raise
    return str(active_config)

def _run_or_raise(argv: list[str], config: dict[str, str], *, capture: bool = False):
    result = run_checked(argv, capture=capture, env=target_process_env(config))
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip().splitlines()
        suffix = f": {detail[-1]}" if detail else ""
        raise ConfigError(f"command failed ({result.returncode}): {shell_join(argv)}{suffix}")
    return result

def _copy_local_scripts(dst: Path) -> None:
    src = SCRIPT_DIR
    if src.resolve() == dst.resolve():
        raise ConfigError(f"refuse to overwrite current skill scripts: {dst}")
    dst.mkdir(parents=True, exist_ok=True)
    for child in list(dst.iterdir()):
        shutil.rmtree(child) if child.is_dir() else child.unlink()
    for child in src.iterdir():
        if child.name in SYNC_EXCLUDES:
            continue
        target = dst / child.name
        if child.is_dir():
            shutil.copytree(child, target, ignore=shutil.ignore_patterns(*SYNC_EXCLUDES))
        else:
            shutil.copy2(child, target)

def _prune_local_sync_excludes() -> None:
    for name in SYNC_EXCLUDES:
        for path in SCRIPT_DIR.rglob(name):
            if path.is_dir():
                shutil.rmtree(path)

def _chmod_local_entrypoint(dst: Path) -> None:
    entrypoint = dst / "collect_trace_profile.sh"
    if not entrypoint.is_file():
        raise ConfigError(f"synced entrypoint is missing: {entrypoint}")
    entrypoint.chmod(entrypoint.stat().st_mode | 0o111)

def _chmod_target_entrypoint(config: dict[str, str]) -> None:
    _run_or_raise(
        command_for_run(
            config,
            ["chmod", "+x", ".trace-report/scripts/collect_trace_profile.sh"],
        ),
        config,
    )

def sync_scripts(config: dict[str, str]) -> None:
    mode = target_mode(config)
    remote_workdir = config["REMOTE_WORKDIR"]
    container = config["CONTAINER_NAME"]
    server = f"{config['SERVER_USER']}@{config['SERVER_HOST']}"

    if mode == "local":
        dst = Path(remote_workdir) / ".trace-report" / "scripts"
        _copy_local_scripts(dst)
        _chmod_local_entrypoint(dst)
        return
    if mode == "docker":
        _prune_local_sync_excludes()
        _run_or_raise(command_for_run(config, ["sh", "-lc", "rm -rf .trace-report/scripts && mkdir -p .trace-report/scripts"]), config)
        _run_or_raise(["docker", "cp", f"{SCRIPT_DIR}/.", f"{container}:{remote_workdir}/.trace-report/scripts/"], config)
        _chmod_target_entrypoint(config)
        return
    if mode == "ssh":
        _run_or_raise([*ssh_command_prefix(), server, f"rm -rf {shlex.quote(remote_workdir + '/.trace-report/scripts')} && mkdir -p {shlex.quote(remote_workdir + '/.trace-report/scripts')}"], config)
        _run_or_raise([*rsync_command_prefix(), "-a", "--delete", "--exclude", "__pycache__/", f"{SCRIPT_DIR}/", f"{server}:{remote_workdir}/.trace-report/scripts/"], config)
        _chmod_target_entrypoint(config)
        return

    remote_stage = ""
    try:
        result = _run_or_raise([*ssh_command_prefix(), server, "mktemp -d /tmp/trace-report-scripts.XXXXXX"], config, capture=True)
        remote_stage = (result.stdout or "").strip()
        _run_or_raise([*rsync_command_prefix(), "-a", "--delete", "--exclude", "__pycache__/", f"{SCRIPT_DIR}/", f"{server}:{remote_stage}/"], config)
        _run_or_raise(command_for_run(config, ["sh", "-lc", "rm -rf .trace-report/scripts && mkdir -p .trace-report/scripts"]), config)
        _run_or_raise([*ssh_command_prefix(), server, shell_join(["docker", "cp", f"{remote_stage}/.", f"{container}:{remote_workdir}/.trace-report/scripts/"])], config)
        _chmod_target_entrypoint(config)
    finally:
        if remote_stage:
            run_checked([*ssh_command_prefix(), server, f"rm -rf {shlex.quote(remote_stage)}"], env=target_process_env(config))

def sync_artifacts(config: dict[str, str], run_dir: str) -> Path:
    mode = target_mode(config)
    run_rel = run_dir.rstrip("/")
    run_path = Path(run_rel)
    if run_path.is_absolute() or ".." in run_path.parts or not run_rel.startswith("profile-artifacts/"):
        raise ConfigError("sync-artifacts --run-dir must be under profile-artifacts/")
    run_name = Path(run_rel).name
    local_parent = Path(config["LOCAL_WORKDIR"]) / "profile-artifacts"
    local_target = local_parent / run_name
    server = f"{config['SERVER_USER']}@{config['SERVER_HOST']}"
    container = config["CONTAINER_NAME"]
    remote_workdir = config["REMOTE_WORKDIR"]

    local_parent.mkdir(parents=True, exist_ok=True)
    if mode == "local":
        source = Path(remote_workdir) / run_rel
        if source.resolve() == local_target.resolve():
            return local_target
        if local_target.exists():
            shutil.rmtree(local_target)
        shutil.copytree(source, local_target)
        return local_target
    if mode == "docker":
        result = run_checked(["docker", "cp", f"{container}:{remote_workdir}/{run_rel}", str(local_parent)])
        if result.returncode != 0:
            raise ConfigError("docker artifact sync failed")
        return local_target
    if mode == "ssh":
        _run_or_raise([*rsync_command_prefix(), "-a", f"{server}:{remote_workdir}/{run_rel}", f"{local_parent}/"], config)
        return local_target

    remote_stage = ""
    try:
        result = _run_or_raise([*ssh_command_prefix(), server, "mktemp -d /tmp/trace-report-artifacts.XXXXXX"], config, capture=True)
        remote_stage = (result.stdout or "").strip()
        _run_or_raise([*ssh_command_prefix(), server, f"mkdir -p {shlex.quote(remote_stage + '/profile-artifacts')} && {shell_join(['docker', 'cp', f'{container}:{remote_workdir}/{run_rel}', f'{remote_stage}/profile-artifacts/'])}"], config)
        _run_or_raise([*rsync_command_prefix(), "-a", f"{server}:{remote_stage}/profile-artifacts/{run_name}", f"{local_parent}/"], config)
        return local_target
    finally:
        if remote_stage:
            run_checked([*ssh_command_prefix(), server, f"rm -rf {shlex.quote(remote_stage)}"], env=target_process_env(config))

def run_source_latency(config: dict[str, str], run_env: dict[str, str]) -> int:
    command = [
        "python3",
        ".trace-report/scripts/trace_profile_pipeline.py",
        "source-latency",
        "--run-dir",
        run_env["PROFILE_RUN_DIR"],
        "--tag",
        run_env["PROFILE_TAG"],
        "--source",
        config["MACA_SOURCE_FILE"],
        "--binary",
        config["MACA_LINEINFO_BINARY"],
        "--cycle-dpc-id",
        config["CYCLE_TRACE_DPC_ID"],
        "--objdump-bin",
        config["MACA_OBJDUMP_BIN"],
    ]
    llvm_objdump_bin = config.get("MACA_LLVM_OBJDUMP_BIN") or ""
    if llvm_objdump_bin:
        command.extend(["--llvm-objdump-bin", llvm_objdump_bin])

    result = run_in_target_workdir(config, command, capture=True)
    log_path = _write_target_command_log(config, run_env, "source-latency.log", "source-latency", command, result)
    _emit_result_summary("source-latency", result, log_path=log_path)
    if result.returncode != 0:
        detail = _last_error_line(result) or f"exit={result.returncode}"
        _emit_stage_error("source-latency", f"exit={result.returncode}; last error: {detail}")
        return result.returncode or 1

    required = [
        f"{run_env['PROFILE_RUN_DIR']}/analysis/source_latency_{run_env['PROFILE_TAG']}.md",
        f"{run_env['PROFILE_RUN_DIR']}/analysis/source_latency_{run_env['PROFILE_TAG']}.csv",
        f"{run_env['PROFILE_RUN_DIR']}/analysis/source_latency_{run_env['PROFILE_TAG']}.json",
    ]
    missing: list[str] = []
    for path in required:
        check = run_in_target_workdir(config, ["test", "-s", path], capture=True)
        if check.returncode != 0:
            missing.append(path)
    if missing:
        _emit_stage_error("source-latency", "missing/invalid required output: " + ", ".join(missing))
        return 1

    print(f"[trace_report_env] source-latency complete: {run_env['PROFILE_RUN_DIR']}")
    return 0

def refresh_report(config: dict[str, str], run_env: dict[str, str]) -> int:
    command = [
        "env",
        "TRACE_REPORT_REQUIRE_SOURCE_LATENCY_SUMMARY=1",
        "python3",
        ".trace-report/scripts/trace_profile_pipeline.py",
        "analyze",
        "--run-dir",
        run_env["PROFILE_RUN_DIR"],
        "--tag",
        run_env["PROFILE_TAG"],
        "--artifact-dir",
        f"{run_env['PROFILE_RUN_DIR']}/artifacts",
        "--cycle-dpc-id",
        config["CYCLE_TRACE_DPC_ID"],
        "--require-mcprofiler",
        bool_text(config, "REQUIRE_MCPROFILER"),
        "--heuristic-bound-mode",
        config["HEURISTIC_BOUND_MODE"],
    ]
    result = run_in_target_workdir(config, command, capture=True)
    log_path = _write_target_command_log(config, run_env, "refresh-report.log", "refresh-report", command, result)
    _emit_result_summary("refresh-report", result, log_path=log_path)
    if result.returncode != 0:
        detail = _last_error_line(result) or f"exit={result.returncode}"
        _emit_stage_error("refresh-report", f"exit={result.returncode}; last error: {detail}")
        return result.returncode or 1
    print(f"[trace_report_env] report refreshed: {run_env['PROFILE_RUN_DIR']}/REPORT_{run_env['PROFILE_TAG']}.md")
    return 0

def check_report(config: dict[str, str], run_env: dict[str, str], *, require_llm_followup: bool) -> int:
    command = [
        "python3",
        ".trace-report/scripts/trace_profile_pipeline.py",
        "check-report",
        "--run-dir",
        run_env["PROFILE_RUN_DIR"],
        "--tag",
        run_env["PROFILE_TAG"],
        "--require-source-latency",
        bool_text(config, "ENABLE_SOURCE_LATENCY"),
        "--require-llm-followup",
        "true" if require_llm_followup else "false",
    ]
    result = run_in_target_workdir(config, command, capture=True)
    log_path = _write_target_command_log(config, run_env, "check-report.log", "check-report", command, result)
    _emit_result_summary("check-report", result, log_path=log_path)
    if result.returncode != 0:
        detail = _last_error_line(result) or f"exit={result.returncode}"
        _emit_stage_error("check-report", f"exit={result.returncode}; last error: {detail}")
        return result.returncode or 1
    print(f"[trace_report_env] report check complete: {run_env['PROFILE_RUN_DIR']}/REPORT_{run_env['PROFILE_TAG']}.md")
    return 0

def auto_split_usecases_if_needed(config: dict[str, str], run_env: dict[str, str]) -> int:
    metrics_path = f"{run_env['PROFILE_RUN_DIR']}/analysis/metrics_all_{run_env['PROFILE_TAG']}.json"
    try:
        raw_metrics = _read_target_json(config, metrics_path)
    except ConfigError as exc:
        print(f"[trace_report_env] warning: could not inspect usecase split need: {exc}", file=sys.stderr)
        return 0
    if not isinstance(raw_metrics, dict):
        return 0
    scope = raw_metrics.get("collection_scope")
    if not isinstance(scope, dict):
        return 0
    if scope.get("mcprofiler_scope") != "per-kernel-multiple-occurrences":
        return 0
    kernel_name = str(scope.get("cycle_trace_kernel_name") or config.get("CYCLE_TRACE_KERNEL_NAME") or "")
    if not kernel_name:
        print(
            "[trace_report_env] warning: mcProfiler has multiple occurrences but CYCLE_TRACE_KERNEL_NAME is empty; "
            "skip automatic split-usecases",
            file=sys.stderr,
        )
        return 0

    command = [
        "python3",
        ".trace-report/scripts/trace_profile_pipeline.py",
        "split-usecases",
        "--run-dir",
        run_env["PROFILE_RUN_DIR"],
        "--tag",
        run_env["PROFILE_TAG"],
        "--cycle-dpc-id",
        config["CYCLE_TRACE_DPC_ID"],
        "--heuristic-bound-mode",
        config["HEURISTIC_BOUND_MODE"],
        "--require-mcprofiler",
        bool_text(config, "REQUIRE_MCPROFILER"),
        "--cycle-kernel-name",
        kernel_name,
    ]
    result = run_in_target_workdir(config, command, capture=True)
    log_path = _write_target_command_log(config, run_env, "split-usecases.log", "split-usecases", command, result)
    _emit_result_summary("split-usecases", result, log_path=log_path)
    if result.returncode != 0:
        detail = _last_error_line(result) or f"exit={result.returncode}"
        _emit_stage_error("split-usecases", f"exit={result.returncode}; last error: {detail}")
        return result.returncode or 1

    manifest_path = f"{run_env['PROFILE_RUN_DIR']}/usecases/manifest_{run_env['PROFILE_TAG']}.json"
    try:
        raw_manifest = _read_target_json(config, manifest_path)
    except ConfigError as exc:
        print(f"[trace_report_env] warning: split-usecases did not produce readable manifest: {exc}", file=sys.stderr)
        return 0
    reports = raw_manifest.get("generated_reports") if isinstance(raw_manifest, dict) else None
    if isinstance(reports, list) and reports:
        print(f"[trace_report_env] usecase split complete: {manifest_path}")
        print("[trace_report_env] final diagnosis target: usecases generated_reports")
    else:
        print(
            f"[trace_report_env] warning: split-usecases produced no generated_reports; inspect {manifest_path}",
            file=sys.stderr,
        )
    return 0

def run_usecase_source_latency(config: dict[str, str], run_env: dict[str, str]) -> int:
    manifest_path, generated_reports = _read_generated_reports(config, run_env)
    if not generated_reports:
        return 0

    for report_path in generated_reports:
        usecase_dir = str(Path(report_path).parent)
        usecase_id = Path(usecase_dir).name
        usecase_tag = _usecase_tag_from_report(report_path)
        cycle_json = f"{usecase_dir}/artifacts/c-trace_output_dpc_{config['CYCLE_TRACE_DPC_ID']}.json"
        source_command = [
            "python3",
            ".trace-report/scripts/trace_profile_pipeline.py",
            "source-latency",
            "--run-dir",
            usecase_dir,
            "--tag",
            usecase_tag,
            "--cycle-json",
            cycle_json,
            "--source",
            config["MACA_SOURCE_FILE"],
            "--binary",
            config["MACA_LINEINFO_BINARY"],
            "--cycle-dpc-id",
            config["CYCLE_TRACE_DPC_ID"],
            "--objdump-bin",
            config["MACA_OBJDUMP_BIN"],
        ]
        llvm_objdump_bin = config.get("MACA_LLVM_OBJDUMP_BIN") or ""
        if llvm_objdump_bin:
            source_command.extend(["--llvm-objdump-bin", llvm_objdump_bin])

        result = run_in_target_workdir(config, source_command, capture=True)
        log_path = _write_target_command_log(
            config,
            run_env,
            f"source-latency-{usecase_id}.log",
            f"source-latency {usecase_id}",
            source_command,
            result,
        )
        _emit_result_summary(f"source-latency {usecase_id}", result, log_path=log_path)
        if result.returncode != 0:
            detail = _last_error_line(result) or f"exit={result.returncode}"
            _emit_stage_error("usecase-source-latency", f"{usecase_id}: exit={result.returncode}; last error: {detail}")
            return result.returncode or 1

        refresh_command = [
            "env",
            "TRACE_REPORT_REQUIRE_SOURCE_LATENCY_SUMMARY=1",
            "python3",
            ".trace-report/scripts/trace_profile_pipeline.py",
            "analyze",
            "--run-dir",
            usecase_dir,
            "--tag",
            usecase_tag,
            "--artifact-dir",
            f"{usecase_dir}/artifacts",
            "--cycle-dpc-id",
            config["CYCLE_TRACE_DPC_ID"],
            "--require-mcprofiler",
            bool_text(config, "REQUIRE_MCPROFILER"),
            "--heuristic-bound-mode",
            config["HEURISTIC_BOUND_MODE"],
        ]
        result = run_in_target_workdir(config, refresh_command, capture=True)
        log_path = _write_target_command_log(
            config,
            run_env,
            f"refresh-report-{usecase_id}.log",
            f"refresh-report {usecase_id}",
            refresh_command,
            result,
        )
        _emit_result_summary(f"refresh-report {usecase_id}", result, log_path=log_path)
        if result.returncode != 0:
            detail = _last_error_line(result) or f"exit={result.returncode}"
            _emit_stage_error("usecase-refresh-report", f"{usecase_id}: exit={result.returncode}; last error: {detail}")
            return result.returncode or 1
        print(f"[trace_report_env] usecase source-latency complete: {usecase_dir}")

    print(f"[trace_report_env] usecase source-latency complete: {len(generated_reports)} reports from {manifest_path}")
    return 0

def append_diagnosis(
    config: dict[str, str],
    run_dir: str,
    diagnosis_file: str,
    *,
    check_final: bool = True,
) -> int:
    try:
        run_env = derive_run_env(run_dir)
    except ConfigError as exc:
        print(f"[trace_report_env] error: {format_error(str(exc))}", file=sys.stderr)
        return 1

    path = Path(diagnosis_file)
    if not path.is_file():
        print(f"[trace_report_env] error: {format_error(f'diagnosis file does not exist: {diagnosis_file}')}", file=sys.stderr)
        return 1

    try:
        sync_scripts(config)
    except ConfigError as exc:
        print(f"[trace_report_env] error: {format_error(str(exc))}", file=sys.stderr)
        return 1

    diagnosis_target = f"{run_env['PROFILE_RUN_DIR']}/llm_followup_diagnosis.md"
    save_command = [
        "bash",
        "-lc",
        f"mkdir -p {shlex.quote(run_env['PROFILE_RUN_DIR'])} && cat > {shlex.quote(diagnosis_target)}",
    ]
    with path.open("r", encoding="utf-8") as stdin:
        save_result = run_in_target_workdir(config, save_command, capture=True, stdin=stdin)
    save_log = _write_target_command_log(config, run_env, "save-diagnosis.log", "save-diagnosis", save_command, save_result)
    _emit_result_summary("save-diagnosis", save_result, log_path=save_log)
    if save_result.returncode != 0:
        detail = _last_error_line(save_result) or f"exit={save_result.returncode}"
        print(f"[trace_report_env] error: {format_error(f'save diagnosis failed: exit={save_result.returncode}; last error: {detail}')}", file=sys.stderr)
        return save_result.returncode or 1

    command = [
        "python3",
        ".trace-report/scripts/trace_profile_pipeline.py",
        "append-diagnosis",
        "--report",
        f"{run_env['PROFILE_RUN_DIR']}/REPORT_{run_env['PROFILE_TAG']}.md",
    ]
    with path.open("r", encoding="utf-8") as stdin:
        result = run_in_target_workdir(config, command, capture=True, stdin=stdin)
    log_path = _write_target_command_log(config, run_env, "append-diagnosis.log", "append-diagnosis", command, result)
    _emit_result_summary("append-diagnosis", result, log_path=log_path)
    if result.returncode != 0:
        detail = _last_error_line(result) or f"exit={result.returncode}"
        print(f"[trace_report_env] error: {format_error(f'append-diagnosis failed: exit={result.returncode}; last error: {detail}')}", file=sys.stderr)
        return result.returncode or 1

    if not check_final:
        return 0
    return check_report(config, run_env, require_llm_followup=True)

def _read_target_json(config: dict[str, str], path: str) -> object:
    command = [
        "python3",
        "-c",
        "import json,sys; print(json.dumps(json.load(open(sys.argv[1], encoding='utf-8')), ensure_ascii=False))",
        path,
    ]
    result = run_in_target_workdir(config, command, capture=True)
    if result.returncode != 0:
        detail = _last_error_line(result) or f"exit={result.returncode}"
        raise ConfigError(f"failed to read target JSON {path}: {detail}")
    try:
        return json.loads(result.stdout)
    except Exception as exc:
        raise ConfigError(f"failed to parse target JSON {path}: {exc}") from exc

def _target_file_exists(config: dict[str, str], path: str) -> bool:
    result = run_in_target_workdir(config, ["test", "-s", path], capture=True)
    return result.returncode == 0

def _manifest_path(run_env: dict[str, str]) -> str:
    return f"{run_env['PROFILE_RUN_DIR']}/usecases/manifest_{run_env['PROFILE_TAG']}.json"

def _read_generated_reports(config: dict[str, str], run_env: dict[str, str]) -> tuple[str, list[str]]:
    manifest_path = _manifest_path(run_env)
    if not _target_file_exists(config, manifest_path):
        return manifest_path, []
    raw_manifest = _read_target_json(config, manifest_path)
    if not isinstance(raw_manifest, dict):
        raise ConfigError(f"usecase manifest must be a JSON object: {manifest_path}")
    reports = raw_manifest.get("generated_reports", [])
    if not isinstance(reports, list):
        raise ConfigError(f"usecase manifest generated_reports must be a list: {manifest_path}")
    return manifest_path, [str(report) for report in reports if str(report)]

def summarize_workflow_stop_condition(config: dict[str, str], run_env: dict[str, str]) -> None:
    manifest_path, generated_reports = _read_generated_reports(config, run_env)
    report_path = f"{run_env['PROFILE_RUN_DIR']}/REPORT_{run_env['PROFILE_TAG']}.md"
    metrics_key_path = f"{run_env['PROFILE_RUN_DIR']}/analysis/metrics_key_{run_env['PROFILE_TAG']}.json"
    metrics_all_path = f"{run_env['PROFILE_RUN_DIR']}/analysis/metrics_all_{run_env['PROFILE_TAG']}.json"
    print("[trace_report_env] base collection complete")
    print("[trace_report_env] workflow_stage: collect")
    print(f"[trace_report_env] run_dir: {run_env['PROFILE_RUN_DIR']}")
    print(f"[trace_report_env] tag: {run_env['PROFILE_TAG']}")
    print(f"[trace_report_env] report: {report_path}")
    print(f"[trace_report_env] metrics_key: {metrics_key_path}")
    print(f"[trace_report_env] metrics_all: {metrics_all_path}")
    print("[trace_report_env] artifact_location: target")
    print("[trace_report_env] local_sync_pending: true")
    _emit_auth_context(config)
    print(f"[trace_report_env] report_target_path: {report_path}")
    print(f"[trace_report_env] metrics_key_target_path: {metrics_key_path}")
    print(f"[trace_report_env] metrics_all_target_path: {metrics_all_path}")
    print("[trace_report_env] diagnosis_read_hint:")
    print(
        "  python3 scripts/trace_report_env.py --config \"$ACTIVE_CONFIG\" run -- "
        f"sed -n '1,260p' {shlex.quote(report_path)}"
    )
    print(
        "  python3 scripts/trace_report_env.py --config \"$ACTIVE_CONFIG\" run -- "
        f"sed -n '1,220p' {shlex.quote(metrics_key_path)}"
    )
    print("[trace_report_env] workflow_state: needs_llm_followup")
    if generated_reports:
        print("[trace_report_env] final_diagnosis_target: usecases generated_reports")
        print("[trace_report_env] required_next_command:")
        print(
            "  python3 scripts/trace_report_env.py --config \"$ACTIVE_CONFIG\" finalize-run "
            f"--run-dir {shlex.quote(run_env['PROFILE_RUN_DIR'])} "
            f"--manifest {shlex.quote(manifest_path)} "
            "--diagnosis-dir <diagnosis-dir> "
            "--diagnosis-file <parent-scope-diagnosis.md>"
        )
        print("[trace_report_env] generated_reports:")
        for report in generated_reports:
            print(f"  - {report}")
        return
    print("[trace_report_env] final_diagnosis_target: main report")
    print("[trace_report_env] required_next_command:")
    print(
        "  python3 scripts/trace_report_env.py --config \"$ACTIVE_CONFIG\" finalize-run "
        f"--run-dir {shlex.quote(run_env['PROFILE_RUN_DIR'])} "
        "--diagnosis-file <diagnosis.md>"
    )

def summarize_artifact_sync_next_step(config: dict[str, str], run_dir: str) -> None:
    print("[trace_report_env] final report check complete")
    print("[trace_report_env] workflow_stage: finalize")
    print("[trace_report_env] workflow_state: needs_artifact_sync")
    _emit_auth_context(config)
    print("[trace_report_env] required_next_command:")
    print(
        "  python3 scripts/trace_report_env.py --config \"$ACTIVE_CONFIG\" sync-artifacts "
        f"--run-dir {shlex.quote(run_dir.rstrip('/'))}"
    )

def _usecase_tag_from_report(report_path: str) -> str:
    name = Path(report_path).name
    if not name.startswith("REPORT_") or not name.endswith(".md"):
        raise ConfigError(f"usecase generated report has unexpected name: {report_path}")
    return name[len("REPORT_"):-len(".md")]

def append_usecase_diagnosis(config: dict[str, str], run_dir: str, manifest: str, diagnosis_dir: str) -> int:
    try:
        run_env = derive_run_env(run_dir)
    except ConfigError as exc:
        print(f"[trace_report_env] error: {format_error(str(exc))}", file=sys.stderr)
        return 1

    diagnosis_root = Path(diagnosis_dir)
    if not diagnosis_root.is_dir():
        print(f"[trace_report_env] error: {format_error(f'diagnosis directory does not exist: {diagnosis_dir}')}", file=sys.stderr)
        return 1

    manifest_path = manifest
    if not manifest_path.startswith("/") and not manifest_path.startswith(run_dir.rstrip("/") + "/"):
        manifest_path = f"{run_dir.rstrip('/')}/{manifest_path}"

    try:
        sync_scripts(config)
        raw_manifest = _read_target_json(config, manifest_path)
    except ConfigError as exc:
        print(f"[trace_report_env] error: {format_error(str(exc))}", file=sys.stderr)
        return 1
    if not isinstance(raw_manifest, dict):
        print(f"[trace_report_env] error: {format_error(f'usecase manifest must be a JSON object: {manifest_path}')}", file=sys.stderr)
        return 1
    reports = raw_manifest.get("generated_reports", [])
    if not isinstance(reports, list) or not reports:
        print(f"[trace_report_env] error: {format_error(f'usecase manifest has no generated_reports: {manifest_path}')}", file=sys.stderr)
        return 1

    for report in reports:
        report_path = str(report)
        usecase_dir = Path(report_path).parent
        usecase_id = usecase_dir.name
        diagnosis_file = diagnosis_root / f"{usecase_id}.md"
        if not diagnosis_file.is_file():
            print(
                f"[trace_report_env] error: {format_error(f'missing usecase diagnosis file: {diagnosis_file}')}",
                file=sys.stderr,
            )
            return 1

        command = [
            "python3",
            ".trace-report/scripts/trace_profile_pipeline.py",
            "append-diagnosis",
            "--report",
            report_path,
        ]
        with diagnosis_file.open("r", encoding="utf-8") as stdin:
            result = run_in_target_workdir(config, command, capture=True, stdin=stdin)
        log_path = _write_target_command_log(
            config,
            run_env,
            f"append-diagnosis-{usecase_id}.log",
            f"append-diagnosis {usecase_id}",
            command,
            result,
        )
        _emit_result_summary(f"append-diagnosis {usecase_id}", result, log_path=log_path)
        if result.returncode != 0:
            detail = _last_error_line(result) or f"exit={result.returncode}"
            print(f"[trace_report_env] error: {format_error(f'append-usecase-diagnosis failed for {usecase_id}: exit={result.returncode}; last error: {detail}')}", file=sys.stderr)
            return result.returncode or 1

        check_command = [
            "python3",
            ".trace-report/scripts/trace_profile_pipeline.py",
            "check-report",
            "--run-dir",
            str(usecase_dir),
            "--tag",
            _usecase_tag_from_report(report_path),
            "--require-source-latency",
            "false",
            "--require-llm-followup",
            "true",
        ]
        result = run_in_target_workdir(config, check_command, capture=True)
        log_path = _write_target_command_log(
            config,
            run_env,
            f"check-report-{usecase_id}.log",
            f"check-report {usecase_id}",
            check_command,
            result,
        )
        _emit_result_summary(f"check-report {usecase_id}", result, log_path=log_path)
        if result.returncode != 0:
            detail = _last_error_line(result) or f"exit={result.returncode}"
            print(f"[trace_report_env] error: {format_error(f'usecase report check failed for {usecase_id}: exit={result.returncode}; last error: {detail}')}", file=sys.stderr)
            return result.returncode or 1

    print(f"[trace_report_env] usecase diagnosis appended: {len(reports)} reports from {manifest_path}")
    return 0

def finalize_run(args: argparse.Namespace, config: dict[str, str]) -> int:
    try:
        run_env = derive_run_env(args.run_dir)
        manifest_path, generated_reports = _read_generated_reports(config, run_env)
    except ConfigError as exc:
        print(f"[trace_report_env] error: {format_error(str(exc))}", file=sys.stderr)
        return 1

    if generated_reports:
        if not args.diagnosis_file:
            print(
                "[trace_report_env] error: "
                + format_error(
                    "finalize-run requires --diagnosis-file for the parent report when generated usecase reports exist; "
                    "final check validates both the parent REPORT_<tag>.md and all generated usecase reports"
                ),
                file=sys.stderr,
            )
            return 1
        parent_status = append_diagnosis(
            config,
            args.run_dir,
            args.diagnosis_file,
            check_final=False,
        )
        if parent_status != 0:
            return parent_status
        if not args.diagnosis_dir:
            print(
                "[trace_report_env] error: "
                + format_error(
                    "finalize-run requires --diagnosis-dir when generated usecase reports exist: "
                    + manifest_path
                ),
                file=sys.stderr,
            )
            return 1
        usecase_status = append_usecase_diagnosis(config, args.run_dir, args.manifest or manifest_path, args.diagnosis_dir)
        if usecase_status != 0:
            return usecase_status
        final_status = check_report(config, run_env, require_llm_followup=True)
        if final_status == 0:
            print(f"[trace_report_env] final_reports_checked: {1 + len(generated_reports)}")
            summarize_artifact_sync_next_step(config, args.run_dir)
        return final_status

    if not args.diagnosis_file:
        print(
            "[trace_report_env] error: "
            + format_error("finalize-run requires --diagnosis-file when no generated usecase reports exist"),
            file=sys.stderr,
        )
        return 1
    final_status = append_diagnosis(config, args.run_dir, args.diagnosis_file)
    if final_status == 0:
        print("[trace_report_env] final_reports_checked: 1")
        summarize_artifact_sync_next_step(config, args.run_dir)
    return final_status

def collect_run(args: argparse.Namespace) -> int:
    active_path: str | None = None
    config_source = "file" if args.config else "direct"
    _emit_stage_start("validate")
    try:
        if args.config:
            if args.set:
                raise ConfigError("--config and collect-run --set are mutually exclusive")
            active_path = str(find_active_config(args.config))
            config = load_config(active_path)
        else:
            config = build_init_config(args.set)
            errs = validate_config(config, check_target=False)
            if errs:
                emit_errors(errs)
                _emit_stage_error("validate", "active config initialization failed")
                return 1
            active_path = init_local_config(config, force=args.force)
            print(f"[trace_report_env] active config: {active_path}")
        print(f"[trace_report_env] config_source: {config_source}")
        if active_path:
            print(f"[trace_report_env] config_path: {active_path}")
    except ConfigError as exc:
        print(f"[trace_report_env] error: {format_error(str(exc))}", file=sys.stderr)
        _emit_stage_error("validate", str(exc))
        return 1

    try:
        run_env = derive_run_env(args.run_dir)
    except ConfigError as exc:
        print(f"[trace_report_env] error: {format_error(str(exc))}", file=sys.stderr)
        _emit_stage_error("validate", str(exc))
        return 1

    try:
        requested_device, selected_device, auto_selected_device = _select_collect_device(config)
    except ConfigError as exc:
        print(f"[trace_report_env] error: {format_error(str(exc))}", file=sys.stderr)
        _emit_stage_error("validate", str(exc))
        return 1

    errs = validate_config(config, check_target=True)
    if errs:
        emit_errors(errs)
        _emit_stage_error("validate", "preflight validation failed")
        return 1
    print(f"[trace_report_env] ok: mode={target_mode(config)}")
    print(f"[trace_report_env] requested_device: {requested_device}")
    print(f"[trace_report_env] selected_device: {selected_device}")
    _emit_auth_context(config)
    _emit_stage_ok("validate")

    _emit_stage_start("sync-scripts")
    try:
        sync_scripts(config)
    except ConfigError as exc:
        print(f"[trace_report_env] error: {format_error(str(exc))}", file=sys.stderr)
        _emit_stage_error("sync-scripts", str(exc))
        return 1
    print("[trace_report_env] synced scripts")
    _emit_stage_ok("sync-scripts")

    _emit_stage_start("create-run-dir", run_env["PROFILE_RUN_DIR"])
    mkdir_result = run_in_target_workdir(config, ["mkdir", "-p", run_env["PROFILE_RUN_DIR"]], capture=True)
    if mkdir_result.stdout or mkdir_result.stderr or mkdir_result.returncode != 0:
        _emit_result_summary("create-run-dir", mkdir_result)
    if mkdir_result.returncode != 0:
        detail = _last_error_line(mkdir_result) or f"exit={mkdir_result.returncode}"
        _emit_stage_error("create-run-dir", detail)
        return mkdir_result.returncode or 1
    _emit_stage_ok("create-run-dir")

    collect_command = [
        ".trace-report/scripts/collect_trace_profile.sh",
        "--run-dir",
        run_env["PROFILE_RUN_DIR"],
        "--tag",
        run_env["PROFILE_TAG"],
        "--exec-cmd",
        config["OP_EXEC_CMD"],
        "--device",
        config["MACA_VISIBLE_DEVICES"],
        "--requested-device",
        requested_device,
        "--auto-selected-device",
        "true" if auto_selected_device else "false",
        "--target-peu",
        config["CYCLE_TRACE_TARGET_PEU"],
        "--dpg-page-num",
        config["CYCLE_TRACE_DPG_PAGE_NUM"],
        "--target-ap",
        config["CYCLE_TRACE_TARGET_AP"],
        "--dpc-id",
        config["CYCLE_TRACE_DPC_ID"],
        "--heuristic-bound-mode",
        config["HEURISTIC_BOUND_MODE"],
        "--collect-timeout-seconds",
        config["COLLECT_TIMEOUT_SECONDS"],
        "--require-mcprofiler",
        bool_text(config, "REQUIRE_MCPROFILER"),
        "--skip-mcprofiler",
        bool_text(config, "SKIP_MCPROFILER"),
        "--enable-source-latency",
        bool_text(config, "ENABLE_SOURCE_LATENCY"),
        "--mctracer-bin",
        config["MC_TRACER_BIN"],
        "--cycle-trace-bin",
        config["CYCLE_TRACE_BIN"],
        "--mcprofiler-bin",
        config["MC_PROFILER_BIN"],
        "--profiler-port",
        config.get("PROFILER_PORT") or "50123",
    ]
    _emit_stage_start("collect", run_env["PROFILE_RUN_DIR"])
    collect_result = run_in_target_workdir(config, collect_command, capture=True)
    log_path = _write_target_command_log(config, run_env, "collect-run.log", "collect-run", collect_command, collect_result)
    _emit_result_summary("collect-run", collect_result, log_path=log_path)
    if collect_result.returncode != 0:
        detail = _last_error_line(collect_result) or f"exit={collect_result.returncode}"
        _emit_stage_error("collect", f"exit={collect_result.returncode}; last error: {detail}")
        return collect_result.returncode or 1

    print(f"[trace_report_env] collect-run complete: {run_env['PROFILE_RUN_DIR']}")
    _emit_stage_ok("collect")
    if _bool_enabled(config, "ENABLE_SOURCE_LATENCY"):
        _emit_stage_start("source-latency")
        source_latency_status = run_source_latency(config, run_env)
        if source_latency_status != 0:
            return source_latency_status
        _emit_stage_ok("source-latency")
        _emit_stage_start("refresh-report")
        refresh_status = refresh_report(config, run_env)
        if refresh_status != 0:
            return refresh_status
        _emit_stage_ok("refresh-report")
    _emit_stage_start("check-report")
    check_status = check_report(config, run_env, require_llm_followup=False)
    if check_status != 0:
        return check_status
    _emit_stage_ok("check-report")
    _emit_stage_start("split-usecases")
    split_status = auto_split_usecases_if_needed(config, run_env)
    if split_status != 0:
        return split_status
    _emit_stage_ok("split-usecases")
    if _bool_enabled(config, "ENABLE_SOURCE_LATENCY"):
        _emit_stage_start("usecase-source-latency")
        usecase_source_latency_status = run_usecase_source_latency(config, run_env)
        if usecase_source_latency_status != 0:
            return usecase_source_latency_status
        _emit_stage_ok("usecase-source-latency")
    _emit_stage_start("workflow-summary")
    try:
        summarize_workflow_stop_condition(config, run_env)
    except ConfigError as exc:
        print(f"[trace_report_env] warning: could not summarize workflow stop condition: {exc}", file=sys.stderr)
    if active_path:
        print(f"[trace_report_env] active config: {active_path}")
    _emit_stage_ok("workflow-summary")
    return 0

def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config",
        help=f"Required path to local active config, usually $LOCAL_WORKDIR/{ACTIVE_CONFIG_REL_PATH}",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    init_local_p = sub.add_parser(
        "init-config",
        help="Generate LOCAL_WORKDIR/.trace-report/config/trace_report_env.yaml from --set values",
    )
    init_local_p.add_argument("--set", action="append", default=[], metavar="KEY=VALUE", help="Override one config key; can be repeated")
    init_local_p.add_argument("--force", action="store_true", help="Overwrite an existing local active config")

    for name in ("export", "mode", "validate"):
        sub.add_parser(name)
    validate_p = sub.choices["validate"]
    validate_p.add_argument("--skip-target-checks", action="store_true")

    sub.add_parser("sync-scripts", help="Sync this skill's scripts/ directory to target .trace-report/scripts/")
    sync_artifacts_p = sub.add_parser("sync-artifacts", help="Sync one target profile-artifacts run directory back to LOCAL_WORKDIR")
    sync_artifacts_p.add_argument("--run-dir", required=True, help="Run directory under profile-artifacts/")

    append_diag_p = sub.add_parser("append-diagnosis", help="Append required LLM diagnosis from a local file and validate the final report.")
    append_diag_p.add_argument("--run-dir", required=True, help="Run directory under profile-artifacts/<kernel>_v<N>_<tag>")
    append_diag_p.add_argument("--diagnosis-file", required=True, help="Local Markdown file containing the LLM follow-up diagnosis body")
    append_usecase_diag_p = sub.add_parser("append-usecase-diagnosis", help="Append LLM diagnosis files to usecase generated reports and validate each report.")
    append_usecase_diag_p.add_argument("--run-dir", required=True, help="Parent run directory under profile-artifacts/<kernel>_v<N>_<tag>")
    append_usecase_diag_p.add_argument("--manifest", required=True, help="Usecase manifest path, absolute or relative to --run-dir")
    append_usecase_diag_p.add_argument("--diagnosis-dir", required=True, help="Local directory containing usecase_XXX.md diagnosis files")
    finalize_p = sub.add_parser("finalize-run", help="Append required LLM diagnosis to the correct final report targets and validate the workflow.")
    finalize_p.add_argument("--run-dir", required=True, help="Run directory under profile-artifacts/<kernel>_v<N>_<tag>")
    finalize_p.add_argument("--diagnosis-file", help="Local Markdown file for the main report LLM follow-up diagnosis")
    finalize_p.add_argument("--manifest", help="Usecase manifest path, absolute or relative to --run-dir; defaults to usecases/manifest_<tag>.json")
    finalize_p.add_argument("--diagnosis-dir", help="Local directory containing usecase_XXX.md diagnosis files when generated usecase reports exist")

    check_report_p = sub.add_parser("check-report", help="Validate final report workflow quality gates.")
    check_report_p.add_argument("--run-dir", required=True, help="Run directory under profile-artifacts/<kernel>_v<N>_<tag>")
    check_report_p.add_argument("--allow-missing-llm", action="store_true", help="Only for intermediate collect-run checks before required LLM diagnosis is appended")

    collect_run_p = sub.add_parser("collect-run", help="Validate, sync scripts, create run dir, and run collection")
    collect_run_p.add_argument("--run-dir", required=True, help="Run directory under profile-artifacts/<kernel>_v<N>_<tag>")
    collect_run_p.add_argument("--set", action="append", default=[], metavar="KEY=VALUE", help="Override one config key when --config is not used; can be repeated")
    collect_run_p.add_argument("--force", action="store_true", help="Overwrite an existing local active config when --config is not used")

    run_p = sub.add_parser("run")
    run_p.add_argument("target_command", nargs=argparse.REMAINDER)
    derive_p = sub.add_parser("derive-run", help="Derive PROFILE_* exports from profile-artifacts/<kernel>_v<N>_<tag>")
    derive_p.add_argument("run_dir")

    args = parser.parse_args(argv)

    try:
        if args.command == "derive-run":
            emit_derived_run_exports(args.run_dir)
            return 0
        if args.command == "collect-run":
            return collect_run(args)
        if args.command == "init-config":
            config = build_init_config(args.set)
            errs = validate_config(config, check_target=False)
            if errs:
                emit_errors(errs)
                return 1
            active_path = init_local_config(config, force=args.force)
            print(f"[trace_report_env] active config: {active_path}")
            return 0

        config = load_active_config(args.config)
        if args.command == "export":
            emit_exports(config)
            return 0
        if args.command == "mode":
            errs = validate_config(config, check_target=False)
            if errs:
                emit_errors(errs)
                return 1
            print(target_mode(config))
            return 0
        if args.command == "validate":
            errs = validate_config(config, check_target=not args.skip_target_checks)
            if errs:
                emit_errors(errs)
                return 1
            print(f"[trace_report_env] ok: mode={target_mode(config)}")
            _emit_auth_context(config)
            return 0
        if args.command == "sync-scripts":
            errs = validate_config(config, check_target=True)
            if errs:
                emit_errors(errs)
                return 1
            sync_scripts(config)
            print("[trace_report_env] synced scripts")
            return 0
        if args.command == "sync-artifacts":
            errs = validate_config(config, check_target=True)
            if errs:
                emit_errors(errs)
                return 1
            local_run_dir = sync_artifacts(config, args.run_dir)
            print("[trace_report_env] workflow_stage: sync")
            print(f"[trace_report_env] synced artifacts: {Path(args.run_dir).name}")
            print("[trace_report_env] artifact_location: local")
            print(f"[trace_report_env] local_run_dir: {local_run_dir}")
            print("[trace_report_env] workflow_state: complete")
            _emit_auth_context(config)
            return 0
        if args.command == "append-diagnosis":
            errs = validate_config(config, check_target=True)
            if errs:
                emit_errors(errs)
                return 1
            return append_diagnosis(config, args.run_dir, args.diagnosis_file)
        if args.command == "append-usecase-diagnosis":
            errs = validate_config(config, check_target=True)
            if errs:
                emit_errors(errs)
                return 1
            return append_usecase_diagnosis(config, args.run_dir, args.manifest, args.diagnosis_dir)
        if args.command == "finalize-run":
            errs = validate_config(config, check_target=True)
            if errs:
                emit_errors(errs)
                return 1
            return finalize_run(args, config)
        if args.command == "check-report":
            errs = validate_config(config, check_target=True)
            if errs:
                emit_errors(errs)
                return 1
            try:
                run_env = derive_run_env(args.run_dir)
            except ConfigError as exc:
                print(f"[trace_report_env] error: {format_error(str(exc))}", file=sys.stderr)
                return 1
            sync_scripts(config)
            status = check_report(config, run_env, require_llm_followup=not args.allow_missing_llm)
            if status == 0:
                _emit_auth_context(config)
            return status
        if args.command == "run":
            command = list(args.target_command)
            if command and command[0] == "--":
                command = command[1:]
            if not command:
                raise ConfigError("run requires a command after --")
            errs = validate_config(config, check_target=True)
            if errs:
                emit_errors(errs)
                return 1
            return run_in_target_workdir(config, command, stdin=None).returncode
    except ConfigError as exc:
        print(f"[trace_report_env] error: {format_error(str(exc))}", file=sys.stderr)
        return 1

    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
