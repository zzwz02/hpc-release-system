# 网站结果交付

本模块将文件和结构化结论交给网站，由网站按本轮 `post_comment` 选择追加 Jira 评论。Jira 操作边界遵守[AGENTS.md](../../../../../../AGENTS.md#边界必须遵守)。

## 报告与证据

记录目录按[AGENTS.md](../../../../../../AGENTS.md#工作目录约定)。每轮交付将 `REPORT.md`、候选 patch、必要复现脚本和日志复制到当前对话的 `artifacts/`。

`REPORT.md` 写清问题分类、归属、当前结论、关键对照、实际环境、修复与验证、未确认项和下一步。引用历史结果时注明来源和适用条件，不写成本轮重新执行的结果。完成条件与归属依据按[主流程](../../../SKILL.md#3-按归属处理)核对。

修复首次交付应包含可执行的应用方法、候选补丁、原案例前后结果与对应日志，不等待用户追问后补交。临时操作和正式候选采用不同方案时分别注明验证范围。解释性追问直接回答，不重新要求审核同一修复，不为凑流程编造新实验。

交接材料给出原始现象、缩小范围的关键实验、主受理团队，以及按[外组交接要求](../../problem-analysis/SKILL.md#外组交接)准备的复现材料：必要代码或输入、环境、构建与运行命令、预期和实际结果及其与原问题的关系。接手分工与责任判断按[主流程](../../../SKILL.md#3-按归属处理)表达。

`summary` 简述定位结果、复现材料及尚未完成的原因，区分有效独立案例、已验证的缩减原案和诊断材料；报告给出文件位置与验证结果，复现文件列入 `artifacts`。

工单涉及发布应用时，报告分别写明目录查询结果、应用范围依据与故障技术归因；`ownership.reasoning` 概述这两层判断，`evidence` 仅记录实际查询/实验，范围缺口按下文填写 `action_required` 和 `assistance`。沿用规定的 JSON 字段，不增加自定义 `application_scope` 顶层字段。

未执行步骤注明原因。缩减输入和诊断改动注明“仅用于定位”，不计入 `fix` 或修复补丁，也不替代原案例验收。修复轮次的 `.patch` / `.diff` 只包含候选修复，诊断改动随复现材料说明。环境、版本、镜像 digest、源码 commit、实际加载组件按已确认事实填写；不能用主机 IP 或 good/bad 标签代替版本。缺少条件时保留已经达到的技术结论，并用 `action_required` 和 `assistance` 说明实际缺口。

## 分析路径

完成实质问题分析或候选修复时，在 `work/runs/<JIRA-KEY>/ANALYSIS_PATH.md` 持续维护可复核的判断路径。每个有效动作完成并形成判断后立即追加，不在结束时仅凭最终结论补写过程。只为实际改变当前判断的复现、对照、定位、修复和验证建立步骤；准备环境、浏览目录或无新信息的重复命令保留在执行记录中，不单独展开。

每一步只设一个步骤标题，正文固定合并为三项：

```markdown
# 分析路径

## 第 N 步：<本步解决的问题>

**目标**：说明本步要区分或验证什么。

**操作与证据**：说明实际动作、关键命令和结果，并引用 `evidence/`、源码位置或交付产物。

**判断与后续**：说明证据支持、排除或尚不能确认什么，以及该判断为什么导向下一步。

## 当前结论

- **已确认**：……
- **已排除**：……
- **尚未确认**：……
- **修复状态**：……
```

操作与证据只写真实执行和观察到的事实，不粘贴可由证据文件承载的大段日志。无效实验或矛盾结果在其影响当前判断时保留为一步，明确说明为什么不能用于支持或排除候选。修复步骤沿用同一格式，在“操作与证据”中说明改动针对前述哪项判断、改动内容、针对性测试和原案例验证结果；在“判断与后续”中说明是否足以支持修复有效及仍需的人工审核。

续办时读取并追加现有文件，保留仍有效的步骤。新证据推翻旧判断时不删除原步骤，新增一步说明被修正的判断、修正依据及新的下一动作，并刷新末尾“当前结论”。该文件记录基于证据的工程判断，不记录内部推演或未实际核对的猜测。

交付时把当前文件复制为 `artifacts/ANALYSIS_PATH.md`，与 `artifacts/REPORT.md` 一并逐项列入结构化结论的 `artifacts`。报告概括最终事实、根因、修复和限制，不重复展开全部步骤；`summary`、`root_cause` 和 `evidence` 继续提供网站及 Jira 评论所需的简要结论与关键证据。本轮没有进行实质分析或修复时，不编造空步骤。

## 输出契约

最终消息仅输出符合网站本轮 `outputSchema` 的 JSON。所有必需字段保留；未知字符串用空串，无条目用空数组。不要增加自定义顶层字段，不输出独立发布脚本的回执。

| 字段 | 内容 |
|---|---|
| `conclusion` | 本轮达到的技术结论，只表达分析深度 |
| `action_required` | 当前需要的人为动作；没有时为 `none` |
| `assistance` | 仅 `needs_info` / `needs_help` 时填写 `current_issue`、`owner`、`request`，其他状态三个字符串均留空 |
| `issue_category` | 构建、运行、正确性、性能、环境等实际分类 |
| `ownership` | `belongs_to_us`: `yes` / `no` / `unclear`；`target_group`：主受理团队；`reasoning`：证据与限制 |
| `summary`、`root_cause` | 中文结论与已确认根因；未确认机制如实说明 |
| `machines_used` | 本轮实际使用的全部获准 `user@host`，未使用时为 `[]`；旧 schema 不添加此字段 |
| `machine` | 兼容字段：只使用一台机器时填写该 `user@host`，多台或未使用时为空 |
| `reproduction` | `reproduced`、`environment`、`steps`，保留原命令和实际环境 |
| `evidence` | 每项包含 `description`、`command`、`result`，仅实际执行及输出 |
| `fix` | `description`，说明候选改动与验证结果；无修复时为空字符串 |
| `artifacts` | 当前对话内具体文件的相对路径 |
| `next_steps` | 后续技术动作，最多 2 项；无动作时为空数组，不重复 `assistance` |
| `skills_used` | 实际读取和使用的 skill 名称 |
| `comment` | 简短评论：`kind`、`outcome`、`body`、`evidence_indices`、`commands`、`link_text`；以本轮 outputSchema 为准，旧 schema 没有该字段时不添加 |

`comment` 的内容与格式按网站本轮提示和 `outputSchema` 填写；完整报告、补丁和日志按上述要求交付。身份、对话链接、固定声明和记录编号由网站统一添加。

`conclusion` 取值：

| 状态 | 使用条件 |
|---|---|
| `insufficient_evidence` | 现有证据还不能形成更具体的技术判断 |
| `reproduced` | 已稳定复现问题，但尚未缩小到具体范围 |
| `cannot_reproduce` | 已按可用的工单条件实际尝试，当前未复现 |
| `scope_narrowed` | 已定位到具体阶段、组件、kernel 或触发条件，机制尚未确认 |
| `root_cause_confirmed` | 根因已有直接证据支持 |
| `fix_prepared` | 已形成候选修复，但验证尚未完成 |
| `fixed_pending_review` | 候选修复已通过针对性测试和原案例验收，等待人工审核 |
| `analysis_done` | 已完成本轮要求的分析，不涉及修复 |
| `not_our_group` | 仅兼容历史结果；新结果保留实际技术深度，用 `needs_handoff` 表达转交 |

`action_required` 取值：

| 状态 | 使用条件 |
|---|---|
| `none` | 当前无需别人提供材料或执行操作 |
| `needs_info` | 需要提供已有材料、输入、日志、版本或判据 |
| `needs_help` | 需要处理资源、权限、SSH、工具或必须由人工执行的操作 |
| `needs_review` | 需要 owner 审核结论、候选修改或验证证据 |
| `needs_handoff` | 已满足交接要求，需要外组接手；填写 `target_group`。已确认外部责任时用 `no`；需外组牵头调查但责任未定时保留 `unclear`，在 `reasoning` 写明接手依据及未确认范围 |

允许组合：

| conclusion | action_required |
|---|---|
| `insufficient_evidence` | `needs_info` / `needs_help` |
| `reproduced` | `none` / `needs_info` / `needs_help` |
| `cannot_reproduce` | `none` / `needs_info` / `needs_help` |
| `scope_narrowed` | `none` / `needs_info` / `needs_help` / `needs_handoff` |
| `root_cause_confirmed` | `none` / `needs_help` / `needs_review` / `needs_handoff` |
| `fix_prepared` | `needs_info` / `needs_help` / `needs_review` |
| `fixed_pending_review` | `needs_review` |
| `analysis_done` | `none` / `needs_review` |
| `not_our_group` | `needs_handoff` |

输出状态须符合允许组合，各字段与最新证据一致。`needs_handoff` 使用 `ownership` 和 `next_steps` 表达接手信息，不重复填写 `assistance`。历史 `not_our_group` 仅与 `belongs_to_us=no` 配合；新结果用实际技术深度表达。

需要补充或协助时，`current_issue` 说明受影响的工作，`owner` 指明处理人或职责角色，`request` 给出所需材料或操作及必要的版本、文件、环境和判据。实际尝试后未复现、仍缺原失败条件时，保留 `cannot_reproduce` 并填写相应请求。

`next_steps` 只写满足本轮结束条件后仍需开展的技术动作；不把本轮仍可完成的定位工作留给接手方。`needs_info` / `needs_help` 通常留空，避免重复 `assistance`；没有后续动作时不为凑字段编写建议。网站结果视图继续保留这些结构化字段；JIRA 正文按上文独立组织，不能依赖网站自动补充请求。

## 文件回收

网站只拉取 `artifacts` 数组列出的文件，不递归扫描目录，也不会自动收集 `work/runs/`。逐个列出报告、patch 和必要日志，不列目录、远端路径或工作目录之外的文件。每个文件不超过 10 MiB；大日志导出注明来源的相关片段，完整日志的位置及摘要留在报告中。

结束前确认文件真实存在且可读；远端产物先复制回本地 `artifacts/`。排除凭据、SSH key、登录配置、完整会话记录和大型构建目录。文件准备完成不等于网站成功发布评论，也不代表 Jira 工单已关闭；不编造报告链接或发布回执。

网站单独记录文件回收状态：分析结论先保留，任何所列文件缺失、越界、超限或读取失败均标记“交付不完整”，暂停 Jira 评论发布。当前对话最新一轮可以单独重试文件回收，不重新执行模型；已成功回收且校验完整的文件复用。全部文件回收成功后，网站按该轮原有 `post_comment` 设置发布 Jira 评论或仅在网页显示。agent 不直接发布评论。
