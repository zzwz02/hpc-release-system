"""Split multi-kernel or multi-usecase trace artifacts into reportable usecases."""

from __future__ import annotations

import os
import shutil
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from .artifacts import HARDWARE_CATS, load_json, validate_cycle_trace, write_json
from .constants import cycle_trace_filename
from .mcprofiler_artifacts import (
    load_normalized_occurrences,
    match_occurrences_to_launches,
)
from .metrics import analyze_artifacts
from .utils import pct

CONFIDENCE_ORDER = {"low": 0, "medium": 1, "high": 2}


def _coord3(raw: Any) -> list[int]:
    if not isinstance(raw, dict):
        return [0, 0, 0]
    return [int(raw.get(axis, 0) or 0) for axis in ("x", "y", "z")]


def _workgroups(grid: list[int]) -> int:
    total = 1
    for value in grid:
        total *= max(int(value), 0)
    return total


def _resource_signature(launch: dict[str, Any]) -> str:
    parts = [
        launch.get("kernel_name", ""),
        "grid=" + "x".join(str(v) for v in launch.get("grid", [])),
        "block=" + "x".join(str(v) for v in launch.get("block", [])),
        f"regs={launch.get('registers_per_thread', 0)}",
        f"smem={launch.get('static_shared_bytes', 0)}",
        f"dsmem={launch.get('dynamic_shared_bytes', 0)}",
    ]
    return "|".join(parts)


def extract_tracer_launches(path: Path, target_kernel_name: str = "") -> list[dict[str, Any]]:
    raw = load_json(path)
    events = raw.get("traceEvents", []) if isinstance(raw, dict) else []
    launches: list[dict[str, Any]] = []
    if not isinstance(events, list):
        return launches
    for event_index, event in enumerate(events):
        if not isinstance(event, dict) or event.get("ph") != "X":
            continue
        args = event.get("args")
        if not isinstance(args, dict) or not {"grid", "block", "mem"}.issubset(args):
            continue
        kernel_name = str(args.get("name", ""))
        if not kernel_name:
            continue
        if target_kernel_name and kernel_name != target_kernel_name:
            continue
        mem = args.get("mem", {})
        if not isinstance(mem, dict):
            mem = {}
        grid = _coord3(args.get("grid"))
        block = _coord3(args.get("block"))
        launch = {
            "launch_id": len(launches),
            "event_index": event_index,
            "kernel_name": kernel_name,
            "display_name": str(event.get("name", "")),
            "grid": grid,
            "block": block,
            "workgroups": _workgroups(grid),
            "registers_per_thread": int(mem.get("registers_per_thread", 0) or 0),
            "static_shared_bytes": int(mem.get("static_shared", 0) or 0),
            "dynamic_shared_bytes": int(mem.get("dynamic_shared", 0) or 0),
            "private_per_thread": int(mem.get("private_per_thread", 0) or 0),
            "private_total": int(mem.get("private_total", 0) or 0),
            "mtreg_occupancy_pct": float(args.get("mtreg_occupancy(%)", 0) or 0),
            "shared_memory_occupancy_pct": float(
                args.get("shared_memory_occupancy(%)", args.get("shared_memeory_occupancy(%)", 0)) or 0
            ),
            "ts": int(event.get("ts", 0) or 0),
            "dur": int(event.get("dur", 0) or 0),
            "end_ts": int(event.get("ts", 0) or 0) + int(event.get("dur", 0) or 0),
            "queue_id": args.get("queue_id"),
            "hw_queue_id": args.get("hw_queue_id"),
            "co_id": args.get("co_id"),
        }
        launch["signature"] = _resource_signature(launch)
        launch["event"] = event
        launches.append(launch)
    return launches


def _event_ts(event: dict[str, Any]) -> int | None:
    ts = event.get("ts")
    return ts if isinstance(ts, int) else None


def _largest_gap_cuts(values: list[int], count: int) -> list[float]:
    if count <= 1 or len(values) < 2:
        return []
    ordered = sorted(values)
    gaps = [(b - a, a, b) for a, b in zip(ordered, ordered[1:]) if b > a]
    if not gaps:
        return []
    selected = sorted(gaps, reverse=True)[: count - 1]
    return sorted((a + b) / 2.0 for _, a, b in selected)


def _coarse_gap_cuts(values: list[int]) -> list[float]:
    if len(values) < 2:
        return []
    ordered = sorted(values)
    gaps = [b - a for a, b in zip(ordered, ordered[1:]) if b > a]
    if not gaps:
        return []
    median = sorted(gaps)[len(gaps) // 2]
    threshold = max(1_000_000, median * 100)
    return [
        (a + b) / 2.0
        for a, b in zip(ordered, ordered[1:])
        if b - a >= threshold
    ]


def _split_events_by_cuts(events: list[dict[str, Any]], cuts: list[float]) -> list[list[dict[str, Any]]]:
    if not cuts:
        return [events]
    buckets: list[list[dict[str, Any]]] = [[] for _ in range(len(cuts) + 1)]
    for event in events:
        ts = _event_ts(event)
        if ts is None:
            continue
        index = 0
        while index < len(cuts) and ts >= cuts[index]:
            index += 1
        buckets[index].append(event)
    return [bucket for bucket in buckets if bucket]


def _segment_from_events(segment_id: int, events: list[dict[str, Any]]) -> dict[str, Any]:
    ts_values = [ts for event in events if (ts := _event_ts(event)) is not None]
    cats = Counter(str(event.get("cat", "")) for event in events)
    names = Counter(str(event.get("name", "")) for event in events)
    hardware_total = sum(cats[name] for name in HARDWARE_CATS)
    return {
        "segment_id": segment_id,
        "start_ts": min(ts_values) if ts_values else 0,
        "end_ts": max(ts_values) if ts_values else 0,
        "span": (max(ts_values) - min(ts_values)) if ts_values else 0,
        "event_count": len(events),
        "hardware_event_count": hardware_total,
        "wave_start_count": names["Wave start"],
        "wave_end_count": names["Wave end"],
        "category_counts": {name: cats[name] for name in HARDWARE_CATS},
        "category_pcts": {name: pct(cats[name], hardware_total) for name in HARDWARE_CATS},
        "name_counts": dict(names),
        "events": events,
    }


def split_cycle_segments(path: Path, expected_count: int = 0) -> list[dict[str, Any]]:
    raw = load_json(path)
    all_events = raw.get("traceEvents", []) if isinstance(raw, dict) else []
    if not isinstance(all_events, list):
        return []
    timed_events = [event for event in all_events if isinstance(event, dict) and _event_ts(event) is not None]
    hardware_ts = [
        event["ts"]
        for event in timed_events
        if event.get("cat") in HARDWARE_CATS and isinstance(event.get("ts"), int)
    ]
    if not hardware_ts:
        return []

    all_ts = [event["ts"] for event in timed_events if isinstance(event.get("ts"), int)]
    coarse_buckets = _split_events_by_cuts(timed_events, _coarse_gap_cuts(all_ts))
    hardware_buckets = [
        bucket for bucket in coarse_buckets
        if any(event.get("cat") in HARDWARE_CATS for event in bucket)
    ]

    raw_segments: list[list[dict[str, Any]]] = []
    if expected_count and len(hardware_buckets) == 1:
        bucket = hardware_buckets[0]
        bucket_hw_ts = [
            event["ts"]
            for event in bucket
            if event.get("cat") in HARDWARE_CATS and isinstance(event.get("ts"), int)
        ]
        raw_segments.extend(_split_events_by_cuts(bucket, _largest_gap_cuts(bucket_hw_ts, expected_count)))
    else:
        raw_segments.extend(hardware_buckets)

    segments = [_segment_from_events(index, events) for index, events in enumerate(raw_segments)]
    return [segment for segment in segments if segment["hardware_event_count"] > 0]


def _ratio_error(left: list[float], right: list[float]) -> float:
    if len(left) != len(right) or not left:
        return 1.0
    left_total = sum(left)
    right_total = sum(right)
    if left_total <= 0 or right_total <= 0:
        return 1.0
    return max(abs((l / left_total) - (r / right_total)) for l, r in zip(left, right))


def match_launches_to_segments(
    launches: list[dict[str, Any]],
    segments: list[dict[str, Any]],
    min_confidence: str = "medium",
) -> dict[str, Any]:
    if not launches:
        return {"status": "ambiguous", "confidence": "low", "reason": "no matching mcTracer launches", "matches": []}
    if not segments:
        return {"status": "ambiguous", "confidence": "low", "reason": "no hardware CycleTrace segments", "matches": []}
    if len(launches) != len(segments):
        return {
            "status": "ambiguous",
            "confidence": "low",
            "reason": f"launch count ({len(launches)}) does not match segment count ({len(segments)})",
            "matches": [],
        }

    workgroups = [float(launch.get("workgroups", 0) or 0) for launch in launches]
    waves = [float(segment.get("wave_start_count", 0) or 0) for segment in segments]
    durations = [float(launch.get("dur", 0) or 0) for launch in launches]
    spans = [float(segment.get("span", 0) or 0) for segment in segments]
    wave_error = _ratio_error(workgroups, waves)
    span_error = _ratio_error(durations, spans)
    if wave_error <= 0.05 and span_error <= 0.15:
        confidence = "high"
    elif wave_error <= 0.15 or span_error <= 0.25:
        confidence = "medium"
    else:
        confidence = "low"
    status = "matched" if CONFIDENCE_ORDER[confidence] >= CONFIDENCE_ORDER[min_confidence] else "ambiguous"
    matches = []
    if status == "matched":
        for launch, segment in zip(launches, segments):
            matches.append({
                "launch_id": launch["launch_id"],
                "segment_id": segment["segment_id"],
                "signature": launch["signature"],
                "confidence": confidence,
                "reason": (
                    f"ordered match; wave/grid ratio error={wave_error:.4f}; "
                    f"duration/span ratio error={span_error:.4f}"
                ),
            })
    return {
        "status": status,
        "confidence": confidence,
        "reason": (
            f"ordered match candidate; wave/grid ratio error={wave_error:.4f}; "
            f"duration/span ratio error={span_error:.4f}"
        ),
        "matches": matches,
    }


def aggregate_matches_by_usecase(
    launches: list[dict[str, Any]],
    segments: list[dict[str, Any]],
    matches: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    launch_by_id = {launch["launch_id"]: launch for launch in launches}
    segment_by_id = {segment["segment_id"]: segment for segment in segments}
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for match in matches:
        grouped[str(match["signature"])].append(match)

    usecases: list[dict[str, Any]] = []
    for index, (signature, group) in enumerate(grouped.items()):
        group_launches = [launch_by_id[item["launch_id"]] for item in group]
        group_segments = [segment_by_id[item["segment_id"]] for item in group]
        category_counts = Counter()
        for segment in group_segments:
            category_counts.update(segment.get("category_counts", {}))
        hardware_total = sum(category_counts[name] for name in HARDWARE_CATS)
        durations = [int(launch.get("dur", 0) or 0) for launch in group_launches]
        spans = [int(segment.get("span", 0) or 0) for segment in group_segments]
        first_launch = group_launches[0]
        usecases.append({
            "usecase_id": f"usecase_{index:03d}",
            "signature": signature,
            "kernel_name": first_launch["kernel_name"],
            "display_name": first_launch["display_name"],
            "grid": first_launch["grid"],
            "block": first_launch["block"],
            "launch_count": len(group_launches),
            "matched_launch_ids": [launch["launch_id"] for launch in group_launches],
            "matched_segment_ids": [segment["segment_id"] for segment in group_segments],
            "confidence": min((item["confidence"] for item in group), key=lambda value: CONFIDENCE_ORDER[value]),
            "category_counts": {name: category_counts[name] for name in HARDWARE_CATS},
            "category_pcts": {name: pct(category_counts[name], hardware_total) for name in HARDWARE_CATS},
            "hardware_event_count": hardware_total,
            "wave_start_count": sum(int(segment.get("wave_start_count", 0) or 0) for segment in group_segments),
            "duration_sum": sum(durations),
            "duration_mean": round(sum(durations) / len(durations), 4) if durations else 0,
            "span_sum": sum(spans),
            "span_mean": round(sum(spans) / len(spans), 4) if spans else 0,
            "launches": group_launches,
            "segments": group_segments,
        })
    return usecases


def _filtered_tracer_json(original: dict[str, Any], launches: list[dict[str, Any]]) -> dict[str, Any]:
    keep_indices = {launch["event_index"] for launch in launches}
    events = original.get("traceEvents", []) if isinstance(original, dict) else []
    filtered_events = [
        event for index, event in enumerate(events)
        if not isinstance(event, dict)
        or event.get("ph") == "M"
        or index in keep_indices
        or (event.get("ph") == "X" and event.get("pid") == 1)
    ]
    return {"traceEvents": filtered_events}


def _filtered_cycle_json(original: dict[str, Any], segments: list[dict[str, Any]]) -> dict[str, Any]:
    metadata = [
        event for event in original.get("traceEvents", [])
        if isinstance(event, dict) and event.get("ph") == "M"
    ] if isinstance(original, dict) else []
    events: list[dict[str, Any]] = []
    for segment in segments:
        events.extend(segment.get("events", []))
    return {"traceEvents": metadata + events}


def _write_collection_manifest(
    artifact_dir: Path,
    *,
    source_artifact_dir: Path,
    tag: str,
    cycle_dpc_id: str | int | None,
    require_mcprofiler: bool,
    skip_mcprofiler: bool,
    usecase: dict[str, Any],
) -> None:
    derived_files = [cycle_trace_filename(cycle_dpc_id), "tracer_out.json"]
    missing_optional = []
    mcprofiler_reference = {} if skip_mcprofiler else _mcprofiler_reference(artifact_dir, usecase)
    if mcprofiler_reference:
        referenced_files = list(mcprofiler_reference.get("files", {}).values())
        files = mcprofiler_reference.get("files", {})
        if not isinstance(files, dict) or "dumped" not in files:
            missing_optional.append("mcprofiler_reference.files.dumped")
        if not isinstance(files, dict) or "txt_json" not in files:
            missing_optional.append("mcprofiler_reference.files.txt_json")
    else:
        referenced_files = []
        for name in ("mcprofiler_report_dumped.json", "mcprofiler_report.txt.json"):
            if (artifact_dir / name).exists():
                derived_files.append(name)
            else:
                missing_optional.append(name)
    invalid_required = validate_cycle_trace(artifact_dir / cycle_trace_filename(cycle_dpc_id), usecase["kernel_name"], "")
    mcprofiler_scope = "skipped" if skip_mcprofiler else usecase.get("mcprofiler_scope", "command-level")
    source_manifest = {}
    source_manifest_path = source_artifact_dir / "collection_manifest.json"
    if source_manifest_path.exists():
        try:
            loaded_manifest = load_json(source_manifest_path)
            if isinstance(loaded_manifest, dict):
                source_manifest = loaded_manifest
        except Exception:
            source_manifest = {}
    missing_required = (
        missing_optional
        if require_mcprofiler and mcprofiler_scope in {"per-usecase", "per-usecase-ref"}
        else []
    )
    manifest = {
        "tag": tag,
        "source": str(source_artifact_dir),
        "artifact_dir": str(artifact_dir),
        "copied_files": derived_files,
        "derived_files": derived_files,
        "referenced_files": referenced_files,
        "require_mcprofiler": require_mcprofiler,
        "skip_mcprofiler": skip_mcprofiler,
        "mcprofiler_available": (not skip_mcprofiler) and not missing_optional,
        "cycle_trace_primary": cycle_trace_filename(cycle_dpc_id),
        "cycle_trace_files": [cycle_trace_filename(cycle_dpc_id)],
        "missing_required": missing_required,
        "missing_optional": missing_optional,
        "invalid_optional": [],
        "invalid_required": invalid_required,
        "derived": True,
        "derived_from": str(source_artifact_dir),
        "usecase_id": usecase["usecase_id"],
        "matched_occurrence_id": usecase.get("matched_occurrence_id", ""),
        "matched_occurrence_dir": usecase.get("matched_occurrence_dir", ""),
        "match_confidence": usecase["confidence"],
        "matched_launch_ids": usecase["matched_launch_ids"],
        "matched_segment_ids": usecase["matched_segment_ids"],
        "mcprofiler_scope": mcprofiler_scope,
        "mcprofiler_match": usecase.get("mcprofiler_match", {}),
        "requested_maca_visible_devices": source_manifest.get("requested_maca_visible_devices", ""),
        "selected_maca_visible_devices": source_manifest.get("selected_maca_visible_devices", ""),
        "auto_selected_device": bool(source_manifest.get("auto_selected_device", False)),
    }
    if mcprofiler_reference:
        manifest["mcprofiler_reference"] = mcprofiler_reference
    write_json(artifact_dir / "collection_manifest.json", manifest)


def _relpath(path: Path, start: Path) -> str:
    return os.path.relpath(path, start)


def _mcprofiler_reference(artifact_dir: Path, usecase: dict[str, Any]) -> dict[str, Any]:
    occurrence = usecase.get("mcprofiler_occurrence")
    if usecase.get("mcprofiler_scope") != "per-usecase-ref" or not isinstance(occurrence, dict):
        return {}
    occurrence_dir = Path(str(occurrence.get("occurrence_dir", "")))
    if not occurrence_dir.is_dir():
        return {}
    files = {
        "dumped": occurrence_dir / "mcprofiler_report_dumped.json",
        "txt_json": occurrence_dir / "mcprofiler_report.txt.json",
        "txt": occurrence_dir / "mcprofiler_report.txt",
        "txt_csv": occurrence_dir / "mcprofiler_report.txt.csv",
    }
    png_dir = occurrence_dir / "png"
    existing_files = {
        key: _relpath(path, artifact_dir)
        for key, path in files.items()
        if path.is_file()
    }
    reference: dict[str, Any] = {
        "occurrence_id": occurrence.get("occurrence_id", ""),
        "artifact_dir": _relpath(occurrence_dir, artifact_dir),
        "files": existing_files,
        "png_dir": _relpath(png_dir, artifact_dir) if png_dir.is_dir() else "",
        "png_files": occurrence.get("png_files", []),
    }
    return reference


def attach_profiler_occurrences_to_usecases(
    usecases: list[dict[str, Any]],
    occurrences: list[dict[str, Any]],
    profiler_match: dict[str, Any],
) -> None:
    if profiler_match.get("status") != "matched":
        for usecase in usecases:
            usecase["mcprofiler_scope"] = "per-kernel-unmatched"
            usecase["mcprofiler_match"] = profiler_match
        return
    occurrence_by_launch_id = {
        match.get("launch_id"): next(
            (
                occurrence for occurrence in occurrences
                if occurrence.get("occurrence_id") == match.get("occurrence_id")
            ),
            None,
        )
        for match in profiler_match.get("matches", [])
    }
    for usecase in usecases:
        matched_occurrences = [
            occurrence_by_launch_id.get(launch_id)
            for launch_id in usecase.get("matched_launch_ids", [])
            if occurrence_by_launch_id.get(launch_id)
        ]
        if len(matched_occurrences) == 1:
            occurrence_dir = str(matched_occurrences[0].get("occurrence_dir", ""))
            usecase["mcprofiler_scope"] = "per-usecase-ref"
            usecase["mcprofiler_occurrence"] = matched_occurrences[0]
            usecase["matched_occurrence_id"] = matched_occurrences[0].get("occurrence_id", "")
            usecase["matched_occurrence_dir"] = occurrence_dir
            usecase["mcprofiler_match"] = {
                "status": "matched",
                "confidence": profiler_match.get("confidence", "medium"),
                "reason": "single mcProfiler occurrence matched to this usecase",
                "occurrence_id": matched_occurrences[0].get("occurrence_id"),
                "occurrence_dir": occurrence_dir,
            }
        elif matched_occurrences:
            usecase["mcprofiler_scope"] = "per-usecase-multiple-occurrences"
            usecase["matched_occurrence_ids"] = [item.get("occurrence_id") for item in matched_occurrences]
            usecase["mcprofiler_match"] = {
                "status": "ambiguous",
                "confidence": "low",
                "reason": "multiple mcProfiler occurrences map to this aggregated usecase; profiler aggregation is not applied",
                "occurrence_ids": [item.get("occurrence_id") for item in matched_occurrences],
            }
        else:
            usecase["mcprofiler_scope"] = "per-kernel-unmatched"
            usecase["mcprofiler_match"] = profiler_match


def write_usecase_artifacts(
    *,
    run_dir: Path,
    tag: str,
    source_artifact_dir: Path,
    tracer_raw: dict[str, Any],
    cycle_raw: dict[str, Any],
    usecase: dict[str, Any],
    cycle_dpc_id: str | int | None,
    require_mcprofiler: bool,
    skip_mcprofiler: bool = False,
) -> Path:
    usecase_dir = run_dir / "usecases" / usecase["usecase_id"]
    artifact_dir = usecase_dir / "artifacts"
    if artifact_dir.exists():
        shutil.rmtree(artifact_dir)
    artifact_dir.mkdir(parents=True, exist_ok=True)

    write_json(artifact_dir / "tracer_out.json", _filtered_tracer_json(tracer_raw, usecase["launches"]))
    write_json(artifact_dir / cycle_trace_filename(cycle_dpc_id), _filtered_cycle_json(cycle_raw, usecase["segments"]))
    _write_collection_manifest(
        artifact_dir,
        source_artifact_dir=source_artifact_dir,
        tag=tag,
        cycle_dpc_id=cycle_dpc_id,
        require_mcprofiler=require_mcprofiler,
        skip_mcprofiler=skip_mcprofiler,
        usecase=usecase,
    )
    return usecase_dir


def write_usecase_index(out_dir: Path, tag: str, manifest: dict[str, Any]) -> None:
    reports_by_usecase = {
        Path(str(report)).parent.name: str(report)
        for report in manifest.get("generated_reports", [])
    }
    lines = [
        f"# Usecase Index `{tag}`",
        "",
        "| usecase | occurrence | launch ids | segment ids | confidence | report |",
        "|---|---|---|---|---|---|",
    ]
    for usecase in manifest.get("usecases", []):
        if not isinstance(usecase, dict):
            continue
        usecase_id = str(usecase.get("usecase_id", ""))
        occurrence = str(
            usecase.get("matched_occurrence_id")
            or ",".join(str(item) for item in usecase.get("matched_occurrence_ids", []) if item)
            or "N/A"
        )
        launch_ids = ",".join(str(item) for item in usecase.get("matched_launch_ids", []))
        segment_ids = ",".join(str(item) for item in usecase.get("matched_segment_ids", []))
        confidence = str(usecase.get("confidence", ""))
        report = reports_by_usecase.get(usecase_id, "")
        lines.append(
            f"| `{usecase_id}` | `{occurrence}` | `{launch_ids}` | "
            f"`{segment_ids}` | `{confidence}` | `{report}` |"
        )
    lines.append("")
    (out_dir / f"index_{tag}.md").write_text("\n".join(lines), encoding="utf-8")


def split_usecases(
    *,
    run_dir: Path,
    tag: str,
    artifact_dir: Path | None = None,
    cycle_dpc_id: str | int | None = None,
    heuristic_bound_mode: str = "coarse",
    require_mcprofiler: bool = False,
    skip_mcprofiler: bool = False,
    cycle_kernel_name: str = "",
    min_confidence: str = "medium",
    write_ambiguous: bool = False,
) -> Path:
    if min_confidence not in CONFIDENCE_ORDER:
        raise ValueError("split-usecases --min-confidence must be high, medium, or low")
    source_artifact_dir = artifact_dir or run_dir / "artifacts"
    if not source_artifact_dir.is_dir():
        raise ValueError(f"split-usecases artifact directory does not exist: {source_artifact_dir}")
    tracer_path = source_artifact_dir / "tracer_out.json"
    cycle_path = source_artifact_dir / cycle_trace_filename(cycle_dpc_id)
    if not tracer_path.exists() or not cycle_path.exists():
        raise ValueError("split-usecases requires tracer_out.json and primary CycleTrace JSON in artifacts")

    tracer_raw = load_json(tracer_path)
    cycle_raw = load_json(cycle_path)
    launches = extract_tracer_launches(tracer_path, cycle_kernel_name)
    segments = split_cycle_segments(cycle_path, len(launches))
    match_result = match_launches_to_segments(launches, segments, min_confidence)
    usecases = aggregate_matches_by_usecase(launches, segments, match_result.get("matches", []))
    profiler_occurrences = [] if skip_mcprofiler else load_normalized_occurrences(source_artifact_dir)
    profiler_match = (
        {
            "status": "skipped",
            "confidence": "high",
            "reason": "SKIP_MCPROFILER=true; mcProfiler matching is disabled",
            "matches": [],
        }
        if skip_mcprofiler
        else match_occurrences_to_launches(profiler_occurrences, launches) if cycle_kernel_name else {
        "status": "command-level",
        "confidence": "low",
        "reason": "CYCLE_TRACE_KERNEL_NAME is not set; mcProfiler remains command-level",
        "matches": [],
        }
    )
    if skip_mcprofiler:
        for usecase in usecases:
            usecase["mcprofiler_scope"] = "skipped"
            usecase["mcprofiler_match"] = profiler_match
    else:
        attach_profiler_occurrences_to_usecases(usecases, profiler_occurrences, profiler_match)

    out_dir = run_dir / "usecases"
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "tag": tag,
        "source_artifact_dir": str(source_artifact_dir),
        "cycle_trace_file": cycle_trace_filename(cycle_dpc_id),
        "cycle_kernel_name": cycle_kernel_name,
        "min_confidence": min_confidence,
        "skip_mcprofiler": skip_mcprofiler,
        "launches": [{key: value for key, value in launch.items() if key != "event"} for launch in launches],
        "segments": [{key: value for key, value in segment.items() if key != "events"} for segment in segments],
        "match_result": match_result,
        "mcprofiler_match_result": profiler_match,
        "usecases": [
            {key: value for key, value in usecase.items() if key not in {"launches", "segments"}}
            for usecase in usecases
        ],
        "generated_reports": [],
    }

    can_write_reports = match_result.get("status") == "matched" or write_ambiguous
    if can_write_reports:
        for usecase in usecases:
            usecase_tag = f"{tag}_{usecase['usecase_id']}"
            usecase_dir = write_usecase_artifacts(
                run_dir=run_dir,
                tag=usecase_tag,
                source_artifact_dir=source_artifact_dir,
                tracer_raw=tracer_raw,
                cycle_raw=cycle_raw,
                usecase=usecase,
                cycle_dpc_id=cycle_dpc_id,
                require_mcprofiler=require_mcprofiler,
                skip_mcprofiler=skip_mcprofiler,
            )
            analyze_artifacts(
                usecase_dir,
                usecase_tag,
                usecase_dir / "artifacts",
                heuristic_bound_mode,
                cycle_dpc_id,
                require_mcprofiler,
                skip_mcprofiler,
                usecase["kernel_name"],
                "",
                "C",
            )
            manifest["generated_reports"].append(str(usecase_dir / f"REPORT_{usecase_tag}.md"))

    write_json(out_dir / f"manifest_{tag}.json", manifest)
    write_usecase_index(out_dir, tag, manifest)
    return out_dir
