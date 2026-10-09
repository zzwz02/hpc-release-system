# 采集与解析

本文负责标准采集入口、会影响采集行为的参数、成功产物质量门、阶段恢复、清理、离线解析和对比入口。

本文不是标准 `collect-run` 前必读文档。普通采集只读 `reference/quick-collect.md` 即可。不要为了确认普通参数、普通成功产物或普通质量门而读取本文。只有阶段恢复、清理归档、离线解析、compare、复杂采集 scope、复杂产物复用边界，或 quick-collect 无法决策标准采集行为时，才读取本文对应章节。

## 读取门槛

常规采集先读 `reference/quick-collect.md`。只有 quick 文档无法覆盖参数细节、阶段恢复、清理归档、离线解析、compare 或复杂产物复用边界时，才读取本文；明确 `[TR-*]` 失败优先按 `^## TR-...$` 标题局部读取 `reference/09-error-troubleshooting.md` 对应详情块。

| 边界 | 文档 |
|---|---|
| 本文负责 | `collect-run` 主路径、参数来源、成功产物、阶段恢复、离线解析、compare |
| 环境、active config、SSH/password SSH、目标工具路径 | `reference/00-preflight.md` |
| run 目录命名、`PROFILE_*` 派生和同步边界 | `reference/01-directory-layout.md` |
| 明确 `[TR-*]` 报错 | 按 `^## TR-...$` 标题局部读取 `reference/09-error-troubleshooting.md` 对应详情块 |
| 非报错类背景和常见误解 | `reference/03-common-issues.md` |
| CycleTrace 参数语义 | `reference/12-cycle-trace-options.md` |
| source-latency 输入、输出和归因模型 | `reference/10-source-latency.md` |
| pydpg bulk 安装和启用 | `reference/11-pydpg-bulk.md` |
| usecase 匹配、manifest 和派生报告边界 | `reference/13-usecase-split.md` |
| LLM 第 9 章和 finalize 规则 | `reference/08-llm-followup-diagnosis.md` |
| 脚本模块职责和维护边界 | `reference/14-script-module-map.md` |

## 标准入口

标准采集统一使用 `collect-run`。该入口会完成 Preflight、active config 校验、run
目录派生、脚本同步、目标目录创建、mcTracer/CycleTrace 必需采集、mcProfiler 条件采集，以及 trace pipeline 归档/分析；
恢复场景中 pipeline 可能使用 `trace_profile_pipeline.py analyze` 复用已归档 artifacts。
不要在标准 workflow 中手工拆分执行 `derive-run`、目标 `mkdir`、
`sync-scripts` 或 `.trace-report/scripts/collect_trace_profile.sh`。
本文后续出现的底层命令只用于离线恢复、复查或错误定位，不覆盖 `collect-run` / `finalize-run` 的标准入口。

`collect-run` 成功后会运行基础 `check-report`，确认采集和基础报告产物完整。若
`ENABLE_SOURCE_LATENCY=true`，这也包括 source-latency 后处理、报告刷新和源码行归因吸收。
若基础报告显示 `mcProfiler scope=per-kernel-multiple-occurrences`，`collect-run` 会继续自动执行
`split-usecases`，把同名目标 kernel 下的多个 usecase 拆成派生报告；自动拆分结果以
`<run-dir>/usecases/manifest_<tag>.json` 为准。
但完整 workflow 还必须按 `reference/quick-finalize.md` 追加
`## 9. LLM Follow-up Diagnosis`，通过最终 `check-report`，并执行第 5 步同步本次 run 目录回本地。
`collect-run` 结束时会输出 `workflow_state: needs_llm_followup`、`final_diagnosis_target`
和推荐的 `required_next_command`；优先使用 `finalize-run` 追加诊断。`finalize-run`
成功后会输出 `workflow_state: needs_artifact_sync` 和推荐的 `sync-artifacts` 命令；
`sync-artifacts` 成功并输出 `workflow_state: complete` 后才算完整 workflow 完成。
不要把只有 `collect-run` 成功、尚未追加 LLM 诊断或尚未同步本地 artifacts 的报告作为最终交付。

若 quick-collect 无法决策，再按必要性定位以下深层 reference：

| 需要确认的内容 | 参考文件 |
|---|---|
| 环境、active config、SSH/password SSH、工具路径和参数来源 | `reference/00-preflight.md` |
| run 目录命名、`PROFILE_*` 派生和标准目录结构 | `reference/01-directory-layout.md` |
| 明确 `[TR-*]` 报错 | 按 `^## TR-...$` 标题局部读取 `reference/09-error-troubleshooting.md` 对应详情块 |

后续独立目标环境命令必须通过
`python3 scripts/trace_report_env.py --config "$ACTIVE_CONFIG" run -- ...` 包装，
在 `[REMOTE_WORKDIR]` 对应的目标执行环境内完成。

## 一键采集

首次运行且还没有 active config 时，用 `--set KEY=VALUE` 初始化配置并采集：

```bash
python3 scripts/trace_report_env.py collect-run \
  --run-dir profile-artifacts/<kernel>_v<N>_<tag> \
  --set LOCAL_WORKDIR=... \
  --set REMOTE_WORKDIR=... \
  --set MACA_VISIBLE_DEVICES=-1 \
  --set AUTO_SELECT_DEVICE_TIMEOUT_SECONDS=120 \
  --set 'OP_EXEC_CMD=./profile_target_wrapper.sh --case-id <case_id>' \
  --set HEURISTIC_BOUND_MODE=coarse \
  --set COLLECT_TIMEOUT_SECONDS=600 \
  --set SKIP_MCPROFILER=false \
  --set REQUIRE_MCPROFILER=false \
  --set CYCLE_TRACE_TARGET_PEU=1 \
  --set CYCLE_TRACE_DPG_PAGE_NUM=32 \
  --set CYCLE_TRACE_TARGET_AP=0 \
  --set CYCLE_TRACE_DPC_ID=0 \
  --set PROFILER_PORT=50123 \
  --set ENABLE_SOURCE_LATENCY=false \
  --set MC_TRACER_BIN=/opt/maca/bin/mcTracer \
  --set CYCLE_TRACE_BIN=/opt/maca-20260505/bin/cycle-trace-ng \
  --set MC_PROFILER_BIN=/opt/maca/restricted/Tools/mcProfiler/mcProfiler-ubuntu18.04/mcProfiler
```

三工具路径以目标环境实际安装位置为准；示例中的不同前缀来自默认环境配置，不表示必须统一到同一个安装目录。

已有 active config 时，必须显式传入 `--config "$ACTIVE_CONFIG"`：

```bash
ACTIVE_CONFIG="$LOCAL_WORKDIR/.trace-report/config/trace_report_env.yaml"

python3 scripts/trace_report_env.py --config "$ACTIVE_CONFIG" collect-run \
  --run-dir profile-artifacts/<kernel>_v<N>_<tag>
```

`config/trace_report_env.yaml` 是本 skill 仓库内的本地模板；实际运行配置是当前服务器中的
`[LOCAL_WORKDIR]/.trace-report/config/trace_report_env.yaml`。配置 YAML 不需要同步到远程服务器或容器。
若本地 active config 已存在，默认拒绝覆盖；只有明确要覆盖本地配置时才使用 `--force`。
后续所有配置读取和允许的参数修改都必须发生在本地 active config 上。

`OP_EXEC_CMD` 是目标 workdir 下可直接执行的 argv 文本，不是 shell 脚本片段。不要在其中使用
`env` 前缀、shell 内建、管道、重定向、命令替换、通配符或依赖 shell 展开的表达式；mcTracer、
CycleTrace 和 mcProfiler 可能按 argv 直接启动目标程序。环境变量应通过 active config、
wrapper、容器环境或目标执行包装注入。示例：

```text
OP_EXEC_CMD=./profile_target_wrapper.sh --case-id fp32_router_gemm_m1_fp32
```

通过 CLI `--set` 传入含空格的 `OP_EXEC_CMD` 时，必须引用整个 `KEY=VALUE` 参数，
例如 `--set 'OP_EXEC_CMD=./profile_target_wrapper.sh --case-id fp32_router_gemm_m1_fp32'`。
只引用 VALUE 或完全不引用会让 shell 把 `--case-id` 拆成 `trace_report_env.py` 的额外参数。

run 目录名必须符合 `profile-artifacts/<kernel>_v<N>_<tag>`；`collect-run` 会自动派生
`PROFILE_RUN_DIR`、`PROFILE_TAG`、`PROFILE_KERNEL` 和 `PROFILE_VERSION`。命名和派生规则见
`reference/01-directory-layout.md`。

## 参数来源

底层采集脚本参数由 `collect-run` 从 active config 和 run 目录自动传入：

`collect-run` 会显式传入必需采集参数、source-latency 开关、工具路径和 mcProfiler port；
同时，目标执行包装会把 active config 中的所有已知键注入目标环境变量。底层脚本的
`CYCLE_TRACE_SAMPLE_MODE`、`CYCLE_TRACE_KERNEL_NAME`、`CYCLE_TRACE_KERNEL_REPEAT` 和
`CYCLE_TRACE_TOOLS_JSON` 默认从这些环境变量读取；只有手工直接调用
`.trace-report/scripts/collect_trace_profile.sh` 时，才需要显式传下面的可选 CLI 参数。

| 参数 | 用途 |
|---|---|
| `--run-dir` | profile 产物归档目录，来自 `--run-dir` |
| `--tag` | 报告和 JSON 文件名 tag，由 run 目录 basename 派生 |
| `--exec-cmd` | 被 profile 的执行命令，来自 YAML `OP_EXEC_CMD` |
| `--device` | 解析后的实际采集设备；当 YAML `MACA_VISIBLE_DEVICES=-1` 时由 collect-run 自动选择空闲 GPU |
| `--target-peu` | YAML `CYCLE_TRACE_TARGET_PEU`，CycleTrace 目标 PEU bitmap，范围 1-15；默认 `1` 表示 `0x1` 采集 PEU 0；15 表示 `0b1111` 采集 4 条 PEU |
| `--dpg-page-num` | YAML `CYCLE_TRACE_DPG_PAGE_NUM`，CycleTrace DPG buffer 页数，单位 2M；工具默认 `32` 即 64M |
| `--target-ap` | YAML `CYCLE_TRACE_TARGET_AP`，CycleTrace 目标 AP，范围 0-15；工具默认 `0` |
| `--dpc-id` | YAML `CYCLE_TRACE_DPC_ID`，CycleTrace 目标 DPC；格式为 `0` 或 `2,3` 这类无空格 0-7 列表；默认 `0`，采集 DPC 0 |
| `--heuristic-bound-mode` | YAML `HEURISTIC_BOUND_MODE`，C500 heuristic bound 粒度：`coarse` 或 `detailed`；不影响 Roofline bound |
| `--collect-timeout-seconds` | YAML `COLLECT_TIMEOUT_SECONDS`，三工具采集共享超时预算，默认 `600` 秒；只限制 mcTracer、cycle-trace-ng 和 mcProfiler，不限制 trace pipeline 归档/分析 |
| `--require-mcprofiler` | YAML `REQUIRE_MCPROFILER`，默认 `false`；`true` 时 mcProfiler 两个 JSON 必须完整存在且可解析，`false` 时 mcProfiler 缺失/超时不阻断报告 |
| `--enable-source-latency` | YAML `ENABLE_SOURCE_LATENCY`，默认 `false`；`true` 时采集脚本让 `cycle-trace-ng` 执行 `MACA_LINEINFO_CYCLE_TRACE_EXEC_CMD`，为空时回退 `MACA_LINEINFO_BINARY`，并追加运行期 `--kernel-probe`，collect-run 在常规报告后继续运行严格 source-latency 阶段 |
| `--mctracer-bin` / `--cycle-trace-bin` / `--mcprofiler-bin` | YAML 工具路径；采集脚本不在内部补默认值 |
| `--profiler-port` | YAML `PROFILER_PORT`，默认 `50123`；传给 `mcProfiler perf_exec --port`，用于避开本地端口占用 |

可选参数：

| 参数 | 用途 |
|---|---|
| `--workdir` | 覆盖执行 `--exec-cmd` 的目录；未传时使用当前目录 |
| `--case-name` | 覆盖 mcProfiler `--casename` |
| `--cycle-sample-mode` | 覆盖 cycle-trace-ng `--sample-mode`；默认读取 `CYCLE_TRACE_SAMPLE_MODE`，仍为空则由工具使用默认 `A(all kernels)`；`A/B/C/D` 语义见 `reference/12-cycle-trace-options.md` |
| `--cycle-kernel-name` | 覆盖 `CYCLE_TRACE_KERNEL_NAME`，临时将 `CYCLE_TRACE_TOOLS_JSON` 的 `cycleTrace.kernels` 收敛为只包含目标 kernel 的列表 |
| `--cycle-kernel-repeat` | 覆盖 `CYCLE_TRACE_KERNEL_REPEAT`，作为单目标 kernel 的 `repeat` 值 |
| `--cycle-tools-json` | 覆盖 `CYCLE_TRACE_TOOLS_JSON`，默认 `/opt/maca/etc/tools.json` |

`--exec-cmd` 会按 shell quoting 规则解析成 argv，并直接传给 mcTracer 和 CycleTrace；
不要依赖 shell 展开、管道、重定向或内联环境变量赋值。`--case-name` 不要求用户传入；
脚本默认使用 `<run-dir>` 的 basename，即 Workflow 确定的 `<kernel>_v<N>_<tag>`，
作为 mcProfiler case 名称。

`PROFILER_PORT` 控制 mcProfiler 的本地服务端口。默认 `50123` 与 mcProfiler 既有行为保持一致；
若目标环境中该端口被占用，在本地 active config 中改成未占用端口后重新执行 `collect-run`，
底层命令会传入 `mcProfiler perf_exec --port "$PROFILER_PORT"`。

## CycleTrace 采集范围控制

`CYCLE_TRACE_SAMPLE_MODE` 为空时，标准采集不传 `--sample-mode`，由工具使用默认
`A(all kernels)`。当 CycleTrace JSON 过大或需要只采目标 kernel 时，设置
`CYCLE_TRACE_SAMPLE_MODE=C` 和精确的 `CYCLE_TRACE_KERNEL_NAME`；若设置 kernel name 但
sample mode 为空，脚本会自动使用 `C`。`A/B/C/D`、PEU、AP、DPC、DPG 和未暴露工具参数的详细语义见
`reference/12-cycle-trace-options.md`。

`CYCLE_TRACE_KERNEL_NAME` 必须匹配 mcTracer kernel event 的 `args.name`，即 mangled/internal
kernel name。脚本会在运行 CycleTrace 前校验该值；不匹配时直接报 `TR-COL-016` 并列出候选值。设置
kernel name 后，CycleTrace、mcTracer 分析字段和 mcProfiler per-kernel 采集都会进入目标 kernel
scope；最终 `REPORT_<tag>.md` 会展示 target kernel、repeat、sample mode 和 scope note。

当 small-workgroup recovery campaign 为解决已核验的 `TR-COL-006` 而重复 trace target 时，重复仅增加该次采集命令内目标 kernel 的 launch 次数；不会改变单次 launch 的 `grid`、`block` 或 workgroup_count。报告会显示 target launch count 和 invocation scope；多 launch 的 CycleTrace instruction count 与 span 只能作为聚合采样证据，性能验证仍应使用原始未重复命令。

```yaml
CYCLE_TRACE_SAMPLE_MODE: "C"
CYCLE_TRACE_KERNEL_NAME: "<target_kernel_name>"
CYCLE_TRACE_KERNEL_REPEAT: "1"
CYCLE_TRACE_TOOLS_JSON: "/opt/maca/etc/tools.json"
```

切换 `CYCLE_TRACE_KERNEL_NAME` 会改变采集 scope。同一 run 目录中旧的命令级 CycleTrace、mcProfiler、
pipeline 输出和 report 默认不能复用；只有 mcTracer `tracer_out.json` 可作为候选 kernel 和 launch
证据复用。若目标 kernel 出现多个 occurrence，主报告会进入
`mcProfiler scope=per-kernel-multiple-occurrences`，后续自动 `split-usecases` 的完整规则见
`reference/13-usecase-split.md`。

`COLLECT_TIMEOUT_SECONDS` 只统计三工具命令本身的共享耗时预算。一次 `collect-run` 中，
mcTracer、cycle-trace-ng 和 mcProfiler 对本次实际执行的工具共同消耗该预算。mcTracer 或
cycle-trace-ng 超过剩余预算时，采集以 `TR-COL-013` 失败；mcProfiler 超时是否阻断由
`REQUIRE_MCPROFILER` 决定：`true` 时失败，`false` 时终止本次 mcProfiler 进程组、清理本次 mcProfiler 半成品并继续生成
带 `N/A` profiler 边界的报告。三工具在预算内完成后，trace pipeline 的归档、解析和报告生成
不受该超时限制。底层采集脚本每次只执行一次采集，但会先检查同一 run 目录中已有的有效产物：
已成功阶段会跳过，不再重新采集；失败重跑时从第一个缺失或无效阶段继续，并重新获得一份
`COLLECT_TIMEOUT_SECONDS` 预算。失败或超时时，脚本只终止本次失败工具启动的进程组，并删除该失败阶段
的本次产物；不会全局清理历史或用户手工启动的进程。

参数合法性和 SSH/password SSH 输入方式见 `reference/00-preflight.md`；run 目录派生规则见
`reference/01-directory-layout.md`。

## 成功产物

标准采集会运行 mcTracer、CycleTrace，并按 `REQUIRE_MCPROFILER` 策略尝试 mcProfiler，
随后标准化原始产物并调用 trace pipeline 生成 JSON 和 Markdown 报告。若同一 run 目录已有
通过校验的阶段产物，`collect-run` 会复用这些产物，只执行缺失或无效的后续阶段。

| 工具 | 标准化输出 | 作用 |
|---|---|---|
| mcTracer | `artifacts/tracer_out.json` | grid/block、寄存器、共享内存、private memory、occupancy |
| CycleTrace | `artifacts/c-trace_output_dpc_<primary CYCLE_TRACE_DPC_ID>.json`；多 DPC 时同时归档其他 `artifacts/c-trace_output_dpc_*.json` | 指令事件、`cat` 硬件分类、WIF、wave 生命周期；自动分析仅使用 primary DPC |
| mcProfiler | `artifacts/mcprofiler_report_dumped.json`、`artifacts/mcprofiler_report.txt.json`、每类最新 `*.png`；`REQUIRE_MCPROFILER=false` 时可缺失 | duty ratio、IPC、cache hit、bank conflict、经验 Roofline；缺失时对应数据为 `N/A` |
| source-line cycle attribution | `analysis/source_latency_<tag>.md`、`.csv`、`.json` | CycleTrace-only 源码行 cycle attribution；默认可选，`ENABLE_SOURCE_LATENCY=true` 时属于一键采集强制后处理质量门，成功后刷新基础 `REPORT_<tag>.md` |
| trace pipeline | `analysis/*.json`、`analysis/*.md`、`REPORT_<tag>.md` | 指标解析、bound 判断、覆盖度摘要、报告和对比；存在 `analysis/source_latency_<tag>.json` 时基础 report 会合并源码行归因摘要 |

标准一键采集成功时，以下必需产物必须存在且有效：

- `<run-dir>/artifacts/tracer_out.json`
- `<run-dir>/artifacts/c-trace_output_dpc_<primary CYCLE_TRACE_DPC_ID>.json`
- `<run-dir>/analysis/digest_<tag>.md`
- `<run-dir>/analysis/metrics_all_<tag>.json`
- `<run-dir>/analysis/metrics_key_<tag>.json`
- `<run-dir>/REPORT_<tag>.md`

当 `REQUIRE_MCPROFILER=true` 时，以下产物也必须存在且可解析：

- `<run-dir>/artifacts/mcprofiler_report_dumped.json`
- `<run-dir>/artifacts/mcprofiler_report.txt.json`

当 `REQUIRE_MCPROFILER=false` 时，`collection_manifest.json` 会用
`mcprofiler_available` 记录两个 mcProfiler JSON 是否完整可解析；为 `false` 时
`metrics_all_<tag>.json` 中 `profiler.available=false`，报告中的 duty、IPC、cache、bank
conflict 和 Roofline 数据为 `N/A`。

CycleTrace 的内容校验比“文件存在”更严格：primary DPC JSON
`c-trace_output_dpc_<primary CYCLE_TRACE_DPC_ID>.json` 必须存在且可读，必须包含非空
`traceEvents`，且至少包含 MTE/STE/MMA/BSM/GLOBAL/ARRIVE/LDU 中任一硬件类别事件。
多 DPC 采集时，`CYCLE_TRACE_DPC_ID` 中请求的每个 DPC JSON 都是标准一键采集的必需产物；
pipeline 会归档这些 `c-trace_output_dpc_*.json`，但指标、digest 和 `REPORT_<tag>.md`
只解析 primary DPC；报告必须在 Profiling setup 中说明已归档 DPC 列表和被分析的 primary 文件。

分析字段和报告引用边界见 `reference/04-analysis-dimensions.md`、
`reference/05-c500-metric-names.md` 和 `reference/07-report-template.md`。

## Usecase 拆分

标准 `collect-run` 始终先生成一个主 `REPORT_<tag>.md`。当主报告显示
`mcProfiler scope=per-kernel-multiple-occurrences` 时，`collect-run` 会自动执行一次
`split-usecases`，并把输出写入 `<run-dir>/usecases/manifest_<tag>.json`。agent 默认读取最新
manifest 和 `generated_reports`，不主动重跑 split。只有用户明确要求复查、调低 confidence、排查匹配问题，或在离线 `run` 后处理时，才手工执行同一个后处理入口；具体命令见 `reference/13-usecase-split.md`。

当 CycleTrace 使用 `CYCLE_TRACE_SAMPLE_MODE=C` 只采集一个目标 kernel 时，手工 split 应传入
同一个 `--cycle-kernel-name`，避免把 mcTracer 中未被 CycleTrace 采集的其他 kernel 纳入候选。
判断本次是否生成了有效派生报告，以最新
`usecases/manifest_<tag>.json.generated_reports` 为准；目录中遗留的旧 `usecase_*` 报告不代表
本次有效输出。自动拆分命令的 stdout/stderr 会归档到 `<run-dir>/logs/split-usecases.log`。
完整拆分逻辑、适用条件、可信度、输出边界和错误处理见 `reference/13-usecase-split.md`。

## Run 日志

标准 workflow 会把关键脚本输出归档到本次 run 目录。`collect-run` 负责采集、source-latency、split-usecases 和基础 check 日志；`finalize-run` 负责 append-diagnosis、usecase diagnosis 和最终 check 日志。

```text
collect-run 阶段：
<run-dir>/logs/collect-run.log
<run-dir>/logs/mctracer.log            # mcTracer 原始 stdout/stderr；复用时写 status: reused
<run-dir>/logs/cycle-trace-ng.log       # CycleTrace 原始 stdout/stderr
<run-dir>/logs/mcprofiler.log           # mcProfiler 原始 stdout/stderr；复用时写 status: reused，可选跳过时写 status: skipped
<run-dir>/logs/trace-profile-pipeline.log # pipeline run/analyze stdout/stderr；复用时写 status: reused
<run-dir>/logs/source-latency.log       # 仅 ENABLE_SOURCE_LATENCY=true 且父报告 source-latency 触发时生成
<run-dir>/logs/refresh-report.log       # 仅父报告 source-latency 后刷新报告时生成
<run-dir>/logs/check-report.log
<run-dir>/logs/split-usecases.log   # 仅自动 split-usecases 触发时生成
<run-dir>/logs/source-latency-usecase_XXX.log
<run-dir>/logs/refresh-report-usecase_XXX.log

finalize-run 阶段：
<run-dir>/logs/append-diagnosis.log
<run-dir>/logs/append-diagnosis-usecase_XXX.log
<run-dir>/logs/check-report-usecase_XXX.log
```

标准命令 stdout/stderr 默认只输出短摘要、状态字段和日志路径；这些日志保留命令、退出码、完整 stdout 和 stderr，便于无上下文 agent 或用户复盘实际执行路径。固定阶段日志每次 `collect-run` 都会覆盖写；若该阶段复用已有有效产物，日志内容为 `status: reused`；若可选 mcProfiler 因 `REQUIRE_MCPROFILER=false` 被跳过，日志内容为 `status: skipped`；这些情况都不保留上一次工具原始输出。日志归档不替代
`collection_manifest.json`、`metrics_all_<tag>.json` 或 `usecases/manifest_<tag>.json` 的结构化质量门。

## 阶段恢复、清理和归档时机

底层脚本按 mcTracer、CycleTrace、mcProfiler、trace pipeline 四个阶段推进。重试同一个
run 目录时，脚本会复用已经存在且有效的阶段产物，只从第一个缺失或无效阶段开始执行。失败时
当前失败阶段的本次 run_dir 半成品会删除，workdir 根目录临时产物会统一清理，已完成阶段的
run_dir 标准化产物保留。每次新的 `collect-run` 调用都会重新设置
`tool_collect_remaining_seconds="$COLLECT_TIMEOUT_SECONDS"`。

| 阶段 | 工具原始产物 | 阶段标准化产物 | 失败时删除 | 保留内容 | `cp` / 归档时机 |
|---|---|---|---|---|---|
| mcTracer | `$workdir/tracer_out_*/tracer_out-*.json` | `$run_dir/tracer_out.json`，最终 `$run_dir/artifacts/tracer_out.json` | 本次 marker 之后生成的 `$workdir/tracer_out_*`，以及本次写出的 `$run_dir/tracer_out.json` | 旧的有效 `$run_dir/tracer_out.json` 或 `$run_dir/artifacts/tracer_out.json` | mcTracer 成功并找到 JSON 后立即 `cp` 到 `$run_dir/tracer_out.json`；pipeline `run` 成功时归档到 `artifacts/` |
| CycleTrace | `$workdir/c-trace_output_dpc_*.json`、`$workdir/.2*.db`、`$run_dir/logs/cycle-trace-ng.log`；`ENABLE_SOURCE_LATENCY=true` 时 kernel-probe 可能在 `$workdir` 根目录生成 `<cycle-exec-basename>.[0-9]*.out` | `$run_dir/c-trace_output_dpc_*.json`，最终 `$run_dir/artifacts/c-trace_output_dpc_*.json` | 本次 marker 之后生成的 `$workdir/c-trace_output_dpc_*.json`、`$workdir/.2*.db`、`$workdir/<cycle-exec-basename>.[0-9]*.out`，以及本次写出的 `$run_dir/c-trace_output_dpc_*.json` | 已有效的 mcTracer 产物；旧的有效 CycleTrace artifacts；`$run_dir/logs/cycle-trace-ng.log`；`$run_dir/artifacts/lineinfo/lineinfo_binary.*.out` | cycle-trace-ng 成功并找到 primary JSON 后，按 DPC `cp` 到 `$run_dir/`；pipeline `run` 成功时归档到 `artifacts/` |
| mcProfiler | `$run_dir/mcprofiler_raw_<tag>/report_dumped_result.json`、`report.txt.json`、`*.png*` | `$run_dir/mcprofiler_report_dumped.json`、`$run_dir/mcprofiler_report.txt.json`、`$run_dir/*.png*`，最终进入 `$run_dir/artifacts/` | `$run_dir/mcprofiler_raw_<tag>`、本次写出的两个 mcProfiler JSON、marker 后生成的 `$run_dir/*.png*` | 已有效的 mcTracer、CycleTrace 产物；旧的有效 mcProfiler artifacts | mcProfiler 成功后先 `cp` 两个 JSON 到 `$run_dir/`，再复制 PNG 到 `$run_dir/`，然后删除 raw 目录；`REQUIRE_MCPROFILER=true` 时失败/缺失会阻断，`false` 时只 warning 并继续 pipeline |
| trace pipeline | `$run_dir` 根目录必需标准化产物，或 `$run_dir/artifacts/` | `$run_dir/artifacts/*`、`$run_dir/analysis/*`、`$run_dir/REPORT_<tag>.md` | 本次生成的 `REPORT_<tag>.md`、`analysis/digest_<tag>.md`、`metrics_all_<tag>.json`、`metrics_key_<tag>.json` | 已成功的工具产物全部保留 | mcTracer 和 CycleTrace 有效后即可执行；mcProfiler 存在则归档并解析，缺失且 `REQUIRE_MCPROFILER=false` 时归档 manifest 的 `missing_optional` 并输出 `N/A` |

当某个阶段因上次成功已经只存在于 `artifacts/` 中，而本次后续阶段在 run 根目录生成了新产物时，
脚本会在 pipeline 前把已归档成功产物按需回填到 run 根目录，使 `trace_profile_pipeline.py run`
仍能从完整的一组标准化 raw 产物归档和分析。pipeline 成功后会删除 run 根目录中的 raw JSON/PNG，
避免与 `artifacts/` 重复。采集成功或失败退出前，脚本会清理 `$workdir` 下本次流程不再需要的
临时 `tracer_out_*`、`.2*.db`、`c-trace*.json`；`ENABLE_SOURCE_LATENCY=true` 时，也会清理
kernel-probe 留下的 `<cycle-exec-basename>.[0-9]*.out`。该清理不会删除
`$run_dir/artifacts/lineinfo/lineinfo_binary.*.out`。

## 离线解析

已有 raw 产物且它们位于同一个目录时，可手工做离线归档和分析；mcTracer 和 CycleTrace 必需，mcProfiler 是否必需由 `--require-mcprofiler` 决定。离线入口只在恢复或排查时使用：

| 入口 | 使用时机 |
|---|---|
| `trace_profile_pipeline.py run --source ...` | raw 三工具产物在同一目录，需要重新归档并分析。 |
| `trace_profile_pipeline.py analyze --artifact-dir ...` | 已有标准 `artifacts/`，只刷新 `analysis/` 和基础报告。 |
| `trace_profile_pipeline.py collect` + `analyze` | 需要分步定位归档或分析阶段问题。 |

离线 `run --source` 不会从 mcProfiler 原始 `--per-kernel` 输出目录自动执行 normalize；若要复现
`CYCLE_TRACE_KERNEL_NAME` 下的 per-kernel 行为，`--source` 目录需要已经包含标准
`mcprofiler_report_*`，或包含标准采集/normalize 后生成的 `mcprofiler_per_kernel/manifest.json` 和
`occurrence_<N>/`。否则 `--require-mcprofiler true` 会把缺失标准 mcProfiler JSON 视为不完整产物。

具体参数以 `trace_profile_pipeline.py --help` 和对应错误码 `TR-ART-*` / `TR-ANA-*` 为准；不要用离线入口绕过未解决的标准采集失败。

`coarse` 输出 `compute`、`memory`、`latency`、`occupancy`、`mixed`、`unclear` 之一。
`detailed` 保留 C500 复合瓶颈标签。离线分析历史 artifacts 时才允许缺少 mcProfiler，
并必须把 bank conflict、cache hit、真实 IPC、经验 Roofline 和部分 bound 证据标记为不可用。
离线分析的有效产物边界见 `reference/05-c500-metric-names.md`。

## Source-Latency 采集开关

`ENABLE_SOURCE_LATENCY=true` 时，mcTracer 和 mcProfiler 仍使用 `OP_EXEC_CMD`；CycleTrace 会执行
`MACA_LINEINFO_CYCLE_TRACE_EXEC_CMD`，为空时回退 `MACA_LINEINFO_BINARY`，并追加运行期
`--kernel-probe`。workflow 不会自动重编译目标程序，lineinfo binary、objdump 输入、归因模型和手工
调试命令见 `reference/10-source-latency.md`。

常规报告成功后，`collect-run` 会严格生成 `analysis/source_latency_<tag>.md/.csv/.json`，再刷新
`REPORT_<tag>.md` 吸收 source-latency summary；缺失、不可解析或未被报告吸收都会返回非零。若之后
`split-usecases` 生成 generated reports，且 source-latency 已启用，workflow 还会逐个生成
per-usecase source-latency 并刷新对应 usecase 报告。kernel-probe plugin 报错按
`reference/09-error-troubleshooting.md` 的 `TR-COL-014` / `TR-COL-015` 处理；只有需要安装、启用或
排查 pydpg bulk fast path 时，才读取 `reference/11-pydpg-bulk.md`。

## 多版本对比

先对每个版本完成独立标准 workflow，或在离线排查中完成 `run` / `analyze`。需要版本对比时使用 `trace_profile_pipeline.py compare`；同一 run 目录内对比用重复 `--tag`，跨 run 目录对比用重复 `--case label=/path/to/run-dir`。输出写入 `analysis/compare_*.md`；`--case` 模式要求每个 run 目录的 `analysis/` 下只有一个
`metrics_all_*.json`，也可用 `--output-dir` 覆盖。诊断收敛和复采对比规则见
`reference/06-diagnosis-playbook.md`。

## 排查索引

明确异常优先按 `^## TR-...$` 标题局部读取 `reference/09-error-troubleshooting.md` 对应详情块，非错误类背景和人工判断点见
`reference/03-common-issues.md`。不要手工拼接跨 run、旧版本或未校验的部分产物继续生成报告；
一键采集失败时应按 `[TR-*]` 报错修复后重新运行 `collect-run`，由底层脚本判断哪些已成功阶段可以复用。

## 分析边界索引

CycleTrace `dur=4`、WIF counter scope、GVM peak in-flight、PNG 图引用边界，以及
source-latency modeled attribution 与真实 wall-clock 占比的区别，属于诊断解释边界。生成报告或
判断瓶颈前先读 `reference/quick-diagnose.md`；进入 Step 3 后，`reference/04-analysis-dimensions.md`
和 `reference/06-diagnosis-playbook.md` 是诊断基础，必须读取。只有 quick 和诊断基础文档仍无法判定
字段口径、证据冲突、归因边界或修改方向时，再按需局部读取其他对应 reference，例如
`reference/05-c500-metric-names.md` 或 `reference/10-source-latency.md`。不要在采集文档中展开分析结论。
