# 错误排查

本文汇总 `scripts/` 中会直接输出的报错，以及 `reference/` 中已有的排查场景。遇到
Preflight、脚本同步、采集、解析或追加诊断失败时，脚本会尽量在摘要输出中给出
`[TR-...]` 报错 ID、`cause_code`、`last_stderr` 和 `log:` 路径；也可以按报错文本匹配详情块中的 `匹配信息`，再按对应处理方法处理。若表内建议涉及 `OP_EXEC_CMD`、`REMOTE_WORKDIR`、`SERVER_*` 或
`CONTAINER_NAME`，只报告和验证，不要擅自修改受保护配置。

低 token 使用方式：

1. 先从命令摘要的 `cause_code`、`last_error_code`、`last_stderr` 或 `log:` 路径中提取具体 `[TR-*]` 错误码；必要时只读取对应 `<run-dir>/logs/*.log` 的 tail。
2. 用标题精确搜索读取对应详情块，例如：

```bash
rg -n "^## TR-COL-006$" -A 12 reference/09-error-troubleshooting.md
```

3. 如果同一失败同时包含 workflow 阶段码和具体原因码，先定位 `TR-WF-*` 阶段码，再定位摘要 `cause_code` 或日志 tail 中最末的 `TR-COL-*`、`TR-ART-*`、`TR-ANA-*`、`TR-REP-*`、`TR-LLM-*`、`TR-USC-*` 或 `TR-SYNC-*` 具体原因码。
4. 若 `-A 12` 未覆盖完整详情块，再按同一标题增加 `-A` 范围；不要因出现错误码就全文读取本文。
5. 只有没有明确错误码、错误码定位不到、多个阶段互相矛盾，或需要跨阶段清理/重跑/参考文档索引时，才读取本文相关章节或全文。

## 错误码索引

| 报错 ID | 阶段 | 摘要 |
|---|---|---|
| TR-PF-001 | preflight/config | 没有显式传本地 active config |
| TR-PF-002 | preflight/config | 指定的 active config 不存在 |
| TR-PF-003 | preflight/config | `init-config --set` 格式错误或 key 不受支持 |
| TR-PF-004 | preflight/config | active config 已存在且未授权覆盖 |
| TR-PF-005 | preflight/config | 当前进程无法创建 `LOCAL_WORKDIR` 或写入 active config |
| TR-PF-006 | preflight/config | YAML 不是 flat env contract |
| TR-PF-007 | preflight/config | SSH 参数只填了一半 |
| TR-PF-008 | preflight/config | 基础配置为空或模式值非法 |
| TR-PF-009 | preflight/config | `OP_EXEC_CMD` shell argv 解析失败 |
| TR-PF-010 | preflight/config | CycleTrace PEU 参数非法 |
| TR-PF-011 | preflight/config | CycleTrace DPG buffer 页数非法 |
| TR-PF-012 | preflight/config | CycleTrace AP 参数非法 |
| TR-PF-013 | preflight/config | CycleTrace DPC 参数非法 |
| TR-PF-014 | preflight/config | 工具路径配置为空 |
| TR-PF-015 | preflight/config | 三工具采集超时参数非法 |
| TR-PF-016 | preflight/config | 布尔开关非法 |
| TR-PF-017 | preflight/config | mcProfiler 本地服务端口非法 |
| TR-PF-018 | preflight/config | CycleTrace kernel filter 配置非法 |
| TR-PF-019 | preflight/config | source-latency 严格模式缺少输入 |
| TR-PF-020 | preflight/config | lineinfo CycleTrace 执行命令格式非法 |
| TR-PF-021 | preflight/config | source-latency objdump 配置非法 |
| TR-PF-022 | preflight/config | `--config` 与 `collect-run --set` 同时使用 |
| TR-PF-023 | preflight/config | YAML 中存在未知配置 key |
| TR-SSH-001 | ssh | 用户只设置了 `SSHPASS`，没有设置 `TRACE_REPORT_SSH_PASSWORD` |
| TR-SSH-002 | ssh | 设置了 `TRACE_REPORT_SSH_PASSWORD`，但本机没有 `sshpass` |
| TR-SSH-003 | ssh | SSH 主机不可达、认证失败或密码错误 |
| TR-TGT-001 | target-env | 目标执行目录不存在 |
| TR-TGT-002 | target-env | docker 容器配置缺失、不存在或不可 inspect |
| TR-TGT-003 | target-env | 目标环境工具不可执行或 CycleTrace tools.json 不可用 |
| TR-TGT-004 | target-env | 目标命令在 `REMOTE_WORKDIR` 下运行失败 |
| TR-TGT-005 | target-env | 目标环境没有 `mx-smi` |
| TR-TGT-006 | target-env | `MACA_VISIBLE_DEVICES` 不在目标 `mx-smi` 输出中 |
| TR-TGT-009 | target-env | 自动 GPU 选择失败、阈值非法或等待超时 |
| TR-TGT-007 | target-env | source-latency 输入文件不存在 |
| TR-TGT-008 | target-env | lineinfo CycleTrace 执行命令运行失败 |
| TR-SYNC-001 | sync | 同步脚本目标等于当前 skill 脚本目录 |
| TR-SYNC-002 | sync | 同步后入口脚本缺失 |
| TR-SYNC-003 | sync | 目标命令、`docker cp`、`rsync` 或远端 staging 命令失败 |
| TR-WF-001 | collect-run workflow | `collect-run` 在 active config 初始化、run 目录解析或 Preflight 阶段失败 |
| TR-WF-002 | collect-run workflow | `collect-run` 在同步脚本阶段失败 |
| TR-WF-003 | collect-run workflow | `collect-run` 在目标环境创建 run 目录阶段失败 |
| TR-WF-004 | collect-run workflow | `collect-run` 在采集阶段失败 |
| TR-WF-005 | collect-run workflow | `collect-run` 在强制 source-latency 阶段失败 |
| TR-WF-006 | collect-run workflow | `collect-run` 在 source-latency 后刷新基础报告失败 |
| TR-WF-007 | collect-run workflow | `collect-run` 在基础报告检查阶段失败 |
| TR-WF-008 | collect-run workflow | `collect-run` 在自动 usecase 拆分阶段失败 |
| TR-WF-009 | collect-run workflow | `collect-run` 在 generated usecase source-latency 阶段失败 |
| TR-WF-010 | collect-run workflow | `collect-run` 在 generated usecase 报告刷新阶段失败 |
| TR-RUN-001 | env runner | `trace_report_env.py run` 没有目标命令 |
| TR-RUN-002 | env runner | run 目录名不符合规范 |
| TR-RUN-003 | env runner | `sync-artifacts` 指向非法目录 |
| TR-COL-001 | collect | 采集脚本必需参数缺失 |
| TR-COL-002 | collect | 采集脚本参数值非法 |
| TR-COL-003 | collect | 执行命令无法拆成可执行 argv |
| TR-COL-004 | collect | pipeline 入口脚本未同步、目标还是旧版本或 CycleTrace tools.json 临时更新失败 |
| TR-COL-005 | collect | mcTracer 没有生成新 JSON 或 JSON 为空 |
| TR-COL-006 | collect | CycleTrace primary DPC JSON 缺失、为空、无硬件事件或 JSON 无效 |
| TR-COL-007 | collect | CycleTrace 命令不存在或路径错误 |
| TR-COL-008 | collect | CycleTrace 开启 JSON 后不输出文件 |
| TR-COL-009 | collect | `cycle-trace-ng` 在 DPG 后崩溃 |
| TR-COL-010 | collect | mcProfiler 命令或输出目录异常 |
| TR-COL-011 | collect | strict 模式下 mcProfiler 必需 JSON 缺失、为空、不可解析或 per-kernel 归档失败 |
| TR-COL-012 | collect | 归档或最终报告产物缺失，或已有产物 scope 与当前 active config 不匹配 |
| TR-COL-013 | collect | 必需采集阶段超时 |
| TR-COL-014 | collect | kernel-probe Python plugin 加载失败 |
| TR-COL-015 | collect | kernel-probe Python plugin 内部 objdump 路径不可用 |
| TR-COL-016 | collect | CycleTrace kernel filter 名称与 mcTracer `args.name` 不匹配 |
| TR-COL-017 | collect | mcTracer JSON 无效、缺少 `traceEvents` 或没有 kernel launch event |
| TR-ART-001 | artifacts | 离线归档源不是目录 |
| TR-ART-002 | artifacts | 标准化 artifacts 不完整或 CycleTrace 无效 |
| TR-ANA-001 | analyze | `analyze` 找不到 artifacts 或 run 目录 |
| TR-ANA-002 | analyze | Digest 中 `profiler` 字段缺失 |
| TR-ANA-003 | analyze | CycleTrace JSON 可读但不含有效事件 |
| TR-ANA-004 | analyze | 离线分析时 CycleTrace kernel filter 后无硬件事件 |
| TR-ANA-005 | analyze | 离线分析时 mcTracer 找不到目标 kernel event |
| TR-CMP-001 | compare | compare 输入不足或格式错误 |
| TR-CMP-002 | compare | compare 无法确定输出或找不到 metrics |
| TR-USC-001 | usecase split | usecase 拆分缺少输入目录或必需 artifacts |
| TR-USC-002 | usecase split | usecase 拆分 confidence 参数非法或匹配未达到生成条件 |
| TR-LLM-001 | llm diagnosis | 追加诊断入口、诊断文件或包装执行错误 |
| TR-LLM-002 | llm diagnosis | here-doc 或 stdin 内容为空 |
| TR-LLM-003 | llm diagnosis | LLM 诊断缺少必需 heading |
| TR-LLM-004 | llm diagnosis | `Final Diagnosis` 没有诊断项或缺少每项推理链 |
| TR-LLM-005 | llm diagnosis | 报告路径不存在或 marker 不完整 |
| TR-REP-001 | report check | 最终报告检查发现必需文件缺失或为空 |
| TR-REP-002 | report check | `collection_manifest.json` 显示必需产物缺失、无效或 usecase mcProfiler 引用不可用 |
| TR-REP-003 | report check | `metrics_all_<tag>.json` 结构非法 |
| TR-REP-004 | report check | source-latency 未被基础报告吸收或不可用 |
| TR-REP-005 | report check | 最终报告缺少必需 LLM 第 9 章 |
| TR-REP-006 | report check | CycleTrace kernel filter 启用时最终报告缺少性能数据 scope |
| TR-REP-007 | report check | generated usecase 报告缺少必需 LLM 第 9 章 |

## 错误码详情

按错误码标题局部读取详情块；不要全文读取本节。详情块是本文唯一的按需读取单位；定位到错误码后只读取对应 `## TR-...` 到下一个 `##` 前的内容。

## TR-PF-001

- 阶段：preflight/config
- 触发：没有显式传本地 active config
- 匹配信息：
  - `active config must be passed explicitly with --config "$ACTIVE_CONFIG"`
- 处理方法：
  - 先用 `collect-run --set KEY=VALUE ...` 或 `init-config` 生成 `[LOCAL_WORKDIR]/.trace-report/config/trace_report_env.yaml`，设置 `ACTIVE_CONFIG`；
  - 后续 `collect-run`、`validate`、`export`、`mode`、`run`、`sync-scripts`、`sync-artifacts` 都显式传 `--config "$ACTIVE_CONFIG"`。

## TR-PF-002

- 阶段：preflight/config
- 触发：指定的 active config 不存在
- 匹配信息：
  - `active config not found: <path>`
  - `config file not found: <path>`
- 处理方法：
  - 确认 `LOCAL_WORKDIR` 和 `ACTIVE_CONFIG` 指向同一台本地机器上的 active config；
  - 不要改仓库内模板替代 active config。

## TR-PF-003

- 阶段：preflight/config
- 触发：`init-config --set` 格式错误或 key 不受支持
- 匹配信息：
  - `--set requires KEY=VALUE`
  - `--set requires a non-empty KEY`
  - `unknown config key`
- 处理方法：
  - 用重复的 `--set KEY=VALUE` 传入 `KNOWN_KEYS` 中的顶层配置项；
  - 不要传嵌套结构或未知 key。

## TR-PF-004

- 阶段：preflight/config
- 触发：active config 已存在且未授权覆盖
- 匹配信息：
  - `active config already exists: ...; use --force to overwrite it`
- 处理方法：
  - 优先复用并编辑本地 active config；
  - 只有明确要覆盖本地配置时才使用 `--force`。

## TR-PF-005

- 阶段：preflight/config
- 触发：当前进程无法写入 active config 位置
- 匹配信息：
  - `cannot write active config at ...`
  - `Read-only file system`
  - `Permission denied`
- 处理方法：
  - 给 `LOCAL_WORKDIR` 授权写入，或在具备权限的本地环境重跑 `init-config`；
  - 不要把仓库模板 `config/trace_report_env.yaml` 当成运行配置。

## TR-PF-006

- 阶段：preflight/config
- 触发：YAML 不是 flat env contract
- 匹配信息：
  - `<source>:<line>: nested YAML is not supported`
  - `expected KEY: value`
  - `invalid key`
- 处理方法：
  - active config 只能使用顶层 `KEY: value` 标量；
  - 删除嵌套 YAML、list、复杂对象和非法 key。

## TR-PF-007

- 阶段：preflight/config
- 触发：SSH 参数只填了一半
- 匹配信息：
  - `SERVER_USER and SERVER_HOST must be both set or both empty`
- 处理方法：
  - 两者都为空表示 local/docker；
  - 两者都非空表示 ssh/ssh+docker。
  - 除非用户明确指定，不要替用户猜测远端账号或主机。

## TR-PF-008

- 阶段：preflight/config
- 触发：基础配置为空或模式值非法
- 匹配信息：
  - `LOCAL_WORKDIR must not be empty`
  - `LOCAL_WORKDIR must not be empty before writing active config`
  - `REMOTE_WORKDIR must not be empty`
  - `OP_EXEC_CMD must not be empty`
  - `MACA_VISIBLE_DEVICES must not be empty`
  - `HEURISTIC_BOUND_MODE must be coarse or detailed`
- 处理方法：
  - 补齐本地 active config；
  - `HEURISTIC_BOUND_MODE` 只能为 `coarse` 或 `detailed`，只控制 C500 heuristic bound 粒度，不影响 Roofline bound。
  - `OP_EXEC_CMD` 是受保护配置，错误时先报告。

## TR-PF-009

- 阶段：preflight/config
- 触发：`OP_EXEC_CMD` shell argv 解析失败
- 匹配信息：
  - `OP_EXEC_CMD must be valid shell-style argv text`
- 处理方法：
  - 修正 quoting；
  - 采集脚本会按 shell quoting 拆成 argv，不支持依赖管道、重定向、shell 展开或内联环境变量赋值。

## TR-PF-010

- 阶段：preflight/config
- 触发：CycleTrace PEU 参数非法
- 匹配信息：
  - `CYCLE_TRACE_TARGET_PEU must not be empty`
  - `CYCLE_TRACE_TARGET_PEU must be an integer from 1 to 15`
  - `CYCLE_TRACE_TARGET_PEU must be in [1, 15]; 1 (0x1) captures PEU 0`
  - `--target-peu must be an integer from 1 to 15`
  - `--target-peu must be in [1, 15]; 1 (0x1) captures PEU 0`
- 处理方法：
  - 设置为 `1` 到 `15` 的整数；
  - 该值是 PEU bitmap，`1=0001b` 采 PEU0，`15=1111b` 采 4 条 PEU。
  - 工具的 `0` 表示 disable target PEU，但标准报告需要有效硬件事件，因此不允许。

## TR-PF-011

- 阶段：preflight/config
- 触发：CycleTrace DPG buffer 页数非法
- 匹配信息：
  - `CYCLE_TRACE_DPG_PAGE_NUM must not be empty`
  - `CYCLE_TRACE_DPG_PAGE_NUM must be a positive integer`
  - `CYCLE_TRACE_DPG_PAGE_NUM must be greater than 0`
  - `--dpg-page-num must be a positive integer`
  - `--dpg-page-num must be greater than 0`
- 处理方法：
  - 设置为正整数；
  - 工具默认 `32`，即 64M。
  - trace 内容过多时可在用户授权后适当调大。

## TR-PF-012

- 阶段：preflight/config
- 触发：CycleTrace AP 参数非法
- 匹配信息：
  - `CYCLE_TRACE_TARGET_AP must not be empty`
  - `CYCLE_TRACE_TARGET_AP must be an integer from 0 to 15`
  - `CYCLE_TRACE_TARGET_AP must be in [0, 15]`
  - `--target-ap must be an integer from 0 to 15`
  - `--target-ap must be in [0, 15]`
- 处理方法：
  - 设置为 `0` 到 `15` 的整数；
  - 默认 `0`。

## TR-PF-013

- 阶段：preflight/config
- 触发：CycleTrace DPC 参数非法
- 匹配信息：
  - `CYCLE_TRACE_DPC_ID must not be empty`
  - `CYCLE_TRACE_DPC_ID must be a comma-separated list of integers from 0 to 7 with no spaces`
  - `DPC id must be a comma-separated list of integers from 0 to 7 with no spaces`
  - `--dpc-id must be a comma-separated list...`
- 处理方法：
  - 使用 `0` 或 `2,3` 这类无空格列表；
  - pipeline 只分析第一个 DPC，其他生成文件只归档。

## TR-PF-014

- 阶段：preflight/config
- 触发：工具路径配置为空
- 匹配信息：
  - `MC_TRACER_BIN must not be empty`
  - `CYCLE_TRACE_BIN must not be empty`
  - `MC_PROFILER_BIN must not be empty`
  - `MC_PROFILER_BIN must not be empty when REQUIRE_MCPROFILER=true`
- 处理方法：
  - 在本地 active config 中填入目标环境内的工具路径；
  - 不同模式下路径检查位置不同。

## TR-PF-015

- 阶段：preflight/config
- 触发：三工具采集超时参数非法
- 匹配信息：
  - `COLLECT_TIMEOUT_SECONDS must not be empty`
  - `COLLECT_TIMEOUT_SECONDS must be a positive integer number of seconds`
- 处理方法：
  - 设置为正整数秒数；
  - 默认 `600`，只限制 mcTracer、cycle-trace-ng 和 mcProfiler 的共享采集耗时。

## TR-PF-016

- 阶段：preflight/config
- 触发：布尔开关非法
- 匹配信息：
  - `REQUIRE_MCPROFILER must be a boolean value: true/false, yes/no, or 1/0`
  - `ENABLE_SOURCE_LATENCY must be a boolean value: true/false, yes/no, or 1/0`
- 处理方法：
  - 设置为布尔文本；
  - `REQUIRE_MCPROFILER=false` 表示 mcProfiler 尽力采集、失败降级为 `N/A`，`true` 表示 mcProfiler 两个 JSON 必须完整存在且可解析；
  - `ENABLE_SOURCE_LATENCY=true` 表示本次 CycleTrace 采集执行 `MACA_LINEINFO_CYCLE_TRACE_EXEC_CMD`，为空时回退 `MACA_LINEINFO_BINARY`，追加运行期 `--kernel-probe`，并在常规报告后强制运行 source-latency。

## TR-PF-017

- 阶段：preflight/config
- 触发：mcProfiler 本地服务端口非法
- 匹配信息：
  - `PROFILER_PORT must be an integer TCP port from 1 to 65535`
- 处理方法：
  - 设置为 `1..65535` 的整数；
  - 为空时默认使用 `50123`。
  - 若目标环境日志显示 `Address already in use` 或 `127.0.0.1:50123` 绑定失败，把本地 active config 的 `PROFILER_PORT` 改成未占用端口后重跑 `collect-run`。

## TR-PF-018

- 阶段：preflight/config
- 触发：CycleTrace kernel filter 配置非法
- 匹配信息：
  - `CYCLE_TRACE_SAMPLE_MODE must be A, B, C, or D when set`
  - `CYCLE_TRACE_KERNEL_NAME requires CYCLE_TRACE_SAMPLE_MODE=C when sample mode is set`
  - `CYCLE_TRACE_KERNEL_REPEAT must be an integer when set`
  - `CYCLE_TRACE_KERNEL_REPEAT requires CYCLE_TRACE_KERNEL_NAME`
  - `CYCLE_TRACE_TOOLS_JSON is not writable in <mode> target: <path>`
- 处理方法：
  - `A=all kernels`，`B=blit kernels only`，`C=custom cycleTrace.kernels`，`D=DIDT kernel only`。
  - 若设置 `CYCLE_TRACE_KERNEL_NAME`，`CYCLE_TRACE_SAMPLE_MODE` 必须为空或为 `C`；
  - `CYCLE_TRACE_KERNEL_REPEAT` 必须为整数且不能脱离 kernel name 单独设置。
  - `CYCLE_TRACE_TOOLS_JSON` 默认 `/opt/maca/etc/tools.json`，启用 kernel filter 时目标环境必须可写，修正 active config 或目标权限后重跑 `validate` / `collect-run`。

## TR-PF-019

- 阶段：preflight/config
- 触发：source-latency 严格模式缺少输入
- 匹配信息：
  - `MACA_SOURCE_FILE must not be empty when ENABLE_SOURCE_LATENCY=true`
  - `MACA_LINEINFO_BINARY must not be empty when ENABLE_SOURCE_LATENCY=true`
- 处理方法：
  - 填写目标环境内存在的源码文件，以及存在、按 `-lineinfo --kernel-probe 0` 编译的最终可执行文件；
  - `MACA_LINEINFO_CYCLE_TRACE_EXEC_CMD` 可选，为空时回退执行 `MACA_LINEINFO_BINARY`。

## TR-PF-020

- 阶段：preflight/config
- 触发：lineinfo CycleTrace 执行命令格式非法
- 匹配信息：
  - `MACA_LINEINFO_CYCLE_TRACE_EXEC_CMD must be valid shell-style argv text: ...`
  - `MACA_LINEINFO_CYCLE_TRACE_EXEC_CMD did not produce an executable`
- 处理方法：
  - 仅在 `ENABLE_SOURCE_LATENCY=true` 时校验。
  - 按 shell quoting 规则填写；
  - 该字段可包含运行参数。
  - 若为空，严格 source-latency 路径会回退执行 `MACA_LINEINFO_BINARY`。

## TR-PF-021

- 阶段：preflight/config
- 触发：source-latency objdump 配置非法
- 匹配信息：
  - `MACA_OBJDUMP_ARGS must be valid shell-style argv text: ...`
  - `MACA_OBJDUMP_BIN is not executable in <mode> target: <path>`
  - `MACA_LLVM_OBJDUMP_BIN is not executable in <mode> target: <path>`
- 处理方法：
  - `MACA_OBJDUMP_ARGS` 只要非空就必须是 shell-style argv 文本；
  - `MACA_OBJDUMP_BIN`、`MACA_LLVM_OBJDUMP_BIN` 或派生 `llvm-objdump` 仅在 `ENABLE_SOURCE_LATENCY=true` 时检查可执行性。
  - 它们只影响 trace-report 后处理，不能修复 kernel-probe plugin 内部 `mxobjdump --list-elf` 路径问题，后者按 `TR-COL-015` 处理。

## TR-PF-022

- 阶段：preflight/config
- 触发：`--config` 与 `collect-run --set` 同时使用
- 匹配信息：
  - `--config and collect-run --set are mutually exclusive`
- 处理方法：
  - 配置文件模式只传 `--config <path>`；直接参数模式只传 `collect-run --set KEY=VALUE`。
  - 两种输入模式不得混用。

## TR-PF-023

- 阶段：preflight/config
- 触发：YAML 中包含不受支持的配置 key
- 匹配信息：
  - `<path>:<line>: unknown config key: <key>`
- 处理方法：
  - 更正 key 拼写或删除不受支持的配置项；配置只接受当前 flat env contract 中定义的字段。

## TR-SSH-001

- 阶段：ssh
- 触发：用户只设置了 `SSHPASS`，没有设置 `TRACE_REPORT_SSH_PASSWORD`
- 匹配信息：
  - `SSHPASS is ignored by trace-report; set TRACE_REPORT_SSH_PASSWORD for password SSH authentication`
- 处理方法：
  - 改为在本地设置 `TRACE_REPORT_SSH_PASSWORD`。
  - `trace_report_env.py` 会自动把它映射给 SSH/rsync 子进程的 `SSHPASS`；
  - 不要把密码写入 YAML、命令行、报告或产物。

## TR-SSH-002

- 阶段：ssh
- 触发：设置了 `TRACE_REPORT_SSH_PASSWORD`，但本机没有 `sshpass`
- 匹配信息：
  - `sshpass is required when TRACE_REPORT_SSH_PASSWORD is set`
- 处理方法：
  - 安装 `sshpass` 后重跑 `validate`；
  - 如果不需要密码认证，清除 `TRACE_REPORT_SSH_PASSWORD` 以恢复系统默认 SSH 认证。

## TR-SSH-003

- 阶段：ssh
- 触发：SSH 主机不可达、认证失败或密码错误
- 匹配信息：
  - `SSH connection failed for <user>@<host>: ...; if using password authentication, set TRACE_REPORT_SSH_PASSWORD`
- 处理方法：
  - 先修正网络、host、用户、key/agent 或本地 `TRACE_REPORT_SSH_PASSWORD`。
  - Preflight 会在后续 docker/workdir/tool 检查前停止，避免噪声错误。

## TR-TGT-001

- 阶段：target-env
- 触发：目标执行目录不存在
- 匹配信息：
  - `REMOTE_WORKDIR does not exist in <mode> target: <path>`
- 处理方法：
  - 按模式在正确位置创建或修正目录：local 本机，docker 容器内，ssh 远程主机，ssh+docker 远程容器内。
  - 不要用 `LOCAL_WORKDIR` 替代 docker 内路径。

## TR-TGT-002

- 阶段：target-env
- 触发：docker 容器配置缺失、不存在或不可 inspect
- 匹配信息：
  - `CONTAINER_NAME must not be empty in docker mode`
  - `container does not exist or is not inspectable: <name>`
- 处理方法：
  - 确认 `CONTAINER_NAME` 正确；
  - ssh+docker 模式下在远程主机检查 `docker inspect <name>`。
  - `CONTAINER_NAME` 是受保护配置，先报告确认。

## TR-TGT-003

- 阶段：target-env
- 触发：目标环境工具不可执行或 CycleTrace tools.json 不可用
- 匹配信息：
  - `<TOOL_KEY> is not executable in <mode> target: <path>`
  - `mcTracer not executable: <path>`
  - `cycle-trace-ng not executable: <path>`
  - `mcProfiler not executable: <path>`
  - `CycleTrace tools.json not found: <path>`
  - `CycleTrace tools.json is not writable: <path>`
- 处理方法：
  - 修正目标环境文件权限或在本地 active config 中填写正确工具路径；
  - 可用 `find /opt -name ... -type f` 辅助定位。
  - 若是 `tools.json` 报错，确认 `CYCLE_TRACE_TOOLS_JSON` 指向目标环境存在且可写的配置文件。

## TR-TGT-004

- 阶段：target-env
- 触发：目标命令在 `REMOTE_WORKDIR` 下运行失败
- 匹配信息：
  - `OP_EXEC_CMD failed in REMOTE_WORKDIR (<path>): ...`
- 处理方法：
  - 在目标环境内直接运行 `OP_EXEC_CMD` 做最小复现，报告失败命令、目录、输入文件线索和错误信息；
  - 除非用户明确要求，不修改 `OP_EXEC_CMD` 或 `REMOTE_WORKDIR`。

## TR-TGT-005

- 阶段：target-env
- 触发：目标环境没有 `mx-smi`
- 匹配信息：
  - `mx-smi is not available in target environment; cannot validate MACA_VISIBLE_DEVICES`
- 处理方法：
  - 修复目标驱动/工具环境；
  - 不要跳过 device 校验继续采集。

## TR-TGT-006

- 阶段：target-env
- 触发：`MACA_VISIBLE_DEVICES` 不在目标 `mx-smi` 输出中
- 匹配信息：
  - `MACA_VISIBLE_DEVICES=<id> was not found in mx-smi output`
- 处理方法：
  - 修改本地 active config 的 `MACA_VISIBLE_DEVICES` 为目标环境可见设备。
  - 若只暴露一个设备，CycleTrace `--target-device` 通常仍使用逻辑 0，不要把物理 ID 直接当成 target device。

## TR-TGT-007

- 阶段：target-env
- 触发：source-latency 输入文件不存在
- 匹配信息：
  - `MACA_SOURCE_FILE does not exist in REMOTE_WORKDIR (<path>): <file>`
  - `MACA_LINEINFO_BINARY does not exist in REMOTE_WORKDIR (<path>): <binary>`
- 处理方法：
  - 仅在 `ENABLE_SOURCE_LATENCY=true` 时触发。
  - 确认源码文件和 lineinfo binary 在目标环境中可见；
  - `MACA_LINEINFO_BINARY` 是单个 binary 路径，不含参数。

## TR-TGT-008

- 阶段：target-env
- 触发：lineinfo CycleTrace 执行命令运行失败
- 匹配信息：
  - `MACA_LINEINFO_CYCLE_TRACE_EXEC_CMD failed to execute in REMOTE_WORKDIR (<path>): ...`
- 处理方法：
  - 仅在 `ENABLE_SOURCE_LATENCY=true` 时触发。
  - 在目标环境中直接运行该命令做最小复现；
  - 若该字段为空，则检查 `MACA_LINEINFO_BINARY` 是否可直接运行。
  - 该命令应与 `OP_EXEC_CMD` 使用等价 workload，并执行按 `-lineinfo --kernel-probe 0` 编译的 lineinfo binary。

## TR-TGT-009

- 阶段：target-env
- 触发：`MACA_VISIBLE_DEVICES=-1` 自动选择 GPU 失败
- 匹配信息：
  - `automatic GPU selection failed during <stage>: ...`
  - `automatic GPU selection could not parse Attached GPUs from mx-smi`
  - `automatic GPU selection timed out after <N> seconds; all GPUs are busy`
  - `MACA_MEM_BUSY_PCT must be a number`
- 处理方法：
  - 确认目标环境中 `mx-smi`、`mx-smi --show-process` 和 `mx-smi --show-memory` 都可执行；
  - 若等待超时，增加 `AUTO_SELECT_DEVICE_TIMEOUT_SECONDS`、降低 `MACA_MEM_BUSY_PCT` 阈值，或显式设置 `MACA_VISIBLE_DEVICES=<id>` 固定采集设备；
  - 自动选择只基于实时 `mx-smi` 探测，不提供跨进程 GPU 锁。

## TR-SYNC-001

- 阶段：sync
- 触发：同步脚本目标等于当前 skill 脚本目录
- 匹配信息：
  - `refuse to overwrite current skill scripts: <path>`
- 处理方法：
  - 确认 `REMOTE_WORKDIR` 指向被 profile 的优化 repo，而不是本 skill 仓库。
  - 只同步到 `[REMOTE_WORKDIR]/.trace-report/scripts/`。

## TR-SYNC-002

- 阶段：sync
- 触发：同步后入口脚本缺失
- 匹配信息：
  - `synced entrypoint is missing: .../collect_trace_profile.sh`
- 处理方法：
  - 重新执行 `python3 scripts/trace_report_env.py --config "$ACTIVE_CONFIG" sync-scripts`；
  - 如果仍缺失，检查本 skill 仓库 `scripts/` 是否完整。

## TR-SYNC-003

- 阶段：sync
- 触发：目标命令、`docker cp`、`rsync` 或远端 staging 命令失败
- 匹配信息：
  - `command failed (<code>): <command>: <detail>`
  - `docker artifact sync failed`
- 处理方法：
  - 先看失败命令属于 sync-scripts 还是 sync-artifacts；
  - 检查 SSH 认证、容器名、目标路径权限、`docker cp` 可用性和远端 `/tmp` staging 权限。
  - ssh+docker staging 是临时目录，失败路径也应清理。

## TR-WF-001

- 阶段：collect-run workflow
- 触发：`collect-run` 在 active config 初始化、run 目录解析或 Preflight 阶段失败
- 匹配信息：
  - `collect-run failed at validate: ...`
- 处理方法：
  - 先查看同一段 stderr 中更具体的 `TR-PF-*`、`TR-SSH-*`、`TR-TGT-*` 或 `TR-RUN-*` 报错；
  - 修正 active config、SSH、目标环境或 run 目录名后重新执行 `collect-run`。

## TR-WF-002

- 阶段：collect-run workflow
- 触发：`collect-run` 在同步脚本阶段失败
- 匹配信息：
  - `collect-run failed at sync-scripts: ...`
- 处理方法：
  - 先按同一段 stderr 中的 `TR-SYNC-*` 或目标命令错误处理；
  - 确认 `[REMOTE_WORKDIR]/.trace-report/scripts/` 可写、容器/SSH/rsync/docker cp 可用，修复后重新执行 `collect-run`。

## TR-WF-003

- 阶段：collect-run workflow
- 触发：`collect-run` 在目标环境创建 run 目录阶段失败
- 匹配信息：
  - `collect-run failed at create-run-dir: ...`
- 处理方法：
  - 检查目标环境中的 `[REMOTE_WORKDIR]/profile-artifacts/` 权限、磁盘空间和容器/远端工作目录；
  - 不要改用本地路径绕过目标环境。

## TR-WF-004

- 阶段：collect-run workflow
- 触发：`collect-run` 在采集阶段失败
- 匹配信息：
  - `collect-run failed at collect: exit=<code>; last error: ...`
- 处理方法：
  - 先查命令摘要中的 `cause_code` / `last_error_code`；必要时读取 `<run-dir>/logs/collect-run.log` 或对应阶段日志 tail，定位 `TR-COL-*`、`TR-ART-*` 或 `TR-ANA-*` 报错。
  - 底层脚本会终止本次失败工具启动的进程组，删除当前失败阶段的本次 run_dir 半成品，并清理 workdir 根目录临时产物；
  - 已成功阶段的 run_dir 标准化产物保留。
  - 是否重新执行 `collect-run` 由 `SKILL.md` 的 agent retry 策略决定；
  - 未获授权不要自动修改受保护配置。
  - 该进程清理不覆盖历史或用户手工启动的进程。

## TR-WF-005

- 阶段：collect-run workflow
- 触发：`collect-run` 在强制 source-latency 阶段失败
- 匹配信息：
  - `collect-run failed at source-latency: exit=<code>; last error: ...`
  - `collect-run failed at source-latency: missing/invalid required output: ...`
- 处理方法：
  - 先看摘要 `log:` 指向的 `<run-dir>/logs/source-latency.log` tail 中的 source-latency 原始错误，再检查 `MACA_SOURCE_FILE`、`MACA_LINEINFO_BINARY` 是否真实来自带 `-lineinfo --kernel-probe 0` 的编译目标、`MACA_OBJDUMP_BIN`、`MACA_LLVM_OBJDUMP_BIN` 和 `analysis/source_latency_<tag>.md/.csv/.json`；
  - 不要用普通 `OP_EXEC_CMD` binary 改名代替 lineinfo binary。
  - 常见原始错误包括 `cannot find CycleTrace JSON in <run-dir>: <name>`、`cannot find _kernelprobe_replication_0.link.o in <run-dir>`、`mxobjdump --extract-elf failed with code <N>; output written to ...`、`mxobjdump --extract-elf did not produce lineinfo_binary.*.out in ...`、`llvm-objdump --line-numbers failed with code <N>; output written to ...`、`no lineinfo input was provided or found; provide --binary, --device-elf, --kernelprobe-link-obj, --lineinfo-objdump, or run source-latency in a kernel-probe run directory containing _kernelprobe_replication_0.link.o`、`entry label ... not found in lineinfo objdump: ...`。
  - source-latency 底层 `RuntimeError` / `ValueError` 不一定都有独立 `[TR-*]` 映射；若摘要只有 `TR-WF-005`，以 `source-latency.log` tail 中的原始错误文本为准，不要全文读取本文。
  - 后处理机制见 `reference/10-source-latency.md`；
  - kernel-probe plugin 问题仍按 `TR-COL-014` / `TR-COL-015`。

## TR-WF-006

- 阶段：collect-run workflow
- 触发：`collect-run` 在 source-latency 后刷新基础报告失败
- 匹配信息：
  - `collect-run failed at refresh-report: exit=<code>; last error: ...`
- 处理方法：
  - source-latency 已成功生成后，workflow 会以严格 summary 模式重新执行 `trace_profile_pipeline.py analyze`，让基础 `REPORT_<tag>.md` 吸收 `analysis/source_latency_<tag>.json`。
  - 该阶段失败时，先看摘要 `log:` 指向的 `<run-dir>/logs/refresh-report.log` tail，定位 `TR-ANA-*`、`TR-ART-*` 或 Python 原始错误；
  - 检查 `<run-dir>/artifacts/`、`analysis/source_latency_<tag>.json` 是否存在、可解析且 tag 一致，同时检查 mcProfiler strict 配置。
  - 修复后可重新执行同一个 `collect-run`，或在目标环境中用 `trace_report_env.py run -- env TRACE_REPORT_REQUIRE_SOURCE_LATENCY_SUMMARY=1 python3 .trace-report/scripts/trace_profile_pipeline.py analyze ...` 刷新基础报告。

## TR-WF-007

- 阶段：collect-run workflow
- 触发：`collect-run` 在基础报告检查阶段失败
- 匹配信息：
  - `collect-run failed at check-report: exit=<code>; last error: ...`
- 处理方法：
  - `collect-run` 会在采集和基础报告完成后运行不要求 LLM 第 9 章的基础 `check-report`。
  - 先看命令摘要中的 `last_error_code` / `last_stderr`；必要时读取 `<run-dir>/logs/check-report.log`、`append-diagnosis.log` 或 usecase 对应日志 tail，定位 `TR-REP-*` 或 `TR-LLM-*`；
  - 若是 source-latency 吸收失败，检查 `analysis/source_latency_<tag>.json`、`metrics_all_<tag>.json` 和 `REPORT_<tag>.md` 是否一致。
  - 若基础报告已完成但缺少第 9 章，不要重采；按 `reference/quick-finalize.md` 生成诊断 Markdown 文件，执行 `finalize-run --diagnosis-file <diagnosis.md>`，成功后再按输出的 `required_next_command` 执行 `sync-artifacts`。

## TR-WF-008

- 阶段：collect-run workflow
- 触发：`collect-run` 在自动 usecase 拆分阶段失败
- 匹配信息：
  - `collect-run failed at split-usecases: exit=<code>; last error: ...`
- 处理方法：
  - 主报告已生成并通过基础检查，但 `mcProfiler scope=per-kernel-multiple-occurrences` 触发的自动 `split-usecases` 失败。
  - 先查看 `<run-dir>/logs/split-usecases.log`，再按 `TR-USC-*` 或原始 Python 错误处理；
  - 修复后可手工重新执行 `trace_profile_pipeline.py split-usecases` 或重跑同一 run 的 `collect-run`。

## TR-WF-009

- 阶段：collect-run workflow
- 触发：`collect-run` 在 generated usecase source-latency 阶段失败
- 匹配信息：
  - `collect-run failed at usecase-source-latency: exit=<code>; last error: ...`
- 处理方法：
  - 仅在 `ENABLE_SOURCE_LATENCY=true` 且 `split-usecases` 生成 `generated_reports` 后触发。
  - 先查看 `<run-dir>/logs/source-latency-usecase_XXX.log`，再检查对应 `usecases/usecase_XXX/artifacts/c-trace_output_dpc_<N>.json`、`MACA_SOURCE_FILE`、`MACA_LINEINFO_BINARY`、objdump 配置和 `usecases/usecase_XXX/analysis/source_latency_*`。
  - 不要把父级 `analysis/source_latency_<tag>.*` 当作单 usecase 源码热点替代。

## TR-WF-010

- 阶段：collect-run workflow
- 触发：`collect-run` 在 generated usecase 报告刷新阶段失败
- 匹配信息：
  - `collect-run failed at usecase-refresh-report: exit=<code>; last error: ...`
- 处理方法：
  - per-usecase source-latency 已生成后，workflow 会以严格 summary 模式刷新对应 `usecases/usecase_XXX/REPORT_*.md`。
  - 先查看 `<run-dir>/logs/refresh-report-usecase_XXX.log`，检查该 usecase 的 `analysis/source_latency_*.json`、`analysis/metrics_all_*.json`、`artifacts/collection_manifest.json` 是否存在且 tag/scope 一致；
  - 修复后可重跑同一 run 的 `collect-run` 或手工对该 usecase 运行 `trace_profile_pipeline.py analyze`。

## TR-RUN-001

- 阶段：env runner
- 触发：`trace_report_env.py run` 没有目标命令
- 匹配信息：
  - `run requires a command after --`
- 处理方法：
  - 使用 `python3 scripts/trace_report_env.py --config "$ACTIVE_CONFIG" run -- <target command>`。

## TR-RUN-002

- 阶段：env runner
- 触发：run 目录名不符合规范
- 匹配信息：
  - `run directory basename must match <kernel>_v<N>_<tag>: <name>`
- 处理方法：
  - 使用 `profile-artifacts/<kernel>_v<N>_<tag>/` 命名；
  - 标准采集交给 `collect-run` 自动派生 `PROFILE_*`，不要手写 tag。

## TR-RUN-003

- 阶段：env runner
- 触发：`sync-artifacts` 指向非法目录
- 匹配信息：
  - `sync-artifacts --run-dir must be under profile-artifacts/`
- 处理方法：
  - 只同步本次 run 的相对目录，如 `profile-artifacts/gemm_v0_baseline`；
  - 不能传绝对路径、`..` 或非 `profile-artifacts/` 路径。

## TR-COL-001

- 阶段：collect
- 触发：采集脚本必需参数缺失
- 匹配信息：
  - `<option> requires an argument`
  - `unknown argument: <arg>`
  - `--run-dir is required`
  - `--tag is required`
  - `--exec-cmd is required`
  - `--device is required`
  - `--target-peu is required`
  - `--dpg-page-num is required`
  - `--target-ap is required`
  - `--dpc-id is required`
  - `--heuristic-bound-mode is required`
  - `--collect-timeout-seconds is required`
  - `--require-mcprofiler is required`
  - `--mctracer-bin is required`
  - `--cycle-trace-bin is required`
  - `--mcprofiler-bin is required when --require-mcprofiler=true`
  - `MACA_LINEINFO_BINARY is required when --enable-source-latency=true`
- 处理方法：
  - 标准采集不要手工调用底层脚本；
  - 改用 `reference/02-collection.md` 的 `collect-run`，由 active config 和 run 目录自动传齐参数。
  - 若启用 source-latency，需在 active config 中设置 `MACA_LINEINFO_BINARY`。

## TR-COL-002

- 阶段：collect
- 触发：采集脚本参数值非法
- 匹配信息：
  - `--target-peu must be ...`
  - `--dpg-page-num must be ...`
  - `--target-ap must be ...`
  - `--dpc-id must be ...`
  - `--cycle-sample-mode must be A, B, C, or D`
  - `--cycle-kernel-repeat must be an integer`
  - `--cycle-kernel-repeat requires --cycle-kernel-name`
  - `--cycle-kernel-name requires --cycle-sample-mode C`
  - `--heuristic-bound-mode must be coarse or detailed`
  - `--collect-timeout-seconds must be ...`
  - `--require-mcprofiler must be a boolean value: true/false, yes/no, or 1/0`
  - `--enable-source-latency must be a boolean value: true/false, yes/no, or 1/0`
  - `--profiler-port must be ...`
- 处理方法：
  - 与 Preflight 参数规则保持一致，修正本地 active config 后重新执行 `collect-run`。
  - sample mode 含义：`A=all kernels`、`B=blit kernels only`、`C=custom cycleTrace.kernels`、`D=DIDT kernel only`。

## TR-COL-003

- 阶段：collect
- 触发：执行命令无法拆成可执行 argv
- 匹配信息：
  - `failed to parse --exec-cmd`
  - `--exec-cmd did not produce an executable`
  - `failed to parse MACA_LINEINFO_CYCLE_TRACE_EXEC_CMD`
  - `MACA_LINEINFO_CYCLE_TRACE_EXEC_CMD did not produce an executable`
- 处理方法：
  - 修正对应命令的 shell quoting；
  - 不要依赖 shell 特性。
  - `OP_EXEC_CMD` 受保护，只能在用户明确要求时修改；
  - `MACA_LINEINFO_CYCLE_TRACE_EXEC_CMD` 可包含 lineinfo 采集参数。

## TR-COL-004

- 阶段：collect
- 触发：pipeline 入口脚本未同步、目标还是旧版本或 CycleTrace tools.json 临时更新失败
- 匹配信息：
  - `trace_profile_pipeline.py not found next to this script`
  - `failed to create tools.json backup`
  - `failed to back up <path>`
  - `failed to update cycleTrace.kernels in tools.json`
  - `failed to verify CycleTrace single-kernel filter in tools.json`
- 处理方法：
  - 重新运行 `sync-scripts`，确保目标 `[REMOTE_WORKDIR]/.trace-report/scripts/` 是当前 skill 的完整 `scripts/`。
  - 若是 `tools.json` 更新失败，检查 `CYCLE_TRACE_TOOLS_JSON` 路径、权限、磁盘空间和 JSON 结构；
  - 单算子采集还需确认文件中存在 `cycleTrace.kernels`，且 `CYCLE_TRACE_KERNEL_NAME` 是真实 kernel name。
  - 脚本退出时会尝试恢复备份。

## TR-COL-005

- 阶段：collect
- 触发：mcTracer 没有生成新 JSON 或 JSON 为空
- 匹配信息：
  - `[Step 1/4] mcTracer output not found: tracer_out_*/tracer_out-*.json`
  - `missing/invalid required output: mcTracer JSON`
- 处理方法：
  - 确认 `OP_EXEC_CMD` 会触发目标 kernel，mcTracer 路径可执行，目标目录可写；
  - 手工采集时避免选到旧 `tracer_out_*`。

## TR-COL-006

- 阶段：collect
- 触发：CycleTrace primary DPC JSON 缺失、为空、无硬件事件或 JSON 无效。
- 匹配信息：`[Step 2/4] CycleTrace output not found: c-trace_output_dpc_<N>.json`、`missing/invalid required output: CycleTrace JSON`、`CycleTrace JSON has no traceEvents`、`CycleTrace JSON has no hardware instruction events`、`CycleTrace JSON is invalid: <path>`。
- 不手工拼接旧数据、跨 run 或未校验部分产物；底层脚本可复用同一 run 内已校验阶段产物。恢复遵守[主入口的计数与停止条件](../SKILL.md#恢复与停止条件)。

### small-workgroup 恢复

1. 重试前从本次有效 mcTracer 的 `args.name`、`args.grid`、`args.block` 推导并交叉核对 `workgroup_count = grid.x * grid.y * grid.z`。源码 launch 信息可补充核对，但不是前置条件。
2. 目标 kernel 唯一且 `workgroup_count < 104` 时，优先进行一次 trace-only 恢复 campaign。对 mcoplib，AP count 按整卡 `104`，不得以 `CYCLE_TRACE_TARGET_AP` 或 DPC 参数范围代替。
3. 首个总 `--iters` 取 `next_power_of_two(ceil(104/workgroup_count))` 并重建 trace-only target；仍无事件时总次数按 2 倍增长，最后一次可直接取剩余 deadline 能容纳的最大值。任何候选产生有效硬件事件 JSON 即停止。
4. 全部候选共享一个由 `COLLECT_TIMEOUT_SECONDS` 导出的 absolute collection deadline，不得逐次重置。起点放不进剩余预算时记录 `budget insufficient`，不以任意小次数替代；deadline 耗尽即按主入口停止。
5. 整个 campaign 只计一次恢复尝试。不得扫描 AP、修改 workload shape、case、设备或 validation 命令；报告注明多 launch scope，聚合 instruction count / span 不能解释为单次 launch latency。

没有可用 grid、目标 kernel 不唯一、`workgroup_count >= 104`，或 campaign 失败但仍有预算时，再排查 `OP_EXEC_CMD`、`MACA_VISIBLE_DEVICES`、kernel filter name/repeat、DPC、PEU、sample mode 与工具配置（包括 `--target-peu`、`--dpg-page-num`、`--target-ap`、`--dpc-id`）。仅在授权范围调整非保护采集参数并重跑 Preflight，不能无变化重试；预算耗尽不另开参数扫描。

## TR-COL-007

- 阶段：collect
- 触发：CycleTrace 命令不存在或路径错误
- 匹配信息：
  - `cycle-trace-ng: command not found`
- 处理方法：
  - 确认 `CYCLE_TRACE_BIN` 指向目标环境内可执行文件；
  - 可在容器或远端内 `find /opt -name "cycle-trace-ng" -type f`。

## TR-COL-008

- 阶段：collect
- 触发：CycleTrace 开启 JSON 后不输出文件
- 匹配信息：
  - `--format json` 不输出任何文件
  - `cycle-trace-ng --format json did not produce a primary CycleTrace JSON file`
- 处理方法：
  - 确认采集命令设置了 `ENABLE_DPG=1 ENABLE_DPG_DUMP=1`，并使用 `ISU_FASTMODEL=0`。
  - 标准脚本已设置这些变量；
  - 若仍失败，按 TR-COL-006 做最小复现。

## TR-COL-009

- 阶段：collect
- 触发：`cycle-trace-ng` 在 DPG 后崩溃
- 匹配信息：
  - `*** SIGSEGV DETECT! ***`，或日志先出现 `Dispatch ttrace dump start packet`、`dpc0 copy tokens has done` 后崩溃
  - `cycle-trace-ng failed after DPG trace activity; inspect SIGSEGV or DPG dump logs`
- 处理方法：
  - 优先按采集参数排查：直接运行 `OP_EXEC_CMD`，确认 device 可见，尝试 `--target-peu` 的 `1/3/7/15`，必要时增大 `--dpg-page-num`。
  - 不要手工用 mcTracer 部分产物继续分析；
  - 重新执行同一 run 的 `collect-run` 时可由底层脚本复用已成功阶段。

## TR-COL-010

- 阶段：collect
- 触发：mcProfiler 命令或输出目录异常
- 匹配信息：
  - `mcProfiler command failed with exit=...`
  - `perf_exec` 命令找不到
  - `[Step 3/4] mcProfiler output directory not found: <dir>`
- 处理方法：
  - `REQUIRE_MCPROFILER=true` 时该问题阻断采集；
  - 确认 `MC_PROFILER_BIN` 指向 mcProfiler 可执行文件，目标目录可写。
  - 若日志显示 `Address already in use`、`127.0.0.1:50123` 或 Flask 端口绑定失败，修改 `PROFILER_PORT` 到未占用端口后重试。
  - 若是大规模 kernel 导致采集过久，增大 `COLLECT_TIMEOUT_SECONDS` 后重试；
  - 可在目标环境 `find /opt -name "mcProfiler" -type f`。

## TR-COL-011

- 阶段：collect
- 触发：strict 模式下 mcProfiler 必需 JSON 缺失、为空、不可解析或 per-kernel 归档失败
- 匹配信息：
  - `mcProfiler artifacts are required by REQUIRE_MCPROFILER=true...`
  - `mcProfiler artifacts are required by REQUIRE_MCPROFILER=true but dumped JSON is missing`
  - `mcProfiler artifacts are required by REQUIRE_MCPROFILER=true but report text JSON is missing`
  - `mcProfiler per-kernel artifacts are required by REQUIRE_MCPROFILER=true but could not be normalized for CYCLE_TRACE_KERNEL_NAME=...`
  - `missing/invalid required output: mcProfiler dumped JSON`
  - `missing/invalid required output: mcProfiler report text JSON`
  - `missing/invalid required output: mcprofiler_per_kernel manifest is incomplete for multi-occurrence profiler scope`
- 处理方法：
  - 只有 `REQUIRE_MCPROFILER=true` 时该问题阻断采集。
  - 未设置 `CYCLE_TRACE_KERNEL_NAME` 时确认 `mcprofiler_report_dumped.json` 和 `mcprofiler_report.txt.json` 均生成且可解析。
  - 设置 `CYCLE_TRACE_KERNEL_NAME` 时脚本会使用 `--kernelnames/--per-kernel`，并只接受 `<idx><kernel>_dumped_result.json` / `<idx><kernel>.txt.json` 这类目标前缀文件，通用 `report_*` 不会回退为目标 kernel 数据。
  - 若只有一个完整目标 occurrence，脚本会归档为标准 `mcprofiler_report_*`；
  - 若有多个完整目标 occurrence，采集不是失败，主报告会显示 `mcProfiler scope=per-kernel-multiple-occurrences`，标准 `mcprofiler_report_*` 不生成，完整 occurrence 保留在 `artifacts/mcprofiler_per_kernel/` 供 `split-usecases` 拆分。
  - 只有 occurrence 缺文件、数量不完整或 manifest 不一致时才按本错误处理。
  - 若日志显示端口绑定失败，修改 `PROFILER_PORT`；
  - 若 mcProfiler 采集过长，增大 `COLLECT_TIMEOUT_SECONDS` 后重试。
  - `REQUIRE_MCPROFILER=false` 时同类问题只作为 warning，报告中 profiler 指标为 `N/A`。

## TR-COL-012

- 阶段：collect
- 触发：归档或最终报告产物缺失，或已有产物 scope 与当前 active config 不匹配
- 匹配信息：
  - `required trace tool outputs are incomplete before pipeline`
  - `missing/invalid required output: archived ...`
  - `digest Markdown`
  - `full metrics JSON`
  - `key metrics JSON`
  - `final report`
- 处理方法：
  - 查看 `artifacts/collection_manifest.json` 的 `missing_required` / `invalid_required` 和最后一次失败步骤；
  - 脚本会清理 pipeline 本次生成的报告/analysis 半成品，但保留已成功且 scope 可信的工具产物。
  - 若本次设置了 `CYCLE_TRACE_KERNEL_NAME`，旧的无 kernel/命令级 CycleTrace、mcProfiler、analysis 和 report 不能复用，只有 mcTracer `tracer_out.json` 可跨 scope 复用；
  - 重新执行同一 run 的 `collect-run`，让脚本重采 CycleTrace 和 mcProfiler 并刷新报告。
  - 是否重新执行 `collect-run` 由 `SKILL.md` 的 agent retry 策略决定。

## TR-COL-013

- 阶段：collect
- 触发：必需采集阶段超时
- 匹配信息：
  - `trace tool collection timed out... current limit COLLECT_TIMEOUT_SECONDS=<N>s`
- 处理方法：
  - 本次实际执行的工具共享采集预算已耗尽；
  - mcTracer 或 cycle-trace-ng 超时一定失败，`REQUIRE_MCPROFILER=true` 时 mcProfiler 超时也失败。
  - 脚本会终止本次超时工具启动的进程组，当前失败阶段 run_dir 半成品会被清理，workdir 根目录临时产物也会被清理，已成功阶段的 run_dir 标准化产物保留。
  - 若 `ENABLE_SOURCE_LATENCY=true` 且日志出现 `pydpg bulk fast path is not enabled` warning，按 `reference/11-pydpg-bulk.md` 判断是否需要显式安装 helper；该 helper 不由标准 `collect-run` 自动安装。
  - 根据实际需求增大 `COLLECT_TIMEOUT_SECONDS`、缩短 `OP_EXEC_CMD` 或减少采集范围后重新执行 `collect-run`；
  - 下一次同 run 目录重试会跳过已成功阶段并重新获得一份超时预算。

## TR-COL-014

- 阶段：collect
- 触发：kernel-probe Python plugin 加载失败
- 匹配信息：
  - `Load libpython failed`
  - `cannot acquire createPyPlugin handle`
  - `cannot load python plugin, skip kernel probe`
- 处理方法：
  - `ENABLE_SOURCE_LATENCY=true` 时这是硬错误，即使 `cycle-trace-ng` 返回 0 也必须停止。
  - 确认 `CYCLE_TRACE_BIN` 对应安装目录下存在 `share/cycle-trace`，并让 `/opt/maca/share/cycle-trace` 指向匹配目录，例如 `docker exec <container> bash -lc 'ln -sfn <cycle-trace-install>/share/cycle-trace /opt/maca/share/cycle-trace'`。
  - 如果容器 `trace_py310_test` 使用 `/opt/maca-20260505/bin/cycle-trace-ng`，示例为 `docker exec trace_py310_test bash -lc 'ln -sfn /opt/maca-20260505/share/cycle-trace /opt/maca/share/cycle-trace'`。
  - 背景解释见 `reference/03-common-issues.md`；
  - source-latency 后处理机制见 `reference/10-source-latency.md`。

## TR-COL-015

- 阶段：collect
- 触发：kernel-probe Python plugin 内部 objdump 路径不可用
- 匹配信息：
  - `Command '<plugin-expected-mxobjdump> --list-elf <binary>' returned non-zero exit status 127`
- 处理方法：
  - `ENABLE_SOURCE_LATENCY=true` 时这是硬错误。
  - 该错误来自 `CYCLE_TRACE_BIN` 启动的 `cycle-trace-ng --kernel-probe` Python plugin，不是 trace-report 后续 `source-latency` 阶段；
  - `MACA_OBJDUMP_BIN` 只控制 trace-report 后处理用的 `mxobjdump --extract-elf`，不能修复 plugin 内部查找路径。
  - 确认 plugin 期望的 `mxobjdump` / `llvm-objdump` 路径在目标环境中存在并可执行。
  - 通用模板：`docker exec <container> bash -lc 'mkdir -p <plugin-expected-mxgpu-llvm-bin-dir> && ln -sfn <actual-mxgpu-llvm-bin-dir>/mxobjdump <plugin-expected-mxgpu-llvm-bin-dir>/mxobjdump && ln -sfn <actual-mxgpu-llvm-bin-dir>/llvm-objdump <plugin-expected-mxgpu-llvm-bin-dir>/llvm-objdump'`。
  - 如果容器 `trace_py310_test` 中 plugin 期望 `/opt/maca/mxgpu_llvm/bin`，实际工具在 `/opt/maca/restricted/Tools/mxgpu_llvm/bin`
  - 示例为`docker exec trace_py310_test bash -lc 'mkdir -p /opt/maca/mxgpu_llvm/bin && ln -sfn /opt/maca/restricted/Tools/mxgpu_llvm/bin/mxobjdump /opt/maca/mxgpu_llvm/bin/mxobjdump && ln -sfn /opt/maca/restricted/Tools/mxgpu_llvm/bin/llvm-objdump /opt/maca/mxgpu_llvm/bin/llvm-objdump'`。
  - 背景解释见 `reference/03-common-issues.md`；
  - source-latency 后处理机制见 `reference/10-source-latency.md`。

## TR-COL-016

- 阶段：collect
- 触发：CycleTrace kernel filter 名称与 mcTracer `args.name` 不匹配
- 匹配信息：
  - `CYCLE_TRACE_KERNEL_NAME does not match any mcTracer args.name`
  - `current value matches mcTracer display name/top-level name only`
  - `candidate mcTracer args.name values:`
  - `CycleTrace JSON has no hardware instruction events under CYCLE_TRACE_KERNEL_NAME=...`
- 处理方法：
  - `CYCLE_TRACE_KERNEL_NAME` 必须填写 mcTracer kernel event 的 `args.name`，也就是 mangled/internal kernel name；
  - 不要填写同一 event 顶层 `name` 中的 demangled/display name。
  - 按错误输出中的 candidate mcTracer `args.name` 复制完整字符串；
  - 候选列表最多展示前 8 个，如果没有符合的候选值，回到 mcTracer 的 `tracer_out.json` 中检查 `traceEvents[].args.name` 并复制完整精确值。
  - 若 name 正确但仍无硬件事件，检查 `CYCLE_TRACE_KERNEL_REPEAT` 是否选择了不存在的 repetition。

## TR-COL-017

- 阶段：collect
- 触发：mcTracer JSON 可读性或结构校验失败
- 匹配信息：
  - `invalid mcTracer JSON`
  - `mcTracer JSON has no traceEvents`
  - `mcTracer JSON has no kernel launch events`
- 处理方法：
  - 不要用该 mcTracer 结果继续诊断，也不要手工拼接旧 `tracer_out.json`。
  - 先确认 `OP_EXEC_CMD` 在目标 workdir 下能直接执行，并能触发目标 kernel。
  - 检查 mcTracer 原始输出目录和 `<run-dir>/tracer_out.json` 是否来自本次 run。
  - 修复执行命令或采集环境后，重新执行同一 run 目录的 `collect-run`。

## TR-ART-001

- 阶段：artifacts
- 触发：离线归档源不是目录
- 匹配信息：
  - `source is not a directory: <path>`
- 处理方法：
  - `trace_profile_pipeline.py collect/run --source` 必须指向包含原始 JSON/PNG 的目录。

## TR-ART-002

- 阶段：artifacts
- 触发：标准化 artifacts 不完整或 CycleTrace 无效
- 匹配信息：
  - `required artifacts are incomplete: missing=..., invalid=...`
  - `collection_manifest.json.invalid_required` 非空
- 处理方法：
  - 回到采集阶段补齐或重采；
  - 空 CycleTrace、metadata-only 或无硬件事件的 CycleTrace 不得生成高置信诊断。

## TR-ANA-001

- 阶段：analyze
- 触发：`analyze` 找不到 artifacts 或 run 目录
- 匹配信息：
  - `trace_profile_pipeline.py analyze 找不到 artifacts`
  - `FileNotFoundError(<run-dir>)`
- 处理方法：
  - `analyze` 默认读取 `<run-dir>/artifacts/`；
  - 先执行 `run` 或 `collect` 标准化原始产物。

## TR-ANA-002

- 阶段：analyze
- 触发：Digest 中 `profiler` 字段缺失
- 匹配信息：
  - `Digest 中 profiler 字段缺失`
  - `mcProfiler artifacts are missing; profiler duty, IPC, cache, bank-conflict, and Roofline evidence are N/A.`
- 处理方法：
  - `REQUIRE_MCPROFILER=false` 时这是允许的数据边界；
  - 需要硬件 duty、IPC、cache、bank conflict 或 Roofline 证据时，补齐两个 mcProfiler JSON，或把 `REQUIRE_MCPROFILER=true` 并增大 `COLLECT_TIMEOUT_SECONDS` 后重采。

## TR-ANA-003

- 阶段：analyze
- 触发：CycleTrace JSON 可读但不含有效事件。
- 匹配信息：`CycleTrace JSON has no traceEvents: <path>`、`CycleTrace JSON has no hardware instruction events: <path>`。
- 按 [TR-COL-006](#tr-col-006)处理无效产物与恢复；该条目统一维护 small-workgroup 条件、算法、AP 语义、预算和聚合范围限制。不能仅凭 metadata 或空 `traceEvents` 做高置信诊断。

## TR-ANA-004

- 阶段：analyze
- 触发：离线分析时 CycleTrace kernel filter 后无硬件事件
- 匹配信息：
  - `CycleTrace JSON has no hardware instruction events under CYCLE_TRACE_KERNEL_NAME=...`
- 处理方法：
  - 离线 `analyze` 读取到 metadata-only 或 wave-only JSON。
  - 确认该 JSON 是用 mcTracer `args.name` 作为 `CYCLE_TRACE_KERNEL_NAME` 采集得到；
  - 如果 kernel name 正确，检查 `CYCLE_TRACE_KERNEL_REPEAT` 是否过窄。

## TR-ANA-005

- 阶段：analyze
- 触发：离线分析时 mcTracer 找不到目标 kernel event
- 匹配信息：
  - `mcTracer JSON has no kernel launch entry matching CYCLE_TRACE_KERNEL_NAME=...`
- 处理方法：
  - `analyze` 会用 `CYCLE_TRACE_KERNEL_NAME` 匹配 mcTracer `traceEvents[].args.name`，以保证 mcTracer launch/resource 字段和 CycleTrace 单 kernel scope 对齐。
  - 确认 active config 与本次 `tracer_out.json` 属于同一次采集；
  - 如果候选不在错误输出中，直接检查 `tracer_out.json` 的 `traceEvents[].args.name` 并复制完整精确值。

## TR-CMP-001

- 阶段：compare
- 触发：compare 输入不足或格式错误
- 匹配信息：
  - `compare requires at least two --tag values`
  - `compare requires at least two --case values`
  - `compare requires either --case repeated at least twice...`
  - `compare requires either --case repeated at least twice, or --run-dir with repeated --tag`
  - `--case must use label=/path/to/run-dir`
  - `--case label must not be empty`
- 处理方法：
  - 对同一 run 目录使用至少两个 `--tag`，或跨目录使用至少两个 `--case label=/path/to/run-dir`。

## TR-CMP-002

- 阶段：compare
- 触发：compare 无法确定输出或找不到 metrics
- 匹配信息：
  - `compare output directory could not be determined`
  - `no metrics_all_*.json found in <analysis_dir>`
  - `multiple metrics_all_*.json files found in <analysis_dir>`
  - `--case ...` 下 metrics 数量不符合预期
- 处理方法：
  - 确认每个 case 已完成 `run` 或 `collect + analyze`，且 `analysis/` 下有且只有期望的 `metrics_all_*.json`；
  - 必要时用 `--output-dir` 指定输出目录。

## TR-USC-001

- 阶段：usecase split
- 触发：usecase 拆分缺少输入目录或必需 artifacts
- 匹配信息：
  - `split-usecases artifact directory does not exist: <path>`
  - `split-usecases requires tracer_out.json and primary CycleTrace JSON in artifacts`
- 处理方法：
  - 先完成标准 `collect-run` 或离线 `trace_profile_pipeline.py run`，确认 `<run-dir>/artifacts/tracer_out.json` 和 primary `c-trace_output_dpc_<CYCLE_TRACE_DPC_ID>.json` 存在且有效。
  - 该后处理不从 run 根目录 raw JSON 自动拼接产物。

## TR-USC-002

- 阶段：usecase split
- 触发：usecase 拆分 confidence 参数非法或匹配未达到生成条件
- 匹配信息：
  - `split-usecases --min-confidence must be high, medium, or low`
  - `match_result.status=ambiguous`
  - `mcprofiler_match_result.status=ambiguous`
- 处理方法：
  - 使用 `--min-confidence high|medium|low`。
  - 默认 `medium`；
  - 低置信或数量不匹配时默认只写 `usecases/manifest_<tag>.json`。
  - 若需要接受低置信 ordered candidate，显式使用 `--min-confidence low`；
  - 没有 matches 的 ambiguous 结果不会生成派生报告。
  - 若 `mcprofiler_match_result` ambiguous，说明 mcProfiler occurrence 数量、顺序或 `WORKGROUPS` 无法与 mcTracer/CycleTrace usecase 校验一致；
  - 此时 usecase 报告不能使用 profiler 强证据，优先转为单 kernel 单用例采集。

## TR-LLM-001

- 阶段：llm diagnosis
- 触发：追加诊断入口、诊断文件或包装执行错误
- 匹配信息：
  - `append-diagnosis must be run through trace_report_env.py --config "$ACTIVE_CONFIG" run so it writes the target report`
  - `diagnosis file does not exist: ...`
  - `append-diagnosis failed: exit=...`
- 处理方法：
  - 标准 workflow 使用 `python3 scripts/trace_report_env.py --config "$ACTIVE_CONFIG" finalize-run --run-dir "$PROFILE_RUN_DIR" --diagnosis-file <file>`。
  - 只有手工调试底层 stdin 时才用 `trace_report_env.py run -- python3 .trace-report/scripts/trace_profile_pipeline.py append-diagnosis --report ...`。

## TR-LLM-002

- 阶段：llm diagnosis
- 触发：here-doc 或 stdin 内容为空
- 匹配信息：
  - `LLM follow-up diagnosis content is empty`
- 处理方法：
  - 确认 here-doc 非空；
  - 若明明传了内容，先重新 `sync-scripts`，避免目标还是旧版 stdin 处理逻辑。

## TR-LLM-003

- 阶段：llm diagnosis
- 触发：LLM 诊断缺少必需 heading
- 匹配信息：
  - `LLM follow-up diagnosis is missing required headings: ...`
- 处理方法：
  - 追加内容必须包含 `### Summary`、`### Quality Gate Result`、`### Final Diagnosis`、`### Validation Plan`。

## TR-LLM-004

- 阶段：llm diagnosis
- 触发：`Final Diagnosis` 没有诊断项或缺少每项推理链
- 匹配信息：
  - `Final Diagnosis must include at least one #### Diagnosis item`
  - `Diagnosis <N> is missing #### Reasoning Chain`
  - `Reasoning Chain is missing: ...`
- 处理方法：
  - 每个 `#### Diagnosis ...` 都必须有自己的 `#### Reasoning Chain`，并覆盖 measured evidence、mechanism、counter-evidence / weaker hypotheses、confidence boundary、edit rationale、validation expectation。

## TR-LLM-005

- 阶段：llm diagnosis
- 触发：报告路径不存在或 marker 不完整
- 匹配信息：
  - `report does not exist: <path>`
  - `report has incomplete LLM follow-up diagnosis markers`
- 处理方法：
  - 确认 `--report "$PROFILE_RUN_DIR/REPORT_${PROFILE_TAG}.md"` 指向目标环境中的同一个报告；
  - marker 不完整时先修复报告结构，避免追加到不可判定位置。

## TR-REP-001

- 阶段：report check
- 触发：最终报告检查发现必需文件缺失或为空
- 匹配信息：
  - `required report check input is missing or empty: <path>`
  - `required report output is missing or empty: <path>`
- 处理方法：
  - 回到对应阶段补齐产物。
  - `REPORT_<tag>.md`、`analysis/digest_<tag>.md`、`metrics_all_<tag>.json`、`metrics_key_<tag>.json` 和 `artifacts/collection_manifest.json` 是基础报告检查必需项。

## TR-REP-002

- 阶段：report check
- 触发：`collection_manifest.json` 显示必需产物缺失、无效或 usecase mcProfiler 引用不可用
- 匹配信息：
  - `collection_manifest.json missing_required is not empty: ...`
  - `collection_manifest.json invalid_required is not empty: ...`
  - `collection_manifest.json mcprofiler_reference...`
- 处理方法：
  - 不要继续做高置信诊断；
  - 回到采集阶段重采或补齐无效产物。
  - 若是 `per-usecase-ref` 引用失败，检查 `usecase_*/artifacts/collection_manifest.json.mcprofiler_reference` 指向的父级 `artifacts/mcprofiler_per_kernel/occurrence_*` 是否存在，且 `mcprofiler_report_dumped.json` 与 `mcprofiler_report.txt.json` 可解析。

## TR-REP-003

- 阶段：report check
- 触发：`metrics_all_<tag>.json` 结构非法
- 匹配信息：
  - `metrics_all_<tag>.json must contain a JSON object`
- 处理方法：
  - 重新执行同 run 目录的 `collect-run` 或目标环境中的 `trace_profile_pipeline.py analyze`，确认分析脚本和 artifacts 匹配。

## TR-REP-004

- 阶段：report check
- 触发：source-latency 未被基础报告吸收或不可用
- 匹配信息：
  - `metrics_all source_latency.available must be true when ENABLE_SOURCE_LATENCY=true`
  - `metrics_all source_latency.coverage must be an object when ENABLE_SOURCE_LATENCY=true`
  - `metrics_all source_latency.coverage.raw_modeled_cycles must be > 0 when ENABLE_SOURCE_LATENCY=true`
  - `REPORT is missing source-latency summary markers: ...`
  - `REPORT must state that source-latency produced no attributed source lines`
  - `REPORT is missing source-latency top instruction summary when attributed source lines exist`
- 处理方法：
  - `ENABLE_SOURCE_LATENCY=true` 时，`analysis/source_latency_<tag>.md/.csv/.json` 必须存在，`metrics_all` 必须有可用 summary 和非零 modeled cycles，基础 `REPORT_<tag>.md` 必须包含 `### 3.8 Source-Line Attribution`、coverage、Actionability、top-line/top-instruction 摘要、modeled-attribution caveat 和 source-latency artifacts。
  - 若 summary 没有 attributed source lines，报告必须明确降级说明，不能给高置信源码行修改目标。
  - 修复后重新执行 `collect-run` 或刷新基础报告。

## TR-REP-005

- 阶段：report check
- 触发：最终报告缺少必需 LLM 第 9 章
- 匹配信息：
  - `REPORT is missing complete LLM follow-up diagnosis markers`
  - `REPORT is missing complete LLM follow-up diagnosis markers. collect-run has produced only the base report; do not recollect...`
- 处理方法：
  - 不要重新采集。
  - 先按 `reference/quick-finalize.md` 读取 `REPORT_<tag>.md`、`analysis/metrics_key_<tag>.json`，必要时读取 `analysis/metrics_all_<tag>.json`，生成诊断 Markdown 文件，再执行 `python3 scripts/trace_report_env.py --config "$ACTIVE_CONFIG" finalize-run --run-dir "$PROFILE_RUN_DIR" --diagnosis-file <diagnosis.md>`。
  - 只有 quick 结构不足或 generated usecase 复杂失败时，再读 `reference/08-llm-followup-diagnosis.md`。
  - 完整 workflow 不能跳过 LLM follow-up diagnosis；
  - 若该错误由最终 `check-report` 抛出，agent 应继续补写并追加第 9 章，而不是重跑 `collect-run`。

## TR-REP-006

- 阶段：report check
- 触发：CycleTrace kernel filter 启用时最终报告缺少性能数据 scope
- 匹配信息：
  - `metrics_all collection_scope.cycle_trace_kernel_name must not be empty when CycleTrace kernel filter is enabled`
  - `REPORT is missing CycleTrace kernel scope markers: ...`
- 处理方法：
  - 设置 `CYCLE_TRACE_KERNEL_NAME` 后，`metrics_all.collection_scope` 和 `REPORT_<tag>.md` 必须展示 CycleTrace target kernel、repeat、sample mode 和 scope note。
  - 修复后重新执行同 run 目录的 `collect-run` 或刷新基础报告。

## TR-REP-007

- 阶段：report check
- 触发：generated usecase 报告缺少必需 LLM 第 9 章
- 匹配信息：
  - `generated usecase reports require finalize-run with usecase diagnoses; ...`
- 处理方法：
  - `<run-dir>/usecases/manifest_<tag>.json.generated_reports` 非空时，最终 `check-report` 会逐个校验 usecase 报告第 9 章。
  - 为父级 scope 生成 `<parent-scope-diagnosis.md>`，为每个 `usecase_XXX` 生成 `<diagnosis-dir>/usecase_XXX.md`，执行 `python3 scripts/trace_report_env.py --config "$ACTIVE_CONFIG" finalize-run --run-dir "$PROFILE_RUN_DIR" --diagnosis-file <parent-scope-diagnosis.md> --diagnosis-dir <diagnosis-dir>`。
  - 不要重采。

## pydpg bulk 安装脚本错误

`[pydpg-bulk] ERROR` 不属于标准 `[TR-*]` 错误链路；安装、启用和错误处理统一见
`reference/11-pydpg-bulk.md`。

## 错误修复后的参考

| 场景 | 参考文档 |
|---|---|
| active config、SSH/password SSH、工具路径和受保护配置 | `reference/00-preflight.md` |
| run 目录命名、`PROFILE_*` 派生和同步边界 | `reference/01-directory-layout.md` |
| `collect-run`、采集行为、成功产物、阶段恢复、清理、离线解析和 compare 入口 | `reference/02-collection.md` |
| 非错误类背景、指标现象或常见误解 | `reference/03-common-issues.md` |
| source-latency 后处理和源码行归因 | `reference/10-source-latency.md` |
| pydpg bulk fast path 安装、启用和 `[pydpg-bulk] ERROR` | `reference/11-pydpg-bulk.md` |
| LLM follow-up diagnosis 追加和质量门失败写法 | `reference/08-llm-followup-diagnosis.md` |
| 多 kernel / 多 usecase 派生报告 | `reference/13-usecase-split.md` |
