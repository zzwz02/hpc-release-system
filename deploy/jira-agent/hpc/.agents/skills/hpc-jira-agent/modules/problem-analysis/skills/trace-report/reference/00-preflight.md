# Preflight 环境配置与模式判断

本文不是标准采集前必读文档。不要因为 `collect-run` 会执行 validate，就提前读取本文。只有出现 preflight/config/target 失败、active config 冲突、受保护配置冲突、SSH/docker 工具校验失败，或 quick-collect 无法决策某个校验问题时，才读取本文对应章节。

Preflight 只做三件事：确认 YAML 配置、验证目标环境、导出环境变量。除非用户明确授权，只能修改非保护采集参数；Preflight 失败时不得创建 run 目录，也不得开始采集。
遇到明确 `[TR-PF-*]`、`[TR-SSH-*]` 或 `[TR-TGT-*]` 时，先按 `^## TR-...$` 标题局部读取
`reference/09-error-troubleshooting.md` 的对应详情块；只有需要确认配置来源、执行模式或受保护配置边界时，再读本文相关章节。

## 配置来源

调用本 skill 时优先使用 YAML 配置环境参数。本仓库的
`config/trace_report_env.yaml` 只作为本地模板，用于生成配置默认值；
实际运行配置必须位于当前服务器的
`[LOCAL_WORKDIR]/.trace-report/config/trace_report_env.yaml`。执行
`init-config` 后，`validate`、`export`、`mode` 和 `run` 只把本地 active config
作为运行配置，并用它生成 local/docker/ssh/ssh+docker 目标命令；这些子命令必须显式传
`--config "$ACTIVE_CONFIG"`，其中 `ACTIVE_CONFIG="$LOCAL_WORKDIR/.trace-report/config/trace_report_env.yaml"`。配置 YAML 不需要同步到远程服务器或容器。本地模板不得作为后续修改对象。需要
调整 YAML 时，必须调整本地 active config，并遵守“受保护配置”规则。

| 参数 | 含义 |
|---|---|
| `CONTAINER_NAME` | 采集所在容器名称；为空表示不进入 docker |
| `SERVER_USER` / `SERVER_HOST` | 同时为空表示本机；同时非空表示走 SSH |
| `LOCAL_WORKDIR` | 本地归档目录，默认 `.` |
| `REMOTE_WORKDIR` | 实际执行目录，默认 `.`；docker 模式表示容器内目录 |

`LOCAL_WORKDIR` 是控制侧目录：保存 active config，并接收 `sync-artifacts` 回传的 `profile-artifacts/`。`REMOTE_WORKDIR` 是目标执行侧目录：标准采集会在其中创建 `.trace-report/scripts/` 和 `profile-artifacts/`，并在其中执行目标命令与采集/分析脚本。两者在 local 模式下可以位于同一台机器，但不能因此混淆配置归档与目标执行职责。

SSH 默认使用系统当前认证方式（key、agent 或交互式认证）。如果用户在本地提供
`TRACE_REPORT_SSH_PASSWORD` 环境变量，`trace_report_env.py --config "$ACTIVE_CONFIG" validate/run` 发起的目标
SSH 命令会自动使用 `sshpass -e ssh`，并把该变量映射为子进程的 `SSHPASS`；密码不得
写入本地模板或本地 active config。标准采集通过 `reference/02-collection.md` 的
`collect-run` 入口完成脚本同步和目标执行；只有排查 `TR-SYNC-*` 或独立验证脚本同步时，才直接使用
`sync-scripts`。只设置 `SSHPASS` 不会启用 password SSH；应设置
`TRACE_REPORT_SSH_PASSWORD`。password
SSH 相关 `[TR-SSH-*]` 报错按 `^## TR-...$` 标题局部读取 `reference/09-error-troubleshooting.md` 对应详情块。

## 受保护配置

除非用户明确指定或要求修改，不允许修改以下配置项：

- `CONTAINER_NAME`
- `SERVER_USER`
- `SERVER_HOST`
- `LOCAL_WORKDIR`
- `REMOTE_WORKDIR`
- `OP_EXEC_CMD`

Preflight 或异常排查发现这些配置错误时，只报告需要用户确认的失败项；不要擅自改成推测值。Workflow 可自动建议或修改的本地 active config 项仅限用户已授权自动调整的非保护采集参数；若未获授权，只能报告建议修改项并等待确认。非保护采集参数例如 `MACA_VISIBLE_DEVICES`、`CYCLE_TRACE_TARGET_PEU`、`CYCLE_TRACE_DPG_PAGE_NUM`、`CYCLE_TRACE_TARGET_AP`、`CYCLE_TRACE_DPC_ID`、`CYCLE_TRACE_SAMPLE_MODE`、`CYCLE_TRACE_KERNEL_NAME`、`CYCLE_TRACE_KERNEL_REPEAT`、`CYCLE_TRACE_TOOLS_JSON`、`MACA_SOURCE_FILE`、`MACA_LINEINFO_BINARY`、`MACA_LINEINFO_CYCLE_TRACE_EXEC_CMD`、`MACA_OBJDUMP_BIN`、`MACA_OBJDUMP_ARGS`、`MACA_LLVM_OBJDUMP_BIN`、`ENABLE_SOURCE_LATENCY`、工具路径、`PROFILER_PORT`、`HEURISTIC_BOUND_MODE`、`COLLECT_TIMEOUT_SECONDS` 或 `REQUIRE_MCPROFILER`。

配置项与默认值：

| 参数 | 默认值 | 用途 |
|---|---|---|
| `MACA_VISIBLE_DEVICES` | `-1` | 采集设备 ID；`-1` 表示 collect-run 自动选择空闲 GPU，显式数字表示固定使用该 GPU |
| `AUTO_SELECT_DEVICE_TIMEOUT_SECONDS` | `120` | `MACA_VISIBLE_DEVICES=-1` 时自动等待空闲 GPU 的最长时间，单位秒 |
| `OP_EXEC_CMD` | `./test_maca` | 算子执行命令；关键执行参数，属于受保护配置 |
| `HEURISTIC_BOUND_MODE` | `coarse` | 控制 C500 heuristic bound 粒度；`coarse` 输出 `compute/memory/latency/occupancy/mixed/unclear`，`detailed` 输出 C500 复合标签；不影响 Roofline bound |
| `COLLECT_TIMEOUT_SECONDS` | `600` | 三工具采集总耗时上限，单位秒；只限制 mcTracer、cycle-trace-ng 和 mcProfiler，不限制 pipeline 解析 |
| `SKIP_MCPROFILER` | `false` | mcProfiler 采集跳过开关；`true` 时不运行 mcProfiler，相关 profiler 指标不可用 |
| `REQUIRE_MCPROFILER` | `false` | mcProfiler 严格性开关；`false` 时缺失/超时不阻断报告但 profiler 指标为 `N/A`；`true` 时命令级或单 occurrence 的两个 mcProfiler JSON 必须完整存在且可解析，设置 `CYCLE_TRACE_KERNEL_NAME` 且命中多个完整 occurrence 时，以完整 `mcprofiler_per_kernel` occurrence 集作为后续 `split-usecases` 输入 |
| `CYCLE_TRACE_TARGET_PEU` | `1` | CycleTrace `--target-peu` PEU bitmap；`1=0001b` 采 PEU0，`15=1111b` 采 4 条 PEU；详细语义见 `reference/12-cycle-trace-options.md` |
| `CYCLE_TRACE_DPG_PAGE_NUM` | `32` | CycleTrace `--dpg-page-num`，单位 2M，工具默认 `32` 即 64M |
| `CYCLE_TRACE_TARGET_AP` | `0` | CycleTrace `--target-ap`，目标 AP，范围 0-15，工具默认 `0` |
| `CYCLE_TRACE_DPC_ID` | `0` | CycleTrace `--dpc-id`，默认采集 DPC 0；逗号列表时归档全部请求 DPC，自动分析只用第一个 primary DPC |
| `CYCLE_TRACE_SAMPLE_MODE` | 空 | 可选 CycleTrace `--sample-mode`；空值表示不传参数，由工具默认 `A(all kernels)`；`A/B/C/D` 含义见 `reference/12-cycle-trace-options.md` |
| `CYCLE_TRACE_KERNEL_NAME` | 空 | 可选目标 kernel 名；设置后采集脚本自动临时收敛 `cycleTrace.kernels` 并启用 sample-mode C；应填写来自 mcTracer kernel event `args.name`、mcProfiler `mtreg_occupancy` 下方 `name` 或已有报告的真实 kernel name，不是算子简称，也不是 mcTracer 顶层 display `name` |
| `CYCLE_TRACE_KERNEL_REPEAT` | 空 | 可选 repetition；设置 kernel 名但不设置时脚本使用 `-1`，`repeat >= 0` 表示指定 repetition |
| `CYCLE_TRACE_TOOLS_JSON` | `/opt/maca/etc/tools.json` | `cycleTrace.kernels` 所在全局 tools.json；脚本会备份，采集期间只临时替换 `cycleTrace.kernels` 数组，并在退出时恢复原文件 |
| `PROFILER_PORT` | `50123` | mcProfiler 本地服务端口；目标环境端口冲突时可改为其他 1-65535 端口 |
| `MACA_SOURCE_FILE` | 空 | 可选 `.cu` 源文件；source-latency 用它读取源码行文本并匹配 user source |
| `MACA_LINEINFO_BINARY` | 空 | 可选最终可执行文件路径；该 binary 的 kernel 编译 flags 应包含 `-lineinfo` 和编译期 `--kernel-probe 0`。source-latency 后处理会从它提取 device ELF 和源码行号；该字段不承载运行参数 |
| `MACA_LINEINFO_CYCLE_TRACE_EXEC_CMD` | 空 | 可选 lineinfo CycleTrace 采集执行命令；`ENABLE_SOURCE_LATENCY=true` 时，CycleTrace 执行该命令并追加运行期 `--kernel-probe`，为空则回退执行 `MACA_LINEINFO_BINARY` |
| `MACA_OBJDUMP_BIN` | `/opt/maca/restricted/Tools/mxgpu_llvm/bin/mxobjdump` | source-latency 用于从最终 binary 提取 device ELF 的 objdump 工具 |
| `MACA_OBJDUMP_ARGS` | `--source --print-code` | 保留给需要直接调试 mxobjdump 输出的场景；source-latency 的标准路径固定使用 `--extract-elf` |
| `MACA_LLVM_OBJDUMP_BIN` | 空 | 可选 `llvm-objdump` 路径；为空时从 `MACA_OBJDUMP_BIN` 同目录派生，用于 `--line-numbers --disassemble` |
| `ENABLE_SOURCE_LATENCY` | `false` | 设为 `true` 时，collect-run 会让 `cycle-trace-ng` 执行 `MACA_LINEINFO_CYCLE_TRACE_EXEC_CMD`，为空时回退 `MACA_LINEINFO_BINARY`，并追加运行期 `--kernel-probe`，在三工具常规报告后执行严格 source-latency 阶段；目标程序、`MACA_LINEINFO_BINARY` 和 lineinfo CycleTrace 执行命令应来自同一组 kernel compile flags，`MACA_LINEINFO_BINARY` 应是带 `-lineinfo` 和编译期 `--kernel-probe 0` 的最终可执行文件路径，运行参数放入 `MACA_LINEINFO_CYCLE_TRACE_EXEC_CMD` |
| `MC_TRACER_BIN` | `/opt/maca/bin/mcTracer` | mcTracer 路径 |
| `CYCLE_TRACE_BIN` | `/opt/maca-20260505/bin/cycle-trace-ng` | cycle-trace-ng 路径 |
| `MC_PROFILER_BIN` | `/opt/maca/restricted/Tools/mcProfiler/mcProfiler-ubuntu18.04/mcProfiler` | mcProfiler 路径 |

## Run 目录边界

Preflight 不负责创建 run 目录，也不负责派生 `PROFILE_*` 变量；标准采集由 `collect-run` 在 Preflight 后继续完成这些步骤。单独执行 Preflight 通过后，先按 `reference/quick-collect.md` 确认 run 目录；只有 run 名无法派生或本地/远端路径边界不清时，再读 `reference/01-directory-layout.md`。

## 独立 Preflight 命令

标准 Workflow 不需要手工执行下列命令；按 `reference/quick-collect.md` 使用
`collect-run` 即可完成配置初始化、验证、脚本同步和采集。下列命令只用于解释 active
config 生命周期、独立验证环境或定位 Preflight 问题。

```bash
LOCAL_WORKDIR=/path/to/local/archive
python3 scripts/trace_report_env.py init-config \
  --set LOCAL_WORKDIR="$LOCAL_WORKDIR" \
  --set REMOTE_WORKDIR=/path/to/target/workdir \
  --set 'OP_EXEC_CMD=./test_maca' \
  --set MACA_VISIBLE_DEVICES=-1
ACTIVE_CONFIG="$LOCAL_WORKDIR/.trace-report/config/trace_report_env.yaml"
python3 scripts/trace_report_env.py --config "$ACTIVE_CONFIG" validate
eval "$(python3 scripts/trace_report_env.py --config "$ACTIVE_CONFIG" export)"
```

`init-config` 只接受重复的 `--set KEY=VALUE` 覆盖项，先从本地模板生成 YAML，
写入 `LOCAL_WORKDIR/.trace-report/config/trace_report_env.yaml`，再通过本地静态校验。
如果 VALUE 含空格，必须引用整个 `KEY=VALUE`，例如
`--set 'OP_EXEC_CMD=./profile_target_wrapper.sh --case-id smoke'`。
如果本地 active config 已存在，命令会拒绝覆盖；只有明确要覆盖本地配置时才使用 `--force`。
初始化后，后续所有配置修改都必须发生在本地 active config 上。

`validate` 会验证：

- `SERVER_USER` / `SERVER_HOST` 必须同时为空或同时非空。
- `LOCAL_WORKDIR` 和 `REMOTE_WORKDIR` 存在。
- docker 模式下容器存在。
- SSH/ssh+docker 模式下，如果本地已设置 `TRACE_REPORT_SSH_PASSWORD`，本机必须安装
  `sshpass`。
- `MACA_VISIBLE_DEVICES` 为显式数字时，`OP_EXEC_CMD` 能在 `REMOTE_WORKDIR` 下正常运行结束；为 `-1` 时，独立 `validate` 只校验目标环境和 `mx-smi` 可用性，`collect-run` 会先选出实际 GPU 再校验并执行 `OP_EXEC_CMD`。
- `CYCLE_TRACE_TARGET_PEU` 位于 1-15；默认 `1` 表示 `0x1`，采集 PEU 0；`15` 表示 `0b1111`，采集 4 条 PEU 数据。
- `CYCLE_TRACE_DPG_PAGE_NUM` 是正整数；工具默认 `32`，表示 64M DPG buffer。
- `CYCLE_TRACE_TARGET_AP` 位于 0-15；工具默认 `0`。
- `CYCLE_TRACE_DPC_ID` 为 0-7 的逗号分隔列表，不允许空格；默认 `0`，采集 DPC 0。
- `CYCLE_TRACE_SAMPLE_MODE` 为空或 `A/B/C/D`；空值表示工具默认 `A(all kernels)`，`B/D` 是工具特定 kernel 类别过滤，标准诊断通常不推荐；设置 `CYCLE_TRACE_KERNEL_NAME` 时必须为空或 `C`。
- `CYCLE_TRACE_KERNEL_REPEAT` 为空或整数；设置 repeat 时必须同时设置 `CYCLE_TRACE_KERNEL_NAME`。
- 设置 `CYCLE_TRACE_KERNEL_NAME` 时，`CYCLE_TRACE_TOOLS_JSON` 或默认 `/opt/maca/etc/tools.json` 在目标环境中必须可写。
- `ENABLE_SOURCE_LATENCY=false` 时，`MACA_SOURCE_FILE`、`MACA_LINEINFO_BINARY`、
  `MACA_LINEINFO_CYCLE_TRACE_EXEC_CMD`、`MACA_OBJDUMP_BIN` 和 `MACA_LLVM_OBJDUMP_BIN`
  可作为暂存配置存在，不参与目标环境文件、可执行性或试运行校验。
- `ENABLE_SOURCE_LATENCY=true` 时，`MACA_SOURCE_FILE` 必填，且该源文件必须在
  `REMOTE_WORKDIR` 中存在。
- `ENABLE_SOURCE_LATENCY=true` 时，`MACA_LINEINFO_BINARY` 必填，且该最终二进制必须在
  `REMOTE_WORKDIR` 中存在；它应由包含 `-lineinfo` 和编译期 `--kernel-probe 0` 的 kernel
  compile flags 生成。该字段是单个 binary 路径，不承载运行参数；若 lineinfo 采集需要参数，写入
  `MACA_LINEINFO_CYCLE_TRACE_EXEC_CMD`。
- Preflight 只检查 `MACA_LINEINFO_BINARY` 是否存在、lineinfo 执行命令是否可运行以及 objdump
  工具是否可执行，不会从 binary 中反推出编译 flags 是否真实包含 `-lineinfo --kernel-probe 0`；
  source-latency 失败或归因不可用时，应优先检查 lineinfo binary 的编译命令。
- `ENABLE_SOURCE_LATENCY=true` 时，`MACA_LINEINFO_CYCLE_TRACE_EXEC_CMD` 必须是合法
  shell-style argv text 并可在 `REMOTE_WORKDIR` 中成功执行；该项为空时，执行命令回退为
  `MACA_LINEINFO_BINARY`。
- `ENABLE_SOURCE_LATENCY=true` 时，会检查 `MACA_OBJDUMP_BIN` 是否可执行，并检查
  `MACA_LLVM_OBJDUMP_BIN`；若该项为空则检查从 `MACA_OBJDUMP_BIN` 同目录派生的
  `llvm-objdump`。
- `MACA_OBJDUMP_ARGS` 若非空，必须是合法 shell-style argv text；默认使用
  `/opt/maca/restricted/Tools/mxgpu_llvm/bin/mxobjdump --source --print-code`，仅作为手工调试参数保留。
- `MACA_OBJDUMP_BIN` 只控制 trace-report 后续 `source-latency` 阶段的 objdump 路径，不控制
  `CYCLE_TRACE_BIN` 启动的 `cycle-trace-ng --kernel-probe` Python plugin 内部
  `mxobjdump --list-elf` 查找路径；后者的运行期路径错误会按 `TR-COL-015` 报告。
- `ENABLE_SOURCE_LATENCY` 必须是布尔值；设为 `true` 时，`collect-run` 会让 CycleTrace 采集执行
  `MACA_LINEINFO_CYCLE_TRACE_EXEC_CMD`，为空时回退 `MACA_LINEINFO_BINARY`，并追加
  运行期 `--kernel-probe`；三工具常规报告后自动运行严格 `source-latency` 后处理；不会自动重编译目标程序。
- `MACA_VISIBLE_DEVICES` 为 `-1` 时目标环境可执行 `mx-smi`，collect-run 会在采集前解析为实际 GPU；为显式数字时，该设备能从目标环境 `mx-smi` 中找到。
- `MC_TRACER_BIN`、`CYCLE_TRACE_BIN` 在目标环境中可执行；`REQUIRE_MCPROFILER=true` 时还会要求 `MC_PROFILER_BIN` 可执行。
- `PROFILER_PORT` 为空时使用默认 `50123`；非空时必须是 `1` 到 `65535` 的 TCP 端口。

校验成功时输出 `[trace_report_env] ok: mode=<mode>`；`mode` 子命令只输出当前模式。

## 执行模式

执行模式由本地 active config 决定：

| 模式 | 条件 | 执行形式 |
|---|---|---|
| local | `CONTAINER_NAME` 为空，且 `SERVER_USER` / `SERVER_HOST` 为空 | `cd "$REMOTE_WORKDIR" && <command>` |
| docker | `CONTAINER_NAME` 非空，SSH 为空 | `docker exec -i -w "$REMOTE_WORKDIR" "$CONTAINER_NAME" <command>` |
| ssh | `SERVER_USER` / `SERVER_HOST` 同时非空，`CONTAINER_NAME` 为空 | `ssh "$SERVER_USER@$SERVER_HOST" 'cd "$REMOTE_WORKDIR" && <command>'` |
| ssh+docker | SSH 与 `CONTAINER_NAME` 都非空 | `ssh "$SERVER_USER@$SERVER_HOST" 'docker exec -i -w "$REMOTE_WORKDIR" "$CONTAINER_NAME" <command>'` |

若本地设置了 `TRACE_REPORT_SSH_PASSWORD`，上述 SSH 入口会自动变为
`sshpass -e ssh ...`；未设置时保持原始 `ssh ...`。

独立手工目标环境命令必须用 `trace_report_env.py --config "$ACTIVE_CONFIG" run -- ...`
包装，使 local、docker、ssh、ssh+docker 四种模式的入口保持一致。标准 workflow 命令仍直接使用
`collect-run`、`finalize-run`、`sync-artifacts`，不要再套一层 `run --`。
