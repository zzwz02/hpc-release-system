"""mcProfiler per-kernel artifact discovery, normalization, and matching."""

from __future__ import annotations

import argparse
import csv
import json
import re
import shutil
from pathlib import Path
from typing import Any

from .artifacts import load_json, write_json
from .utils import parse_number


PER_KERNEL_DIR = "mcprofiler_per_kernel"
OCCURRENCE_DIR_PREFIX = "occurrence_"


def _safe_int(value: Any) -> int:
    try:
        return int(value)
    except Exception:
        return 0


def _occurrence_sort_key(item: dict[str, Any]) -> tuple[int, str]:
    return (_safe_int(item.get("occurrence_index")), str(item.get("occurrence_key", "")))


def _filename_prefix(path: Path, kernel_name: str) -> str:
    index = path.name.find(kernel_name)
    return path.name[:index] if index >= 0 else ""


def _occurrence_index(prefix: str) -> int:
    match = re.search(r"(\d+)", prefix)
    return int(match.group(1)) if match else 0


def _find_scalar(report: dict[str, Any], name: str) -> float:
    for entries in report.values():
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if isinstance(entry, dict) and entry.get("name") == name:
                return parse_number(entry.get("data", entry.get("value", 0)))
    return 0.0


def _pngs_from_txt_json(path: Path) -> list[str]:
    try:
        raw = load_json(path)
    except Exception:
        return []
    out: list[str] = []
    if not isinstance(raw, dict):
        return out
    for entries in raw.values():
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            value = entry.get("value", entry.get("data"))
            if isinstance(value, dict):
                filename = value.get("filename")
                if isinstance(filename, str) and filename:
                    out.append(Path(filename).name)
    return sorted(set(out))


def _pngs_from_csv(path: Path) -> list[str]:
    if not path.exists():
        return []
    out: set[str] = set()
    with path.open(encoding="utf-8", errors="ignore", newline="") as handle:
        for row in csv.reader(handle):
            for cell in row:
                for match in re.findall(r"[^\"',\s]+\.png[^\"]*", cell):
                    out.add(Path(match).name)
    return sorted(out)


def _linked_pngs(source_dir: Path, csv_path: Path | None, txt_json_path: Path | None) -> list[str]:
    names: list[str] = []
    if csv_path and csv_path.exists():
        names.extend(_pngs_from_csv(csv_path))
    if not names and txt_json_path and txt_json_path.exists():
        names.extend(_pngs_from_txt_json(txt_json_path))
    return sorted(name for name in set(names) if (source_dir / name).exists())


def discover_per_kernel_occurrences(source_dir: Path, kernel_name: str) -> list[dict[str, Any]]:
    if not kernel_name:
        return []
    grouped: dict[str, dict[str, Any]] = {}
    suffixes = {
        "dumped": f"{kernel_name}_dumped_result.json",
        "txt_json": f"{kernel_name}.txt.json",
        "txt_csv": f"{kernel_name}.txt.csv",
        "txt": f"{kernel_name}.txt",
    }
    for path in source_dir.iterdir() if source_dir.is_dir() else []:
        if not path.is_file():
            continue
        for kind, suffix in suffixes.items():
            if not path.name.endswith(suffix):
                continue
            prefix = _filename_prefix(path, kernel_name)
            item = grouped.setdefault(
                prefix,
                {
                    "occurrence_key": prefix,
                    "occurrence_index": _occurrence_index(prefix),
                    "kernel_name": kernel_name,
                    "source_dir": str(source_dir),
                    "files": {},
                },
            )
            item["files"][kind] = path.name
    occurrences: list[dict[str, Any]] = []
    for item in grouped.values():
        files = item.get("files", {})
        dumped = source_dir / files.get("dumped", "")
        txt_json = source_dir / files.get("txt_json", "")
        if dumped.exists():
            try:
                raw = load_json(dumped)
            except Exception:
                raw = {}
            if isinstance(raw, dict):
                item["profiler_workgroups"] = _find_scalar(raw, "WORKGROUPS")
                item["profiler_waves"] = _find_scalar(raw, "WAVES")
                item["profiler_total_instructions"] = _find_scalar(raw, "Total Instructions")
                item["profiler_total_cycles"] = _find_scalar(raw, "Total Cycles")
        item["png_files"] = _linked_pngs(
            source_dir,
            source_dir / files["txt_csv"] if "txt_csv" in files else None,
            txt_json if txt_json.exists() else None,
        )
        occurrences.append(item)
    occurrences.sort(key=_occurrence_sort_key)
    for ordinal, item in enumerate(occurrences):
        item["ordinal"] = ordinal
        item["occurrence_id"] = f"{OCCURRENCE_DIR_PREFIX}{ordinal:03d}"
    return occurrences


def _copy_optional(src: Path, dest: Path, manifest_files: dict[str, str], key: str) -> None:
    if src.is_file():
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
        manifest_files[key] = dest.name


def copy_occurrence(
    occurrence: dict[str, Any],
    source_dir: Path,
    dest_dir: Path,
    *,
    include_files: bool = True,
    include_png: bool = True,
) -> dict[str, Any]:
    if dest_dir.exists():
        shutil.rmtree(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    files = occurrence.get("files", {})
    copied: dict[str, str] = {}
    if include_files:
        _copy_optional(source_dir / files.get("dumped", ""), dest_dir / "mcprofiler_report_dumped.json", copied, "dumped")
        _copy_optional(source_dir / files.get("txt_json", ""), dest_dir / "mcprofiler_report.txt.json", copied, "txt_json")
        _copy_optional(source_dir / files.get("txt", ""), dest_dir / "mcprofiler_report.txt", copied, "txt")
        _copy_optional(source_dir / files.get("txt_csv", ""), dest_dir / "mcprofiler_report.txt.csv", copied, "txt_csv")
    png_names = []
    if include_files and include_png:
        png_dir = dest_dir / "png"
        png_dir.mkdir(parents=True, exist_ok=True)
        for name in occurrence.get("png_files", []):
            src = source_dir / str(name)
            if src.exists():
                shutil.copy2(src, png_dir / src.name)
                png_names.append(src.name)
    manifest = {
        **{key: value for key, value in occurrence.items() if key not in {"files"}},
        "copied_files": copied,
        "png_files": png_names,
        "source_png_files": occurrence.get("png_files", []),
        "source_files": files,
        "artifact_scope": "mcprofiler-per-kernel-occurrence",
    }
    write_json(dest_dir / "manifest.json", manifest)
    return manifest


def _occurrence_has_required_files(occurrence: dict[str, Any], source_dir: Path) -> bool:
    files = occurrence.get("files", {})
    return (
        (source_dir / files.get("dumped", "")).is_file()
        and (source_dir / files.get("txt_json", "")).is_file()
    )


def _copy_standard_occurrence_files(occurrence: dict[str, Any], source_dir: Path, run_dir: Path) -> None:
    files = occurrence.get("files", {})
    mapping = {
        "dumped": "mcprofiler_report_dumped.json",
        "txt_json": "mcprofiler_report.txt.json",
        "txt": "mcprofiler_report.txt",
        "txt_csv": "mcprofiler_report.txt.csv",
    }
    for key, dest_name in mapping.items():
        src = source_dir / files.get(key, "")
        if src.is_file():
            shutil.copy2(src, run_dir / dest_name)
    for name in occurrence.get("png_files", []):
        src = source_dir / str(name)
        if src.is_file():
            shutil.copy2(src, run_dir / src.name)


def normalize_per_kernel_output(
    source_dir: Path,
    run_dir: Path,
    kernel_name: str,
    *,
    require_mcprofiler: bool = False,
) -> dict[str, Any]:
    occurrences = discover_per_kernel_occurrences(source_dir, kernel_name)
    per_kernel_dir = run_dir / PER_KERNEL_DIR
    if per_kernel_dir.exists():
        shutil.rmtree(per_kernel_dir)
    per_kernel_dir.mkdir(parents=True, exist_ok=True)
    for old in (
        "mcprofiler_report_dumped.json",
        "mcprofiler_report.txt.json",
        "mcprofiler_report.txt",
        "mcprofiler_report.txt.csv",
    ):
        path = run_dir / old
        if path.exists():
            path.unlink()
    for png in run_dir.glob("*.png*"):
        png.unlink()

    status = "missing" if not occurrences else "normalized"
    reason = ""
    complete_source_occurrences = [
        item for item in occurrences
        if _occurrence_has_required_files(item, source_dir)
    ]
    single_complete_occurrence = len(complete_source_occurrences) == 1 and len(occurrences) == 1
    normalized_occurrences: list[dict[str, Any]] = []
    for item in occurrences:
        occurrence_dir = per_kernel_dir / str(item["occurrence_id"])
        normalized_occurrences.append(
            copy_occurrence(
                item,
                source_dir,
                occurrence_dir,
                include_files=not single_complete_occurrence,
                include_png=not single_complete_occurrence,
            )
        )

    if not occurrences:
        reason = f"no mcProfiler per-kernel artifacts match CYCLE_TRACE_KERNEL_NAME={kernel_name}"
    elif len(complete_source_occurrences) != len(occurrences):
        status = "incomplete"
        reason = "one or more mcProfiler per-kernel occurrences lack dumped JSON or txt JSON"
    elif single_complete_occurrence:
        _copy_standard_occurrence_files(complete_source_occurrences[0], source_dir, run_dir)
        status = "single-occurrence"
        reason = "single target occurrence normalized to standard mcProfiler filenames"
    else:
        status = "multi-occurrence"
        reason = "multiple target occurrences normalized under mcprofiler_per_kernel"

    manifest = {
        "kernel_name": kernel_name,
        "source_dir": str(source_dir),
        "run_dir": str(run_dir),
        "status": status,
        "reason": reason,
        "require_mcprofiler": require_mcprofiler,
        "occurrence_count": len(occurrences),
        "complete_occurrence_count": len(complete_source_occurrences),
        "occurrences": normalized_occurrences,
    }
    write_json(per_kernel_dir / "manifest.json", manifest)
    if require_mcprofiler and status in {"missing", "incomplete"}:
        raise RuntimeError(reason)
    return manifest


def load_normalized_occurrences(source_artifact_dir: Path) -> list[dict[str, Any]]:
    base = source_artifact_dir / PER_KERNEL_DIR
    manifest_path = base / "manifest.json"
    if not manifest_path.exists():
        return []
    raw = load_json(manifest_path)
    occurrences = raw.get("occurrences", []) if isinstance(raw, dict) else []
    if not isinstance(occurrences, list):
        return []
    out: list[dict[str, Any]] = []
    for item in occurrences:
        if not isinstance(item, dict):
            continue
        occurrence_id = str(item.get("occurrence_id", ""))
        occurrence_dir = base / occurrence_id
        if occurrence_id and occurrence_dir.is_dir():
            item = dict(item)
            item["occurrence_dir"] = str(occurrence_dir)
            out.append(item)
    return sorted(out, key=_occurrence_sort_key)


def match_occurrences_to_launches(
    occurrences: list[dict[str, Any]],
    launches: list[dict[str, Any]],
) -> dict[str, Any]:
    if not occurrences:
        return {"status": "unavailable", "confidence": "low", "reason": "no mcProfiler per-kernel occurrences", "matches": []}
    if len(occurrences) != len(launches):
        return {
            "status": "ambiguous",
            "confidence": "low",
            "reason": f"mcProfiler occurrence count ({len(occurrences)}) does not match mcTracer target launch count ({len(launches)})",
            "matches": [],
        }
    matches: list[dict[str, Any]] = []
    for occurrence, launch in zip(occurrences, launches):
        profiler_workgroups = int(parse_number(occurrence.get("profiler_workgroups", 0)))
        launch_workgroups = int(launch.get("workgroups", 0) or 0)
        if profiler_workgroups and launch_workgroups and profiler_workgroups != launch_workgroups:
            return {
                "status": "ambiguous",
                "confidence": "low",
                "reason": (
                    "mcProfiler WORKGROUPS does not match mcTracer grid product: "
                    f"occurrence={occurrence.get('occurrence_id')} profiler={profiler_workgroups} "
                    f"launch={launch.get('launch_id')} grid_product={launch_workgroups}"
                ),
                "matches": [],
            }
        matches.append({
            "occurrence_id": occurrence.get("occurrence_id", ""),
            "launch_id": launch.get("launch_id"),
            "confidence": "high" if profiler_workgroups == launch_workgroups and profiler_workgroups else "medium",
            "reason": (
                "ordered mcProfiler occurrence matched to mcTracer target launch; "
                f"WORKGROUPS={profiler_workgroups}, grid_product={launch_workgroups}"
            ),
        })
    confidence = "high" if all(item["confidence"] == "high" for item in matches) else "medium"
    return {
        "status": "matched",
        "confidence": confidence,
        "reason": "mcProfiler per-kernel occurrences match mcTracer target launches by order and workgroups",
        "matches": matches,
    }


def copy_occurrence_to_artifact_dir(occurrence: dict[str, Any], artifact_dir: Path) -> None:
    source_dir = Path(str(occurrence.get("occurrence_dir", "")))
    if not source_dir.is_dir():
        return
    for name in ("mcprofiler_report_dumped.json", "mcprofiler_report.txt.json", "mcprofiler_report.txt", "mcprofiler_report.txt.csv"):
        src = source_dir / name
        if src.exists():
            shutil.copy2(src, artifact_dir / name)
    png_src = source_dir / "png"
    png_dest = artifact_dir / "png"
    if png_src.is_dir():
        png_dest.mkdir(parents=True, exist_ok=True)
        for png in png_src.glob("*.png*"):
            shutil.copy2(png, png_dest / png.name)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Normalize mcProfiler per-kernel artifacts")
    parser.add_argument("normalize", choices=("normalize",))
    parser.add_argument("--source-dir", required=True)
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--kernel-name", required=True)
    parser.add_argument("--require-mcprofiler", default="false")
    args = parser.parse_args(argv)
    require = str(args.require_mcprofiler).lower() in {"1", "true", "yes", "y"}
    manifest = normalize_per_kernel_output(
        Path(args.source_dir),
        Path(args.run_dir),
        args.kernel_name,
        require_mcprofiler=require,
    )
    print(json.dumps({"status": manifest["status"], "reason": manifest["reason"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
