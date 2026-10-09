# 脚本模块索引

本文面向维护者和开发者，用于定位脚本职责、入口调用关系和内部模块边界。标准 workflow 控制以
`SKILL.md`、`reference/quick-collect.md`、`reference/quick-diagnose.md` 和
`reference/quick-finalize.md` 为准；`reference/02-collection.md` 只承接采集参数、阶段恢复、离线解析和 compare 等深层说明。报告结构、LLM 诊断、source-latency 和 usecase 拆分分别由 `reference/07-report-template.md`、`reference/08-llm-followup-diagnosis.md`、`reference/10-source-latency.md` 和 `reference/13-usecase-split.md` 承接。不要因为本文列出内部模块就绕过标准入口手工拼接产物。

## 读取门槛

只有维护脚本、重构脚本、追踪模块边界或解释入口调用关系时，才读取本文。正常 trace-report workflow 不读取本文，也不因为本文列出内部模块就绕过标准入口。

## 调用层级

```text
trace_report_env.py
  -> collect-run
      -> collect_trace_profile.sh
          -> trace_profile_pipeline.py run/analyze
      -> trace_profile_pipeline.py source-latency/check-report/split-usecases
  -> finalize-run / append-diagnosis / append-usecase-diagnosis
      -> trace_profile_pipeline.py append-diagnosis/check-report
  -> sync-artifacts / run
trace_profile_pipeline.py
  -> collect/analyze/run/compare/split-usecases/source-latency/append-diagnosis/check-report
  -> trace_report/*.py
```

- `trace_report_env.py` 是用户和 agent 的环境入口，负责 active config、目标环境、脚本同步、标准 workflow 编排和最终收尾。
- `collect_trace_profile.sh` 是目标环境内的采集脚本，负责 mcTracer、CycleTrace、mcProfiler 采集和原始产物标准化。
- `trace_profile_pipeline.py` 是目标环境内的 pipeline CLI，负责归档、解析、报告、compare、source-latency、usecase split 和质量门；`collect` 是离线归档子命令，不是 `collect_trace_profile.sh` 当前直接调用路径。
- `scripts/trace_report/*.py` 是实现模块，通常只通过上述入口调用。

位置边界：`trace_report_env.py` 从控制侧启动；它将本地 `scripts/` 发布到目标 `${REMOTE_WORKDIR}/.trace-report/scripts/`。标准 workflow 中的 `collect_trace_profile.sh`、`trace_profile_pipeline.py` 和内部模块在目标执行侧运行，目标 cwd 为 `REMOTE_WORKDIR`。配置写入和 artifact 回传留在控制侧 `LOCAL_WORKDIR`。

## 入口脚本

| 文件 | 职责 | 主要承接文档 |
|---|---|---|
| `scripts/trace_report_env.py` | 读取 YAML、校验目标环境、导出环境变量，包装 local/docker/ssh/ssh+docker 执行，并编排 `collect-run`、`finalize-run`、同步和阶段恢复 | `reference/00-preflight.md`、`reference/01-directory-layout.md`、`reference/02-collection.md` |
| `scripts/collect_trace_profile.sh` | 在目标环境执行三工具采集，处理超时、阶段复用、失败清理、CycleTrace kernel filter、mcProfiler per-kernel 采集和 source-latency 采集前置 | `reference/02-collection.md`、`reference/12-cycle-trace-options.md` |
| `scripts/trace_profile_pipeline.py` | 提供 `collect`、`analyze`、`run`、`compare`、`split-usecases`、`append-diagnosis`、`check-report`、`source-latency` 子命令 | `reference/02-collection.md`、`reference/08-llm-followup-diagnosis.md`、`reference/10-source-latency.md`、`reference/13-usecase-split.md` |
| `scripts/install_cycle_trace_pydpg_bulk.sh` | 安装或检查 CycleTrace pydpg bulk fast path，输出启用环境变量 | `reference/11-pydpg-bulk.md` |

## 内部模块

| 模块 | 职责 | 主要承接文档 |
|---|---|---|
| `trace_report/env_config.py` | flat YAML env contract、默认值、受保护配置和执行模式判断 | `reference/00-preflight.md` |
| `trace_report/env_validate.py` | local/docker/ssh/ssh+docker 目标环境 preflight，校验工作目录、工具、设备、source-latency 输入和目标命令 | `reference/00-preflight.md`、`reference/09-error-troubleshooting.md` |
| `trace_report/target.py` | 目标环境命令构造与执行包装，包括 docker、ssh、ssh+docker staging 和同步辅助 | `reference/00-preflight.md`、`reference/01-directory-layout.md` |
| `trace_report/errors.py` | 稳定错误 ID 匹配和报错格式化 | `reference/09-error-troubleshooting.md` |
| `trace_report/artifacts.py` | 原始产物归档、标准文件发现、manifest 写入和 JSON 读写 | `reference/01-directory-layout.md`、`reference/02-collection.md` |
| `trace_report/parsers.py` | 解析 CycleTrace、mcTracer、mcProfiler 原始产物为统一中间结构 | `reference/04-analysis-dimensions.md`、`reference/05-c500-metric-names.md` |
| `trace_report/metrics.py` | 汇总 `metrics_all` / `metrics_key`、bound 分类、诊断规则和分析产物写入 | `reference/04-analysis-dimensions.md`、`reference/05-c500-metric-names.md`、`reference/06-diagnosis-playbook.md` |
| `trace_report/render.py` | 生成 `digest_<tag>.md` 和 `REPORT_<tag>.md`，包含报告章节、数据边界和 source-latency summary 吸收 | `reference/07-report-template.md`、`reference/10-source-latency.md` |
| `trace_report/compare.py` | 对同 run 多 tag 或跨 run 多 case 的 `metrics_all` 进行对比报告生成 | `reference/02-collection.md` |
| `trace_report/mcprofiler_artifacts.py` | 归档 mcProfiler per-kernel occurrence，建立父级 occurrence 与 usecase 报告引用关系 | `reference/02-collection.md`、`reference/13-usecase-split.md` |
| `trace_report/source_latency.py` | 使用 lineinfo device ELF / objdump 将 CycleTrace 指令事件归因到源码行，输出 md/csv/json | `reference/10-source-latency.md` |
| `trace_report/usecase_split.py` | 按 mcTracer launch、CycleTrace segment 和 mcProfiler occurrence 拆分多 kernel / 多 usecase 产物并生成派生报告 | `reference/13-usecase-split.md` |
| `trace_report/report_append.py` | 校验并追加 `## 9. LLM Follow-up Diagnosis`，处理 marker 替换和结构化 LLM 诊断规则 | `reference/08-llm-followup-diagnosis.md` |
| `trace_report/report_check.py` | 校验基础报告、source-latency 吸收、LLM 第 9 章、generated usecase LLM 诊断和最终质量门 | `reference/07-report-template.md`、`reference/08-llm-followup-diagnosis.md` |
| `trace_report/constants.py` | 共享文件名、目录名、硬件类别和 source-latency 默认 cost 等常量 | 对应使用场景的 reference |
| `trace_report/utils.py` | 数值、百分比、表格和通用格式化辅助 | 对应使用场景的 reference |

## 维护边界

- 修改环境配置、目标执行或同步逻辑时，优先更新 `reference/00-preflight.md` 和 `reference/09-error-troubleshooting.md`。
- 修改采集参数、产物质量门、阶段恢复或 compare 入口时，优先更新 `reference/02-collection.md`。
- 修改指标字段、bound 分类或诊断规则时，优先更新 `reference/04-analysis-dimensions.md`、`reference/05-c500-metric-names.md` 和 `reference/06-diagnosis-playbook.md`。
- 修改报告结构或最终质量门时，优先更新 `reference/07-report-template.md`。
- 修改 LLM 追加、marker 或 generated usecase LLM 质量门时，优先更新 `reference/08-llm-followup-diagnosis.md`。
- 修改 source-latency 归因模型、输入或输出时，优先更新 `reference/10-source-latency.md`。
- 修改 usecase split 匹配、manifest 或父级/usecase 报告边界时，优先更新 `reference/13-usecase-split.md`。

## 不建议直接调用的内容

- 标准 workflow 通过 `trace_report_env.py collect-run/finalize-run` 调用；维护、离线排查或兼容调试时才直接使用 `trace_profile_pipeline.py`，不要直接调用 `trace_report/*.py` 模块函数。
- 不用内部 parser/metrics 输出手工拼接报告；报告必须经过 `analyze` 和 `check-report`。
- 不用父级 aggregate source-latency 替代 generated usecase 的 per-usecase source-latency。
- 不把本地 `config/trace_report_env.yaml` 模板当作 active config；实际 active config 位于 `[LOCAL_WORKDIR]/.trace-report/config/trace_report_env.yaml`。
