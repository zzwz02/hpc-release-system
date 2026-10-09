---
name: trace-report
description: "为 C500 算子或 kernel 调查提供可选的指令与硬件行为证据：通过 mcTracer、CycleTrace、mcProfiler 采集和分析 trace，并形成可复核报告。"
---

# trace-report

本 Skill 为 C500 kernel、指令和硬件行为调查提供证据。使用前明确待区分的问题及采集如何改变判断；阶段计时、组件对照或其他获准工具也可能更适合，不能仅凭“性能”或“回退”标签进入本流程。

选用后先采集再诊断，不编造指标，缺失数据写成边界。正确性未通过时先返回上层诊断；本 skill 交付 trace 报告，原案例精度、性能验收和优化有效性仍由上层流程负责。

## 按执行状态读取

每次先读本入口，再按下表进入当前阶段。表中列出的参考不是预读清单，不能在采集前提前读取诊断或收尾文档。

| 当前状态或任务 | 操作与应读文档 | 转入下一阶段的条件 |
|---|---|---|
| Step 0–2：准备或执行采集 | [quick-collect](reference/quick-collect.md)：配置、执行位置、采集范围、标准命令和产物有效性；在本阶段内处理采集失败 | `collect-run` 返回 `needs_llm_followup` |
| Step 3：`needs_llm_followup`，需要分析采集证据 | [quick-diagnose](reference/quick-diagnose.md)：读取目标侧报告及指标，结合该阶段规定的诊断基础文档形成结论 | 形成 artifacts 支持的 diagnosis 与 Next Concrete Edit，证据不足则明确边界 |
| Step 4：`needs_llm_followup`，需要生成 diagnosis 文件或执行 finalize | [quick-finalize](reference/quick-finalize.md)：写入第 9 章并执行 `finalize-run` / `check-report` | `needs_artifact_sync` 且报告检查通过 |
| Step 5：`needs_artifact_sync` | 同一 [quick-finalize](reference/quick-finalize.md) 中的 `sync-artifacts`，按输出命令同步本次 run | `workflow_state: complete`，本地归档就绪 |
| `failed` 或明确 `[TR-*]` | 当前阶段 quick 的失败处理；按具体错误码局部查询[错误手册](reference/09-error-troubleshooting.md)。第 9 章、finalize 或 sync 错误进入 quick-finalize | 补齐条件后按实际状态继续，遵守下述停止条件 |

各阶段 quick 维护本阶段的必要输入、质量要求和特殊参考入口。扩展阅读时明确触发源（用户要求、失败、错误码、quick 无法决策或结构化字段冲突）、当前文档的具体缺口和目标章节；缺少其中一项时不展开。采集前 source-latency 输入准备，以及诊断阶段要求的基础文档，属于各自阶段明确列出的必要阅读。

完整 stdout/stderr、raw artifacts 和工具日志留在磁盘，只读取当前决策所需的摘要或片段。`DEVELOPER_README.md` 仅供人类维护，不属于 agent 执行流程；入口、阶段参考和脚本帮助不足时报告文档缺口。

## 脚本输出字段优先级

`trace_report_env.py` 的结构化输出是 workflow 状态来源；文档示例命令只作说明，不覆盖脚本输出的下一步命令。

| 字段 | 用途 |
|---|---|
| `workflow_state` | 决定当前 workflow 状态；`needs_llm_followup` 进入 Step 3，`needs_artifact_sync` 进入 Step 5，`complete` 表示 workflow 完成，`failed` 进入异常处理。 |
| `required_next_command` | 决定下一条标准命令；优先于 reference 中的示例命令和人工记忆中的命令。 |
| `artifact_location` / `local_sync_pending` | 决定报告和 metrics 路径所在侧；`needs_llm_followup` 时为 target 且本地同步未完成，只有 `sync-artifacts` 输出 `complete` 后才读取 `local_run_dir`。 |
| `final_diagnosis_target` | 决定 LLM 诊断目标；`main report` 写单报告诊断，`usecases generated_reports` 同时准备父级 scope 诊断和每个 generated usecase 诊断。 |
| `error_code` / `cause_code` / `[TR-*]` | 决定异常排查入口；先按具体错误码标题局部读取 `reference/09-error-troubleshooting.md`，再看阶段码。 |

## 恢复与停止条件

- 普通错误最多执行 3 次恢复尝试；不得无参数变化地重复采集，不手工调用底层脚本或拼接未校验的部分产物。
- `TR-COL-006` 先按[对应错误条目](reference/09-error-troubleshooting.md#tr-col-006)判断 small-workgroup 恢复是否适用，算法和预算只在该条目维护。三次恢复失败、campaign deadline 耗尽或不可恢复时停止，报告失败阶段、缺失/无效产物、最近错误码、已尝试次数和日志路径。
- 自动恢复不得把 `REQUIRE_MCPROFILER=false` 改成 `true`；允许 `true` 降为 `false`。重新启用硬校验不能作为自动恢复策略。
- 等待执行通道授权不计为采集失败或恢复尝试。受保护的目标、设备、案例和验证命令按 quick-collect 保持不变。

## 标准入口与交付

标准 workflow 只直接调用 `scripts/trace_report_env.py`；内部脚本职责见[维护地图](reference/14-script-module-map.md)，仅在维护或排查其职责时读取。

汇报区分 Evidence、Inference 和 Action。agent 临时命令、shell 重定向或局部检查失败不能直接认定为工具缺陷。最终完成必须经过阶段文档规定的报告质量检查与产物同步，不能只用采集成功代替完整 workflow。
