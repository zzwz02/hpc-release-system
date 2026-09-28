#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage:
  collect_trace_profile.sh --run-dir DIR --tag TAG --exec-cmd CMD --device ID --target-peu ID --dpg-page-num NUM --target-ap ID --dpc-id IDS --heuristic-bound-mode MODE --collect-timeout-seconds SEC --require-mcprofiler BOOL --mctracer-bin PATH --cycle-trace-bin PATH --mcprofiler-bin PATH [options]

Collect C500 profiling artifacts with mcTracer, cycle-trace-ng, and mcProfiler,
then run trace_profile_pipeline.py to generate JSON/Markdown reports.
This script performs one collection pass. Workflow-level retry decisions are
handled by the trace-report skill instructions.

Required:
  --run-dir DIR          Output run directory, for example profile-artifacts/gemm_v0_baseline
  --tag TAG              Analysis tag, for example baseline or v0_baseline
  --exec-cmd CMD         Profiled command executed from --workdir/current directory
  --device ID            resolved MACA_VISIBLE_DEVICES value used by profiling tools
  --requested-device ID  original MACA_VISIBLE_DEVICES value before auto-selection
  --auto-selected-device BOOL
                         true when --device was selected from MACA_VISIBLE_DEVICES=-1
  --target-peu ID        cycle-trace-ng --target-peu PEU bitmap; 1-15, default 1 (0x1) captures PEU 0
  --dpg-page-num NUM     cycle-trace-ng --dpg-page-num value; tool default is 32 (64M)
  --target-ap ID         cycle-trace-ng --target-ap value; tool default is 0
  --dpc-id IDS           cycle-trace-ng --dpc-id value; comma-separated 0-7 list, default 0 captures DPC 0
  --heuristic-bound-mode MODE
                         C500 heuristic bound mode: coarse or detailed; does not affect Roofline bound
  --collect-timeout-seconds SEC
                         Shared timeout for mcTracer, cycle-trace-ng, and mcProfiler only; default from YAML is 600 seconds
  --require-mcprofiler BOOL
                         true requires complete mcProfiler JSONs; false continues with N/A profiler metrics
  --skip-mcprofiler BOOL true disables mcProfiler collection, reuse, restoration, and metrics ingestion
  --mctracer-bin PATH    mcTracer path
  --cycle-trace-bin PATH cycle-trace-ng path
  --mcprofiler-bin PATH  mcProfiler path

Options:
  --workdir DIR          Directory to run --exec-cmd from (default: current directory)
  --case-name NAME       mcProfiler --casename value (default: basename of RUN_DIR)
  --enable-source-latency BOOL
                         true makes cycle-trace-ng run MACA_LINEINFO_CYCLE_TRACE_EXEC_CMD,
                         or MACA_LINEINFO_BINARY when unset, with --kernel-probe; default false
  --profiler-port PORT   mcProfiler local service port; default is 50123
  --cycle-sample-mode M  cycle-trace-ng --sample-mode:
                         A=all kernels, B=blit kernels only,
                         C=custom cycleTrace.kernels, D=DIDT kernel only;
                         empty/default lets tool use A
  --cycle-kernel-name N  Temporarily replace cycleTrace.kernels with a single target kernel
  --cycle-kernel-repeat N
                         Repeat value for the single target kernel; default -1 when kernel name is set
  --cycle-tools-json P   tools.json path for cycleTrace.kernels (default: /opt/maca/etc/tools.json)
  -h, --help             Show this help

Example:
  .trace-report/scripts/collect_trace_profile.sh \
    --run-dir profile-artifacts/bf16_gemm_kernel_v0_baseline \
    --tag baseline \
    --exec-cmd './test_maca' \
    --device 0 \
    --target-peu 1 \
    --dpg-page-num 32 \
    --target-ap 0 \
    --dpc-id 0 \
    --heuristic-bound-mode coarse \
    --collect-timeout-seconds 600 \
    --require-mcprofiler false \
    --enable-source-latency false \
    --mctracer-bin /opt/maca/bin/mcTracer \
    --cycle-trace-bin /opt/maca-20260505/bin/cycle-trace-ng \
    --mcprofiler-bin /opt/maca/restricted/Tools/mcProfiler/mcProfiler-ubuntu18.04/mcProfiler \
    --profiler-port 50123
USAGE
}

die() {
  local error_id="$1"
  shift
  echo "[collect_trace_profile] error: [$error_id] $*" >&2
  exit 1
}

require_file() {
  local path="$1"
  local label="$2"
  local error_id="${3:-TR-COL-012}"
  if [[ ! -s "$path" ]]; then
    echo "[collect_trace_profile] error: [$error_id] missing/invalid required output: $label ($path)" >&2
    return 1
  fi
}

validate_mctracer_file() {
  local path="$1"
  local label="${2:-mcTracer JSON}"
  local severity="${3:-error}"
  [[ -s "$path" ]] || return 1

  PYTHONPATH="$script_dir" python3 - "$path" "$label" "$severity" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
label = sys.argv[2]
severity = sys.argv[3]
prefix = f"[collect_trace_profile] {severity}: [TR-COL-017]"
try:
    data = json.loads(path.read_text(encoding="utf-8"))
except Exception as exc:
    print(
        f"{prefix} invalid {label}: {path} ({exc})",
        file=sys.stderr,
    )
    raise SystemExit(1)

events = data.get("traceEvents", [])
if not isinstance(events, list):
    events = []

kernel_events = [
    event for event in events
    if isinstance(event, dict)
    and event.get("ph") == "X"
    and isinstance(event.get("args"), dict)
    and {"grid", "block", "mem"}.issubset(event["args"])
]
if kernel_events:
    raise SystemExit(0)

metadata_count = sum(
    1 for event in events
    if isinstance(event, dict) and event.get("ph") == "M"
)
print(
    f"{prefix} {label} has no kernel launch "
    "entries with args.grid/args.block/args.mem; "
    f"traceEvents={len(events)}, metadataEvents={metadata_count}. "
    "The profiled command likely exited before mcTracer observed a kernel, "
    "or the target did not launch the expected custom op. Re-run after fixing "
    "the target wrapper/runtime behavior.",
    file=sys.stderr,
)
raise SystemExit(1)
PY
}

mcprofiler_required_artifacts_satisfied() {
  local artifact_dir="$1"
  if [[ -n "$cycle_kernel_name" ]]; then
    mcprofiler_kernel_artifacts_satisfied "$artifact_dir"
    return $?
  fi
  if [[ -s "$artifact_dir/mcprofiler_report_dumped.json" && -s "$artifact_dir/mcprofiler_report.txt.json" ]]; then
    return 0
  fi
  local manifest="$artifact_dir/mcprofiler_per_kernel/manifest.json"
  if [[ ! -s "$manifest" ]]; then
    return 1
  fi
  python3 - "$manifest" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
try:
    data = json.loads(path.read_text(encoding="utf-8"))
except Exception:
    sys.exit(1)
status = data.get("status")
occurrence_count = int(data.get("occurrence_count") or 0)
complete_count = int(data.get("complete_occurrence_count") or 0)
if status == "multi-occurrence" and occurrence_count > 1 and occurrence_count == complete_count:
    sys.exit(0)
sys.exit(1)
PY
}

parse_exec_cmd() {
  local cmd="$1"
  python3 - "$cmd" <<'PY'
import shlex
import sys

for part in shlex.split(sys.argv[1]):
    print(part)
PY
}

require_arg() {
  local option="$1"
  if [[ $# -lt 2 ]]; then
    die "TR-COL-001" "$option requires an argument"
  fi
}

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
workdir=""
run_dir=""
tag=""
exec_cmd=""
exec_argv=()
cycle_exec_argv=()
cycle_exec_cmd=""
lineinfo_binary=""
lineinfo_cycle_trace_exec_cmd=""
case_name=""
device=""
requested_device=""
auto_selected_device="false"
target_peu=""
dpg_page_num=""
target_ap=""
dpc_id=""
heuristic_bound_mode=""
collect_timeout_seconds=""
tool_collect_remaining_seconds=""
require_mcprofiler=""
skip_mcprofiler=""
enable_source_latency=""
mctracer_bin=""
cycle_trace_bin=""
mcprofiler_bin=""
profiler_port=""
profiler_cmdline=""
profiler_out=""
current_marker=""
current_tool_label=""
current_tool_pid=""
current_tool_uses_setsid="false"
cycle_trace_extra_args=()
cycle_sample_mode=""
cycle_kernel_name=""
cycle_kernel_repeat=""
cycle_tools_json=""
cycle_tools_backup=""
cycle_tools_configured=0
cycle_exec_output_base=""

cleanup_lineinfo_cycle_workdir_outputs() {
  local marker_path="${1:-}"
  [[ "$enable_source_latency" == "true" ]] || return 0
  [[ -n "$cycle_exec_output_base" && "$cycle_exec_output_base" != "." && "$cycle_exec_output_base" != "/" ]] || return 0
  if [[ -n "$marker_path" && -f "$marker_path" ]]; then
    find "$workdir" -maxdepth 1 -type f -name "${cycle_exec_output_base}.[0-9]*.out" -newer "$marker_path" -delete
  else
    find "$workdir" -maxdepth 1 -type f -name "${cycle_exec_output_base}.[0-9]*.out" -delete
  fi
}

cleanup_workdir_temporary_outputs() {
  find "$workdir" -maxdepth 1 -type d -name 'tracer_out_*' -exec rm -rf {} +
  find "$workdir" -maxdepth 1 -type f \( -name '.2*.db' -o -name 'c-trace*.json' \) -delete
  cleanup_lineinfo_cycle_workdir_outputs
}

restore_cycle_trace_tools_json() {
  if [[ "$cycle_tools_configured" -eq 1 && -n "$cycle_tools_backup" && -f "$cycle_tools_backup" ]]; then
    cp -p "$cycle_tools_backup" "$cycle_tools_json" || {
      echo "[collect_trace_profile] warning: failed to restore $cycle_tools_json from $cycle_tools_backup" >&2
    }
    rm -f "$cycle_tools_backup"
    cycle_tools_configured=0
  fi
}

configure_cycle_trace_tools_json() {
  [[ -n "$cycle_kernel_name" ]] || return 0
  [[ -f "$cycle_tools_json" ]] || die "TR-TGT-003" "CycleTrace tools.json not found: $cycle_tools_json"
  [[ -w "$cycle_tools_json" ]] || die "TR-TGT-003" "CycleTrace tools.json is not writable: $cycle_tools_json"
  cycle_tools_backup="$(mktemp "${TMPDIR:-/tmp}/trace-report-tools-json.XXXXXX")" || die "TR-COL-004" "failed to create tools.json backup"
  cp -p "$cycle_tools_json" "$cycle_tools_backup" || die "TR-COL-004" "failed to back up $cycle_tools_json"
  cycle_tools_configured=1
  python3 - "$cycle_tools_json" "$cycle_kernel_name" "$cycle_kernel_repeat" <<'PY' || die "TR-COL-004" "failed to update cycleTrace.kernels in tools.json"
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
kernel_name = sys.argv[2]
repeat = int(sys.argv[3])
text = path.read_text(encoding="utf-8")

def find_balanced(src, start, open_ch, close_ch):
    depth = 0
    in_string = False
    escaped = False
    line_comment = False
    block_comment = False
    i = start
    while i < len(src):
        ch = src[i]
        nxt = src[i + 1] if i + 1 < len(src) else ""
        if line_comment:
            if ch == "\n":
                line_comment = False
            i += 1
            continue
        if block_comment:
            if ch == "*" and nxt == "/":
                block_comment = False
                i += 2
            else:
                i += 1
            continue
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            i += 1
            continue
        if ch == "/" and nxt == "/":
            line_comment = True
            i += 2
            continue
        if ch == "/" and nxt == "*":
            block_comment = True
            i += 2
            continue
        if ch == '"':
            in_string = True
            i += 1
            continue
        if ch == open_ch:
            depth += 1
        elif ch == close_ch:
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return None

cycle_key = '"cycleTrace"'
cycle_pos = text.find(cycle_key)
if cycle_pos < 0:
    raise SystemExit("cycleTrace block not found")
cycle_brace = text.find("{", cycle_pos)
if cycle_brace < 0:
    raise SystemExit("cycleTrace object not found")
cycle_end = find_balanced(text, cycle_brace, "{", "}")
if cycle_end is None:
    raise SystemExit("cycleTrace object end not found")

kernels_key = '"kernels"'
kernels_pos = text.find(kernels_key, cycle_brace, cycle_end)
if kernels_pos < 0:
    raise SystemExit("cycleTrace.kernels not found")
kernels_bracket = text.find("[", kernels_pos, cycle_end)
if kernels_bracket < 0:
    raise SystemExit("cycleTrace.kernels array not found")
kernels_end = find_balanced(text, kernels_bracket, "[", "]")
if kernels_end is None or kernels_end > cycle_end:
    raise SystemExit("cycleTrace.kernels array end not found")

line_start = text.rfind("\n", 0, kernels_pos) + 1
indent = text[line_start:kernels_pos]
item_indent = indent + "    "
replacement = (
    '"kernels": [\n'
    f'{item_indent}{{\n'
    f'{item_indent}    "kernelName": {json.dumps(kernel_name)},\n'
    f'{item_indent}    "repeat": {repeat}\n'
    f'{item_indent}}}\n'
    f'{indent}]'
)
path.write_text(text[:kernels_pos] + replacement + text[kernels_end:], encoding="utf-8")
PY
  echo "[setup] CycleTrace kernel filter: tools_json=$cycle_tools_json kernelName=$cycle_kernel_name repeat=$cycle_kernel_repeat sample_mode=$cycle_sample_mode"
}

verify_cycle_trace_tools_json_kernel_filter() {
  [[ -n "$cycle_kernel_name" ]] || return 0
  python3 - "$cycle_tools_json" "$cycle_kernel_name" "$cycle_kernel_repeat" <<'PY' || return 1
import json
import re
import sys
from pathlib import Path

path = Path(sys.argv[1])
expected_kernel = sys.argv[2]
expected_repeat = int(sys.argv[3])
text = path.read_text(encoding="utf-8")
clean = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
clean = re.sub(r"//.*", "", clean)
data = json.loads(clean)
cycle_trace = data.get("cycleTrace")
if not isinstance(cycle_trace, dict):
    raise SystemExit("cycleTrace object not found")
kernels = cycle_trace.get("kernels")
if not isinstance(kernels, list) or len(kernels) != 1:
    raise SystemExit("cycleTrace.kernels must contain exactly one kernel during single-kernel collection")
kernel = kernels[0]
if not isinstance(kernel, dict):
    raise SystemExit("cycleTrace.kernels[0] must be an object")
if kernel.get("kernelName") != expected_kernel:
    raise SystemExit("cycleTrace.kernels[0].kernelName mismatch")
if int(kernel.get("repeat")) != expected_repeat:
    raise SystemExit("cycleTrace.kernels[0].repeat mismatch")
PY
}

terminate_current_tool_processes() {
  local reason="${1:-failure}"
  local pid="$current_tool_pid"
  local label="$current_tool_label"
  local uses_setsid="$current_tool_uses_setsid"
  if [[ -z "$pid" ]]; then
    return 0
  fi

  if [[ "$uses_setsid" == "true" ]]; then
    echo "[collect_trace_profile] warning: terminating ${label:-current trace tool} processes after ${reason}" >&2
    kill -TERM -- "-$pid" 2>/dev/null || true
    sleep 2
    kill -KILL -- "-$pid" 2>/dev/null || true
  elif kill -0 "$pid" 2>/dev/null; then
    echo "[collect_trace_profile] warning: terminating ${label:-current trace tool} processes after ${reason}" >&2
    kill -TERM "$pid" 2>/dev/null || true
    sleep 2
    kill -KILL "$pid" 2>/dev/null || true
  fi

  current_tool_label=""
  current_tool_pid=""
  current_tool_uses_setsid="false"
}

cleanup_on_exit() {
  local status=$?
  terminate_current_tool_processes "script exit"
  restore_cycle_trace_tools_json
  return "$status"
}

trap cleanup_on_exit EXIT
trap 'terminate_current_tool_processes "interrupt"; exit 130' INT
trap 'terminate_current_tool_processes "termination"; exit 143' TERM
trap 'terminate_current_tool_processes "hangup"; exit 129' HUP

run_tool_with_timeout() {
  local label="$1"
  shift
  local timeout_is_error="true"
  if [[ "${1:-}" == "--timeout-warning-only" ]]; then
    timeout_is_error="false"
    shift
  fi
  local started
  local ended
  local elapsed
  local status
  local timed_out="false"
  local tool_pid
  local deadline
  local now
  local errexit_was_set="false"

  if (( tool_collect_remaining_seconds <= 0 )); then
    if [[ "$timeout_is_error" == "true" ]]; then
      echo "[collect_trace_profile] error: [TR-COL-013] trace tool collection timed out before ${label}; current limit COLLECT_TIMEOUT_SECONDS=${collect_timeout_seconds}s. Increase COLLECT_TIMEOUT_SECONDS in the active config and rerun collect-run if this collection needs more time." >&2
    fi
    return 124
  fi

  started="$(date +%s)"
  deadline=$(( started + tool_collect_remaining_seconds ))
  if command -v setsid >/dev/null 2>&1; then
    setsid "$@" &
    current_tool_uses_setsid="true"
  else
    "$@" &
    current_tool_uses_setsid="false"
  fi
  tool_pid=$!
  current_tool_label="$label"
  current_tool_pid="$tool_pid"

  while kill -0 "$tool_pid" 2>/dev/null; do
    now="$(date +%s)"
    if (( now >= deadline )); then
      timed_out="true"
      status=124
      terminate_current_tool_processes "timeout"
      break
    fi
    sleep 1
  done

  if [[ "$timed_out" == "true" ]]; then
    wait "$tool_pid" 2>/dev/null || true
  else
    case "$-" in
      *e*) errexit_was_set="true" ;;
    esac
    set +e
    wait "$tool_pid"
    status=$?
    if [[ "$errexit_was_set" == "true" ]]; then
      set -e
    else
      set +e
    fi
    if [[ "$status" -ne 0 ]]; then
      terminate_current_tool_processes "failure"
    else
      current_tool_label=""
      current_tool_pid=""
      current_tool_uses_setsid="false"
    fi
  fi

  ended="$(date +%s)"
  elapsed=$(( ended - started ))
  tool_collect_remaining_seconds=$(( tool_collect_remaining_seconds - elapsed ))
  if (( tool_collect_remaining_seconds < 0 )); then
    tool_collect_remaining_seconds=0
  fi

  if [[ "$status" -eq 124 && "$timeout_is_error" == "true" ]]; then
    echo "[collect_trace_profile] error: [TR-COL-013] trace tool collection timed out during ${label}; current limit COLLECT_TIMEOUT_SECONDS=${collect_timeout_seconds}s. Increase COLLECT_TIMEOUT_SECONDS in the active config and rerun collect-run if this collection needs more time." >&2
  fi
  return "$status"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --workdir) require_arg "$1" "$@"; workdir="$2"; shift 2 ;;
    --run-dir) require_arg "$1" "$@"; run_dir="$2"; shift 2 ;;
    --tag) require_arg "$1" "$@"; tag="$2"; shift 2 ;;
    --exec-cmd) require_arg "$1" "$@"; exec_cmd="$2"; shift 2 ;;
	    --case-name) require_arg "$1" "$@"; case_name="$2"; shift 2 ;;
	    --device) require_arg "$1" "$@"; device="$2"; shift 2 ;;
	    --requested-device) require_arg "$1" "$@"; requested_device="$2"; shift 2 ;;
	    --auto-selected-device) require_arg "$1" "$@"; auto_selected_device="$2"; shift 2 ;;
    --target-peu) require_arg "$1" "$@"; target_peu="$2"; shift 2 ;;
    --dpg-page-num) require_arg "$1" "$@"; dpg_page_num="$2"; shift 2 ;;
    --target-ap) require_arg "$1" "$@"; target_ap="$2"; shift 2 ;;
    --dpc-id) require_arg "$1" "$@"; dpc_id="$2"; shift 2 ;;
    --heuristic-bound-mode) require_arg "$1" "$@"; heuristic_bound_mode="$2"; shift 2 ;;
    --collect-timeout-seconds) require_arg "$1" "$@"; collect_timeout_seconds="$2"; shift 2 ;;
    --require-mcprofiler) require_arg "$1" "$@"; require_mcprofiler="$2"; shift 2 ;;
    --skip-mcprofiler) require_arg "$1" "$@"; skip_mcprofiler="$2"; shift 2 ;;
    --enable-source-latency) require_arg "$1" "$@"; enable_source_latency="$2"; shift 2 ;;
    --cycle-sample-mode) require_arg "$1" "$@"; cycle_sample_mode="$2"; shift 2 ;;
    --cycle-kernel-name) require_arg "$1" "$@"; cycle_kernel_name="$2"; shift 2 ;;
    --cycle-kernel-repeat) require_arg "$1" "$@"; cycle_kernel_repeat="$2"; shift 2 ;;
    --cycle-tools-json) require_arg "$1" "$@"; cycle_tools_json="$2"; shift 2 ;;
    --mctracer-bin) require_arg "$1" "$@"; mctracer_bin="$2"; shift 2 ;;
    --cycle-trace-bin) require_arg "$1" "$@"; cycle_trace_bin="$2"; shift 2 ;;
    --mcprofiler-bin) require_arg "$1" "$@"; mcprofiler_bin="$2"; shift 2 ;;
    --profiler-port) require_arg "$1" "$@"; profiler_port="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) die "TR-COL-001" "unknown argument: $1" ;;
  esac
done

[[ -n "$run_dir" ]] || die "TR-COL-001" "--run-dir is required"
[[ -n "$tag" ]] || die "TR-COL-001" "--tag is required"
[[ -n "$exec_cmd" ]] || die "TR-COL-001" "--exec-cmd is required"
[[ -n "$device" ]] || die "TR-COL-001" "--device is required"
requested_device="${requested_device:-$device}"
[[ "$auto_selected_device" == "true" || "$auto_selected_device" == "false" ]] || die "TR-COL-002" "--auto-selected-device must be true or false"
[[ -n "$target_peu" ]] || die "TR-COL-001" "--target-peu is required"
[[ "$target_peu" =~ ^[0-9]+$ ]] || die "TR-COL-002" "--target-peu must be an integer from 1 to 15"
(( target_peu >= 1 && target_peu <= 15 )) || die "TR-COL-002" "--target-peu must be in [1, 15]; 1 (0x1) captures PEU 0"
[[ -n "$dpg_page_num" ]] || die "TR-COL-001" "--dpg-page-num is required"
[[ "$dpg_page_num" =~ ^[0-9]+$ ]] || die "TR-COL-002" "--dpg-page-num must be a positive integer"
(( dpg_page_num > 0 )) || die "TR-COL-002" "--dpg-page-num must be greater than 0"
[[ -n "$target_ap" ]] || die "TR-COL-001" "--target-ap is required"
[[ "$target_ap" =~ ^[0-9]+$ ]] || die "TR-COL-002" "--target-ap must be an integer from 0 to 15"
(( target_ap >= 0 && target_ap <= 15 )) || die "TR-COL-002" "--target-ap must be in [0, 15]"
[[ -n "$dpc_id" ]] || die "TR-COL-001" "--dpc-id is required"
[[ "$dpc_id" =~ ^[0-7](,[0-7])*$ ]] || die "TR-COL-002" "--dpc-id must be a comma-separated list of integers from 0 to 7 with no spaces"
IFS=',' read -ra dpc_id_parts <<< "$dpc_id"
cycle_sample_mode="${cycle_sample_mode:-${CYCLE_TRACE_SAMPLE_MODE:-}}"
cycle_kernel_name="${cycle_kernel_name:-${CYCLE_TRACE_KERNEL_NAME:-}}"
cycle_kernel_repeat="${cycle_kernel_repeat:-${CYCLE_TRACE_KERNEL_REPEAT:-}}"
cycle_tools_json="${cycle_tools_json:-${CYCLE_TRACE_TOOLS_JSON:-/opt/maca/etc/tools.json}}"
if [[ -n "$cycle_kernel_name" && -z "$cycle_sample_mode" ]]; then
  cycle_sample_mode="C"
fi
if [[ -n "$cycle_kernel_name" && -z "$cycle_kernel_repeat" ]]; then
  cycle_kernel_repeat="-1"
fi
if [[ -n "$cycle_sample_mode" ]]; then
  [[ "$cycle_sample_mode" =~ ^[ABCD]$ ]] || die "TR-COL-002" "--cycle-sample-mode must be A, B, C, or D"
fi
if [[ -n "$cycle_kernel_repeat" ]]; then
  [[ "$cycle_kernel_repeat" =~ ^-?[0-9]+$ ]] || die "TR-COL-002" "--cycle-kernel-repeat must be an integer"
fi
if [[ -n "$cycle_kernel_repeat" && -z "$cycle_kernel_name" ]]; then
  die "TR-COL-002" "--cycle-kernel-repeat requires --cycle-kernel-name"
fi
if [[ -n "$cycle_kernel_name" && "$cycle_sample_mode" != "C" ]]; then
  die "TR-COL-002" "--cycle-kernel-name requires --cycle-sample-mode C"
fi
[[ -n "$heuristic_bound_mode" ]] || die "TR-COL-001" "--heuristic-bound-mode is required"
[[ "$heuristic_bound_mode" == "coarse" || "$heuristic_bound_mode" == "detailed" ]] || die "TR-COL-002" "--heuristic-bound-mode must be coarse or detailed"
[[ -n "$collect_timeout_seconds" ]] || die "TR-COL-001" "--collect-timeout-seconds is required"
[[ "$collect_timeout_seconds" =~ ^[0-9]+$ ]] || die "TR-COL-002" "--collect-timeout-seconds must be a positive integer"
(( collect_timeout_seconds > 0 )) || die "TR-COL-002" "--collect-timeout-seconds must be greater than 0"
skip_mcprofiler="${skip_mcprofiler:-${SKIP_MCPROFILER:-false}}"
case "${skip_mcprofiler,,}" in
  1|true|yes|y|on) skip_mcprofiler="true" ;;
  0|false|no|n|off|"") skip_mcprofiler="false" ;;
  *) die "TR-COL-002" "--skip-mcprofiler must be a boolean value: true/false, yes/no, or 1/0" ;;
esac
if [[ "$skip_mcprofiler" == "true" ]]; then
  require_mcprofiler="false"
elif [[ -z "$require_mcprofiler" ]]; then
  die "TR-COL-001" "--require-mcprofiler is required"
else
  case "${require_mcprofiler,,}" in
    1|true|yes|y|on) require_mcprofiler="true" ;;
    0|false|no|n|off) require_mcprofiler="false" ;;
    *) die "TR-COL-002" "--require-mcprofiler must be a boolean value: true/false, yes/no, or 1/0" ;;
  esac
fi
enable_source_latency="${enable_source_latency:-${ENABLE_SOURCE_LATENCY:-false}}"
case "${enable_source_latency,,}" in
  1|true|yes|y|on) enable_source_latency="true" ;;
  0|false|no|n|off|"") enable_source_latency="false" ;;
  *) die "TR-COL-002" "--enable-source-latency must be a boolean value: true/false, yes/no, or 1/0" ;;
esac
[[ -n "$mctracer_bin" ]] || die "TR-COL-001" "--mctracer-bin is required"
[[ -n "$cycle_trace_bin" ]] || die "TR-COL-001" "--cycle-trace-bin is required"
if [[ "$require_mcprofiler" == "true" ]]; then
  [[ -n "$mcprofiler_bin" ]] || die "TR-COL-001" "--mcprofiler-bin is required when --require-mcprofiler=true"
fi
profiler_port="${profiler_port:-${PROFILER_PORT:-50123}}"
if [[ "$skip_mcprofiler" != "true" ]]; then
  [[ "$profiler_port" =~ ^[0-9]+$ ]] || die "TR-COL-002" "--profiler-port must be an integer TCP port from 1 to 65535"
  (( profiler_port >= 1 && profiler_port <= 65535 )) || die "TR-COL-002" "--profiler-port must be an integer TCP port from 1 to 65535"
fi
mapfile -t exec_argv < <(parse_exec_cmd "$exec_cmd") || die "TR-COL-003" "failed to parse --exec-cmd"
[[ "${#exec_argv[@]}" -gt 0 ]] || die "TR-COL-003" "--exec-cmd did not produce an executable"
lineinfo_binary="${MACA_LINEINFO_BINARY:-}"
lineinfo_cycle_trace_exec_cmd="${MACA_LINEINFO_CYCLE_TRACE_EXEC_CMD:-}"
cycle_exec_cmd="$exec_cmd"
cycle_exec_argv=("${exec_argv[@]}")
if [[ "$enable_source_latency" == "true" ]]; then
  [[ -n "$lineinfo_binary" ]] || die "TR-COL-001" "MACA_LINEINFO_BINARY is required when --enable-source-latency=true"
  if [[ -z "$lineinfo_cycle_trace_exec_cmd" ]]; then
    lineinfo_cycle_trace_exec_cmd="$lineinfo_binary"
  fi
  mapfile -t cycle_exec_argv < <(parse_exec_cmd "$lineinfo_cycle_trace_exec_cmd") || die "TR-COL-003" "failed to parse MACA_LINEINFO_CYCLE_TRACE_EXEC_CMD"
  [[ "${#cycle_exec_argv[@]}" -gt 0 ]] || die "TR-COL-003" "MACA_LINEINFO_CYCLE_TRACE_EXEC_CMD did not produce an executable"
  cycle_exec_cmd="$lineinfo_cycle_trace_exec_cmd"
fi

workdir="${workdir:-$PWD}"
mkdir -p "$run_dir"
run_dir="$(cd "$run_dir" && pwd)"
case_name="${case_name:-$(basename "$run_dir")}"
workdir="$(cd "$workdir" && pwd)"
cycle_exec_output_base="$(basename -- "${cycle_exec_argv[0]}")"
profiler_out="$run_dir/mcprofiler_raw_${tag}"
profiler_cmdline="export MACA_PATH=/opt/maca/; export LD_LIBRARY_PATH=/opt/maca/lib/:\${LD_LIBRARY_PATH}; ${exec_cmd}"

[[ -x "$mctracer_bin" ]] || die "TR-TGT-003" "mcTracer not executable: $mctracer_bin"
[[ -x "$cycle_trace_bin" ]] || die "TR-TGT-003" "cycle-trace-ng not executable: $cycle_trace_bin"
if [[ "$require_mcprofiler" == "true" ]]; then
  [[ -x "$mcprofiler_bin" ]] || die "TR-TGT-003" "mcProfiler not executable: $mcprofiler_bin"
elif [[ "$skip_mcprofiler" != "true" && ! -x "$mcprofiler_bin" ]]; then
  echo "[collect_trace_profile] warning: mcProfiler not executable: $mcprofiler_bin; continuing without mcProfiler because REQUIRE_MCPROFILER=false." >&2
fi
[[ -f "$script_dir/trace_profile_pipeline.py" ]] || die "TR-COL-004" "trace_profile_pipeline.py not found next to this script"

primary_dpc_id="${dpc_id%%,*}"
cycle_trace_name="c-trace_output_dpc_${primary_dpc_id}.json"
cycle_trace_extra_args=(--dpg-page-num "$dpg_page_num" --target-ap "$target_ap" --dpc-id "$dpc_id")
if [[ -n "$cycle_sample_mode" ]]; then
  cycle_trace_extra_args+=(--sample-mode "$cycle_sample_mode")
fi
if [[ "$enable_source_latency" == "true" ]]; then
  cycle_trace_extra_args+=(--kernel-probe)
fi

file_is_present() {
  local path="$1"
  [[ -s "$path" ]]
}

validate_cycle_trace_file() {
  local path="$1"
  PYTHONPATH="$script_dir" python3 - "$path" "$cycle_kernel_name" "$cycle_kernel_repeat" <<'PY'
import sys
from pathlib import Path
from trace_report.artifacts import validate_cycle_trace

errors = validate_cycle_trace(Path(sys.argv[1]), sys.argv[2], sys.argv[3])
if errors:
    for error in errors:
        if error.startswith("CycleTrace JSON has no hardware instruction events under CYCLE_TRACE_KERNEL_NAME="):
            print(f"[collect_trace_profile] error: [TR-COL-016] {error}", file=sys.stderr)
        else:
            print(error, file=sys.stderr)
    raise SystemExit(1)
PY
}

verify_cycle_kernel_name_against_mctracer() {
  [[ -n "$cycle_kernel_name" ]] || return 0

  local tracer_path="$run_dir/tracer_out.json"
  if [[ ! -s "$tracer_path" && -s "$run_dir/artifacts/tracer_out.json" ]]; then
    tracer_path="$run_dir/artifacts/tracer_out.json"
  fi
  [[ -s "$tracer_path" ]] || return 0

  PYTHONPATH="$script_dir" python3 - "$tracer_path" "$cycle_kernel_name" <<'PY' || return 1
import sys
from pathlib import Path
from trace_report.artifacts import cycle_kernel_candidates_from_tracer

path = Path(sys.argv[1])
requested = sys.argv[2]
candidates = cycle_kernel_candidates_from_tracer(path)
mangled = [item["mangled_name"] for item in candidates]
if requested in mangled:
    raise SystemExit(0)

display_matches = [
    item for item in candidates
    if item.get("display_name") == requested
]
print(
    "[collect_trace_profile] error: [TR-COL-016] "
    "CYCLE_TRACE_KERNEL_NAME does not match any mcTracer args.name. "
    f"current={requested}",
    file=sys.stderr,
)
if display_matches:
    print(
        "[collect_trace_profile] error: [TR-COL-016] "
        "current value matches mcTracer display name/top-level name only; "
        "use args.name for cycleTrace.kernels[].kernelName.",
        file=sys.stderr,
    )
if candidates:
    print("[collect_trace_profile] error: [TR-COL-016] candidate mcTracer args.name values:", file=sys.stderr)
    for item in candidates[:8]:
        display = item.get("display_name", "")
        suffix = f"  # display name: {display}" if display else ""
        print(f"  - {item['mangled_name']}{suffix}", file=sys.stderr)
    if len(candidates) > 8:
        print(
            "[collect_trace_profile] error: [TR-COL-016] "
            "candidate mcTracer args.name values are truncated to the first 8; "
            "if the target is not listed, inspect mcTracer tracer_out.json "
            "traceEvents[].args.name and copy the exact value.",
            file=sys.stderr,
        )
else:
    print(
        "[collect_trace_profile] error: [TR-COL-016] "
        "mcTracer JSON has no kernel launch entries with args.name/grid/block/mem.",
        file=sys.stderr,
    )
raise SystemExit(1)
PY
}

check_kernel_probe_plugin_failure() {
  local log_path="$1"
  [[ "$enable_source_latency" == "true" ]] || return 0
  [[ -s "$log_path" ]] || return 0

  if grep -q -E 'Load libpython failed|cannot acquire createPyPlugin handle|cannot load python plugin, skip kernel probe' "$log_path"; then
    echo "[collect_trace_profile] error: [TR-COL-014] cycle-trace-ng --kernel-probe Python plugin failed to load; kernel probe was skipped although cycle-trace-ng may exit 0. Ensure /opt/maca/share/cycle-trace points to the share/cycle-trace directory matching CYCLE_TRACE_BIN, or set up an equivalent compatible path. Example: docker exec <container> bash -lc 'ln -sfn <cycle-trace-install>/share/cycle-trace /opt/maca/share/cycle-trace'." >&2
    return 1
  fi
}

check_kernel_probe_objdump_path_failure() {
  local log_path="$1"
  [[ "$enable_source_latency" == "true" ]] || return 0
  [[ -s "$log_path" ]] || return 0

  if grep -q -E "Command '([^']*/)?mxobjdump --list-elf [^']*' returned non-zero exit status 127" "$log_path"; then
    echo "[collect_trace_profile] error: [TR-COL-015] cycle-trace-ng --kernel-probe Python plugin failed while running its internal mxobjdump --list-elf command; CYCLE_TRACE_BIN=$cycle_trace_bin controls the kernel-probe plugin environment, while MACA_OBJDUMP_BIN only controls trace-report source-latency post-processing. Ensure the mxobjdump and llvm-objdump paths expected by the CYCLE_TRACE_BIN plugin are available in the target environment." >&2
    return 1
  fi
}

resolve_cycle_trace_share_dir() {
  local bin_path="$cycle_trace_bin"
  local bin_dir
  if [[ "$bin_path" == */* ]]; then
    bin_dir="$(cd "$(dirname "$bin_path")" 2>/dev/null && pwd -P)" || return 1
  else
    local resolved_bin
    resolved_bin="$(command -v "$bin_path" 2>/dev/null)" || return 1
    bin_dir="$(cd "$(dirname "$resolved_bin")" 2>/dev/null && pwd -P)" || return 1
  fi

  local install_dir
  install_dir="$(cd "$bin_dir/.." 2>/dev/null && pwd -P)" || return 1
  local share_dir="$install_dir/share/cycle-trace"
  [[ -d "$share_dir" ]] || return 1
  printf '%s\n' "$share_dir"
}

pydpg_bulk_available() {
  local share_dir
  share_dir="$(resolve_cycle_trace_share_dir)" || {
    echo "[collect_trace_profile] warning: pydpg bulk fast path is not enabled because share/cycle-trace could not be resolved from CYCLE_TRACE_BIN=$cycle_trace_bin; kernel-probe may use the slower Python path." >&2
    return 1
  }

  local code_mapper="$share_dir/code_mapper.py"
  [[ -f "$code_mapper" ]] || {
    echo "[collect_trace_profile] warning: pydpg bulk fast path is not enabled because code_mapper.py is missing under $share_dir; kernel-probe may use the slower Python path." >&2
    return 1
  }
  [[ -f "$share_dir/pydpg.so" ]] || {
    echo "[collect_trace_profile] warning: pydpg bulk fast path is not enabled because pydpg.so is missing under $share_dir; kernel-probe may use the slower Python path." >&2
    return 1
  }
  [[ -f "$share_dir/pydpg_bulk.cpp" ]] || {
    echo "[collect_trace_profile] warning: pydpg bulk fast path is not enabled because pydpg_bulk.cpp is missing under $share_dir; run scripts/install_cycle_trace_pydpg_bulk.sh before collecting source-latency if kernel-probe is too slow." >&2
    return 1
  }
  [[ -s "$share_dir/pydpg_bulk.so" ]] || {
    echo "[collect_trace_profile] warning: pydpg bulk fast path is not enabled because pydpg_bulk.so is missing or empty under $share_dir; run scripts/install_cycle_trace_pydpg_bulk.sh before collecting source-latency if kernel-probe is too slow." >&2
    return 1
  }
  grep -q 'CYCLE_TRACE_USE_PYDPG_BULK' "$code_mapper" || {
    echo "[collect_trace_profile] warning: pydpg bulk fast path is not enabled because code_mapper.py is not patched with CYCLE_TRACE_USE_PYDPG_BULK; run scripts/install_cycle_trace_pydpg_bulk.sh before collecting source-latency if kernel-probe is too slow." >&2
    return 1
  }
  grep -q 'pydpg_bulk.apply_mapped_tokens' "$code_mapper" || {
    echo "[collect_trace_profile] warning: pydpg bulk fast path is not enabled because code_mapper.py does not call pydpg_bulk.apply_mapped_tokens; run scripts/install_cycle_trace_pydpg_bulk.sh before collecting source-latency if kernel-probe is too slow." >&2
    return 1
  }

  echo "[collect_trace_profile] pydpg bulk fast path enabled for kernel-probe: $share_dir" >&2
  return 0
}

have_mctracer_artifact() {
  if file_is_present "$run_dir/tracer_out.json" && validate_mctracer_file "$run_dir/tracer_out.json" "existing mcTracer JSON" "warning"; then
    return 0
  fi
  if file_is_present "$run_dir/artifacts/tracer_out.json" && validate_mctracer_file "$run_dir/artifacts/tracer_out.json" "existing mcTracer artifact JSON" "warning"; then
    return 0
  fi
  return 1
}

pipeline_scope_matches_current_kernel() {
  [[ -n "$cycle_kernel_name" ]] || return 0
  local metrics_path="$run_dir/analysis/metrics_all_${tag}.json"
  local report_path="$run_dir/REPORT_${tag}.md"
  python3 - "$metrics_path" "$report_path" "$cycle_kernel_name" "$cycle_kernel_repeat" "$cycle_sample_mode" <<'PY'
import json
import sys
from pathlib import Path

metrics_path = Path(sys.argv[1])
report_path = Path(sys.argv[2])
expected_kernel = sys.argv[3]
expected_repeat = sys.argv[4]
expected_sample_mode = sys.argv[5]

if not metrics_path.is_file() or not report_path.is_file():
    raise SystemExit(1)

try:
    metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
except Exception:
    raise SystemExit(1)
if not isinstance(metrics, dict):
    raise SystemExit(1)
scope = metrics.get("collection_scope")
if not isinstance(scope, dict):
    raise SystemExit(1)
if scope.get("cycle_trace_kernel_filter_enabled") is not True:
    raise SystemExit(1)
if scope.get("cycle_trace_kernel_name") != expected_kernel:
    raise SystemExit(1)
if str(scope.get("cycle_trace_kernel_repeat", "")) != expected_repeat:
    raise SystemExit(1)
if str(scope.get("cycle_trace_sample_mode", "")) != expected_sample_mode:
    raise SystemExit(1)

report = report_path.read_text(encoding="utf-8")
required = (
    "Performance scope: CycleTrace single-kernel filter enabled",
    f"CycleTrace target kernel: `{expected_kernel}`",
)
if any(marker not in report for marker in required):
    raise SystemExit(1)
PY
}

mcprofiler_kernel_artifacts_satisfied() {
  local base_dir="$1"
  local manifest="$base_dir/mcprofiler_per_kernel/manifest.json"
  [[ -s "$manifest" ]] || return 1
  python3 - "$manifest" "$base_dir" "$cycle_kernel_name" <<'PY'
import json
import sys
from pathlib import Path

manifest_path = Path(sys.argv[1])
base_dir = Path(sys.argv[2])
expected_kernel = sys.argv[3]

try:
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
except Exception:
    raise SystemExit(1)
if not isinstance(data, dict):
    raise SystemExit(1)
if data.get("kernel_name") != expected_kernel:
    raise SystemExit(1)

status = data.get("status")
try:
    occurrence_count = int(data.get("occurrence_count") or 0)
    complete_count = int(data.get("complete_occurrence_count") or 0)
except Exception:
    raise SystemExit(1)

if status == "single-occurrence" and occurrence_count == 1 and complete_count == 1:
    required = ("mcprofiler_report_dumped.json", "mcprofiler_report.txt.json")
    if all((base_dir / name).is_file() and (base_dir / name).stat().st_size > 0 for name in required):
        raise SystemExit(0)
    raise SystemExit(1)

if status == "multi-occurrence" and occurrence_count > 1 and occurrence_count == complete_count:
    occurrences = data.get("occurrences", [])
    if not isinstance(occurrences, list) or len(occurrences) != occurrence_count:
        raise SystemExit(1)
    for item in occurrences:
        if not isinstance(item, dict):
            raise SystemExit(1)
        occurrence_id = str(item.get("occurrence_id", ""))
        if not occurrence_id:
            raise SystemExit(1)
        occurrence_dir = base_dir / "mcprofiler_per_kernel" / occurrence_id
        required = ("mcprofiler_report_dumped.json", "mcprofiler_report.txt.json")
        if not all((occurrence_dir / name).is_file() and (occurrence_dir / name).stat().st_size > 0 for name in required):
            raise SystemExit(1)
    raise SystemExit(0)

raise SystemExit(1)
PY
}

mcprofiler_kernel_manifest_status() {
  local base_dir="$1"
  local manifest="$base_dir/mcprofiler_per_kernel/manifest.json"
  [[ -s "$manifest" ]] || return 1
  python3 - "$manifest" "$cycle_kernel_name" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
expected_kernel = sys.argv[2]
try:
    data = json.loads(path.read_text(encoding="utf-8"))
except Exception:
    raise SystemExit(1)
if not isinstance(data, dict) or data.get("kernel_name") != expected_kernel:
    raise SystemExit(1)
status = data.get("status")
if isinstance(status, str) and status:
    print(status)
    raise SystemExit(0)
raise SystemExit(1)
PY
}

have_cycle_artifact() {
  local root_path="$run_dir/$cycle_trace_name"
  local archive_path="$run_dir/artifacts/$cycle_trace_name"
  if [[ -n "$cycle_kernel_name" ]]; then
    pipeline_scope_matches_current_kernel || return 1
    if file_is_present "$archive_path"; then
      validate_cycle_trace_file "$archive_path"
      return $?
    fi
    return 1
  fi
  if file_is_present "$root_path"; then
    validate_cycle_trace_file "$root_path"
    return $?
  fi
  if file_is_present "$archive_path"; then
    validate_cycle_trace_file "$archive_path"
    return $?
  fi
  return 1
}

have_mcprofiler_artifact() {
  [[ "$skip_mcprofiler" == "true" ]] && return 1
  if [[ -n "$cycle_kernel_name" ]]; then
    mcprofiler_kernel_artifacts_satisfied "$run_dir" ||
    mcprofiler_kernel_artifacts_satisfied "$run_dir/artifacts"
    return $?
  fi
  {
    file_is_present "$run_dir/mcprofiler_report_dumped.json" &&
    file_is_present "$run_dir/mcprofiler_report.txt.json"
  } || {
    file_is_present "$run_dir/artifacts/mcprofiler_report_dumped.json" &&
    file_is_present "$run_dir/artifacts/mcprofiler_report.txt.json"
  }
}

pipeline_mcprofiler_skip_scope_matches() {
  local expected="$skip_mcprofiler"
  local metrics_key="$run_dir/analysis/metrics_key_${tag}.json"
  local manifest="$run_dir/artifacts/collection_manifest.json"
  PYTHONPATH="$script_dir" python3 - "$expected" "$metrics_key" "$manifest" <<'PY'
import json
import sys
from pathlib import Path

expected = sys.argv[1] == "true"
metrics_key = Path(sys.argv[2])
manifest = Path(sys.argv[3])

def load(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}

metric_payload = load(metrics_key)
manifest_payload = load(manifest)
scope = metric_payload.get("collection_scope", {}) if isinstance(metric_payload, dict) else {}
metric_skip = bool(scope.get("skip_mcprofiler", False)) or scope.get("mcprofiler_scope") == "skipped"
manifest_skip = (
    bool(manifest_payload.get("skip_mcprofiler", False))
    or manifest_payload.get("mcprofiler_scope") == "skipped"
) if isinstance(manifest_payload, dict) else False
if metric_skip == expected and manifest_skip == expected:
    raise SystemExit(0)
raise SystemExit(1)
PY
}

pipeline_device_scope_matches() {
  local metrics_key="$run_dir/analysis/metrics_key_${tag}.json"
  PYTHONPATH="$script_dir" python3 - "$metrics_key" "$device" "${requested_device:-$device}" "$auto_selected_device" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
selected = sys.argv[2]
requested = sys.argv[3]
auto_selected = sys.argv[4].lower() == "true"
try:
    data = json.loads(path.read_text(encoding="utf-8"))
except Exception:
    raise SystemExit(1)
scope = data.get("collection_scope", {}) if isinstance(data, dict) else {}
if not isinstance(scope, dict):
    raise SystemExit(1)
if str(scope.get("selected_maca_visible_devices", "")) != selected:
    raise SystemExit(1)
if str(scope.get("requested_maca_visible_devices", "")) != requested:
    raise SystemExit(1)
if bool(scope.get("auto_selected_device", False)) != auto_selected:
    raise SystemExit(1)
PY
}

have_pipeline_outputs() {
  if [[ -n "$cycle_kernel_name" ]]; then
    pipeline_scope_matches_current_kernel || return 1
  fi
  file_is_present "$run_dir/artifacts/tracer_out.json" &&
  file_is_present "$run_dir/artifacts/$cycle_trace_name" &&
  file_is_present "$run_dir/analysis/digest_${tag}.md" &&
	  file_is_present "$run_dir/analysis/metrics_all_${tag}.json" &&
	  file_is_present "$run_dir/analysis/metrics_key_${tag}.json" &&
		  file_is_present "$run_dir/REPORT_${tag}.md" &&
  pipeline_device_scope_matches &&
	  pipeline_mcprofiler_skip_scope_matches &&
	  {
    [[ "$require_mcprofiler" != "true" ]] ||
    {
      [[ -n "$cycle_kernel_name" ]] &&
      mcprofiler_kernel_artifacts_satisfied "$run_dir/artifacts"
    } ||
    {
      [[ -z "$cycle_kernel_name" ]] &&
      file_is_present "$run_dir/artifacts/mcprofiler_report_dumped.json" &&
      file_is_present "$run_dir/artifacts/mcprofiler_report.txt.json"
    }
  }
}

restore_raw_from_artifacts() {
  local name="$1"
  if [[ ! -s "$run_dir/$name" && -s "$run_dir/artifacts/$name" ]]; then
    cp "$run_dir/artifacts/$name" "$run_dir/$name"
  fi
}

restore_tool_raws_from_artifacts() {
  restore_raw_from_artifacts "tracer_out.json"
  if [[ -z "$cycle_kernel_name" ]] || pipeline_scope_matches_current_kernel; then
    for part in "${dpc_id_parts[@]}"; do
      restore_raw_from_artifacts "c-trace_output_dpc_${part}.json"
    done
  fi
  if [[ -z "$cycle_kernel_name" ]]; then
    [[ "$skip_mcprofiler" == "true" ]] && return 0
    restore_raw_from_artifacts "mcprofiler_report_dumped.json"
    restore_raw_from_artifacts "mcprofiler_report.txt.json"
    restore_raw_from_artifacts "mcprofiler_report.txt"
    restore_raw_from_artifacts "mcprofiler_report.txt.csv"
  fi
  if [[ -d "$run_dir/artifacts" ]]; then
    [[ "$skip_mcprofiler" == "true" ]] && return 0
    if [[ -z "$cycle_kernel_name" ]]; then
      find "$run_dir/artifacts" -maxdepth 1 -name '*.png*' -type f -exec cp -n {} "$run_dir/" \;
    elif mcprofiler_kernel_artifacts_satisfied "$run_dir/artifacts"; then
      local profiler_status
      profiler_status="$(mcprofiler_kernel_manifest_status "$run_dir/artifacts" || true)"
      if [[ "$profiler_status" == "single-occurrence" ]]; then
        restore_raw_from_artifacts "mcprofiler_report_dumped.json"
        restore_raw_from_artifacts "mcprofiler_report.txt.json"
        restore_raw_from_artifacts "mcprofiler_report.txt"
        restore_raw_from_artifacts "mcprofiler_report.txt.csv"
        find "$run_dir/artifacts" -maxdepth 1 -name '*.png*' -type f -exec cp -n {} "$run_dir/" \;
      fi
      if [[ ! -d "$run_dir/mcprofiler_per_kernel" && -d "$run_dir/artifacts/mcprofiler_per_kernel" ]]; then
        cp -R "$run_dir/artifacts/mcprofiler_per_kernel" "$run_dir/mcprofiler_per_kernel"
      fi
    fi
    if [[ -z "$cycle_kernel_name" && ! -d "$run_dir/mcprofiler_per_kernel" && -d "$run_dir/artifacts/mcprofiler_per_kernel" ]]; then
      cp -R "$run_dir/artifacts/mcprofiler_per_kernel" "$run_dir/mcprofiler_per_kernel"
    fi
  fi
}

cleanup_marker_workdir_outputs() {
  local marker_path="${1:-}"
  if [[ -n "$marker_path" && -f "$marker_path" ]]; then
    find "$workdir" -maxdepth 1 -type d -name 'tracer_out_*' -newer "$marker_path" -exec rm -rf {} +
    find "$workdir" -maxdepth 1 -type f \( -name '.2*.db' -o -name 'c-trace*.json' \) -newer "$marker_path" -delete
    cleanup_lineinfo_cycle_workdir_outputs "$marker_path"
  fi
}

cleanup_mctracer_failure() {
  local marker_path="${1:-}"
  if [[ -n "$marker_path" && -f "$marker_path" ]]; then
    find "$workdir" -maxdepth 1 -type d -name 'tracer_out_*' -newer "$marker_path" -exec rm -rf {} +
  fi
  rm -f "$run_dir/tracer_out.json"
}

cleanup_cycle_failure() {
  local marker_path="${1:-}"
  if [[ -n "$marker_path" && -f "$marker_path" ]]; then
    find "$workdir" -maxdepth 1 -type f \( -name '.2*.db' -o -name 'c-trace*.json' \) -newer "$marker_path" -delete
    cleanup_lineinfo_cycle_workdir_outputs "$marker_path"
  fi
  rm -f "$run_dir"/c-trace_output_dpc_*.json
}

cleanup_mcprofiler_failure() {
  local marker_path="${1:-}"
  rm -f "$run_dir/mcprofiler_report_dumped.json" "$run_dir/mcprofiler_report.txt.json" \
    "$run_dir/mcprofiler_report.txt" "$run_dir/mcprofiler_report.txt.csv"
  rm -rf "$run_dir/mcprofiler_per_kernel"
  rm -rf "$profiler_out"
  if [[ -n "$marker_path" && -f "$marker_path" ]]; then
    find "$run_dir" -maxdepth 1 -type f -name '*.png*' -newer "$marker_path" -delete
  fi
}

cleanup_pipeline_failure() {
  rm -f \
    "$run_dir/REPORT_${tag}.md" \
    "$run_dir/analysis/digest_${tag}.md" \
    "$run_dir/analysis/metrics_all_${tag}.json" \
    "$run_dir/analysis/metrics_key_${tag}.json"
}

warn_optional_mcprofiler() {
  local reason="$1"
  echo "[collect_trace_profile] warning: mcProfiler is optional because REQUIRE_MCPROFILER=false; ${reason}. Continuing without mcProfiler. mcProfiler-dependent duty, IPC, cache, bank-conflict, and Roofline evidence will be N/A. Current limit COLLECT_TIMEOUT_SECONDS=${collect_timeout_seconds}s; increase COLLECT_TIMEOUT_SECONDS and rerun the same run directory if profiler evidence is needed." >&2
}

write_stage_reuse_log() {
  local log_path="$1"
  local label="$2"
  local source_path="$3"
  mkdir -p "$(dirname "$log_path")"
  {
    echo "# $label"
    echo
    echo "status: reused"
    echo "command_executed: false"
    echo "source: $source_path"
    echo "note: existing valid artifact reused in this collect-run attempt"
  } > "$log_path"
}

write_stage_skip_log() {
  local log_path="$1"
  local label="$2"
  local reason="$3"
  mkdir -p "$(dirname "$log_path")"
  {
    echo "# $label"
    echo
    echo "status: skipped"
    echo "command_executed: false"
    echo "reason: $reason"
  } > "$log_path"
}

mctracer_artifact_source() {
  if file_is_present "$run_dir/tracer_out.json" && validate_mctracer_file "$run_dir/tracer_out.json" "existing mcTracer JSON" "warning" >/dev/null 2>&1; then
    echo "$run_dir/tracer_out.json"
    return 0
  fi
  if file_is_present "$run_dir/artifacts/tracer_out.json" && validate_mctracer_file "$run_dir/artifacts/tracer_out.json" "existing mcTracer artifact JSON" "warning" >/dev/null 2>&1; then
    echo "$run_dir/artifacts/tracer_out.json"
    return 0
  fi
  echo "unknown"
}

cycle_artifact_source() {
  local root_path="$run_dir/$cycle_trace_name"
  local archive_path="$run_dir/artifacts/$cycle_trace_name"
  if [[ -n "$cycle_kernel_name" ]]; then
    echo "$archive_path"
    return 0
  fi
  if file_is_present "$root_path" && validate_cycle_trace_file "$root_path" >/dev/null 2>&1; then
    echo "$root_path"
    return 0
  fi
  if file_is_present "$archive_path" && validate_cycle_trace_file "$archive_path" >/dev/null 2>&1; then
    echo "$archive_path"
    return 0
  fi
  echo "unknown"
}

mcprofiler_artifact_source() {
  if [[ -n "$cycle_kernel_name" ]]; then
    if mcprofiler_kernel_artifacts_satisfied "$run_dir"; then
      echo "$run_dir/mcprofiler_per_kernel"
      return 0
    fi
    if mcprofiler_kernel_artifacts_satisfied "$run_dir/artifacts"; then
      echo "$run_dir/artifacts/mcprofiler_per_kernel"
      return 0
    fi
    echo "unknown"
    return 0
  fi
  if file_is_present "$run_dir/mcprofiler_report_dumped.json" && file_is_present "$run_dir/mcprofiler_report.txt.json"; then
    echo "$run_dir"
    return 0
  fi
  if file_is_present "$run_dir/artifacts/mcprofiler_report_dumped.json" && file_is_present "$run_dir/artifacts/mcprofiler_report.txt.json"; then
    echo "$run_dir/artifacts"
    return 0
  fi
  echo "unknown"
}

collect_profile() {
  local marker
  marker="$(mktemp "$run_dir/.collect.XXXXXX")"
  current_marker="$marker"
  mkdir -p "$run_dir/logs"

  echo "Starting trace profile collection"

  if have_mctracer_artifact; then
    echo "[Step 1/4] Reusing existing mcTracer artifact"
    write_stage_reuse_log "$run_dir/logs/mctracer.log" "mcTracer" "$(mctracer_artifact_source)"
	  else
	    echo "[Step 1/4] Running mcTracer..."
	    mctracer_log="$run_dir/logs/mctracer.log"
	    set +e
	    run_tool_with_timeout "mcTracer" env MACA_VISIBLE_DEVICES="$device" "$mctracer_bin" "${exec_argv[@]}" \
	      > "$mctracer_log" 2>&1
	    local mctracer_status=$?
	    set -e
	    if [[ "$mctracer_status" -ne 0 ]]; then
	      echo "[collect_trace_profile] mcTracer log: $mctracer_log" >&2
	      cleanup_mctracer_failure "$marker"
	      return "$mctracer_status"
	    fi
	    tracer_json="$(find . -path './tracer_out_*/tracer_out-*.json' -newer "$marker" -type f 2>/dev/null | sort | tail -n 1 || true)"
	    [[ -n "$tracer_json" ]] || {
	      echo "[collect_trace_profile] error: [TR-COL-005] [Step 1/4] mcTracer output not found: tracer_out_*/tracer_out-*.json" >&2
	      echo "[collect_trace_profile] mcTracer log: $mctracer_log" >&2
	      cleanup_mctracer_failure "$marker"
	      return 1
	    }
	    cp "$tracer_json" "$run_dir/tracer_out.json" || {
	      echo "[collect_trace_profile] mcTracer log: $mctracer_log" >&2
	      cleanup_mctracer_failure "$marker"
	      return 1
	    }
	    require_file "$run_dir/tracer_out.json" "mcTracer JSON" "TR-COL-005" || {
	      echo "[collect_trace_profile] mcTracer log: $mctracer_log" >&2
	      cleanup_mctracer_failure "$marker"
	      return 1
	    }
	    validate_mctracer_file "$run_dir/tracer_out.json" "mcTracer JSON" || {
	      echo "[collect_trace_profile] mcTracer log: $mctracer_log" >&2
	      cleanup_mctracer_failure "$marker"
	      return 1
	    }
    echo "[Step 1/4] mcTracer -> $run_dir/tracer_out.json"
  fi

  verify_cycle_kernel_name_against_mctracer || {
    cleanup_cycle_failure "$marker"
    return 1
  }

  if have_cycle_artifact; then
    echo "[Step 2/4] Reusing existing CycleTrace artifact"
    write_stage_reuse_log "$run_dir/logs/cycle-trace-ng.log" "CycleTrace" "$(cycle_artifact_source)"
  else
	    echo "[Step 2/4] Running cycle-trace-ng..."
	    cycle_log="$run_dir/logs/cycle-trace-ng.log"
	    : > "$cycle_log"
	    cycle_trace_env=(ENABLE_DPG=1 ENABLE_DPG_DUMP=1 ISU_FASTMODEL=0 MACA_VISIBLE_DEVICES="$device")
    if [[ "$enable_source_latency" == "true" ]]; then
      if pydpg_bulk_available 2>> "$cycle_log"; then
        cycle_trace_env+=(CYCLE_TRACE_USE_PYDPG_BULK=1 CYCLE_TRACE_PYDPG_BULK_STRICT=1)
      fi
    fi
    set +e
	    run_tool_with_timeout "cycle-trace-ng" env "${cycle_trace_env[@]}" \
	      "$cycle_trace_bin" "${cycle_exec_argv[@]}" --format json --target-peu "$target_peu" "${cycle_trace_extra_args[@]}" \
	      >> "$cycle_log" 2>&1
    cycle_status=$?
    set -e
    if [[ "$cycle_status" -ne 124 ]]; then
      check_kernel_probe_plugin_failure "$cycle_log" || {
        echo "[collect_trace_profile] cycle-trace-ng log: $cycle_log" >&2
        cleanup_cycle_failure "$marker"
        return 1
      }
      check_kernel_probe_objdump_path_failure "$cycle_log" || {
        echo "[collect_trace_profile] cycle-trace-ng log: $cycle_log" >&2
        cleanup_cycle_failure "$marker"
        return 1
      }
    fi
    if [[ "$cycle_status" -ne 0 ]]; then
      cleanup_cycle_failure "$marker"
      if [[ "$cycle_status" -eq 124 ]]; then
        echo "[collect_trace_profile] cycle-trace-ng log: $cycle_log" >&2
        return 124
      fi
      if [[ "$cycle_status" -eq 127 ]] || grep -q "command not found" "$cycle_log"; then
        echo "[collect_trace_profile] error: [TR-COL-007] cycle-trace-ng: command not found" >&2
      fi
      if grep -q -E '(\*\*\* SIGSEGV DETECT! \*\*\*|Dispatch ttrace dump start packet|dpc[0-7] copy tokens has done)' "$cycle_log"; then
        echo "[collect_trace_profile] error: [TR-COL-009] cycle-trace-ng failed after DPG trace activity; inspect SIGSEGV or DPG dump logs" >&2
      fi
      echo "[collect_trace_profile] cycle-trace-ng log: $cycle_log" >&2
      return 1
    fi
    cycle_json="$(find . -maxdepth 1 -name "$cycle_trace_name" -newer "$marker" -type f 2>/dev/null | sort | head -n 1 || true)"
	    [[ -n "$cycle_json" ]] || {
	      echo "[collect_trace_profile] error: [TR-COL-006] [Step 2/4] CycleTrace output not found: $cycle_trace_name" >&2
	      echo "[collect_trace_profile] error: [TR-COL-008] cycle-trace-ng --format json did not produce a primary CycleTrace JSON file" >&2
	      echo "[collect_trace_profile] cycle-trace-ng log: $cycle_log" >&2
	      cleanup_cycle_failure "$marker"
	      return 1
	    }
    for part in "${dpc_id_parts[@]}"; do
      local dpc_trace_name="c-trace_output_dpc_${part}.json"
      local dpc_cycle_json
      dpc_cycle_json="$(find . -maxdepth 1 -name "$dpc_trace_name" -newer "$marker" -type f 2>/dev/null | sort | head -n 1 || true)"
      if [[ -n "$dpc_cycle_json" ]]; then
        cp "$dpc_cycle_json" "$run_dir/$dpc_trace_name" || {
          cleanup_cycle_failure "$marker"
          return 1
        }
        echo "[Step 2/4] CycleTrace -> $run_dir/$dpc_trace_name"
      fi
    done
	    require_file "$run_dir/$cycle_trace_name" "CycleTrace JSON" "TR-COL-006" || {
	      echo "[collect_trace_profile] cycle-trace-ng log: $cycle_log" >&2
	      cleanup_cycle_failure "$marker"
	      return 1
	    }
	    validate_cycle_trace_file "$run_dir/$cycle_trace_name" || {
	      echo "[collect_trace_profile] error: [TR-COL-006] CycleTrace JSON is invalid: $run_dir/$cycle_trace_name" >&2
	      echo "[collect_trace_profile] cycle-trace-ng log: $cycle_log" >&2
	      cleanup_cycle_failure "$marker"
	      return 1
	    }
	  fi

	  if [[ "$skip_mcprofiler" == "true" ]]; then
	    echo "[Step 3/4] Skipping mcProfiler because SKIP_MCPROFILER=true"
	    cleanup_mcprofiler_failure "$marker"
	    write_stage_skip_log "$run_dir/logs/mcprofiler.log" "mcProfiler" "SKIP_MCPROFILER=true"
	  elif have_mcprofiler_artifact; then
	    echo "[Step 3/4] Reusing existing mcProfiler artifacts"
	    write_stage_reuse_log "$run_dir/logs/mcprofiler.log" "mcProfiler" "$(mcprofiler_artifact_source)"
	  elif [[ "$require_mcprofiler" != "true" && ! -x "$mcprofiler_bin" ]]; then
	    write_stage_skip_log "$run_dir/logs/mcprofiler.log" "mcProfiler" "mcProfiler is not executable and REQUIRE_MCPROFILER=false"
	    warn_optional_mcprofiler "mcProfiler is not executable: $mcprofiler_bin"
  else
    echo "[Step 3/4] Running mcProfiler..."
    rm -rf "$profiler_out"
    mcprofiler_log="$run_dir/logs/mcprofiler.log"
    : > "$mcprofiler_log"
    mcprofiler_timeout_args=()
    if [[ "$require_mcprofiler" != "true" ]]; then
      mcprofiler_timeout_args=(--timeout-warning-only)
    fi
    mcprofiler_kernel_args=()
    if [[ -n "$cycle_kernel_name" ]]; then
      mcprofiler_kernel_args=(--kernelnames "$cycle_kernel_name" --per-kernel)
    fi
    set +e
    run_tool_with_timeout "mcProfiler" "${mcprofiler_timeout_args[@]}" env \
      -u HTTP_PROXY -u HTTPS_PROXY -u ALL_PROXY -u FTP_PROXY \
      -u http_proxy -u https_proxy -u all_proxy -u ftp_proxy \
      NO_PROXY='*' no_proxy='*' \
      MACA_VISIBLE_DEVICES="$device" "$mcprofiler_bin" perf_exec \
      --cmdline "$profiler_cmdline" \
      "${mcprofiler_kernel_args[@]}" \
      --casename "$case_name" \
      --output "$profiler_out" \
      --port "$profiler_port" >> "$mcprofiler_log" 2>&1
    local mcprofiler_status=$?
    set -e
    if [[ "$mcprofiler_status" -ne 0 ]]; then
      cleanup_mcprofiler_failure "$marker"
	      if [[ "$require_mcprofiler" == "true" ]]; then
	        if [[ "$mcprofiler_status" -ne 124 ]]; then
	          echo "[collect_trace_profile] error: [TR-COL-010] mcProfiler command failed with exit=${mcprofiler_status}; REQUIRE_MCPROFILER=true requires complete mcProfiler artifacts. Current limit COLLECT_TIMEOUT_SECONDS=${collect_timeout_seconds}s; increase COLLECT_TIMEOUT_SECONDS in the active config and rerun collect-run if mcProfiler needs more time." >&2
	        fi
	        echo "[collect_trace_profile] mcProfiler log: $mcprofiler_log" >&2
	        return "$mcprofiler_status"
	      fi
      if [[ "$mcprofiler_status" -eq 124 ]]; then
        warn_optional_mcprofiler "mcProfiler timed out"
      else
        warn_optional_mcprofiler "mcProfiler command failed with exit=${mcprofiler_status}"
      fi
    else

	      if [[ ! -d "$profiler_out" ]]; then
	        cleanup_mcprofiler_failure "$marker"
	        if [[ "$require_mcprofiler" == "true" ]]; then
	          echo "[collect_trace_profile] error: [TR-COL-010] [Step 3/4] mcProfiler output directory not found: $profiler_out" >&2
	          echo "[collect_trace_profile] mcProfiler log: $mcprofiler_log" >&2
	          return 1
	        fi
        warn_optional_mcprofiler "mcProfiler output directory not found: $profiler_out"
      else
        if [[ -n "$cycle_kernel_name" ]]; then
          set +e
	          PYTHONPATH="$script_dir" python3 -m trace_report.mcprofiler_artifacts normalize \
	            --source-dir "$profiler_out" \
	            --run-dir "$run_dir" \
	            --kernel-name "$cycle_kernel_name" \
	            --require-mcprofiler "$require_mcprofiler" >> "$mcprofiler_log" 2>&1
          local normalize_status=$?
          set -e
          if [[ "$normalize_status" -ne 0 ]]; then
            cleanup_mcprofiler_failure "$marker"
	            if [[ "$require_mcprofiler" == "true" ]]; then
	              echo "[collect_trace_profile] error: [TR-COL-011] mcProfiler per-kernel artifacts are required by REQUIRE_MCPROFILER=true but could not be normalized for CYCLE_TRACE_KERNEL_NAME=$cycle_kernel_name" >&2
	              echo "[collect_trace_profile] mcProfiler log: $mcprofiler_log" >&2
	              return "$normalize_status"
	            fi
            warn_optional_mcprofiler "mcProfiler per-kernel artifacts could not be normalized"
          else
            rm -rf "$profiler_out"
            echo "[Step 3/4] mcProfiler per-kernel artifacts normalized under $run_dir"
          fi
        else
          if [[ -f "$profiler_out/report_dumped_result.json" ]]; then
            cp "$profiler_out/report_dumped_result.json" "$run_dir/mcprofiler_report_dumped.json" || {
              cleanup_mcprofiler_failure "$marker"
              return 1
            }
          elif [[ -f "$profiler_out/mcprofiler_report_dumped.json" ]]; then
            cp "$profiler_out/mcprofiler_report_dumped.json" "$run_dir/mcprofiler_report_dumped.json" || {
              cleanup_mcprofiler_failure "$marker"
              return 1
            }
          fi
          if [[ ! -s "$run_dir/mcprofiler_report_dumped.json" ]]; then
            cleanup_mcprofiler_failure "$marker"
            if [[ "$require_mcprofiler" == "true" ]]; then
	          echo "[collect_trace_profile] error: [TR-COL-011] mcProfiler artifacts are required by REQUIRE_MCPROFILER=true but dumped JSON is missing; current limit COLLECT_TIMEOUT_SECONDS=${collect_timeout_seconds}s. Increase COLLECT_TIMEOUT_SECONDS in the active config and rerun collect-run if mcProfiler needs more time." >&2
	          echo "[collect_trace_profile] mcProfiler log: $mcprofiler_log" >&2
	          return 1
	        fi
            warn_optional_mcprofiler "mcProfiler dumped JSON is missing"
          elif [[ -f "$profiler_out/report.txt.json" ]]; then
            cp "$profiler_out/report.txt.json" "$run_dir/mcprofiler_report.txt.json" || {
              cleanup_mcprofiler_failure "$marker"
              return 1
            }
          elif [[ -f "$profiler_out/mcprofiler_report.txt.json" ]]; then
            cp "$profiler_out/mcprofiler_report.txt.json" "$run_dir/mcprofiler_report.txt.json" || {
              cleanup_mcprofiler_failure "$marker"
              return 1
            }
          fi
          if [[ -s "$run_dir/mcprofiler_report_dumped.json" && ! -s "$run_dir/mcprofiler_report.txt.json" ]]; then
            cleanup_mcprofiler_failure "$marker"
            if [[ "$require_mcprofiler" == "true" ]]; then
	              echo "[collect_trace_profile] error: [TR-COL-011] mcProfiler artifacts are required by REQUIRE_MCPROFILER=true but report text JSON is missing; current limit COLLECT_TIMEOUT_SECONDS=${collect_timeout_seconds}s. Increase COLLECT_TIMEOUT_SECONDS in the active config and rerun collect-run if mcProfiler needs more time." >&2
	              echo "[collect_trace_profile] mcProfiler log: $mcprofiler_log" >&2
	              return 1
	            fi
            warn_optional_mcprofiler "mcProfiler report text JSON is missing"
          elif [[ -s "$run_dir/mcprofiler_report_dumped.json" && -s "$run_dir/mcprofiler_report.txt.json" ]]; then
            find "$profiler_out" -maxdepth 1 -name '*.png*' -type f -exec cp {} "$run_dir/" \;
            rm -rf "$profiler_out"
            echo "[Step 3/4] mcProfiler artifacts normalized under $run_dir"
          fi
        fi
      fi
    fi
  fi

  if have_pipeline_outputs; then
    echo "[Step 4/4] Reusing existing trace profile pipeline outputs"
    write_stage_reuse_log "$run_dir/logs/trace-profile-pipeline.log" "trace_profile_pipeline.py" "$run_dir/analysis"
  else
    echo "[Step 4/4] Running trace_profile_pipeline.py..."
    pipeline_log="$run_dir/logs/trace-profile-pipeline.log"
    : > "$pipeline_log"
    restore_tool_raws_from_artifacts
    cleanup_pipeline_failure
    if [[ -s "$run_dir/tracer_out.json" && -s "$run_dir/$cycle_trace_name" ]]; then
      TRACE_REPORT_REQUESTED_MACA_VISIBLE_DEVICES="$requested_device" \
      TRACE_REPORT_SELECTED_MACA_VISIBLE_DEVICES="$device" \
      TRACE_REPORT_AUTO_SELECTED_DEVICE="$auto_selected_device" \
      python3 "$script_dir/trace_profile_pipeline.py" run \
        --source "$run_dir" \
        --run-dir "$run_dir" \
        --tag "$tag" \
        --heuristic-bound-mode "$heuristic_bound_mode" \
        --cycle-dpc-id "$dpc_id" \
	        --cycle-kernel-name "$cycle_kernel_name" \
		        --cycle-kernel-repeat "$cycle_kernel_repeat" \
		        --cycle-sample-mode "$cycle_sample_mode" \
		        --require-mcprofiler "$require_mcprofiler" \
		        --skip-mcprofiler "$skip_mcprofiler" >> "$pipeline_log" 2>&1 || {
	          echo "[collect_trace_profile] trace_profile_pipeline.py log: $pipeline_log" >&2
	          cleanup_pipeline_failure
	          return 1
	        }
    elif [[ -s "$run_dir/artifacts/tracer_out.json" && -s "$run_dir/artifacts/$cycle_trace_name" ]]; then
      TRACE_REPORT_REQUESTED_MACA_VISIBLE_DEVICES="$requested_device" \
      TRACE_REPORT_SELECTED_MACA_VISIBLE_DEVICES="$device" \
      TRACE_REPORT_AUTO_SELECTED_DEVICE="$auto_selected_device" \
      python3 "$script_dir/trace_profile_pipeline.py" analyze \
        --run-dir "$run_dir" \
        --artifact-dir "$run_dir/artifacts" \
        --tag "$tag" \
        --heuristic-bound-mode "$heuristic_bound_mode" \
        --cycle-dpc-id "$dpc_id" \
	        --cycle-kernel-name "$cycle_kernel_name" \
		        --cycle-kernel-repeat "$cycle_kernel_repeat" \
		        --cycle-sample-mode "$cycle_sample_mode" \
		        --require-mcprofiler "$require_mcprofiler" \
		        --skip-mcprofiler "$skip_mcprofiler" >> "$pipeline_log" 2>&1 || {
	          echo "[collect_trace_profile] trace_profile_pipeline.py log: $pipeline_log" >&2
	          cleanup_pipeline_failure
	          return 1
	        }
	    else
	      echo "[collect_trace_profile] error: [TR-COL-012] required trace tool outputs are incomplete before pipeline" >&2
	      echo "[collect_trace_profile] trace_profile_pipeline.py log: $pipeline_log" >&2
	      cleanup_pipeline_failure
	      return 1
    fi
  fi

  require_file "$run_dir/artifacts/tracer_out.json" "archived mcTracer JSON" "TR-COL-012" || return 1
  require_file "$run_dir/artifacts/$cycle_trace_name" "archived CycleTrace JSON" "TR-COL-012" || return 1
  if [[ "$require_mcprofiler" == "true" && "$skip_mcprofiler" != "true" ]]; then
    if ! mcprofiler_required_artifacts_satisfied "$run_dir/artifacts"; then
      echo "[collect_trace_profile] error: [TR-COL-012] missing/invalid required output: archived mcProfiler JSON or complete mcProfiler per-kernel multi-occurrence manifest ($run_dir/artifacts)" >&2
      return 1
    fi
  fi
  require_file "$run_dir/analysis/digest_${tag}.md" "digest Markdown" "TR-COL-012" || return 1
  require_file "$run_dir/analysis/metrics_all_${tag}.json" "full metrics JSON" "TR-COL-012" || return 1
  require_file "$run_dir/analysis/metrics_key_${tag}.json" "key metrics JSON" "TR-COL-012" || return 1
  require_file "$run_dir/REPORT_${tag}.md" "final report" "TR-COL-012" || return 1

  cleanup_marker_workdir_outputs "$marker"
  rm -f "$marker"
  current_marker=""
}

echo "=========================================="
echo " Trace profile collection"
echo " workdir:      $workdir"
echo " run_dir:      $run_dir"
echo " tag:          $tag"
echo " exec_cmd:     $exec_cmd"
if [[ "$enable_source_latency" == "true" ]]; then
  echo " cycle_exec:   $cycle_exec_cmd"
fi
echo " case_name:    $case_name"
echo " device:       $device"
echo " requested_device: $requested_device"
echo " auto_selected_device: $auto_selected_device"
echo " target_peu:   $target_peu"
echo " dpg_page_num: $dpg_page_num"
echo " target_ap:    $target_ap"
echo " dpc_id:       $dpc_id"
echo " sample_mode:  ${cycle_sample_mode:-tool-default}"
echo " cycle_kernel: ${cycle_kernel_name:-none}"
if [[ -n "$cycle_kernel_name" ]]; then
  echo " cycle_repeat: $cycle_kernel_repeat"
  echo " tools_json:   $cycle_tools_json"
fi
echo " heuristic_bound_mode: $heuristic_bound_mode"
echo " collect_timeout_seconds: $collect_timeout_seconds"
  echo " require_mcprofiler: $require_mcprofiler"
  echo " skip_mcprofiler: $skip_mcprofiler"
echo " profiler_port: $profiler_port"
echo " enable_source_latency: $enable_source_latency"
echo "=========================================="

cd "$workdir"
configure_cycle_trace_tools_json
verify_cycle_trace_tools_json_kernel_filter || die "TR-COL-004" "failed to verify CycleTrace single-kernel filter in tools.json"

tool_collect_remaining_seconds="$collect_timeout_seconds"

set +e
collect_profile
collect_status=$?
set -e

if [[ "$collect_status" -ne 0 ]]; then
  cleanup_workdir_temporary_outputs
  if [[ -n "$current_marker" && -f "$current_marker" ]]; then
    rm -f "$current_marker"
  fi
  current_marker=""
  if [[ "$collect_status" -eq 124 ]]; then
    die "TR-COL-013" "trace tool collection timed out; current limit COLLECT_TIMEOUT_SECONDS=${collect_timeout_seconds}s. Current failed stage outputs and workdir temporary outputs were deleted; previously completed run_dir stage outputs were kept. Increase COLLECT_TIMEOUT_SECONDS in the active config and rerun collect-run if this collection needs more time."
  fi
  echo "[collect_trace_profile] error: collection failed. Current failed stage outputs and workdir temporary outputs were deleted; previously completed run_dir stage outputs were kept." >&2
  exit "$collect_status"
fi

cleanup_workdir_temporary_outputs

echo "=========================================="
echo " Trace profile complete"
echo " report: $run_dir/REPORT_${tag}.md"
echo " metrics: $run_dir/analysis/metrics_all_${tag}.json"
echo "=========================================="
