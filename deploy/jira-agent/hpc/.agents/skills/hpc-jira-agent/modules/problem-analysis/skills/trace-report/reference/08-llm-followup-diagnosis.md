# LLM Follow-up Diagnosis

本文档定义基础采集、报告生成和诊断分析完成后的 LLM 增量诊断追加方式。采集和离线解析只生成基础
`REPORT_<tag>.md`；LLM 进一步诊断是完整 workflow 的必需步骤，必须把增量诊断写回目标环境中的
同一个报告，并通过最终 `check-report` 后才算最终交付完成。

## 读取门槛

常规 LLM 追加先读 `reference/quick-finalize.md`。只有 quick 结构不足、出现 `TR-LLM-*`、`TR-REP-005/007`，或 generated usecase 的追加/校验失败需要细分职责时，才读取本文。

本文只定义 LLM 第 9 章的追加、职责划分和结构校验。基础报告结构见
`reference/07-report-template.md`，source-latency 输入与归因边界见
`reference/10-source-latency.md`，usecase 拆分、manifest 和派生报告边界见
`reference/13-usecase-split.md`。

## 执行入口

标准命令以 `reference/quick-finalize.md` 的“标准入口”为准。本文只展开 `finalize-run` 在单报告和 generated usecase 场景中的职责划分、写入位置、结构校验和失败边界。

`finalize-run` 会读取 `usecases/manifest_<tag>.json.generated_reports`：没有 generated reports 时
追加主报告并执行最终 `check-report`；存在 generated reports 时，必须追加主报告 scope 诊断，
并逐个追加 usecase LLM 诊断，最后对父 run 执行完整 `check-report`。当前最终质量门会同时校验
主报告和所有 generated usecase 报告的第 9 章；缺少主报告 `--diagnosis-file` 会导致最终
`check-report` 失败。`finalize-run` 只完成报告追加和最终报告质量门；成功后会输出
`workflow_state: needs_artifact_sync` 和第 5 步 `sync-artifacts` 命令。完整 workflow 以
`sync-artifacts` 成功后的 `workflow_state: complete` 为准。

多 usecase 场景中，主报告和 usecase 报告的第 9 章职责不同：

- 主报告第 9 章是 run-level scope/audit 诊断，只能说明采集完成度、kernel filter、usecase
  拆分、manifest/generated reports、aggregate 指标边界，以及最终优化应查看哪些 usecase 报告。
  不要把父级 aggregate 指标写成某个单 case 的源码优化结论。
- generated usecase 报告第 9 章是单 case 优化诊断，必须至少引用该 usecase 自身
  `## 4. Diagnosis` 中一个 `HIGH` 或 `MEDIUM` 性能诊断，并给出优化方向、置信边界和验证期望。
  只写“需要和其他 usecase 对比”“作为 regression guard”或“验证目标”不算完整 usecase LLM 诊断。
- 如果父级报告有 source-latency 而 usecase 报告没有 source-latency，该源码行归因只能写成
  aggregate source-latency hint。启用 source-latency 且 `split-usecases` 生成 usecase reports 时，
  `collect-run` 会在拆分后逐个生成 `usecases/usecase_XXX/analysis/source_latency_*` 并刷新
  usecase 报告；任一 usecase source-latency 生成或报告刷新失败都会使 `collect-run` 返回非 0。
  只有这些 per-usecase source-latency 产物才能作为单 usecase source-line edit target。

## 兼容与调试入口

标准 workflow 一律使用本文开头的 `finalize-run`。下列入口只在兼容旧流程、定位 stdin/marker 问题或单独排查 usecase 追加失败时使用：

| 入口 | 仅在何时使用 | 边界 |
|---|---|---|
| `trace_report_env.py append-diagnosis --diagnosis-file ...` | 单报告第 9 章追加失败，需要隔离 `finalize-run` 外层选择逻辑时 | 只处理一个主报告；标准 workflow 仍回到 `finalize-run`。 |
| `trace_report_env.py run -- python3 .trace-report/scripts/trace_profile_pipeline.py append-diagnosis --report ...` | 底层 stdin 或 marker 调试 | 必须在目标环境执行，不能直接本地写报告；内容仍必须满足本文第 9 章结构。 |
| `trace_report_env.py append-usecase-diagnosis --diagnosis-dir ...` | 兼容旧流程或单独定位 usecase 追加失败 | 不处理父报告，不代表完整 workflow。 |

## Usecase 报告入口

`split-usecases` 生成的 `usecases/usecase_XXX/REPORT_*.md` 也是基础报告；如果最终交付依据是这些
单 usecase 报告，每个 generated report 都必须补充第 9 章。推荐为每个 usecase 准备一个本地诊断
文件：

```text
<local-diagnosis-dir>/
├── usecase_000.md
└── usecase_001.md
```

然后执行统一收尾入口：

```bash
python3 scripts/trace_report_env.py --config "$ACTIVE_CONFIG" finalize-run \
  --run-dir "$PROFILE_RUN_DIR" \
  --diagnosis-file /path/to/parent_scope_diagnosis.md \
  --diagnosis-dir <local-diagnosis-dir>
```

兼容入口 `append-usecase-diagnosis` 只逐个追加 generated report，不改变主报告，也不重新生成 source-latency 派生结果；任一 usecase 缺失诊断文件、marker 不完整或 reasoning chain 不合格都会返回非零。per-usecase source-latency 应由启用 source-latency 的 `collect-run` 在 split 后生成；缺失时按 `reference/10-source-latency.md` 排查边界。

## 写入位置

追加内容写入 `REPORT_<tag>.md` 的以下章节：

```markdown
## 9. LLM Follow-up Diagnosis

<!-- trace-report:llm-follow-up-diagnosis:start -->
...
<!-- trace-report:llm-follow-up-diagnosis:end -->
```

如果报告中没有该章节，命令会追加新章节；如果已有完整 marker，命令会替换 marker 内的旧
内容。marker 不完整等追加失败场景按 `^## TR-LLM-005$` 标题局部读取
`reference/09-error-troubleshooting.md` 对应详情块。

完整 workflow 的最终检查要求该章节存在且通过结构化校验。只有 `collect-run` 成功但未追加
该章节时，报告只能视为采集和基础分析完成，不能视为最终交付完成。

## 内容要求

`reference/quick-finalize.md` 已包含单报告 LLM diagnosis 的最小 heading/schema、自检命令和
`TR-LLM-004` 预防规则。本文只在 quick 结构不足、出现 `TR-LLM-*`、`TR-REP-005/007`、
generated usecase 复杂职责、或需要解释第 9 章职责边界时展开。

第 9 章只写 LLM 相对主报告的增量诊断，不重复主报告已有的完整 Evidence table、Next
Concrete Edit 和 Data Boundaries。LLM 诊断必须引用 `REPORT_<tag>.md`、
`analysis/metrics_all_<tag>.json` 或参考文档中的真实字段；缺失数据必须写成边界。

对 generated usecase 报告，`REPORT_<tag>_usecase_XXX.md`、该 usecase 的
`analysis/metrics_all_<tag>_usecase_XXX.json` 和
`analysis/metrics_key_<tag>_usecase_XXX.json` 是单 case 证据源。usecase LLM
诊断必须把基础报告中的性能诊断转成至少一个明确优化方向，例如 tensor/MMA 路径、
shared-memory layout/padding、occupancy/resource footprint、pipeline bubble/load-use
distance 或该 usecase 特有的 WIF/DPC imbalance。跨 usecase 对比只能作为补充，不能替代
单 usecase 优化诊断。

若基础报告包含 `### 3.8 Source-Line Attribution`，LLM 诊断必须检查该节并在推理链中使用：
top source line、coverage、top instruction，以及它是否支持当前 aggregate bound。coverage 低、
unmapped/unmodeled 占比高或 top line 与 aggregate bound 不一致时，必须写入 confidence boundary。
不得把 source-latency 的 modeled attribution 改写成完整 wall-clock stall share、完整 per-PC
stall hotspot 或 NCU 风格 per-warp stall reason；可以引用 top modeled instruction 作为源码行
归因证据。

必需顶层 heading：

- `### Summary`
- `### Quality Gate Result`
- `### Final Diagnosis`
- `### Validation Plan`

`Final Diagnosis` 中每个 `#### Diagnosis ...` 诊断块都必须带有自己的
`#### Reasoning Chain`。一个全局推理链不够。每个推理链必须覆盖：

1. measured evidence
2. mechanism
3. counter-evidence / weaker hypotheses
4. confidence boundary
5. edit rationale
6. validation expectation

## 质量门失败

如果空/无效 CycleTrace、CycleTrace 与 mcProfiler 指令分布大幅矛盾、Next Concrete Edit
不匹配 kernel 类型或证据不足，不得追加高置信诊断。此时应在第 9 章追加低置信/停止原因，
并列出需要重采或补采的项目。即使质量门失败，也必须使用同一结构写成至少一个
`#### Diagnosis ...` 块和对应 `#### Reasoning Chain`，把 Claim 写成低置信或停止结论，
不要省略必需的顶层 `###` heading。

generated usecase 报告的最终校验会额外检查第 9 章是否引用了基础 `## 4. Diagnosis`
中的性能诊断项。若只写作用域、对比关系或回归守护，`check-report --require-llm-followup true`
应失败，需要重新准备该 usecase 的诊断文件后优先重新执行 `finalize-run`；只有兼容旧流程或
手工调试时才直接执行 `append-usecase-diagnosis`。
