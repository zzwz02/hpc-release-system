"""Target-aware validation for trace-report environment config."""

from __future__ import annotations

import os
import re
import shlex
import shutil
from pathlib import Path

from .env_config import parse_bool, parse_exec_cmd, target_mode, value
from .target import (
    run_checked,
    run_in_target_workdir,
    shell_join,
    ssh_command_prefix,
    ssh_password_enabled,
    target_process_env,
    target_test_command,
)


def ensure_local_workdir(config: dict[str, str]) -> str | None:
    """Create the configured local archive root before validating its use."""
    local_workdir = value(config, "LOCAL_WORKDIR")
    if not local_workdir:
        return None
    try:
        Path(local_workdir).mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return f"cannot create LOCAL_WORKDIR at {local_workdir}: {exc}"
    return None


def validate_config(config: dict[str, str], *, check_target: bool) -> list[str]:
    errors: list[str] = []

    if bool(value(config, "SERVER_USER")) != bool(value(config, "SERVER_HOST")):
        errors.append("SERVER_USER and SERVER_HOST must be both set or both empty")

    if value(config, "HEURISTIC_BOUND_MODE") not in {"coarse", "detailed"}:
        errors.append("HEURISTIC_BOUND_MODE must be coarse or detailed")

    collect_timeout = value(config, "COLLECT_TIMEOUT_SECONDS")
    if not collect_timeout:
        errors.append("COLLECT_TIMEOUT_SECONDS must not be empty")
    elif not collect_timeout.isdigit() or int(collect_timeout) <= 0:
        errors.append("COLLECT_TIMEOUT_SECONDS must be a positive integer number of seconds")
    auto_select_timeout = value(config, "AUTO_SELECT_DEVICE_TIMEOUT_SECONDS")
    if not auto_select_timeout:
        errors.append("AUTO_SELECT_DEVICE_TIMEOUT_SECONDS must not be empty")
    elif not auto_select_timeout.isdigit() or int(auto_select_timeout) <= 0:
        errors.append("AUTO_SELECT_DEVICE_TIMEOUT_SECONDS must be a positive integer number of seconds")

    skip_mcprofiler = value(config, "SKIP_MCPROFILER")
    skip_mcprofiler_enabled = parse_bool(skip_mcprofiler) is True
    if skip_mcprofiler and parse_bool(skip_mcprofiler) is None:
        errors.append("SKIP_MCPROFILER must be a boolean value: true/false, yes/no, or 1/0")
    require_mcprofiler = value(config, "REQUIRE_MCPROFILER")
    require_mcprofiler_enabled = (not skip_mcprofiler_enabled) and parse_bool(require_mcprofiler) is True
    if not skip_mcprofiler_enabled and require_mcprofiler and parse_bool(require_mcprofiler) is None:
        errors.append("REQUIRE_MCPROFILER must be a boolean value: true/false, yes/no, or 1/0")
    enable_source_latency = value(config, "ENABLE_SOURCE_LATENCY")
    enable_source_latency_enabled = parse_bool(enable_source_latency) is True
    if enable_source_latency and parse_bool(enable_source_latency) is None:
        errors.append("ENABLE_SOURCE_LATENCY must be a boolean value: true/false, yes/no, or 1/0")

    if not value(config, "LOCAL_WORKDIR"):
        errors.append("LOCAL_WORKDIR must not be empty")
    else:
        local_workdir_error = ensure_local_workdir(config)
        if local_workdir_error:
            errors.append(local_workdir_error)

    if not value(config, "REMOTE_WORKDIR"):
        errors.append("REMOTE_WORKDIR must not be empty")

    if not value(config, "OP_EXEC_CMD"):
        errors.append("OP_EXEC_CMD must not be empty")
    elif parse_exec_cmd(config) is None:
        errors.append("OP_EXEC_CMD must be valid shell-style argv text")

    if not value(config, "MACA_VISIBLE_DEVICES"):
        errors.append("MACA_VISIBLE_DEVICES must not be empty")

    target_peu = value(config, "CYCLE_TRACE_TARGET_PEU")
    if not target_peu:
        errors.append("CYCLE_TRACE_TARGET_PEU must not be empty")
    elif not target_peu.isdigit():
        errors.append("CYCLE_TRACE_TARGET_PEU must be an integer from 1 to 15")
    elif not 1 <= int(target_peu) <= 15:
        errors.append("CYCLE_TRACE_TARGET_PEU must be in [1, 15]; 1 (0x1) captures PEU 0")

    dpg_page_num = value(config, "CYCLE_TRACE_DPG_PAGE_NUM")
    if not dpg_page_num:
        errors.append("CYCLE_TRACE_DPG_PAGE_NUM must not be empty")
    elif not dpg_page_num.isdigit():
        errors.append("CYCLE_TRACE_DPG_PAGE_NUM must be a positive integer")
    elif int(dpg_page_num) <= 0:
        errors.append("CYCLE_TRACE_DPG_PAGE_NUM must be greater than 0")

    target_ap = value(config, "CYCLE_TRACE_TARGET_AP")
    if not target_ap:
        errors.append("CYCLE_TRACE_TARGET_AP must not be empty")
    elif not target_ap.isdigit():
        errors.append("CYCLE_TRACE_TARGET_AP must be an integer from 0 to 15")
    elif not 0 <= int(target_ap) <= 15:
        errors.append("CYCLE_TRACE_TARGET_AP must be in [0, 15]")

    dpc_id = value(config, "CYCLE_TRACE_DPC_ID")
    if not dpc_id:
        errors.append("CYCLE_TRACE_DPC_ID must not be empty")
    elif not re.fullmatch(r"[0-7](,[0-7])*", dpc_id):
        errors.append("CYCLE_TRACE_DPC_ID must be a comma-separated list of integers from 0 to 7 with no spaces")

    sample_mode = value(config, "CYCLE_TRACE_SAMPLE_MODE")
    kernel_name = value(config, "CYCLE_TRACE_KERNEL_NAME")
    kernel_repeat = value(config, "CYCLE_TRACE_KERNEL_REPEAT")
    if sample_mode and sample_mode not in {"A", "B", "C", "D"}:
        errors.append("CYCLE_TRACE_SAMPLE_MODE must be A, B, C, or D when set")
    if kernel_name and sample_mode and sample_mode != "C":
        errors.append("CYCLE_TRACE_KERNEL_NAME requires CYCLE_TRACE_SAMPLE_MODE=C when sample mode is set")
    if kernel_repeat:
        if not re.fullmatch(r"-?[0-9]+", kernel_repeat):
            errors.append("CYCLE_TRACE_KERNEL_REPEAT must be an integer when set")
        if not kernel_name:
            errors.append("CYCLE_TRACE_KERNEL_REPEAT requires CYCLE_TRACE_KERNEL_NAME")

    source_file = value(config, "MACA_SOURCE_FILE")
    lineinfo_binary = value(config, "MACA_LINEINFO_BINARY")
    lineinfo_cycle_trace_exec_cmd = value(config, "MACA_LINEINFO_CYCLE_TRACE_EXEC_CMD")
    effective_lineinfo_cycle_trace_exec_cmd = (
        lineinfo_cycle_trace_exec_cmd or lineinfo_binary if enable_source_latency_enabled else ""
    )
    lineinfo_cycle_trace_exec_argv: list[str] = []
    objdump_bin = value(config, "MACA_OBJDUMP_BIN")
    objdump_args = value(config, "MACA_OBJDUMP_ARGS")
    llvm_objdump_bin = value(config, "MACA_LLVM_OBJDUMP_BIN")
    if objdump_args:
        try:
            shlex.split(objdump_args)
        except ValueError as exc:
            errors.append(f"MACA_OBJDUMP_ARGS must be valid shell-style argv text: {exc}")
    if enable_source_latency_enabled:
        if not source_file:
            errors.append("MACA_SOURCE_FILE must not be empty when ENABLE_SOURCE_LATENCY=true")
        if not lineinfo_binary:
            errors.append("MACA_LINEINFO_BINARY must not be empty when ENABLE_SOURCE_LATENCY=true")
    if enable_source_latency_enabled and effective_lineinfo_cycle_trace_exec_cmd:
        try:
            lineinfo_cycle_trace_exec_argv = shlex.split(effective_lineinfo_cycle_trace_exec_cmd)
        except ValueError as exc:
            errors.append(f"MACA_LINEINFO_CYCLE_TRACE_EXEC_CMD must be valid shell-style argv text: {exc}")
        else:
            if not lineinfo_cycle_trace_exec_argv:
                errors.append("MACA_LINEINFO_CYCLE_TRACE_EXEC_CMD did not produce an executable")

    for key in ("MC_TRACER_BIN", "CYCLE_TRACE_BIN"):
        if not value(config, key):
            errors.append(f"{key} must not be empty")
    if require_mcprofiler_enabled and not value(config, "MC_PROFILER_BIN"):
        errors.append("MC_PROFILER_BIN must not be empty when REQUIRE_MCPROFILER=true")
    if not skip_mcprofiler_enabled:
        profiler_port = value(config, "PROFILER_PORT") or "50123"
        if not profiler_port.isdigit() or not 1 <= int(profiler_port) <= 65535:
            errors.append("PROFILER_PORT must be an integer TCP port from 1 to 65535")

    mode = target_mode(config)
    if mode in {"docker", "ssh+docker"} and not value(config, "CONTAINER_NAME"):
        errors.append("CONTAINER_NAME must not be empty in docker mode")

    if mode in {"ssh", "ssh+docker"} and ssh_password_enabled() and shutil.which("sshpass") is None:
        errors.append("sshpass is required when TRACE_REPORT_SSH_PASSWORD is set")

    if (
        check_target
        and mode in {"ssh", "ssh+docker"}
        and os.environ.get("SSHPASS")
        and not ssh_password_enabled()
    ):
        errors.append(
            "SSHPASS is ignored by trace-report; set TRACE_REPORT_SSH_PASSWORD "
            "for password SSH authentication"
        )

    if errors or not check_target:
        return errors

    if mode in {"ssh", "ssh+docker"}:
        server = f"{value(config, 'SERVER_USER')}@{value(config, 'SERVER_HOST')}"
        ssh_probe = run_checked(
            [*ssh_command_prefix(), server, "true"],
            capture=True,
            env=target_process_env(config),
        )
        if ssh_probe.returncode != 0:
            detail = (ssh_probe.stderr or ssh_probe.stdout or "").strip().splitlines()
            suffix = f": {detail[-1]}" if detail else ""
            errors.append(
                f"SSH connection failed for {server}{suffix}; "
                "if using password authentication, set TRACE_REPORT_SSH_PASSWORD"
            )
            return errors

    cmd = target_test_command(config, ["test", "-d", value(config, "REMOTE_WORKDIR")])
    if run_checked(cmd, capture=True, env=target_process_env(config)).returncode != 0:
        errors.append(f"REMOTE_WORKDIR does not exist in {mode} target: {value(config, 'REMOTE_WORKDIR')}")

    if mode in {"docker", "ssh+docker"}:
        cmd = (
            ["docker", "inspect", value(config, "CONTAINER_NAME")]
            if mode == "docker"
            else [
                *ssh_command_prefix(),
                f"{value(config, 'SERVER_USER')}@{value(config, 'SERVER_HOST')}",
                shell_join(["docker", "inspect", value(config, "CONTAINER_NAME")]),
            ]
        )
        if run_checked(cmd, capture=True, env=target_process_env(config)).returncode != 0:
            errors.append(f"container does not exist or is not inspectable: {value(config, 'CONTAINER_NAME')}")

    target_tool_keys = ["MC_TRACER_BIN", "CYCLE_TRACE_BIN"]
    if require_mcprofiler_enabled:
        target_tool_keys.append("MC_PROFILER_BIN")
    for key in target_tool_keys:
        cmd = target_test_command(config, ["test", "-x", value(config, key)])
        if run_checked(cmd, capture=True, env=target_process_env(config)).returncode != 0:
            errors.append(f"{key} is not executable in {mode} target: {value(config, key)}")

    if kernel_name:
        tools_json = value(config, "CYCLE_TRACE_TOOLS_JSON") or "/opt/maca/etc/tools.json"
        cmd = target_test_command(config, ["test", "-w", tools_json])
        if run_checked(cmd, capture=True, env=target_process_env(config)).returncode != 0:
            errors.append(f"CYCLE_TRACE_TOOLS_JSON is not writable in {mode} target: {tools_json}")

    if enable_source_latency_enabled and source_file:
        source_check = run_in_target_workdir(config, ["test", "-f", source_file], capture=True)
        if source_check.returncode != 0:
            errors.append(f"MACA_SOURCE_FILE does not exist in REMOTE_WORKDIR ({value(config, 'REMOTE_WORKDIR')}): {source_file}")

    if enable_source_latency_enabled and lineinfo_binary:
        binary_check = run_in_target_workdir(config, ["test", "-f", lineinfo_binary], capture=True)
        if binary_check.returncode != 0:
            errors.append(f"MACA_LINEINFO_BINARY does not exist in REMOTE_WORKDIR ({value(config, 'REMOTE_WORKDIR')}): {lineinfo_binary}")

    if enable_source_latency_enabled and lineinfo_cycle_trace_exec_argv:
        try:
            lineinfo_run = run_in_target_workdir(config, lineinfo_cycle_trace_exec_argv, capture=True)
        except OSError as exc:
            errors.append(f"MACA_LINEINFO_CYCLE_TRACE_EXEC_CMD failed to execute in REMOTE_WORKDIR ({value(config, 'REMOTE_WORKDIR')}): {exc}")
        else:
            if lineinfo_run.returncode != 0:
                detail = (lineinfo_run.stderr or lineinfo_run.stdout or "").strip().splitlines()
                suffix = f": {detail[-1]}" if detail else ""
                errors.append(
                    f"MACA_LINEINFO_CYCLE_TRACE_EXEC_CMD failed to execute in REMOTE_WORKDIR "
                    f"({value(config, 'REMOTE_WORKDIR')}){suffix}"
                )

    if enable_source_latency_enabled and lineinfo_binary:
        if objdump_bin:
            if "/" in objdump_bin:
                objdump_cmd = ["test", "-x", objdump_bin]
            else:
                objdump_cmd = ["sh", "-lc", shell_join(["command", "-v", objdump_bin])]
            objdump_check = run_in_target_workdir(config, objdump_cmd, capture=True)
            if objdump_check.returncode != 0:
                errors.append(f"MACA_OBJDUMP_BIN is not executable in {mode} target: {objdump_bin}")

    if enable_source_latency_enabled and (source_file or lineinfo_binary):
        if llvm_objdump_bin:
            effective_llvm_objdump = llvm_objdump_bin
        elif objdump_bin and "/" in objdump_bin:
            effective_llvm_objdump = str(Path(objdump_bin).with_name("llvm-objdump"))
        else:
            effective_llvm_objdump = "llvm-objdump"
        if "/" in effective_llvm_objdump:
            llvm_objdump_cmd = ["test", "-x", effective_llvm_objdump]
        else:
            llvm_objdump_cmd = ["sh", "-lc", shell_join(["command", "-v", effective_llvm_objdump])]
        llvm_objdump_check = run_in_target_workdir(config, llvm_objdump_cmd, capture=True)
        if llvm_objdump_check.returncode != 0:
            errors.append(f"MACA_LLVM_OBJDUMP_BIN is not executable in {mode} target: {effective_llvm_objdump}")

    exec_argv = parse_exec_cmd(config)
    if exec_argv is not None and value(config, "MACA_VISIBLE_DEVICES") != "-1":
        try:
            op = run_in_target_workdir(config, exec_argv, capture=True)
        except OSError as exc:
            errors.append(f"OP_EXEC_CMD failed in REMOTE_WORKDIR ({value(config, 'REMOTE_WORKDIR')}): {exc}")
        else:
            if op.returncode != 0:
                detail = (op.stderr or op.stdout or "").strip().splitlines()
                suffix = f": {detail[-1]}" if detail else ""
                errors.append(f"OP_EXEC_CMD failed in REMOTE_WORKDIR ({value(config, 'REMOTE_WORKDIR')}){suffix}")

    mxsmi = run_checked(target_test_command(config, ["mx-smi"]), capture=True, env=target_process_env(config))
    if mxsmi.returncode != 0:
        errors.append("mx-smi is not available in target environment; cannot validate MACA_VISIBLE_DEVICES")
    else:
        device = value(config, "MACA_VISIBLE_DEVICES")
        visible = re.findall(r"(?m)^\s*(\d+)\s", mxsmi.stdout or "")
        if device != "-1" and device not in visible and not re.search(rf"(?<!\d){re.escape(device)}(?!\d)", mxsmi.stdout or ""):
            errors.append(f"MACA_VISIBLE_DEVICES={device} was not found in mx-smi output")

    return errors
