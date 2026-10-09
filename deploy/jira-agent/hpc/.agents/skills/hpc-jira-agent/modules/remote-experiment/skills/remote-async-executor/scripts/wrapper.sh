#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 3 ]]; then
  echo "用法: wrapper.sh <job_dir> <job_id> <run_script> [artifacts_file] [artifact_root]" >&2
  exit 2
fi

job_dir="$1"
job_id="$2"
run_script="$3"
artifacts_file="${4:-}"
artifact_root="${5:-$(dirname "${run_script}")}"

meta_file="${job_dir}/meta.json"
status_file="${job_dir}/status.json"
stdout_file="${job_dir}/stdout.log"
stderr_file="${job_dir}/stderr.log"
result_file="${job_dir}/result.json"
heartbeat_file="${job_dir}/heartbeat.txt"

mkdir -p "${job_dir}"
touch "${stdout_file}" "${stderr_file}"

iso_now() {
  date +"%Y-%m-%dT%H:%M:%S%z"
}

json_escape() {
  local input="${1:-}"
  input="${input//\\/\\\\}"
  input="${input//\"/\\\"}"
  input="${input//$'\r'/}"
  input="${input//$'\n'/\\n}"
  printf '%s' "${input}"
}

write_status() {
  local state="$1"
  local stage="$2"
  local progress="${3:-}"
  local exit_code="${4:-null}"
  cat > "${status_file}" <<EOF
{
  "job_id": "$(json_escape "${job_id}")",
  "state": "$(json_escape "${state}")",
  "current_stage": "$(json_escape "${stage}")",
  "progress": "$(json_escape "${progress}")",
  "start_time": "$(json_escape "${start_time}")",
  "update_time": "$(json_escape "$(iso_now)")",
  "exit_code": ${exit_code}
}
EOF
}

write_meta() {
  cat > "${meta_file}" <<EOF
{
  "job_id": "$(json_escape "${job_id}")",
  "created_at": "$(json_escape "${start_time}")",
  "timezone": "$(json_escape "${timezone_name}")",
  "wrapper_pid": $$,
  "run_script": "$(json_escape "${run_script}")",
  "artifact_root": "$(json_escape "${artifact_root}")",
  "host": "$(json_escape "${host_name}")",
  "tmux_session": "$(json_escape "${TMUX:-}")"
}
EOF
}

write_heartbeat() {
  printf '%s\n' "$(iso_now)" > "${heartbeat_file}"
}

collect_artifacts_json() {
  if [[ -z "${artifacts_file}" || ! -f "${artifacts_file}" ]]; then
    printf '[]'
    return
  fi

  local artifact_full_path=""
  local first=1
  printf '['
  while IFS= read -r artifact_path; do
    [[ -z "${artifact_path}" ]] && continue
    if [[ ${first} -eq 0 ]]; then
      printf ','
    fi
    first=0
    if [[ "${artifact_path}" = /* ]]; then
      artifact_full_path="${artifact_path}"
    else
      artifact_full_path="${artifact_root%/}/${artifact_path}"
    fi

    if [[ -e "${artifact_full_path}" ]]; then
      printf '{"path":"%s","exists":true}' "$(json_escape "${artifact_path}")"
    else
      printf '{"path":"%s","exists":false}' "$(json_escape "${artifact_path}")"
    fi
  done < "${artifacts_file}"
  printf ']'
}

write_result() {
  local state="$1"
  local exit_code="$2"
  local summary="$3"
  local artifacts_json
  artifacts_json="$(collect_artifacts_json)"
  cat > "${result_file}" <<EOF
{
  "job_id": "$(json_escape "${job_id}")",
  "state": "$(json_escape "${state}")",
  "exit_code": ${exit_code},
  "finished_at": "$(json_escape "$(iso_now)")",
  "summary": "$(json_escape "${summary}")",
  "artifacts": ${artifacts_json}
}
EOF
}

heartbeat_loop() {
  while true; do
    write_heartbeat
    sleep 15
  done
}

finish() {
  local state="$1"
  local exit_code="$2"
  local summary="$3"

  if [[ -n "${heartbeat_pid:-}" ]]; then
    kill "${heartbeat_pid}" >/dev/null 2>&1 || true
    wait "${heartbeat_pid}" 2>/dev/null || true
  fi

  write_status "${state}" "finished" "" "${exit_code}"
  write_result "${state}" "${exit_code}" "${summary}"
}

handle_cancel() {
  echo "任务已取消" >> "${stderr_file}"
  finish "canceled" 130 "远程任务已取消"
  exit 130
}

trap handle_cancel HUP INT TERM

start_time="$(iso_now)"
timezone_name="${TZ:-$(date +%Z)}"
host_name="$(hostname)"

write_meta
write_status "starting" "wrapper_init" ""
write_heartbeat
heartbeat_loop &
heartbeat_pid=$!

if [[ ! -x "${run_script}" ]]; then
  chmod +x "${run_script}"
fi

write_status "running" "executing" ""
set +e
"${run_script}" >> "${stdout_file}" 2>> "${stderr_file}"
command_exit_code=$?
set -e

write_status "collecting" "collecting_artifacts" ""

if [[ ${command_exit_code} -eq 0 ]]; then
  finish "succeeded" "${command_exit_code}" "远程测试执行成功"
else
  finish "failed" "${command_exit_code}" "远程测试执行失败"
fi

exit "${command_exit_code}"
