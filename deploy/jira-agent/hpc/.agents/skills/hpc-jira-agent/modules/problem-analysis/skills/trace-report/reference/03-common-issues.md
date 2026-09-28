# 常见问题排查

本文档只保留未集中到错误表中的背景说明和非报错类常见问题。日志中已有明确 `[TR-*]` 时，不读本文；
先按 `^## TR-...$` 标题局部读取 `reference/09-error-troubleshooting.md` 对应详情块。

## 采集参数

### `--case-name` 应该传什么？

通常不传。脚本默认使用 `<run-dir>` 的 basename，即 `<kernel>_v<N>_<tag>`。只有需要对齐外部 mcProfiler case 名时才显式传 `--case-name`。

## CycleTrace

### CycleTrace JSON 过大，需要只抓目标 kernel 或指定 repetition

`cycle-trace-ng` 支持 `--sample-mode C` 读取 `CYCLE_TRACE_TOOLS_JSON` 中的
`cycleTrace.kernels`。当全量 CycleTrace 文件过大，或用户明确要求单算子 CycleTrace 采集时，
优先使用标准 `collect-run`，并在本地 active config 设置：

```yaml
CYCLE_TRACE_SAMPLE_MODE: "C"
CYCLE_TRACE_KERNEL_NAME: "<target_kernel_name>"
CYCLE_TRACE_KERNEL_REPEAT: "1"
CYCLE_TRACE_TOOLS_JSON: "/opt/maca/etc/tools.json"
```

采集脚本会自动备份 `CYCLE_TRACE_TOOLS_JSON`、临时收敛 `cycleTrace.kernels`、运行
`--sample-mode C`，并在退出时恢复原配置。具体采集行为以 `reference/02-collection.md`
为准，sample mode 和 kernel filter 语义以 `reference/12-cycle-trace-options.md` 为准。

注意事项：

- `kernelName` 必须使用 mcTracer、mcProfiler `mtreg_occupancy` 下方 `name`、报告或工具输出中的真实 kernel 名，不要填写算子简称、run 目录名或手写推测名称。
- `repeat < 0` 表示不限 repetition；设置 kernel 名但不设置 repeat 时脚本使用 `-1`；`repeat >= 0` 表示指定 repetition，适合重复调用场景中抽样。
- kernel 名不匹配时，MTE/STE/BSM/GLOBAL/ARRIVE/LDU 硬件事件会变为 0，pipeline 会判为无效 CycleTrace；但文件仍可能包含大量 wave start/end 冗余 token，不一定很小。
- `/opt/maca/etc/tools.json` 是全局配置；脚本会在正常退出、失败退出、INT/TERM 时恢复。若进程被 `SIGKILL` 或宿主机崩溃，仍需人工检查并恢复。
- 不建议为了让 JSON 变小盲目使用 `CYCLE_TRACE_SAMPLE_MODE=B` 或 `D`；它们是工具特定 blit/DIDT kernel 过滤，可能采不到当前目标 kernel。sample mode 详细语义见 `reference/12-cycle-trace-options.md`。
- 若 small-workgroup recovery campaign 因已核验的 `TR-COL-006` 重复 trace target，重复仅用于提高硬件事件采样机会。查看报告的 target launch count 和 invocation scope，不能将该命令的聚合 instruction count 或 span 解释成单次 kernel latency。

### 多个 `dpc_N.json` 文件，该用哪个？
`c-trace_output_dpc_N.json` 中的 `N` 对应 `--dpc-id`。标准一键采集要求 `CYCLE_TRACE_DPC_ID`
中请求的每个 DPC 文件都存在并归档；digest/report 只读取逗号列表中的第一个 DPC。例如
`CYCLE_TRACE_DPC_ID=2,3` 时，`dpc_2` 和 `dpc_3` 都是必需归档产物，但自动分析只使用
`dpc_2`。怀疑 DPC 间负载不均时，用 Web UI 查看已归档的所有 DPC，或在用户授权后调整
`CYCLE_TRACE_DPC_ID` 重采。

### 为什么所有指令 dur=4？
CycleTrace 不以真实延迟记录指令——`dur=4` 是 issue slot 计数，不是执行周期。
真实延迟数据从 mcProfiler `MTE_4CYCLES/MTE_16CYCLES/MMA_16CYCLES/MMA_32CYCLES` 获取。

### `cycle-trace-ng --kernel-probe` 报 `Load libpython failed`

`ENABLE_SOURCE_LATENCY=true` 时，标准采集会让 `cycle-trace-ng` 执行
`MACA_LINEINFO_CYCLE_TRACE_EXEC_CMD`，为空时回退 `MACA_LINEINFO_BINARY`，并追加
运行期 `--kernel-probe`。某些
MACA 安装中，`cycle-trace-ng` 会按 `MACA_PATH` 或默认 `/opt/maca` 查找 bundled
`cycle-trace/python` 和 Python plugin 资源。如果 `CYCLE_TRACE_BIN` 来自另一个安装目录，
但 `/opt/maca/share/cycle-trace` 不存在或指向不匹配版本，日志可能出现：

```text
Load libpython failed.
cannot acquire createPyPlugin handle;
cannot load python plugin, skip kernel probe ..
```

这类失败可能不会让 `cycle-trace-ng` 返回非零；工具会跳过 kernel probe 后继续执行。
因此 `ENABLE_SOURCE_LATENCY=true` 时 trace-report 会扫描日志并按 `TR-COL-014` 主动失败。
修复动作以 `reference/09-error-troubleshooting.md` 的 `TR-COL-014` 为准；背景上通常是
`/opt/maca/share/cycle-trace` 与当前 `CYCLE_TRACE_BIN` 对应安装目录不匹配。

### Python plugin 加载成功后 `mxobjdump --list-elf` 失败

`ENABLE_SOURCE_LATENCY=true` 时，`CYCLE_TRACE_BIN` 对应的 `cycle-trace-ng --kernel-probe`
会启动自己的 Python plugin。该 plugin 可能按自身环境查找 `mxobjdump`，并明确报：

```text
Command '<plugin-expected-mxobjdump> --list-elf <binary>' returned non-zero exit status 127.
```

这表示 plugin 已加载成功，但它期望的 `mxobjdump` 路径在目标环境中不可用。这个错误不同于
`TR-COL-014` 的 Python plugin 加载失败，也不同于 trace-report 后续 `source-latency`
阶段使用的 `MACA_OBJDUMP_BIN`。修改 `MACA_OBJDUMP_BIN` 不能修复 `CYCLE_TRACE_BIN`
内部 plugin 的查找路径。

修复动作以 `reference/09-error-troubleshooting.md` 的 `TR-COL-015` 为准。这里保留的关键判断是：
plugin 内部 `mxobjdump --list-elf` 路径问题属于 CycleTrace kernel-probe 采集阶段，不属于
trace-report 后续 `source-latency` 后处理阶段。

### 如何区分 kernel-probe 采集问题和 source-latency 后处理问题

`ENABLE_SOURCE_LATENCY=true` 包含两个阶段：

1. 采集阶段：`collect-run` 让 `cycle-trace-ng` 执行
   `MACA_LINEINFO_CYCLE_TRACE_EXEC_CMD`，为空时回退 `MACA_LINEINFO_BINARY`，并追加
   运行期 `--kernel-probe`，目的是让 CycleTrace JSON 带上后续源码行归因需要的 `line/code`
   信息。这个阶段的问题通常出现在
   `<run-dir>/logs/cycle-trace-ng.log`，例如 Python plugin 加载失败、`skip kernel probe`，或 plugin 内部
   `mxobjdump --list-elf` 路径错误；明确报错按 `TR-COL-014` / `TR-COL-015` 处理。
2. 后处理阶段：三工具采集和常规报告成功后，`trace_profile_pipeline.py source-latency` 使用
   `MACA_LINEINFO_BINARY`、`MACA_OBJDUMP_BIN` 和同目录或显式指定的 `llvm-objdump` 生成源码行
   attribution。这个阶段不控制 `cycle-trace-ng --kernel-probe` Python plugin 内部的工具查找路径。

因此，修改 `MACA_OBJDUMP_BIN` 只影响后处理的 `mxobjdump --extract-elf`，不能修复
`CYCLE_TRACE_BIN` plugin 内部 `mxobjdump --list-elf` 路径问题。后处理输入、输出、归因模型和
手工调试命令见 `reference/10-source-latency.md`。

---

## mcTracer

### `shared_memeory_occupancy(%)` 拼写错误
这是 mcTracer JSON key 的已知 typo（`memeory` 而非 `memory`）。digest 脚本已处理。

### `private_per_thread = 0` 但怀疑有 PM 溢出
mcTracer 的 PM 检测是**启动时**的编译器报告。运行时仍可能因寄存器压力导致溢出。
用 mcProfiler `Private Read/Write Instructions` 或 Compiler Explorer 搜索 `ldp`/`stp` 双重确认。

---

## mcProfiler

### `mcProfiler` 卡住在代理地址或缺少 JSON

如果日志反复出现类似 `try to connect to server`、`connect to 10.x.x.x:3456`，
或者 `mcProfiler` 最终没有生成 `report_dumped_result.json` / `report.txt.json`，
先检查目标环境是否设置了代理：

```bash
env | grep -i '_proxy'
```

标准采集脚本会在 `mcProfiler` 子进程里清除 `HTTP_PROXY`、`HTTPS_PROXY`、`ALL_PROXY`、
`FTP_PROXY` 及小写同名变量，并设置 `NO_PROXY=*`，避免 mcProfiler 的本地 HTTP/RPC
连接被代理劫持。若手工复现，应使用同样的 no-proxy 包装：

```bash
env -u HTTP_PROXY -u HTTPS_PROXY -u ALL_PROXY -u FTP_PROXY \
    -u http_proxy -u https_proxy -u all_proxy -u ftp_proxy \
    NO_PROXY='*' no_proxy='*' \
    [MC_PROFILER_BIN] perf_exec --cmdline '...' --casename ... --output ...
```

### `mcProfiler` 端口被占用

如果日志出现 `Address already in use`、`127.0.0.1:50123` 或 Flask 端口绑定失败，
说明 mcProfiler 默认本地服务端口被占用。标准采集通过 active config 的
`PROFILER_PORT` 传给 `mcProfiler perf_exec --port`，默认值为 `50123`。改成目标环境中未占用的
`1..65535` 端口后重新执行 `collect-run`。

---

## Digest 生成

### `metrics_key_<tag>.json` 比 `metrics_all_<tag>.json` 少很多字段
这是预期行为。`metrics_key` 只保留稳定核心指标、采集 scope 和覆盖度摘要；完整字段、未提升指标及不可用维度在 `metrics_all` 中。

---

## 采集环境

### Profiling 导致 kernel 运行时间爆炸
mcProfiler 通过多次 replay 采集所有 counter batch，
实际运行时间可能比正常执行长 10-100×。这是正常现象。

### Kernel 在 profiler 下 crash 或不正常
1. Profiler 的 clock jitter 可能影响 timing-sensitive kernel
2. 先验证 kernel 正确性（不挂 profiler 跑 100 次确认稳定）
3. 降低 `--iterations` 参数减少 replay 次数

---

## 指标解读

### MMA 占比高（> 20%）但 performance 差
MMA 指令多不代表 MMA 流水线饱和——如果 MMA 指令被内存依赖停顿，占比虚高。检查 `profiler.ap_mma_duty_pct`（mcProfiler `AP MMA Duty ratio`）和 `profiler.real_ipc`。

### NOP 占比高但不知道原因
C500 没有 warp stall 计数器（这是相较 NVIDIA NCU 的核心差距）。
NOP 高 + IPC 低 → 流水线停顿（可能是 memory latency、barrier、bank conflict）。
用排除法：先查 smem efficiency + GVM buffer + L2 hit rate。

### "AP busy Duty" = 0.00% 但 kernel 明明在跑
这个指标是 AP busy / Total Cycles。如果 kernel 短但 profiling 时间窗口长
（特别是 mcProfiler 的 replay 次数多），AP busy 占比会很低。
关注 `Compute Instructions busy Duty`（计算指令占 AP 总 cycle 的比重）更有意义。

### Instruction Distribution 应该看 `cat` 还是 `name`？
主分类看 CycleTrace `cat`，因为 `cat` 表示事件在哪类 C500 硬件上执行。`name` 只用于
解释子事件，例如 `STE` cat 中的 `S_NOP`/`Branch`，`GLOBAL` cat 中的
`GVM Load`/`GVM Store`。
