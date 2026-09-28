# C500 诊断手册

本手册只列当前 `trace_profile_pipeline.py` 能用 `metrics_all_<tag>.json` 或 `REPORT_<tag>.md` 支撑的诊断模式。无法由当前 artifacts 证明的内容只能写成风险或数据边界。

## 读取门槛

本文是 Step 3 诊断基础文档。只有 `collect-run` 已输出 `workflow_state: needs_llm_followup`，且已进入 `reference/quick-diagnose.md` 后，才读取本文；不得在采集前为了提前准备诊断而读取。进入 Step 3 后必须用本文匹配当前 artifacts 可支撑的诊断模式、排除 unsupported 结论，并收敛到一个 Next Concrete Edit。若多个模式并列，按本文优先级、实现代价和当前证据支撑度收敛。

**优先选择实现简单、改动小的修改方向。**

## 使用顺序

1. 先看 `bound.type`、`bound.primary`、`bound.evidence` 和 `bound.rationale`。
   `bound.primary == mixed` 表示多个 coarse 信号并列，不要把报告压成单一 bound；
   `bound.primary == unclear` 表示当前阈值没有识别到明确 bound。
2. 再按下面模式检查具体指标。
3. 每次报告只收敛到一个 Next Concrete Edit。
4. 修改后用同一 shape 重新采集，对比 `REPORT_<tag>.md` 和关键 JSON 指标。

## 可支撑诊断模式

### A. WSM bank conflict

触发信号：
- 确认存在共享内存访问时，`0 < profiler.shared_memory_efficiency_pct < 80-85`
- 或 `profiler.avg_conflict_cycles_per_inst > 0.5`
- 辅助信号：`launch.static_shared_bytes`、`launch.dynamic_shared_bytes`、
  `launch.shared_memory_occupancy_pct` 非零，`profiler.avg_cycles_per_load/store`
  偏高，CycleTrace `BSM` share 偏高

机制：C500 wave=64，WSM 只有 32 banks，布局不当会导致访问序列化。

优先修改：shared-memory padding、skew/XOR swizzle、调整转置/分块布局，减少不必要的 shared-memory 往返。

排除：shared-memory efficiency 接近 100% 且 conflict cycles 为 0 时，不把 BSM 占比高直接解释成 bank conflict；shared-memory efficiency 为 0 且 conflict cycles 为 0 时，也不能单独解释成 bank conflict。

### B. Low occupancy / launch bound

触发信号：
- `0 < launch.effective_occupancy_pct < 25`
- `launch.mtreg_occupancy_pct` 或 `launch.shared_memory_occupancy_pct` 明显低
- 辅助信号：`profiler.achieved_waves` 低、CycleTrace WIF 分位数低

机制：寄存器或 shared memory 限制每 AP 常驻 wave/block；当前 artifacts 只能直接使用 mtreg 和 shared-memory 字段，不能完整拆出 streg limiter。

优先修改：先按 kernel 类型分流。GEMM/matmul/MMA/attention/conv 等 tensor 路径 kernel，检查寄存器压力、WSM tile footprint 和 tile shape；ReLU、elementwise、copy/reduction 等非 tensor kernel，优先检查 grid/block 覆盖、tail effect 和调度达成，只有 mcTracer 或编译器证明确有资源限制时才降低寄存器 live range、shared memory footprint 或使用 launch bounds。

排除：`launch.effective_occupancy_pct == 0` 时先视为不可用或互相矛盾字段，不直接判定低 occupancy；理论 occupancy 高但 WIF/achieved waves 低时，优先检查 grid 数量、tail effect 或负载不均，不直接归因到资源限制。

### C. Memory traffic / latency pressure

触发信号：
- CycleTrace `GLOBAL` / `cycle.gvm_pct` 偏高
- `profiler.vls_duty_pct > 50` 或 `profiler.l2c_duty_pct > 50`
- `0 < profiler.l2c_hit_rate_pct < 80`
- `profiler.dnoc_read_average_latency` 或 DNOC high-latency share 偏高
- `profiler.dnoc_read_req/write_req`、global memory bytes 或 global read/write instructions 明显偏高
- 辅助信号：`profiler.isu_stall_summary.top == "vls_pipeline_stall"` 且占比高

机制：全局访存请求、L2 miss 或 DNOC 延迟尾部导致 pipeline 等待。

优先修改：先区分 kernel 类型和证据。tensor 路径 kernel 可考虑 tile 局部性、WSM staging/复用、布局重排和减少重复 global load/store；非 tensor kernel 默认优先做连续访问、向量化/合并访存、减少冗余读写和改善 grid/block 数据覆盖。只有已存在共享内存访问或明确数据复用证据时，非 tensor kernel 才建议引入或调整 WSM。

排除：L2C hit rate 高、DNOC 高延迟 share 低且 `GLOBAL` share 不高时，不把主瓶颈归因到 global memory bandwidth。ISU stall layout 不是 per-warp stall reason，只作为 issue-side 辅助证据。

### D. Tensor path underuse / vector compute bound

触发信号：
- 对应 GEMM/matmul 类 kernel，`cycle.mte_pct` 高而 `cycle.mma_pct` 低
- `profiler.ap_mte_duty_pct` 高而 `profiler.ap_mma_duty_pct` 低
- `profiler.compute_instruction_cycles_summary.mma_cycles_pct` 明显低，或 Roofline 点远低于
  `profiler.roofline.peak_mma_fp16_tflops`
- `bound.primary == compute` 且 evidence 指向 MTE/vector 路径

机制：应使用 MMA 的计算落在 vector/MTE 路径，或 MMA 指令数量/硬件 duty 不足。

优先修改：检查 dtype、tile shape、数据布局、dispatch 路径和实际指令分布，确认预期 tensor 计算是否映射到 MMA 指令路径。

排除：ReLU、elementwise、copy/reduction 类 kernel 本来就不应强行使用 MMA；MMA duty 高但慢时转查 memory、bank conflict 或 occupancy。

### E. Pipeline bubble / latency

触发信号：
- `cycle.nop_pct > 10`
- 或 `profiler.real_ipc < 10` 且 memory pressure 不强
- `bound.primary == latency`

机制：同步、load-use 间隔、依赖链或可并行 wave 不足造成 issue 空洞。CycleTrace NOP 是信号，但不能直接给出真实 stall 周期。

优先修改：重排 load/compute、double buffering、减少 barrier、增加独立计算量。

排除：NOP 高但 IPC/duty 同时高时，可能是正常 issue 间隙；必须结合 SOL/IPC 判断。

### F. Private memory / spill pressure

触发信号：
- `launch.private_per_thread > 0` 或 `launch.private_total > 0`
- `profiler.memory_data_flow.private_kernel_rd/wr` 非零

机制：寄存器溢出到 private memory，访问代价接近 global memory。

优先修改：减少局部数组和 live range、内联小函数、使用向量类型替代栈数组、调整 launch bounds。

排除：mcTracer 和 mcProfiler private memory 都为 0 时，不把性能问题归因到 PM spill。

### G. Tail effect / workload imbalance

触发信号：
- `cycle.wif_p75 / cycle.wif_p25 > 2`
- `profiler.dpc_compute_imbalance_pct` 明显偏高
- `profiler.ap_busy_duty_pct` 很低但 kernel 工作量不小

机制：grid 不整齐、各 DPC/AP 工作量不均或 kernel 尾部只剩少量 wave。

优先修改：让 grid 接近 AP 数量的整数倍、调整 tile 分配、减少尾块差异。

排除：当前 artifacts 没有 per-AP timeline，不能证明具体哪段时间产生 tail；只能给出 aggregate imbalance 判断。

### H. Roofline mismatch

触发信号：
- `profiler.achieved_flops_tflops`、`profiler.achieved_bandwidth_gbs`、
  `profiler.achieved_intensity_flop_per_byte` 与理论预期矛盾
- `profiler.roofline.case_VL1_I` / `case_L2C_I` 和 DRAM intensity 差异明显
- `profiler.roofline.hbm_usage_pct`、`vl1_usage_pct`、`l2c_usage_pct` 指示当前点只使用了
  mcProfiler RoofLine 图中部分带宽 roof
- `profiler.roofline.peak_trans_tflops`、`peak_fma_tflops`、`peak_mma_fp16_tflops`、
  `peak_int8_tflops` 与 achieved FLOPS 差距大
- `profiler.roofline.roofline_bound` 为 `under-roof`，或 `hbm_achieved_vs_roof_pct` 很低，说明当前点离公式化 roof 较远
- `profiler.roofline.compute_gap_pct`、`hbm_bandwidth_gap_pct`、`selected_roof_gap_pct` 显示到 compute peak、HBM bandwidth peak 和 selected HBM roof 的经验 headroom
- `profiler.roofline.gap_hint` 提示优先排查 compute peak gap、HBM bandwidth gap 或 under-roof latency/efficiency gap
- C500 heuristic bound 与 `profiler.roofline.roofline_bound` 不一致，需要分别解释：前者是硬件症状分类，后者是 Roofline 公式位置

机制：理论 AI 假设理想访存；经验 Roofline 反映 cache miss、bank conflict、重复流量等真实开销。

优先修改：先看 `roofline_bound` 判断当前点处于 memory-region、compute-region 还是 under-roof；再用 `selected_roof_gap_pct` 判断 gap 大小，用 `gap_hint` 选择优先排查维度。若 under-roof、置信度低或 `latency_or_efficiency_gap_pct` 可用，再回到 memory hierarchy、SOL、ISU stall、bank conflict、latency/occupancy 维度定位。

排除：没有 mcProfiler roofline 字段时，不生成 Roofline 强结论。RoofLine peak/usage/placement/gap 是 mcProfiler 图表上下文和公式化派生，不能替代 operator-specific 理论 FLOP/Byte，也不能单独替代 C500 heuristic bound。

### Source-line attribution overlay

触发信号：
- `source_latency.available=true`
- `source_latency.source_lines` 非空

机制：source-latency 把 CycleTrace 指令事件和 lineinfo objdump 映射到用户源码行，
输出 CycleTrace/asm modeled cycle attribution。它定位可先检查的源码语句，但不改变
三工具 aggregate 指标给出的主瓶颈分类。

优先修改：先按 aggregate 指标选择 compute / memory / latency / occupancy / mixed / unclear
诊断方向，再用 top source line 选择最具体的修改入口。若 top source line 的 instruction/category
与主瓶颈一致，可把该行作为首选 edit target；若不一致，只作为定位线索并保留置信度边界。

排除：coverage 低、top source line 为空、unmapped/unmodeled 占比高时，不给高置信源码行结论。
可报告 top modeled instruction / instruction category 作为源码行归因证据；不得把
source-latency 百分比解释成完整 wall-clock stall share、完整 per-PC stall hotspot 或 NCU
风格 per-warp stall reason。

## 数据边界

- 默认三工具 artifacts 不提供 source-line attribution；存在有效 source-latency summary 时，
  支持 CycleTrace/asm modeled source-line attribution 和 top modeled instruction，不支持完整
  per-PC stall hotspot。
- 当前 artifacts 不提供完整 PM-sampling/per-AP timeline；只能使用 CycleTrace span、WIF
  quartiles、GVM 600-cycle peak、DPC compute cycles、achieved/dispatched waves 做有限事件时间线和
  aggregate balance proxy。
- 当前 artifacts 不提供 sectors/request 或 useful-bytes/sector，不能证明 global coalescing 质量。
- `launch.effective_occupancy_pct` 只由 mtreg/shared-memory 字段计算；streg 需要编译器或汇编证据。
- `launch.effective_occupancy_pct == 0` 不是低 occupancy 结论；必须写成资源字段不可用或互相矛盾。
- GVM 600-cycle peak 是压力代理，不是精确 buffer occupancy。

## 优先级

默认按实现收益与代价排序：

| 优先级 | 模式 |
|---|---|
| 1 | Private memory / spill pressure |
| 2 | WSM bank conflict |
| 3 | Memory traffic / latency pressure |
| 4 | Tensor path underuse / vector compute bound |
| 5 | Low occupancy / launch bound |
| 6 | Pipeline bubble / latency |
| 7 | Tail effect / workload imbalance |
| 8 | Roofline mismatch |

## 相关文档

- `reference/04-analysis-dimensions.md`：6 维度分析框架，定义各维度可用字段和判断边界。
- `reference/05-c500-metric-names.md`：指标名称、来源和不可用边界。
- `reference/C500-architecture.md`：需要理解 C500 硬件并行性、存储子系统或与 A100 对比时读取。
