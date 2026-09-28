# 网站结果交付

本模块将文件和结构化结论交给网站，由网站按本轮 `post_comment` 选择追加 Jira 评论。Jira 操作边界遵守[AGENTS.md](../../../../../../AGENTS.md#边界必须遵守)。

## 报告与证据

记录目录按[AGENTS.md](../../../../../../AGENTS.md#工作目录约定)。每轮交付将 `REPORT.md`、候选 patch、必要复现脚本和日志复制到当前对话的 `artifacts/`。

`REPORT.md` 写清问题分类、归属、当前结论、关键对照、实际环境、修复与验证、未确认项和下一步。组件相关性不等于机制根因；只有通过针对性验证和原案例全部验收，才能给出 `fixed_pending_review`，补丁仍由 owner 审核提交。

交接材料给出原始现象、缩小范围的关键实验、主受理团队、完整可执行的命令、输入、环境、实际结果和判据。主受理是路由决定，不是已经证明最终缺陷归属；跨组件未二分时说明协同团队。没有网站材料核验的账号时写明确职责角色，不猜测账号或直接查询 Jira。

工单涉及发布应用时，报告分别写明目录查询结果、应用范围依据与故障技术归因；`ownership.reasoning` 概述这两层判断，`evidence` 仅记录实际查询/实验，范围缺口按下文填写 `action_required` 和 `assistance`。沿用规定的 JSON 字段，不增加自定义 `application_scope` 顶层字段。

未执行步骤注明原因，诊断改动注明“仅用于定位”。环境、版本、镜像 digest、源码 commit、实际加载组件按已确认事实填写；不能用主机 IP 或 good/bad 标签代替版本。缺少条件时保留已经达到的技术结论，并用 `action_required` 和 `assistance` 说明实际缺口。

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
| `machine` | 实际使用的获准 `user@host`；未使用机器时为空 |
| `reproduction` | `reproduced`、`environment`、`steps`，保留原命令和实际环境 |
| `evidence` | 每项包含 `description`、`command`、`result`，仅实际执行及输出 |
| `fix` | `description`，说明候选改动与验证结果；无修复时为空字符串 |
| `artifacts` | 当前对话内具体文件的相对路径 |
| `next_steps` | 后续技术动作，最多 2 项；无动作时为空数组，不重复 `assistance` |
| `skills_used` | 实际读取和使用的 skill 名称 |

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
| `not_our_group` | 证据支持由其他组处理，并已给出主受理团队与复核方法 |

`action_required` 取值：

| 状态 | 使用条件 |
|---|---|
| `none` | 当前无需别人提供材料或执行操作 |
| `needs_info` | 需要提供已有材料、输入、日志、版本或判据 |
| `needs_help` | 需要处理资源、权限、SSH、工具或必须由人工执行的操作 |
| `needs_review` | 需要 owner 审核结论、候选修改或验证证据 |
| `needs_handoff` | 需要转交；`ownership.belongs_to_us=no` 并填写 `target_group` |

允许组合：

| conclusion | action_required |
|---|---|
| `insufficient_evidence` | `needs_info` / `needs_help` |
| `reproduced` | `none` / `needs_info` / `needs_help` |
| `cannot_reproduce` | `none` |
| `scope_narrowed` | `none` / `needs_info` / `needs_help` |
| `root_cause_confirmed` | `none` / `needs_help` / `needs_review` / `needs_handoff` |
| `fix_prepared` | `needs_info` / `needs_help` / `needs_review` |
| `fixed_pending_review` | `needs_review` |
| `analysis_done` | `none` / `needs_review` |
| `not_our_group` | `needs_handoff` |

输出前检查各字段是否与本轮最新证据一致且彼此不冲突；有冲突时以已验证证据为准。再确认 `conclusion` 与 `action_required` 属于允许组合，`assistance` 只在 `needs_info` / `needs_help` 时填写；`needs_handoff` 使用 `ownership.target_group` 和 `next_steps` 表达转交信息。

需要补充或协助时，`assistance` 三项都要具体：`current_issue` 写当前卡在哪里，`owner` 写能提供材料或完成操作的人/角色，`request` 写可直接执行或提供的内容。使用具体版本、文件、命令、输入和指标，避免“补齐环境”“提供基线”“请协助处理”“进一步定位”“完成闭环”等无法直接行动的表述。例如：“请提供一次正常版本的测试记录，包含版本、输入、命令和总耗时”。

`next_steps` 只写结论成立后仍要继续的技术动作，最多两项。`needs_info` / `needs_help` 通常留空，避免重复 `assistance`；没有后续动作时不为凑字段编写建议。网站会依据这两个状态按需显示“所需补充”“所需协助”“待审核”或“建议接手”，空内容不会生成标题。

## 文件回收

网站只拉取 `artifacts` 数组列出的文件，不递归扫描目录，也不会自动收集 `work/runs/`。逐个列出报告、patch 和必要日志，不列目录、远端路径或工作目录之外的文件。每个文件不超过 10 MiB；大日志导出注明来源的相关片段，完整日志的位置及摘要留在报告中。

结束前确认文件真实存在且可读；远端产物先复制回本地 `artifacts/`。排除凭据、SSH key、登录配置、完整会话记录和大型构建目录。文件准备完成不等于网站成功发布评论，也不代表 Jira 工单已关闭；不编造报告链接或发布回执。

网站单独记录文件回收状态：分析结论先保留，任何所列文件缺失、越界、超限或读取失败均标记“交付不完整”，暂停 Jira 评论发布。当前对话最新一轮可以单独重试文件回收，不重新执行模型；已成功回收且校验完整的文件复用。全部文件回收成功后，网站按该轮原有 `post_comment` 设置发布 Jira 评论或仅在网页显示。agent 不直接发布评论。
