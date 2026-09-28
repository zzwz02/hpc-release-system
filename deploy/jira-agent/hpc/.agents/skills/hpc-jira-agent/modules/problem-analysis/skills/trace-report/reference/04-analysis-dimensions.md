# C500 6 维度分析框架

按以下顺序检查。报告中的结论必须引用 `analysis/metrics_all_<tag>.json` 或 `REPORT_<tag>.md` 中真实存在的字段；缺失的指标只能写成数据边界，不能生成强结论。

## 读取门槛

本文是 Step 3 诊断基础文档。只有 `collect-run` 已输出 `workflow_state: needs_llm_followup`，且已进入 `reference/quick-diagnose.md` 后，才读取本文；不得在采集前为了提前准备诊断而读取。读取本文用于六维度字段、阈值和不可用边界检查，不代表可以全文读取 `metrics_all`、raw artifacts 或完整 logs；仍按 `quick-diagnose.md` 的上下文控制局部展开证据。

---
## C500架构
- [C500 芯片架构](./C500-architecture.md)

---

## Dimension 1 — Occupancy & Launch Geometry

**要回答的问题**: kernel 用了多少硬件资源？理论 occupancy、实际 wave 达成和 tail effect 是否一致？

**可用字段**:
- `launch.grid`、`launch.block`、`launch.registers_per_thread`
- `launch.static_shared_bytes`、`launch.dynamic_shared_bytes`
- `launch.mtreg_occupancy_pct`、`launch.shared_memory_occupancy_pct`、`launch.effective_occupancy_pct`
- `cycle.wif_mean`、`cycle.wif_p25`、`cycle.wif_p50`、`cycle.wif_p75`、`cycle.wif_max`
- `profiler.achieved_waves`、`profiler.dispatched_waves`、`profiler.average_wave_life_cycles`
- `profiler.dpc_compute_imbalance_pct`、`profiler.dpc_compute_cycles`

**判断条件**:
1. `0 < launch.effective_occupancy_pct < 25`：低 occupancy，是主要风险；结合 mtreg/shared-memory 哪个更低判断限制来源。
2. `launch.mtreg_occupancy_pct < launch.shared_memory_occupancy_pct`：mtreg 更可能是 occupancy limiter。
3. `launch.shared_memory_occupancy_pct < launch.mtreg_occupancy_pct`：shared memory 更可能是 occupancy limiter。
4. `cycle.wif_p25 > 0` 且 `cycle.wif_p75 / cycle.wif_p25 > 2`：存在 aggregate tail-effect 或负载不均。
5. `profiler.dpc_compute_imbalance_pct > 20`：DPC 间 compute cycles 分布不均，需要检查 grid/tile 分配。
6. `profiler.achieved_waves` 明显低于 `profiler.dispatched_waves`：调度达成异常或 occupancy 实际不足。
7. `profiler.average_wave_life_cycles` 明显升高且 IPC 低：可能是内存依赖、同步或 bank conflict 拉长 wave 生命周期。

**不可用边界**:
- streg occupancy limiter 当前 artifacts 不提供；需要编译器或汇编证据补充。
- WIF 是 raw CycleTrace 重建分布；counter scope 未确认前不要换算为 waves/AP。
- `launch.effective_occupancy_pct == 0` 不直接等同于 occupancy bottleneck；必须先视为
  mcTracer 资源字段不可用或互相矛盾，并结合 kernel 类型、寄存器、shared memory、achieved
  waves、WIF 和编译器/汇编证据复核。非 tensor kernel 不能仅凭 0 值建议降低资源。

---

## Dimension 2 — 指令分布

**要回答的问题**: 哪个硬件类别执行了主要指令？是否走到了预期计算路径？

**可用字段**:
- `cycle.category_counts`、`cycle.category_pcts`
- `cycle.mte_pct`、`cycle.ste_pct`、`cycle.mma_pct`、`cycle.bsm_pct`、`cycle.gvm_pct`
- `cycle.nop_pct`、`cycle.branch_pct`、`cycle.raw_name_counts`
- `profiler.hardware_instruction_counts`、`profiler.hardware_instruction_total`（用于交叉验证）

**判断条件**:
1. 主分类必须使用 CycleTrace `cat`：`MTE`、`STE`、`MMA`、`BSM`、`GLOBAL`、`ARRIVE`、`LDU`。`cat` 表示在哪类 C500 硬件上执行。
2. GEMM / matmul / Attention 等应走 tensor core 的 kernel：`cycle.mma_pct < 1` 且 `profiler.ap_mma_duty_pct < 3` → tensor core 未被使用。
3. GEMM / matmul / Attention 中 `cycle.mte_pct > 30` 且 `cycle.mma_pct < 1` → 计算回退到 vector/MTE 路径。ReLU、elementwise、copy/reduction 类算子的 MMA=0 不是问题。
4. `cycle.nop_pct > 10`：显著流水线气泡；结合 `profiler.real_ipc` 和 memory/WSM 指标定位原因。
5. `cycle.gvm_pct > 25`：global memory 指令占比高；进入 Memory Hierarchy 维度确认 cache/DRAM。
6. `cycle.bsm_pct > 30`：WSM 指令占比高；进入 WSM 维度检查 conflict。
7. CycleTrace 与 mcProfiler hardware instruction 占比差异 > 5 percentage points：优先视为计数口径差异或采集异常，需要复核报告中的 cross-check 表。

**口径说明**:
- `STE` cat 包含 STE-name、`S_NOP` 和 `Branch` 等 STE-category 事件。
- `GLOBAL` cat 对应 GVM Load/Store；报告中可显示为 `GLOBAL/GVM`。
- `ARRIVE` cat 对应 Synchronization 类事件。

---

## Dimension 3 — SOL / Pipeline Duty

**要回答的问题**: 各流水线实际忙碌程度如何？指令占比和硬件 duty 是否一致？

**可用字段**:
- `profiler.ap_mte_duty_pct`、`profiler.ap_ste_duty_pct`、`profiler.ap_mma_duty_pct`
- `profiler.vls_duty_pct`、`profiler.l2c_duty_pct`
- `profiler.compute_inst_busy_duty_pct`
- `profiler.real_ipc`、`profiler.instruction_throughput`
- `profiler.ap_active_cycles`、`profiler.average_ap_busy_cycles`

**判断条件**:
1. 对 GEMM / matmul / Attention 等 tensor-like kernel，`profiler.ap_mma_duty_pct < 3` 且 `cycle.mma_pct < 1`：没有走 MMA 路径。
2. `profiler.ap_mma_duty_pct < 3` 且 `cycle.mma_pct > 5`：MMA 指令存在但被依赖或 memory stall 阻塞。
3. `profiler.ap_mte_duty_pct > 15` 或 `cycle.mte_pct > 30`：MTE/vector 计算路径是主要计算压力。
4. `profiler.vls_duty_pct > 50`：vector load/store 单元压力高。
5. `profiler.l2c_duty_pct > 50`：L2 带宽或 L2 访问压力高。
6. `profiler.real_ipc < 10` 且 `cycle.nop_pct > 10`：低 IPC 与 pipeline bubble 一致。
7. `profiler.compute_inst_busy_duty_pct` 低且 AP duty 都低：可能是 occupancy、同步或长延迟，而不是单一 compute pipe 饱和。
8. `profiler.compute_instruction_cycles_summary.mma_cycles_pct` 低但 kernel 应该是 GEMM/tensor 路径：说明 compute cycles 未充分落到 MMA 路径。
9. `profiler.isu_stall_summary.top_pct > 50`：某个 ISU stall bucket 明显主导，可作为 issue-side 辅助证据。

**谨慎使用**:
- `AP busy Duty` 当前不作为 headline 强判断；报告只保留 AP active/busy cycles 作为上下文。
- `ISU stall cycles layout` 不是 NCU per-warp stall reason，不能据此命名 warp stall hotspot。

---

## Dimension 4 — Memory Hierarchy

**要回答的问题**: 全局访存、cache 和 DRAM latency 是否构成瓶颈？

**可用字段**:
- `profiler.vl1_hit_rate_pct`、`profiler.l2c_hit_rate_pct`、`profiler.sl1_hit_rate_pct`
- `profiler.dnoc_read_average_latency`、`profiler.dnoc_latency_histogram`
- `profiler.dnoc_read_req`、`profiler.dnoc_write_req`
- `profiler.dnoc_latency_histogram_pct`
- `profiler.global_memory_read_bytes`、`profiler.global_memory_write_bytes`
- `profiler.global_read_instructions`、`profiler.global_write_instructions`
- `profiler.vl1_read_instructions`、`profiler.vl1_write_instructions`
- `profiler.l2c_read_instructions`、`profiler.l2c_write_instructions`
- `profiler.constant_read_instructions`、`profiler.constant_read_sl1_instructions`、`profiler.constant_read_l2_instructions`
- `profiler.vl1_partition_stalls`、`profiler.vl1_partition_stall_summary`
- `profiler.memory_data_flow`
- `cycle.gvm_pct`、`cycle.gvm_600cy_peak`

**判断条件**:
1. `0 < profiler.l2c_hit_rate_pct < 80` 且同时存在 `cycle.gvm_pct > 10`、
   `profiler.vls_duty_pct > 10` 或 `profiler.l2c_duty_pct > 10`：cache 效率可能参与
   memory pressure；再结合 DNOC、global bytes 和指令数判断是否要改访存路径。
2. `profiler.l2c_hit_rate_pct > 85` 且报告中的 `DNOC >512-cycle share` 低：DRAM 拥塞假设较弱。
3. `profiler.dnoc_read_average_latency > 500`：DRAM/NoC 延迟偏高。
4. 报告中的 `DNOC >512-cycle share > 10%`：DNOC latency tail 明显；该比例同时保存在 `profiler.dnoc_latency_histogram_pct`。
5. `cycle.gvm_600cy_peak >= 13`：GVM issue 在 600-cycle 窗口内有拥堵风险。
6. `profiler.vl1_partition_stall_summary.spread_pct` 高：可能存在 cache partition imbalance。
7. `profiler.memory_data_flow` 中 global/private/shared 某一路径异常高：作为 traffic source 定位依据。
8. `profiler.global_memory_read_bytes/write_bytes` 和 DNOC req 可作为 traffic volume 上下文，但不等同于 NCU sector/request。

**不可用边界**:
- 当前 artifacts 没有 global coalescing sectors/request 或 useful-bytes/sector，不能直接判断 coalescing 质量。
- VL1 默认可能关闭；VL1 hit rate 需要结合实际配置解释，L2C hit rate 更适合做 headline。

---

## Dimension 5 — WSM Bank Conflict & Private Memory

**要回答的问题**: Workgroup Shared Memory 访问是否被 bank conflict 序列化？是否存在 private memory 溢出？

**可用字段**:
- `profiler.shared_memory_efficiency_pct`
- `profiler.avg_conflict_cycles_per_inst`
- `profiler.avg_cycles_per_load`、`profiler.avg_cycles_per_store`
- `profiler.avg_latency_per_load_instruction`、`profiler.avg_latency_per_store_instruction`、`profiler.avg_latency_per_atomic_instruction`
- `profiler.avg_cycles_per_atomic_instruction`
- `launch.private_per_thread`
- `profiler.memory_data_flow.private_kernel_rd`、`profiler.memory_data_flow.private_kernel_wr`
- `launch.static_shared_bytes`、`launch.dynamic_shared_bytes`、`launch.shared_memory_occupancy_pct`
- `cycle.bsm_pct`、`profiler.shared_memory_load_instructions`、`profiler.shared_memory_store_instructions`
- `profiler.all_scalars` 中名称包含 `atomic` 的字段

**判断条件**:
1. `profiler.avg_conflict_cycles_per_inst > 0`：WSM 存取指令存在 bank conflict。
2. `profiler.avg_conflict_cycles_per_inst > 0.5`：bank conflict 已足以作为主要优化候选。
3. 只有确认存在共享内存访问时，`0 < profiler.shared_memory_efficiency_pct < 80` 才表示显著 bank conflict；优先 padding/swizzle。
4. `80 <= profiler.shared_memory_efficiency_pct < 95`：中等冲突，结合 load/store cycles 判断收益。
5. `profiler.avg_cycles_per_load > 2` 或 `profiler.avg_cycles_per_store > 2`：load/store 被序列化。
6. `launch.private_per_thread > 0`：mcTracer 显示 PM 溢出。
7. private read/write 指令 > 0：mcProfiler 硬件确认 PM 流量。
8. atomic 计数 > 0：atomic 序列化/争用可能成为独立瓶颈。
9. `shared_memory_efficiency_pct == 0` 且 `avg_conflict_cycles_per_inst == 0` 时，不单独判定 bank conflict；优先检查是否根本没有 WSM 访问或该字段不可用。

**C500 特殊性**:
- C500 Wave=64，WSM 32 banks；后半 wave 镜像前半 wave 的 bank 访问，stride-1 比 NVIDIA Warp=32 更容易冲突。

---

## Dimension 6 — Roofline

**要回答的问题**: kernel 的经验表现更接近 compute-bound 还是 memory-bound？

**可用字段**:
- `profiler.achieved_flops_tflops`
- `profiler.achieved_bandwidth_gbs`
- `profiler.achieved_intensity_flop_per_byte`
- `profiler.roofline.case_I`
- `profiler.roofline.case_VL1_I`
- `profiler.roofline.case_L2C_I`
- `profiler.roofline.peak_trans_tflops`、`peak_fma_tflops`、`peak_mma_fp16_tflops`、`peak_int8_tflops`
- `profiler.roofline.hbm_usage_pct`、`vl1_usage_pct`、`l2c_usage_pct`
- `profiler.roofline.selected_compute_peak_name`、`selected_compute_peak_tflops`、`hbm_ridge_intensity_flop_per_byte`、`hbm_memory_roof_tflops`、`hbm_selected_roof_tflops`
- `profiler.roofline.hbm_region`、`roofline_bound`、`roofline_bound_confidence`、`hbm_achieved_vs_roof_pct`
- `profiler.roofline.compute_gap_pct`、`hbm_bandwidth_gap_pct`、`selected_roof_gap_pct`、`latency_or_efficiency_gap_pct`、`gap_hint`
- `profiler.roofline.all_ops`、`all_memacs`、`vl1_memacs`、`l2c_memacs`、`during`、`core_clk`、`mc_clk`

**判断条件**:
1. Roofline placement 使用 `P_roof(I)=min(P_peak, I*B_peak)`；HBM ridge intensity 为 `selected_peak_tflops * 1000 / MAX_Bandwith`。
2. Achieved FLOPS 远低于相关 peak context，且 `profiler.ap_mma_duty_pct < 3`：不是硬件 compute 饱和，而是没有走到 MMA 或被 stall 阻塞。
3. `case_I` 与外部 operator/shape 理论 AI 差距大：实际访存模式劣于理论模型。`achieved_intensity_flop_per_byte` 是 `case_I` 的提升字段，不能与 `case_I` 互相比差异。
4. `case_VL1_I`、`case_L2C_I` 与 DRAM 层 intensity 差异大：优先结合 cache hit 和 memory data flow 判断瓶颈层级。
5. `roofline_bound=under-roof` 表示当前点离理论 roof 过远，单靠 Roofline 不能断言已被 compute 或 memory roof 饱和，需结合 C500 heuristic bound。
6. `profiler.roofline.hbm_usage_pct`、`vl1_usage_pct`、`l2c_usage_pct` 显示当前点相对 HBM/VL1/L2C bandwidth roof 的使用率；这是上下文，不是越高越好的统一优化目标。
7. `compute_gap_pct` 是达成 FLOPS 到 selected compute peak 的经验 headroom；`hbm_bandwidth_gap_pct` 是达成带宽到 `MAX_Bandwith` 的经验 headroom；`selected_roof_gap_pct` 是当前点到 `min(selected_peak, case_I * MAX_Bandwith / 1000)` 的经验 headroom。
8. `selected_roof_gap_pct` 很高或 `gap_hint=under-roof latency/efficiency gap` 时，优先回到 SOL、ISU stall、memory hierarchy、bank conflict、occupancy 等维度解释 gap；不能仅凭 gap 字段下结论。
9. `gap_hint` 只说明经验 Roofline gap 的优先排查方向，不替代 `bound.primary`。

**不可用边界**:
- 没有外部 operator/shape 公式时，脚本只输出 mcProfiler 经验 Roofline，不编造理论 FLOP/Byte。
- RoofLine peak、usage、placement 和 gap 是 mcProfiler 图表上下文和派生公式，不能替代 operator-specific 理论 FLOP/Byte。

---

## Optional Overlay — Source-Line Attribution

当 `analysis/source_latency_<tag>.json` 存在且 `source_latency.available=true` 时，
报告应额外分析源码行级 modeled cycle attribution。该层用于定位“从哪一行开始看/改”，
不替代三工具 aggregate 指标给出的 compute / memory / latency / occupancy / mixed / unclear
瓶颈分类。

**要回答的问题**:
- 哪些源码行贡献了主要 modeled cycles？
- 这些源码行对应的 top instruction / instruction category 是什么？
- mapped coverage 是否足以支撑源码行级定位？
- top source line 是否能解释 aggregate bound 的主信号？

**可用字段**:
- `source_latency.coverage.modeled_source_mapped_cycle_share_pct`
- `source_latency.coverage.modeled_source_mapped_event_share_pct`
- `source_latency.coverage.modeled_unmapped_reasons`
- `source_latency.coverage.unmodeled_no_fixed_cycle_events`
- `source_latency.source_lines[*].file` / `line` / `source_text`
- `source_latency.source_lines[*].pct_total_cycles`
- `source_latency.source_lines[*].pct_mapped_cycles`
- `source_latency.source_lines[*].top_modeled_instruction`

**判断条件**:
1. 先看 coverage，再看 top source line；coverage 低时只能做低置信定位。
2. `% total` 表示该源码行 mapped modeled cycles / 全部 modeled cycles，是保守全局占比。
3. `% mapped` 表示该源码行 mapped modeled cycles / 已成功映射源码行的 modeled cycles，
   适合看已解释源码行之间的相对排序。
4. top source line 的 instruction/category 与 aggregate bound 主信号一致时，可提升
   Next Concrete Edit 的定位置信度。
5. top source line 与 aggregate bound 不一致时，保留为定位线索，不改变主瓶颈分类。

**不可用边界**:
- Source-line attribution 是 CycleTrace/asm modeled attribution，不是完整 wall-clock stall share。
- 可报告 top modeled instruction / instruction category 作为源码行归因证据；不支持完整
  per-PC stall hotspot 或 NCU 风格 per-warp stall reason。
- 不得从低 coverage、未映射或未建模事件中编造源码行热点。

---

## Bound 分类默认判断

`trace_profile_pipeline.py run/analyze --heuristic-bound-mode coarse` 输出 C500 heuristic bound：`compute`、`memory`、`latency`、`occupancy`、`mixed` 或 `unclear`：

1. `compute`：MTE/vector 路径或 tensor underuse 是主信号，常见条件为 `cycle.mte_pct > 30`、`profiler.ap_mte_duty_pct > 15`、`cycle.mma_pct < 1` 且 `profiler.ap_mma_duty_pct < 3`。
2. `memory`：global/cache/DRAM 或 WSM conflict 是主信号，常见条件为
   `cycle.gvm_pct > 25`、`profiler.vls_duty_pct > 50`、`profiler.l2c_duty_pct > 50`，
   或 `0 < profiler.l2c_hit_rate_pct < 80` 且存在 GVM/VLS/L2C 活动佐证，或
   `profiler.avg_conflict_cycles_per_inst > 0.5`。
3. `latency`：`cycle.nop_pct > 10` 或 low IPC + low duty，且 memory/compute 证据不足以单独解释。
4. `occupancy`：`0 < launch.effective_occupancy_pct < 25` 或 WIF/DPC/achieved waves 显示并行度不足；`effective_occupancy_pct == 0` 只作为不可用/矛盾信号。
5. `mixed`：多个 coarse 信号同时触发，不强制选择单一主 bound。
6. `unclear`：没有 coarse 信号达到阈值，必须保留证据和数据边界，不能默认归为 compute。

`--heuristic-bound-mode detailed` 保留 C500 复合标签，例如
`mte/vector-compute-bound + shared-memory-bank-conflict + low-occupancy`。

---

## 当前 artifacts 不支持的分析

- source-line / per-PC stall hotspot：默认三工具 artifacts 不提供源码行归因；若存在有效
  `analysis/source_latency_<tag>.json`，可分析 CycleTrace/asm modeled source-line attribution，
  并可报告 top modeled instruction / instruction category；但仍不支持完整 per-PC stall hotspot、
  NCU 风格 per-warp stall reason 或 wall-clock stall share。
- timeline shape：可用 CycleTrace `Wave start/end` 派生的 span、WIF quartiles、GVM 600-cycle
  peak，以及 DPC compute cycles、achieved/dispatched waves 做有限事件时间线和 aggregate
  balance proxy；仍不支持 PM-sampling/per-AP 利用率时间线，不能证明精确 idle/active phase。
- global coalescing 质量：缺 sectors/request 或 useful-bytes/sector 等价计数。
- 完整 streg occupancy limiter：需要编译器或汇编证据。
