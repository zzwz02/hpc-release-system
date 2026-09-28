# Usecase Split 后处理

本文描述同一 run 中多 kernel 或同名 kernel 多 usecase 的拆分流程。标准 `collect-run` 在主报告显示
`mcProfiler scope=per-kernel-multiple-occurrences` 时会自动执行该步骤；手工入口仍用于离线 `run` 后处理、
复查 manifest、调低 confidence 或排查匹配问题。主报告仍按原流程生成；拆分报告只用于需要保持“单
kernel 单 usecase”分析口径的场景。

## 读取门槛

只有 `generated_reports` 非空、manifest ambiguous、用户要求单 kernel 单 usecase 结论，或 final/diagnosis 目标需要落到 generated usecase 报告时，才读取本文。普通单报告路径不默认读取本文。

## 适用场景

- 同一 `OP_EXEC_CMD` 触发多个 kernel，而用户希望分别看每个 kernel 的报告。
- 同一个 `CYCLE_TRACE_KERNEL_NAME` 在一次执行中出现多条用例，例如同名 kernel 被不同 shape
  调用。
- 同一个 kernel name 和同一 resource signature 出现多次，表示同一 usecase 被重复执行；此时可
  聚合后生成一个 usecase 报告。

不适用场景：

- CycleTrace JSON 本身没有硬件事件或只有 metadata。
- mcTracer launch 数和 CycleTrace 时间段数无法建立顺序对应关系。
- 用户没有接受低置信匹配时，不应生成独立报告。

## 自动触发与复查入口

`collect-run` 自动触发时使用 active config 中的 `CYCLE_TRACE_DPC_ID`、`HEURISTIC_BOUND_MODE`、
`REQUIRE_MCPROFILER` 和 `CYCLE_TRACE_KERNEL_NAME`。agent 默认读取 `usecases/manifest_<tag>.json` 和
`generated_reports`，不主动重跑 split。只有以下场景才手工执行 `trace_profile_pipeline.py split-usecases`：

| 场景 | 目的 |
|---|---|
| 离线 `run` 后处理 | 已有 artifacts，需要补生成 usecase manifest/report。 |
| 复查 manifest | 核对匹配状态、generated reports 或 ambiguous 原因。 |
| 调整 `--min-confidence` | 用户接受不同置信阈值后重新生成。 |
| 排查匹配问题 | 复核 launch、CycleTrace segment、mcProfiler occurrence 对齐。 |

`--cycle-kernel-name` 是可选过滤条件，但当 CycleTrace 用 `CYCLE_TRACE_SAMPLE_MODE=C` 只采目标
kernel 时，应传入同一个精确 mcTracer `args.name`。脚本不做短名、子串或相似 name 匹配。
当主报告显示 `mcProfiler scope=per-kernel-multiple-occurrences` 时，标准采集已经保留完整
`artifacts/mcprofiler_per_kernel/occurrence_<N>/` 作为拆分输入；此时应检查 `collect-run` 自动生成的
`usecases/manifest_<tag>.json`，而不是把主
报告当成单 usecase 报告。若 `mcProfiler scope=per-kernel` 且只有一个 occurrence，标准
`artifacts/mcprofiler_report_*` 已经是主报告输入，`mcprofiler_per_kernel/occurrence_000/` 可能只保留
manifest 审计信息，不作为拆分输入。

可选参数：

| 参数 | 含义 |
|---|---|
| `--artifact-dir` | 覆盖输入 artifacts 目录，默认 `<run-dir>/artifacts` |
| `--min-confidence` | 生成派生报告所需最低置信度，默认 `medium`，可选 `high/medium/low` |
| `--write-ambiguous` | 默认 `false`；当前只在已经形成可聚合 usecase 时允许继续写派生 artifacts/report。若 launch/segment 数量不匹配或没有 matches，仍只写 manifest，不会生成报告 |

## 匹配逻辑

1. 从 `tracer_out.json` 提取 kernel launch。若传入 `--cycle-kernel-name`，只保留
   `traceEvents[].args.name` 完全相等的 launch。
2. 从 primary CycleTrace JSON 按 timestamp 切分硬件事件段。先用大时间空洞做粗分段；如果只有一个
   粗段但 mcTracer 期望多个 launch，再按硬件事件内部最大时间空洞拆分。
3. 要求 launch 数和 CycleTrace segment 数一致。数量不一致时状态为 `ambiguous`，默认只输出
   manifest。
4. 按顺序匹配 launch 和 segment，并用两个比例校验：
   - mcTracer `grid.x * grid.y * grid.z` 与 CycleTrace `Wave start` 数的归一化比例。
   - mcTracer kernel event duration 与 CycleTrace segment span 的归一化比例。
5. 比例误差满足阈值时给出 `high` 或 `medium` confidence；低于 `--min-confidence` 时不生成报告。
6. 按 `kernel_name + grid + block + registers + static/dynamic shared memory` 组成 usecase
   signature。相同 signature 的多次 launch/segment 聚合为同一个 usecase，不同 signature 拆成
   不同 usecase。
7. 如果 `artifacts/mcprofiler_per_kernel/` 存在且 occurrence 目录含完整 JSON/TXT/CSV/PNG，脚本会读取 mcProfiler per-kernel occurrence：
   - `mcProfiler.json.conditions.kernelnames` 由采集阶段保证来自同一个 `CYCLE_TRACE_KERNEL_NAME`。
   - occurrence 数必须与 mcTracer 目标 launch 数一致。
   - occurrence 顺序只作为候选匹配，必须再用 mcProfiler `WORKGROUPS` 和 mcTracer
     `grid.x * grid.y * grid.z` 校验。
   - 校验通过后，每个 usecase 若只对应一个 occurrence，会设置
     `mcprofiler_scope=per-usecase-ref`，并在 usecase 的 `collection_manifest.json` 中写入
     `mcprofiler_reference`，引用父级 `artifacts/mcprofiler_per_kernel/occurrence_*` 下的
     `mcprofiler_report_dumped.json`、`mcprofiler_report.txt.json`、`mcprofiler_report.txt`、
     `mcprofiler_report.txt.csv` 和对应 PNG；这些文件不再复制到 usecase 目录。
   - 若没有匹配到单个 per-usecase occurrence，脚本不会把父级命令级
     `mcprofiler_report_*` 复制到 usecase 下；该 usecase 的 profiler 指标必须保持不可用或写成
     scope 边界，避免把父级混合数据误当作单 usecase 数据。
   - 对派生 usecase 报告，`REQUIRE_MCPROFILER=true` 表示原始采集阶段需要 mcProfiler；不表示每个
     usecase 都必须有两个标准 mcProfiler JSON。只有 `mcprofiler_scope=per-usecase-ref` 时，缺失
     或无法解析被引用的 mcProfiler JSON 才是派生报告错误。
   - 单 occurrence 主报告路径为了避免重复归档，可能只在 `mcprofiler_per_kernel/occurrence_000/`
     保留 manifest；这种情况下不需要拆分，主 artifacts 下的标准 `mcprofiler_report_*` 和 PNG 已经是
     对齐数据。

该逻辑依赖 mcTracer 和 CycleTrace 同一次执行中的相对顺序以及相近的用例比例，不依赖绝对时间戳同一
坐标系。absolute timestamp 只用于 CycleTrace 内部分段。

## 可信度判定

数据及派生报告完全可信，需要同时满足：

- `match_result.status=matched`，且 `match_result.confidence` 达到本次要求的
  `--min-confidence`；默认至少为 `medium`，用于正式诊断建议优先要求 `high`。
- mcTracer launch 数与 CycleTrace 硬件 segment 数一致，没有未匹配 launch 或未匹配 segment。
- 每个 usecase 的 signature 能区分实际用例：不同 shape、grid/block 或资源配置会拆成不同
  usecase；同一 signature 的重复 launch 被视为同一用例重复执行并聚合。
- `wave/grid ratio error` 和 `duration/span ratio error` 都在 manifest 的 confidence 阈值内；
  样例中 `wave/grid ratio error=0` 且 `duration/span ratio error` 很小，属于可直接用于
  单 usecase 报告的证据。
- 派生 artifacts 的 `collection_manifest.json.invalid_required` 为空，派生报告通过基础
  `check-report`。
- 若报告中使用 mcProfiler 指标，必须确认其 scope：命令级 mcProfiler 只能作为旁证；只有 mcProfiler
  本身是 per-kernel 采集并被 usecase 标记为 `per-usecase-ref` 时，才可把 `profiler.*` 作为该
  usecase 的完全对齐数据。设置
  `CYCLE_TRACE_KERNEL_NAME` 后，标准采集会使用 `--kernelnames/--per-kernel`，但仍必须通过
  occurrence 数量和 `WORKGROUPS` 校验后，才能写入对应 usecase。

数据及派生报告不完全可信，需要用户排查后再决定是否采信：

- `match_result.status=ambiguous`，或 confidence 低于预期，即使使用
  `--write-ambiguous true` 生成了报告，也只能作为排查材料。若需要把低置信 ordered candidate 写成
  报告，应显式降低 `--min-confidence low`；数量不匹配或没有 matches 时只会写 manifest。
- launch 数与 segment 数不一致，说明至少有 kernel/usecase 没有建立一一对应关系。
- 同一 kernel name 下多个用例的 signature 完全相同，但用户知道它们实际输入 shape 或语义不同。
  当前脚本会把它们聚合为同一 usecase；这时报告只可信于“同一 signature 聚合”层面，不能区分每条
  用例。
- CycleTrace 时间段存在重叠、长尾间隔异常、硬件事件分段不稳定，或 manifest 中两个比例误差明显偏大。
- CycleTrace 用 `CYCLE_TRACE_SAMPLE_MODE=C` 只采了一个 kernel，但 `split-usecases` 未传入同一个
  `--cycle-kernel-name`，导致 mcTracer 候选包含未被 CycleTrace 采集的其他 kernel。
- `profiler.*` 被用于强结论，但 mcProfiler 不是 per-kernel 采集或 usecase 没有
  `per-usecase-ref` 引用；此时 profiler 只能作命令级旁证或不可用边界。
- `mcprofiler_match_result.status` 不是 `matched`，或某个 usecase 的
  `mcprofiler_match.status=ambiguous`。这表示 mcProfiler occurrence 无法与 mcTracer/CycleTrace
  usecase 建立可信对应，报告中 profiler 指标不可作为该 usecase 的强证据。
- 同一 signature 聚合了多个 mcProfiler occurrence。当前脚本不会把多个 profiler JSON 自动合成为一个
  profiler 视图；此类 usecase 的 CycleTrace/mcTracer 聚合仍可用于排查，但 profiler 指标应转为
  单算子单用例采集确认。

遇到不完全可信情况时，优先排查 `usecases/manifest_<tag>.json` 中的 launches、segments、
match_result 和 usecases。如果无法解释不一致，应转为单算子单用例采集：让 `OP_EXEC_CMD` 只执行一个
目标用例，或使用 `CYCLE_TRACE_SAMPLE_MODE=C` + 精确 `CYCLE_TRACE_KERNEL_NAME` 缩小 CycleTrace
范围，并在 workload 层只保留一个 shape / 一次目标调用。重新采集后再生成主报告，避免把混合数据写成
高置信优化结论。

## 输出

成功匹配后输出：

```text
<run-dir>/usecases/
├── manifest_<tag>.json
├── index_<tag>.md
└── usecase_000/
    ├── artifacts/
    │   ├── tracer_out.json
    │   ├── c-trace_output_dpc_<primary>.json
    │   └── collection_manifest.json       # mcprofiler_reference 指向父级 occurrence 产物
    ├── analysis/
    │   ├── metrics_all_<tag>_usecase_000.json
    │   ├── metrics_key_<tag>_usecase_000.json
    │   └── digest_<tag>_usecase_000.md
    └── REPORT_<tag>_usecase_000.md
```

`manifest_<tag>.json` 是匹配审计入口，包含 launches、segments、match_result、usecases 和
generated_reports。`index_<tag>.md` 是人工阅读入口，汇总 `usecase_id`、matched occurrence、
launch/segment id 和报告路径。usecase 与 occurrence 都使用 0-based 编号，但两者仍不是同一概念：
`usecase_000` 是按 signature 聚合后的用例编号，`occurrence_000` 是 mcProfiler per-kernel occurrence
编号。匹配关系必须以 manifest/index/report 中的 `Usecase mapping` 为准，不能只凭编号相同推断。
单 usecase 报告本身复用标准报告模板；usecase id、匹配 confidence、matched launch/segment id 和
聚合关系以 manifest 为准。判断本次 `split-usecases` 是否实际生成了有效派生报告，也必须以最新
manifest 的 `generated_reports` 为准；目录中遗留的旧 `usecase_*` 报告不代表本次运行有效输出。

`usecase_XXX/artifacts/tracer_out.json` 和 `usecase_XXX/artifacts/c-trace_output_dpc_<primary>.json`
是过滤后的派生输入，不是父级 `artifacts/` 原始文件的重复副本，必须保留以便单 usecase 报告可独立
复查。mcProfiler 文件和 PNG 不复制到 usecase 目录；匹配到单个 occurrence 时，报告通过
`collection_manifest.json.mcprofiler_reference` 读取父级
`artifacts/mcprofiler_per_kernel/occurrence_*`，父级 occurrence 目录仍是唯一审计源。

## Usecase LLM 诊断

`split-usecases` 只生成基础派生报告，不会自动写入 `## 9. LLM Follow-up Diagnosis`。如果最终交付
依据是 usecase 报告，必须为 `manifest_<tag>.json.generated_reports` 中的每个报告补充 LLM 诊断。
本节只说明 usecase 报告定位、文件命名和追加入口；第 9 章内容结构、职责划分和质量门以
`reference/08-llm-followup-diagnosis.md` 为准。

推荐使用统一收尾入口；父级 scope 诊断和每个 generated usecase 诊断都必须准备：

```bash
python3 scripts/trace_report_env.py --config "$ACTIVE_CONFIG" finalize-run \
  --run-dir "$PROFILE_RUN_DIR" \
  --diagnosis-file <parent-scope-diagnosis.md> \
  --diagnosis-dir <local-diagnosis-dir>
```

`<local-diagnosis-dir>` 下按 usecase id 放置诊断正文：

```text
<local-diagnosis-dir>/
├── usecase_000.md
└── usecase_001.md
```

`finalize-run` 会先追加父级 scope 诊断，再读取目标环境中的 manifest，逐个向 generated report
追加第 9 章，并执行完整最终 `check-report`。任一诊断文件缺失、marker 不完整、reasoning chain
不合格，或 usecase 第 9 章没有引用自身基础报告中的性能诊断项，都会返回非 0。
`append-usecase-diagnosis` 仅作为兼容或手工调试入口；它只追加 usecase LLM 诊断，不处理父报告，
也不代表完整 workflow。需要源码行归因时，先按“指标边界”确认 usecase 报告是否已有
per-usecase source-latency。

## 指标边界

- `launch.*` 来自派生 `artifacts/tracer_out.json` 中保留的匹配 launch。若同一 usecase 聚合了多次
  launch，标准报告中的 launch/resource 字段代表该 signature 的 launch 形态；聚合次数和 duration
  汇总看 manifest。
- `cycle.*` 来自派生 CycleTrace JSON 中对应 segment 的硬件事件；同一 usecase 多次执行时会合并这些
  segment 的事件。
- `profiler.*` 若存在，优先来自匹配到该 usecase 的 mcProfiler per-kernel occurrence。若没有匹配
  occurrence，profiler 字段必须保持不可用或命令级边界，不能把通用 `report_*` 写成单 usecase 数据。
- `mcprofiler_scope=per-kernel-unmatched` 或 `per-usecase-multiple-occurrences` 时，派生报告仍可用于
  CycleTrace/mcTracer 层面的排查，但 profiler 指标不可作为强证据；需要 profiler 强结论时应转为
  单算子单用例重采。
- 父级 `analysis/source_latency_<tag>.*` 使用父级 CycleTrace，属于 aggregate source-line
  attribution；当同一目标 kernel 有多个 usecase/occurrence 时，该结果可能混合多个 usecase，
  不能直接作为某个单 usecase 的源码行热点。启用 source-latency 且 `split-usecases` 生成
  usecase reports 时，`collect-run` 会在拆分后对每个派生 usecase CycleTrace 运行
  `source-latency`，输出到对应 `usecases/usecase_XXX/analysis/` 并刷新该 usecase 报告；任一
  usecase 生成或刷新失败都会使 `collect-run` 返回非 0。若这些产物缺失，通常说明使用了旧产物、
  手工拆分结果或未启用 source-latency，应把 source-line attribution 写成边界，或手工确认派生
  CycleTrace JSON 是否适合单独运行 `source-latency`。

## 质量门

`split-usecases` 不覆盖主 `REPORT_<tag>.md`。标准 `collect-run` 只在主报告进入
`per-kernel-multiple-occurrences` 边界时自动触发；其他场景仍可作为手工后处理使用。默认只有匹配状态
达到阈值时才生成派生报告；否则只写 manifest 供人工判断。
`--write-ambiguous true` 不能把没有 matches 的 ambiguous 结果强行变成报告；这种情况下仍应读取
manifest 并排查，或转为单算子单用例重采。
只有“可信度判定”中完全可信的派生报告，才可作为单 kernel 单 usecase 诊断结论交付；其他情况应在报告
中写为数据边界，或重采单算子单用例数据。

父级 `REPORT_<tag>.md` 应保留为 run-level scope/audit artifact：记录采集范围、kernel filter、
manifest/index、aggregate source-latency 边界和 generated report 入口。单 kernel 单 case 的优化
方向必须以对应 generated usecase 报告为准；父级报告不得替代 usecase 报告给出最终优化决策。

出现 `TR-USC-*` 报错时，先按 `reference/09-error-troubleshooting.md` 处理。若 manifest 中
`match_result.status=ambiguous`，不要把派生报告当作高置信单 usecase 证据。
