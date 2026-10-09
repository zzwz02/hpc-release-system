# Trace Profiling Report Template

## Read Gate

For routine finalization, read `reference/quick-finalize.md` first. Read this file only when the base report structure is abnormal, the template itself is being maintained, or `check-report` points to missing or invalid base sections. Do not treat this file as required for every `finalize-run`.

The report is the deliverable. Raw JSON, PNG charts, and extracted metric JSON
are evidence. The default single-version report is saved as:

```text
profile-artifacts/<kernel>_v<N>_<tag>/REPORT_<tag>.md
```

The script also writes a compact digest to `analysis/digest_<tag>.md`, full
machine-readable metrics to `analysis/metrics_all_<tag>.json`, and a stable
key-metric JSON to `analysis/metrics_key_<tag>.json`. Both JSON files include
collection scope and metric coverage summaries.

When `ENABLE_SOURCE_LATENCY=true`, `collect-run` also writes
`analysis/source_latency_<tag>.md`, `analysis/source_latency_<tag>.csv`, and
`analysis/source_latency_<tag>.json`, then refreshes `REPORT_<tag>.md` in strict
summary mode so the base report includes a compact source-line attribution
summary. The standalone source-latency files remain the full detail source. If
the summary is missing, unreadable, or not absorbed by the base report, the
standard `collect-run` must fail instead of silently omitting this section.

The complete workflow also requires `## 9. LLM Follow-up Diagnosis`. `collect-run`
proves collection and base analysis only. The final deliverable is the same
`REPORT_<tag>.md` after `trace_report_env.py finalize-run` or an equivalent
append entry writes the LLM section and the final `check-report` passes. LLM
content, finalize behavior, and generated usecase LLM checks are defined in
`reference/08-llm-followup-diagnosis.md`.

Optional usecase split reports are written under
`profile-artifacts/<kernel>_v<N>_<tag>/usecases/usecase_XXX/REPORT_<tag>_usecase_XXX.md`.
They use the same template against filtered artifacts, while the match evidence
and aggregation details remain in `usecases/manifest_<tag>.json`. They do not
replace the main `REPORT_<tag>.md`. `collect-run` only generates and records
them when `split-usecases` is triggered; the intermediate `collect-run` check
does not require their LLM section. In the final workflow check, every
`generated_reports` entry in `usecases/manifest_<tag>.json` must have a complete
LLM Follow-up Diagnosis.

采集、离线解析和 compare 命令见 `reference/02-collection.md`。远端/容器产物同步只在
Workflow 同步步骤执行；同步边界见 `reference/01-directory-layout.md`，同步报错见
`reference/09-error-troubleshooting.md`。报告正文不要重复这些操作命令。

## Template

```markdown
# `<kernel_name>` Trace Profiling Report

**Tag:** `<tag>`
**Target GPU / arch:** MetaX C500
**Tools:** CycleTrace + mcTracer + mcProfiler
**Run directory:** `profile-artifacts/<kernel>_v<N>_<tag>/`
**Artifact directory:** `profile-artifacts/<kernel>_v<N>_<tag>/artifacts/`

## 0. Profiling setup

- Kernel: `<exact kernel name>`
- Performance scope: `CycleTrace command-level collection`, or `CycleTrace single-kernel filter enabled`
- CycleTrace target kernel: when `CYCLE_TRACE_KERNEL_NAME` is set, the exact target kernel name
- CycleTrace kernel repeat: when `CYCLE_TRACE_KERNEL_NAME` is set, the effective repeat value
- CycleTrace sample mode: when `CYCLE_TRACE_KERNEL_NAME` is set, `C`
- mcProfiler scope: `command-level`, `per-kernel`, `per-kernel-multiple-occurrences`,
  `per-usecase-ref`, `per-kernel-unmatched`, or `per-usecase-multiple-occurrences`
- Scope note: when `CYCLE_TRACE_KERNEL_NAME` is set, state that CycleTrace metrics and mcTracer
  launch/resource fields are narrowed to the target kernel
- mcProfiler scope note: state whether profiler metrics are command-level, a single target kernel
  occurrence, multiple target occurrences that require `split-usecases`, a usecase reference to
  one parent occurrence, or unavailable because the usecase could not be matched to exactly one
  occurrence
- Grid / block: `<grid>` / `<block>`
- CycleTrace JSON: `<artifact_dir>/c-trace_output_dpc_<primary CYCLE_TRACE_DPC_ID>.json`
- CycleTrace DPC selection: when multiple DPC files are archived, list all archived
  `c-trace_output_dpc_*.json` files and state that analysis uses only the primary DPC
- mcTracer JSON: `<artifact_dir>/tracer_out.json`
- mcProfiler JSON: `<artifact_dir>/mcprofiler_report_dumped.json`, `not collected`,
  `collection_manifest.json.mcprofiler_reference.files.dumped` for
  `per-usecase-ref`, or `<artifact_dir>/mcprofiler_per_kernel/` when scope is
  `per-kernel-multiple-occurrences`
- Source-latency artifacts: when `ENABLE_SOURCE_LATENCY=true`, list
  `analysis/source_latency_<tag>.md/.csv/.json`, source file, and mapped-cycle
  coverage summary
- Usecase split: for optional split reports, state the `usecase_id` in the run
  path and refer to `usecases/manifest_<tag>.json` for launch/segment match
  confidence and aggregation details
- Usecase mapping: when the report is a split usecase with
  `mcProfiler scope=per-usecase-ref`, state `usecase_XXX -> occurrence_XXX`
  from `collection_manifest.json`; both ids are 0-based, but the mapping is
  still explicit rather than inferred from equal numbers
- Heuristic bound mode: `coarse` by default; `detailed` only when C500 compound labels are needed; separate from Roofline bound
- Artifact quality: required artifacts present; CycleTrace has non-empty `traceEvents`
  and hardware instruction events; no required invalid entry in `collection_manifest.json`.
  When `mcProfiler scope=per-kernel-multiple-occurrences`, standard
  `mcprofiler_report_*` files are intentionally absent and the complete
  `mcprofiler_per_kernel` occurrence set is the split-usecases input. When
  `mcProfiler scope=per-usecase-ref`, standard `mcprofiler_report_*` files are
  intentionally absent from the usecase directory and must be read from the
  manifest reference. When scope is `per-kernel-unmatched` or
  `per-usecase-multiple-occurrences`, profiler metrics must be treated as
  unavailable for strong usecase conclusions.

## 1. Headline

- Bottleneck class: `<compute | memory | latency | occupancy | mixed | unclear>` for coarse mode
- Primary: `<same as class, or first detailed label>`
- Signals: `<triggered coarse signals, or none>`
- Confidence: `High | Medium | Low`
- Dominant signal: `<specific metric names and values>`
- One-line read: `<single sentence diagnosis>`

## 2. Evidence

| Metric | Value | Source | Interpretation |
|---|---:|---|---|
| Kernel span | `<cycles>` | CycleTrace wave lifecycle | End-to-end trace span |
| CycleTrace MTE share | `<%>` | CycleTrace instruction events | Vector compute instruction share |
| CycleTrace MMA share | `<%>` | CycleTrace instruction events | Tensor/MMA underuse only when the kernel is expected to use MMA |
| AP MTE duty | `<%>` | mcProfiler SOL | Hardware vector pipe duty |
| AP MMA duty | `<%>` | mcProfiler SOL | Hardware tensor pipe duty |
| Shared memory efficiency | `<%>` | mcProfiler WSM | Bank-conflict signal |
| Avg conflict cycles/inst | `<cycles>` | mcProfiler WSM | Conflict penalty |
| Effective occupancy bound | `<%>` | mcTracer | mtreg/shared-memory launch bound |
| L2C hit rate | `<%>` | mcProfiler memory hierarchy | DRAM hypothesis check |
| WIF P75/P25 | `<ratio>` | CycleTrace wave lifecycle | Aggregate tail-effect proxy |
| DPC compute imbalance | `<%>` | mcProfiler DPC cycles | Aggregate compute-balance signal |
| DNOC >512-cycle share | `<%>` | mcProfiler DNOC histogram | DRAM latency-tail check |
| Roofline HBM usage | `<%>` | mcProfiler RoofLine | Achieved bandwidth divided by HBM peak from the RoofLine chart |
| Roofline gap | `compute_gap=<%>, hbm_bandwidth_gap=<%>, selected_roof_gap=<%>, hint=<text>` | Derived from Roofline formula | Empirical headroom; use other dimensions to explain the cause |
| Top ISU stall | `<bucket> / <%>` | mcProfiler ISU stall layout | Dominant issue-side stall bucket |
| Source-latency coverage | `<mapped cycles>/<modeled cycles> (<%>)` | CycleTrace/asm source-line attribution | Only when source-latency exists; higher mapped-cycle share makes source-line localization more actionable |
| Source-latency top line | `<file:line> <% total> / <% mapped>` | CycleTrace/asm source-line attribution | First code location to inspect for the dominant modeled-cycle source |

For multi-version analysis, generate `analysis/compare_<tag1>_vs_<tag2>.md` and
add delta columns only when both versions exist. Do not force Baseline/Candidate
columns in a single-version report.

## 3. Per-dimension analysis

### 3.1 Occupancy & Launch
`grid`, `block`, registers/thread, static/dynamic shared memory, private memory,
mtreg/shared-memory occupancy fields, raw WIF distribution including P25/P50/P75,
DPC compute min/avg/max + imbalance, achieved/dispatched waves, workgroups, and
average wave life. Treat `effective_occupancy_pct == 0` as unavailable or
inconsistent until resource-limit evidence is confirmed; do not use a zero value
alone to recommend resource reduction.

### 3.2 Instruction Distribution
Use CycleTrace `cat` as the primary classification because it identifies the
C500 hardware category where the event executes. Report counts and percentages
for `MTE`, `STE`, `MMA`, `BSM`, `GLOBAL`, `ARRIVE`, and `LDU`. Keep name-level
subevents such as `S_NOP`, `Branch`, `GVM Load`, `GVM Store`, and
`Synchronization` as context under their hardware categories. Include
mcProfiler `Instructions Comparison` as a hardware cross-check and flag large
percentage-point deltas.

### 3.3 SOL / Hardware Duty
mcProfiler AP MTE/STE/MMA duty, AP busy duty, compute-instruction busy duty, real
IPC, instruction throughput, throughput efficiency, instructions/AP, AP active
cycles, average AP busy cycles, compute-instruction cycle split, and ISU stall
cycle layout. Treat `AP busy Duty` as unit-sensitive context until the tool
scale is confirmed. ISU stall layout is issue-side context, not an NCU-style
per-warp stall reason.

### 3.4 Memory Hierarchy
VL1/L2C/SL1 hit rate, global byte counters, global/L2/VL1 read/write instruction
counts, constant read path counts, DNOC average latency, DNOC read/write request
counts, and available chart artifacts. Include DNOC latency histogram bucket
shares, high-latency share, VL1 partition-stall spread, and memory data flow
read/write counts. Do not reinterpret global byte counters as NCU sectors/request.

### 3.5 Bank Conflict & Private Memory
Shared-memory efficiency, load/store instruction counts, average cycles per
load/store, average latency per load/store/atomic instruction, conflict cycles,
mcTracer private-memory fields, mcProfiler private read/write confirmation, and
atomic counters when present.

### 3.6 Roofline
Empirical `case_flops`, `case_bandwith`, and `case_I` from mcProfiler. Keep
`case_bandwith` spelled as the mcProfiler raw field name. Use
`case_VL1_I` and `case_L2C_I` as cache-level intensity context. Include
mcProfiler RoofLine peak context (`TRANS`, `FMA`, `MMA-FP16`, `INT8`) derived
from `MAX_Flops_base`, plus HBM/VL1/L2C bandwidth-usage percentages matching
the RoofLine chart context. These are chart-context bandwidth roof usages, not
a complete FLOPS peak-utilization model. Include formula-based Roofline placement:
selected compute peak, HBM ridge intensity, selected HBM roof, achieved/roof,
`hbm_region`, `roofline_bound`, and `roofline_bound_confidence`. Also include
gap headroom fields: `compute_gap_pct`, `hbm_bandwidth_gap_pct`,
`selected_roof_gap_pct`, `latency_or_efficiency_gap_pct`, and `gap_hint`. Keep this separate
from the C500 heuristic bottleneck class. Use theoretical operator roofline only when
shape/operator metadata is separately supplied.

### 3.7 Data Availability Boundaries
State whether source-line attribution is unavailable, or available only as
CycleTrace/asm modeled attribution. When source-latency exists, include mapped
modeled cycles/events, top modeled instruction, and unmapped reasons. Also state
that current artifacts provide only limited CycleTrace event-timeline proxies
(`span_cycles`, WIF quartiles, GVM 600-cycle peak) plus aggregate DPC/wave balance,
not full PM-sampling/per-AP utilization timelines. Keep global-coalescing quality
and full streg occupancy decomposition as unsupported without extra artifacts.

### 3.8 Source-Line Attribution

Only include this section when `analysis/source_latency_<tag>.json` exists,
parses as a valid summary object, and is loaded as available by the analyzer.
Show a compact top-source-lines table and link to the full source-latency
artifacts. The table should include rank, `file:line`, `% total`, `% mapped`,
modeled cycles, events, top instruction, and source text. Percentages are
CycleTrace/asm modeled attribution, not complete wall-clock stall shares.
Also include an actionability statement based on mapped-cycle coverage and
unmapped/unmodeled reasons: high coverage can guide the first edit target,
medium coverage can rank candidate lines, and low coverage is only a hint.

When this section is present, source-latency may guide `Diagnosis` and
`Next Concrete Edit`, but it must not replace mcProfiler/CycleTrace aggregate
metrics as the bound-classification basis.
Diagnosis and Next Concrete Edit should explicitly state whether the top source
line supports the aggregate bound signal. If it does not, preserve the aggregate
bound and describe the source line as a low-confidence localization hint.

### 3.9 Metric Coverage
Summarize what the report does and does not cover:

- reported metric-group count
- parsed-but-not-promoted group count
- unavailable analysis dimension count

Also list unsupported dimensions from `metric_coverage.unavailable_dimensions`.
The full structured form is in `metric_coverage` inside
`analysis/metrics_all_<tag>.json`; the summary is also copied to
`analysis/metrics_key_<tag>.json`.

## 4. Diagnosis

List the fired rules in priority order. Each finding must include:

- Severity
- Title
- Evidence with concrete values
- Next action

## 5. Inference Chain

1. Measured: cite concrete values from the Evidence table.
2. Likely mechanism: connect the metric pattern to C500 behavior.
3. Why other hypotheses are weaker: explicitly rule out at least one plausible
   alternative.
4. Risk: state what the proposed code change could worsen.

## 6. Next Concrete Edit

- File: kernel source file, if it can be identified outside the current artifacts.
- Change: one concrete code-level change.
- Validation: exact rerun command or script.
- Expected metric movement: metric names, direction, and target range.
- Kernel-type gate: if the kernel name matches the tensor path
  (`gemm`, `matmul`, `mma`, `attention`, `flash_attn`, `mha`, `bmm`, `conv`), the report may describe
  evidence-backed MMA instruction path, tile shape, WSM staging, or padding
  diagnostics. For ReLU, elementwise, copy, reduction, and other non-tensor
  kernels, do not suggest MMA or WSM staging unless shared-memory access or
  reuse evidence already exists.

## 7. Artifacts

- `analysis/metrics_all_<tag>.json`
- `analysis/metrics_key_<tag>.json`
- `analysis/digest_<tag>.md`
- `REPORT_<tag>.md`
- `analysis/source_latency_<tag>.md`
- `analysis/source_latency_<tag>.csv`
- `analysis/source_latency_<tag>.json`
  only when `ENABLE_SOURCE_LATENCY=true`

## 8. Caveats

- Empty CycleTrace, metadata-only CycleTrace, or CycleTrace without hardware
  instruction events is an invalid artifact; do not generate a high-confidence
  diagnosis from it.
- CycleTrace instruction `dur=4` is an issue-slot marker, not real latency.
- GVM 600-cycle peak is a pressure proxy, not exact buffer occupancy.
- Effective occupancy uses mcTracer mtreg/shared-memory fields only; streg needs
  compiler or assembly evidence.
- If source-latency is absent, current artifacts do not provide source-line
  attribution. If source-latency is present, it is CycleTrace/asm modeled
  attribution with top modeled instruction context, not complete wall-clock stall
  attribution, and can be imprecise for inlined helpers or compiler-moved instructions.
- Current artifacts provide limited CycleTrace event-timeline proxies (`span_cycles`,
  WIF quartiles, GVM 600-cycle peak) and aggregate DPC/wave balance only; they do not
  provide PM-sampling/per-AP utilization timeline data.
- Current artifacts do not expose sectors/request or useful-bytes/sector
  counters; do not claim global coalescing quality from memory-flow counts alone.
- AP busy duty is reported as raw mcProfiler context when its unit/scale is not
  confirmed.
- RoofLine peak, usage, and placement fields are mcProfiler chart context and
  formula-based derivations; they are not a substitute for operator-specific
  theoretical FLOP/Byte without shape metadata.

## 9. LLM Follow-up Diagnosis

This section is appended after the base report has been generated and the LLM
incremental diagnosis is ready. It is mandatory for a complete workflow. The
initial automated pipeline report may not contain it, so the report is not final
until this section is appended and checked. The
execution entry, content requirements, replacement behavior, usecase report handling, and validation rules are defined in
`reference/08-llm-followup-diagnosis.md`; keep that file as the source of truth.

The appended block is delimited by:

    <!-- trace-report:llm-follow-up-diagnosis:start -->
    ...
    <!-- trace-report:llm-follow-up-diagnosis:end -->

Before final delivery, run the wrapped final check:

    python3 scripts/trace_report_env.py --config "$ACTIVE_CONFIG" check-report \
      --run-dir "$PROFILE_RUN_DIR"

`--allow-missing-llm` is only for the intermediate `collect-run` check before
the mandatory LLM follow-up diagnosis has been appended. When
`<run-dir>/usecases/manifest_<tag>.json.generated_reports` is non-empty, the
final check also validates every generated usecase report; remediation is defined
in `reference/08-llm-followup-diagnosis.md`.
```

## Style Rules

- Cite exact metric values for every claim.
- Prefer hardware-measured mcProfiler duty/hit/conflict counters over inference
  from CycleTrace instruction counts when both are present.
- Keep the headline dense; the bottleneck and top evidence must fit in 30
  seconds of reading.
- Single-version reports use `Value`; comparison reports use `Baseline`,
  `Candidate`, and `Delta`.
- Do not invent source file or line numbers when the current artifacts do not
  contain source attribution.
