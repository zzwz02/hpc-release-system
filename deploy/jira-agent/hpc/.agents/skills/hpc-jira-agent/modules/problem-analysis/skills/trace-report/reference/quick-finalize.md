# Quick Finalize

负责 Step 4–5 的 diagnosis 写入、报告质量检查和本次 run 同步，按[主入口状态表](../SKILL.md#按执行状态读取)进入。

## Diagnosis 输入读取规则

`finalize-run` 需要本地 diagnosis Markdown 文件，但 `workflow_state: needs_llm_followup` 阶段的报告和 metrics 仍在目标侧；本地归档只有在 `sync-artifacts` 输出 `workflow_state: complete` 后才可靠存在。

生成 diagnosis 前，按 `report_target_path` / `metrics_key_target_path` 及 [Collect 后路径边界](quick-collect.md#collect-后路径边界)通过目标包装器读取。诊断方法和数据解释使用 [quick-diagnose](quick-diagnose.md)；本文只负责写回和检查。

## 标准入口

普通单报告：

```bash
python3 scripts/trace_report_env.py --config "$ACTIVE_CONFIG" finalize-run \
  --run-dir "$PROFILE_RUN_DIR" \
  --diagnosis-file /path/to/llm_followup_diagnosis.md
```

generated usecase reports：

```bash
python3 scripts/trace_report_env.py --config "$ACTIVE_CONFIG" finalize-run \
  --run-dir "$PROFILE_RUN_DIR" \
  --diagnosis-file /path/to/parent_scope_diagnosis.md \
  --diagnosis-dir /path/to/usecase_diagnoses
```

同步仅覆盖本次 run，不扩大到其他任务或整个工作目录：

```bash
python3 scripts/trace_report_env.py --config "$ACTIVE_CONFIG" \
  sync-artifacts --run-dir "$PROFILE_RUN_DIR"
```

`finalize-run` 成功后必须按其输出的 `required_next_command` 调用 `sync-artifacts`。
`append-diagnosis` / `append-usecase-diagnosis` 仅是深层兼容或手工调试入口，不用于标准 workflow。

## Diagnosis 文件最小结构

`finalize-run` 会自动生成外层 `## 9. LLM Follow-up Diagnosis` 标题和 marker。
`--diagnosis-file` / `--diagnosis-dir` 中的 Markdown 内容应从以下 `###` 小节开始，至少包含：

- `### Summary`
- `### Quality Gate Result`
- `### Final Diagnosis`
- `### Validation Plan`

`### Final Diagnosis` 下面至少包含一个 `#### Diagnosis ...` 诊断块，且每个诊断块必须有自己的
`#### Reasoning Chain`。一个全局 reasoning chain 不够。每个 reasoning chain 必须覆盖：

- Measured evidence
- Mechanism
- Counter-evidence / weaker hypotheses
- Confidence boundary
- Edit rationale
- Validation expectation

最小模板：

```markdown
### Summary

<one-paragraph run/result summary>

### Quality Gate Result

<PASS/FAIL with explicit boundaries>

### Final Diagnosis

#### Diagnosis 1: <evidence-backed claim>

#### Reasoning Chain

- Measured evidence: ...
- Mechanism: ...
- Counter-evidence / weaker hypotheses: ...
- Confidence boundary: ...
- Edit rationale: ...
- Validation expectation: ...

### Validation Plan

<same-shape rerun and metric movement expectations>
```

每个 diagnosis 必须引用当前 artifacts 可支撑的证据。缺失数据写成边界，不得补造指标。

`finalize-run` 前可用轻量自检确认结构：

```bash
rg -n "^### (Summary|Quality Gate Result|Final Diagnosis|Validation Plan)$|^#### Diagnosis|^#### Reasoning Chain$" <diagnosis.md>
```

若缺少 `#### Diagnosis` 或 `#### Reasoning Chain`，`finalize-run` 会以 `TR-LLM-004` 失败；不要先运行再依赖失败发现该结构问题。

## 成功信号

- `finalize-run` 输出 `workflow_state: needs_artifact_sync`。
- 报告包含完整 `## 9. LLM Follow-up Diagnosis`，final `check-report` 通过。
- `sync-artifacts` 输出 `workflow_state: complete`。
- 本次 run 目录同步到 `$LOCAL_WORKDIR/profile-artifacts/<run-basename>/`。

## 常见失败

- `TR-REP-005`：缺 LLM 第 9 章，不要重采；补齐 diagnosis 后重新 `finalize-run`。
- `TR-REP-007`：generated usecase 报告缺第 9 章；为每个 generated usecase 补诊断。
- 其他明确 `[TR-*]`：先按 `^## TR-...$` 标题局部读取 `reference/09-error-troubleshooting.md` 对应详情块。

## 深层文档读取门槛

只在本文无法完成下一步决策时读取深层文档：

- `reference/08-llm-followup-diagnosis.md`：quick 结构不足、`TR-LLM-*`、`TR-REP-005/007`、generated usecase 复杂失败。
- `reference/07-report-template.md`：基础报告结构异常、模板维护、`check-report` 指向基础章节缺失。
- `reference/01-directory-layout.md`：sync 目标边界不清、本地/远端路径混淆。
- `reference/13-usecase-split.md`：final target 是 generated reports，且 manifest/index/report 对应关系不清。
