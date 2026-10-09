# C500 指标名称参考

本文档只描述当前 `scripts/trace_profile_pipeline.py` 会解析、输出或在报告中引用的指标。结论必须引用 `analysis/metrics_all_<tag>.json`、`analysis/metrics_key_<tag>.json` 或 `REPORT_<tag>.md` 中真实存在的字段。

## 读取门槛

常规诊断先读 `reference/quick-diagnose.md`。只有字段未知、字段冲突、旧口径风险或 `metrics_key` 不够用时，才读取本文；优先定位具体字段，不默认全文加载。

## 输出文件

| 文件 | 内容 | 使用场景 |
|---|---|---|
| `analysis/metrics_all_<tag>.json` | 完整解析结果、bound 证据、诊断列表、metric coverage | 诊断依据和二次分析 |
| `analysis/metrics_key_<tag>.json` | 稳定核心指标、采集 scope 和覆盖度摘要 | 多版本对比、快速检查 |
| `analysis/digest_<tag>.md` | 简版指标摘要 | 快速人工阅读 |
| `REPORT_<tag>.md` | 最终诊断报告 | 交付物 |

## 有效产物边界

标准分析要求 `artifacts/c-trace_output_dpc_<primary CYCLE_TRACE_DPC_ID>.json` 可读，包含非空 `traceEvents`，且至少有MTE/STE/MMA/BSM/GLOBAL/ARRIVE/LDU 硬件类别事件。`collection_manifest.json` 中的 `invalid_required` 非空时，不应继续生成高置信诊断。mcProfiler 是否是一键采集必需产物由 `REQUIRE_MCPROFILER` 决定；默认 `false` 时缺少 mcProfiler 仍可生成报告，但相关 duty、IPC、cache、bank conflict 和 Roofline 字段必须写成不可用。
多 DPC 采集时，`CYCLE_TRACE_DPC_ID` 中请求的每个 `c-trace_output_dpc_*.json` 都是一键采集必需产物并归档到 `artifacts/`，但 `cycle.*` 指标只来自 primary DPC；报告会列出已归档 DPC 文件和被分析的 primary 文件。
`split-usecases` 生成的派生报告复用同一套字段：`launch.*` 来自 filtered mcTracer launch，
`cycle.*` 来自 filtered CycleTrace 时间段；若匹配到单个 mcProfiler per-kernel occurrence，
`profiler.*` 通过 `collection_manifest.json.mcprofiler_reference` 读取父级 occurrence 对应的
profiler JSON/TXT JSON；若没有匹配 occurrence，则 profiler
字段保持不可用或命令级边界。usecase 拆分匹配详情在
`usecases/manifest_<tag>.json` 中，不属于主 `metrics_all_<tag>.json` 字段。只有 manifest 中
match 状态、confidence、数量对应和 signature 区分能力满足 `reference/13-usecase-split.md` 的
可信度判定时，派生报告中的 `launch.*`、`cycle.*` 与可用的 `profiler.*` 才可视为完全对齐；
否则必须降级为排查材料或重采单算子单用例数据。

## 核心字段对照

### Kernel 与 Launch

| JSON 字段 | 来源 | 报告位置 | 含义 |
|---|---|---|---|
| `kernel.name` / `launch.kernel_name` | mcTracer | Profiling setup / Occupancy | kernel 名称 |
| `kernel.grid` / `kernel.block` | mcTracer | Profiling setup / Occupancy | launch geometry |
| `launch.registers_per_thread` | mcTracer | Occupancy | 每线程寄存器数 |
| `launch.static_shared_bytes` | mcTracer | Occupancy | 静态 shared memory 字节数 |
| `launch.dynamic_shared_bytes` | mcTracer | metrics_all | 动态 shared memory 字节数 |
| `launch.private_per_thread` / `launch.private_total` | mcTracer | Bank Conflict & Private Memory | private memory 静态报告 |
| `launch.mtreg_occupancy_pct` | mcTracer | Occupancy | mtreg 限制下的 occupancy |
| `launch.shared_memory_occupancy_pct` | mcTracer | Occupancy | shared memory 限制下的 occupancy |
| `launch.effective_occupancy_pct` | pipeline 派生 | Headline / Occupancy | 当前仅取 mtreg/shared-memory 两者较低值；不含 streg；`0` 视为不可用/矛盾信号而非低 occupancy 结论 |
| `launch.kernel_event_duration` | mcTracer | metrics_all | host/API trace 中 kernel 事件持续时间；不作为 device headline |
| `launch.top_api_calls_by_duration` | mcTracer | metrics_all | API 开销排查上下文 |

### Collection Scope

| JSON 字段 | 来源 | 报告位置 | 含义 |
|---|---|---|---|
| `collection_scope.cycle_trace_kernel_filter_enabled` | active config / pipeline | Profiling setup | 是否设置了 `CYCLE_TRACE_KERNEL_NAME` 并启用 CycleTrace 单 kernel scope |
| `collection_scope.cycle_trace_kernel_name` | active config / pipeline | Profiling setup | 本次 CycleTrace 指标对应的目标 kernel name |
| `collection_scope.cycle_trace_kernel_repeat` | active config / pipeline | Profiling setup | 写入 `cycleTrace.kernels[0].repeat` 的 repetition；未设置时为 `-1` |
| `collection_scope.cycle_trace_sample_mode` | active config / pipeline | Profiling setup | 单 kernel scope 下为 `C` |
| `collection_scope.cycle_trace_scope_note` | pipeline 派生 | Profiling setup | 说明 CycleTrace 指标和 mcTracer launch/resource 字段被收敛到目标 kernel |
| `collection_scope.mcprofiler_scope` | collection manifest / pipeline | Profiling setup | `command-level`、`per-kernel`、`per-kernel-multiple-occurrences`、`per-usecase-ref`、`per-kernel-unmatched` 或 `per-usecase-multiple-occurrences`；说明 profiler 指标是否已收敛到目标 kernel/usecase |
| `collection_scope.mcprofiler_occurrence_count` | mcProfiler per-kernel manifest / pipeline | Profiling setup | 目标 kernel 的 mcProfiler occurrence 数量；多 occurrence 需要 `split-usecases` 才能进入单 usecase 报告 |
| `collection_scope.mcprofiler_scope_note` | pipeline 派生 | Profiling setup | 说明 mcProfiler 是命令级、单 occurrence per-kernel、usecase 引用式单 occurrence，还是多 occurrence 需拆分或不可用 |

### CycleTrace 指令与 Wave

指令分布必须以 CycleTrace `cat` 为主分类，因为 `cat` 表示事件在哪类 C500 硬件上执行。
`name` 只作为子事件补充。

| JSON 字段 | 来源 | 报告位置 | 含义 |
|---|---|---|---|
| `cycle.total_instructions` | CycleTrace | Key Metrics | 硬件类别指令总数，不含 wave start/end 等 metadata |
| `cycle.category_counts` / `cycle.category_pcts` | CycleTrace `cat` | Instruction Distribution | `MTE`、`STE`、`MMA`、`BSM`、`GLOBAL`、`ARRIVE`、`LDU` 分类计数和占比 |
| `cycle.raw_name_counts` | CycleTrace `name` | Instruction Distribution 子事件 | `S_NOP`、`Branch`、`GVM Load/Store`、`Synchronization` 等 name 级审计 |
| `cycle.mte_pct` | CycleTrace `cat=MTE` | Headline / Evidence | MTE/vector 计算路径占比 |
| `cycle.mma_pct` | CycleTrace `cat=MMA` | Headline / Evidence | MMA/tensor 路径占比 |
| `cycle.ste_pct` | CycleTrace `cat=STE` | Instruction Distribution | 标量/控制类硬件类别占比 |
| `cycle.bsm_pct` | CycleTrace `cat=BSM` | Key Metrics / Instruction Distribution | WSM 相关硬件类别占比 |
| `cycle.gvm_pct` | CycleTrace `cat=GLOBAL` | Evidence / Memory | GVM global load/store 类别占比 |
| `cycle.arrive_pct` | CycleTrace `cat=ARRIVE` | Key Metrics / Instruction Distribution | 同步/arrival 类别占比 |
| `cycle.ldu_pct` | CycleTrace `cat=LDU` | Key Metrics / Instruction Distribution | LDU 类别占比 |
| `cycle.nop_pct` | CycleTrace `name=S_NOP` | Key Metrics / Latency 诊断 | issue 空洞信号；不是真实 stall 周期 |
| `cycle.branch_pct` | CycleTrace `name=Branch` | metrics_all | 分支子事件占比 |
| `cycle.sync_count` | CycleTrace `name=Synchronization` | metrics_all / Instruction Distribution | 同步事件计数；不是等待 stall cycle |
| `cycle.span_cycles` / `kernel.span_cycles` | CycleTrace wave lifecycle | Evidence | wave 生命周期跨度 |
| `cycle.wif_mean/p25/p50/p75/max` | CycleTrace wave 重建 | Occupancy | raw WIF 分布；counter scope 未确认前不要换算为 waves/AP |
| `cycle.gvm_600cy_peak` | pipeline 派生 | Key Metrics / Memory | 600-cycle 窗口内 GVM issue 峰值，是压力代理 |
| `cycle.issue_density_inst_per_cycle` | pipeline 派生 | metrics_all | CycleTrace issue density sanity check；不要替代真实 IPC |

### mcProfiler SOL / IPC / Instruction

| JSON 字段 | mcProfiler 原始名 | 报告位置 | 含义 |
|---|---|---|---|
| `profiler.total_instructions` | `Total Instructions` | metrics_all | mcProfiler 指令总数 |
| `profiler.compute_instructions` | `Compute Instructions` | metrics_all | 计算类指令数 |
| `profiler.memory_instructions` | `Memory Instructions` | metrics_all | 访存类指令数 |
| `profiler.total_cycles` | `Total Cycles` | metrics_all | 总 cycle |
| `profiler.ap_active_cycles` | `AP active cycles` | SOL / Hardware Duty | AP 活跃 cycle |
| `profiler.ap_mte_duty_pct` | `AP MTE Duty ratio` | Headline / SOL | MTE pipe duty |
| `profiler.ap_ste_duty_pct` | `AP STE Duty ratio` | SOL | STE pipe duty |
| `profiler.ap_mma_duty_pct` | `AP MMA Duty ratio` | Headline / SOL | MMA pipe duty |
| `profiler.vls_duty_pct` | `VLS Duty ratio` | Bound evidence / metrics_all | Vector load/store duty |
| `profiler.l2c_duty_pct` | `L2C Duty ratio` | Bound evidence / metrics_all | L2C duty |
| `profiler.real_ipc` | `instruction per cycle` | SOL / Key Metrics | mcProfiler 真实 IPC |
| `profiler.instruction_throughput` | `instruction throughput` | SOL | 指令吞吐率 |
| `profiler.instruction_throughput_efficiency_pct` | `instruction throughput efficiency` | SOL / Key Metrics | 指令吞吐效率 |
| `profiler.instructions_per_ap` | `instructions per AP` | SOL / Key Metrics | 平均每 AP 指令数 |
| `profiler.avg_cycles_per_instruction` | `average cycles per instruction` | SOL / Hardware Duty | 平均每条指令 cycle |
| `profiler.avg_latency_all_stages_per_instruction` | `average latency of all stages per instruction` | SOL / Hardware Duty | 全阶段平均指令 latency |
| `profiler.compute_instruction_cycles_rate` | `Compute Instructions Cycles Rate` | SOL / Hardware Duty | MTE/MMA compute-cycle 图表原始项 |
| `profiler.compute_instruction_cycles_summary` | pipeline 派生 | SOL / Key Metrics | compute-cycle 总量、MTE/MMA cycle share |
| `profiler.isu_stall_cycles` | `ISU stall cycles layout` | SOL / Hardware Duty | ISU stall cycle 图表原始项 |
| `profiler.isu_stall_summary` | pipeline 派生 | Evidence / Key Metrics | ISU stall 总量、最大 stall bucket 和 share |
| `profiler.compute_inst_busy_duty_pct` | `Compute Instructions busy Duty` | SOL | 计算指令 busy duty |
| `profiler.ap_busy_duty_pct` | `AP busy Duty` | SOL caveat | 当前单位尺度有歧义，只作为上下文 |
| `profiler.hardware_instruction_counts` | `Instructions Comparison` | Instruction cross-check | mcProfiler 硬件指令类别计数 |
| `profiler.hw_mte_pct` / `hw_mma_pct` 等 | pipeline 派生 | metrics_all | mcProfiler instruction mix 百分比 |

### Memory Hierarchy

| JSON 字段 | mcProfiler 原始名 | 报告位置 | 含义 |
|---|---|---|---|
| `profiler.vl1_hit_rate_pct` | `VL1 Hit Rate` | Memory Hierarchy | VL1 命中率；需结合 VL1 配置解释 |
| `profiler.l2c_hit_rate_pct` | `L2C Hit Rate` | Evidence / Memory | L2C 命中率，适合作 headline cache 信号 |
| `profiler.sl1_hit_rate_pct` | `Constant SL1 Hit Rate` | Memory Hierarchy | 常量/标量 L1 命中率 |
| `profiler.dnoc_read_average_latency` | `Dnoc Read Average Latency` | Memory Hierarchy | DNOC 读平均延迟 |
| `profiler.dnoc_read_req` / `profiler.dnoc_write_req` | `Dnoc Read Req` / `Dnoc Write Req` | Memory Hierarchy / Key Metrics | DNOC read/write request 数 |
| `profiler.dnoc_latency_histogram` | `Dnoc Read Latency Histogram` | Memory Hierarchy | DNOC 延迟分布；报告派生 `>512-cycle share` |
| `profiler.dnoc_latency_histogram_pct` | pipeline 派生 | Memory Hierarchy | DNOC latency bucket 百分比 |
| `profiler.vl1_partition_stalls` | `VL1 partition stall cycles layout` | Memory Hierarchy | VL1 partition stall 分布 |
| `profiler.vl1_partition_stall_summary` | pipeline 派生 | Memory Hierarchy | VL1 partition min/avg/max/spread |
| `profiler.memory_data_flow` | `Memory Data Flow Chart` | Memory Hierarchy | global/shared/private/constant/cache 层级流量 |
| `profiler.memory_data_flow.global_kernel_rd/wr` | Memory Data Flow | Memory data flow | kernel global read/write 指令 |
| `profiler.memory_data_flow.private_kernel_rd/wr` | Memory Data Flow | Private Memory | private memory 硬件流量 |
| `profiler.memory_data_flow.shared_kernel_rd/wr` | Memory Data Flow | Memory data flow | shared memory read/write 指令 |
| `profiler.global_memory_read_bytes` / `global_memory_write_bytes` | `Global Memory Read bytes` / `Global Memory Write bytes` | Memory Hierarchy / Key Metrics | mcProfiler global memory byte counters；不要等同 NCU sectors/request |
| `profiler.global_read_instructions` / `global_write_instructions` | `Global Read Instructions` / `Global Write Instructions` | Memory Hierarchy / Key Metrics | global read/write 指令数 |
| `profiler.vl1_read_instructions` / `vl1_write_instructions` | `VL1 Read Instructions` / `VL1 Write Instructions` | Memory Hierarchy | VL1 read/write 指令数 |
| `profiler.l2c_read_instructions` / `l2c_write_instructions` | `L2C Read Instructions` / `L2C Write Instructions` | Memory Hierarchy | L2C read/write 指令数 |
| `profiler.constant_read_instructions` / `constant_read_sl1_instructions` / `constant_read_l2_instructions` | Constant read scalars | Memory Hierarchy | constant read path 指令数 |

当前 artifacts 不提供 sectors/request 或 useful-bytes/sector 等价字段，不能直接判断 global
coalescing 质量。

### WSM / Bank Conflict / Atomic

| JSON 字段 | mcProfiler 原始名 | 报告位置 | 含义 |
|---|---|---|---|
| `profiler.shared_memory_load_instructions` | `load instructions` | Bank Conflict | WSM load 指令数 |
| `profiler.shared_memory_store_instructions` | `store instructions` | Bank Conflict | WSM store 指令数 |
| `profiler.shared_memory_efficiency_pct` | `shared memory access efficiency` | Headline / Bank Conflict | WSM 无冲突效率 |
| `profiler.avg_conflict_cycles_per_inst` | `average conflict cycles per instruction` | Headline / Bank Conflict | 每条 WSM 指令平均冲突 cycle |
| `profiler.avg_cycles_per_load` | `average cycles per load instruction` | Bank Conflict | 每条 WSM load 平均 cycle |
| `profiler.avg_cycles_per_store` | `average cycles per store instruction` | Bank Conflict | 每条 WSM store 平均 cycle |
| `profiler.avg_latency_per_load_instruction` / `avg_latency_per_store_instruction` / `avg_latency_per_atomic_instruction` | latency per load/store/atomic | Bank Conflict & Private Memory | load/store/atomic 平均 latency |
| `profiler.avg_cycles_per_atomic_instruction` | `average cycles per atomic instruction` | Bank Conflict & Private Memory | atomic 平均 cycle |
| `profiler.all_scalars` 中包含 `atomic` 的字段 | mcProfiler scalar | Bank Conflict & Private Memory | atomic 压力；报告汇总为总 atomic scalar count |

### Wave / DPC Balance

| JSON 字段 | mcProfiler 原始名 | 报告位置 | 含义 |
|---|---|---|---|
| `profiler.achieved_waves` | `Achieved waves` | Occupancy / Key Metrics | 实际达成 wave 数 |
| `profiler.dispatched_waves` | `Dispatched waves` | Occupancy / Key Metrics | dispatch wave 数 |
| `profiler.average_wave_life_cycles` | `Average Wave life cycles` | Occupancy | 平均 wave 生命周期 |
| `profiler.workgroups` | `WORKGROUPS` | Occupancy | workgroup 数 |
| `profiler.dpc_compute_cycles` | `DPC Computing cycles comparison` | Occupancy / Balance | 各 DPC 计算 cycle |
| `profiler.dpc_compute_imbalance_pct` | pipeline 派生 | Evidence / Key Metrics | DPC compute cycles 不均衡比例 |

### Empirical Roofline

| JSON 字段 | mcProfiler / pipeline 字段 | 报告位置 | 含义 |
|---|---|---|---|
| `profiler.achieved_flops_tflops` | `case_flops` | Roofline / Key Metrics | 经验达成 FLOPS |
| `profiler.achieved_bandwidth_gbs` | `case_bandwith` | Roofline / Key Metrics | 经验达成带宽，保留工具原始拼写含义 |
| `profiler.achieved_intensity_flop_per_byte` | `case_I` | Roofline / Key Metrics | DRAM 层经验操作强度 |
| `profiler.roofline.case_VL1_I` | `case_VL1_I` | Roofline / Key Metrics | VL1 层操作强度 |
| `profiler.roofline.case_L2C_I` | `case_L2C_I` | Roofline / Key Metrics | L2C 层操作强度 |
| `profiler.roofline.MAX_Flops_base`、`MAX_Bandwith`、`MAX_I` | mcProfiler roofline | metrics_all | 工具输出的峰值/拐点上下文 |
| `profiler.roofline.all_ops`、`all_memacs`、`vl1_memacs`、`l2c_memacs`、`during`、`core_clk`、`mc_clk` | mcProfiler roofline | Roofline / metrics_all | Roofline 原始上下文 |
| `profiler.roofline.peak_trans_tflops`、`peak_fma_tflops`、`peak_mma_fp16_tflops`、`peak_int8_tflops` | pipeline 派生自 `MAX_Flops_base` | Roofline / Key Metrics | 与 mcProfiler RoofLine 图水平线一致的峰值上下文 |
| `profiler.roofline.hbm_usage_pct` | `case_bandwith / MAX_Bandwith` | Roofline / Key Metrics | HBM bandwidth usage；示例图为 15.94% |
| `profiler.roofline.vl1_effective_bandwidth_gbs` / `l2c_effective_bandwidth_gbs` | `case_flops * 1000 / case_<level>_I` | Roofline / Key Metrics | VL1/L2C 等效带宽，用于解释 cache-level usage |
| `profiler.roofline.vl1_usage_pct` / `l2c_usage_pct` | effective bandwidth / C500 peak bandwidth | Roofline / Key Metrics | 使用 C500 架构带宽常量和 `core_clk` 复现 mcProfiler 图例 usage；示例图为 2.68% / 14.47% |
| `profiler.roofline.selected_compute_peak_name` / `selected_compute_peak_tflops` | pipeline 派生 | Roofline / Key Metrics | Roofline placement 选用的 compute peak；tensor-like 且有 MMA 证据时用 MMA-FP16，否则默认 FMA |
| `profiler.roofline.hbm_ridge_intensity_flop_per_byte` | `selected_peak_tflops * 1000 / MAX_Bandwith` | Roofline / Key Metrics | HBM ridge point，低于该 AI 为 memory-region，高于等于该 AI 为 compute-region |
| `profiler.roofline.hbm_memory_roof_tflops` / `hbm_selected_roof_tflops` | `case_I * MAX_Bandwith / 1000` / `min(selected_peak, memory_roof)` | Roofline / Key Metrics | HBM memory roof 和当前 AI 下的选中 roof |
| `profiler.roofline.hbm_region` / `roofline_bound` / `roofline_bound_confidence` | pipeline 派生 | Roofline / Key Metrics | 公式化 Roofline placement/bound；与 C500 heuristic bound 分开解释 |
| `profiler.roofline.hbm_achieved_vs_roof_pct` | `case_flops / hbm_selected_roof_tflops` | Roofline / Key Metrics | 当前点距离选中 roof 的比例；低于阈值时输出 `under-roof` |
| `profiler.roofline.compute_gap_pct` | `100 - case_flops / selected_compute_peak_tflops` | Roofline / Key Metrics | 到 selected compute peak 的经验 headroom；越低越接近 peak |
| `profiler.roofline.hbm_bandwidth_gap_pct` | `100 - case_bandwith / MAX_Bandwith` | Roofline / Key Metrics | 到 HBM peak bandwidth 的经验 headroom；越低越接近 HBM roof |
| `profiler.roofline.selected_roof_gap_pct` | `100 - case_flops / hbm_selected_roof_tflops` | Roofline / Key Metrics | 到当前 AI 下选中 HBM roof 的经验 headroom |
| `profiler.roofline.latency_or_efficiency_gap_pct` | `selected_roof_gap_pct` when gap >= 50 else `N/A` | Roofline / Key Metrics | under-roof 明显时的延迟/效率排查信号，不是独立硬件计数器 |
| `profiler.roofline.gap_hint` | pipeline 派生 | Roofline / Evidence | Roofline gap 的优先排查提示；不替代 C500 heuristic bound |

没有外部 operator/shape 公式时，只使用 mcProfiler 经验字段，不补造理论 FLOP/Byte。Roofline gap 是经验 headroom，用于解释“离 roof 还有多远”，不直接改变 `bound.primary` 的 C500 heuristic 分类。

## Bound 分类字段

| JSON 字段 | 含义 |
|---|---|
| `bound.method` | 固定为 `c500-heuristic`，表示该 `bound` 对象是 C500 硬件症状启发式分类，不是 Roofline bound |
| `bound.mode` | `coarse` 或 `detailed` |
| `bound.type` | coarse 下为 `compute`、`memory`、`latency`、`occupancy`、`mixed`、`unclear` 之一；detailed 下为复合标签 |
| `bound.primary` | 主类，coarse 和 detailed 都会输出 |
| `bound.signals` | coarse 触发的信号列表；`mixed` 时包含多个，`unclear` 时为空 |
| `bound.evidence` | 用于分类的稳定证据字段和值 |
| `bound.rationale` | 分类理由列表 |
| `bound.thresholds` | 当前脚本使用的阈值说明 |

默认 `coarse` 四类判断以 `bound.thresholds` 为准。当前脚本阈值为：

- `tensor_underuse`: kernel 名匹配 tensor 路径，且 `cycle_mma_pct < 1`、`ap_mma_duty_pct < 3`
- `mte_path`: `cycle_mte_pct > 30` 或 `ap_mte_duty_pct > 15`
- `bank_conflict`: `avg_conflict_cycles_per_inst > 0.5`，或有 WSM 活动且
  `0 < shared_memory_efficiency_pct < 80`
- `low_occupancy`: `0 < effective_occupancy_pct < 25`
- `memory_pressure`: `gvm_pct > 25`、`vls/l2c duty > 50`，或
  `l2c_hit_rate_pct < 80` 且有 GVM/VLS/L2C 活动佐证
- `latency_signal`: memory pressure 不成立时，`nop_pct > 10` 或 `real_ipc < 10`
- `mixed`: 多个 coarse 信号同时触发，不强制压成单一 bound
- `unclear`: 没有 coarse 信号触发，报告证据和数据边界，不强制归为 compute

若证据冲突或缺少 mcProfiler，报告必须把结论降级并写入数据边界。

## 覆盖度字段

| JSON 字段 | 含义 |
|---|---|
| `metric_coverage.summary.reported_metric_groups` | 直接进入 `REPORT_<tag>.md` 的指标组数 |
| `metric_coverage.summary.parsed_not_promoted_groups` | 已解析但未进入主报告正文的指标组数 |
| `metric_coverage.summary.unavailable_dimensions` | 当前 artifacts 不支持的分析维度数 |
| `metric_coverage.reported_metric_groups` | 每个已报告指标组的字段、用途 |
| `metric_coverage.parsed_not_promoted` | 未提升字段、作用和未提升原因 |
| `metric_coverage.unavailable_dimensions` | 不可支持维度、影响和所需补充 artifact |

## 不要使用的旧口径

- 不再使用 `ipc_est` 作为 IPC；真实 IPC 使用 `profiler.real_ipc`。
- 不用 CycleTrace `dur=4` 推导真实执行延迟。
- 不用 `name` 作为 Instruction Distribution 主分类；主分类使用 `cat`。
- 不把 `AP busy Duty` 作为 headline bound 依据，当前只作为单位敏感上下文。
- 不声称 C500 有 NCU 风格 per-warp stall reason、完整 per-PC stall hotspot 或 coalescing sectors/request；
  若 source-latency 可用，可以声称 CycleTrace/asm modeled source-line attribution 和 top
  modeled instruction，但不能改写成 wall-clock stall share。
- 不声称有完整 PM-sampling/per-AP 利用率时间线；可使用 CycleTrace span、WIF quartiles、
  GVM 600-cycle peak、DPC compute cycles、achieved/dispatched waves 作为有限事件时间线和
  aggregate balance proxy。

## 相关文档

- `reference/04-analysis-dimensions.md`：6 维度分析框架，定义各维度可用字段和判断边界。
- `reference/06-diagnosis-playbook.md`：从指标组合映射到可执行修改。
- `reference/C500-architecture.md`：需要理解 C500 硬件并行性、存储子系统或与 A100 对比时读取。
