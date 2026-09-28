# Quick Collect

负责 Step 0–2 的配置、采集与失败处理；阶段切换和通用恢复上限见[主入口](../SKILL.md#按执行状态读取)。

## 标准入口

控制侧运行 `trace_report_env.py`，负责 active config、脚本发布、状态和产物回传。`LOCAL_WORKDIR` 保存配置及同步归档，不存在时由入口递归创建；为空、无法创建或不可用时才视为 Preflight 失败。

目标侧在 `REMOTE_WORKDIR` 中运行应用、采集、解析和报告检查；可按配置选择 local/docker/ssh/ssh+docker。入口将脚本发布到目标 `.trace-report/scripts/` 后执行内部采集与 pipeline。local 模式可同机，但控制侧归档与目标侧执行目录职责仍分开。

直接提供参数时，用 `collect-run --set KEY=VALUE` 初始化必要配置；该模式会基于仓库模板写入 `$LOCAL_WORKDIR/.trace-report/config/trace_report_env.yaml`：

```bash
python3 scripts/trace_report_env.py \
  collect-run --run-dir profile-artifacts/<kernel>_v<N>_<tag> \
  --set LOCAL_WORKDIR=/path/to/local/workdir \
  --set REMOTE_WORKDIR=/path/to/remote/workdir \
  --set 'OP_EXEC_CMD=argv text run from target workdir'
```

提供配置文件时，显式传入 `--config`。配置文件可以位于任意本地可读路径；脚本只读加载该文件，不复制、不改写：

```bash
python3 scripts/trace_report_env.py --config /any/existing/user-trace-report.yaml \
  collect-run --run-dir "$PROFILE_RUN_DIR"
```

`--config` 与 `collect-run --set` 互斥，不得在同一条 `collect-run` 中混用。配置文件中的未知键会直接失败，避免参数拼写错误静默失效。`collect-run` 会输出 `config_source: direct|file` 和最终使用的 `config_path`。

不要手工调用 `scripts/collect_trace_profile.sh`，也不要手工拼接部分产物继续分析；底层 pipeline 命令只在深层排查文档中按需出现。

### OP_EXEC_CMD quoting 规则

`OP_EXEC_CMD` 通常包含空格或 `--case-id` 等目标命令参数；必须把完整
`KEY=VALUE` 作为同一个 `--set` 参数传给 `trace_report_env.py`：

```bash
--set 'OP_EXEC_CMD=./profile_target_wrapper.sh --case-id case_001'
```

不要写成：

```bash
--set OP_EXEC_CMD=./profile_target_wrapper.sh --case-id case_001
```

后一种写法会让 shell 把 `--case-id` 拆成 `trace_report_env.py` 自己的参数，常见错误是
`unrecognized arguments: --case-id ...`。出现该错误时，不要重采或读深层文档；先按本节把
`OP_EXEC_CMD` 重新 quote 成单个 `--set 'OP_EXEC_CMD=...'` 参数后重跑。

## 必须确认

- `OP_EXEC_CMD` 是目标 workdir 下可直接执行的 argv 文本；不含 `env` 前缀、管道、重定向或 shell 展开。环境通过 active config、wrapper 或目标进程环境注入。
- `OP_EXEC_CMD` 含空格时，命令行上必须使用 `--set 'OP_EXEC_CMD=...'`，保证完整 argv 文本没有被 shell 拆分。
- run 目录符合 `profile-artifacts/<kernel>_v<N>_<tag>`。
- `PROFILE_RUN_DIR`、`PROFILE_TAG`、`PROFILE_KERNEL`、`PROFILE_VERSION` 由 `collect-run` 自动派生。
- 除非用户明确要求，不修改 `CONTAINER_NAME`、`SERVER_USER`、`SERVER_HOST`、`LOCAL_WORKDIR`、`REMOTE_WORKDIR`、`OP_EXEC_CMD`。

## Password SSH 最小规则

当 `SERVER_USER` / `SERVER_HOST` 非空且用户提供 SSH 密码时，后续所有本地发起的
`trace_report_env.py --config "$ACTIVE_CONFIG" validate`、`run`、`collect-run`、
`finalize-run`、`check-report` 和 `sync-artifacts` 命令都必须在同一进程环境中设置
`TRACE_REPORT_SSH_PASSWORD`。只设置 `SSHPASS` 不会启用 trace-report 的 password SSH
包装。密码不得写入 active config、diagnosis 文件、报告或任何归档记录。

SSH 或 ssh+docker 模式下，首次 `validate`、`collect-run`、`run`、`finalize-run`、
`check-report` 或 `sync-artifacts` 必须使用能访问目标 SSH 端口的执行通道；不要先在已知网络受限的沙箱中试跑。若执行表面要求网络权限授权，等待授权不计为采集失败或重试次数。

`required_next_command` 只输出标准命令，不包含密码环境变量；agent 执行下一步时负责继续注入
`TRACE_REPORT_SSH_PASSWORD`。出现 `TR-SSH-*` 后，只按错误码局部读取
`reference/09-error-troubleshooting.md` 对应小节；不要因为 password SSH 本身读取完整
`reference/00-preflight.md`。

## 采集范围与有效性

未设 `CYCLE_TRACE_KERNEL_NAME` 时报告不是严格单 kernel 单 usecase；设置后按目标 kernel 收敛，可信粒度取决于三工具是否对应同一 kernel/usecase。切换该值后，旧命令级 CycleTrace/mcProfiler、pipeline 输出和报告不能直接复用为目标 kernel 证据。

目标 kernel 有多个 usecase/occurrence 时，`collect-run` 自动拆分；检查 `usecases/manifest_<tag>.json` 的 `generated_reports`，下一阶段按单 usecase 报告诊断，不能混跑多个 shape 后作单 case 结论。

CycleTrace 必须是可读 JSON，`traceEvents` 非空且含 MTE/STE/MMA/BSM/GLOBAL/ARRIVE/LDU 硬件类别事件；空文件、metadata-only 或无硬件事件均需重采。`collection_manifest.json.invalid_required` 非空时停止或恢复，不用旧报告继续诊断。mcProfiler 是否为强制产物由 `REQUIRE_MCPROFILER` 决定。

## 成功信号

`collect-run` 成功后至少应看到：

- `artifacts/tracer_out.json`
- `artifacts/c-trace_output_dpc_<primary>.json`
- `analysis/*.json`、`analysis/*.md`（包括 `digest_<tag>.md`）
- `analysis/metrics_all_<tag>.json`
- `analysis/metrics_key_<tag>.json`
- `REPORT_<tag>.md`
- 报告的 Profiling setup 显示实际 `MACA_VISIBLE_DEVICES`
- `workflow_state: needs_llm_followup`

若 `ENABLE_SOURCE_LATENCY=true`，还必须有 `analysis/source_latency_<tag>.md/.csv/.json`，且基础报告已吸收 source-latency summary。

## Collect 后路径边界

`collect-run` 输出 `workflow_state: needs_llm_followup` 时，`report`、`metrics_key`、`metrics_all` 和对应 `*_target_path` 都是目标侧 run 路径；此时 `local_sync_pending: true`，不要直接读取 `$LOCAL_WORKDIR/profile-artifacts/<run>`。

sync 前需要读取诊断输入时，用目标执行包装器局部读取：

```bash
python3 scripts/trace_report_env.py --config "$ACTIVE_CONFIG" run -- \
  sed -n '1,260p' profile-artifacts/<run>/REPORT_<tag>.md

python3 scripts/trace_report_env.py --config "$ACTIVE_CONFIG" run -- \
  sed -n '1,220p' profile-artifacts/<run>/analysis/metrics_key_<tag>.json
```

只有 `sync-artifacts` 输出 `workflow_state: complete` 后，才按 `local_run_dir` 或 `$LOCAL_WORKDIR/profile-artifacts/<run-basename>/` 读取本地归档。

## 失败处理

1. 先从命令摘要读取 `failed_stage`、`cause_code`、`last_stderr` 和 `log:` 路径；必要时只读取对应日志 tail。若阶段日志显示 `status: reused` 或 `status: skipped`，说明本次没有执行该底层工具。
2. 从摘要或日志 tail 提取明确 `[TR-*]`。
3. 有错误码时按标题精确搜索 `reference/09-error-troubleshooting.md` 的对应详情块，例如：

```bash
rg -n "^## TR-COL-006$" -A 12 trace-report/reference/09-error-troubleshooting.md
```

4. 若 `-A 12` 未覆盖完整详情块，再按同一标题增加 `-A` 范围；不要因出现错误码就全文读取 `09-error-troubleshooting.md`。
5. 同时有 `TR-WF-*` 阶段码和具体原因码时，遵守主入口字段优先级：先定位 `cause_code` 或日志中的具体原因码，必要时再查阶段码。
6. `TR-COL-006` 在重试前先按[错误条目](09-error-troubleshooting.md#tr-col-006)核对适用条件、恢复算法与预算；不在本文件另维护算法副本。
7. 按[主入口恢复与停止条件](../SKILL.md#恢复与停止条件)计数和停止，保留本次调整与结果。

## 本阶段按需参考

遵守主入口的扩展阅读条件，按以下具体缺口选择章节；配置项存在、SSH/docker 模式、正常提供受保护配置或普通 `validate` / `collect-run` 本身不触发扩展阅读。

| 缺口或任务 | 参考 |
|---|---|
| active config 冲突、SSH/docker/工具路径校验或 preflight 失败；脚本报告受保护配置冲突，或用户要求修改 | [00-preflight](00-preflight.md) |
| run 名无法派生、同步边界不清、本地/远端路径混淆 | [01-directory-layout](01-directory-layout.md) |
| 阶段恢复、清理、离线解析、compare、复杂采集 scope 或产物复用问题超出本文 | [02-collection](02-collection.md) |
| 无明确错误码，需要解释背景或常见指标现象 | [03-common-issues](03-common-issues.md) |
| `ENABLE_SOURCE_LATENCY=true`，采集前准备 lineinfo；或其输入缺失、归因/coverage 解释和失败 | [10-source-latency](10-source-latency.md) 的对应章节 |
| 安装、启用或排查 pydpg bulk | [11-pydpg-bulk](11-pydpg-bulk.md) |
| 需要解释、调整或排查 sample mode、AP、DPC、PEU、DPG 参数 | [12-cycle-trace-options](12-cycle-trace-options.md) |
| 多 usecase，`generated_reports` 非空、manifest ambiguous，或用户要求单 usecase 结论 | [13-usecase-split](13-usecase-split.md) |

source-latency 准备只读取其输入和质量/归因边界，不因此继续读取其他深层文档。启用时必须在 Step 1 前设置 `ENABLE_SOURCE_LATENCY=true` 并准备 lineinfo；各参考均需各自触发条件成立。参数仅被设置且没有冲突、失败或解释需求时，不预读参数手册。
