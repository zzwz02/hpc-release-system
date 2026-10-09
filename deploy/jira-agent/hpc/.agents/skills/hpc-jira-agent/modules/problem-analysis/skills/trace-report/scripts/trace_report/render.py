"""Markdown rendering for trace-report metrics."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .constants import KEY_METRICS, NA, cycle_trace_filename
from .utils import (
    as_number,
    expects_tensor_path,
    fmt,
    fmt_float,
    fmt_pct,
    get_path,
    is_metric_available,
    min_avg_max,
    numeric_values,
    parse_number,
    pct,
    ratio,
)

def dpc_balance_summary(profiler: dict[str, Any]) -> str:
    values = numeric_values(profiler.get("dpc_compute_cycles", {}))
    if not values:
        return NA
    lo, avg, hi = min_avg_max(values)
    return (
        f"min/avg/max={fmt(lo)}/{fmt(avg)}/{fmt(hi)} cycles, "
        f"imbalance={profiler.get('dpc_compute_imbalance_pct', 0):.2f}%"
    )

def high_dnoc_share(histogram: dict[str, Any]) -> tuple[float, float, float]:
    total = sum(parse_number(v) for v in histogram.values())
    high = sum(
        parse_number(v)
        for k, v in histogram.items()
        if "512" in str(k) or "1k" in str(k) or "2k" in str(k)
    )
    return total, high, pct(high, total)

def vl1_partition_summary(profiler: dict[str, Any]) -> str:
    summary = profiler.get("vl1_partition_stall_summary", {})
    if summary:
        return (
            f"min/avg/max={fmt(summary.get('min'))}/{fmt(summary.get('avg'))}/{fmt(summary.get('max'))} cycles, "
            f"spread={fmt_pct(summary.get('spread_pct'))}"
        )
    values = numeric_values(profiler.get("vl1_partition_stalls", {}))
    if not values:
        return NA
    lo, avg, hi = min_avg_max(values)
    spread = pct(hi - lo, avg)
    return f"min/avg/max={fmt(lo)}/{fmt(avg)}/{fmt(hi)} cycles, spread={spread:.2f}%"

def dnoc_histogram_rows(profiler: dict[str, Any]) -> list[str]:
    histogram = profiler.get("dnoc_latency_histogram", {})
    shares = profiler.get("dnoc_latency_histogram_pct", {})
    if not histogram:
        return ["- DNOC latency histogram is unavailable."]
    return [
        f"- {bucket}: {fmt(value)} samples ({shares.get(bucket, 0):.2f}%)"
        for bucket, value in histogram.items()
    ]

def compute_cycles_summary(profiler: dict[str, Any]) -> str:
    summary = profiler.get("compute_instruction_cycles_summary", {})
    if not summary:
        return NA
    return (
        f"total={fmt(summary.get('total'))} cycles, "
        f"MTE={fmt(summary.get('mte_cycles'))} ({fmt_pct(summary.get('mte_cycles_pct'))}), "
        f"MMA={fmt(summary.get('mma_cycles'))} ({fmt_pct(summary.get('mma_cycles_pct'))})"
    )

def isu_stall_summary(profiler: dict[str, Any]) -> str:
    summary = profiler.get("isu_stall_summary", {})
    if not summary:
        return NA
    return (
        f"total={fmt(summary.get('total'))} cycles, "
        f"top={summary.get('top', '')} {fmt(summary.get('top_cycles'))} cycles "
        f"({fmt_pct(summary.get('top_pct'))})"
    )

def stall_share_rows(profiler: dict[str, Any]) -> list[str]:
    stalls = profiler.get("isu_stall_cycles", {})
    shares = profiler.get("isu_stall_summary", {}).get("shares_pct", {})
    if not stalls:
        return ["- ISU stall cycle layout is unavailable."]
    return [
        f"- {name}: {fmt(value)} cycles ({shares.get(name, 0):.2f}%)"
        for name, value in stalls.items()
    ]

def roofline_peak_summary(roof: dict[str, Any]) -> str:
    return (
        f"TRANS={fmt_float(roof.get('peak_trans_tflops'))}, "
        f"FMA={fmt_float(roof.get('peak_fma_tflops'))}, "
        f"MMA-FP16={fmt_float(roof.get('peak_mma_fp16_tflops'))}, "
        f"INT8={fmt_float(roof.get('peak_int8_tflops'))} TFLOP/s"
    )

def roofline_usage_summary(roof: dict[str, Any]) -> str:
    return (
        f"HBM={fmt_pct(roof.get('hbm_usage_pct'))}, "
        f"VL1={fmt_pct(roof.get('vl1_usage_pct'))}, "
        f"L2C={fmt_pct(roof.get('l2c_usage_pct'))}"
    )

def roofline_bound_summary(roof: dict[str, Any]) -> str:
    return (
        f"{roof.get('roofline_bound', NA)} "
        f"(confidence={roof.get('roofline_bound_confidence', NA)}, "
        f"region={roof.get('hbm_region', NA)}, "
        f"achieved/roof={fmt_pct(roof.get('hbm_achieved_vs_roof_pct'))})"
    )

def roofline_gap_summary(roof: dict[str, Any]) -> str:
    return (
        f"compute_gap={fmt_pct(roof.get('compute_gap_pct'))}, "
        f"hbm_bandwidth_gap={fmt_pct(roof.get('hbm_bandwidth_gap_pct'))}, "
        f"selected_roof_gap={fmt_pct(roof.get('selected_roof_gap_pct'))}, "
        f"latency_or_efficiency_gap={fmt_pct(roof.get('latency_or_efficiency_gap_pct'))}, "
        f"hint={roof.get('gap_hint', NA)}"
    )

def memory_flow_summary(flow: dict[str, Any]) -> list[str]:
    if not flow:
        return ["- Memory data flow chart is unavailable."]
    aliases = (
        ("global", ("global_kernel_rd", "global_kernel_wr")),
        ("shared", ("shared_kernel_rd", "shared_kernel_wr")),
        ("constant", ("const_kernel_rd", "const_kernel_wr")),
        ("private", ("private_kernel_rd", "private_kernel_wr")),
        ("generic", ("generic_kernel_rd", "generic_kernel_wr")),
        ("generic_vl1c", ("Ggeneric_vl1c_rd", "Ggeneric_vl1c_wr")),
        ("const_sl1c", ("const_sl1c_rd", "const_sl1c_wr")),
        ("vl1c_l2c", ("vl1c_l2c_rd", "vl1c_l2c_wr")),
        ("l2c_global", ("l2c_Gmemory_rd", "l2c_Gmemory_wr")),
    )
    lines = []
    for label, (rd_key, wr_key) in aliases:
        rd_present = rd_key in flow
        wr_present = wr_key in flow
        rd = flow.get(rd_key, 0)
        wr = flow.get(wr_key, 0)
        if rd_present or wr_present or parse_number(rd) or parse_number(wr):
            lines.append(f"- {label}: read={fmt(parse_number(rd))}, write={fmt(parse_number(wr))}")
    return lines or ["- Memory data flow chart has no recognized read/write entries."]

def render_run_dir(artifact_dir: str) -> str:
    if not artifact_dir:
        return ""
    path = Path(artifact_dir)
    if path.name == "artifacts" and path.parent != path:
        return str(path.parent)
    if path.parent != path:
        return str(path.parent)
    return str(path)

def dpc_id_for_rerun(metrics: dict[str, Any]) -> str:
    explicit = metrics.get("cycle_dpc_id")
    if explicit:
        return str(explicit)
    primary = metrics.get("cycle_dpc_primary")
    if primary is not None:
        return str(primary)
    filename = str(metrics.get("cycle_trace_file", ""))
    prefix = "c-trace_output_dpc_"
    suffix = ".json"
    if filename.startswith(prefix) and filename.endswith(suffix):
        return filename[len(prefix):-len(suffix)]
    return "0"

def source_latency_available(metrics: dict[str, Any]) -> bool:
    return bool(metrics.get("source_latency", {}).get("available", False))

def source_latency_coverage_summary(source_latency: dict[str, Any]) -> str:
    coverage = source_latency.get("coverage", {})
    return (
        f"mapped cycles={fmt(coverage.get('modeled_source_mapped_cycles'))}/"
        f"{fmt(coverage.get('raw_modeled_cycles'))} "
        f"({fmt_pct(coverage.get('modeled_source_mapped_cycle_share_pct'))}), "
        f"mapped events={fmt(coverage.get('modeled_source_mapped_events'))}/"
        f"{fmt(coverage.get('raw_modeled_events'))} "
        f"({fmt_pct(coverage.get('modeled_source_mapped_event_share_pct'))})"
    )

def source_latency_actionability(source_latency: dict[str, Any]) -> str:
    coverage = source_latency.get("coverage", {})
    mapped_cycle_share = coverage.get("modeled_source_mapped_cycle_share_pct")
    try:
        mapped_cycle_share_float = float(mapped_cycle_share)
    except (TypeError, ValueError):
        mapped_cycle_share_float = 0.0
    top = source_latency_top_line(source_latency)
    if not top:
        return (
            "Actionability: no source lines were attributed; keep source-latency as a "
            "coverage boundary and do not choose a source-line edit target from it."
        )
    if mapped_cycle_share_float >= 70.0:
        confidence = "high"
        use = "can guide the first source-level edit target"
    elif mapped_cycle_share_float >= 30.0:
        confidence = "medium"
        use = "can rank candidate source lines, but aggregate metrics still choose the diagnosis direction"
    else:
        confidence = "low"
        use = "is only a localization hint because most modeled cycles did not map to user source"
    return (
        f"Actionability: {confidence} source-line localization; top line "
        f"{source_latency_top_line_summary(source_latency)} {use}."
    )

def source_line_location(row: dict[str, Any]) -> str:
    path = row.get("source_path", "")
    line = row.get("source_line", "")
    return f"{path}:{line}" if path or line != "" else NA

def source_latency_top_line(source_latency: dict[str, Any]) -> dict[str, Any] | None:
    lines = source_latency.get("source_lines", [])
    if lines and isinstance(lines[0], dict):
        return lines[0]
    return None

def source_latency_top_line_summary(source_latency: dict[str, Any]) -> str:
    top = source_latency_top_line(source_latency)
    if not top:
        return NA
    text = str(top.get("source_text", "")).strip()
    if len(text) > 80:
        text = text[:77] + "..."
    return (
        f"`{source_line_location(top)}` "
        f"{fmt_pct(top.get('pct_total_cycles'))} total / "
        f"{fmt_pct(top.get('pct_mapped_cycles'))} mapped"
        + (f" (`{text}`)" if text else "")
    )

def source_latency_artifact_lines(source_latency: dict[str, Any], tag: str) -> list[str]:
    if not source_latency.get("available"):
        return []
    return [
        f"- Source-latency report: `analysis/source_latency_{tag}.md`",
        f"- Source-latency CSV: `analysis/source_latency_{tag}.csv`",
        f"- Source-latency JSON: `analysis/source_latency_{tag}.json`",
    ]

def source_latency_rows(source_latency: dict[str, Any], limit: int = 5) -> list[str]:
    rows = [
        "| Rank | Source line | % total | % mapped | Modeled cycles | Events | Top instruction | Source text |",
        "|---:|---|---:|---:|---:|---:|---|---|",
    ]
    lines = [row for row in source_latency.get("source_lines", []) if isinstance(row, dict)]
    if not lines:
        return ["- Source-latency is available, but no source lines were attributed."]
    for row in lines[:limit]:
        text = str(row.get("source_text", "")).replace("|", "\\|")
        instr = str(row.get("top_modeled_instruction", "")).replace("|", "\\|")
        if len(instr) > 96:
            instr = instr[:93] + "..."
        rows.append(
            f"| {row.get('rank', '')} | `{source_line_location(row)}` | "
            f"{fmt_pct(row.get('pct_total_cycles'))} | {fmt_pct(row.get('pct_mapped_cycles'))} | "
            f"{fmt(row.get('total_cycles'))} | {fmt(row.get('events'))} | "
            f"`{instr}` | `{text}` |"
        )
    return rows

def source_latency_boundary_lines(source_latency: dict[str, Any]) -> list[str]:
    if not source_latency.get("available"):
        return [
            "- Default artifacts do not provide source-line attribution; the report cannot name source-line edit targets without source-latency.",
            "- Current artifacts do not provide full per-PC stall attribution or NCU-style per-warp stall reasons.",
            "- Current artifacts provide limited CycleTrace event-timeline proxies and aggregate balance only, not full PM-sampling/per-AP utilization timelines.",
        ]
    coverage = source_latency.get("coverage", {})
    unmapped = coverage.get("modeled_unmapped_reasons", {})
    unmodeled = coverage.get("unmodeled_no_fixed_cycle_events", {})
    lines = [
        "- Source-line attribution is available as CycleTrace/asm modeled attribution with top modeled instruction context; it is not full wall-clock stall attribution.",
        f"- Source-latency coverage: {source_latency_coverage_summary(source_latency)}.",
        f"- {source_latency_actionability(source_latency)}",
        "- Current artifacts provide limited CycleTrace event-timeline proxies and aggregate balance only, not full PM-sampling/per-AP utilization timelines.",
    ]
    if unmapped:
        details = ", ".join(f"{key}={fmt(value)}" for key, value in unmapped.items())
        lines.append(f"- Unmapped modeled event reasons: {details}.")
    if unmodeled:
        details = ", ".join(f"{key}={fmt(value)}" for key, value in unmodeled.items())
        lines.append(f"- Events without modeled cycle cost: {details}.")
    return lines

def metric_coverage_rows(coverage: dict[str, Any]) -> list[str]:
    rows = [
        "| Category | Count | Meaning |",
        "|---|---:|---|",
    ]
    summary = coverage.get("summary", {})
    rows.extend([
        f"| Reported metric groups | {summary.get('reported_metric_groups', 0)} | Metric groups directly shown in `REPORT_<tag>.md` |",
        f"| Parsed but not promoted groups | {summary.get('parsed_not_promoted_groups', 0)} | Available in `metrics_all_<tag>.json`, omitted from the main prose unless diagnostic |",
        f"| Unavailable dimensions | {summary.get('unavailable_dimensions', 0)} | Analyses that current artifacts cannot support |",
    ])
    return rows

def hardware_instruction_rows(cycle: dict[str, Any], profiler: dict[str, Any]) -> list[str]:
    mapping = (
        ("MTE", "MTE", "mte", "mte_pct"),
        ("STE", "STE", "ste", "ste_pct"),
        ("MMA", "MMA", "mma", "mma_pct"),
        ("BSM", "BSM", "bsm", "bsm_pct"),
        ("GLOBAL/GVM", "GVM", "gvm_total", "gvm_pct"),
        ("LDU", "LDU", "ldu", None),
    )
    hw_counts = profiler.get("hardware_instruction_counts", {})
    hw_total = as_number(profiler.get("hardware_instruction_total", 0))
    rows = [
        "| Class | CycleTrace count/share | mcProfiler count/share | Delta |",
        "|---|---:|---:|---:|",
    ]
    for label, hw_label, count_key, pct_key in mapping:
        cycle_count = cycle.get(count_key, 0)
        cycle_pct = cycle.get(pct_key, pct(cycle_count, cycle.get("total_instructions", 0)))
        hw_count = parse_number(hw_counts.get(hw_label, 0))
        hw_pct = pct(hw_count, hw_total)
        rows.append(
            f"| {label} | {fmt(cycle_count)} / {cycle_pct:.2f}% | "
            f"{fmt(hw_count)} / {hw_pct:.2f}% | {hw_pct - cycle_pct:+.2f} pp |"
        )
    return rows

def cycle_category_rows(cycle: dict[str, Any]) -> list[str]:
    category_counts = cycle.get("category_counts", {})
    category_pcts = cycle.get("category_pcts", {})
    category_meaning = {
        "MTE": "Vector/Matrix Tensor Extension compute category",
        "STE": "Scalar/control hardware category; includes STE-name, S_NOP, and Branch events",
        "MMA": "Matrix-multiply hardware category",
        "BSM": "Block shared-memory hardware category",
        "GLOBAL": "Global vector-memory hardware category; GVM Load/Store subevents",
        "ARRIVE": "Synchronization/arrival category",
        "LDU": "Load data unit category",
    }
    rows = [
        "| CycleTrace cat | Count | Share | Hardware meaning |",
        "|---|---:|---:|---|",
    ]
    for cat in ("MTE", "STE", "MMA", "BSM", "GLOBAL", "ARRIVE", "LDU"):
        rows.append(
            f"| {cat} | {fmt(category_counts.get(cat, 0))} | "
            f"{category_pcts.get(cat, 0):.2f}% | {category_meaning[cat]} |"
        )
    return rows

def render_digest(metrics: dict[str, Any]) -> str:
    tag = metrics["tag"]
    kernel = metrics["kernel"]
    launch = metrics["launch"]
    cycle = metrics["cycle"]
    profiler = metrics["profiler"]
    bound = metrics["bound"]
    scope = metrics.get("collection_scope", {})

    lines = [
        f"# C500 Trace Profile Digest: {tag}",
        "",
        "## Kernel",
        "",
        f"- Name: `{kernel.get('name', '')}`",
        f"- Grid: `{kernel.get('grid', [])}`",
        f"- Block: `{kernel.get('block', [])}`",
        f"- Span: `{fmt(kernel.get('span_cycles', 0))}` cycles",
        f"- Performance scope: {collection_scope_digest(scope)}",
        "",
        "## Bound Classification",
        "",
        f"- Method: `{bound.get('method', 'c500-heuristic')}`",
        f"- Heuristic mode: `{bound.get('mode', '')}`",
        f"- Type: `{bound.get('type', '')}`",
        f"- Primary: `{bound.get('primary', '')}`",
        f"- Signals: `{', '.join(bound.get('signals', [])) or 'none'}`",
        "",
        "Evidence:",
        "",
        f"- MMA share / AP MMA duty: {cycle.get('mma_pct', 0):.2f}% / {fmt_pct(profiler.get('ap_mma_duty_pct'))}",
        f"- MTE share / AP MTE duty: {cycle.get('mte_pct', 0):.2f}% / {fmt_pct(profiler.get('ap_mte_duty_pct'))}",
        f"- GVM share / VLS duty / L2C duty: {cycle.get('gvm_pct', 0):.2f}% / {fmt_pct(profiler.get('vls_duty_pct'))} / {fmt_pct(profiler.get('l2c_duty_pct'))}",
        f"- VL1/L2C hit rate: {fmt_pct(profiler.get('vl1_hit_rate_pct'))} / {fmt_pct(profiler.get('l2c_hit_rate_pct'))}",
        f"- Shared-memory efficiency / conflict cycles: {fmt_pct(profiler.get('shared_memory_efficiency_pct'))} / {fmt(profiler.get('avg_conflict_cycles_per_inst'))}",
        f"- Effective occupancy bound: {launch.get('effective_occupancy_pct', 0):.2f}%",
        f"- WIF P25/P50/P75/max: {cycle.get('wif_p25', 0):.2f} / {cycle.get('wif_p50', 0):.2f} / {cycle.get('wif_p75', 0):.2f} / {cycle.get('wif_max', 0)}",
        f"- DPC balance: {dpc_balance_summary(profiler)}",
        f"- Roofline bound: {roofline_bound_summary(profiler.get('roofline', {}))}",
        f"- Roofline gap: {roofline_gap_summary(profiler.get('roofline', {}))}",
        "",
        "Rationale:",
        "",
        *[f"- {line}" for line in bound.get("rationale", [])],
        "",
        "## Key Metrics",
        "",
        "| Metric | Value | Unit | Better |",
        "|---|---:|---|---|",
    ]
    for path, label, unit, better in KEY_METRICS:
        lines.append(f"| {label} | {fmt(get_path(metrics, path))} | {unit} | {better} |")

    lines.extend([
        "",
        "## Instruction Distribution",
        "",
        "Primary classification uses CycleTrace `cat`, which identifies the C500 hardware category.",
        "",
        *cycle_category_rows(cycle),
        "",
        f"- STE subevents by `name`: STE={cycle.get('raw_name_counts', {}).get('STE', 0):,}, S_NOP={cycle.get('s_nop', 0):,}, Branch={cycle.get('branch', 0):,}.",
        f"- GLOBAL subevents by `name`: GVM Load={cycle.get('gvm_load', 0):,}, GVM Store={cycle.get('gvm_store', 0):,}.",
        f"- ARRIVE subevents by `name`: Synchronization={cycle.get('raw_name_counts', {}).get('Synchronization', 0):,}.",
        "",
        "## mcProfiler Hardware Evidence",
        "",
        f"- AP MTE/STE/MMA duty: {fmt_pct(profiler.get('ap_mte_duty_pct'))} / {fmt_pct(profiler.get('ap_ste_duty_pct'))} / {fmt_pct(profiler.get('ap_mma_duty_pct'))}",
        f"- Real IPC: {fmt_float(profiler.get('real_ipc'))}",
        f"- VL1/L2C hit rate: {fmt_pct(profiler.get('vl1_hit_rate_pct'))} / {fmt_pct(profiler.get('l2c_hit_rate_pct'))}",
        f"- Shared memory efficiency: {fmt_pct(profiler.get('shared_memory_efficiency_pct'))}",
        f"- Avg conflict cycles/inst: {fmt(profiler.get('avg_conflict_cycles_per_inst'))}",
        f"- DPC compute balance: {dpc_balance_summary(profiler)}",
        f"- Achieved/dispatched waves: {fmt(profiler.get('achieved_waves'))} / {fmt(profiler.get('dispatched_waves'))}; workgroups={fmt(profiler.get('workgroups'))}; avg wave life={fmt(profiler.get('average_wave_life_cycles'))} cycles",
        f"- Empirical roofline: {fmt_float(profiler.get('achieved_flops_tflops'))} TFLOPS, {fmt_float(profiler.get('achieved_bandwidth_gbs'))} GB/s",
        f"- Roofline intensity: DRAM={fmt_float(profiler.get('achieved_intensity_flop_per_byte'))}, VL1={fmt_float(profiler.get('roofline', {}).get('case_VL1_I', NA))}, L2C={fmt_float(profiler.get('roofline', {}).get('case_L2C_I', NA))} FLOP/Byte",
        f"- Roofline placement/bound: {roofline_bound_summary(profiler.get('roofline', {}))}",
        f"- Roofline gap: {roofline_gap_summary(profiler.get('roofline', {}))}",
        f"- Roofline usage: {roofline_usage_summary(profiler.get('roofline', {}))}",
        "",
        "## Diagnosis",
        "",
    ])
    for item in metrics["diagnosis"]:
        lines.extend([
            f"### {item['severity'].upper()}: {item['title']}",
            "",
            f"- Evidence: {item['evidence']}",
            f"- Next: {item['next']}",
            "",
        ])

    lines.extend([
        "## Scope Notes",
        "",
        "- CycleTrace `dur=4` is used only as an issue-slot marker, not real instruction latency.",
        "- GVM 600-cycle peak is a pressure proxy, not exact buffer occupancy.",
        "- WIF is reported as raw trace distribution; converting it to waves/AP requires confirming counter scope.",
        "- Effective occupancy uses mcTracer mtreg/shared-memory fields only; streg requires compiler/assembly evidence.",
        "",
    ])
    return "\n".join(lines)

def collection_scope_digest(scope: dict[str, Any]) -> str:
    if not scope.get("cycle_trace_kernel_filter_enabled"):
        return "CycleTrace command-level collection"
    return (
        "CycleTrace single-kernel filter enabled; target="
        f"`{scope.get('cycle_trace_kernel_name', '')}`, "
        f"repeat=`{scope.get('cycle_trace_kernel_repeat', '')}`, "
        f"sample_mode=`{scope.get('cycle_trace_sample_mode', '')}`, "
        f"target_launches=`{scope.get('target_kernel_occurrence_count', 0)}`"
    )

def collection_scope_setup_lines(scope: dict[str, Any]) -> list[str]:
    device = str(scope.get("selected_maca_visible_devices", "") or "")
    requested_device = str(scope.get("requested_maca_visible_devices", "") or device)
    auto_selected = bool(scope.get("auto_selected_device", False))
    device_line = (
        f"- MACA_VISIBLE_DEVICES: requested `{requested_device}`, selected `{device}` by auto device selection"
        if auto_selected
        else f"- MACA_VISIBLE_DEVICES: `{device}`"
        if device
        else ""
    )
    if not scope.get("cycle_trace_kernel_filter_enabled"):
        lines = [
            "- Performance scope: CycleTrace command-level collection",
            f"- mcProfiler scope: `{scope.get('mcprofiler_scope', 'command-level')}`",
        ]
        if device_line:
            lines.insert(1, device_line)
        return lines
    lines = [
        "- Performance scope: CycleTrace single-kernel filter enabled",
        f"- CycleTrace target kernel: `{scope.get('cycle_trace_kernel_name', '')}`",
        f"- CycleTrace kernel repeat: `{scope.get('cycle_trace_kernel_repeat', '')}`",
        f"- CycleTrace sample mode: `{scope.get('cycle_trace_sample_mode', '')}`",
        f"- Target kernel launch count in trace command: `{scope.get('target_kernel_occurrence_count', 0)}`",
        f"- Target invocation scope: `{scope.get('target_invocation_scope', 'unknown')}`",
        f"- Target invocation scope note: {scope.get('target_invocation_scope_note', '')}",
        f"- mcProfiler scope: `{scope.get('mcprofiler_scope', 'command-level')}`",
        f"- mcProfiler occurrence count: `{scope.get('mcprofiler_occurrence_count', 0)}`",
        f"- Scope note: {scope.get('cycle_trace_scope_note', '')}",
        f"- mcProfiler scope note: {scope.get('mcprofiler_scope_note', '')}",
    ]
    if device_line:
        lines.insert(1, device_line)
    return lines

def confidence(metrics: dict[str, Any]) -> str:
    bound = metrics.get("bound", {})
    evidence = bound.get("evidence", {})
    support = 0
    has_profiler = bool(metrics.get("profiler", {}).get("available", False))
    if evidence.get("cycle_mte_pct", 0) > 30 or (
        has_profiler
        and is_metric_available(evidence.get("ap_mte_duty_pct"))
        and as_number(evidence.get("ap_mte_duty_pct")) > 15
    ):
        support += 1
    if (
        has_profiler
        and is_metric_available(evidence.get("shared_memory_efficiency_pct"))
        and as_number(evidence.get("shared_memory_efficiency_pct")) < 80
    ):
        support += 1
    if (
        has_profiler
        and is_metric_available(evidence.get("avg_conflict_cycles_per_inst"))
        and as_number(evidence.get("avg_conflict_cycles_per_inst")) > 0.5
    ):
        support += 1
    if 0 < evidence.get("effective_occupancy_pct", 0) < 25:
        support += 1
    if (
        has_profiler
        and is_metric_available(evidence.get("l2c_hit_rate_pct"))
        and as_number(evidence.get("l2c_hit_rate_pct")) > 90
        and evidence.get("cycle_gvm_pct", 0) < 15
    ):
        support += 1
    if support >= 3:
        return "High"
    if support >= 2:
        return "Medium"
    return "Low"

def primary_signal(metrics: dict[str, Any]) -> str:
    cycle = metrics["cycle"]
    profiler = metrics["profiler"]
    launch = metrics["launch"]
    signals = [
        f"MTE share={cycle.get('mte_pct', 0):.2f}% / AP MTE duty={fmt_pct(profiler.get('ap_mte_duty_pct'))}",
        f"MMA share={cycle.get('mma_pct', 0):.2f}% / AP MMA duty={fmt_pct(profiler.get('ap_mma_duty_pct'))}",
        f"SMEM efficiency={fmt_pct(profiler.get('shared_memory_efficiency_pct'))}, conflict cycles/inst={fmt(profiler.get('avg_conflict_cycles_per_inst'))}",
        f"effective occupancy={launch.get('effective_occupancy_pct', 0):.2f}%",
    ]
    return "; ".join(signals)

def one_line_read(metrics: dict[str, Any]) -> str:
    bound = metrics["bound"]
    cycle = metrics["cycle"]
    profiler = metrics["profiler"]
    primary = str(bound.get("primary", ""))
    signals = bound.get("signals", [])
    signal_text = ", ".join(signals) if signals else "none"
    if not profiler.get("available", False):
        if primary == "unclear":
            return (
                "no dominant bound is identified from CycleTrace and mcTracer evidence; "
                "mcProfiler-dependent duty, cache, bank-conflict, IPC, and Roofline evidence is N/A."
            )
        return (
            f"the primary bound signal is `{primary}` from CycleTrace and mcTracer evidence; "
            "mcProfiler-dependent duty, cache, bank-conflict, IPC, and Roofline evidence is N/A."
        )
    if primary == "mixed":
        return (
            f"no single coarse bound dominates; triggered coarse signals are {signal_text}."
        )
    if primary == "unclear":
        return (
            "no coarse bound threshold fired strongly enough to classify this kernel as compute, memory, latency, or occupancy bound."
        )
    if primary == "memory":
        return (
            f"the primary bound signal is `memory` because GVM/VLS/L2C/cache thresholds fired "
            f"(GVM share={cycle.get('gvm_pct', 0):.2f}%, L2C hit={fmt_pct(profiler.get('l2c_hit_rate_pct'))})."
        )
    if primary == "latency":
        return (
            f"the primary bound signal is `latency` because NOP/IPC evidence indicates pipeline bubbles "
            f"(NOP share={cycle.get('nop_pct', 0):.2f}%, real IPC={fmt_float(profiler.get('real_ipc'))})."
        )
    if primary == "occupancy":
        return (
            f"the primary bound signal is `occupancy` because launch occupancy is low "
            f"(effective occupancy={metrics['launch'].get('effective_occupancy_pct', 0):.2f}%)."
        )
    return (
        f"the primary bound signal is `{primary}` because compute-side evidence dominates "
        f"(MTE share={cycle.get('mte_pct', 0):.2f}%, AP MTE duty={fmt_pct(profiler.get('ap_mte_duty_pct'))}, "
        f"AP MMA duty={fmt_pct(profiler.get('ap_mma_duty_pct'))})."
    )

def mechanism_for_primary(metrics: dict[str, Any], dnoc_high_pct: float, wif_ratio: float) -> str:
    bound = metrics["bound"]
    cycle = metrics["cycle"]
    profiler = metrics["profiler"]
    launch = metrics["launch"]
    source_latency = metrics.get("source_latency", {})
    primary = str(bound.get("primary", ""))
    signals = bound.get("signals", [])
    source_context = ""
    if source_latency.get("available"):
        source_context = f" Source-latency localizes the top modeled-cycle line to {source_latency_top_line_summary(source_latency)}."
    if primary == "mixed":
        signal_text = ", ".join(signals) if signals else "multiple signals"
        return (
            f"no single coarse bound dominates; triggered signals are {signal_text}. "
            f"Use the ranked diagnosis list before choosing one edit path.{source_context}"
        )
    if primary == "unclear":
        return (
            "current aggregate evidence does not identify a dominant compute, memory, latency, or occupancy bound; "
            f"treat this as an evidence boundary until more discriminating artifacts or source context are available.{source_context}"
        )
    if primary == "memory":
        return (
            f"memory/cache evidence is the current primary signal: GVM share={cycle.get('gvm_pct', 0):.2f}%, "
            f"VLS duty={fmt_pct(profiler.get('vls_duty_pct'))}, L2C duty={fmt_pct(profiler.get('l2c_duty_pct'))}, "
            f"L2C hit={fmt_pct(profiler.get('l2c_hit_rate_pct'))}, and DNOC >512-cycle share={dnoc_high_pct:.2f}%.{source_context}"
        )
    if primary == "latency":
        return (
            f"latency or issue-bubble evidence is the current primary signal: NOP share={cycle.get('nop_pct', 0):.2f}%, "
            f"real IPC={fmt_float(profiler.get('real_ipc'))}, and top ISU stall="
            f"{profiler.get('isu_stall_summary', {}).get('top', NA)} / {fmt_pct(profiler.get('isu_stall_summary', {}).get('top_pct'))}.{source_context}"
        )
    if primary == "occupancy":
        return (
            f"launch/parallelism evidence is the current primary signal: effective occupancy="
            f"{launch.get('effective_occupancy_pct', 0):.2f}%, achieved/dispatched waves="
            f"{fmt(profiler.get('achieved_waves'))}/{fmt(profiler.get('dispatched_waves'))}, and WIF P75/P25={wif_ratio:.2f}x.{source_context}"
        )
    return (
        f"compute-side evidence is the current primary signal: MTE share={cycle.get('mte_pct', 0):.2f}%, "
        f"MMA share={cycle.get('mma_pct', 0):.2f}%, AP MTE duty={fmt_pct(profiler.get('ap_mte_duty_pct'))}, "
        f"AP MMA duty={fmt_pct(profiler.get('ap_mma_duty_pct'))}, and MMA compute-cycle share="
        f"{fmt_pct(profiler.get('compute_instruction_cycles_summary', {}).get('mma_cycles_pct'))}.{source_context}"
    )

def weaker_hypotheses(metrics: dict[str, Any], dnoc_high_pct: float, wif_ratio: float) -> str:
    cycle = metrics["cycle"]
    profiler = metrics["profiler"]
    launch = metrics["launch"]
    return (
        f"counter-evidence for alternate bounds is GVM share={cycle.get('gvm_pct', 0):.2f}%, "
        f"L2C hit={fmt_pct(profiler.get('l2c_hit_rate_pct'))}, DNOC >512-cycle share={dnoc_high_pct:.2f}%, "
        f"DPC imbalance={fmt_pct(profiler.get('dpc_compute_imbalance_pct'))}, WIF P75/P25={wif_ratio:.2f}x, "
        f"and effective occupancy={launch.get('effective_occupancy_pct', 0):.2f}%."
    )

def next_edit_lines(metrics: dict[str, Any]) -> list[str]:
    bound = metrics["bound"]
    cycle = metrics["cycle"]
    profiler = metrics["profiler"]
    kernel = metrics["kernel"]
    primary = str(bound.get("primary", ""))
    source_latency = metrics.get("source_latency", {})
    top_source = source_latency_top_line(source_latency) if source_latency.get("available") else None
    if top_source:
        file_line = f"- File: `{source_line_location(top_source)}` from source-latency top modeled-cycle attribution."
        source_hint = (
            " Start from the source-latency top lines and confirm the source statement still maps "
            "to the intended kernel path after editing."
        )
    else:
        file_line = "- File: kernel source file if it can be identified outside the current artifacts; current artifacts do not contain source-line attribution, so the exact file must be supplied or confirmed before editing."
        source_hint = ""
    tensor_expected = expects_tensor_path(str(kernel.get("name", "")))
    if primary == "mixed":
        signals = ", ".join(bound.get("signals", [])) or "multiple coarse signals"
        return [
            file_line,
            f"- Change: no single coarse bound dominates ({signals}); choose the first high-confidence diagnosis that is actionable with source or assembly evidence, and avoid applying a second optimization until the same-shape rerun shows the first signal moved.{source_hint}",
            "- Validation: rerun `trace_profile_pipeline.py run` on the same shape and compare all triggered signal groups in `bound.signals`, the diagnosis list, and `REPORT_<tag>.md`.",
            "- Expected metric movement: the selected diagnosis metric should improve without regressing the other triggered coarse signals.",
        ]
    if primary == "unclear":
        return [
            file_line,
            f"- Change: do not apply a bound-specific kernel edit from the current aggregate artifacts alone; collect or inspect the missing discriminating evidence first.{source_hint}",
            "- Validation: rerun or extend profiling with the same shape and confirm whether a coarse signal becomes compute, memory, latency, occupancy, or mixed.",
            "- Expected metric movement: a follow-up report should either identify a concrete signal or preserve `unclear` with explicit data boundaries.",
        ]
    if primary == "memory":
        if tensor_expected:
            change = "reduce the dominant traffic source shown by Memory Hierarchy, for example by improving tile reuse, removing repeated global loads/stores, or staging reused data through WSM when conflict risk is controlled."
            expected = "GVM/VLS pressure and global bytes should decrease, while L2C hit rate or achieved bandwidth context should improve without worsening shared-memory conflict."
        else:
            change = "reduce global read/write traffic for this non-tensor kernel, for example by removing redundant loads/stores, improving contiguous/vectorized access, or fusing adjacent elementwise work when that path exists."
            expected = "global bytes, GVM/VLS pressure, and DNOC requests should decrease without introducing shared-memory traffic or occupancy regressions."
        return [
            file_line,
            f"- Change: {change}{source_hint}",
            "- Validation: rerun `trace_profile_pipeline.py run` on the same shape and compare `cycle.gvm_pct`, `profiler.vls_duty_pct`, `profiler.l2c_hit_rate_pct`, DNOC latency buckets, global memory bytes, and `REPORT_<tag>.md`.",
            f"- Expected metric movement: {expected}",
        ]
    if primary == "latency":
        return [
            file_line,
            f"- Change: shorten dependency chains and issue gaps by reordering load/compute, reducing unnecessary synchronization, or adding independent work between long-latency operations.{source_hint}",
            "- Validation: rerun `trace_profile_pipeline.py run` on the same shape and compare `cycle.nop_pct`, `profiler.real_ipc`, ISU stall summary, average wave life, and `REPORT_<tag>.md`.",
            "- Expected metric movement: NOP share and dominant ISU stall share should decrease, while real IPC should increase.",
        ]
    if primary == "occupancy":
        if tensor_expected:
            change = "reduce the limiting tensor-kernel resource after confirming whether registers, WSM tile footprint, or tile shape is the actual limiter."
        else:
            change = "adjust grid/block coverage for this non-tensor kernel first; reduce register or shared-memory footprint only if mcTracer/compiler evidence confirms a real resource limiter."
        return [
            file_line,
            f"- Change: {change}{source_hint}",
            "- Validation: rerun `trace_profile_pipeline.py run` on the same shape and compare effective occupancy, mtreg/shared-memory occupancy, achieved waves, WIF quartiles, DPC imbalance, and `REPORT_<tag>.md`.",
            "- Expected metric movement: effective occupancy and achieved waves should increase without creating new memory or bank-conflict pressure.",
        ]
    if (
        tensor_expected
        and cycle.get("mma_pct", 0) < 1
        and is_metric_available(profiler.get("ap_mma_duty_pct"))
        and as_number(profiler.get("ap_mma_duty_pct")) < 3
    ):
        change = "confirm whether the profiled tensor-capable inner loop is actually lowered to the expected MMA instruction path for the same tile shape, then adjust the kernel implementation only if source or assembly evidence confirms a mismatch."
        expected = "AP MMA duty and CycleTrace MMA share should increase; if shared-memory efficiency remains below 80-85%, handle WSM layout padding/skew as the next separate edit."
    else:
        change = "focus the compute path indicated by SOL and compute-cycle split, reducing scalar/vector overhead or improving the intended compute instruction mix."
        expected = "the intended compute duty and compute-cycle share should increase without regressing memory hierarchy or occupancy metrics."
    return [
        file_line,
        f"- Change: {change}{source_hint}",
        "- Validation: rerun `trace_profile_pipeline.py run` on the same shape and compare AP duty ratios, CycleTrace instruction shares, compute instruction cycle split, shared memory efficiency, and `REPORT_<tag>.md`.",
        f"- Expected metric movement: {expected}",
    ]

def render_report(metrics: dict[str, Any]) -> str:
    tag = metrics["tag"]
    kernel = metrics["kernel"]
    launch = metrics["launch"]
    cycle = metrics["cycle"]
    profiler = metrics["profiler"]
    bound = metrics["bound"]
    coverage = metrics.get("metric_coverage", {})
    source_latency = metrics.get("source_latency", {})
    collection_scope = metrics.get("collection_scope", {})
    mcprofiler_artifacts = metrics.get("mcprofiler_artifacts", {})
    artifact_dir = metrics.get("artifact_dir", "")
    run_dir = render_run_dir(artifact_dir)
    has_profiler = bool(profiler.get("available", False))
    mcprofiler_scope = str(collection_scope.get("mcprofiler_scope", "command-level"))
    skip_mcprofiler = bool(collection_scope.get("skip_mcprofiler", False)) or mcprofiler_scope == "skipped"
    has_multi_occurrence_profiler = mcprofiler_scope == "per-kernel-multiple-occurrences"
    dnoc_total, dnoc_high, dnoc_high_pct = high_dnoc_share(profiler.get("dnoc_latency_histogram", {}))
    wif_p25 = cycle.get("wif_p25", 0)
    wif_p75 = cycle.get("wif_p75", 0)
    wif_ratio = ratio(wif_p75, wif_p25)
    private_reads = parse_number(profiler.get("memory_data_flow", {}).get("private_kernel_rd", 0))
    private_writes = parse_number(profiler.get("memory_data_flow", {}).get("private_kernel_wr", 0))
    atomic_keys = [
        key for key in profiler.get("all_scalars", {})
        if "atomic" in key.lower()
    ]
    atomic_total = sum(parse_number(profiler.get("all_scalars", {}).get(key, 0)) for key in atomic_keys)
    roof = profiler.get("roofline", {})
    cycle_trace_files = metrics.get("cycle_trace_files", [])
    cycle_dpc_id = dpc_id_for_rerun(metrics)
    multi_dpc_note = (
        f"- CycleTrace DPC selection: archived `{', '.join(cycle_trace_files)}`; "
        f"analysis uses primary `{metrics.get('cycle_trace_file', cycle_trace_filename())}`."
        if len(cycle_trace_files) > 1
        else ""
    )
    if expects_tensor_path(str(kernel.get("name", ""))):
        mma_interpretation = "Tensor/MMA path underuse signal for tensor-like kernels"
    else:
        mma_interpretation = "MMA path is not expected for this kernel type"
    source_latency_setup = []
    if source_latency.get("available"):
        source_latency_setup = [
            f"- Source-latency source: `{source_latency.get('inputs', {}).get('source', '')}`",
            f"- Source-latency artifacts: `analysis/source_latency_{tag}.md`, `analysis/source_latency_{tag}.csv`, `analysis/source_latency_{tag}.json`",
            f"- Source-latency coverage: {source_latency_coverage_summary(source_latency)}",
        ]

    lines = [
        f"# `{kernel.get('name', '')}` Trace Profiling Report",
        "",
        f"**Tag:** `{tag}`",
        "**Target GPU / arch:** MetaX C500",
        "**Tools:** CycleTrace + mcTracer" + (
            " + mcProfiler"
            if has_profiler or has_multi_occurrence_profiler
            else " (mcProfiler skipped)"
            if skip_mcprofiler
            else " (mcProfiler missing)"
        ),
        f"**Run directory:** `{run_dir}`",
        f"**Artifact directory:** `{artifact_dir}`",
        "",
        "## 0. Profiling setup",
        "",
        f"- Kernel: `{kernel.get('name', '')}`",
        *collection_scope_setup_lines(collection_scope),
        (
            f"- Usecase mapping: `{collection_scope.get('usecase_id', '')}` -> "
            f"`{collection_scope.get('matched_occurrence_id', '')}`"
            if collection_scope.get("usecase_id") and collection_scope.get("matched_occurrence_id")
            else ""
        ),
        f"- Grid / block: `{kernel.get('grid', [])}` / `{kernel.get('block', [])}`",
        f"- CycleTrace JSON: `{artifact_dir}/{metrics.get('cycle_trace_file', cycle_trace_filename())}`",
        multi_dpc_note,
        f"- mcTracer JSON: `{artifact_dir}/tracer_out.json`",
        "- mcProfiler JSON: skipped by `SKIP_MCPROFILER=true`" if skip_mcprofiler else (
        f"- mcProfiler JSON: `{mcprofiler_artifacts.get('dumped') or f'{artifact_dir}/mcprofiler_report_dumped.json'}`" if has_profiler else (
            f"- mcProfiler per-kernel artifacts: `{artifact_dir}/mcprofiler_per_kernel/`"
            if has_multi_occurrence_profiler
            else "- mcProfiler JSON: not collected"
        )),
        (
            f"- mcProfiler artifact mode: `reference` from `{mcprofiler_artifacts.get('artifact_dir', '')}`"
            if has_profiler and mcprofiler_artifacts.get("mode") == "reference"
            else ""
        ),
        *source_latency_setup,
        "- Heuristic bound mode: `" + str(bound.get("mode", "")) + "`",
        "",
        "Runnable pipeline:",
        "",
        "```bash",
        "python3 scripts/trace_report_env.py --config \"$ACTIVE_CONFIG\" run -- \\",
        "  python3 .trace-report/scripts/trace_profile_pipeline.py run \\",
        "  --source <profile-artifacts-dir> \\",
        "  --run-dir <profile-artifacts-dir> \\",
        f"  --tag {tag} \\",
        f"  --cycle-dpc-id {cycle_dpc_id} \\",
        f"  --heuristic-bound-mode {bound.get('mode', 'coarse')}",
        "```",
        "",
        "## 1. Headline",
        "",
        f"- Bottleneck class: `{bound.get('type', '')}`",
        f"- Primary: `{bound.get('primary', '')}`",
        f"- Roofline bound: `{roof.get('roofline_bound', NA)}` ({roof.get('hbm_region', NA)}, confidence={roof.get('roofline_bound_confidence', NA)})",
        f"- Confidence: {confidence(metrics)}",
        f"- Dominant signal: {primary_signal(metrics)}",
        f"- One-line read: {one_line_read(metrics)}",
        "",
        "## 2. Evidence",
        "",
        "| Metric | Value | Source | Interpretation |",
        "|---|---:|---|---|",
        f"| Kernel span | {fmt(cycle.get('span_cycles', 0))} cycles | CycleTrace wave lifecycle | End-to-end trace span, lower is better |",
        f"| CycleTrace MTE share | {cycle.get('mte_pct', 0):.2f}% | CycleTrace instruction events | Vector compute path dominates instruction mix |",
        f"| CycleTrace MMA share | {cycle.get('mma_pct', 0):.2f}% | CycleTrace instruction events | {mma_interpretation} |",
        f"| AP MTE duty | {fmt_pct(profiler.get('ap_mte_duty_pct'))} | mcProfiler SOL | Hardware vector pipe duty |",
        f"| AP MMA duty | {fmt_pct(profiler.get('ap_mma_duty_pct'))} | mcProfiler SOL | Tensor pipe utilization |",
        f"| Shared memory efficiency | {fmt_pct(profiler.get('shared_memory_efficiency_pct'))} | mcProfiler WSM | Bank conflict signal when below 80-85% |",
        f"| Avg conflict cycles/inst | {fmt(profiler.get('avg_conflict_cycles_per_inst'))} | mcProfiler WSM | Conflict penalty per smem instruction |",
        f"| Effective occupancy bound | {launch.get('effective_occupancy_pct', 0):.2f}% | mcTracer | Lower of mtreg and shared-memory occupancy fields |",
        f"| L2C hit rate | {fmt_pct(profiler.get('l2c_hit_rate_pct'))} | mcProfiler memory hierarchy | High value weakens DRAM bandwidth hypothesis |",
        f"| WIF P75/P25 | {wif_ratio:.2f}x | CycleTrace wave lifecycle | Aggregate tail-effect proxy; >2x suggests imbalance |",
        f"| DPC compute imbalance | {fmt_pct(profiler.get('dpc_compute_imbalance_pct'))} | mcProfiler DPC cycles | Aggregate compute-balance signal |",
        f"| DNOC >512-cycle share | {dnoc_high_pct:.2f}% | mcProfiler DNOC histogram | DRAM latency-tail check |",
        f"| Roofline HBM usage | {fmt_pct(roof.get('hbm_usage_pct'))} | mcProfiler RoofLine | Achieved bandwidth divided by HBM peak from the RoofLine chart |",
        f"| Roofline bound | {roof.get('roofline_bound', NA)} / {fmt_pct(roof.get('hbm_achieved_vs_roof_pct'))} | Derived from Roofline formula | Pure Roofline placement; separate from C500 heuristic bound |",
        f"| Roofline gap | {roofline_gap_summary(roof)} | Derived from Roofline formula | Empirical headroom; use other dimensions to explain the cause |",
        f"| Top ISU stall | {profiler.get('isu_stall_summary', {}).get('top', NA)} / {fmt_pct(profiler.get('isu_stall_summary', {}).get('top_pct'))} | mcProfiler ISU stall layout | Dominant issue-side stall bucket |",
        *(
            [
                f"| Source-latency coverage | {source_latency_coverage_summary(source_latency)} | CycleTrace/asm source-line attribution | Higher mapped-cycle share makes source-line localization more actionable |",
                f"| Source-latency top line | {source_latency_top_line_summary(source_latency)} | CycleTrace/asm source-line attribution | First code location to inspect for the dominant modeled-cycle source |",
            ]
            if source_latency.get("available")
            else []
        ),
        "",
        "## 3. Per-dimension analysis",
        "",
        "### 3.1 Occupancy & Launch",
        f"- Grid/block is `{kernel.get('grid', [])}` / `{kernel.get('block', [])}`; registers/thread={launch.get('registers_per_thread', 0)}, static shared={fmt(launch.get('static_shared_bytes', 0))} bytes.",
        f"- mcTracer occupancy fields are mtreg={launch.get('mtreg_occupancy_pct', 0):.2f}% and shared-memory={launch.get('shared_memory_occupancy_pct', 0):.2f}%; digest effective bound={launch.get('effective_occupancy_pct', 0):.2f}%.",
        f"- CycleTrace WIF mean/P25/P50/P75/max={cycle.get('wif_mean', 0):.2f}/{cycle.get('wif_p25', 0):.2f}/{cycle.get('wif_p50', 0):.2f}/{cycle.get('wif_p75', 0):.2f}/{cycle.get('wif_max', 0)} raw waves; P75/P25={wif_ratio:.2f}x.",
        f"- mcProfiler achieved/dispatched waves={fmt(profiler.get('achieved_waves'))}/{fmt(profiler.get('dispatched_waves'))}, workgroups={fmt(profiler.get('workgroups'))}, average wave life={fmt(profiler.get('average_wave_life_cycles'))} cycles.",
        f"- DPC compute balance: {dpc_balance_summary(profiler)}.",
        "",
        "### 3.2 Instruction Distribution",
        "- Primary classification uses CycleTrace `cat`, because `cat` identifies the C500 hardware category where the event executes.",
        "",
        *cycle_category_rows(cycle),
        "",
        f"- STE subevents by `name`: STE={cycle.get('raw_name_counts', {}).get('STE', 0):,}, S_NOP={cycle.get('s_nop', 0):,}, Branch={cycle.get('branch', 0):,}.",
        f"- GLOBAL subevents by `name`: GVM Load={cycle.get('gvm_load', 0):,}, GVM Store={cycle.get('gvm_store', 0):,}.",
        f"- ARRIVE subevents by `name`: Synchronization={cycle.get('raw_name_counts', {}).get('Synchronization', 0):,}.",
        "",
        "Hardware instruction cross-check:",
        "",
        *hardware_instruction_rows(cycle, profiler),
        "",
        "### 3.3 SOL / Hardware Duty",
        f"- AP MTE/STE/MMA duty={fmt_pct(profiler.get('ap_mte_duty_pct'))} / {fmt_pct(profiler.get('ap_ste_duty_pct'))} / {fmt_pct(profiler.get('ap_mma_duty_pct'))}.",
        f"- Real IPC={fmt_float(profiler.get('real_ipc'))}; instruction throughput={fmt(profiler.get('instruction_throughput'))}; throughput efficiency={fmt_pct(profiler.get('instruction_throughput_efficiency_pct'))}; compute-instruction busy duty={fmt_pct(profiler.get('compute_inst_busy_duty_pct'))}.",
        f"- Instructions per AP={fmt(profiler.get('instructions_per_ap'))}; average cycles/instruction={fmt_float(profiler.get('avg_cycles_per_instruction'))}; average all-stage latency/instruction={fmt_float(profiler.get('avg_latency_all_stages_per_instruction'))} cycles.",
        f"- Compute instruction cycle split: {compute_cycles_summary(profiler)}.",
        f"- ISU stall summary: {isu_stall_summary(profiler)}.",
        f"- AP active cycles={fmt(profiler.get('ap_active_cycles'))}; average AP busy cycles={fmt(profiler.get('average_ap_busy_cycles'))}; AP busy duty={fmt(profiler.get('ap_busy_duty_pct'))}. Treat AP busy duty as unit-sensitive context until the tool scale is confirmed.",
        "",
        "ISU stall cycle layout:",
        "",
        *stall_share_rows(profiler),
        "",
        "### 3.4 Memory Hierarchy",
        f"- VL1/L2C/SL1 hit rate={fmt_pct(profiler.get('vl1_hit_rate_pct'))} / {fmt_pct(profiler.get('l2c_hit_rate_pct'))} / {fmt_pct(profiler.get('sl1_hit_rate_pct'))}.",
        f"- DNOC read average latency={fmt(profiler.get('dnoc_read_average_latency'))} cycles; read/write requests={fmt(profiler.get('dnoc_read_req'))}/{fmt(profiler.get('dnoc_write_req'))}; achieved bandwidth={fmt_float(profiler.get('achieved_bandwidth_gbs'))} GB/s.",
        f"- Global memory bytes read/write={fmt(profiler.get('global_memory_read_bytes'))}/{fmt(profiler.get('global_memory_write_bytes'))}; global instructions read/write={fmt(profiler.get('global_read_instructions'))}/{fmt(profiler.get('global_write_instructions'))}.",
        f"- VL1 instructions read/write={fmt(profiler.get('vl1_read_instructions'))}/{fmt(profiler.get('vl1_write_instructions'))}; L2C instructions read/write={fmt(profiler.get('l2c_read_instructions'))}/{fmt(profiler.get('l2c_write_instructions'))}.",
        f"- Constant read path: total={fmt(profiler.get('constant_read_instructions'))}, SL1={fmt(profiler.get('constant_read_sl1_instructions'))}, L2={fmt(profiler.get('constant_read_l2_instructions'))}.",
        f"- DNOC latency histogram total={fmt(dnoc_total)} samples, >512-cycle samples={fmt(dnoc_high)} ({dnoc_high_pct:.2f}%).",
        f"- VL1 partition stalls: {vl1_partition_summary(profiler)}.",
        "",
        "DNOC latency buckets:",
        "",
        *dnoc_histogram_rows(profiler),
        "",
        "Memory data flow:",
        "",
        *memory_flow_summary(profiler.get("memory_data_flow", {})),
        "",
        "### 3.5 Bank Conflict & Private Memory",
        f"- Shared memory load/store instructions={fmt(profiler.get('shared_memory_load_instructions'))}/{fmt(profiler.get('shared_memory_store_instructions'))}.",
        f"- Avg cycles per load/store={fmt_float(profiler.get('avg_cycles_per_load'))}/{fmt_float(profiler.get('avg_cycles_per_store'))}; conflict cycles/inst={fmt(profiler.get('avg_conflict_cycles_per_inst'))}.",
        f"- Avg latency per load/store/atomic instruction={fmt_float(profiler.get('avg_latency_per_load_instruction'))}/{fmt_float(profiler.get('avg_latency_per_store_instruction'))}/{fmt_float(profiler.get('avg_latency_per_atomic_instruction'))} cycles; avg cycles per atomic instruction={fmt_float(profiler.get('avg_cycles_per_atomic_instruction'))}.",
        f"- Private memory from mcTracer: per_thread={launch.get('private_per_thread', 0)}, total={launch.get('private_total', 0)}.",
        f"- Private memory from mcProfiler memory flow: read={fmt(private_reads)}, write={fmt(private_writes)}.",
        f"- Atomic pressure: {fmt(atomic_total)} total atomic scalar counts across {len(atomic_keys)} atomic-related mcProfiler fields.",
        "",
        "### 3.6 Roofline",
        f"- Achieved point: {fmt_float(profiler.get('achieved_flops_tflops'))} TFLOP/s @ {fmt_float(profiler.get('achieved_bandwidth_gbs'))} GByte/s.",
        f"- Intensities: HBM={fmt_float(profiler.get('achieved_intensity_flop_per_byte'))}, VL1={fmt_float(roof.get('case_VL1_I', NA))}, L2C={fmt_float(roof.get('case_L2C_I', NA))} FLOP/Byte.",
        f"- Peak context from mcProfiler RoofLine: {roofline_peak_summary(roof)}.",
        f"- Roofline placement: selected peak={roof.get('selected_compute_peak_name', NA)} ({fmt_float(roof.get('selected_compute_peak_tflops'))} TFLOP/s, source={roof.get('selected_compute_peak_source', NA)}), HBM ridge intensity={fmt_float(roof.get('hbm_ridge_intensity_flop_per_byte'))} FLOP/Byte, region={roof.get('hbm_region', NA)}.",
        f"- Roofline bound: {roofline_bound_summary(roof)}; selected HBM roof={fmt_float(roof.get('hbm_selected_roof_tflops'))} TFLOP/s, memory roof={fmt_float(roof.get('hbm_memory_roof_tflops'))} TFLOP/s.",
        f"- Gap headroom: {roofline_gap_summary(roof)}.",
        f"- Cache-level roof context: VL1 effective BW={fmt_float(roof.get('vl1_effective_bandwidth_gbs'))} GB/s, selected roof={fmt_float(roof.get('vl1_selected_roof_tflops'))} TFLOP/s, achieved/roof={fmt_pct(roof.get('vl1_achieved_vs_roof_pct'))}; L2C effective BW={fmt_float(roof.get('l2c_effective_bandwidth_gbs'))} GB/s, selected roof={fmt_float(roof.get('l2c_selected_roof_tflops'))} TFLOP/s, achieved/roof={fmt_pct(roof.get('l2c_achieved_vs_roof_pct'))}.",
        f"- Bandwidth usage from mcProfiler RoofLine context: {roofline_usage_summary(roof)}.",
        f"- Roofline raw context: all_ops={fmt(roof.get('all_ops'))}, all_memacs={fmt(roof.get('all_memacs'))}, vl1_memacs={fmt(roof.get('vl1_memacs'))}, l2c_memacs={fmt(roof.get('l2c_memacs'))}, duration={fmt(roof.get('during'))}, core_clk={fmt_float(roof.get('core_clk'), 3)} GHz, mc_clk={fmt_float(roof.get('mc_clk'), 3)} GHz.",
        f"- Roofline interpretation: {roof.get('roofline_bound_reason', NA)}",
        "- The headline bottleneck class is a C500 heuristic diagnosis; Roofline placement is the formula-based bound view. Operator-theoretical FLOP/Byte still requires external shape/formula metadata.",
        "",
        "### 3.7 Data Availability Boundaries",
        *source_latency_boundary_lines(source_latency),
        "- Current artifacts do not expose a C500 sectors/request or useful-bytes/sector equivalent; memory flow and hit rates are not enough to claim global coalescing quality.",
        "",
        "### 3.8 Source-Line Attribution" if source_latency.get("available") else "",
        "" if source_latency.get("available") else "",
        *(
            [
                f"- Coverage: {source_latency_coverage_summary(source_latency)}.",
                "- Percentages are modeled CycleTrace/asm attribution, not full wall-clock time shares.",
                f"- {source_latency_actionability(source_latency)}",
                "- Source-latency may guide the source edit target, but aggregate CycleTrace/mcTracer/mcProfiler metrics remain the bound-classification basis.",
                "",
                *source_latency_rows(source_latency),
                "",
            ]
            if source_latency.get("available")
            else []
        ),
        "### 3.9 Metric Coverage" if source_latency.get("available") else "### 3.8 Metric Coverage",
        "",
        *metric_coverage_rows(coverage),
        "",
        "## 4. Diagnosis",
        "",
    ]
    for item in metrics["diagnosis"]:
        lines.extend([
            f"### {item['severity'].upper()}: {item['title']}",
            "",
            f"- Evidence: {item['evidence']}",
            f"- Next: {item['next']}",
            "",
        ])

    lines.extend([
        "## 5. Inference Chain",
        "",
        f"1. Measured: {primary_signal(metrics)}.",
        f"2. Likely mechanism: {mechanism_for_primary(metrics, dnoc_high_pct, wif_ratio)}",
        f"3. Why other hypotheses are weaker: {weaker_hypotheses(metrics, dnoc_high_pct, wif_ratio)}",
        "4. Risk: the proposed kernel change can shift pressure across compute, memory hierarchy, WSM conflict, and occupancy; validate with the same shape before applying a second edit.",
        "",
        "## 6. Next Concrete Edit",
        "",
        *next_edit_lines(metrics),
        "",
        "## 7. Artifacts",
        "",
        f"- Full metrics: `analysis/metrics_all_{tag}.json`",
        f"- Key metrics: `analysis/metrics_key_{tag}.json`",
        f"- Digest: `analysis/digest_{tag}.md`",
        f"- This report: `REPORT_{tag}.md`",
        *source_latency_artifact_lines(source_latency, tag),
        "",
        "## 8. Caveats",
        "",
        "- CycleTrace instruction `dur=4` is an issue-slot marker, not real execution latency.",
        "- GVM 600-cycle peak is a pressure proxy, not exact GVM buffer occupancy.",
        "- Effective occupancy uses mcTracer mtreg/shared-memory fields only; streg requires compiler/assembly evidence.",
        "- AP busy duty is reported as raw mcProfiler context because the example scale is ambiguous relative to per-pipe duty ratios.",
        *(
            [
                "- Source-latency percentages are CycleTrace/asm modeled attribution, not complete wall-clock stall attribution.",
                "- Source-line attribution can be imprecise for inlined helpers or compiler-moved instructions; use the linked source-latency CSV/JSON for full mapping detail.",
            ]
            if source_latency.get("available")
            else []
        ),
        "",
    ])
    return "\n".join(lines)
