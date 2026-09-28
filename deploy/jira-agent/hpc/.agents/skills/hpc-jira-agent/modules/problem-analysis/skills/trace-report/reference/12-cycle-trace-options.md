# CycleTrace 参数语义

本文只解释 trace-report 暴露或显式约束的 `cycle-trace-ng` 参数语义。标准采集仍统一通过
`trace_report_env.py collect-run` 执行，不直接调用 `cycle-trace-ng`。

## 读取门槛

只有必须调整或解释 sample mode、kernel filter、AP、PEU、DPC、DPG 等 CycleTrace 参数语义时，才读取本文。标准采集参数已足够时，不默认读取本文。

## Sample Mode

`CYCLE_TRACE_SAMPLE_MODE` 对应 `cycle-trace-ng --sample-mode`。为空时，采集脚本不传
`--sample-mode`，由工具使用默认模式 `A`。

| 值 | 工具含义 | trace-report 使用建议 |
|---|---|---|
| 空 | 不传 `--sample-mode`，工具默认 `A` | 标准全量诊断默认用法 |
| `A` | all kernels，采集所有 kernel | 与空值语义等价；需要显式固定工具模式时使用 |
| `B` | blit kernels only | 仅在用户明确需要工具特定 blit kernel 过滤时使用 |
| `C` | custom kernel list，读取 `CYCLE_TRACE_TOOLS_JSON` 中的 `cycleTrace.kernels` | CycleTrace JSON 过大或只需目标 kernel 时使用 |
| `D` | DIDT kernel only | 仅在用户明确需要工具特定 DIDT kernel 过滤时使用 |

标准性能诊断优先使用空值或 `A`。不要为了减少 JSON 体积盲目使用 `B` 或 `D`；它们是工具定义的
kernel 类别过滤，可能采不到目标 kernel。缩小目标 kernel 范围时优先使用 `C`。

## Kernel Filter

`CYCLE_TRACE_KERNEL_NAME` 和 `CYCLE_TRACE_KERNEL_REPEAT` 只用于 sample mode `C`。
当设置 `CYCLE_TRACE_KERNEL_NAME` 且 `CYCLE_TRACE_SAMPLE_MODE` 为空时，采集脚本会自动使用 `C`。

| 配置 | 含义 |
|---|---|
| `CYCLE_TRACE_KERNEL_NAME` | 写入 `cycleTrace.kernels[0].kernelName` 的真实 kernel 名；必须来自 mcTracer、mcProfiler `mtreg_occupancy` 下方 `name`、报告或工具输出，不应填写算子简称、run 目录名或手写推测名 |
| `CYCLE_TRACE_KERNEL_REPEAT` | 写入 `cycleTrace.kernels[0].repeat`；为空且 kernel name 已设置时脚本使用 `-1` |
| `CYCLE_TRACE_TOOLS_JSON` | 包含 `cycleTrace.kernels` 的目标环境 tools.json，默认 `/opt/maca/etc/tools.json` |

设置 `CYCLE_TRACE_KERNEL_NAME` 后，采集脚本只临时替换 `cycleTrace.kernels` 数组，使本次
sample-mode C 采集期间列表中只有目标 kernel；`cycleTrace` 下其他字段不重写，原始 kernels 列表
在退出时从备份恢复。保留原始列表中的第二个或更多 entry 会破坏单算子采集语义，因此不会参与本次采集。
最终 `REPORT_<tag>.md` 会展示 CycleTrace target kernel、repeat、sample mode 和 scope note；
这些字段限定 CycleTrace 指标的归属，同时分析阶段会从 mcTracer `tracer_out.json` 选择同名
`args.name` 的 kernel event 作为 launch/resource 字段来源；mcProfiler 会自动使用同一个
`CYCLE_TRACE_KERNEL_NAME` 执行 `--kernelnames/--per-kernel`，并只从目标 kernel 前缀文件归档分析
输入。若同名 kernel 出现多个 occurrence，需要用 `split-usecases` 校验并生成 per-usecase 报告。

对 mcTracer 产物，`CYCLE_TRACE_KERNEL_NAME` 应使用 kernel event 的 `args.name` 字段，而不是同一
event 顶层的 `name` 字段。顶层 `name` 通常是 demangled/display name，例如 `void xxx(...)`；
sample-mode C 需要的是 `args.name` 中的 mangled/internal name。名称不匹配时，`cycle-trace-ng`
仍可能输出 JSON，但只含 wave start/end、metadata 或空 category，硬件指令计数为 0。
采集脚本报 `TR-COL-016` 时最多展示前 8 个 mcTracer `args.name` 候选；如果没有符合的候选值，
需要在 mcTracer `tracer_out.json` 中继续查找 `traceEvents[].args.name`，并复制完整精确值。

`repeat < 0` 表示该 kernel 不限 repetition；`repeat >= 0` 表示指定 repetition，适合重复调用中抽取
单次或少量代表性 trace。kernel 名不匹配时，MTE/STE/MMA/BSM/GLOBAL/ARRIVE/LDU 等硬件事件可能为
0，pipeline 会把 CycleTrace 判为无效；文件仍可能包含 wave start/end 等 token，不一定会很小。

`CYCLE_TRACE_TOOLS_JSON` 是目标环境全局配置。采集脚本会在正常退出、失败退出、INT/TERM 时恢复；
若进程被 SIGKILL 或宿主机崩溃，仍需人工检查并恢复。

## AP / PEU / DPC / DPG

| trace-report 配置 | `cycle-trace-ng` 参数 | 工具语义 | trace-report 约束 |
|---|---|---|---|
| `CYCLE_TRACE_TARGET_PEU` | `--target-peu` | PEU bitmap，bitfield `[3:0]`；`1=0001b` 采 PEU0，`3=0011b` 采 PEU0/1，`7=0111b` 采 PEU0/1/2，`15=1111b` 采 4 条 PEU | 标准 workflow 允许 `1..15`；工具的 `0` 表示 disable target PEU，但会破坏标准报告所需硬件事件，因此不允许 |
| `CYCLE_TRACE_DPG_PAGE_NUM` | `--dpg-page-num` | DPG buffer 页数，单位 2M；工具默认 `32` 即 64M | 必须为正整数；trace 内容过多时可在用户授权后增大 |
| `CYCLE_TRACE_TARGET_AP` | `--target-ap` | 目标 AP，决定哪个 AP 的 INST token 被 dump；范围 `0..15` | 必须为 `0..15` |
| `CYCLE_TRACE_DPC_ID` | `--dpc-id` | 目标 DPC，可写 `0` 或 `0,1,2,3`；每个 id 范围 `0..7` | 必须是无空格逗号列表；所有请求 DPC 都归档，自动分析只使用列表第一个 primary DPC |

多 DPC 采集时，`artifacts/c-trace_output_dpc_*.json` 会保留所有请求 DPC 文件，但
`metrics_all`、digest 和 `REPORT_<tag>.md` 只解析 primary DPC。

`CYCLE_TRACE_TARGET_AP` 的参数语义和范围保持不变。需要注意的是，C500 的 AP 数量大于某些小
shape kernel 的 workgroup 数量时，目标 kernel 的 workgroup 只会落到部分 AP；如果
`cycle-trace-ng --target-ap` 指定的 AP 没有被分配到目标 workgroup，CycleTrace JSON 可能存在，
但没有 MTE/STE/MMA/BSM/GLOBAL/ARRIVE/LDU 等硬件事件。该现象是采集边界，不是
trace-report 可自动修复的参数错误。是否通过增大输入 shape 或其它方式增加单次 kernel 的
workgroup 数，会改变被 profile 的 workload，必须由调用方或用户明确决定。

## Kernel Probe 与 Source Latency

`ENABLE_SOURCE_LATENCY=true` 时，trace-report 会让 CycleTrace 执行
`MACA_LINEINFO_CYCLE_TRACE_EXEC_CMD`，为空时回退 `MACA_LINEINFO_BINARY`，并追加运行期
`--kernel-probe`。这与 `CYCLE_TRACE_SAMPLE_MODE` 是不同维度：

- `--kernel-probe` 用于 token 到 asm/source 映射，要求目标程序编译期包含 `--kernel-probe 0`。
- `--sample-mode` 控制采集哪些 kernel。
- 需要 source-latency 且 CycleTrace JSON 过大时，可以同时使用 `ENABLE_SOURCE_LATENCY=true` 和
  `CYCLE_TRACE_SAMPLE_MODE=C`。

## 暂不暴露的工具参数

`cycle-trace-ng --help` 还包含 `--filter`、`--token-msk`、`--pwr-mode`、`--pack-timeout`、
`--sample-data-file`、`--sample-data-interval`、`--sample-data-compressed`、`--sample-data-dump`、
`--only-sample`、`--only-parse`、`--input-rawdata` 和 `--xcore-name` 等参数。trace-report
当前没有在 YAML 中暴露这些参数，也没有为它们设计产物质量门。

如需支持这些参数，应单独设计配置项、Preflight 校验、采集命令拼接、归档规则和报告边界；不要在标准
workflow 中通过手工命令绕过 `collect-run`。
