"""Metric assembly, bound classification, and diagnosis rules."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from .artifacts import find_artifact_dir, load_json, write_json
from .constants import KEY_METRICS, NA, cycle_trace_filename, primary_dpc_id, validate_dpc_id
from .parsers import derive_roofline_placement, empty_profiler, parse_cycle_trace, parse_profiler, parse_tracer
from .render import render_digest, render_report
from .utils import (
    as_number,
    expects_tensor_path,
    fmt,
    fmt_pct,
    get_path,
    has_bank_conflict_signal,
    has_wsm_activity,
    is_metric_available,
    parse_number,
)

def analyze_artifacts(
    run_dir: Path,
    tag: str,
    artifact_dir: Path | None = None,
    heuristic_bound_mode: str = "coarse",
    cycle_dpc_id: str | int | None = None,
    require_mcprofiler: bool = False,
    skip_mcprofiler: bool = False,
    cycle_kernel_name: str = "",
    cycle_kernel_repeat: str = "",
    cycle_sample_mode: str = "",
) -> dict[str, Any]:
    root = find_artifact_dir(run_dir, tag, artifact_dir)
    if not root.is_dir():
        raise FileNotFoundError(root)

    requested_dpc_id = validate_dpc_id(cycle_dpc_id)
    cycle_file = cycle_trace_filename(requested_dpc_id)
    cycle_path = root / cycle_file
    tracer_path = root / "tracer_out.json"
    dumped_path, txt_path, profiler_artifacts = resolve_profiler_artifacts(root, skip_mcprofiler=skip_mcprofiler)
    complete_profiler_artifacts = dumped_path.exists() and txt_path.exists()

    analysis_dir = run_dir / "analysis"
    analysis_dir.mkdir(parents=True, exist_ok=True)

    collection_scope = build_collection_scope(
        cycle_kernel_name=cycle_kernel_name or os.environ.get("CYCLE_TRACE_KERNEL_NAME", ""),
        cycle_kernel_repeat=cycle_kernel_repeat or os.environ.get("CYCLE_TRACE_KERNEL_REPEAT", ""),
        cycle_sample_mode=cycle_sample_mode or os.environ.get("CYCLE_TRACE_SAMPLE_MODE", ""),
    )
    if skip_mcprofiler:
        collection_scope["skip_mcprofiler"] = True
        collection_scope["mcprofiler_scope"] = "skipped"
        collection_scope["mcprofiler_scope_note"] = "mcProfiler was skipped by SKIP_MCPROFILER=true; profiler metrics are intentionally unavailable."
    apply_mcprofiler_scope(collection_scope, root)
    apply_device_scope(collection_scope, root)
    profiler_optional_by_scope = collection_scope.get("mcprofiler_scope") in {
        "per-kernel-multiple-occurrences",
        "per-kernel-unmatched",
        "per-usecase-multiple-occurrences",
    }
    launch = parse_tracer(tracer_path, collection_scope.get("cycle_trace_kernel_name", ""))
    target_occurrences = int(launch.get("target_kernel_occurrence_count", 0) or 0)
    collection_scope["target_kernel_occurrence_count"] = target_occurrences
    if target_occurrences > 1:
        collection_scope["target_invocation_scope"] = "multiple_target_kernel_launches"
        collection_scope["target_invocation_scope_note"] = (
            "The target kernel launched multiple times in this trace command. "
            "CycleTrace instruction counts and trace span are aggregate sampling evidence, not single-launch latency."
        )
    else:
        collection_scope["target_invocation_scope"] = "single_target_kernel_launch"
        collection_scope["target_invocation_scope_note"] = "The target kernel launched once in this trace command."
    cycle = parse_cycle_trace(
        cycle_path,
        collection_scope.get("cycle_trace_kernel_name", ""),
        collection_scope.get("cycle_trace_kernel_repeat", ""),
    )
    profiler_parse_error = ""
    if complete_profiler_artifacts:
        try:
            profiler = parse_profiler(dumped_path, txt_path)
        except Exception as exc:
            profiler_parse_error = str(exc)
            profiler = empty_profiler()
    else:
        profiler = empty_profiler()
    if require_mcprofiler and not skip_mcprofiler and not profiler.get("available", False) and not profiler_optional_by_scope:
        suffix = f": {profiler_parse_error}" if profiler_parse_error else ""
        raise ValueError(
            "mcProfiler artifacts are required by REQUIRE_MCPROFILER=true but are missing; "
            f"increase COLLECT_TIMEOUT_SECONDS and retry if mcProfiler needs more time{suffix}"
        )
    update_roofline_placement(launch, cycle, profiler)

    metrics = {
        "tag": tag,
        "artifact_dir": str(root),
        "require_mcprofiler": require_mcprofiler,
        "skip_mcprofiler": skip_mcprofiler,
        "mcprofiler_available": complete_profiler_artifacts and bool(profiler.get("available", False)),
        "mcprofiler_artifacts": profiler_artifacts,
        "cycle_dpc_id": requested_dpc_id,
        "cycle_dpc_primary": primary_dpc_id(requested_dpc_id),
        "cycle_trace_file": cycle_file,
        "cycle_trace_files": sorted(path.name for path in root.glob("c-trace_output_dpc_*.json")),
        "kernel": {
            "name": launch.get("kernel_name", ""),
            "grid": launch.get("grid", []),
            "block": launch.get("block", []),
            "span_cycles": cycle.get("span_cycles", 0),
        },
        "launch": launch,
        "cycle": cycle,
        "profiler": profiler,
        "collection_scope": collection_scope,
        "bound": classify_bound(launch, cycle, profiler, heuristic_bound_mode),
        "diagnosis": diagnose(launch, cycle, profiler),
    }
    metrics["source_latency"] = load_source_latency_summary(analysis_dir, tag)
    metrics["metric_coverage"] = build_metric_coverage(metrics)
    key = {path: get_path(metrics, path) for path, _, _, _ in KEY_METRICS}

    write_json(analysis_dir / f"metrics_all_{tag}.json", metrics)
    write_json(
        analysis_dir / f"metrics_key_{tag}.json",
        {
            "tag": tag,
            "metrics": key,
            "collection_scope": collection_scope,
            "metric_coverage_summary": metrics["metric_coverage"]["summary"],
        },
    )
    (analysis_dir / f"digest_{tag}.md").write_text(render_digest(metrics), encoding="utf-8")
    (run_dir / f"REPORT_{tag}.md").write_text(render_report(metrics), encoding="utf-8")
    return metrics

def build_collection_scope(
    *,
    cycle_kernel_name: str = "",
    cycle_kernel_repeat: str = "",
    cycle_sample_mode: str = "",
) -> dict[str, Any]:
    kernel_name = str(cycle_kernel_name or "")
    kernel_repeat = str(cycle_kernel_repeat or "")
    sample_mode = str(cycle_sample_mode or "")
    if kernel_name:
        if not kernel_repeat:
            kernel_repeat = "-1"
        if not sample_mode:
            sample_mode = "C"
    return {
        "cycle_trace_kernel_filter_enabled": bool(kernel_name),
        "cycle_trace_kernel_name": kernel_name,
        "cycle_trace_kernel_repeat": kernel_repeat,
        "cycle_trace_sample_mode": sample_mode,
        "cycle_trace_scope_note": (
            "CycleTrace metrics and mcTracer launch/resource fields are narrowed to "
            "the target kernel; mcProfiler scope is reported separately."
            if kernel_name
            else "CycleTrace metrics use the configured command-level collection scope."
        ),
        "mcprofiler_scope": "command-level",
        "skip_mcprofiler": False,
        "mcprofiler_scope_note": "mcProfiler metrics use the configured command-level collection scope.",
    }

def apply_mcprofiler_scope(scope: dict[str, Any], artifact_dir: Path) -> None:
    manifest_path = artifact_dir / "collection_manifest.json"
    if not manifest_path.exists():
        return
    try:
        manifest = load_json(manifest_path)
    except Exception:
        return
    if not isinstance(manifest, dict):
        return
    profiler_scope = str(manifest.get("mcprofiler_scope", "") or "command-level")
    skip_mcprofiler = bool(scope.get("skip_mcprofiler", False)) or bool(manifest.get("skip_mcprofiler", False)) or profiler_scope == "skipped"
    per_kernel = manifest.get("mcprofiler_per_kernel", {})
    occurrence_count = per_kernel.get("occurrence_count", 0) if isinstance(per_kernel, dict) else 0
    scope["skip_mcprofiler"] = skip_mcprofiler
    scope["mcprofiler_scope"] = profiler_scope
    scope["mcprofiler_occurrence_count"] = occurrence_count
    if "usecase_id" in manifest:
        scope["usecase_id"] = manifest.get("usecase_id", "")
    if "matched_occurrence_id" in manifest:
        scope["matched_occurrence_id"] = manifest.get("matched_occurrence_id", "")
    if "matched_occurrence_dir" in manifest:
        scope["matched_occurrence_dir"] = manifest.get("matched_occurrence_dir", "")
    if skip_mcprofiler:
        scope["mcprofiler_scope"] = "skipped"
        scope["mcprofiler_scope_note"] = "mcProfiler was skipped by SKIP_MCPROFILER=true; profiler metrics are intentionally unavailable."
    elif profiler_scope == "per-kernel":
        scope["mcprofiler_scope_note"] = (
            "mcProfiler metrics were collected with --kernelnames/--per-kernel and "
            "normalized from the target kernel occurrence artifacts."
        )
    elif profiler_scope == "per-kernel-multiple-occurrences":
        scope["mcprofiler_scope_note"] = (
            "mcProfiler produced multiple target-kernel occurrences; the main report "
            "does not use command-level report_* profiler data. Use split-usecases for "
            "per-usecase profiler metrics."
        )
    elif profiler_scope == "per-kernel-unmatched":
        scope["mcprofiler_scope_note"] = (
            "mcProfiler per-kernel occurrences could not be matched to this derived "
            "usecase; profiler metrics are unavailable for strong conclusions."
        )
    elif profiler_scope == "per-usecase-multiple-occurrences":
        scope["mcprofiler_scope_note"] = (
            "Multiple mcProfiler occurrences map to this aggregated usecase; profiler "
            "aggregation is not applied, so profiler metrics are unavailable for strong conclusions."
        )
    elif profiler_scope == "per-usecase-ref":
        reference = manifest.get("mcprofiler_reference", {})
        occurrence_id = reference.get("occurrence_id", "") if isinstance(reference, dict) else ""
        scope["mcprofiler_scope_note"] = (
            "mcProfiler metrics are matched to this usecase and read by reference from "
            f"parent per-kernel occurrence artifacts{f' ({occurrence_id})' if occurrence_id else ''}."
        )
    else:
        scope["mcprofiler_scope_note"] = "mcProfiler metrics use the configured command-level collection scope."

def apply_device_scope(scope: dict[str, Any], artifact_dir: Path) -> None:
    manifest_path = artifact_dir / "collection_manifest.json"
    if not manifest_path.exists():
        return
    try:
        manifest = load_json(manifest_path)
    except Exception:
        return
    if not isinstance(manifest, dict):
        return
    requested = str(manifest.get("requested_maca_visible_devices", "") or "")
    selected = str(manifest.get("selected_maca_visible_devices", "") or "")
    auto_selected = bool(manifest.get("auto_selected_device", False))
    if not selected:
        return
    scope["requested_maca_visible_devices"] = requested or selected
    scope["selected_maca_visible_devices"] = selected
    scope["auto_selected_device"] = auto_selected


def resolve_profiler_artifacts(artifact_dir: Path, *, skip_mcprofiler: bool = False) -> tuple[Path, Path, dict[str, Any]]:
    local_dumped = artifact_dir / "mcprofiler_report_dumped.json"
    local_txt = artifact_dir / "mcprofiler_report.txt.json"
    artifacts: dict[str, Any] = {
        "mode": "skipped" if skip_mcprofiler else "local",
        "dumped": str(local_dumped),
        "txt_json": str(local_txt),
        "txt": str(artifact_dir / "mcprofiler_report.txt"),
        "txt_csv": str(artifact_dir / "mcprofiler_report.txt.csv"),
        "png_dir": str(artifact_dir / "png"),
    }
    if skip_mcprofiler:
        return (
            artifact_dir / ".skipped_mcprofiler_report_dumped.json",
            artifact_dir / ".skipped_mcprofiler_report.txt.json",
            artifacts,
        )
    if local_dumped.exists() or local_txt.exists():
        return local_dumped, local_txt, artifacts

    manifest_path = artifact_dir / "collection_manifest.json"
    if not manifest_path.exists():
        return local_dumped, local_txt, artifacts
    try:
        manifest = load_json(manifest_path)
    except Exception:
        return local_dumped, local_txt, artifacts
    if not isinstance(manifest, dict):
        return local_dumped, local_txt, artifacts
    reference = manifest.get("mcprofiler_reference")
    if not isinstance(reference, dict):
        return local_dumped, local_txt, artifacts
    files = reference.get("files", {})
    if not isinstance(files, dict):
        return local_dumped, local_txt, artifacts

    def resolve(key: str, fallback: Path) -> Path:
        value = files.get(key)
        if not isinstance(value, str) or not value:
            return fallback
        path = Path(value)
        return path if path.is_absolute() else artifact_dir / path

    dumped = resolve("dumped", local_dumped)
    txt = resolve("txt_json", local_txt)
    ref_artifact_dir = reference.get("artifact_dir", "")
    ref_png_dir = reference.get("png_dir", "")
    artifacts = {
        "mode": "reference",
        "occurrence_id": reference.get("occurrence_id", ""),
        "artifact_dir": str((artifact_dir / ref_artifact_dir) if isinstance(ref_artifact_dir, str) and ref_artifact_dir and not Path(ref_artifact_dir).is_absolute() else ref_artifact_dir),
        "dumped": str(dumped),
        "txt_json": str(txt),
        "txt": str(resolve("txt", artifact_dir / "mcprofiler_report.txt")),
        "txt_csv": str(resolve("txt_csv", artifact_dir / "mcprofiler_report.txt.csv")),
        "png_dir": str((artifact_dir / ref_png_dir) if isinstance(ref_png_dir, str) and ref_png_dir and not Path(ref_png_dir).is_absolute() else ref_png_dir),
        "png_files": reference.get("png_files", []),
    }
    return dumped, txt, artifacts

def load_source_latency_summary(analysis_dir: Path, tag: str) -> dict[str, Any]:
    path = analysis_dir / f"source_latency_{tag}.json"
    strict = os.environ.get("TRACE_REPORT_REQUIRE_SOURCE_LATENCY_SUMMARY") == "1"
    if not path.exists():
        if strict:
            raise FileNotFoundError(f"source-latency summary is required but missing: {path}")
        return {"available": False}
    try:
        raw = load_json(path)
    except Exception:
        if strict:
            raise
        return {"available": False}
    if not isinstance(raw, dict):
        if strict:
            raise ValueError(f"source-latency JSON is not an object: {path}")
        return {"available": False}
    coverage = raw.get("coverage", {})
    inputs = raw.get("inputs", {})
    source_lines = raw.get("source_lines", [])
    top_instructions = raw.get("top_modeled_instructions", [])
    if not isinstance(coverage, dict):
        coverage = {}
    if not isinstance(inputs, dict):
        inputs = {}
    if not isinstance(source_lines, list):
        source_lines = []
    if not isinstance(top_instructions, list):
        top_instructions = []

    return {
        "available": True,
        "json": str(path),
        "markdown": str(analysis_dir / f"source_latency_{tag}.md"),
        "csv": str(analysis_dir / f"source_latency_{tag}.csv"),
        "inputs": {
            "source": inputs.get("source", ""),
            "cycle_trace_json": inputs.get("cycle_trace_json", ""),
            "lineinfo_input_kind": inputs.get("lineinfo_input_kind", ""),
            "lineinfo_objdump": inputs.get("lineinfo_objdump", ""),
            "lineinfo_device_elf": inputs.get("lineinfo_device_elf", ""),
        },
        "coverage": {
            "trace_events_total": coverage.get("trace_events_total", 0),
            "trace_events_with_line_code": coverage.get("trace_events_with_line_code", 0),
            "lineinfo_entry_instructions": coverage.get("lineinfo_entry_instructions", 0),
            "raw_modeled_events": coverage.get("raw_modeled_events", 0),
            "raw_modeled_cycles": coverage.get("raw_modeled_cycles", 0),
            "modeled_source_mapped_events": coverage.get("modeled_source_mapped_events", 0),
            "modeled_source_mapped_cycles": coverage.get("modeled_source_mapped_cycles", 0),
            "modeled_source_mapped_event_share_pct": coverage.get("modeled_source_mapped_event_share_pct", 0),
            "modeled_source_mapped_cycle_share_pct": coverage.get("modeled_source_mapped_cycle_share_pct", 0),
            "modeled_unmapped_reasons": coverage.get("modeled_unmapped_reasons", {}),
            "unmodeled_no_fixed_cycle_events": coverage.get("unmodeled_no_fixed_cycle_events", {}),
        },
        "source_lines": source_lines[:10],
        "top_modeled_instructions": top_instructions[:10],
    }

def classify_bound(
    launch: dict[str, Any],
    cycle: dict[str, Any],
    profiler: dict[str, Any],
    mode: str,
) -> dict[str, Any]:
    """Classify the dominant bound type from stable aggregate counters.

    coarse mode returns one of: compute, memory, latency, occupancy, mixed, unclear.
    detailed mode preserves compound C500-specific labels.
    """
    evidence = {
        "cycle_mma_pct": cycle.get("mma_pct", 0),
        "cycle_mte_pct": cycle.get("mte_pct", 0),
        "cycle_gvm_pct": cycle.get("gvm_pct", 0),
        "cycle_arrive_pct": cycle.get("arrive_pct", 0),
        "cycle_ldu_pct": cycle.get("ldu_pct", 0),
        "cycle_nop_pct": cycle.get("nop_pct", 0),
        "ap_mte_duty_pct": profiler.get("ap_mte_duty_pct", 0),
        "ap_ste_duty_pct": profiler.get("ap_ste_duty_pct", 0),
        "ap_mma_duty_pct": profiler.get("ap_mma_duty_pct", 0),
        "vls_duty_pct": profiler.get("vls_duty_pct", 0),
        "l2c_duty_pct": profiler.get("l2c_duty_pct", 0),
        "vl1_hit_rate_pct": profiler.get("vl1_hit_rate_pct", 0),
        "l2c_hit_rate_pct": profiler.get("l2c_hit_rate_pct", 0),
        "dnoc_read_average_latency": profiler.get("dnoc_read_average_latency", 0),
        "dnoc_read_req": profiler.get("dnoc_read_req", 0),
        "dnoc_write_req": profiler.get("dnoc_write_req", 0),
        "global_memory_read_bytes": profiler.get("global_memory_read_bytes", 0),
        "global_memory_write_bytes": profiler.get("global_memory_write_bytes", 0),
        "shared_memory_efficiency_pct": profiler.get("shared_memory_efficiency_pct", 0),
        "avg_conflict_cycles_per_inst": profiler.get("avg_conflict_cycles_per_inst", 0),
        "effective_occupancy_pct": launch.get("effective_occupancy_pct", 0),
        "mtreg_occupancy_pct": launch.get("mtreg_occupancy_pct", 0),
        "shared_memory_occupancy_pct": launch.get("shared_memory_occupancy_pct", 0),
        "real_ipc": profiler.get("real_ipc", 0),
        "achieved_bandwidth_gbs": profiler.get("achieved_bandwidth_gbs", 0),
        "roofline_hbm_usage_pct": profiler.get("roofline", {}).get("hbm_usage_pct", 0),
        "roofline_vl1_usage_pct": profiler.get("roofline", {}).get("vl1_usage_pct", 0),
        "roofline_l2c_usage_pct": profiler.get("roofline", {}).get("l2c_usage_pct", 0),
        "roofline_compute_gap_pct": profiler.get("roofline", {}).get("compute_gap_pct", 0),
        "roofline_hbm_bandwidth_gap_pct": profiler.get("roofline", {}).get("hbm_bandwidth_gap_pct", 0),
        "roofline_selected_roof_gap_pct": profiler.get("roofline", {}).get("selected_roof_gap_pct", 0),
        "roofline_gap_hint": profiler.get("roofline", {}).get("gap_hint", NA),
        "top_isu_stall": profiler.get("isu_stall_summary", {}).get("top", ""),
        "top_isu_stall_pct": profiler.get("isu_stall_summary", {}).get("top_pct", 0),
    }

    has_profiler = bool(profiler.get("available", False))
    tensor_expected = expects_tensor_path(str(launch.get("kernel_name", "")))
    evidence["tensor_path_expected"] = tensor_expected
    evidence["wsm_activity"] = has_wsm_activity(launch, cycle, profiler)
    ap_mma_available = has_profiler and is_metric_available(evidence["ap_mma_duty_pct"])
    ap_mte_available = has_profiler and is_metric_available(evidence["ap_mte_duty_pct"])
    real_ipc_available = has_profiler and is_metric_available(evidence["real_ipc"])
    l2c_hit_available = has_profiler and is_metric_available(evidence["l2c_hit_rate_pct"])
    vls_duty_available = has_profiler and is_metric_available(evidence["vls_duty_pct"])
    l2c_duty_available = has_profiler and is_metric_available(evidence["l2c_duty_pct"])
    top_isu_stall_available = has_profiler and is_metric_available(evidence["top_isu_stall_pct"])
    is_tensor_underuse = tensor_expected and evidence["cycle_mma_pct"] < 1 and (
        ap_mma_available and as_number(evidence["ap_mma_duty_pct"]) < 3
    )
    is_mte_path = evidence["cycle_mte_pct"] > 30 or (
        ap_mte_available and as_number(evidence["ap_mte_duty_pct"]) > 15
    )
    is_bank_conflict = has_profiler and has_bank_conflict_signal(launch, cycle, profiler)
    effective_occupancy = as_number(evidence["effective_occupancy_pct"])
    is_low_occupancy = 0 < effective_occupancy < 25
    is_memory_pressure = (
        evidence["cycle_gvm_pct"] > 25
        or (vls_duty_available and as_number(evidence["vls_duty_pct"]) > 50)
        or (l2c_duty_available and as_number(evidence["l2c_duty_pct"]) > 50)
        or (
            l2c_hit_available
            and 0 < as_number(evidence["l2c_hit_rate_pct"]) < 80
            and (
                evidence["cycle_gvm_pct"] > 10
                or (vls_duty_available and as_number(evidence["vls_duty_pct"]) > 10)
                or (l2c_duty_available and as_number(evidence["l2c_duty_pct"]) > 10)
            )
        )
    )
    is_latency_signal = evidence["cycle_nop_pct"] > 10 or (
        real_ipc_available and as_number(evidence["real_ipc"]) < 10 and not is_memory_pressure
    )
    is_issue_stall_signal = top_isu_stall_available and as_number(evidence["top_isu_stall_pct"]) > 50
    is_compute_signal = is_tensor_underuse or is_mte_path or (
        ap_mma_available and as_number(evidence["ap_mma_duty_pct"]) >= 30
    )
    is_memory_signal = is_memory_pressure or is_bank_conflict
    coarse_signals: list[str] = []
    if is_compute_signal:
        coarse_signals.append("compute")
    if is_memory_signal:
        coarse_signals.append("memory")
    if is_latency_signal:
        coarse_signals.append("latency")
    if is_low_occupancy:
        coarse_signals.append("occupancy")

    if mode == "detailed":
        labels: list[str] = []
        if is_tensor_underuse and is_mte_path:
            labels.append("mte/vector-compute-bound")
        elif ap_mma_available and as_number(evidence["ap_mma_duty_pct"]) >= 30:
            labels.append("mma/tensor-compute-bound")
        elif is_mte_path:
            labels.append("mte-compute-bound")
        if is_bank_conflict:
            labels.append("shared-memory-bank-conflict")
        if is_low_occupancy:
            labels.append("low-occupancy")
        if is_memory_pressure:
            labels.append("memory-bandwidth/traffic-pressure")
        if is_latency_signal:
            labels.append("latency/pipeline-bubble")
        if not labels:
            labels.append("unclear")
        primary = labels[0]
        bound_type = " + ".join(labels)
    else:
        if len(coarse_signals) > 1:
            primary = "mixed"
        elif len(coarse_signals) == 1:
            primary = coarse_signals[0]
        else:
            primary = "unclear"
        bound_type = primary

    rationale = []
    if is_tensor_underuse:
        rationale.append("MMA share and AP MMA duty are very low, so this is not tensor-core saturation.")
    elif tensor_expected and evidence["cycle_mma_pct"] < 1 and not ap_mma_available:
        rationale.append("Tensor underuse cannot be confirmed because mcProfiler AP MMA duty is N/A.")
    if is_mte_path:
        rationale.append("MTE instruction share / duty dominates the executed compute path.")
    if is_bank_conflict:
        rationale.append("Shared-memory efficiency and conflict cycles indicate WSM bank-conflict pressure.")
    if is_low_occupancy:
        rationale.append("mcTracer occupancy bound is below 25%; streg is unavailable in current JSON.")
    if is_memory_pressure:
        rationale.append("Memory pressure threshold fired from GVM/VLS/L2C/cache evidence.")
    elif has_profiler:
        rationale.append("L2C hit rate and achieved bandwidth do not indicate global-memory bandwidth saturation.")
    if not has_profiler:
        rationale.append("[TR-ANA-002] mcProfiler artifacts are missing; profiler duty, IPC, cache, bank-conflict, and Roofline evidence are N/A.")
    if is_latency_signal:
        rationale.append("NOP/IPC thresholds indicate latency or pipeline-bubble pressure.")
    if is_issue_stall_signal:
        rationale.append("ISU stall layout has a dominant stall bucket; use it as supporting issue-side evidence, not an NCU-style warp stall reason.")
    if mode != "detailed":
        if primary == "mixed":
            rationale.append("Multiple coarse bound signals fired; no single dominant coarse bound is selected.")
        elif primary == "unclear":
            rationale.append("No coarse bound threshold fired strongly enough to classify this kernel as compute, memory, latency, or occupancy bound.")

    return {
        "method": "c500-heuristic",
        "mode": mode,
        "type": bound_type,
        "primary": primary,
        "signals": coarse_signals,
        "evidence": evidence,
        "rationale": rationale,
        "thresholds": {
            "tensor_underuse": "tensor-like kernel, cycle_mma_pct < 1, mcProfiler available, and ap_mma_duty_pct < 3",
            "mte_path": "cycle_mte_pct > 30 or ap_mte_duty_pct > 15",
            "bank_conflict": "avg_conflict_cycles_per_inst > 0.5, or WSM activity with 0 < shared_memory_efficiency_pct < 80",
            "low_occupancy": "0 < effective_occupancy_pct < 25",
            "memory_pressure": "gvm_pct > 25, vls/l2c duty > 50, or l2c_hit_rate_pct < 80 with supporting GVM/VLS/L2C activity",
            "latency_signal": "nop_pct > 10 or real_ipc < 10 when memory pressure is absent",
            "mixed": "more than one coarse signal fires",
            "unclear": "no coarse signal fires; report evidence and data boundaries instead of forcing a bound",
        },
    }

def update_roofline_placement(
    launch: dict[str, Any],
    cycle: dict[str, Any],
    profiler: dict[str, Any],
) -> None:
    roof = profiler.get("roofline", {})
    if not isinstance(roof, dict) or not roof:
        return
    tensor_expected = expects_tensor_path(str(launch.get("kernel_name", "")))
    ap_mma = profiler.get("ap_mma_duty_pct")
    mma_evidence = cycle.get("mma_pct", 0) > 0 or is_metric_available(ap_mma)
    if tensor_expected and mma_evidence:
        peak_name = "MMA-FP16"
        peak_source = "tensor-kernel-with-mma-evidence"
    else:
        peak_name = "FMA"
        peak_source = "default-fma-non-tensor-or-mma-unconfirmed"
    roof.update(derive_roofline_placement(roof, peak_name, peak_source))

def diagnose(launch: dict[str, Any], cycle: dict[str, Any], profiler: dict[str, Any]) -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []
    has_profiler = bool(profiler.get("available", False))
    tensor_expected = expects_tensor_path(str(launch.get("kernel_name", "")))
    if (
        has_profiler
        and tensor_expected
        and is_metric_available(profiler.get("ap_mma_duty_pct"))
        and as_number(profiler.get("ap_mma_duty_pct")) < 3
        and cycle.get("mma_pct", 0) < 1
    ):
        findings.append({
            "severity": "high",
            "title": "Tensor core underuse",
            "evidence": f"MMA share={cycle.get('mma_pct', 0):.2f}%, AP MMA duty={fmt_pct(profiler.get('ap_mma_duty_pct'))}",
            "next": "Check whether the kernel maps the tensor-capable inner loop to the expected MMA instruction path instead of vector MTE work; require source or assembly evidence before naming an implementation change.",
        })
    elif has_profiler and tensor_expected and cycle.get("mma_pct", 0) < 1 and not is_metric_available(profiler.get("ap_mma_duty_pct")):
        findings.append({
            "severity": "info",
            "title": "Tensor core underuse cannot be confirmed",
            "evidence": f"MMA share={cycle.get('mma_pct', 0):.2f}%, AP MMA duty={fmt_pct(profiler.get('ap_mma_duty_pct'))}",
            "next": "Treat AP MMA duty as unavailable; rerun mcProfiler or inspect source/assembly before claiming tensor-core underuse.",
        })
    if has_profiler and has_bank_conflict_signal(launch, cycle, profiler):
        findings.append({
            "severity": "high",
            "title": "Shared memory bank conflict pressure",
            "evidence": f"SMEM efficiency={fmt_pct(profiler.get('shared_memory_efficiency_pct'))}, conflict cycles/inst={fmt(profiler.get('avg_conflict_cycles_per_inst'))}",
            "next": "Inspect WSM layout, vectorized access stride, and padding/skew options.",
        })
    effective_occupancy = as_number(launch.get("effective_occupancy_pct", 0))
    if 0 < effective_occupancy < 25:
        if tensor_expected:
            next_action = "For this tensor-path kernel, inspect register pressure, shared-memory tile footprint, and tile shape before changing launch geometry."
        else:
            next_action = "For this non-tensor kernel, inspect grid/block coverage and only then reduce resource footprint if mcTracer shows a real limiter."
        findings.append({
            "severity": "medium",
            "title": "Low launch occupancy bound",
            "evidence": f"mtreg={launch.get('mtreg_occupancy_pct', 0):.2f}%, smem={launch.get('shared_memory_occupancy_pct', 0):.2f}%",
            "next": f"{next_action} streg limit still needs compiler/assembly evidence.",
        })
    elif effective_occupancy == 0 and (
        launch.get("mtreg_occupancy_pct", 0) == 0
        or launch.get("shared_memory_occupancy_pct", 0) == 0
    ):
        if tensor_expected:
            next_action = "Treat the zero occupancy fields as unavailable until mcTracer or compiler resource output confirms the tensor tile resource limiter."
        else:
            next_action = "Treat the zero occupancy fields as unavailable for this non-tensor kernel; do not reduce resources based on this value alone."
        findings.append({
            "severity": "info",
            "title": "Occupancy fields unavailable or inconsistent",
            "evidence": f"mtreg={launch.get('mtreg_occupancy_pct', 0):.2f}%, smem={launch.get('shared_memory_occupancy_pct', 0):.2f}%",
            "next": next_action,
        })
    if cycle.get("nop_pct", 0) > 10:
        findings.append({
            "severity": "medium",
            "title": "Pipeline bubble signal",
            "evidence": f"NOP share={cycle.get('nop_pct', 0):.2f}%",
            "next": "Correlate with barriers, load-use distance, and mcProfiler IPC.",
        })
    if not findings:
        findings.append({
            "severity": "info",
            "title": "No high-confidence rule fired",
            "evidence": "Rule thresholds did not identify a dominant issue.",
            "next": "Compare against an optimized version and inspect per-source behavior.",
        })
    return findings

def build_metric_coverage(metrics: dict[str, Any]) -> dict[str, Any]:
    launch = metrics.get("launch", {})
    cycle = metrics.get("cycle", {})
    profiler = metrics.get("profiler", {})
    source_latency = metrics.get("source_latency", {})
    flow = profiler.get("memory_data_flow", {})
    atomic_keys = [key for key in profiler.get("all_scalars", {}) if "atomic" in key.lower()]

    report_metric_groups = [
        {
            "group": "kernel_identity",
            "status": "reported",
            "fields": ["kernel.name", "kernel.grid", "kernel.block", "artifact_dir"],
            "role": "Reproducibility and target-kernel confirmation.",
        },
        {
            "group": "bound_classification",
            "status": "reported",
            "fields": ["bound.mode", "bound.type", "bound.primary", "bound.evidence", "bound.rationale"],
            "role": "Headline diagnosis and machine-readable decision evidence.",
        },
        {
            "group": "cycletrace_instruction_mix",
            "status": "reported",
            "fields": ["cycle.category_counts", "cycle.category_pcts", "cycle.mte_pct", "cycle.ste_pct", "cycle.mma_pct", "cycle.bsm_pct", "cycle.gvm_pct", "cycle.arrive_pct", "cycle.ldu_pct", "cycle.sync_count"],
            "role": "Hardware-category instruction mix from CycleTrace `cat`; name-level NOP/Branch/GVM load-store are subevent context.",
        },
        {
            "group": "launch_resources",
            "status": "reported",
            "fields": ["launch.registers_per_thread", "launch.static_shared_bytes", "launch.dynamic_shared_bytes", "launch.private_per_thread", "launch.mtreg_occupancy_pct", "launch.shared_memory_occupancy_pct"],
            "role": "Occupancy and resource-limit diagnosis.",
        },
        {
            "group": "tail_and_balance",
            "status": "reported",
            "fields": ["cycle.span_cycles", "cycle.wif_p25", "cycle.wif_p50", "cycle.wif_p75", "cycle.wif_max", "cycle.gvm_600cy_peak", "profiler.dpc_compute_imbalance_pct", "profiler.achieved_waves", "profiler.dispatched_waves"],
            "role": "Limited CycleTrace event-timeline proxy plus aggregate tail-effect and DPC balance proxy.",
        },
        {
            "group": "hardware_duty",
            "status": "reported",
            "fields": ["profiler.ap_mte_duty_pct", "profiler.ap_ste_duty_pct", "profiler.ap_mma_duty_pct", "profiler.real_ipc", "profiler.instruction_throughput", "profiler.instruction_throughput_efficiency_pct", "profiler.compute_instruction_cycles_summary", "profiler.isu_stall_summary"],
            "role": "Hardware utilization, instruction efficiency, compute-cycle split, and issue-side stall context.",
        },
        {
            "group": "memory_hierarchy",
            "status": "reported",
            "fields": ["profiler.vl1_hit_rate_pct", "profiler.l2c_hit_rate_pct", "profiler.sl1_hit_rate_pct", "profiler.dnoc_read_average_latency", "profiler.dnoc_read_req", "profiler.dnoc_write_req", "profiler.global_memory_read_bytes", "profiler.global_memory_write_bytes", "profiler.memory_data_flow", "profiler.dnoc_latency_histogram_pct", "profiler.vl1_partition_stall_summary"],
            "role": "Memory-bound, traffic volume, latency-tail, and cache-pressure checks.",
        },
        {
            "group": "bank_conflict_private_atomic",
            "status": "reported",
            "fields": ["profiler.shared_memory_efficiency_pct", "profiler.avg_conflict_cycles_per_inst", "profiler.avg_cycles_per_load", "profiler.avg_cycles_per_store", "launch.private_per_thread", "profiler.memory_data_flow.private_kernel_rd"],
            "role": "WSM bank-conflict, spill/private-memory, and serialization context.",
        },
        {
            "group": "roofline",
            "status": "reported",
            "fields": ["profiler.achieved_flops_tflops", "profiler.achieved_bandwidth_gbs", "profiler.achieved_intensity_flop_per_byte", "profiler.roofline.case_VL1_I", "profiler.roofline.case_L2C_I", "profiler.roofline.hbm_usage_pct", "profiler.roofline.vl1_usage_pct", "profiler.roofline.l2c_usage_pct", "profiler.roofline.compute_gap_pct", "profiler.roofline.hbm_bandwidth_gap_pct", "profiler.roofline.selected_roof_gap_pct", "profiler.roofline.gap_hint", "profiler.roofline.peak_fma_tflops", "profiler.roofline.peak_mma_fp16_tflops"],
            "role": "Empirical compute/memory placement plus mcProfiler RoofLine chart peak, usage, and gap headroom context.",
        },
    ]
    if source_latency.get("available"):
        report_metric_groups.append(
            {
                "group": "source_line_attribution",
                "status": "reported",
                "fields": [
                    "source_latency.coverage",
                    "source_latency.source_lines",
                    "source_latency.top_modeled_instructions",
                ],
                "role": "CycleTrace/asm modeled source-line cycle attribution, top modeled instruction context, and source-aware edit targeting.",
            }
        )

    parsed_not_promoted = [
        {
            "field": "cycle.raw_name_counts / cycle.raw_cat_counts",
            "role": "Raw event audit.",
            "reason": "Duplicates summarized hardware-category and subevent counts; mainly useful for parser validation.",
        },
        {
            "field": "cycle.issue_density_inst_per_cycle",
            "role": "CycleTrace issue-density sanity check.",
            "reason": "Wave lifecycle span is not equivalent to real IPC; mcProfiler IPC is more diagnostic.",
        },
        {
            "field": "launch.kernel_event_duration",
            "role": "Host trace duration context.",
            "reason": "Device-kernel report prioritizes CycleTrace span and mcProfiler cycles.",
            "value": launch.get("kernel_event_duration", 0),
        },
        {
            "field": "launch.top_api_calls_by_duration",
            "role": "Runtime/API overhead context.",
            "reason": "Not part of the current device-kernel bottleneck diagnosis.",
            "available_count": len(launch.get("top_api_calls_by_duration", [])),
        },
        {
            "field": "profiler.all_scalars / profiler.all_charts",
            "role": "Lossless mcProfiler raw evidence.",
            "reason": "Too verbose for the report body; retained for audit and future parser expansion.",
            "available_scalars": len(profiler.get("all_scalars", {})),
            "available_charts": len(profiler.get("all_charts", {})),
        },
        {
            "field": "profiler.memory_data_flow raw entries",
            "role": "Detailed path-level memory flow.",
            "reason": "Report shows recognized read/write groups; raw entries remain available for path-specific investigations.",
            "available_count": len(flow),
        },
        {
            "field": "profiler.roofline raw entries",
            "role": "Raw mcProfiler RoofLine chart inputs.",
            "reason": "Report promotes peak, usage, and intensity context; raw fields remain available for audit.",
            "available_count": len(profiler.get("roofline", {})),
        },
        {
            "field": "atomic-related mcProfiler scalar fields",
            "role": "Atomic/serialization pressure.",
            "reason": "Report summarizes total atomic pressure; individual counters are retained in metrics_all.",
            "available_count": len(atomic_keys),
        },
    ]

    unavailable = []
    if not source_latency.get("available"):
        unavailable.append(
            {
                "dimension": "source-line modeled attribution",
                "impact": "Cannot name source-line edit targets from trace evidence.",
                "required_artifact": "Valid source-latency summary from CycleTrace kernel-probe plus lineinfo objdump.",
            }
        )
    unavailable.append(
        {
            "dimension": "full per-PC stall attribution",
            "impact": "Cannot claim complete PC-level stall hotspots, NCU-style per-warp stall reasons, or wall-clock stall shares.",
            "required_artifact": "A PC-level stall attribution source with wall-clock stall semantics.",
        }
    )
    unavailable.extend([
        {
            "dimension": "full PM-sampling or per-AP utilization timeline",
            "impact": "Cannot prove exact per-AP utilization phases, alternating idle phases, or late low occupancy; only limited CycleTrace event-timeline proxies and aggregate balance are available.",
            "required_artifact": "PM sampling or richer CycleTrace timeline export.",
        },
        {
            "dimension": "sector/request and useful-bytes/sector counters",
            "impact": "Cannot claim global-memory coalescing quality.",
            "required_artifact": "C500 equivalent of sector/request or byte-utilization counters.",
        },
        {
            "dimension": "complete occupancy limiter decomposition",
            "impact": "streg and block-limit causes cannot be separated from current mtreg/shared-memory fields.",
            "required_artifact": "Compiler/assembly resource report or richer mcTracer occupancy fields.",
        },
        {
            "dimension": "exact GVM/BSM buffer occupancy over time",
            "impact": "GVM 600-cycle peak remains a pressure proxy, not precise buffer occupancy.",
            "required_artifact": "CycleTrace Web UI derived views or richer trace export.",
        },
    ])

    return {
        "summary": {
            "reported_metric_groups": len(report_metric_groups),
            "parsed_not_promoted_groups": len(parsed_not_promoted),
            "unavailable_dimensions": len(unavailable),
        },
        "reported_metric_groups": report_metric_groups,
        "parsed_not_promoted": parsed_not_promoted,
        "unavailable_dimensions": unavailable,
    }
