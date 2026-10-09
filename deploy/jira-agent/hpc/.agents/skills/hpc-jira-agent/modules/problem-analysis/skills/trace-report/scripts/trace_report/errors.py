"""Stable error identifiers for troubleshooting lookup."""

from __future__ import annotations

import re


ERROR_PATTERNS: tuple[tuple[str, str], ...] = (
    ("TR-PF-001", r"^active config must be passed explicitly"),
    ("TR-PF-002", r"^(active config not found|config file not found):"),
    ("TR-PF-003", r"^(--set requires KEY=VALUE|--set requires a non-empty KEY|unknown config key):"),
    ("TR-PF-004", r"^active config already exists:"),
    ("TR-PF-005", r"^cannot (write active config at|create LOCAL_WORKDIR at )"),
    ("TR-PF-006", r"^.+:\d+: (nested YAML is not supported|expected KEY: value|invalid key:)"),
    ("TR-PF-007", r"^SERVER_USER and SERVER_HOST must be both set or both empty$"),
    ("TR-PF-008", r"^(LOCAL_WORKDIR|REMOTE_WORKDIR|OP_EXEC_CMD|MACA_VISIBLE_DEVICES) must not be empty|^HEURISTIC_BOUND_MODE must be coarse or detailed$"),
    ("TR-PF-009", r"^OP_EXEC_CMD must be valid shell-style argv text$"),
    ("TR-PF-010", r"^CYCLE_TRACE_TARGET_PEU must "),
    ("TR-PF-011", r"^CYCLE_TRACE_DPG_PAGE_NUM must "),
    ("TR-PF-012", r"^CYCLE_TRACE_TARGET_AP must "),
    ("TR-PF-013", r"^(CYCLE_TRACE_DPC_ID must |DPC id must be )"),
    ("TR-PF-014", r"^(MC_TRACER_BIN|CYCLE_TRACE_BIN|MC_PROFILER_BIN) must not be empty"),
    ("TR-PF-015", r"^(COLLECT_TIMEOUT_SECONDS|AUTO_SELECT_DEVICE_TIMEOUT_SECONDS) must "),
    ("TR-PF-016", r"^(REQUIRE_MCPROFILER|ENABLE_SOURCE_LATENCY) must be a boolean value:"),
    ("TR-PF-017", r"^PROFILER_PORT must be an integer TCP port from 1 to 65535$"),
    ("TR-PF-018", r"^(CYCLE_TRACE_SAMPLE_MODE must |CYCLE_TRACE_KERNEL_NAME requires |CYCLE_TRACE_KERNEL_REPEAT must |CYCLE_TRACE_KERNEL_REPEAT requires |CYCLE_TRACE_TOOLS_JSON is not writable )"),
    ("TR-PF-019", r"^(MACA_SOURCE_FILE|MACA_LINEINFO_BINARY) must not be empty when ENABLE_SOURCE_LATENCY=true$"),
    ("TR-PF-020", r"^MACA_LINEINFO_CYCLE_TRACE_EXEC_CMD (must be valid shell-style argv text:|did not produce an executable$)"),
    ("TR-PF-021", r"^(MACA_OBJDUMP_ARGS must be valid shell-style argv text:|MACA_OBJDUMP_BIN is not executable |MACA_LLVM_OBJDUMP_BIN is not executable )"),
    ("TR-PF-022", r"^--config and collect-run --set are mutually exclusive$"),
    ("TR-PF-023", r"^.+:\d+: unknown config key:"),
    ("TR-SSH-001", r"^SSHPASS is ignored by trace-report;"),
    ("TR-SSH-002", r"^sshpass is required when TRACE_REPORT_SSH_PASSWORD is set$"),
    ("TR-SSH-003", r"^SSH connection failed for "),
    ("TR-TGT-001", r"^REMOTE_WORKDIR does not exist in "),
    ("TR-TGT-002", r"^(CONTAINER_NAME must not be empty in docker mode$|container does not exist or is not inspectable:)"),
    ("TR-TGT-003", r"^(.+ is not executable in .+ target:|mcTracer not executable:|cycle-trace-ng not executable:|mcProfiler not executable:|CycleTrace tools\.json (not found|is not writable):)"),
    ("TR-TGT-004", r"^OP_EXEC_CMD failed in REMOTE_WORKDIR "),
    ("TR-TGT-005", r"^mx-smi is not available in target environment;"),
    ("TR-TGT-006", r"^MACA_VISIBLE_DEVICES=.+ was not found in mx-smi output$"),
    ("TR-TGT-009", r"^(MACA_MEM_BUSY_PCT must be a number|automatic GPU selection )"),
    ("TR-TGT-007", r"^(MACA_SOURCE_FILE|MACA_LINEINFO_BINARY) does not exist in REMOTE_WORKDIR "),
    ("TR-TGT-008", r"^MACA_LINEINFO_CYCLE_TRACE_EXEC_CMD failed to execute in REMOTE_WORKDIR "),
    ("TR-SYNC-001", r"^refuse to overwrite current skill scripts:"),
    ("TR-SYNC-002", r"^synced entrypoint is missing:"),
    ("TR-SYNC-003", r"^(command failed \(\d+\):|docker artifact sync failed$)"),
    ("TR-WF-001", r"^collect-run failed at validate:"),
    ("TR-WF-002", r"^collect-run failed at sync-scripts:"),
    ("TR-WF-003", r"^collect-run failed at create-run-dir:"),
    ("TR-WF-004", r"^collect-run failed at collect:"),
    ("TR-WF-005", r"^collect-run failed at source-latency:"),
    ("TR-WF-006", r"^collect-run failed at refresh-report:"),
    ("TR-WF-007", r"^collect-run failed at check-report:"),
    ("TR-WF-008", r"^collect-run failed at split-usecases:"),
    ("TR-WF-009", r"^collect-run failed at usecase-source-latency:"),
    ("TR-WF-010", r"^collect-run failed at usecase-refresh-report:"),
    ("TR-RUN-001", r"^run requires a command after --$"),
    ("TR-RUN-002", r"^run directory basename must match <kernel>_v<N>_<tag>:"),
    ("TR-RUN-003", r"^sync-artifacts --run-dir must be under profile-artifacts/$"),
    ("TR-COL-003", r"^failed to parse --exec-cmd$|^--exec-cmd did not produce an executable$"),
    ("TR-COL-004", r"^(trace_profile_pipeline.py not found next to this script|failed to (create tools\.json backup|back up |update cycleTrace\.kernels in tools\.json|verify CycleTrace single-kernel filter in tools\.json))"),
    ("TR-ANA-004", r"^CycleTrace JSON has no hardware instruction events under CYCLE_TRACE_KERNEL_NAME="),
    ("TR-ANA-005", r"^mcTracer JSON has no kernel launch entry matching CYCLE_TRACE_KERNEL_NAME="),
    ("TR-COL-006", r"^(CycleTrace JSON has no (traceEvents|hardware instruction events)|CycleTrace JSON is invalid:)"),
    ("TR-COL-011", r"^(mcProfiler artifacts are missing;|mcProfiler artifacts are required by REQUIRE_MCPROFILER=true)"),
    ("TR-COL-013", r"^trace tool collection timed out"),
    ("TR-COL-014", r"^cycle-trace-ng --kernel-probe Python plugin failed to load;"),
    ("TR-COL-015", r"^cycle-trace-ng --kernel-probe Python plugin failed while running its internal mxobjdump --list-elf command;"),
    ("TR-COL-016", r"^(CYCLE_TRACE_KERNEL_NAME does not match any mcTracer args\.name|current value matches mcTracer display name/top-level name only|candidate mcTracer args\.name values|mcTracer JSON has no kernel launch entries with args\.name/grid/block/mem)"),
    ("TR-ART-001", r"^source is not a directory:"),
    ("TR-ART-002", r"^required artifacts are incomplete:"),
    ("TR-ANA-001", r"^\[Errno 2\] No such file or directory:"),
    ("TR-ANA-003", r"^CycleTrace JSON has no (traceEvents|hardware instruction events):"),
    ("TR-CMP-001", r"^(compare requires at least two --tag values|compare requires at least two --case values|compare requires either --case repeated at least twice|--case must use label=/path/to/run-dir|--case label must not be empty)"),
    ("TR-CMP-002", r"^(compare output directory could not be determined|no metrics_all_\*\.json found in|multiple metrics_all_\*\.json files found in)"),
    ("TR-USC-001", r"^split-usecases (artifact directory does not exist|requires tracer_out\.json and primary CycleTrace JSON)"),
    ("TR-USC-002", r"^split-usecases --min-confidence must be high, medium, or low$"),
    ("TR-LLM-001", r"^append-diagnosis must be run through trace_report_env\.py "),
    ("TR-LLM-001", r"^diagnosis file does not exist:"),
    ("TR-LLM-001", r"^append-diagnosis failed:"),
    ("TR-LLM-002", r"^LLM follow-up diagnosis content is empty$"),
    ("TR-LLM-003", r"^LLM follow-up diagnosis is missing required headings:"),
    ("TR-LLM-004", r"^(Final Diagnosis must include at least one #### Diagnosis item|Diagnosis \d+ is missing #### Reasoning Chain|Diagnosis \d+ Reasoning Chain is missing:)"),
    ("TR-LLM-005", r"^(report does not exist:|report has incomplete LLM follow-up diagnosis markers$)"),
    ("TR-REP-001", r"^required report (check input|output) is missing or empty:"),
    ("TR-REP-002", r"^collection_manifest\.json (must contain a JSON object|missing_required is not empty:|invalid_required is not empty:|mcprofiler_reference)"),
    ("TR-REP-003", r"^metrics_all_.+\.json must contain a JSON object$"),
    ("TR-REP-004", r"^(metrics_all source_latency\.(available must be true|coverage must be an object|coverage\.raw_modeled_cycles must be > 0)|REPORT is missing source-latency summary markers:|REPORT must state that source-latency produced no attributed source lines|REPORT is missing source-latency top instruction summary when attributed source lines exist)"),
    ("TR-REP-005", r"^REPORT is missing complete LLM follow-up diagnosis markers"),
    ("TR-REP-006", r"^(metrics_all collection_scope\.cycle_trace_kernel_name must not be empty|REPORT is missing CycleTrace kernel scope markers:)"),
    ("TR-REP-007", r"^generated usecase reports require finalize-run with usecase diagnoses;"),
)


def error_id_for(message: str) -> str | None:
    clean = message.strip()
    if re.match(r"^\[TR-[A-Z]+-\d+\]\s+", clean):
        return None
    for error_id, pattern in ERROR_PATTERNS:
        if re.search(pattern, clean):
            return error_id
    return None


def format_error(message: str) -> str:
    error_id = error_id_for(message)
    if error_id is None:
        return message
    return f"[{error_id}] {message}"
