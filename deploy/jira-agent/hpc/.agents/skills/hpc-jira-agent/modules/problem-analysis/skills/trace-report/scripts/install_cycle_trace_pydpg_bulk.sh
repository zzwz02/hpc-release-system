#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Ensure the cycle-trace pydpg bulk write helper is installed.

This script first checks whether the target cycle-trace code_mapper.py is
already patched and pydpg_bulk.so is available. If not, it patches
code_mapper.py in place and builds pydpg_bulk.so against the target machine's
bundled cycle-trace Python/pybind11.

Usage:
  install_cycle_trace_pydpg_bulk.sh [--cycle-trace-dir DIR] [--print-env]

Environment:
  CYCLE_TRACE_DIR   Override cycle-trace directory containing code_mapper.py.
  CXX               C++ compiler, default: g++.

Options:
  --print-env       On success, print only shell exports to stdout. Logs go to stderr.

After install, enable the fast path for kernel-probe runs with:
  export CYCLE_TRACE_USE_PYDPG_BULK=1
  export CYCLE_TRACE_PYDPG_BULK_STRICT=1
USAGE
}

PRINT_ENV=0
PY_CMD=()

log() {
  printf '[pydpg-bulk] %s\n' "$*" >&2
}

die() {
  printf '[pydpg-bulk] ERROR: %s\n' "$*" >&2
  exit 1
}

emit_env() {
  if [[ "$PRINT_ENV" -eq 1 ]]; then
    printf 'export CYCLE_TRACE_USE_PYDPG_BULK=1\n'
    printf 'export CYCLE_TRACE_PYDPG_BULK_STRICT=1\n'
  else
    log "enable with: export CYCLE_TRACE_USE_PYDPG_BULK=1"
    log "optional strict validation: export CYCLE_TRACE_PYDPG_BULK_STRICT=1"
  fi
}

resolve_cycle_trace_dir() {
  if [[ -n "${CYCLE_TRACE_DIR:-}" ]]; then
    [[ -f "${CYCLE_TRACE_DIR}/code_mapper.py" ]] || die "CYCLE_TRACE_DIR does not contain code_mapper.py: ${CYCLE_TRACE_DIR}"
    printf '%s\n' "${CYCLE_TRACE_DIR}"
    return
  fi

  local candidate
  shopt -s nullglob
  local candidates=(/opt/maca/share/cycle-trace /opt/maca-*/share/cycle-trace)
  shopt -u nullglob
  for candidate in "${candidates[@]}"; do
    if [[ -f "${candidate}/code_mapper.py" && -f "${candidate}/pydpg.so" ]]; then
      printf '%s\n' "${candidate}"
      return
    fi
  done

  die "cannot find cycle-trace directory; pass --cycle-trace-dir DIR or set CYCLE_TRACE_DIR"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --cycle-trace-dir)
      [[ $# -ge 2 ]] || die "--cycle-trace-dir requires a value"
      CYCLE_TRACE_DIR="$2"
      shift 2
      ;;
    --print-env)
      PRINT_ENV=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      die "unknown argument: $1"
      ;;
  esac
done

CYCLE_TRACE_DIR="$(resolve_cycle_trace_dir)"
CODE_MAPPER="${CYCLE_TRACE_DIR}/code_mapper.py"
PYROOT="${CYCLE_TRACE_DIR}/python"
PYCFG="${PYROOT}/bin/python3.12-config"
PYEXE="${PYROOT}/bin/python3.12"
PYBIND_INC="${PYROOT}/lib/python3.12/site-packages/pybind11/include"
CXX="${CXX:-g++}"
TS="$(date -u +%Y%m%dT%H%M%SZ)"
export TS

[[ -f "${CODE_MAPPER}" ]] || die "missing code_mapper.py: ${CODE_MAPPER}"
[[ -f "${CYCLE_TRACE_DIR}/pydpg.so" ]] || die "missing pydpg.so: ${CYCLE_TRACE_DIR}/pydpg.so"

set_python_cmd() {
  PY_CMD=()
  if [[ -f "${PYEXE}" ]]; then
    if [[ -x "${PYEXE}" ]]; then
      PY_CMD=("${PYEXE}")
    elif [[ -x /lib64/ld-linux-x86-64.so.2 ]]; then
      PY_CMD=(/lib64/ld-linux-x86-64.so.2 "${PYEXE}")
    fi
  fi
}

validate_imports() {
  set_python_cmd
  if [[ ${#PY_CMD[@]} -eq 0 ]]; then
    if [[ -f "${PYEXE}" ]]; then
      log "skip import validation: cannot execute bundled Python ${PYEXE}"
    else
      log "skip import validation: bundled Python not found at ${PYEXE}"
    fi
    return 0
  fi

  log "validating Python imports"
  PYTHONPATH="${CYCLE_TRACE_DIR}:${PYTHONPATH:-}" \
  LD_LIBRARY_PATH="${CYCLE_TRACE_DIR}:${LD_LIBRARY_PATH:-}" \
    "${PY_CMD[@]}" - <<'PY'
import sys
import pydpg
import pydpg_bulk
print("[pydpg-bulk] import validation ok:", pydpg_bulk.__file__, file=sys.stderr)
PY
}

check_installed() {
  [[ -s "${CYCLE_TRACE_DIR}/pydpg_bulk.so" ]] || return 1
  [[ -f "${CYCLE_TRACE_DIR}/pydpg_bulk.cpp" ]] || return 1
  grep -q 'CYCLE_TRACE_USE_PYDPG_BULK' "${CODE_MAPPER}" || return 1
  grep -q 'pydpg_bulk.apply_mapped_tokens' "${CODE_MAPPER}" || return 1
  validate_imports || die "pydpg_bulk import validation failed; not enabling fast path"
  return 0
}

log "cycle-trace dir: ${CYCLE_TRACE_DIR}"

if check_installed; then
  log "pydpg bulk already installed"
  emit_env
  exit 0
fi

command -v python3 >/dev/null 2>&1 || die "python3 not found"
command -v "${CXX:-g++}" >/dev/null 2>&1 || die "C++ compiler not found: ${CXX:-g++}"

[[ -d "${PYBIND_INC}/pybind11" ]] || die "missing bundled pybind11 headers: ${PYBIND_INC}"
[[ -w "${CYCLE_TRACE_DIR}" ]] || die "cycle-trace directory is not writable; rerun with sufficient permissions: ${CYCLE_TRACE_DIR}"
[[ -w "${CODE_MAPPER}" ]] || die "code_mapper.py is not writable; rerun with sufficient permissions: ${CODE_MAPPER}"

log "pydpg bulk is not fully installed; installing"

if [[ -f "${PYCFG}" ]]; then
  PY_INCLUDES="$(/bin/bash "${PYCFG}" --includes)"
elif [[ -d "${PYROOT}/include/python3.12" ]]; then
  PY_INCLUDES="-I${PYROOT}/include/python3.12"
else
  die "cannot find Python 3.12 headers under ${PYROOT}"
fi

WORKDIR="$(mktemp -d /tmp/pydpg-bulk-install.XXXXXX)"
cleanup() {
  rm -rf "${WORKDIR}"
}
trap cleanup EXIT

CPP_TMP="${WORKDIR}/pydpg_bulk.cpp"
SO_TMP="${WORKDIR}/pydpg_bulk.so"

cat > "${CPP_TMP}" <<'CPP'
#include <cstddef>
#include <string>
#include <tuple>
#include <vector>

#include <pybind11/pybind11.h>
#include <pybind11/stl.h>

namespace py = pybind11;

namespace MXC500 {

class isa_t {
public:
    void add_extend(std::string key, std::string value);
};

class WaveNode {
public:
    void add_info(std::string key, std::string value);
};

}  // namespace MXC500

using MappedToken = std::tuple<std::size_t, std::string, std::string>;

static std::size_t apply_mapped_tokens(
    py::object wave_obj,
    const std::vector<MappedToken>& mapped_tokens,
    const std::string& asm_marked)
{
    py::module_::import("pydpg");

    auto* wave = wave_obj.cast<MXC500::WaveNode*>();
    wave->add_info("asm_marked", asm_marked);

    py::object isas_obj = wave_obj.attr("isas");
    std::size_t applied = 0;
    for (const auto& item : mapped_tokens) {
        const auto& [isa_index, line, code] = item;
        py::object isa_obj = isas_obj[py::int_(isa_index)];
        auto* isa = isa_obj.cast<MXC500::isa_t*>();
        isa->add_extend("line", line);
        isa->add_extend("code", code);
        isa->add_extend("asm_marked", asm_marked);
        ++applied;
    }
    return applied;
}

PYBIND11_MODULE(pydpg_bulk, m) {
    m.doc() = "Bulk write helpers for pydpg source-line mapping.";
    m.def("apply_mapped_tokens", &apply_mapped_tokens,
          py::arg("wave"), py::arg("mapped_tokens"), py::arg("asm_marked"));
}
CPP

log "building pydpg_bulk.so with ${CXX}"
# PY_INCLUDES intentionally contains compiler flags returned by python3.12-config.
# shellcheck disable=SC2086
"${CXX}" -O3 -shared -std=c++17 -fPIC ${PY_INCLUDES} -I"${PYBIND_INC}" \
  "${CPP_TMP}" \
  -L"${CYCLE_TRACE_DIR}" -Wl,--no-as-needed -l:pydpg.so \
  -Wl,-rpath,"${CYCLE_TRACE_DIR}" \
  -o "${SO_TMP}"

install -m 0644 "${CPP_TMP}" "${CYCLE_TRACE_DIR}/pydpg_bulk.cpp"
install -m 0755 "${SO_TMP}" "${CYCLE_TRACE_DIR}/pydpg_bulk.so"
log "installed ${CYCLE_TRACE_DIR}/pydpg_bulk.so"

export CODE_MAPPER
python3 <<'PY'
import os
import sys
import shutil
from pathlib import Path

path = Path(os.environ["CODE_MAPPER"])
text = path.read_text()
if "CYCLE_TRACE_USE_PYDPG_BULK" in text:
    print(f"[pydpg-bulk] code_mapper.py already patched: {path}", file=sys.stderr)
    raise SystemExit(0)

if "import os\n" not in text:
    raise SystemExit("[pydpg-bulk] ERROR: code_mapper.py does not import os; refusing automatic patch")

needle = '''    def apply(self):
        self.wave.add_info("asm_marked", str(self.asm_code.hostimage_va))
'''

replacement = '''    def apply(self):
        if os.environ.get("CYCLE_TRACE_USE_PYDPG_BULK") == "1":
            mapped_tokens = []
            for i, token in self.filtered_tokens.items():
                if token["type"] not in self.ISATokens:
                    continue
                if not token.get("code"):
                    continue
                mapped_tokens.append((i, str(token["code"]["line"]), token["code"]["code"]))
            try:
                import pydpg_bulk
                pydpg_bulk.apply_mapped_tokens(self.wave, mapped_tokens, str(self.asm_code.hostimage_va))
                return
            except Exception as e:
                if os.environ.get("CYCLE_TRACE_PYDPG_BULK_STRICT") == "1":
                    raise
                print("WARNING:pydpg_bulk failed, fallback to Python apply: %s" % e)

        self.wave.add_info("asm_marked", str(self.asm_code.hostimage_va))
'''

if needle not in text:
    raise SystemExit("[pydpg-bulk] ERROR: cannot find expected DPGMap.apply anchor; inspect code_mapper.py manually")

backup = path.with_name(path.name + ".bak_" + os.environ.get("TS", "manual") + "_before_pydpg_bulk")
shutil.copy2(path, backup)
path.write_text(text.replace(needle, replacement, 1))
print(f"[pydpg-bulk] patched {path}", file=sys.stderr)
print(f"[pydpg-bulk] backup  {backup}", file=sys.stderr)
PY

validate_imports || die "pydpg_bulk import validation failed; not enabling fast path"

log "done"
emit_env
