# Quick Diagnose

在 `collect-run` 返回 `needs_llm_followup` 后执行 Step 3；阶段入口见[主流程](../SKILL.md#按执行状态读取)。报告和指标仍在目标侧，按 [quick-collect 的路径边界](quick-collect.md#collect-后路径边界)读取；不要提前假设本地归档已同步。

## 诊断顺序

1. 读取目标 `REPORT_<tag>.md`，提取 headline、bound、evidence、collection scope 和已声明边界。
2. 读取 `analysis/metrics_key_<tag>.json`，复核 launch、CycleTrace、mcProfiler 可用性及 source-latency/usecase 状态。
3. 必须读取 [04-analysis-dimensions](04-analysis-dimensions.md) 做六维度检查，区分有效字段、阈值和不可用边界。
4. 必须读取 [06-diagnosis-playbook](06-diagnosis-playbook.md)，匹配 artifacts 支持的模式、反证及下一具体改动。
5. `metrics_all` 默认只记录路径；关键字段缺失、冲突或边界需复核时局部展开，raw artifacts 和完整日志同样按缺口读取。
6. 收敛到一个 Next Concrete Edit；证据不足写低置信或 `unclear`。不能只给多个方向，也不能强行给高置信单因。

## 必须遵守

- 结论引用真实字段；缺失数据写成边界。
- `dur=4` 不是真实延迟。
- `effective_occupancy_pct == 0` 不直接判定 occupancy bottleneck。
- source-latency 是 modeled attribution，不是实测单行耗时。
- 缺少 mcProfiler 时，profiler duty、IPC、cache、bank conflict 和 Roofline 字段不得编造成可用。
- 多 usecase 时读取 `usecases/manifest_<tag>.json` 和 `generated_reports` 对应报告。父级 `REPORT_<tag>.md` 仅用于 run-level scope/audit，其 LLM 诊断总结范围、拆分状态、aggregate 边界和子报告入口；优化方向落到各 usecase 报告。父级 `analysis/source_latency_<tag>.*` 是 aggregate attribution，不能当作某个 usecase 的源码行热点。
- 结合 CycleTrace 指令与可用的 mcProfiler 硬件证据判断，不能仅以指令占比代替硬件瓶颈；缺少后者时按上述数据边界说明。

## 按需扩展文档读取门槛

`04-analysis-dimensions.md` 和 `06-diagnosis-playbook.md` 是 Step 3 诊断基础，不属于本节按需扩展范围。只在本文与诊断基础文档仍无法完成下一步决策时读取其他深层文档：

- `reference/05-c500-metric-names.md`：字段未知、字段冲突、旧口径风险、`metrics_key` 不够用。
- `reference/C500-architecture.md`：报告结论依赖硬件机制解释，或用户要求 C500/A100 架构对比。
- `reference/10-source-latency.md`：需要解释源码行归因边界或 coverage 可行动性。
- `reference/13-usecase-split.md`：诊断对象是 generated usecase、`generated_reports` 非空、或 manifest 显示匹配不完全可信。
