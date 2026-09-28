"""Source-line cycle attribution from CycleTrace events and line-info asm."""

from __future__ import annotations

import csv
import re
import shutil
import subprocess
from collections import Counter
from pathlib import Path
from typing import Any

from .artifacts import load_json, write_json
from .constants import cycle_trace_filename
from .utils import pct


LABEL_RE = re.compile(r"\s*^[\da-f]+\s<([^>]+)>:$")
CODE_WITH_BYTES_RE = re.compile(r"^\s*([\da-f]+):\s+(([\da-f]{2}\s){8})(.+)$")
CODE_RE = re.compile(r"^\s*([\da-f]+):\s+(.+)$")
LINEINFO_COMMENT_RE = re.compile(r"^;\s+(.+):(\d+)$")
DEFAULT_OBJDUMP_BIN = "/opt/maca/restricted/Tools/mxgpu_llvm/bin/mxobjdump"
DEFAULT_LLVM_OBJDUMP_BIN = str(Path(DEFAULT_OBJDUMP_BIN).with_name("llvm-objdump"))
WRAPPED_LATENCY_MIN = 1 << 63
FIXED_INSTRUCTION_CYCLES = {
    "MTE": 4,
    "STE": 4,
    "ARRIVE": 4,
    "LDU": 64,
    "MMA": 16,
}
FIXED_EVENT_NAME_CYCLES = {
    ("MTE", "Transcendental Function"): (16, "MTE_transcendental_fixed_cycles"),
}
FIXED_MNEMONIC_CYCLES = {
    "snop": 4,
}


def _fmt_pct(value: float) -> str:
    return f"{value:.2f}%"


def _resolve_existing(path: Path, *, cwd: Path | None = None) -> Path:
    candidate = path if path.is_absolute() else (cwd or Path.cwd()) / path
    if not candidate.exists():
        raise FileNotFoundError(candidate)
    return candidate


def _default_cycle_json(run_dir: Path, cycle_dpc_id: str | int | None) -> Path:
    name = cycle_trace_filename(cycle_dpc_id)
    candidates = [
        run_dir / "artifacts" / name,
        run_dir / name,
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    raise FileNotFoundError(f"cannot find CycleTrace JSON in {run_dir}: {name}")


def _default_link_obj(run_dir: Path) -> Path:
    candidates = [
        run_dir / "_kernelprobe_replication_0.link.o",
        run_dir / "artifacts" / "_kernelprobe_replication_0.link.o",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    raise FileNotFoundError(f"cannot find _kernelprobe_replication_0.link.o in {run_dir}")


def _find_default_link_obj(run_dir: Path) -> Path | None:
    try:
        return _default_link_obj(run_dir)
    except FileNotFoundError:
        return None


def _derive_llvm_objdump_bin(objdump_bin: str) -> str:
    path = Path(objdump_bin)
    if path.name == "mxobjdump":
        return str(path.with_name("llvm-objdump"))
    return DEFAULT_LLVM_OBJDUMP_BIN


def _ensure_lineinfo_objdump(
    run_dir: Path,
    binary: Path | None,
    device_elf: Path | None,
    kernelprobe_link_obj: Path | None,
    lineinfo_objdump: Path | None,
    objdump_bin: str,
    llvm_objdump_bin: str,
) -> tuple[Path | None, Path | None, str]:
    if lineinfo_objdump is not None:
        return _resolve_existing(lineinfo_objdump), None, "lineinfo_objdump"

    out_dir = run_dir / "artifacts" / "lineinfo"
    out_dir.mkdir(parents=True, exist_ok=True)

    elf_path: Path
    if device_elf is not None:
        elf_path = _resolve_existing(device_elf)
        out_path = out_dir / "device_elf_lineinfo_objdump.txt"
        input_kind = "device_elf"
    elif kernelprobe_link_obj is not None:
        elf_path = _resolve_existing(kernelprobe_link_obj)
        out_path = out_dir / "kernelprobe_link_lineinfo_objdump.txt"
        input_kind = "kernelprobe_link_obj"
    else:
        default_link_obj = _find_default_link_obj(run_dir)
        if binary is None and default_link_obj is not None:
            elf_path = default_link_obj
            out_path = out_dir / "kernelprobe_link_lineinfo_objdump.txt"
            input_kind = "kernelprobe_link_obj"
        elif binary is None:
            return None, None, "missing_lineinfo_input"
        else:
            out_path = out_dir / "binary_lineinfo_objdump.txt"
            input_kind = "binary"
            src_binary = _resolve_existing(binary)
            local_binary = out_dir / "lineinfo_binary"
            for stale in out_dir.glob("lineinfo_binary.*.out"):
                stale.unlink()
            shutil.copy2(src_binary, local_binary)
            proc = subprocess.run(
                [objdump_bin, "--extract-elf", str(local_binary)],
                check=False,
                text=True,
                cwd=out_dir,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )
            (out_dir / "extract_elf.log").write_text(proc.stdout, encoding="utf-8")
            if proc.returncode != 0:
                raise RuntimeError(f"mxobjdump --extract-elf failed with code {proc.returncode}; output written to {out_dir / 'extract_elf.log'}")
            candidates = sorted(out_dir.glob("lineinfo_binary.*.out"))
            if not candidates:
                raise FileNotFoundError(f"mxobjdump --extract-elf did not produce lineinfo_binary.*.out in {out_dir}")
            elf_path = candidates[0]

    proc = subprocess.run(
        [llvm_objdump_bin, "--line-numbers", "--disassemble", str(elf_path)],
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    out_path.write_text(proc.stdout, encoding="utf-8")
    if proc.returncode != 0:
        raise RuntimeError(f"llvm-objdump --line-numbers failed with code {proc.returncode}; output written to {out_path}")
    return out_path, elf_path, input_kind


def _parse_lineinfo_objdump(path: Path, entry_label: str, source_path: Path) -> list[dict[str, Any]]:
    instructions: list[dict[str, Any]] = []
    current_label: str | None = None
    current_loc: dict[str, Any] | None = None
    current_user_loc: dict[str, Any] | None = None
    filtered_line = 0

    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        label_match = LABEL_RE.match(raw)
        if label_match:
            filtered_line += 1
            current_label = label_match.group(1)
            current_loc = None
            current_user_loc = None
            continue

        loc_match = LINEINFO_COMMENT_RE.match(raw.strip())
        if loc_match:
            loc_path = loc_match.group(1)
            loc = {
                "path": loc_path,
                "line": int(loc_match.group(2)),
                "column": 0,
                "loc_text": raw.strip(),
            }
            current_loc = loc
            if _is_user_source(loc_path, source_path):
                current_user_loc = dict(loc)
            continue

        code_match = CODE_RE.match(raw)
        if not code_match:
            continue
        filtered_line += 1
        if current_label != entry_label:
            continue
        bytes_match = CODE_WITH_BYTES_RE.match(raw.strip())
        code = bytes_match.group(4).strip() if bytes_match else code_match.group(2).strip()
        try:
            address = int(code_match.group(1), 16)
        except ValueError:
            continue
        instructions.append(
            {
                "address": address,
                "filtered_line": filtered_line,
                "code": code,
                "mnemonic": code.split()[0].lower(),
                "loc": current_loc,
                "user_loc": current_user_loc,
            }
        )

    if not instructions:
        raise ValueError(f"entry label {entry_label!r} not found in lineinfo objdump: {path}")
    return instructions


def _is_user_source(path: str, source_path: Path) -> bool:
    normalized = path.replace("\\", "/")
    source_name = source_path.name
    source_posix = str(source_path).replace("\\", "/")
    return normalized.endswith(source_posix) or normalized.endswith("/" + source_name) or normalized == source_name


def _source_lines(source_path: Path) -> list[str]:
    return source_path.read_text(encoding="utf-8", errors="replace").splitlines()


def _next_code_line(source_lines: list[str], line_no: int) -> int | None:
    for idx in range(line_no, len(source_lines)):
        stripped = source_lines[idx].strip()
        if stripped and not stripped.startswith("//"):
            return idx + 1
    return None


def _adjust_user_loc(
    user_loc: dict[str, Any] | None,
    current_loc: dict[str, Any] | None,
    source_lines: list[str],
) -> tuple[dict[str, Any] | None, str]:
    if user_loc is None:
        return None, "no_user_source_loc"
    if current_loc and current_loc.get("path") == user_loc.get("path"):
        return user_loc, "lineinfo"

    line_no = int(user_loc.get("line", 0))
    text = source_lines[line_no - 1].strip() if 1 <= line_no <= len(source_lines) else ""
    control_prefixes = ("for", "if", "while", "switch")
    is_control_header = any(text.startswith(prefix + " ") or text.startswith(prefix + "(") for prefix in control_prefixes)
    if is_control_header and not text.endswith(";"):
        next_line = _next_code_line(source_lines, line_no)
        if next_line is not None:
            adjusted = dict(user_loc)
            adjusted["line"] = next_line
            adjusted["column"] = 0
            adjusted["adjusted_from_line"] = line_no
            return adjusted, "header_intrinsic_next_source_line"
    return user_loc, "nearest_user_loc"


def _extract_latency_events(
    trace_events: list[dict[str, Any]],
) -> tuple[list[tuple[dict[str, Any], dict[str, Any], int]], int, int]:
    latency_events: list[tuple[dict[str, Any], dict[str, Any], int]] = []
    raw_latency_events = 0
    wrapped_latency_events = 0
    for event in trace_events:
        args = event.get("args") or {}
        if "latency(cycles)" not in args:
            continue
        raw_latency_events += 1
        try:
            cycles = int(args["latency(cycles)"])
        except Exception:
            continue
        if cycles >= WRAPPED_LATENCY_MIN:
            wrapped_latency_events += 1
            continue
        latency_events.append((event, args, cycles))
    return latency_events, raw_latency_events, wrapped_latency_events


def _valid_latency_cycles(args: dict[str, Any]) -> tuple[int | None, bool]:
    if "latency(cycles)" not in args:
        return None, False
    try:
        cycles = int(args["latency(cycles)"])
    except Exception:
        return None, False
    if cycles >= WRAPPED_LATENCY_MIN:
        return None, True
    return cycles, False


def _modeled_instruction_cycles(event: dict[str, Any], args: dict[str, Any]) -> tuple[int | None, str]:
    latency_cycles, wrapped = _valid_latency_cycles(args)
    if latency_cycles is not None:
        return latency_cycles, "latency(cycles)"
    if wrapped:
        return None, "wrapped_latency_excluded"
    cat = str(event.get("cat", ""))
    event_name = str(event.get("name", ""))
    fixed_event_cycles = FIXED_EVENT_NAME_CYCLES.get((cat, event_name))
    if fixed_event_cycles is not None:
        return fixed_event_cycles
    mnemonic = _instruction_mnemonic(str(args.get("code", "")))
    fixed_mnemonic_cycles = FIXED_MNEMONIC_CYCLES.get(mnemonic)
    if fixed_mnemonic_cycles is not None:
        return fixed_mnemonic_cycles, f"{mnemonic.upper()}_fixed_cycles"
    fixed_cycles = FIXED_INSTRUCTION_CYCLES.get(cat)
    if fixed_cycles is not None:
        return fixed_cycles, f"{cat}_fixed_cycles"
    return None, "unmodeled_no_fixed_cycle"


def _instruction_mnemonic(instruction_text: str) -> str:
    return instruction_text.split(maxsplit=1)[0].lower() if instruction_text else ""


def _modeled_cycle_costs_summary() -> dict[str, int]:
    costs = dict(FIXED_INSTRUCTION_CYCLES)
    costs.update({f"{mnemonic} mnemonic": cycles for mnemonic, cycles in FIXED_MNEMONIC_CYCLES.items()})
    costs.update({f"{cat}:{name}": cycles for (cat, name), (cycles, _) in FIXED_EVENT_NAME_CYCLES.items()})
    return costs


def write_source_latency_report(
    *,
    run_dir: Path,
    tag: str,
    cycle_json: Path | None = None,
    binary: Path | None = None,
    device_elf: Path | None = None,
    lineinfo_objdump: Path | None = None,
    source: Path,
    kernelprobe_link_obj: Path | None = None,
    objdump_bin: str = DEFAULT_OBJDUMP_BIN,
    llvm_objdump_bin: str | None = None,
    entry_label: str = "MAIN_0",
    cycle_dpc_id: str | int | None = "0",
    output_prefix: str = "source_latency",
) -> dict[str, Path]:
    """Create source-line modeled cycle attribution reports."""

    run_dir = run_dir.resolve()
    analysis_dir = run_dir / "analysis"
    analysis_dir.mkdir(parents=True, exist_ok=True)
    cycle_path = _resolve_existing(cycle_json) if cycle_json else _default_cycle_json(run_dir, cycle_dpc_id)
    source_path = _resolve_existing(source)
    llvm_objdump_bin = llvm_objdump_bin or _derive_llvm_objdump_bin(objdump_bin)

    trace = load_json(cycle_path)
    trace_events = trace.get("traceEvents", []) if isinstance(trace, dict) else []
    _, _, wrapped_latency_events = _extract_latency_events(trace_events)
    trace_events_with_line_code = [
        event
        for event in trace_events
        if "line" in (event.get("args") or {}) and "code" in (event.get("args") or {})
    ]
    source_text = _source_lines(source_path)

    lineinfo_objdump_path, lineinfo_device_elf_path, lineinfo_input_kind = _ensure_lineinfo_objdump(
        run_dir,
        binary,
        device_elf,
        kernelprobe_link_obj,
        lineinfo_objdump,
        objdump_bin,
        llvm_objdump_bin,
    )
    lineinfo_instructions: list[dict[str, Any]] = []
    if lineinfo_objdump_path is not None:
        lineinfo_instructions = _parse_lineinfo_objdump(lineinfo_objdump_path, entry_label, source_path)
        filtered_line_to_lineinfo_instruction = {int(ins["filtered_line"]): ins for ins in lineinfo_instructions}
    else:
        raise FileNotFoundError(
            "no lineinfo input was provided or found; provide --binary, --device-elf, "
            "--kernelprobe-link-obj, --lineinfo-objdump, or run source-latency in a "
            "kernel-probe run directory containing _kernelprobe_replication_0.link.o"
        )

    source_rows: dict[tuple[str, int], dict[str, Any]] = {}
    modeled_instruction_rows: dict[str, dict[str, Any]] = {}
    modeled_unmapped_reasons: Counter[str] = Counter()
    modeled_adjustment_reasons: Counter[str] = Counter()
    modeled_mapping_modes: Counter[str] = Counter()
    modeled_cycle_kinds: Counter[str] = Counter()
    unmodeled_no_fixed_cycle_events: Counter[str] = Counter()
    modeled_unmapped_examples: list[dict[str, Any]] = []

    def map_event_to_lineinfo(
        event: dict[str, Any],
        args: dict[str, Any],
        cycles: int,
        reasons: Counter[str],
        examples: list[dict[str, Any]],
    ) -> tuple[dict[str, Any], str, str] | None:
        instruction_text = args.get("code", "")

        if "line" in args and filtered_line_to_lineinfo_instruction:
            try:
                json_line = int(args["line"])
            except Exception:
                reasons["bad_json_line"] += 1
                return None
            source_instruction = filtered_line_to_lineinfo_instruction.get(json_line)
            if source_instruction is None:
                reasons["json_line_not_in_lineinfo_objdump"] += 1
                if len(examples) < 20:
                    examples.append({"reason": "json_line_not_in_lineinfo_objdump", "line": json_line, "cycles": cycles})
                return None
            instruction_text = source_instruction["code"]
            mapping_mode = f"{lineinfo_input_kind}_lineinfo_json_line"
        else:
            reasons["missing_json_line_or_lineinfo_inputs"] += 1
            return None
        return source_instruction, instruction_text, mapping_mode

    def get_source_row(user_loc: dict[str, Any]) -> dict[str, Any]:
        source_key = (str(user_loc["path"]), int(user_loc["line"]))
        return source_rows.setdefault(
            source_key,
            {
                "source_path": str(user_loc["path"]),
                "source_line": int(user_loc["line"]),
                "modeled_cycles": 0,
                "modeled_events": 0,
                "modeled_max_cycles": 0,
                "modeled_cats": Counter(),
                "modeled_cat_cycles": Counter(),
                "modeled_cycle_kinds": Counter(),
                "modeled_instructions": Counter(),
                "modeled_instruction_events": Counter(),
                "modeled_instruction_max_cycles": {},
                "modeled_asm_lines": Counter(),
                "modeled_adjustments": Counter(),
                "modeled_mapping_modes": Counter(),
            },
        )

    raw_modeled_events = 0
    raw_modeled_cycles = 0
    for event in trace_events:
        args = event.get("args") or {}
        modeled_cycles, cycle_kind = _modeled_instruction_cycles(event, args)
        if modeled_cycles is None:
            if cycle_kind == "unmodeled_no_fixed_cycle" and ("line" in args or "code" in args):
                unmodeled_no_fixed_cycle_events[str(event.get("cat", ""))] += 1
            continue
        raw_modeled_events += 1
        raw_modeled_cycles += modeled_cycles
        modeled_cycle_kinds[cycle_kind] += 1

        mapped = map_event_to_lineinfo(event, args, modeled_cycles, modeled_unmapped_reasons, modeled_unmapped_examples)
        if mapped is None:
            continue
        source_instruction, instruction_text, mapping_mode = mapped

        user_loc, adjustment = _adjust_user_loc(source_instruction.get("user_loc"), source_instruction.get("loc"), source_text)
        modeled_adjustment_reasons[adjustment] += 1
        if user_loc is None:
            modeled_unmapped_reasons["no_user_source_loc"] += 1
            continue
        modeled_mapping_modes[mapping_mode] += 1

        source_row = get_source_row(user_loc)
        cat = str(event.get("cat", ""))
        source_row["modeled_cycles"] += modeled_cycles
        source_row["modeled_events"] += 1
        source_row["modeled_max_cycles"] = max(source_row["modeled_max_cycles"], modeled_cycles)
        source_row["modeled_cats"][cat] += 1
        source_row["modeled_cat_cycles"][cat] += modeled_cycles
        source_row["modeled_cycle_kinds"][cycle_kind] += modeled_cycles
        source_row["modeled_instructions"][instruction_text] += modeled_cycles
        source_row["modeled_instruction_events"][instruction_text] += 1
        source_row["modeled_instruction_max_cycles"][instruction_text] = max(
            source_row["modeled_instruction_max_cycles"].get(instruction_text, 0),
            modeled_cycles,
        )
        if "asm_line" in source_instruction:
            source_row["modeled_asm_lines"][str(source_instruction["asm_line"])] += modeled_cycles
        if "address" in source_instruction:
            source_row["modeled_asm_lines"][hex(int(source_instruction["address"]))] += modeled_cycles
        source_row["modeled_adjustments"][adjustment] += 1
        source_row["modeled_mapping_modes"][mapping_mode] += 1

        instruction_key = instruction_text
        modeled_instruction_row = modeled_instruction_rows.setdefault(
            instruction_key,
            {
                "instruction": instruction_key,
                "cycles": 0,
                "events": 0,
                "max_cycles": 0,
                "source_lines": Counter(),
                "cats": Counter(),
                "cycle_kinds": Counter(),
            },
        )
        modeled_instruction_row["cycles"] += modeled_cycles
        modeled_instruction_row["events"] += 1
        modeled_instruction_row["max_cycles"] = max(modeled_instruction_row["max_cycles"], modeled_cycles)
        modeled_instruction_row["source_lines"][f"{user_loc['path']}:{user_loc['line']}"] += modeled_cycles
        modeled_instruction_row["cats"][cat] += 1
        modeled_instruction_row["cycle_kinds"][cycle_kind] += 1

    modeled_mapped_cycles = sum(row["modeled_cycles"] for row in source_rows.values())
    modeled_mapped_events = sum(row["modeled_events"] for row in source_rows.values())
    sorted_modeled_sources = sorted(source_rows.values(), key=lambda row: row["modeled_cycles"], reverse=True)
    sorted_modeled_instructions = sorted(modeled_instruction_rows.values(), key=lambda row: row["cycles"], reverse=True)

    def source_line_text(line_no: int) -> str:
        if 1 <= line_no <= len(source_text):
            return source_text[line_no - 1].strip()
        return ""

    def top_modeled_instruction_metrics(row: dict[str, Any]) -> tuple[str, int, int, int]:
        if not row["modeled_instructions"]:
            return "", 0, 0, 0
        top_instruction, top_instruction_total_cycles = row["modeled_instructions"].most_common(1)[0]
        top_instruction_events = row["modeled_instruction_events"].get(top_instruction, 0)
        top_instruction_max_cycles = row["modeled_instruction_max_cycles"].get(top_instruction, 0)
        return (
            top_instruction,
            top_instruction_events,
            top_instruction_max_cycles,
            top_instruction_total_cycles,
        )

    def asm_instruction_breakdown(row: dict[str, Any], limit: int | None = None, include_ellipsis: bool = False) -> str:
        parts: list[str] = []
        instructions = row["modeled_instructions"].most_common()
        shown_instructions = instructions[:limit] if limit is not None else instructions
        for instruction, cycles in shown_instructions:
            events = row["modeled_instruction_events"].get(instruction, 0)
            max_cycles = row["modeled_instruction_max_cycles"].get(instruction, 0)
            parts.append(f"{instruction} ({cycles} cyc, {events} ev, max {max_cycles})")
        if include_ellipsis and limit is not None and len(instructions) > limit:
            parts.append(f"... ({len(instructions) - limit} more in CSV/JSON)")
        return "; ".join(parts)

    def source_summary_row(rank: int, row: dict[str, Any]) -> dict[str, Any]:
        (
            top_modeled_instruction,
            top_modeled_instruction_events,
            top_modeled_instruction_max_cycles,
            top_modeled_instruction_total_cycles,
        ) = top_modeled_instruction_metrics(row)
        return {
            "rank": rank,
            "source_path": row["source_path"],
            "source_line": row["source_line"],
            "source_text": source_line_text(row["source_line"]),
            "total_cycles": row["modeled_cycles"],
            "pct_total_cycles": pct(row["modeled_cycles"], raw_modeled_cycles),
            "pct_mapped_cycles": pct(row["modeled_cycles"], modeled_mapped_cycles),
            "events": row["modeled_events"],
            "max_cycles": row["modeled_max_cycles"],
            "latency_cycles": row["modeled_cycle_kinds"].get("latency(cycles)", 0),
            "mte_cycles": row["modeled_cycle_kinds"].get("MTE_fixed_cycles", 0),
            "mte_transcendental_cycles": row["modeled_cycle_kinds"].get("MTE_transcendental_fixed_cycles", 0),
            "ste_cycles": row["modeled_cycle_kinds"].get("STE_fixed_cycles", 0),
            "arrive_cycles": row["modeled_cycle_kinds"].get("ARRIVE_fixed_cycles", 0),
            "ldu_cycles": row["modeled_cycle_kinds"].get("LDU_fixed_cycles", 0),
            "snop_cycles": row["modeled_cycle_kinds"].get("SNOP_fixed_cycles", 0),
            "mma_cycles": row["modeled_cycle_kinds"].get("MMA_fixed_cycles", 0),
            "top_modeled_instruction": top_modeled_instruction,
            "top_modeled_instruction_events": top_modeled_instruction_events,
            "top_modeled_instruction_max_cycles": top_modeled_instruction_max_cycles,
            "top_modeled_instruction_total_cycles": top_modeled_instruction_total_cycles,
            "asm_instruction_breakdown": asm_instruction_breakdown(row),
        }

    md_path = analysis_dir / f"{output_prefix}_{tag}.md"
    csv_path = analysis_dir / f"{output_prefix}_{tag}.csv"
    json_path = analysis_dir / f"{output_prefix}_{tag}.json"

    with csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(
            [
                "rank",
                "source_path",
                "source_line",
                "source_text",
                "total_cycles",
                "pct_total_cycles",
                "pct_mapped_cycles",
                "events",
                "max_cycles",
                "latency_cycles",
                "mte_cycles",
                "mte_transcendental_cycles",
                "ste_cycles",
                "arrive_cycles",
                "ldu_cycles",
                "snop_cycles",
                "mma_cycles",
                "top_modeled_instruction",
                "top_modeled_instruction_events",
                "top_modeled_instruction_max_cycles",
                "top_modeled_instruction_total_cycles",
                "asm_instruction_breakdown",
                "cycle_kinds",
                "cats",
                "asm_addresses",
                "mapping_modes",
            ]
        )
        for rank, row in enumerate(sorted_modeled_sources, 1):
            (
                top_modeled_instruction,
                top_modeled_instruction_events,
                top_modeled_instruction_max_cycles,
                top_modeled_instruction_total_cycles,
            ) = top_modeled_instruction_metrics(row)
            writer.writerow(
                [
                    rank,
                    row["source_path"],
                    row["source_line"],
                    source_line_text(row["source_line"]),
                    row["modeled_cycles"],
                    f"{pct(row['modeled_cycles'], raw_modeled_cycles):.4f}",
                    f"{pct(row['modeled_cycles'], modeled_mapped_cycles):.4f}",
                    row["modeled_events"],
                    row["modeled_max_cycles"],
                    row["modeled_cycle_kinds"].get("latency(cycles)", 0),
                    row["modeled_cycle_kinds"].get("MTE_fixed_cycles", 0),
                    row["modeled_cycle_kinds"].get("MTE_transcendental_fixed_cycles", 0),
                    row["modeled_cycle_kinds"].get("STE_fixed_cycles", 0),
                    row["modeled_cycle_kinds"].get("ARRIVE_fixed_cycles", 0),
                    row["modeled_cycle_kinds"].get("LDU_fixed_cycles", 0),
                    row["modeled_cycle_kinds"].get("SNOP_fixed_cycles", 0),
                    row["modeled_cycle_kinds"].get("MMA_fixed_cycles", 0),
                    top_modeled_instruction,
                    top_modeled_instruction_events,
                    top_modeled_instruction_max_cycles,
                    top_modeled_instruction_total_cycles,
                    asm_instruction_breakdown(row),
                    "; ".join(f"{key}:{value}" for key, value in row["modeled_cycle_kinds"].most_common()),
                    "; ".join(f"{key}:{value}" for key, value in row["modeled_cats"].most_common()),
                    "; ".join(f"{key}:{value}" for key, value in row["modeled_asm_lines"].most_common(8)),
                    "; ".join(f"{key}:{value}" for key, value in row["modeled_mapping_modes"].most_common()),
                ]
            )

    summary = {
        "inputs": {
            "cycle_trace_json": str(cycle_path),
            "source": str(source_path),
            "entry_label": entry_label,
            "objdump_bin": objdump_bin,
            "llvm_objdump_bin": llvm_objdump_bin,
            "binary": str(_resolve_existing(binary)) if binary else None,
            "device_elf": str(_resolve_existing(device_elf)) if device_elf else None,
            "kernelprobe_link_obj": str(_resolve_existing(kernelprobe_link_obj)) if kernelprobe_link_obj else None,
            "lineinfo_device_elf": str(lineinfo_device_elf_path) if lineinfo_device_elf_path else None,
            "lineinfo_objdump": str(lineinfo_objdump_path) if lineinfo_objdump_path else None,
            "lineinfo_input_kind": lineinfo_input_kind,
        },
        "coverage": {
            "wrapped_latency_records_excluded": wrapped_latency_events,
            "wrapped_latency_threshold": WRAPPED_LATENCY_MIN,
            "trace_events_total": len(trace_events),
            "trace_events_with_line_code": len(trace_events_with_line_code),
            "lineinfo_entry_instructions": len(lineinfo_instructions),
            "modeled_cycle_costs": _modeled_cycle_costs_summary(),
            "raw_modeled_events": raw_modeled_events,
            "raw_modeled_cycles": raw_modeled_cycles,
            "modeled_source_mapped_events": modeled_mapped_events,
            "modeled_source_mapped_cycles": modeled_mapped_cycles,
            "modeled_source_mapped_event_share_pct": pct(modeled_mapped_events, raw_modeled_events),
            "modeled_source_mapped_cycle_share_pct": pct(modeled_mapped_cycles, raw_modeled_cycles),
            "modeled_cycle_kinds": dict(modeled_cycle_kinds),
            "modeled_mapping_modes": dict(modeled_mapping_modes),
            "modeled_unmapped_reasons": dict(modeled_unmapped_reasons),
            "modeled_adjustment_reasons": dict(modeled_adjustment_reasons),
            "unmodeled_no_fixed_cycle_events": dict(unmodeled_no_fixed_cycle_events),
        },
        "source_lines": [source_summary_row(rank, row) for rank, row in enumerate(sorted_modeled_sources[:100], 1)],
        "top_modeled_instructions": [
            {
                "rank": rank,
                "instruction": row["instruction"],
                "cycles": row["cycles"],
                "pct_total_modeled_cycles": pct(row["cycles"], raw_modeled_cycles),
                "pct_mapped_modeled_cycles": pct(row["cycles"], modeled_mapped_cycles),
                "events": row["events"],
                "max_cycles": row["max_cycles"],
                "cats": dict(row["cats"]),
                "cycle_kinds": dict(row["cycle_kinds"]),
                "top_source_line": row["source_lines"].most_common(1)[0][0],
                "top_source_line_cycles": row["source_lines"].most_common(1)[0][1],
            }
            for rank, row in enumerate(sorted_modeled_instructions[:100], 1)
        ],
        "modeled_unmapped_examples": modeled_unmapped_examples,
    }
    write_json(json_path, summary)

    lines = [
        "# Source-line cycle attribution",
        "",
        "## Inputs",
        "",
        f"- cycle-trace JSON: `{cycle_path}`",
        f"- lineinfo input kind: `{lineinfo_input_kind}`",
        f"- lineinfo device ELF: `{lineinfo_device_elf_path}`",
        f"- lineinfo objdump: `{lineinfo_objdump_path}`",
        f"- source file: `{source_path}`",
        f"- entry label: `{entry_label}`",
        "",
        "## Coverage",
        "",
        "| Metric | Value |",
        "|---|---:|",
        f"| wrapped latency records excluded | {wrapped_latency_events} |",
        f"| trace events | {len(trace_events)} |",
        f"| JSON events with `line/code` | {len(trace_events_with_line_code)} |",
        f"| lineinfo entry instructions | {len(lineinfo_instructions)} |",
        f"| modeled cycle costs | latency(cycles)=as reported, MTE Transcendental Function=16, other MTE=4, STE=4, ARRIVE=4, LDU=64, SNOP=4, MMA=16 |",
        f"| modeled events | {raw_modeled_events} |",
        f"| modeled cycles | {raw_modeled_cycles} |",
        f"| source-line mapped modeled events | {modeled_mapped_events} ({_fmt_pct(pct(modeled_mapped_events, raw_modeled_events))} of modeled events) |",
        f"| source-line mapped modeled cycles | {modeled_mapped_cycles} ({_fmt_pct(pct(modeled_mapped_cycles, raw_modeled_cycles))} of modeled cycles) |",
    ]
    if modeled_cycle_kinds:
        lines.extend(["", "Modeled cycle event sources:", "", "| Source | Events |", "|---|---:|"])
        for reason, count in modeled_cycle_kinds.most_common():
            lines.append(f"| `{reason}` | {count} |")
    if unmodeled_no_fixed_cycle_events:
        lines.extend(["", "Events without modeled cycle cost:", "", "| Cat | Events |", "|---|---:|"])
        for cat, count in unmodeled_no_fixed_cycle_events.most_common():
            lines.append(f"| `{cat}` | {count} |")
    if modeled_unmapped_reasons:
        lines.extend(["", "Unmapped modeled event reasons:", "", "| Reason | Events |", "|---|---:|"])
        for reason, count in modeled_unmapped_reasons.most_common():
            lines.append(f"| `{reason}` | {count} |")
        lines.extend(
            [
                "",
                "Reason definitions:",
                "",
                "- `json_line_not_in_lineinfo_objdump`: the modeled event has CycleTrace JSON `line/code`, but that filtered line is not present in the selected lineinfo objdump entry. This usually means the event belongs to wrapper/probe/runtime code, another entry range, or a code-mapper line that does not match the current objdump.",
                "- `missing_json_line_or_lineinfo_inputs`: the modeled event does not have usable JSON `line/code`, or the lineinfo objdump mapping table is unavailable.",
                "- `no_user_source_loc`: the event mapped to asm, but the lineinfo location did not resolve to the requested user source file.",
                "- These events are included in global modeled events/cycles, but they are not included in source-line mapped events/cycles or the source-line attribution table.",
            ]
        )

    lines.extend(
        [
            "",
            "## Unified Source-Line Cycle Attribution",
            "",
            "Total cycles are computed from CycleTrace and asm only. Instructions with valid `latency(cycles)` use the reported latency; otherwise MTE `Transcendental Function` uses 16 cycles, other MTE uses 4 cycles, STE, ARRIVE, and SNOP use 4 cycles, LDU uses 64 cycles, and MMA uses 16 cycles. If one source statement expands to multiple asm instructions, all mapped instruction cycles are summed into that source line.",
            "",
            "| Rank | Source line | Total cycles | % total | % mapped | Events | Max | Latency | MTE | MTE Trans | STE | ARRIVE | LDU | SNOP | MMA | Top instruction | Top instruction events | Top instruction max cycles | Top instruction total cycles | Mapped asm cycle breakdown | Source text |",
            "|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---:|---:|---:|---|---|",
        ]
    )
    for rank, row in enumerate(sorted_modeled_sources[:30], 1):
        (
            top_instruction,
            top_instruction_events,
            top_instruction_max_cycles,
            top_instruction_total_cycles,
        ) = top_modeled_instruction_metrics(row)
        text = source_line_text(row["source_line"]).replace("|", "\\|")
        top = f"`{top_instruction}`".replace("|", "\\|")
        instruction_breakdown = asm_instruction_breakdown(row, limit=3, include_ellipsis=True).replace("|", "\\|").replace("; ", "<br>")
        lines.append(
            f"| {rank} | `{row['source_path']}:{row['source_line']}` | {row['modeled_cycles']} | "
            f"{_fmt_pct(pct(row['modeled_cycles'], raw_modeled_cycles))} | {_fmt_pct(pct(row['modeled_cycles'], modeled_mapped_cycles))} | "
            f"{row['modeled_events']} | {row['modeled_max_cycles']} | "
            f"{row['modeled_cycle_kinds'].get('latency(cycles)', 0)} | "
            f"{row['modeled_cycle_kinds'].get('MTE_fixed_cycles', 0)} | "
            f"{row['modeled_cycle_kinds'].get('MTE_transcendental_fixed_cycles', 0)} | "
            f"{row['modeled_cycle_kinds'].get('STE_fixed_cycles', 0)} | "
            f"{row['modeled_cycle_kinds'].get('ARRIVE_fixed_cycles', 0)} | "
            f"{row['modeled_cycle_kinds'].get('LDU_fixed_cycles', 0)} | "
            f"{row['modeled_cycle_kinds'].get('SNOP_fixed_cycles', 0)} | "
            f"{row['modeled_cycle_kinds'].get('MMA_fixed_cycles', 0)} | "
            f"{top} | {top_instruction_events} | {top_instruction_max_cycles} | "
            f"{top_instruction_total_cycles} | {instruction_breakdown} | `{text}` |"
        )

    lines.extend(
        [
            "",
            "## Method And Boundaries",
            "",
            "- The modeled cycle report is based only on CycleTrace and asm mapping. It does not use mcProfiler.",
            "- Modeled cycles use valid `latency(cycles)` when present; otherwise MTE `Transcendental Function`=16 cycles, other MTE=4 cycles, STE=4 cycles, ARRIVE=4 cycles, LDU=64 cycles, SNOP=4 cycles, MMA=16 cycles.",
            "- Categories without a configured fixed cycle cost are excluded from modeled totals and listed in coverage.",
            "- `JSON events with line/code` counts trace events whose JSON carries a filtered objdump line number and asm text; this is used to map back through the lineinfo objdump.",
            "- `lineinfo entry instructions` counts the instructions parsed from the selected kernel entry in the lineinfo objdump; this is the static asm-side mapping table.",
            "- Mapping uses `cycle-trace JSON line/code -> lineinfo objdump filtered instruction line -> user source line`.",
            "- The device ELF can come from `mxobjdump --extract-elf` on the final binary, an explicit extracted device ELF, or the kernel-probe linked object.",
            "- The Markdown `Mapped asm cycle breakdown` column shows at most 3 asm entries per source line. The complete breakdown is preserved in the CSV/JSON outputs.",
            "- `% total` uses all modeled cycles as denominator. `% mapped` uses only modeled cycles successfully attributed to source lines.",
            "- Percentages are CycleTrace/asm modeled attribution. They are not full wall-clock time shares.",
            "- Some modeled events are not attributed when the JSON lacks `line/code`, or the relevant objdump/source mapping is unavailable.",
            "- Source-line attribution can be imprecise for inlined device helpers or compiler-moved code; control-header intrinsics may be adjusted to the next source statement when lineinfo points into runtime headers.",
            "",
            "## Machine-Readable Outputs",
            "",
            f"- CSV rows: `{csv_path}`",
            f"- JSON summary: `{json_path}`",
            "",
        ]
    )
    md_path.write_text("\n".join(lines), encoding="utf-8")
    return {"markdown": md_path, "csv": csv_path, "json": json_path, "lineinfo_objdump": lineinfo_objdump_path}
