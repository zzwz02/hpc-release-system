# MACA Trace Metrics To NCU Metrics Reference

This document maps the metrics currently available from `trace-report`
to the NVIDIA Nsight Compute metric anchors in:

Current MACA outputs considered:

| Output | Role |
|---|---|
| `REPORT_<tag>.md` | Final human report; only selected diagnostic metrics are shown |
| `analysis/metrics_key_<tag>.json` | Stable subset used for comparison |
| `analysis/metrics_all_<tag>.json` | Full parsed CycleTrace, mcTracer, and mcProfiler metric tree |
| `reference/05-c500-metric-names.md` | Current metric contract |
| `reference/07-report-template.md` | Current report output contract |

## Mapping Rules

| Mapping level | Meaning |
|---|---|
| Direct | Same or nearly same measured concept, only tool/hardware naming differs |
| Approximate | Same diagnostic dimension, but the hardware counter semantics differ |
| Derived | Computed from one or more MACA raw metrics |
| No corresponding metric | Current MACA artifacts do not contain equivalent data |

NCU metrics are NVIDIA-specific. MACA/C500 does not expose SM/SMSP/L1TEX/LTS
counters with identical semantics, so most useful mappings are diagnostic
equivalents rather than name-level equivalents.

## Collection Sections

| NCU section | MACA / trace-report equivalent | Mapping | Notes |
|---|---|---|---|
| `SpeedOfLight` | mcProfiler SOL scalars: `AP MTE Duty ratio`, `AP STE Duty ratio`, `AP MMA Duty ratio`, `VLS Duty ratio`, `L2C Duty ratio`, `instruction per cycle`, `instruction throughput` | Approximate | C500 AP/VLS/L2C duty ratios are the closest hardware utilization counters. |
| `SchedulerStats` | mcProfiler `Achieved waves`, `Dispatched waves`, `Average Wave life cycles`; CycleTrace WIF distribution | Approximate | Reports aggregate scheduling and wave residency, but not per-scheduler issue readiness. |
| `WarpStateStats` | No corresponding metric | No corresponding metric | Current artifacts do not expose NVIDIA-style `warp_issue_stalled_*` stall reasons. |
| `Occupancy` | mcTracer `mtreg_occupancy_pct`, `shared_memory_occupancy_pct`; derived `effective_occupancy_pct` | Approximate / Derived | streg/register-file decomposition is not available from current artifacts. |
| `LaunchStats` | mcTracer kernel name, grid, block, register/shared/private memory fields; API duration | Approximate | Covers launch geometry and resource fields, but not all NCU occupancy limit fields. |
| `MemoryWorkloadAnalysis` | mcProfiler hit rates, instruction counts, DNOC latency, memory data flow, WSM counters | Approximate | Provides memory hierarchy and bank-conflict signals, but lacks sectors/request and useful-bytes/sector equivalents. |
| `SourceCounters` | No corresponding metric | No corresponding metric | Current artifacts do not provide source-line, PC, SASS, or PTX attribution. |
| `PmSampling`, `PmSampling_WarpStates` | No corresponding metric | No corresponding metric | Current artifacts do not provide PM-sampling timeline or per-warp-state samples. |

## Runtime And Launch Identity

| NCU metric or field | MACA / trace-report metric | Mapping | Report status | Notes |
|---|---|---|---|---|
| Kernel name / demangled name | `launch.kernel_name`, `launch.mangled_name`, report kernel header | Direct | Reported | Used to confirm the captured kernel. |
| `gpu__time_duration.sum` | `launch.kernel_event_duration`; CycleTrace `cycle.span_cycles`; mcProfiler `Total Cycles` | Approximate | `span_cycles` reported; host/API duration not promoted | `gpu__time_duration.sum` is elapsed duration. MACA has cycle span and mcTracer event duration, but no direct NCU duration metric. |
| `launch__grid_size` | `launch.grid` | Direct | Reported | Grid shape from mcTracer. |
| `launch__block_size` | `launch.block` | Direct | Reported | Block shape from mcTracer. |
| `launch__registers_per_thread` | `launch.registers_per_thread` | Direct | Reported | mtreg-oriented resource field. |
| `launch__shared_mem_per_block_static` | `launch.static_shared_bytes` | Direct | Reported | Static shared memory bytes. |
| `launch__shared_mem_per_block_dynamic` | `launch.dynamic_shared_bytes` | Direct | Available in metrics_all; reported when nonzero/contextual | Dynamic shared memory bytes. |
| `launch__occupancy_limit_registers` | `launch.mtreg_occupancy_pct` | Approximate | Reported | C500 mtreg occupancy bound, not NVIDIA register occupancy-limit metric. |
| `launch__occupancy_limit_shared_mem` | `launch.shared_memory_occupancy_pct` | Approximate | Reported | C500 shared-memory occupancy bound. |
| `launch__occupancy_limit_blocks` | No corresponding metric | No corresponding metric | Not reported | Current mcTracer output does not expose a block-limit/cluster-limit occupancy field. |
| No direct NCU anchor in source doc | `launch.effective_occupancy_pct` | Derived | Reported | trace-report uses min(mtreg occupancy, shared-memory occupancy) as a conservative bound. |
| No direct NCU anchor in source doc | `launch.private_per_thread`, `launch.private_total` | Approximate | Reported | Used as spill/private-memory context. NCU usually confirms spills through local memory counters or SASS. |
| No direct NCU anchor in source doc | `launch.top_api_calls_by_duration` | No corresponding metric in selected NCU table | Available in metrics_all; not reported | Useful for host/API overhead, outside device-kernel report focus. |

## Speed-Of-Light Counters

| NCU metric | MACA / trace-report metric | Mapping | Report status | Notes |
|---|---|---|---|---|
| `sm__throughput.avg.pct_of_peak_sustained_elapsed` | `profiler.compute_inst_busy_duty_pct`, `profiler.ap_busy_duty_pct` | Approximate | Reported | Closest aggregate compute busy signal. `AP busy Duty` scale is currently treated as unit-sensitive context. |
| `sm__inst_executed_pipe_tensor.avg.pct_of_peak_sustained_elapsed` | `profiler.ap_mma_duty_pct`; CycleTrace `cycle.mma_pct`; mcProfiler hardware MMA instruction share | Approximate | Reported | AP MMA duty is the main C500 tensor-pipe signal; CycleTrace/McProfiler instruction shares cross-check it. |
| `sm__pipe_alu_cycles_active.avg.pct_of_peak_sustained_elapsed` | `profiler.ap_mte_duty_pct`, `profiler.ap_ste_duty_pct`; CycleTrace `cycle.mte_pct`, `cycle.ste_pct` | Approximate | Reported | C500 separates MTE and STE duty. MTE is currently used as the vector-compute signal. |
| `dram__throughput.avg.pct_of_peak_sustained_elapsed` | `profiler.achieved_bandwidth_gbs`; `profiler.roofline.hbm_usage_pct`; `profiler.dnoc_read_average_latency`; DNOC latency histogram | Approximate | Reported | MACA reports achieved bandwidth and RoofLine HBM usage context; DNOC latency is a separate latency signal. |
| `lts__t_bytes.avg.pct_of_peak_sustained_elapsed` | `profiler.l2c_duty_pct`; memory data flow `vl1c_l2c_rd/wr`; `profiler.l2c_hit_rate_pct` | Approximate | Duty available in metrics_all; hit/data flow reported | L2C duty is closest to L2 pressure. Data flow counts are instruction/flow counts, not NCU byte sectors. |
| `gpu__compute_memory_throughput.avg.pct_of_peak_sustained_elapsed` | Roofline `case_flops`, `case_bandwith`, `case_I`, `hbm_usage_pct`, `vl1_usage_pct`, `l2c_usage_pct`; derived bound classification | Approximate / Derived | Reported | Used as coarse compute/memory placement evidence and mcProfiler RoofLine chart context. |

## Scheduler And Warp State

| NCU metric | MACA / trace-report metric | Mapping | Report status | Notes |
|---|---|---|---|---|
| `smsp__warps_eligible.avg.per_cycle_active` | No corresponding metric | No corresponding metric | Not reported | Current artifacts do not expose eligible-ready wave count per issue cycle. |
| `sm__warps_active.avg.pct_of_peak_sustained_active` | `cycle.wif_mean`, `cycle.wif_p25`, `cycle.wif_p50`, `cycle.wif_p75`, `cycle.wif_max`; `profiler.achieved_waves` | Approximate | Reported | WIF is a raw wave-in-flight proxy from CycleTrace start/end events, not NVIDIA achieved warp occupancy. |
| `smsp__inst_executed.avg.per_cycle_active` | `profiler.real_ipc`; `profiler.instruction_throughput`; CycleTrace `issue_density_inst_per_cycle` | Approximate | `real_ipc` and throughput reported; CycleTrace density not promoted | mcProfiler IPC is preferred. CycleTrace density uses trace span and is only a sanity check. |
| `smsp__warp_issue_stalled_long_scoreboard.sum` | DNOC average latency and histogram; `profiler.l2c_hit_rate_pct`; `cycle.gvm_pct` | Approximate | Reported as memory latency context | Can weaken/strengthen memory-latency hypotheses, but does not identify stalled warps. |
| `smsp__warp_issue_stalled_short_scoreboard.sum` | `profiler.shared_memory_efficiency_pct`, `profiler.avg_conflict_cycles_per_inst`, WSM load/store cycles | Approximate | Reported | Shared-memory conflict counters are a C500 proxy for short-scoreboard-like WSM pressure. |
| `smsp__warp_issue_stalled_barrier.sum` | CycleTrace `cycle.arrive` and synchronization count | Approximate | Synchronization reported | Counts sync/arrive instructions/events; not wait-stall cycles. |
| `smsp__warp_issue_stalled_membar.sum` | No corresponding metric | No corresponding metric | Not reported | Current artifacts do not expose memory-order wait stall. |
| `smsp__warp_issue_stalled_mio_throttle.sum` | WSM counters and `VLS Duty ratio` | Approximate | WSM reported; VLS available in metrics_all/key | MIO throttle semantics do not directly transfer to C500. |
| `smsp__warp_issue_stalled_lg_throttle.sum` | `cycle.gvm_pct`, mcProfiler global read/write instructions, memory data flow global/generic_vl1c | Approximate | GVM and memory flow reported | Indicates global-memory traffic but not issue throttle. |
| `smsp__warp_issue_stalled_math_pipe_throttle.sum` | AP duty ratios: `ap_mte_duty_pct`, `ap_ste_duty_pct`, `ap_mma_duty_pct`; compute busy duty | Approximate | Reported | Shows compute-pipe pressure but not throttle stall reason. |
| `smsp__warp_issue_stalled_no_instruction.sum` | CycleTrace `cycle.nop_pct`; branch count/share | Approximate | NOP and branch reported | NOP/branch can suggest bubbles/front-end/control overhead but cannot prove no-instruction stall. |
| `smsp__warp_issue_stalled_imc_miss.sum` | No corresponding metric | No corresponding metric | Not reported | No instruction-cache miss counter is available. |
| `smsp__warp_issue_stalled_dispatch_stall.sum` | `profiler.instruction_throughput`; `profiler.instruction_throughput_efficiency_pct`; `profiler.isu_stall_summary`; CycleTrace issue density | Approximate | Throughput and ISU stall context reported | ISU stall layout is issue-side context, not a per-warp stall reason. |
| `smsp__warp_issue_stalled_drain.sum` | WIF quartiles, average wave life, DPC imbalance | Approximate | Reported | Aggregate tail/drain proxy only. |
| `smsp__warp_issue_stalled_not_selected.sum` | No corresponding metric | No corresponding metric | Not reported | No ready-but-not-selected wave metric is available. |

## Memory Path

| NCU metric | MACA / trace-report metric | Mapping | Report status | Notes |
|---|---|---|---|---|
| `dram__bytes_read.sum` | Memory data flow `l2c_Gmemory_rd`; global read flow where present | Approximate | Partially reported via memory data flow | Flow counters are not confirmed as bytes and should not be treated as exact DRAM bytes. |
| `dram__bytes_write.sum` | Memory data flow `l2c_Gmemory_wr`; global write flow where present | Approximate | Partially reported via memory data flow | Same unit caveat as reads. |
| `dram__throughput.avg.pct_of_peak_sustained_elapsed` | `profiler.achieved_bandwidth_gbs`; `profiler.roofline.hbm_usage_pct`; DNOC latency metrics | Approximate | Reported | Achieved GB/s and mcProfiler RoofLine HBM usage are available; DNOC metrics remain latency context. |
| `lts__t_bytes.sum` | Memory data flow `vl1c_l2c_rd/wr`; mcProfiler `L2C Read Instructions`, `L2C Write Instructions` | Approximate | Memory data flow reported; raw L2C instructions available in metrics_all | Instruction/flow counts are not exact NCU L2 bytes. |
| No direct NCU anchor in source doc | `profiler.global_memory_read_bytes`, `profiler.global_memory_write_bytes`, `profiler.dnoc_read_req`, `profiler.dnoc_write_req` | Approximate | Reported | Useful MACA traffic-volume context; not a sectors/request or coalescing-quality metric. |
| `lts__t_sectors_srcunit_tex_op_read.sum` | No corresponding metric | No corresponding metric | Not reported | No sector-source counter is exposed. |
| `l1tex__t_sectors_pipe_lsu_mem_global_op_ld.sum` | No corresponding metric | No corresponding metric | Not reported | No L1/VL1 sector count is exposed. |
| `l1tex__t_sectors_pipe_lsu_mem_global_op_st.sum` | No corresponding metric | No corresponding metric | Not reported | No L1/VL1 sector count is exposed. |
| `l1tex__t_requests_pipe_lsu_mem_global_op_ld.sum` | mcProfiler `Global Read Instructions`, memory data flow `global_kernel_rd`, `generic_vl1c_rd` | Approximate | Data flow reported; scalar raw field available | Counts instructions/flow entries, not NCU requests. |
| `smsp__sass_average_data_bytes_per_sector_mem_global_op_ld.pct` | No corresponding metric | No corresponding metric | Not reported | Current artifacts cannot support global coalescing/sector utilization claims. |
| `l1tex__data_bank_conflicts_pipe_lsu_mem_shared_op_ld.sum` | `shared memory access efficiency`, `average conflict cycles per instruction`, `average cycles per load instruction`, shared load instructions | Approximate | Reported | C500 exposes efficiency and conflict-cycle severity rather than NVIDIA load bank-conflict event count. |
| `l1tex__data_bank_conflicts_pipe_lsu_mem_shared_op_st.sum` | `shared memory access efficiency`, `average conflict cycles per instruction`, `average cycles per store instruction`, shared store instructions | Approximate | Reported | Same as load path. |
| No direct NCU anchor in source doc | `VL1 Hit Rate`, `L2C Hit Rate`, `Constant SL1 Hit Rate` | Approximate | Reported | Useful cache-hit context; NCU derives comparable signals from L1TEX/LTS counters. |
| No direct NCU anchor in source doc | `Dnoc Read Average Latency`, `Dnoc Read Latency Histogram` | Approximate | Reported | C500-specific memory-latency evidence, useful for ruling memory latency in/out. |
| No direct NCU anchor in source doc | `VL1 partition stall cycles layout` | Approximate | Reported | C500 partition-balance signal; no direct NCU table metric in the reference. |

## Tensor Core, TMA, And Architecture Signals

| NCU signal | MACA / trace-report metric | Mapping | Report status | Notes |
|---|---|---|---|---|
| Tensor pipe active below expectation | `profiler.ap_mma_duty_pct`, CycleTrace `cycle.mma_pct`, mcProfiler hardware MMA instruction count/share, `profiler.compute_instruction_cycles_summary.mma_cycles_pct`, RoofLine peak context | Approximate | Reported | Primary signal for tensor-core underuse on C500. |
| TMA/mbarrier wait hotspot | CycleTrace synchronization/arrive counts only | Approximate / weak | Synchronization count reported | Current artifacts do not provide TMA/mbarrier wait duration or source hotspot. |
| TMEM load/store source hotspot | No corresponding metric | No corresponding metric | Not reported | C500 artifacts do not expose NVIDIA TMEM-specific source counters. |
| 2-SM cooperative imbalance | `profiler.dpc_compute_cycles`, `profiler.dpc_compute_imbalance_pct` | Approximate | Reported | DPC compute imbalance is aggregate C500 balance evidence, not NVIDIA CTA-pair/cluster evidence. |
| CLC/tail-wave evidence | CycleTrace WIF quartiles/max, `profiler.average_wave_life_cycles`, achieved/dispatched waves | Approximate | Reported | Aggregate tail-effect proxy. No PM timeline is available. |
| WGMMA wait group hotspots on Hopper | No corresponding metric | No corresponding metric | Not reported | NVIDIA Hopper-specific signal has no current C500 equivalent. |
| C500-specific MMA path | C500 MMA lowering path inferred from AP MMA duty and MMA instruction share | Approximate | Reported | The report can detect low MMA use, but source/assembly is required to prove lowering path. |

## Source Counters And PM Sampling

| NCU evidence | MACA / trace-report metric | Mapping | Report status | Notes |
|---|---|---|---|---|
| Source line with high stall samples | No corresponding metric | No corresponding metric | Not reported | Current artifacts cannot name source-line hotspots. |
| Inline PTX line with high samples | No corresponding metric | No corresponding metric | Not reported | No source/PTX/SASS attribution is collected. |
| PM-sampling timeline shows late low occupancy | WIF quartiles, DPC imbalance, average wave life | Approximate / weak | Reported | Aggregate proxy only; cannot prove timeline shape. |
| PM-sampling timeline shows alternating TMA/MMA idle phases | No corresponding metric | No corresponding metric | Not reported | No per-AP or per-pipeline timeline. |
| One source line dominates long scoreboard | No corresponding metric | No corresponding metric | Not reported | DNOC and hit-rate metrics can suggest memory latency, but cannot identify the source line. |

## PTX And SASS Analysis

| NCU PTX/SASS signal | MACA / trace-report metric | Mapping | Report status | Notes |
|---|---|---|---|---|
| Scalar `ld.global` where vector load was expected | No corresponding metric | No corresponding metric | Not reported | Requires assembly/source evidence. |
| Low bytes/sector plus scalar loads | No corresponding metric | No corresponding metric | Not reported | Current artifacts lack bytes/sector and SASS load width. |
| Local memory load/store in SASS | mcTracer private memory fields; mcProfiler private read/write flow | Approximate | Reported | Can indicate private-memory usage, but cannot prove register spills without assembly. |
| Repeated `cvt` / pack / unpack | No corresponding metric | No corresponding metric | Not reported | Requires PTX/SASS/source evidence. |
| Extra `bar.sync` / mbarrier ops | CycleTrace `arrive` and synchronization count | Approximate | Reported | Counts sync-related events, not source-level barrier instructions or wait time. |
| `tcgen05` / `wgmma` sparse or absent in tensor kernel | `cycle.mma_pct`, `profiler.ap_mma_duty_pct`, hardware MMA instruction share | Approximate | Reported | Detects low tensor-instruction presence; exact instruction mnemonic requires disassembly. |
| Large instruction window with `imc_miss` | No corresponding metric | No corresponding metric | Not reported | No I-cache miss or code-size metric is available. |
| Unexpected cache modifiers | No corresponding metric | No corresponding metric | Not reported | Requires SASS/PTX. |

## Bottleneck To Edit Mapping

| NCU dominant signal | MACA / trace-report corresponding signal | Mapping | Current report use |
|---|---|---|---|
| Long scoreboard | DNOC latency average/histogram, L2C hit rate, GVM share, achieved bandwidth | Approximate | Used to evaluate and often rule out DRAM latency/bandwidth as primary cause. |
| Short scoreboard + bank conflicts | Shared-memory efficiency, average conflict cycles per instruction, WSM load/store cycles | Approximate | Used directly for bank-conflict diagnosis. |
| Barrier/membar/mbarrier | Synchronization/arrive counts | Approximate / weak | Reported as instruction-distribution context; not currently a strong diagnosis without wait metrics. |
| Tensor pipe low | AP MMA duty, CycleTrace MMA share, hardware MMA instruction share | Approximate | Used directly for tensor-core underuse diagnosis. |
| DRAM high, SM low | Achieved bandwidth, RoofLine HBM usage, DNOC latency, GVM share, AP duty ratios, L2C hit rate | Approximate | Used for coarse memory-vs-compute classification. |
| L2 high, DRAM low | L2C duty, L2C hit rate, memory data flow `vl1c_l2c_rd/wr` | Approximate | Available; not always headline unless L2 pressure is diagnostic. |
| Both SM and memory low | Real IPC, WIF distribution, achieved/dispatched waves, effective occupancy, DPC imbalance | Approximate | Used for latency/occupancy/tail hypotheses. |
| I-cache/no-instruction | CycleTrace NOP share, branch share | Approximate / weak | NOP/branch are reported, but no I-cache metric exists. |
| Tail waves | WIF P75/P25, WIF max, achieved/dispatched waves, average wave life, DPC imbalance | Approximate | Used as aggregate tail/balance proxy. |

## MACA Metrics Without A Clear NCU Counter Anchor

These metrics are useful in C500 reports but do not map cleanly to a single NCU
metric in the referenced `metrics.md`.

| MACA / trace-report metric | Role | NCU mapping status |
|---|---|---|
| CycleTrace `MTE`, `STE`, `BSM`, `GVM`, `LDU` instruction categories | C500 instruction-mix decomposition | Approximate only; NCU uses NVIDIA pipe/SASS counter families. |
| CycleTrace `gvm_600cy_peak` | GVM pressure proxy from 600-cycle issue window | No corresponding metric in the referenced NCU table. |
| CycleTrace WIF raw distribution | Wave concurrency and tail proxy | Approximate to active warp/occupancy/tail evidence, not a direct NCU metric. |
| mcProfiler `DPC Computing cycles comparison` | C500 DPC compute balance | No single NCU metric in the referenced table. |
| mcProfiler `VL1 partition stall cycles layout` | C500 VL1 partition imbalance | No single NCU metric in the referenced table. |
| mcProfiler `Memory Data Flow Chart` | C500 memory-flow path counts | Approximate to memory workload analysis; units differ from NCU bytes/sectors. |
| mcProfiler `Dnoc Read Latency Histogram` | C500 DNOC latency-tail evidence | Approximate to memory-latency diagnosis; no single NCU metric in the referenced table. |
| mcProfiler `case_VL1_I`, `case_L2C_I`, `hbm_usage_pct`, `vl1_usage_pct`, `l2c_usage_pct`, RoofLine peak fields | Cache-level roofline intensity, usage, and peak-line context | No single NCU metric in the referenced table, but useful roofline context. |

## Summary

The strongest cross-tool correspondences are:

| Diagnostic dimension | NCU anchor | MACA / trace-report anchor |
|---|---|---|
| Launch identity and resources | `launch__*` fields | mcTracer launch/resource fields |
| Tensor utilization | `sm__inst_executed_pipe_tensor.*` | AP MMA duty + MMA instruction share |
| Vector/ALU utilization | `sm__pipe_alu_cycles_active.*` | AP MTE/STE duty + MTE/STE instruction share |
| L2/cache pressure | `lts__*`, L1/L2 hit derivations | L2C duty/hit, VL1/L2C memory flow |
| Shared-memory conflicts | `l1tex__data_bank_conflicts_*` | shared-memory efficiency + conflict cycles/inst |
| Occupancy/resource bound | `Occupancy`, `LaunchStats` | mtreg/shared occupancy + effective occupancy |
| Tail/balance | PM sampling / active warps / launch waves | WIF quartiles + achieved/dispatched waves + DPC imbalance |

The main non-corresponding areas are NVIDIA-style per-warp stall reasons,
source-line/PC attribution, PM-sampling timelines, SASS/PTX instruction evidence,
sector/request coalescing quality, and exact DRAM/L2 byte-sector counters.
