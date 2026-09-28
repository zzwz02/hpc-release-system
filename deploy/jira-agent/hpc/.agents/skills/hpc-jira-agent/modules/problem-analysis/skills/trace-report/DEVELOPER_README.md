# trace-report

`trace-report` 用于在 MetaX C500 上分析算子性能时，通过 mcTracer、CycleTrace 和 mcProfiler 采集 trace，提取可信性能指标并形成瓶颈诊断报告。

## 适用场景

- benchmark 只能说明慢，但不能解释瓶颈来源。
- 需要判断算子是 compute、memory、latency、occupancy、mixed 还是 unclear bound。
- 需要基于真实 trace/report 输出可验证的诊断结论和数据边界。

## 流程图

```text
trace-report
唯一标准入口：
  python3 scripts/trace_report_env.py [--config "$ACTIVE_CONFIG"] <subcommand>

[0] collect-run（控制侧发起）
    |
    |-- 初始化/读取 active config，派生 PROFILE_RUN_DIR / PROFILE_TAG
    |-- 校验控制侧与目标侧环境，发布 scripts 到目标侧 .trace-report/scripts/
    |-- 在目标侧创建 PROFILE_RUN_DIR，并在 REMOTE_WORKDIR 执行目标算子与采集流程
    |
    `--> [1] 三工具采集与 pipeline（目标侧）
         |
         |-- mcTracer + CycleTrace
         |-- mcProfiler（SKIP_MCPROFILER=true 时跳过）
         |-- 归档 artifacts，解析 metrics，生成基础 REPORT_<tag>.md
         |
         |-- failed
         |    `--> 按 [TR-*] 定位并重试 collect-run（最多 3 次）
         |         REQUIRE_MCPROFILER=false 不得在自动重试中改为 true
         |
         `--> [1a] collect-run 后处理
              |
              |-- ENABLE_SOURCE_LATENCY=true 时生成 source-latency 产物并刷新基础报告
              |-- 执行 base check-report（不要求 LLM follow-up 章节）
              |-- 多 usecase 时拆分并生成 usecase reports
              |-- ENABLE_SOURCE_LATENCY=true 时刷新 generated usecase reports
              |
              `--> workflow_state: needs_llm_followup

[2] LLM follow-up diagnosis（报告与 metrics 仍在目标侧）
    |
    |-- 读取 REPORT_<tag>.md 与 metrics_key_<tag>.json
    |-- 收敛证据支持的瓶颈和 Next Concrete Edit
    |
    `--> [3] finalize-run
         |
         |-- 写入 "## 9. LLM Follow-up Diagnosis"
         |-- 执行 check-report
         |
         `--> workflow_state: needs_artifact_sync

[4] sync-artifacts（控制侧归档）
    |
    |-- 仅同步本次 PROFILE_RUN_DIR 到 LOCAL_WORKDIR/profile-artifacts/
    |
    `--> workflow_state: complete
```

状态推进以各子命令输出的 `workflow_state` 和 `required_next_command` 为准。`collect-run` 失败时，先依据 `failed_stage`、`cause_code`、`last_stderr`、日志路径和明确的 `[TR-*]` 处理；三次尝试仍失败时停止，不继续拼接部分产物进行诊断。

## 使用 Skill 的提示词

调用 `trace-report` 时，用户可选择以下两种互斥的参数提供方式。

### 模式一：直接提供参数

适用于参数较少或临时采集。用户可向 agent 提供类似提示词：

使用 trace-report 分析 C500 算子性能。
CONTAINER_NAME: "my_test"
LOCAL_WORKDIR: "/path/to/local/workdir"
REMOTE_WORKDIR: "/path/to/remote/workdir"
OP_EXEC_CMD: "./test_maca"
SKIP_MCPROFILER: true

agent 应将这些键值转换为 `collect-run --set KEY=VALUE`，并生成默认 active config。

### 模式二：提供配置文件

适用于复用完整环境配置。用户可向 agent 提供类似提示词：

使用 trace-report 分析 C500 算子性能。
配置文件：`/workspace/trace_report_env.yaml`
`run-dir=profile-artifacts/bf16_gemm_kernel_v0_baseline`

agent 应使用 `python3 scripts/trace_report_env.py --config /workspace/trace_report_env.yaml collect-run ...`。配置文件可位于任意本地路径；脚本只读加载，不复制、不改写。

例如，用户可提供 `/workspace/trace_report_env.yaml`。其内容可基于默认模板填写：

```yaml
LOCAL_WORKDIR: "/workspace"
REMOTE_WORKDIR: "/workspace_remote"

# local: leave CONTAINER_NAME, SERVER_USER, and SERVER_HOST empty.
# docker: set CONTAINER_NAME only.
# ssh: set SERVER_USER and SERVER_HOST only.
# ssh+docker: set all three.
CONTAINER_NAME: "my_test"
SERVER_USER: ""
SERVER_HOST: ""

MACA_VISIBLE_DEVICES: "0"
AUTO_SELECT_DEVICE_TIMEOUT_SECONDS: "120"

OP_EXEC_CMD: "./test_maca"

HEURISTIC_BOUND_MODE: "coarse"
COLLECT_TIMEOUT_SECONDS: "600"
SKIP_MCPROFILER: "false"
REQUIRE_MCPROFILER: "false"

CYCLE_TRACE_TARGET_PEU: "1"
CYCLE_TRACE_DPG_PAGE_NUM: "32"
CYCLE_TRACE_TARGET_AP: "0"
CYCLE_TRACE_DPC_ID: "0"
CYCLE_TRACE_SAMPLE_MODE: ""
CYCLE_TRACE_KERNEL_NAME: ""
CYCLE_TRACE_KERNEL_REPEAT: ""
CYCLE_TRACE_TOOLS_JSON: "/opt/maca/etc/tools.json"
PROFILER_PORT: "50123"

ENABLE_SOURCE_LATENCY: "false"
MACA_SOURCE_FILE: ""
MACA_LINEINFO_BINARY: ""
MACA_LINEINFO_CYCLE_TRACE_EXEC_CMD: ""
MACA_OBJDUMP_BIN: "/opt/maca/restricted/Tools/mxgpu_llvm/bin/mxobjdump"
MACA_OBJDUMP_ARGS: "--source --print-code"
MACA_LLVM_OBJDUMP_BIN: ""

MC_TRACER_BIN: "/opt/maca/bin/mcTracer"
CYCLE_TRACE_BIN: "/opt/maca-20260505/bin/cycle-trace-ng"
MC_PROFILER_BIN: "/opt/maca/restricted/Tools/mcProfiler/mcProfiler-ubuntu18.04/mcProfiler"
```

用户应按实际环境替换工作目录、执行命令、连接信息和工具路径；配置文件中的字段必须使用上述已支持的键名。

### 规则

- 两种模式互斥：提供配置文件时，不得同时提供或自动补充 `--set` 参数。
- 配置文件和直接参数中的未知键都会失败，避免参数拼写错误静默失效。
- `REQUIRE_MCPROFILER=false` 时，自动重试不得将其改为 `true`。

## 开发者快速开始

标准采集入口是 `scripts/trace_report_env.py collect-run`。用户直接提供参数时，可以用 `collect-run --set KEY=VALUE` 初始化必要配置并立即采集：

```bash
python3 scripts/trace_report_env.py \
  collect-run --run-dir profile-artifacts/bf16_gemm_kernel_v0_baseline \
  --set LOCAL_WORKDIR=/path/to/local/workdir \
  --set REMOTE_WORKDIR=/path/to/remote/workdir \
  --set OP_EXEC_CMD='./test_maca'
```

本仓库的 `config/trace_report_env.yaml` 只作为直接参数模式的本地模板；该模式生成的实际运行配置位于当前服务器的 `[LOCAL_WORKDIR]/.trace-report/config/trace_report_env.yaml`。

用户提供配置文件时，使用 `--config /any/existing/user.yaml collect-run ...`。配置文件可以位于任意本地可读路径；脚本只读加载，不复制、不改写。`--config` 与 `collect-run --set` 互斥；配置文件中的未知键会直接失败，避免参数拼写错误静默失效。`collect-run` 会输出 `config_source` 和 `config_path` 标识本次使用的输入模式与路径。

执行位置：开发者从控制侧运行 `trace_report_env.py`。该入口在控制侧维护 active config、同步 scripts 和回传 artifacts；同步后，目标侧在 `REMOTE_WORKDIR` 下运行采集、解析和报告检查脚本。local 模式下两侧可在同一机器，`LOCAL_WORKDIR` 仍是归档目录，`REMOTE_WORKDIR` 仍是目标命令 cwd。

若远端需要密码认证，由用户在本地设置 `TRACE_REPORT_SSH_PASSWORD`；密码不要写入 YAML、本地模板或 active config。

```bash
export TRACE_REPORT_SSH_PASSWORD='your-password'
# unset TRACE_REPORT_SSH_PASSWORD
```

## 采集命令

已有 active config 或用户提供配置文件时，显式传入配置：

```bash
ACTIVE_CONFIG="$LOCAL_WORKDIR/.trace-report/config/trace_report_env.yaml"

python3 scripts/trace_report_env.py --config "$ACTIVE_CONFIG" \
  collect-run --run-dir profile-artifacts/bf16_gemm_kernel_v0_baseline
```

run 目录必须符合 `profile-artifacts/<kernel>_v<N>_<tag>`。例如 `profile-artifacts/bf16_gemm_kernel_v0_baseline` 会自动派生：

- `PROFILE_RUN_DIR=profile-artifacts/bf16_gemm_kernel_v0_baseline`
- `PROFILE_TAG=baseline`
- `PROFILE_KERNEL=bf16_gemm_kernel`
- `PROFILE_VERSION=v0`

标准 workflow 不手工拆分执行 `derive-run`、目标 `mkdir`、`sync-scripts` 或 `.trace-report/scripts/collect_trace_profile.sh`。

## finalize 与同步

普通单报告 run 的收尾示例：

```bash
python3 scripts/trace_report_env.py --config "$ACTIVE_CONFIG" finalize-run \
  --run-dir profile-artifacts/bf16_gemm_kernel_v0_baseline \
  --diagnosis-file /path/to/llm_followup_diagnosis.md
```

若 `collect-run` 输出 `final_diagnosis_target: usecases generated_reports`，需要同时准备主报告 scope 诊断文件和每个 `usecase_XXX` 的诊断文件：

```bash
python3 scripts/trace_report_env.py --config "$ACTIVE_CONFIG" finalize-run \
  --run-dir profile-artifacts/bf16_gemm_kernel_v0_baseline \
  --diagnosis-file /path/to/parent_scope_diagnosis.md \
  --diagnosis-dir /path/to/usecase_diagnoses
```

`finalize-run` 成功后，按输出的 `required_next_command` 同步本次 run 产物回本地：

```bash
python3 scripts/trace_report_env.py --config "$ACTIVE_CONFIG" \
  sync-artifacts --run-dir profile-artifacts/bf16_gemm_kernel_v0_baseline
```

## 输出

标准输出位于 `PROFILE_RUN_DIR`：

- `REPORT_<tag>.md`
- `analysis/digest_<tag>.md`
- `analysis/metrics_all_<tag>.json`
- `analysis/metrics_key_<tag>.json`
- `artifacts/`

`ENABLE_SOURCE_LATENCY=true` 时，标准 `collect-run` 还会生成并强制校验 `analysis/source_latency_<tag>.{md,csv,json}` 和 `artifacts/lineinfo/`。

## 开发者附录：三工具直连调试

标准 workflow 仍优先使用 `trace_report_env.py collect-run`。下面示例只用于开发者理解或临时调试三工具如何直接执行 `./test_maca`；这些命令不生成标准 trace-report artifacts/report，不替代 `collect-run`。

mcTracer：

```bash
MACA_VISIBLE_DEVICES=0 /opt/maca/bin/mcTracer ./test_maca
```

CycleTrace：

```bash
ENABLE_DPG=1 ENABLE_DPG_DUMP=1 ISU_FASTMODEL=0 MACA_VISIBLE_DEVICES=0 \
  /opt/maca-20260505/bin/cycle-trace-ng ./test_maca \
  --format json \
  --target-peu 1 \
  --dpg-page-num 32 \
  --target-ap 0 \
  --dpc-id 0
```

mcProfiler：

```bash
NO_PROXY='*' no_proxy='*' MACA_VISIBLE_DEVICES=0 \
  /opt/maca/restricted/Tools/mcProfiler/mcProfiler-ubuntu18.04/mcProfiler perf_exec \
  --cmdline './test_maca' \
  --casename bf16_gemm_kernel_v0_baseline \
  --output profile-artifacts/bf16_gemm_kernel_v0_baseline/mcprofiler_raw_baseline \
  --port 50123
```

聚焦单 kernel 调试 mcProfiler 时，可追加：

```bash
  --kernelnames '<exact mcTracer args.name>' \
  --per-kernel
```

注意：

- 工具实际名称是 `mcTracer`；如果本地命令别名叫 `mcTrace`，以当前环境安装路径为准。
- `./test_maca` 需要能在目标 workdir 下直接执行。
- 这些命令只用于开发者直连工具调试；标准采集、产物归档、分析报告、重试和同步仍使用 `collect-run`。

## 常用调试命令

```bash
python3 scripts/trace_report_env.py --help
python3 scripts/trace_report_env.py collect-run --help
python3 scripts/trace_report_env.py finalize-run --help
python3 scripts/trace_report_env.py sync-artifacts --help
# 仅维护 pipeline 或离线排查时查看：
# python3 scripts/trace_profile_pipeline.py --help
```

明确 `[TR-*]` 报错时，按标题局部读取 `reference/09-error-troubleshooting.md` 的对应详情块，例如：

```bash
rg -n "^## TR-COL-006$" -A 12 reference/09-error-troubleshooting.md
```

## 文档边界

- `README.md` 面向人类开发者快速调试和上手，不作为 agent 执行 trace-report skill 的输入文档。
- `SKILL.md` 是 agent 执行 trace-report skill 时的运行契约。
- `reference/quick-collect.md`、`reference/quick-diagnose.md`、`reference/quick-finalize.md` 是 agent 低 token Step 入口。
- 深层 reference 只在复杂配置、采集恢复、source-latency、usecase split、结构化报错或报告质量门失败时按需读取。

## 参考文件

[maca_compare_ncu_metrics.md](./maca_compare_ncu_metrics.md)，本文档将该 skill 已有数据及报告输出与一些 NCU 指标进行对比映射，中文版：[maca_compare_ncu_metrics_zh.md](./maca_compare_ncu_metrics_zh.md)
