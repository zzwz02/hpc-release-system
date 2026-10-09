# MACA Trace 指标与 NCU 指标对应关系

本文档将 `trace-report` 当前已有数据和报告输出，与以下 NCU 指标参考文档进行对应：

纳入对照的当前 MACA 输出：

| 输出 | 作用 |
|---|---|
| `REPORT_<tag>.md` | 最终诊断报告，只输出诊断需要的关键指标 |
| `analysis/metrics_key_<tag>.json` | 稳定的关键指标子集，用于版本对比 |
| `analysis/metrics_all_<tag>.json` | 完整解析后的 CycleTrace、mcTracer、mcProfiler 指标树 |
| `reference/05-c500-metric-names.md` | 当前指标契约 |
| `reference/07-report-template.md` | 当前报告输出契约 |

## 统计汇总

### 映射关系统计

统计范围：本文档中与 NCU 章节、NCU 指标、NCU 诊断信号或 MACA 特有指标相关的 93 行映射关系；不包含“映射等级说明”和末尾“汇总”表。

| 映射等级 | 数量 | 占比 | 含义 |
|---|---:|---:|---|
| 直接对应 | 6 | 6.45% | 语义基本一致，只是工具或字段命名不同 |
| 近似对应 | 56 | 60.22% | 属于同一诊断维度，但硬件计数器语义不同 |
| 近似/派生 | 2 | 2.15% | 需要由多个 MACA 指标组合解释 |
| 派生 | 1 | 1.08% | 由 MACA 原始字段计算得到 |
| 无对应关系 | 28 | 30.11% | 当前 MACA 产物没有等价数据 |

### 报告输出状态统计

统计范围：带有“报告状态”列的 68 行指标或信号映射。

| 报告状态 | 数量 | 占比 | 说明 |
|---|---:|---:|---|
| 已输出到报告 | 40 | 58.82% | 已在 `REPORT_<tag>.md` 中直接展示或用于诊断叙述 |
| 仅部分输出 / 仅存在于 `metrics_all` | 8 | 11.76% | 数据已解析或间接输出，但不是报告主指标 |
| 未输出或无数据 | 20 | 29.41% | 当前产物缺失，或已有数据不适合进入核心报告 |

### 按 NCU 章节统计

| 章节 | 映射行数 | 直接 | 近似 | 近似/派生 | 派生 | 无对应关系 |
|---|---:|---:|---:|---:|---:|---:|
| Collection Sections | 8 | 0 | 4 | 1 | 0 | 3 |
| Runtime And Launch Identity | 13 | 6 | 4 | 0 | 1 | 2 |
| Speed-Of-Light Counters | 6 | 0 | 5 | 1 | 0 | 0 |
| Scheduler And Warp State | 15 | 0 | 11 | 0 | 0 | 4 |
| Memory Path | 14 | 0 | 10 | 0 | 0 | 4 |
| Tensor Core / TMA / Architecture Signals | 7 | 0 | 5 | 0 | 0 | 2 |
| Source Counters And PM Sampling | 5 | 0 | 1 | 0 | 0 | 4 |
| PTX And SASS Analysis | 8 | 0 | 3 | 0 | 0 | 5 |
| Bottleneck To Edit Mapping | 9 | 0 | 9 | 0 | 0 | 0 |
| MACA 特有或无单一 NCU 锚点指标 | 8 | 0 | 4 | 0 | 0 | 4 |

### 无对应关系原因统计

| 缺失原因 | 数量 | 代表项 |
|---|---:|---|
| NVIDIA/CUDA 专用计数器或架构信号 | 7 | `WarpStateStats`、WGMMA wait group、TMEM、sector source、L1TEX sector |
| 当前 MACA 产物没有源码、PC、SASS、PTX 归因 | 8 | source line stall、inline PTX hotspot、cache modifier、`cvt`/pack/unpack |
| 当前 MACA 产物没有 PM-sampling 或时间线 | 3 | PM timeline、per-AP timeline、alternating TMA/MMA idle phase |
| 当前 MACA 产物没有 warp stall 细分 | 5 | eligible warps、membar、imc miss、not selected、source-line long scoreboard |
| 当前 MACA 产物没有精确字节/扇区/合并访问信息 | 4 | bytes/sector、L1 sectors、DRAM exact bytes |
| 当前 MACA 产物没有 launch block/cluster limit | 1 | `launch__occupancy_limit_blocks` |

## 映射等级说明

| 映射等级 | 含义 |
|---|---|
| 直接对应 | 语义相同或接近相同，只是工具或硬件命名不同 |
| 近似对应 | 属于同一诊断维度，但底层硬件计数器语义不同 |
| 派生 | 由一个或多个 MACA 原始指标计算得到 |
| 无对应关系 | 当前 MACA 产物不包含等价数据 |

NCU 指标是 NVIDIA 硬件和 Nsight Compute 的指标体系。MACA/C500 不提供完全相同的 SM/SMSP/L1TEX/LTS 计数器，因此大多数有效对应关系是“诊断维度对应”，不是“计数器语义完全一致”。

## Collection Sections

| NCU 章节 | MACA / trace-report 对应项 | 映射 | 说明 |
|---|---|---|---|
| `SpeedOfLight` | mcProfiler SOL 标量：`AP MTE Duty ratio`、`AP STE Duty ratio`、`AP MMA Duty ratio`、`VLS Duty ratio`、`L2C Duty ratio`、`instruction per cycle`、`instruction throughput` | 近似对应 | C500 中最接近硬件利用率的指标。 |
| `SchedulerStats` | mcProfiler `Achieved waves`、`Dispatched waves`、`Average Wave life cycles`；CycleTrace WIF 分布 | 近似对应 | 能反映整体调度和 wave 驻留情况，但不是每个 scheduler 的 ready/eligible 信息。 |
| `WarpStateStats` | 无对应关系 | 无对应关系 | 当前产物没有 NVIDIA 风格的 `warp_issue_stalled_*` stall reason。 |
| `Occupancy` | mcTracer `mtreg_occupancy_pct`、`shared_memory_occupancy_pct`；派生 `effective_occupancy_pct` | 近似/派生 | 当前产物缺少 streg/register-file 完整分解。 |
| `LaunchStats` | mcTracer kernel name、grid、block、register/shared/private memory 字段、API duration | 近似对应 | 覆盖 launch 几何和部分资源字段，但不覆盖全部 NCU occupancy limit 字段。 |
| `MemoryWorkloadAnalysis` | mcProfiler hit rate、instruction count、DNOC latency、memory data flow、WSM counter | 近似对应 | 能覆盖 memory hierarchy 和 bank conflict 诊断，但没有 sectors/request 或 useful-bytes/sector。 |
| `SourceCounters` | 无对应关系 | 无对应关系 | 当前产物没有 source line、PC、SASS、PTX 归因。 |
| `PmSampling`、`PmSampling_WarpStates` | 无对应关系 | 无对应关系 | 当前产物没有 PM-sampling timeline 或 per-warp-state samples。 |

## Runtime And Launch Identity

| NCU 指标或字段 | MACA / trace-report 指标 | 映射 | 报告状态 | 说明 |
|---|---|---|---|---|
| Kernel name / demangled name | `launch.kernel_name`、`launch.mangled_name`、报告标题 kernel | 直接对应 | 已输出 | 用于确认采集到目标 kernel。 |
| `gpu__time_duration.sum` | `launch.kernel_event_duration`；CycleTrace `cycle.span_cycles`；mcProfiler `Total Cycles` | 近似对应 | 已输出 `span_cycles`，host/API duration 未进入主报告 | MACA 有 cycle span 和 mcTracer event duration，但没有完全等价的 NCU elapsed duration。 |
| `launch__grid_size` | `launch.grid` | 直接对应 | 已输出 | mcTracer 采集的 grid shape。 |
| `launch__block_size` | `launch.block` | 直接对应 | 已输出 | mcTracer 采集的 block shape。 |
| `launch__registers_per_thread` | `launch.registers_per_thread` | 直接对应 | 已输出 | C500 mtreg 相关资源字段。 |
| `launch__shared_mem_per_block_static` | `launch.static_shared_bytes` | 直接对应 | 已输出 | static shared memory bytes。 |
| `launch__shared_mem_per_block_dynamic` | `launch.dynamic_shared_bytes` | 直接对应 | 存在于 `metrics_all`；非零或有诊断价值时输出 | dynamic shared memory bytes。 |
| `launch__occupancy_limit_registers` | `launch.mtreg_occupancy_pct` | 近似对应 | 已输出 | C500 mtreg occupancy bound，不是 NVIDIA register occupancy-limit 原始指标。 |
| `launch__occupancy_limit_shared_mem` | `launch.shared_memory_occupancy_pct` | 近似对应 | 已输出 | C500 shared-memory occupancy bound。 |
| `launch__occupancy_limit_blocks` | 无对应关系 | 无对应关系 | 未输出 | 当前 mcTracer 没有 block-limit/cluster-limit occupancy 字段。 |
| NCU 源文档无直接锚点 | `launch.effective_occupancy_pct` | 派生 | 已输出 | trace-report 使用 min(mtreg occupancy, shared-memory occupancy) 作为保守 bound。 |
| NCU 源文档无直接锚点 | `launch.private_per_thread`、`launch.private_total` | 近似对应 | 已输出 | 用作 spill/private-memory 上下文；真正确认 spill 仍需要 local memory counter 或汇编。 |
| NCU 源文档无直接锚点 | `launch.top_api_calls_by_duration` | 源文档选定表中无对应关系 | 存在于 `metrics_all`，未进入主报告 | 可用于 host/API overhead 分析，不属于当前 device-kernel 报告重点。 |

## Speed-Of-Light Counters

| NCU 指标 | MACA / trace-report 指标 | 映射 | 报告状态 | 说明 |
|---|---|---|---|---|
| `sm__throughput.avg.pct_of_peak_sustained_elapsed` | `profiler.compute_inst_busy_duty_pct`、`profiler.ap_busy_duty_pct` | 近似对应 | 已输出 | 最接近整体 compute busy 的 C500 指标；`AP busy Duty` 当前按单位/尺度未完全确认的上下文处理。 |
| `sm__inst_executed_pipe_tensor.avg.pct_of_peak_sustained_elapsed` | `profiler.ap_mma_duty_pct`；CycleTrace `cycle.mma_pct`；mcProfiler MMA 硬件指令占比 | 近似对应 | 已输出 | AP MMA duty 是 C500 tensor pipe 的主要证据，CycleTrace/mcProfiler 指令占比用于交叉验证。 |
| `sm__pipe_alu_cycles_active.avg.pct_of_peak_sustained_elapsed` | `profiler.ap_mte_duty_pct`、`profiler.ap_ste_duty_pct`；CycleTrace `cycle.mte_pct`、`cycle.ste_pct` | 近似对应 | 已输出 | C500 区分 MTE 和 STE duty，当前用 MTE 作为 vector-compute 主信号。 |
| `dram__throughput.avg.pct_of_peak_sustained_elapsed` | `profiler.achieved_bandwidth_gbs`、`profiler.roofline.hbm_usage_pct`、`profiler.dnoc_read_average_latency`、DNOC latency histogram | 近似对应 | 已输出 | MACA 输出 achieved GB/s 和 RoofLine HBM usage 上下文；DNOC latency 是独立延迟信号。 |
| `lts__t_bytes.avg.pct_of_peak_sustained_elapsed` | `profiler.l2c_duty_pct`；memory data flow `vl1c_l2c_rd/wr`；`profiler.l2c_hit_rate_pct` | 近似对应 | duty 存在于 `metrics_all`；hit/data flow 已输出 | L2C duty 是最接近 L2 pressure 的 C500 指标；data flow 不是 NCU byte sector。 |
| `gpu__compute_memory_throughput.avg.pct_of_peak_sustained_elapsed` | Roofline `case_flops`、`case_bandwith`、`case_I`、`hbm_usage_pct`、`vl1_usage_pct`、`l2c_usage_pct`；派生 bound classification | 近似/派生 | 已输出 | 用于粗粒度 compute/memory placement 和 mcProfiler RoofLine 图表上下文。 |

## Scheduler And Warp State

| NCU 指标 | MACA / trace-report 指标 | 映射 | 报告状态 | 说明 |
|---|---|---|---|---|
| `smsp__warps_eligible.avg.per_cycle_active` | 无对应关系 | 无对应关系 | 未输出 | 当前产物没有每 issue cycle eligible-ready wave 数。 |
| `sm__warps_active.avg.pct_of_peak_sustained_active` | `cycle.wif_mean`、`cycle.wif_p25`、`cycle.wif_p50`、`cycle.wif_p75`、`cycle.wif_max`；`profiler.achieved_waves` | 近似对应 | 已输出 | WIF 是根据 CycleTrace wave start/end 计算的 raw wave-in-flight 代理，不等价于 NVIDIA achieved warp occupancy。 |
| `smsp__inst_executed.avg.per_cycle_active` | `profiler.real_ipc`、`profiler.instruction_throughput`；CycleTrace `issue_density_inst_per_cycle` | 近似对应 | `real_ipc` 和 throughput 已输出；CycleTrace density 不进入主报告 | mcProfiler IPC 优先；CycleTrace density 只作 sanity check。 |
| `smsp__warp_issue_stalled_long_scoreboard.sum` | DNOC average latency/histogram、`profiler.l2c_hit_rate_pct`、`cycle.gvm_pct` | 近似对应 | 作为 memory latency 上下文输出 | 可支持或削弱 memory-latency 假设，但不能定位具体 stalled warp。 |
| `smsp__warp_issue_stalled_short_scoreboard.sum` | `profiler.shared_memory_efficiency_pct`、`profiler.avg_conflict_cycles_per_inst`、WSM load/store cycles | 近似对应 | 已输出 | C500 shared-memory conflict 指标可作为 short-scoreboard-like WSM pressure 代理。 |
| `smsp__warp_issue_stalled_barrier.sum` | CycleTrace `cycle.arrive` 和 synchronization count | 近似对应 | synchronization 已输出 | 只是 sync/arrive 事件或指令计数，不是等待 stall cycles。 |
| `smsp__warp_issue_stalled_membar.sum` | 无对应关系 | 无对应关系 | 未输出 | 当前产物没有 memory-order wait stall。 |
| `smsp__warp_issue_stalled_mio_throttle.sum` | WSM counters 和 `VLS Duty ratio` | 近似对应 | WSM 已输出；VLS 存在于 `metrics_all/key` | MIO throttle 语义不能直接迁移到 C500。 |
| `smsp__warp_issue_stalled_lg_throttle.sum` | `cycle.gvm_pct`、mcProfiler global read/write instructions、memory data flow global/generic_vl1c | 近似对应 | GVM 和 memory flow 已输出 | 表示 global-memory traffic，但不是 issue throttle。 |
| `smsp__warp_issue_stalled_math_pipe_throttle.sum` | AP duty：`ap_mte_duty_pct`、`ap_ste_duty_pct`、`ap_mma_duty_pct`；compute busy duty | 近似对应 | 已输出 | 能体现 compute-pipe pressure，但不是 throttle stall reason。 |
| `smsp__warp_issue_stalled_no_instruction.sum` | CycleTrace `cycle.nop_pct`、branch count/share | 近似对应 | NOP 和 branch 已输出 | 可提示 bubble/front-end/control overhead，但不能证明 no-instruction stall。 |
| `smsp__warp_issue_stalled_imc_miss.sum` | 无对应关系 | 无对应关系 | 未输出 | 当前没有 instruction-cache miss counter。 |
| `smsp__warp_issue_stalled_dispatch_stall.sum` | `profiler.instruction_throughput`、`profiler.instruction_throughput_efficiency_pct`、`profiler.isu_stall_summary`；CycleTrace issue density | 近似对应 | throughput 和 ISU stall 上下文已输出 | ISU stall layout 是 issue-side 上下文，不是 per-warp stall reason。 |
| `smsp__warp_issue_stalled_drain.sum` | WIF quartiles、average wave life、DPC imbalance | 近似对应 | 已输出 | 只是 aggregate tail/drain proxy。 |
| `smsp__warp_issue_stalled_not_selected.sum` | 无对应关系 | 无对应关系 | 未输出 | 当前没有 ready-but-not-selected wave 指标。 |

## Memory Path

| NCU 指标 | MACA / trace-report 指标 | 映射 | 报告状态 | 说明 |
|---|---|---|---|---|
| `dram__bytes_read.sum` | memory data flow `l2c_Gmemory_rd`；global read flow | 近似对应 | 通过 memory data flow 部分输出 | flow counter 未确认是 bytes，不能当作精确 DRAM bytes。 |
| `dram__bytes_write.sum` | memory data flow `l2c_Gmemory_wr`；global write flow | 近似对应 | 通过 memory data flow 部分输出 | 与 read 同样存在单位限制。 |
| `dram__throughput.avg.pct_of_peak_sustained_elapsed` | `profiler.achieved_bandwidth_gbs`、`profiler.roofline.hbm_usage_pct`；DNOC latency metrics | 近似对应 | 已输出 | 有 achieved GB/s 和 mcProfiler RoofLine HBM usage；DNOC metrics 仍是 latency 上下文。 |
| `lts__t_bytes.sum` | memory data flow `vl1c_l2c_rd/wr`；mcProfiler `L2C Read Instructions`、`L2C Write Instructions` | 近似对应 | memory data flow 已输出；raw L2C instructions 存在于 `metrics_all` | instruction/flow count 不是 NCU L2 bytes。 |
| NCU 源文档无直接锚点 | `profiler.global_memory_read_bytes`、`profiler.global_memory_write_bytes`、`profiler.dnoc_read_req`、`profiler.dnoc_write_req` | 近似对应 | 已输出 | 有用的 MACA traffic volume 上下文；不是 sectors/request 或 coalescing-quality 指标。 |
| `lts__t_sectors_srcunit_tex_op_read.sum` | 无对应关系 | 无对应关系 | 未输出 | 当前没有 sector-source counter。 |
| `l1tex__t_sectors_pipe_lsu_mem_global_op_ld.sum` | 无对应关系 | 无对应关系 | 未输出 | 当前没有 L1/VL1 sector count。 |
| `l1tex__t_sectors_pipe_lsu_mem_global_op_st.sum` | 无对应关系 | 无对应关系 | 未输出 | 当前没有 L1/VL1 sector count。 |
| `l1tex__t_requests_pipe_lsu_mem_global_op_ld.sum` | mcProfiler `Global Read Instructions`、memory data flow `global_kernel_rd`、`generic_vl1c_rd` | 近似对应 | data flow 已输出；raw scalar 可用 | 是 instruction/flow entry，不是 NCU request。 |
| `smsp__sass_average_data_bytes_per_sector_mem_global_op_ld.pct` | 无对应关系 | 无对应关系 | 未输出 | 当前产物不能支持 global coalescing/sector utilization 判断。 |
| `l1tex__data_bank_conflicts_pipe_lsu_mem_shared_op_ld.sum` | `shared memory access efficiency`、`average conflict cycles per instruction`、`average cycles per load instruction`、shared load instructions | 近似对应 | 已输出 | C500 输出 efficiency 和 conflict-cycle severity，不是 NVIDIA load bank-conflict event count。 |
| `l1tex__data_bank_conflicts_pipe_lsu_mem_shared_op_st.sum` | `shared memory access efficiency`、`average conflict cycles per instruction`、`average cycles per store instruction`、shared store instructions | 近似对应 | 已输出 | 与 load path 同类。 |
| NCU 源文档无直接锚点 | `VL1 Hit Rate`、`L2C Hit Rate`、`Constant SL1 Hit Rate` | 近似对应 | 已输出 | cache hit 上下文；NCU 通常从 L1TEX/LTS counter 推导类似信号。 |
| NCU 源文档无直接锚点 | `Dnoc Read Average Latency`、`Dnoc Read Latency Histogram` | 近似对应 | 已输出 | C500-specific memory-latency 证据，可用于排除或确认 memory latency。 |
| NCU 源文档无直接锚点 | `VL1 partition stall cycles layout` | 近似对应 | 已输出 | C500 partition-balance 信号；源 NCU 表中没有单一对应指标。 |

## Tensor Core、TMA 与架构信号

| NCU 信号 | MACA / trace-report 指标 | 映射 | 报告状态 | 说明 |
|---|---|---|---|---|
| Tensor pipe active below expectation | `profiler.ap_mma_duty_pct`、CycleTrace `cycle.mma_pct`、mcProfiler MMA 硬件指令 count/share、`profiler.compute_instruction_cycles_summary.mma_cycles_pct`、RoofLine peak context | 近似对应 | 已输出 | C500 上判断 tensor-core underuse 的主证据。 |
| TMA/mbarrier wait hotspot | 仅有 CycleTrace synchronization/arrive count | 近似但较弱 | synchronization count 已输出 | 当前没有 TMA/mbarrier wait duration 或 source hotspot。 |
| TMEM load/store source hotspot | 无对应关系 | 无对应关系 | 未输出 | C500 当前产物没有 NVIDIA TMEM-specific source counter。 |
| 2-SM cooperative imbalance | `profiler.dpc_compute_cycles`、`profiler.dpc_compute_imbalance_pct` | 近似对应 | 已输出 | DPC compute imbalance 是 C500 aggregate balance 证据，不等价于 NVIDIA CTA-pair/cluster 证据。 |
| CLC/tail-wave evidence | CycleTrace WIF quartiles/max、`profiler.average_wave_life_cycles`、achieved/dispatched waves | 近似对应 | 已输出 | aggregate tail-effect proxy；没有 PM timeline。 |
| WGMMA wait group hotspots on Hopper | 无对应关系 | 无对应关系 | 未输出 | NVIDIA Hopper-specific 信号当前没有 C500 对应项。 |
| C500-specific MMA path | 由 AP MMA duty 和 MMA instruction share 推断 C500 MMA lowering path | 近似对应 | 已输出 | 报告能发现 MMA 使用率低，但确认 lowering path 仍需要源码或汇编。 |

## Source Counters And PM Sampling

| NCU 证据 | MACA / trace-report 指标 | 映射 | 报告状态 | 说明 |
|---|---|---|---|---|
| Source line with high stall samples | 无对应关系 | 无对应关系 | 未输出 | 当前产物不能指出 source-line hotspot。 |
| Inline PTX line with high samples | 无对应关系 | 无对应关系 | 未输出 | 没有 source/PTX/SASS attribution。 |
| PM-sampling timeline shows late low occupancy | WIF quartiles、DPC imbalance、average wave life | 近似但较弱 | 已输出 | 只能作为 aggregate proxy，不能证明 timeline shape。 |
| PM-sampling timeline shows alternating TMA/MMA idle phases | 无对应关系 | 无对应关系 | 未输出 | 没有 per-AP 或 per-pipeline timeline。 |
| One source line dominates long scoreboard | 无对应关系 | 无对应关系 | 未输出 | DNOC 和 hit-rate 可提示 memory latency，但不能定位 source line。 |

## PTX And SASS Analysis

| NCU PTX/SASS 信号 | MACA / trace-report 指标 | 映射 | 报告状态 | 说明 |
|---|---|---|---|---|
| scalar `ld.global` where vector load was expected | 无对应关系 | 无对应关系 | 未输出 | 需要汇编或源码证据。 |
| low bytes/sector plus scalar loads | 无对应关系 | 无对应关系 | 未输出 | 当前缺少 bytes/sector 和 SASS load width。 |
| local memory load/store in SASS | mcTracer private memory 字段；mcProfiler private read/write flow | 近似对应 | 已输出 | 可提示 private-memory 使用，但没有汇编时不能证明 register spill。 |
| repeated `cvt` / pack / unpack | 无对应关系 | 无对应关系 | 未输出 | 需要 PTX/SASS/source 证据。 |
| extra `bar.sync` / mbarrier ops | CycleTrace `arrive` 和 synchronization count | 近似对应 | 已输出 | 只是 sync-related event 计数，不是 source-level barrier 指令或 wait time。 |
| `tcgen05` / `wgmma` sparse or absent in tensor kernel | `cycle.mma_pct`、`profiler.ap_mma_duty_pct`、MMA 硬件指令 share | 近似对应 | 已输出 | 能发现 tensor instruction 低，但具体 mnemonic 需要反汇编。 |
| large instruction window with `imc_miss` | 无对应关系 | 无对应关系 | 未输出 | 当前没有 I-cache miss 或 code-size metric。 |
| unexpected cache modifiers | 无对应关系 | 无对应关系 | 未输出 | 需要 SASS/PTX。 |

## Bottleneck To Edit Mapping

| NCU 主导信号 | MACA / trace-report 对应信号 | 映射 | 当前报告用途 |
|---|---|---|---|
| Long scoreboard | DNOC latency average/histogram、L2C hit rate、GVM share、achieved bandwidth | 近似对应 | 用于评估或排除 DRAM latency/bandwidth 为主因。 |
| Short scoreboard + bank conflicts | shared-memory efficiency、average conflict cycles per instruction、WSM load/store cycles | 近似对应 | 直接用于 bank-conflict 诊断。 |
| Barrier/membar/mbarrier | synchronization/arrive counts | 近似但较弱 | 作为 instruction-distribution 上下文输出；没有 wait 指标时不能强诊断。 |
| Tensor pipe low | AP MMA duty、CycleTrace MMA share、MMA 硬件指令 share | 近似对应 | 直接用于 tensor-core underuse 诊断。 |
| DRAM high, SM low | achieved bandwidth、RoofLine HBM usage、DNOC latency、GVM share、AP duty ratios、L2C hit rate | 近似对应 | 用于 coarse memory-vs-compute 分类。 |
| L2 high, DRAM low | L2C duty、L2C hit rate、memory data flow `vl1c_l2c_rd/wr` | 近似对应 | 已可用；只有 L2 pressure 具有诊断价值时才进入 headline。 |
| Both SM and memory low | real IPC、WIF distribution、achieved/dispatched waves、effective occupancy、DPC imbalance | 近似对应 | 用于 latency/occupancy/tail 假设。 |
| I-cache/no-instruction | CycleTrace NOP share、branch share | 近似但较弱 | NOP/branch 已输出，但没有 I-cache metric。 |
| Tail waves | WIF P75/P25、WIF max、achieved/dispatched waves、average wave life、DPC imbalance | 近似对应 | 作为 aggregate tail/balance proxy。 |

## MACA 中没有清晰 NCU 单指标锚点的指标

这些指标对 C500 报告有用，但不能映射到源 NCU `metrics.md` 中的某一个单独指标。

| MACA / trace-report 指标 | 作用 | NCU 映射状态 |
|---|---|---|
| CycleTrace `MTE`、`STE`、`BSM`、`GVM`、`LDU` instruction categories | C500 instruction-mix 分解 | 只能近似对应；NCU 使用 NVIDIA pipe/SASS counter family。 |
| CycleTrace `gvm_600cy_peak` | 600-cycle issue window 的 GVM pressure proxy | 源 NCU 表中无对应关系。 |
| CycleTrace WIF raw distribution | wave concurrency 和 tail proxy | 可近似对应 active warp/occupancy/tail evidence，但不是直接 NCU 指标。 |
| mcProfiler `DPC Computing cycles comparison` | C500 DPC compute balance | 源 NCU 表中没有单一对应指标。 |
| mcProfiler `VL1 partition stall cycles layout` | C500 VL1 partition imbalance | 源 NCU 表中没有单一对应指标。 |
| mcProfiler `Memory Data Flow Chart` | C500 memory-flow path count | 可近似对应 memory workload analysis，但单位不同于 NCU bytes/sectors。 |
| mcProfiler `Dnoc Read Latency Histogram` | C500 DNOC latency-tail evidence | 可近似对应 memory-latency diagnosis，但源 NCU 表中没有单一对应指标。 |
| mcProfiler `case_VL1_I`、`case_L2C_I`、`hbm_usage_pct`、`vl1_usage_pct`、`l2c_usage_pct`、RoofLine peak fields | cache-level roofline intensity、usage 和 peak-line context | 源 NCU 表中没有单一对应指标，但可作为 roofline 上下文。 |

## 结论

最强的跨工具对应关系如下：

| 诊断维度 | NCU 锚点 | MACA / trace-report 锚点 |
|---|---|---|
| Launch identity and resources | `launch__*` 字段 | mcTracer launch/resource 字段 |
| Tensor utilization | `sm__inst_executed_pipe_tensor.*` | AP MMA duty + MMA instruction share |
| Vector/ALU utilization | `sm__pipe_alu_cycles_active.*` | AP MTE/STE duty + MTE/STE instruction share |
| L2/cache pressure | `lts__*`、L1/L2 hit 派生指标 | L2C duty/hit、VL1/L2C memory flow |
| Shared-memory conflicts | `l1tex__data_bank_conflicts_*` | shared-memory efficiency + conflict cycles/inst |
| Occupancy/resource bound | `Occupancy`、`LaunchStats` | mtreg/shared occupancy + effective occupancy |
| Tail/balance | PM sampling / active warps / launch waves | WIF quartiles + achieved/dispatched waves + DPC imbalance |

当前主要无对应关系的区域是：NVIDIA-style per-warp stall reason、source-line/PC attribution、PM-sampling timeline、SASS/PTX instruction evidence、sector/request coalescing quality，以及精确 DRAM/L2 byte-sector counter。
