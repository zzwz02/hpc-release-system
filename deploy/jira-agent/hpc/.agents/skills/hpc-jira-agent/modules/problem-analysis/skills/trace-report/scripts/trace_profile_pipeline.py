#!/usr/bin/env python3
"""CLI wrapper for C500 trace artifact collection, analysis, and reports."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Sequence

from trace_report.artifacts import collect_artifacts
from trace_report.compare import compare_cases, compare_tags
from trace_report.errors import format_error
from trace_report.metrics import analyze_artifacts
from trace_report.report_append import append_llm_followup_diagnosis
from trace_report.report_check import check_report
from trace_report.source_latency import (
    DEFAULT_LLVM_OBJDUMP_BIN,
    DEFAULT_OBJDUMP_BIN,
    write_source_latency_report,
)
from trace_report.target import TARGET_ENV_MARKER, TARGET_ENV_MARKER_VALUE
from trace_report.usecase_split import split_usecases


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="C500 trace artifact collect/analyze/report pipeline.")
    sub = parser.add_subparsers(dest="cmd", required=True)
    lineinfo_binary_env = os.environ.get("MACA_LINEINFO_BINARY")
    source_file_env = os.environ.get("MACA_SOURCE_FILE")

    collect = sub.add_parser("collect", help="Copy raw trace artifacts into <run-dir>/artifacts/")
    collect.add_argument("--source", type=Path, required=True)
    collect.add_argument("--run-dir", type=Path, required=True)
    collect.add_argument("--tag", required=True)
    collect.add_argument("--cycle-dpc-id", default=os.environ.get("CYCLE_TRACE_DPC_ID", "0"))
    collect.add_argument("--require-mcprofiler", choices=("true", "false"), default="false")
    collect.add_argument("--skip-mcprofiler", choices=("true", "false"), default=os.environ.get("SKIP_MCPROFILER", "false"))

    analyze = sub.add_parser("analyze", help="Analyze artifacts and write <run-dir>/analysis/")
    analyze.add_argument("--run-dir", type=Path, required=True)
    analyze.add_argument("--tag", required=True)
    analyze.add_argument("--artifact-dir", type=Path)
    analyze.add_argument("--cycle-dpc-id", default=os.environ.get("CYCLE_TRACE_DPC_ID", "0"))
    analyze.add_argument("--require-mcprofiler", choices=("true", "false"), default="false")
    analyze.add_argument("--skip-mcprofiler", choices=("true", "false"), default=os.environ.get("SKIP_MCPROFILER", "false"))
    analyze.add_argument("--cycle-kernel-name", default=os.environ.get("CYCLE_TRACE_KERNEL_NAME", ""))
    analyze.add_argument("--cycle-kernel-repeat", default=os.environ.get("CYCLE_TRACE_KERNEL_REPEAT", ""))
    analyze.add_argument("--cycle-sample-mode", default=os.environ.get("CYCLE_TRACE_SAMPLE_MODE", ""))
    analyze.add_argument(
        "--heuristic-bound-mode",
        dest="heuristic_bound_mode",
        choices=("coarse", "detailed"),
        default="coarse",
        help="C500 heuristic bound classification mode. Does not affect Roofline bound. Default: coarse.",
    )

    run = sub.add_parser("run", help="Collect then analyze one profiling artifact directory.")
    run.add_argument("--source", type=Path, required=True)
    run.add_argument("--run-dir", type=Path, required=True)
    run.add_argument("--tag", required=True)
    run.add_argument("--cycle-dpc-id", default=os.environ.get("CYCLE_TRACE_DPC_ID", "0"))
    run.add_argument("--require-mcprofiler", choices=("true", "false"), default="false")
    run.add_argument("--skip-mcprofiler", choices=("true", "false"), default=os.environ.get("SKIP_MCPROFILER", "false"))
    run.add_argument("--cycle-kernel-name", default=os.environ.get("CYCLE_TRACE_KERNEL_NAME", ""))
    run.add_argument("--cycle-kernel-repeat", default=os.environ.get("CYCLE_TRACE_KERNEL_REPEAT", ""))
    run.add_argument("--cycle-sample-mode", default=os.environ.get("CYCLE_TRACE_SAMPLE_MODE", ""))
    run.add_argument(
        "--heuristic-bound-mode",
        dest="heuristic_bound_mode",
        choices=("coarse", "detailed"),
        default="coarse",
        help="C500 heuristic bound classification mode. Does not affect Roofline bound. Default: coarse.",
    )

    compare = sub.add_parser("compare", help="Compare already analyzed tags.")
    compare.add_argument("--run-dir", type=Path, help="Run directory containing analysis/metrics_all_<tag>.json files.")
    compare.add_argument("--tag", action="append", help="Tag in --run-dir. Repeat for same-run-dir comparisons.")
    compare.add_argument("--case", action="append", help="Cross-run comparison case as label=/path/to/run-dir. Repeat at least twice.")
    compare.add_argument("--output-dir", type=Path, help="Output directory for --case comparison. Default: first case analysis dir.")

    split = sub.add_parser("split-usecases", help="Split multi-kernel or multi-usecase artifacts into single-usecase reports.")
    split.add_argument("--run-dir", type=Path, required=True)
    split.add_argument("--tag", required=True)
    split.add_argument("--artifact-dir", type=Path)
    split.add_argument("--cycle-dpc-id", default=os.environ.get("CYCLE_TRACE_DPC_ID", "0"))
    split.add_argument("--require-mcprofiler", choices=("true", "false"), default="false")
    split.add_argument("--skip-mcprofiler", choices=("true", "false"), default=os.environ.get("SKIP_MCPROFILER", "false"))
    split.add_argument("--cycle-kernel-name", default=os.environ.get("CYCLE_TRACE_KERNEL_NAME", ""))
    split.add_argument("--min-confidence", choices=("high", "medium", "low"), default="medium")
    split.add_argument("--write-ambiguous", choices=("true", "false"), default="false")
    split.add_argument(
        "--heuristic-bound-mode",
        dest="heuristic_bound_mode",
        choices=("coarse", "detailed"),
        default="coarse",
        help="C500 heuristic bound classification mode for generated usecase reports.",
    )

    append = sub.add_parser("append-diagnosis", help="Append LLM follow-up diagnosis from stdin to target REPORT_<tag>.md.")
    append.add_argument("--report", type=Path, required=True, help="Path to REPORT_<tag>.md.")

    check = sub.add_parser("check-report", help="Validate generated report workflow quality gates.")
    check.add_argument("--run-dir", type=Path, required=True)
    check.add_argument("--tag", required=True)
    check.add_argument("--require-source-latency", choices=("true", "false"), default="false")
    check.add_argument("--require-llm-followup", choices=("true", "false"), default="true")

    source_latency = sub.add_parser(
        "source-latency",
        help="Attribute CycleTrace modeled cycles to source lines.",
    )
    source_latency.add_argument("--run-dir", type=Path, required=True)
    source_latency.add_argument("--tag", required=True)
    source_latency.add_argument("--cycle-json", type=Path, help="CycleTrace JSON with instruction events, optional latency(cycles), and line/code fields. Defaults to run artifacts.")
    source_latency.add_argument("--binary", type=Path, default=Path(lineinfo_binary_env) if lineinfo_binary_env else None, help="Final executable built with -lineinfo. Preferred input for source-line mapping.")
    source_latency.add_argument("--device-elf", type=Path, help="Already extracted device ELF from the lineinfo binary.")
    source_latency.add_argument("--lineinfo-objdump", type=Path, help="Existing line-info objdump text from llvm-objdump --line-numbers --disassemble for the device ELF.")
    source_latency.add_argument("--source", type=Path, default=Path(source_file_env) if source_file_env else None, required=not bool(source_file_env), help="User source file for line text and source matching. Defaults to MACA_SOURCE_FILE.")
    source_latency.add_argument("--kernelprobe-link-obj", type=Path, help="Kernel-probe linked device object. Defaults to _kernelprobe_replication_0.link.o in run-dir when no binary/device ELF is provided.")
    source_latency.add_argument(
        "--objdump-bin",
        default=os.environ.get("MACA_OBJDUMP_BIN", DEFAULT_OBJDUMP_BIN),
        help="mxobjdump path used to extract device ELF from --binary. Defaults to MACA_OBJDUMP_BIN or mxobjdump.",
    )
    source_latency.add_argument(
        "--llvm-objdump-bin",
        default=os.environ.get("MACA_LLVM_OBJDUMP_BIN", DEFAULT_LLVM_OBJDUMP_BIN),
        help="llvm-objdump path for decoded file:line mapping after mxobjdump --extract-elf. Defaults to MACA_LLVM_OBJDUMP_BIN or llvm-objdump next to mxobjdump.",
    )
    source_latency.add_argument("--entry-label", default="MAIN_0", help="Kernel entry label in the lineinfo objdump.")
    source_latency.add_argument("--cycle-dpc-id", default=os.environ.get("CYCLE_TRACE_DPC_ID", "0"))
    source_latency.add_argument("--output-prefix", default="source_latency", help="analysis/<prefix>_<tag>.{md,csv,json}")

    return parser


def main(argv: Sequence[str]) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.cmd == "collect":
            dest = collect_artifacts(args.source, args.run_dir, args.tag, args.cycle_dpc_id, args.require_mcprofiler == "true", args.skip_mcprofiler == "true")
            print(f"[collect] {args.tag}: {dest}")
        elif args.cmd == "analyze":
            analyze_artifacts(
                args.run_dir,
                args.tag,
                args.artifact_dir,
                args.heuristic_bound_mode,
                args.cycle_dpc_id,
                args.require_mcprofiler == "true",
                args.skip_mcprofiler == "true",
                args.cycle_kernel_name,
                args.cycle_kernel_repeat,
                args.cycle_sample_mode,
            )
            print(f"[analyze] {args.tag}: {args.run_dir / 'analysis'}")
        elif args.cmd == "run":
            dest = collect_artifacts(args.source, args.run_dir, args.tag, args.cycle_dpc_id, args.require_mcprofiler == "true", args.skip_mcprofiler == "true")
            analyze_artifacts(
                args.run_dir,
                args.tag,
                dest,
                args.heuristic_bound_mode,
                args.cycle_dpc_id,
                args.require_mcprofiler == "true",
                args.skip_mcprofiler == "true",
                args.cycle_kernel_name,
                args.cycle_kernel_repeat,
                args.cycle_sample_mode,
            )
            print(f"[run] {args.tag}: {args.run_dir}")
        elif args.cmd == "compare":
            if args.case:
                out = compare_cases(args.case, args.output_dir)
            else:
                if args.run_dir is None or not args.tag:
                    raise ValueError("compare requires either --case repeated at least twice, or --run-dir with repeated --tag")
                out = compare_tags(args.run_dir, args.tag)
            print(f"[compare] {out}")
        elif args.cmd == "split-usecases":
            out = split_usecases(
                run_dir=args.run_dir,
                tag=args.tag,
                artifact_dir=args.artifact_dir,
                cycle_dpc_id=args.cycle_dpc_id,
                heuristic_bound_mode=args.heuristic_bound_mode,
                require_mcprofiler=args.require_mcprofiler == "true",
                skip_mcprofiler=args.skip_mcprofiler == "true",
                cycle_kernel_name=args.cycle_kernel_name,
                min_confidence=args.min_confidence,
                write_ambiguous=args.write_ambiguous == "true",
            )
            print(f"[split-usecases] {out}")
        elif args.cmd == "append-diagnosis":
            if os.environ.get(TARGET_ENV_MARKER) != TARGET_ENV_MARKER_VALUE:
                raise ValueError("append-diagnosis must be run through trace_report_env.py --config \"$ACTIVE_CONFIG\" run so it writes the target report")
            append_llm_followup_diagnosis(args.report, sys.stdin.read())
            print(f"[append-diagnosis] {args.report}")
        elif args.cmd == "check-report":
            check_report(
                args.run_dir,
                args.tag,
                require_source_latency=args.require_source_latency == "true",
                require_llm_followup=args.require_llm_followup == "true",
            )
            print(f"[check-report] {args.run_dir / f'REPORT_{args.tag}.md'}")
        elif args.cmd == "source-latency":
            outputs = write_source_latency_report(
                run_dir=args.run_dir,
                tag=args.tag,
                cycle_json=args.cycle_json,
                binary=args.binary,
                device_elf=args.device_elf,
                lineinfo_objdump=args.lineinfo_objdump,
                source=args.source,
                kernelprobe_link_obj=args.kernelprobe_link_obj,
                objdump_bin=args.objdump_bin,
                llvm_objdump_bin=args.llvm_objdump_bin,
                entry_label=args.entry_label,
                cycle_dpc_id=args.cycle_dpc_id,
                output_prefix=args.output_prefix,
            )
            print(f"[source-latency] {outputs['markdown']}")
    except Exception as exc:
        print(f"error: {format_error(str(exc))}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
