"""Artifact collection and JSON file helpers."""

from __future__ import annotations

import json
import os
import re
import shutil
from pathlib import Path
from typing import Any

from .constants import cycle_trace_filename, cycle_trace_filenames, raw_filenames

HARDWARE_CATS = ("MTE", "STE", "MMA", "BSM", "GLOBAL", "ARRIVE", "LDU")
PER_KERNEL_DIR = "mcprofiler_per_kernel"
MCPROFILER_RAW_NAMES = {
    "mcprofiler_report_dumped.json",
    "mcprofiler_report.txt.json",
    "mcprofiler_report.txt",
    "mcprofiler_report.txt.csv",
}

def load_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as f:
        return json.load(f)

def write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

def cycle_kernel_candidates_from_tracer(path: Path) -> list[dict[str, str]]:
    raw = load_json(path)
    events = raw.get("traceEvents", []) if isinstance(raw, dict) else []
    candidates: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    if not isinstance(events, list):
        return candidates
    for event in events:
        if not isinstance(event, dict) or event.get("ph") != "X":
            continue
        args = event.get("args")
        if not isinstance(args, dict):
            continue
        if not {"grid", "block", "mem"}.issubset(args):
            continue
        mangled_name = str(args.get("name", ""))
        display_name = str(event.get("name", ""))
        if not mangled_name:
            continue
        key = (mangled_name, display_name)
        if key in seen:
            continue
        seen.add(key)
        candidates.append({
            "mangled_name": mangled_name,
            "display_name": display_name,
        })
    return candidates

def validate_cycle_trace(path: Path, cycle_kernel_name: str = "", cycle_kernel_repeat: str = "") -> list[str]:
    try:
        raw = load_json(path)
    except Exception as exc:
        return [f"CycleTrace JSON is not readable: {exc}"]
    events = raw.get("traceEvents", []) if isinstance(raw, dict) else []
    if not isinstance(events, list) or not events:
        return ["CycleTrace JSON has no traceEvents"]
    hardware_events = [
        event for event in events
        if isinstance(event, dict) and event.get("cat") in HARDWARE_CATS
    ]
    if not hardware_events:
        if cycle_kernel_name:
            repeat_note = f", CYCLE_TRACE_KERNEL_REPEAT={cycle_kernel_repeat}" if cycle_kernel_repeat else ""
            return [
                "CycleTrace JSON has no hardware instruction events under "
                f"CYCLE_TRACE_KERNEL_NAME={cycle_kernel_name}{repeat_note}; "
                "verify that CYCLE_TRACE_KERNEL_NAME exactly matches mcTracer args.name "
                "and that CYCLE_TRACE_KERNEL_REPEAT selects an existing repetition"
            ]
        return ["CycleTrace JSON has no hardware instruction events"]
    return []

def validate_json_artifact(path: Path, label: str) -> list[str]:
    try:
        load_json(path)
    except Exception as exc:
        return [f"{label} JSON is not readable: {exc}"]
    return []

def latest_pngs(source: Path) -> list[Path]:
    by_chart: dict[str, Path] = {}
    for png in sorted(source.glob("*.png*")):
        match = re.match(r"^(?P<chart>.*?)(?P<stamp>\d{14,})(?P<suffix>\.png.*)$", png.name)
        key = match.group("chart") if match else png.stem
        previous = by_chart.get(key)
        if previous is None or png.name > previous.name:
            by_chart[key] = png
    return sorted(by_chart.values(), key=lambda path: path.name)

def collect_artifacts(
    source: Path,
    run_dir: Path,
    tag: str,
    cycle_dpc_id: str | int | None = None,
    require_mcprofiler: bool = False,
    skip_mcprofiler: bool = False,
) -> Path:
    if not source.is_dir():
        raise FileNotFoundError(f"source is not a directory: {source}")

    dest = run_dir / "artifacts"
    same_source_and_dest = source.resolve() == dest.resolve()
    if dest.exists() and not same_source_and_dest:
        shutil.rmtree(dest)
    dest.mkdir(parents=True, exist_ok=True)
    cycle_names = cycle_trace_filenames(cycle_dpc_id)
    all_raw_names = raw_filenames(cycle_dpc_id)
    raw_names = tuple(name for name in all_raw_names if not (skip_mcprofiler and name in MCPROFILER_RAW_NAMES))
    copied: list[str] = []
    for name in raw_names:
        src = source / name
        if src.exists():
            if not same_source_and_dest:
                shutil.copy2(src, dest / name)
            copied.append(name)

    source_pngs = list(source.glob("*.png*"))
    if not skip_mcprofiler:
        for png in latest_pngs(source):
            if not same_source_and_dest:
                shutil.copy2(png, dest / png.name)
            copied.append(png.name)

    per_kernel_source = source / PER_KERNEL_DIR
    per_kernel_manifest: dict[str, Any] = {}
    if not skip_mcprofiler and per_kernel_source.is_dir():
        per_kernel_dest = dest / PER_KERNEL_DIR
        if not same_source_and_dest:
            if per_kernel_dest.exists():
                shutil.rmtree(per_kernel_dest)
            shutil.copytree(per_kernel_source, per_kernel_dest)
        copied.append(PER_KERNEL_DIR)
        manifest_path = per_kernel_dest / "manifest.json"
        if manifest_path.exists():
            try:
                raw_manifest = load_json(manifest_path)
                if isinstance(raw_manifest, dict):
                    per_kernel_manifest = raw_manifest
            except Exception:
                per_kernel_manifest = {}

    mcprofiler_names = ("mcprofiler_report_dumped.json", "mcprofiler_report.txt.json")
    missing_optional = [] if skip_mcprofiler else [name for name in mcprofiler_names if name not in copied]
    invalid_optional: list[str] = []
    for name in mcprofiler_names:
        if name in copied:
            invalid_optional.extend(validate_json_artifact(dest / name, name))
    mcprofiler_scope = (
        "skipped"
        if skip_mcprofiler
        else "per-kernel"
        if per_kernel_manifest.get("status") == "single-occurrence"
        else "per-kernel-multiple-occurrences"
        if per_kernel_manifest.get("status") == "multi-occurrence"
        else "command-level"
    )
    requested_device = os.environ.get("TRACE_REPORT_REQUESTED_MACA_VISIBLE_DEVICES", "")
    selected_device = os.environ.get("TRACE_REPORT_SELECTED_MACA_VISIBLE_DEVICES", "")
    auto_selected_device = os.environ.get("TRACE_REPORT_AUTO_SELECTED_DEVICE", "").lower() == "true"
    multi_occurrence_profiler = mcprofiler_scope == "per-kernel-multiple-occurrences"
    mcprofiler_available = (not skip_mcprofiler) and not missing_optional and not invalid_optional
    per_kernel_complete = (
        isinstance(per_kernel_manifest, dict)
        and per_kernel_manifest.get("status") == "multi-occurrence"
        and int(per_kernel_manifest.get("occurrence_count") or 0) > 1
        and per_kernel_manifest.get("occurrence_count") == per_kernel_manifest.get("complete_occurrence_count")
    )
    manifest = {
        "tag": tag,
        "source": str(source),
        "artifact_dir": str(dest),
        "copied_files": copied,
        "require_mcprofiler": require_mcprofiler,
        "skip_mcprofiler": skip_mcprofiler,
        "mcprofiler_available": mcprofiler_available,
        "cycle_trace_primary": cycle_trace_filename(cycle_dpc_id),
        "cycle_trace_files": [name for name in cycle_names if name in copied],
        "missing_required": [
            name for name in (*cycle_names, "tracer_out.json")
            if name not in copied
        ],
        "missing_optional": missing_optional,
        "invalid_optional": invalid_optional,
        "invalid_required": [],
        "mcprofiler_scope": mcprofiler_scope,
        "mcprofiler_per_kernel": per_kernel_manifest,
        "requested_maca_visible_devices": requested_device,
        "selected_maca_visible_devices": selected_device,
        "auto_selected_device": auto_selected_device,
    }
    if require_mcprofiler and not skip_mcprofiler and missing_optional and not multi_occurrence_profiler:
        manifest["missing_required"].extend(missing_optional)
    if require_mcprofiler and not skip_mcprofiler and invalid_optional and not multi_occurrence_profiler:
        manifest["invalid_required"].extend(invalid_optional)
    if require_mcprofiler and not skip_mcprofiler and multi_occurrence_profiler and not per_kernel_complete:
        manifest["invalid_required"].append("mcprofiler_per_kernel manifest is incomplete for multi-occurrence profiler scope")
    cycle_path = dest / cycle_trace_filename(cycle_dpc_id)
    if cycle_path.exists():
        manifest["invalid_required"].extend(validate_cycle_trace(cycle_path))
    write_json(dest / "collection_manifest.json", manifest)
    if manifest["missing_required"] or manifest["invalid_required"]:
        raise ValueError(
            "required artifacts are incomplete: "
            f"missing={manifest['missing_required']}, invalid={manifest['invalid_required']}"
        )
    if source.resolve() == run_dir.resolve():
        for name in raw_names:
            path = source / name
            if path.exists():
                path.unlink()
        for png in source_pngs:
            if png.exists():
                png.unlink()
        per_kernel_root = source / PER_KERNEL_DIR
        if per_kernel_root.exists():
            shutil.rmtree(per_kernel_root)
    return dest

def find_artifact_dir(run_dir: Path, tag: str, artifact_dir: Path | None) -> Path:
    if artifact_dir is not None:
        return artifact_dir
    return run_dir / "artifacts"
