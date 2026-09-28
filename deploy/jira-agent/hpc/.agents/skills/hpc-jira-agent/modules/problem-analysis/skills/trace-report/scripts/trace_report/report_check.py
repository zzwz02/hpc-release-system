"""Validate generated trace reports against workflow quality gates."""

from __future__ import annotations

import json
import re
from pathlib import Path

from .report_append import (
    END_MARKER,
    START_MARKER,
    validate_llm_followup_diagnosis,
)


def _read_json(path: Path) -> object:
    if not path.is_file() or path.stat().st_size == 0:
        raise FileNotFoundError(f"required report check input is missing or empty: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def _require_file(path: Path) -> None:
    if not path.is_file() or path.stat().st_size == 0:
        raise FileNotFoundError(f"required report output is missing or empty: {path}")

def _number(value: object) -> float:
    if isinstance(value, bool):
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return 0.0
    return 0.0


def _require_manifest_ok(run_dir: Path) -> None:
    manifest_path = run_dir / "artifacts" / "collection_manifest.json"
    manifest = _read_json(manifest_path)
    if not isinstance(manifest, dict):
        raise ValueError("collection_manifest.json must contain a JSON object")
    missing = manifest.get("missing_required", [])
    invalid = manifest.get("invalid_required", [])
    if missing:
        raise ValueError(f"collection_manifest.json missing_required is not empty: {missing}")
    if invalid:
        raise ValueError(f"collection_manifest.json invalid_required is not empty: {invalid}")
    if manifest.get("mcprofiler_scope") == "per-usecase-ref":
        _require_mcprofiler_reference_ok(manifest_path.parent, manifest)


def _require_mcprofiler_reference_ok(artifact_dir: Path, manifest: dict[str, object]) -> None:
    reference = manifest.get("mcprofiler_reference")
    if not isinstance(reference, dict):
        raise ValueError("collection_manifest.json mcprofiler_reference must exist for per-usecase-ref")
    files = reference.get("files")
    if not isinstance(files, dict):
        raise ValueError("collection_manifest.json mcprofiler_reference.files must be an object")
    for key in ("dumped", "txt_json"):
        value = files.get(key)
        if not isinstance(value, str) or not value:
            raise ValueError(f"collection_manifest.json mcprofiler_reference.files.{key} is missing")
        path = Path(value)
        if not path.is_absolute():
            path = artifact_dir / path
        try:
            raw = _read_json(path)
        except Exception as exc:
            raise ValueError(f"collection_manifest.json mcprofiler_reference.files.{key} is invalid: {path}: {exc}") from exc
        if not isinstance(raw, dict):
            raise ValueError(f"collection_manifest.json mcprofiler_reference.files.{key} must contain a JSON object: {path}")


def _require_source_latency(run_dir: Path, tag: str, report: str, metrics: dict[str, object]) -> None:
    for suffix in ("md", "csv", "json"):
        _require_file(run_dir / "analysis" / f"source_latency_{tag}.{suffix}")

    source_latency = metrics.get("source_latency")
    if not isinstance(source_latency, dict) or source_latency.get("available") is not True:
        raise ValueError("metrics_all source_latency.available must be true when ENABLE_SOURCE_LATENCY=true")
    coverage = source_latency.get("coverage")
    if not isinstance(coverage, dict):
        raise ValueError("metrics_all source_latency.coverage must be an object when ENABLE_SOURCE_LATENCY=true")
    if _number(coverage.get("raw_modeled_cycles")) <= 0:
        raise ValueError("metrics_all source_latency.coverage.raw_modeled_cycles must be > 0 when ENABLE_SOURCE_LATENCY=true")

    required_report_markers = (
        "### 3.8 Source-Line Attribution",
        "Coverage:",
        "Actionability:",
        "Percentages are modeled CycleTrace/asm attribution",
        "Source-latency top line",
        f"source_latency_{tag}.md",
        f"source_latency_{tag}.csv",
        f"source_latency_{tag}.json",
    )
    missing = [marker for marker in required_report_markers if marker not in report]
    if missing:
        raise ValueError("REPORT is missing source-latency summary markers: " + ", ".join(missing))
    source_lines = source_latency.get("source_lines")
    if isinstance(source_lines, list) and not source_lines and "no source lines were attributed" not in report:
        raise ValueError("REPORT must state that source-latency produced no attributed source lines")
    if isinstance(source_lines, list) and source_lines and "Top instruction" not in report:
        raise ValueError("REPORT is missing source-latency top instruction summary when attributed source lines exist")


def _require_llm_followup(report: str) -> None:
    start = report.find(START_MARKER)
    end = report.find(END_MARKER)
    if start == -1 or end == -1 or end <= start:
        raise ValueError(
            "REPORT is missing complete LLM follow-up diagnosis markers. "
            "collect-run has produced only the base report; do not recollect. "
            "Read reference/quick-finalize.md, REPORT_<tag>.md, "
            "analysis/metrics_key_<tag>.json, and only the needed "
            "analysis/metrics_all_<tag>.json fields; generate a diagnosis "
            "Markdown file, then run: "
            "python3 scripts/trace_report_env.py --config \"$ACTIVE_CONFIG\" finalize-run "
            "--run-dir \"$PROFILE_RUN_DIR\" --diagnosis-file <diagnosis.md>"
        )
    section = report[start + len(START_MARKER):end].strip()
    validate_llm_followup_diagnosis(section)

def _extract_section(report: str, heading: str) -> str:
    pattern = re.compile(rf"^## {re.escape(heading)}\s*$", re.MULTILINE)
    match = pattern.search(report)
    if match is None:
        return ""
    next_match = re.search(r"^## (?!#).+", report[match.end():], re.MULTILINE)
    end = match.end() + next_match.start() if next_match else len(report)
    return report[match.end():end]


def _usecase_diagnosis_titles(report: str) -> list[str]:
    diagnosis = _extract_section(report, "4. Diagnosis")
    titles: list[str] = []
    for line in diagnosis.splitlines():
        match = re.match(r"^###\s+(HIGH|MEDIUM):\s+(.+?)\s*$", line)
        if match:
            titles.append(match.group(2).strip())
    return titles


def _require_usecase_llm_optimization(report: str, section: str, report_name: str) -> None:
    titles = _usecase_diagnosis_titles(report)
    if not titles:
        return
    normalized_section = section.lower()
    matched = [title for title in titles if title.lower() in normalized_section]
    if matched:
        return
    raise ValueError(
        "generated usecase LLM follow-up must reference at least one performance "
        "diagnosis from its own ## 4. Diagnosis section; "
        f"{report_name} available diagnoses: " + ", ".join(titles)
    )


def _require_usecase_reports_llm(run_dir: Path, tag: str) -> None:
    manifest_path = run_dir / "usecases" / f"manifest_{tag}.json"
    if not manifest_path.exists():
        return
    manifest = _read_json(manifest_path)
    if not isinstance(manifest, dict):
        raise ValueError(f"usecase manifest must contain a JSON object: {manifest_path}")
    reports = manifest.get("generated_reports", [])
    if not isinstance(reports, list) or not reports:
        return

    missing: list[str] = []
    invalid: list[str] = []
    for item in reports:
        report_path = Path(str(item))
        if not report_path.is_absolute():
            report_path = Path.cwd() / report_path
        if not report_path.is_file() or report_path.stat().st_size == 0:
            missing.append(str(item))
            continue
        report = report_path.read_text(encoding="utf-8")
        start = report.find(START_MARKER)
        end = report.find(END_MARKER)
        if start == -1 or end == -1 or end <= start:
            missing.append(str(item))
            continue
        section = report[start + len(START_MARKER):end].strip()
        try:
            validate_llm_followup_diagnosis(section)
            _require_usecase_llm_optimization(report, section, str(item))
        except ValueError as exc:
            invalid.append(f"{item}: {exc}")
    if missing or invalid:
        details = []
        if missing:
            details.append("missing=" + ", ".join(missing))
        if invalid:
            details.append("invalid=" + " | ".join(invalid))
        raise ValueError(
            "generated usecase reports require finalize-run with usecase diagnoses; "
            + "; ".join(details)
            + ". Run: python3 scripts/trace_report_env.py --config \"$ACTIVE_CONFIG\" "
            "finalize-run --run-dir \"$PROFILE_RUN_DIR\" "
            "--diagnosis-file <parent-scope-diagnosis.md> "
            f"--diagnosis-dir <diagnosis-dir>"
        )

def _require_cycle_trace_scope(report: str, metrics: dict[str, object]) -> None:
    scope = metrics.get("collection_scope")
    if not isinstance(scope, dict) or scope.get("cycle_trace_kernel_filter_enabled") is not True:
        return
    kernel_name = str(scope.get("cycle_trace_kernel_name", ""))
    if not kernel_name:
        raise ValueError("metrics_all collection_scope.cycle_trace_kernel_name must not be empty when CycleTrace kernel filter is enabled")
    required_report_markers = (
        "Performance scope: CycleTrace single-kernel filter enabled",
        f"CycleTrace target kernel: `{kernel_name}`",
        "Scope note:",
    )
    missing = [marker for marker in required_report_markers if marker not in report]
    if missing:
        raise ValueError("REPORT is missing CycleTrace kernel scope markers: " + ", ".join(missing))


def check_report(
    run_dir: Path,
    tag: str,
    *,
    require_source_latency: bool,
    require_llm_followup: bool,
) -> None:
    """Raise if a generated report does not satisfy workflow quality gates."""
    report_path = run_dir / f"REPORT_{tag}.md"
    metrics_all_path = run_dir / "analysis" / f"metrics_all_{tag}.json"
    metrics_key_path = run_dir / "analysis" / f"metrics_key_{tag}.json"
    digest_path = run_dir / "analysis" / f"digest_{tag}.md"

    for path in (report_path, metrics_all_path, metrics_key_path, digest_path):
        _require_file(path)
    _require_manifest_ok(run_dir)

    metrics = _read_json(metrics_all_path)
    if not isinstance(metrics, dict):
        raise ValueError(f"metrics_all_{tag}.json must contain a JSON object")
    report = report_path.read_text(encoding="utf-8")

    _require_cycle_trace_scope(report, metrics)
    if require_source_latency:
        _require_source_latency(run_dir, tag, report, metrics)
    if require_llm_followup:
        _require_llm_followup(report)
        _require_usecase_reports_llm(run_dir, tag)
