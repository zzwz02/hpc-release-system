# Profile Artifacts 目录规范

## 顶层规则

所有 profiling 产物放在优化 repo 的 `profile-artifacts/` 下。
每个版本或实验目标使用一个独立子目录，不跨版本覆盖或复用。若同一个 run 目录的标准
`collect-run` 失败，后续重试可以复用该 run 目录中已经成功且有效的阶段产物，并从失败阶段继续。

## 命名

```
profile-artifacts/<kernel>_v<N>_<tag>/
```

- `<kernel>`: 算子名（如 `gemm`, `flash_attn`, `layernorm`）
- `v<N>`: 版本号，递增（v0, v1, v2...）
- `<tag>`: 描述性标签（`baseline`, `opt_tile128`, `fix_bank_conflict`）

示例：`profile-artifacts/gemm_v0_baseline/`、`profile-artifacts/gemm_v1_tile64/`

## 确认、派生与创建 run 目录

Workflow 确认 run 目录后，统一交给 `collect-run` 从目录名派生变量并在目标环境创建目录：

```bash
python3 scripts/trace_report_env.py --config "$ACTIVE_CONFIG" collect-run \
  --run-dir profile-artifacts/<kernel>_v<N>_<tag>
```

`collect-run` 内部会派生：

- `PROFILE_RUN_DIR`: 去掉末尾 `/` 的 run 目录路径
- `PROFILE_TAG`: run 目录 basename 中 `_v<N>_` 之后的 `<tag>`
- `PROFILE_KERNEL`: run 目录 basename 中 `_v<N>_` 之前的 `<kernel>`
- `PROFILE_VERSION`: `v<N>`

如果 run 目录名无法按 `<kernel>_v<N>_<tag>` 解析，按
`reference/09-error-troubleshooting.md` 的 `TR-RUN-002` 处理；不要手写或猜测
`PROFILE_TAG`。

离线排查或只需要 shell exports 时，可以单独使用 `derive-run`；标准采集 workflow 不再手工执行 `derive-run` 或目标 `mkdir`。

mcProfiler case 名称默认使用 run 目录 basename，即 `<kernel>_v<N>_<tag>`；除非有明确兼容需求，不要另行传入 `--case-name`。

## 标准 run 目录结构

```
profile-artifacts/<kernel>_v<N>_<tag>/
├── artifacts/                      # trace_profile_pipeline.py collect 归档的原始 JSON 与最新 PNG
│   ├── c-trace_output_dpc_<primary CYCLE_TRACE_DPC_ID>.json  # CycleTrace primary DPC 指令 trace，自动分析此文件
│   ├── c-trace_output_dpc_<other CYCLE_TRACE_DPC_ID>.json    # 多 DPC 采集时归档其他 DPC trace，但不自动分析
│   ├── tracer_out.json              # mcTracer 输出（launch config）
│   ├── mcprofiler_report_dumped.json  # mcProfiler 硬件计数器；多 occurrence 时不生成
│   ├── mcprofiler_report.txt.json   # mcProfiler 补充数据（Roofline）；多 occurrence 时不生成
│   ├── mcprofiler_report.txt        # （可选）mcProfiler 文本报告；多 occurrence 时不生成
│   ├── mcprofiler_report.txt.csv    # （可选）mcProfiler 文本报告 CSV；多 occurrence 时不生成
│   ├── mcprofiler_per_kernel/       # （可选）CYCLE_TRACE_KERNEL_NAME 下的 per-kernel occurrence 审计/拆分输入
│   ├── collection_manifest.json
│   └── lineinfo/                    # （可选）source-latency lineinfo binary/objdump 证据
├── analysis/                       # trace_profile_pipeline.py analyze 生成的指标/报告
│   ├── metrics_key_<tag>.json       # 核心指标 + 采集 scope + 覆盖度摘要
│   ├── metrics_all_<tag>.json       # 完整指标 + metric_coverage
│   ├── digest_<tag>.md
│   └── source_latency_<tag>.{md,csv,json}  # （可选）源码行 cycle attribution
├── REPORT_<tag>.md                 # 最终交付报告，引用 analysis/ 与 artifacts/ 证据
├── usecases/                       # （可选）split-usecases 后处理生成的单 kernel 单 usecase 派生报告
│   ├── manifest_<tag>.json          # 拆分匹配结果、confidence、launch/segment 对应关系
│   ├── index_<tag>.md               # usecase -> occurrence/report 人工索引
│   └── usecase_000/
│       ├── artifacts/               # filtered tracer/CycleTrace；mcProfiler/PNG 默认引用父级 occurrence
│       ├── analysis/
│       └── REPORT_<tag>_usecase_000.md
└── baselines/                      # （可选）对比用 baseline 报告
    └── <baseline_version>.json
```

`collection_manifest.json` 必须记录：

- `missing_required`: 必需原始产物缺失列表，当前包含 `tracer_out.json` 和
  `CYCLE_TRACE_DPC_ID` 请求的所有 `c-trace_output_dpc_*.json`
- `cycle_trace_primary` / `cycle_trace_files`: primary DPC 文件名和实际已归档的 DPC
  CycleTrace 文件名；多 DPC 时报告必须说明“归档全部请求 DPC、只分析 primary”
- `require_mcprofiler`: 本次归档/分析是否要求 mcProfiler 完整产物，来自
  `REQUIRE_MCPROFILER`
- `mcprofiler_available`: 两个 mcProfiler JSON 都存在且可解析时为 `true`，否则为
  `false`。当 `mcprofiler_scope=per-kernel-multiple-occurrences` 时，主报告不会任选一个
  occurrence 生成这两个标准 JSON；此时以 `mcprofiler_per_kernel` 的完整 occurrence 为拆分输入。
- `missing_optional`: `REQUIRE_MCPROFILER=false` 时缺失的可选 mcProfiler 产物列表；
  strict 模式下这些缺失会同时进入 `missing_required`；但
  `mcprofiler_scope=per-kernel-multiple-occurrences` 时，标准 mcProfiler JSON 缺失不是错误，要求
  per-kernel manifest 中 `occurrence_count == complete_occurrence_count > 1`
- `invalid_optional`: 可选 mcProfiler JSON 存在但不可解析时的错误列表；strict 模式下这些
  错误会同时进入 `invalid_required`
- `invalid_required`: 必需产物内容无效列表，例如 CycleTrace JSON 不可读、没有
  `traceEvents`、或没有 MTE/STE/MMA/BSM/GLOBAL/ARRIVE/LDU 硬件事件

若 active config 设置了 `CYCLE_TRACE_KERNEL_NAME`，`collection_manifest.json` 还必须能证明本次归档
属于目标 kernel scope：命令级旧 `mcprofiler_report_*` 不能替代
`mcprofiler_per_kernel/manifest.json`，旧的命令级 CycleTrace 也不能替代匹配当前 kernel/repeat/sample
mode 的 pipeline scope。由未设置 kernel 的 run 目录切换到设置 kernel 时，只有 mcTracer
`tracer_out.json` 可复用；CycleTrace、mcProfiler、analysis 和 report 需要重采或重新生成。

标准 `collect-run` 只有在 `ENABLE_SOURCE_LATENCY=true` 时生成
`artifacts/lineinfo/` 和 `analysis/source_latency_<tag>.*`；此时它们属于严格质量门。
普通三工具采集保持 `ENABLE_SOURCE_LATENCY=false` 时不要求这些产物。独立手工执行
`trace_profile_pipeline.py source-latency` 也可能生成同名产物，但这不改变标准一键采集的质量门。

`usecases/` 由自动或手工 `trace_profile_pipeline.py split-usecases` 生成；标准 `collect-run` 只在主报告
进入 `per-kernel-multiple-occurrences` 边界时自动触发。它复用同一 run 的 `artifacts/`，为不同 kernel
或同名 kernel 的不同 usecase 生成派生 artifacts 与独立报告；主 `REPORT_<tag>.md` 不会因此被覆盖。
`usecase_XXX/artifacts/tracer_out.json` 和 primary CycleTrace JSON 是过滤后的派生输入，必须保留以便
单 usecase 报告可独立复查。mcProfiler 标准文件和 PNG 不复制到 usecase 目录；当该 usecase
匹配到单个 per-kernel occurrence 时，`collection_manifest.json` 会写入
`mcprofiler_scope=per-usecase-ref`、`matched_occurrence_id` 和 `mcprofiler_reference`，报告读取父级
`artifacts/mcprofiler_per_kernel/occurrence_*` 中的 JSON/TXT/PNG。没有匹配时不会引用或复制父级
命令级 mcProfiler 文件。对 usecase 派生报告，`REQUIRE_MCPROFILER=true` 不会强制所有 usecase 都有
mcProfiler JSON；只有 `mcprofiler_scope=per-usecase-ref` 时，缺失该 occurrence 的引用 JSON 才进入
`missing_required`。`per-kernel-unmatched` 和 `per-usecase-multiple-occurrences` 会把 profiler
写成不可用边界。

usecase 与 occurrence 都使用 0-based 编号，但编号含义不同：`usecase_000` 是按 signature 聚合后的
派生用例目录，`occurrence_000` 是 mcProfiler per-kernel occurrence 目录。两者的真实对应关系以
`usecases/index_<tag>.md`、`usecases/manifest_<tag>.json` 和报告中的 `Usecase mapping` 为准。

标准一键采集只有在必需原始产物、`analysis/*.json`、`analysis/*.md` 和
`REPORT_<tag>.md` 都存在且必需内容有效时才算成功。若 `REQUIRE_MCPROFILER=true`，命令级或单
occurrence 模式下两个 mcProfiler JSON 也是必需内容；
`mcprofiler_scope=per-kernel-multiple-occurrences` 时，完整 per-kernel occurrence 目录是必需内容，
主报告不要求也不会生成标准 mcProfiler JSON。默认 `REQUIRE_MCPROFILER=false` 时缺失 mcProfiler
仍可成功，但报告必须把相关指标写成 `N/A`。

## 对比两个版本

每个版本独立成目录；不要把不同版本、不同 shape 或不同优化目标写入同一个 run 目录。对比时引用两份 report/digest，或用
`trace_profile_pipeline.py compare` 生成 `analysis/compare_<tag1>_vs_<tag2>.md`。

```text
profile-artifacts/
├── gemm_v0_baseline/
│   ├── REPORT_baseline.md
│   └── analysis/digest_baseline.md
├── gemm_v1_opt_tile128/
│   ├── REPORT_opt_tile128.md
│   └── analysis/digest_opt_tile128.md
└── gemm_v0_vs_v1_comparison.md     # 对比文档，引用两份 report/digest
```

## 什么不该放进 run 目录

- 临时文件、备份文件、`*.bak`
- 数据集文件（safetensors、npy 等）
- 编译器中间产物（`.o`, `.d`）
- 工具缓存
