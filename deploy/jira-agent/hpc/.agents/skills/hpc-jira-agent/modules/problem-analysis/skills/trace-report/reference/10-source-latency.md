# Source Latency 后处理与源码行归因

本文说明 `trace_profile_pipeline.py source-latency` 的后处理输入、输出、归因模型和质量边界。
采集阶段如何启用 `ENABLE_SOURCE_LATENCY`、`cycle-trace-ng --kernel-probe` 对三工具采集的影响，
见 `reference/02-collection.md`；明确 `[TR-*]` 报错先按 `^## TR-...$` 标题局部读取
`reference/09-error-troubleshooting.md` 对应详情块，需要背景解释时再看 `reference/03-common-issues.md`。

本文只解决 source-latency 的输入准备、输出和归因边界；不解释 active config 生命周期、SSH/docker 执行模式、run 目录派生或 collect-run 阶段恢复。读取本文不自动触发 `reference/00-preflight.md`、`reference/01-directory-layout.md` 或 `reference/02-collection.md`；只有这些文档自己的读取门槛成立时才局部读取。

## 读取门槛

只有启用 `ENABLE_SOURCE_LATENCY=true`、lineinfo 输入缺失、source-latency 失败、或需要解释源码行归因边界时，才读取本文。普通采集、普通诊断和普通 finalize 不默认读取本文。

## 使用边界

当需要把热点 kernel 的动态 trace 事件和源码行号、静态指令序列对应起来时，可以额外生成
源码行号级静态指令证据。`ENABLE_SOURCE_LATENCY=false` 时该步骤不属于三工具 profile 的成功质量门；
`ENABLE_SOURCE_LATENCY=true` 时它属于本次 `collect-run` 的强制后处理质量门，失败必须返回非零。

标准方案是在最终可执行文件的 kernel 编译 flags 中同时加入 `-lineinfo` 和编译期
`--kernel-probe 0`，生成专用于 source-latency 的最终 binary，然后对该 binary 做 objdump。
`source-latency` 会把该 binary 复制到当前 run 的 `artifacts/lineinfo/`，
用 `mxobjdump --extract-elf` 提取 device ELF，再用 `llvm-objdump --line-numbers --disassemble`
得到指令地址到源码 `file:line` 的映射。

`ENABLE_SOURCE_LATENCY=true` 的 `collect-run` 严格路径要求 `MACA_SOURCE_FILE` 和
`MACA_LINEINFO_BINARY` 都在 active config 中设置并通过 preflight。该模式下
`cycle-trace-ng` 以运行期 `--kernel-probe` 执行 `MACA_LINEINFO_CYCLE_TRACE_EXEC_CMD`，为空时回退
`MACA_LINEINFO_BINARY`；后处理使用 `MACA_LINEINFO_BINARY` 提取 lineinfo。使用已有
device ELF、line-number objdump 或 kernel-probe linked object 的手工路径仍可通过独立
`trace_profile_pipeline.py source-latency` 命令调试，但不属于一键 `collect-run` 的默认严格路径。

## Lineinfo Binary 编译要求

`MACA_LINEINFO_BINARY` 不是普通性能采集 binary。它应使用与目标 kernel 等价的源码、宏、
优化级别和 workload，并额外加入：

- `-lineinfo`：保留 device ELF 到源码 `file:line` 的映射。
- `--kernel-probe 0`：生成与 `cycle-trace-ng --kernel-probe` 配对的 kernel-probe 兼容 binary。

编译期 `--kernel-probe 0` 不等同于采集脚本自动追加给 `cycle-trace-ng` 的运行期
`--kernel-probe`；启用 source-latency 时两者需要配对。

示例：

```make
test_maca: $(objs)
	$(maca_cc) $(CFLAG) $(include) $^ -o test_maca $(LIBS)

test_maca_lineinfo: $(objs)
	$(maca_cc) $(CFLAG) $(include) -lineinfo --kernel-probe 0 $^ -o test_maca_lineinfo $(LIBS)
```

`MACA_LINEINFO_BINARY` 指向 `test_maca_lineinfo`；如运行需要参数，放入
`MACA_LINEINFO_CYCLE_TRACE_EXEC_CMD`。

## 排查入口

标准启用方式是在 active config 中设置 `ENABLE_SOURCE_LATENCY=true`，然后执行 `collect-run`。手工 `trace_profile_pipeline.py source-latency` 只用于定位输入、objdump 或 mapping 问题；必须通过 `trace_report_env.py --config "$ACTIVE_CONFIG" run -- ...` 进入目标环境，具体参数以脚本 `--help` 为准。

`MACA_LLVM_OBJDUMP_BIN` 可显式指定；为空时从 `MACA_OBJDUMP_BIN` 同目录派生 `llvm-objdump`。

## 默认输入

- CycleTrace JSON：`<run-dir>/artifacts/c-trace_output_dpc_<CYCLE_TRACE_DPC_ID>.json`，
  不存在时回退到 `<run-dir>/c-trace_output_dpc_<CYCLE_TRACE_DPC_ID>.json`。
- lineinfo binary：`--binary "$MACA_LINEINFO_BINARY"`；该 binary 应由包含 `-lineinfo`
  和编译期 `--kernel-probe 0` 的 kernel compile flags 生成。`source-latency` 会复制该
  binary 到 `artifacts/lineinfo/lineinfo_binary`，并生成 `lineinfo_binary.*.out` 和
  `binary_lineinfo_objdump.txt`。
- lineinfo CycleTrace exec cmd：`MACA_LINEINFO_CYCLE_TRACE_EXEC_CMD` 只控制
  `cycle-trace-ng --kernel-probe` 的执行命令，可包含参数；为空时回退 `MACA_LINEINFO_BINARY`。
- kernel-probe workdir 临时文件：`cycle-trace-ng --kernel-probe` 可能在 `$REMOTE_WORKDIR`
  根目录生成 `<cycle-exec-basename>.[0-9]*.out`，例如 `test_maca_lineinfo.0.out`。该文件不是
  标准 source-latency 后处理输入；标准后处理使用 `artifacts/lineinfo/lineinfo_binary.*.out`。
  标准采集会在当前 CycleTrace 阶段失败或整次采集成功时清理这个 workdir 根目录临时文件。
- kernel-probe linked object：`<run-dir>/_kernelprobe_replication_0.link.o`，不存在时回退到
  `<run-dir>/artifacts/_kernelprobe_replication_0.link.o`。
- kernel entry label：默认 `MAIN_0`。

默认 `MACA_OBJDUMP_BIN` 为 `/opt/maca/restricted/Tools/mxgpu_llvm/bin/mxobjdump`，
`MACA_OBJDUMP_ARGS` 为 `--source --print-code`。`MACA_OBJDUMP_ARGS` 只作为手工调试参数保留；
标准 source-latency 路径固定使用 `mxobjdump --extract-elf`。

## 手工输入变体

如果最终 binary 不能直接用于标准路径，可在排查时选择已有中间输入：

| 输入 | 用途 |
|---|---|
| `--device-elf` | 已经提取出 device ELF，避免重复 `mxobjdump --extract-elf`。 |
| `--lineinfo-objdump` | 已有 line-number objdump 文本，直接复核源码行映射。 |
| `--kernelprobe-link-obj` | 复用 `cycle-trace-ng --kernel-probe` 生成的 linked object。 |

若只提供 `--kernelprobe-link-obj` 或使用默认 linked object，`source-latency` 会通过 `MACA_LLVM_OBJDUMP_BIN` 或从 `MACA_OBJDUMP_BIN` 同目录派生的 `llvm-objdump` 生成 `artifacts/lineinfo/kernelprobe_link_lineinfo_objdump.txt`。

该路径不再通过额外编译生成汇编文件。若最终 binary 无法提取 device ELF、未按
`-lineinfo --kernel-probe 0` 编译、kernel-probe 没有 linked object、CycleTrace JSON 没有
可用 `line/code`，或 objdump 中没有 `-lineinfo` 行号，源码行 cycle attribution 不可用，
只能报告指令级热点和 coverage 边界。

## 输出

- `analysis/source_latency_<tag>.md`：人工阅读报表。
- `analysis/source_latency_<tag>.csv`：源码行粒度表格。
- `analysis/source_latency_<tag>.json`：coverage、top source lines 和未映射原因。
- `REPORT_<tag>.md`：`ENABLE_SOURCE_LATENCY=true` 的 `collect-run` 会在 source-latency
  成功后刷新基础报告，把 `analysis/source_latency_<tag>.json` 中的 coverage、top source
  lines、top instruction 和边界摘要合并进 Evidence、Source-Line Attribution、Next Concrete
  Edit、Artifacts 和 Caveats。该报告仍需按完整 workflow 追加 LLM 第 9 章后才是最终交付；
  source-latency 完整明细仍以 `source_latency_<tag>.md/.csv/.json` 为准。
- `artifacts/lineinfo/lineinfo_binary`：复制到 run 目录的 lineinfo binary。
- `artifacts/lineinfo/lineinfo_binary.*.out`：从 binary 提取出的 device ELF。
- `artifacts/lineinfo/binary_lineinfo_objdump.txt`：line-number objdump 文本。

## Source-Line Cycle Attribution

当 CycleTrace JSON 和 line-number objdump 能建立指令到源码行的映射时，`source-latency`
输出统一的源码行 cycle attribution。该表只基于 CycleTrace 和 asm：有有效
`latency(cycles)` 的事件使用采样值；没有 latency 的 MTE `Transcendental Function`
按 16 cycles、其他 MTE/STE/ARRIVE/SNOP 按 4 cycles、LDU 按 64 cycles、MMA 按 16 cycles
建模。未配置固定 cost 的类别列为未建模覆盖率，不混入 modeled totals。

归因方法：

1. 读取 CycleTrace 指令事件；有有效 `latency(cycles)` 时使用该值，wrapped latency
   事件排除，没有 latency 的受支持指令按上述固定 cycle cost 建模。不得把原始 `dur=4`
   当作通用真实 latency。
2. 读取 JSON 中的 `line/code`，用 JSON filtered line 匹配 lineinfo objdump 中的
   instruction line，再通过 `-lineinfo` device ELF objdump / `llvm-objdump --line-numbers`
   给出的源码 `file:line` 回到用户源码。
3. 把可映射指令聚合到用户源码行；若定位落到 runtime header，
   报表会标记 attribution mode，并在控制语句 header 后的 intrinsic 场景中尝试归到下一条源码语句。

报表中的百分比：

- `% total` = 该源码行 mapped modeled cycles / 全部 modeled cycles。它是保守的全局占比。
- `% mapped` = 该源码行 mapped modeled cycles / 成功映射到源码行的 modeled cycles。它适合看
  已解释源码行之间的相对排序，但会排除未映射部分。

质量边界：

- 报表必须输出 modeled events/cycles、JSON `line/code` 覆盖、lineinfo entry instruction
  覆盖、源码行 mapped modeled events/cycles、mapping modes、unmapped reasons 和未建模类别；
  coverage 低时只能做低置信归因。
- 该能力给出 CycleTrace/asm modeled attribution，不是完整 wall-clock time 占比。
- 任何源码行归因都必须来自真实 JSON `line/code` 和真实 lineinfo mapping；objdump address
  只作为 mapped asm 明细保留，不是当前标准路径的 JSON 事件回映射入口。缺少源码映射输入时不要编造。
