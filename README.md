# HPC App 发布信息协作系统

用于管理 HPC / AI4Sci App 的每月统一发布：Owner 自主报名、Gerrit app_info 自动同步、QA 结论与交付镜像 digest、CICD 申请与交付，以及发布文档产物。当前运行入口是 `app/main.py`（FastAPI）和 `web/src/main.tsx`（React）。

本文说明当前实现，规则以代码为准；拟建功能见 [发布流程改进建议](docs/release-process-roadmap.md)。仓库根的数据库可能是真实业务库，开发、测试和导出按下文隔离。

## 从哪里开始

| 使用者 | 日常入口与职责 |
| --- | --- |
| RM | 周期管理：新建月度版本、填当月环境和基础镜像、发布与解锁、app_info 同步状态、通知设置；CICD：审批、交付处理；发布文档：导出和检查 |
| Owner | App 工作台：为单个或本人名下多个运行中 App 报名、编辑分类和可选手册资料；提交新增 App、CICD 配置和启停申请 |
| QA | QA：处理测试范围扩大申请、查看测试范围与命令、填结论和交付镜像 digest、上传日志、使用 AI 分析建议 |
| SPD | CICD：查看交付任务、确认交付或退回；Jenkins 失败查询和 CICD 助手辅助排查 |
| Guest | 查看总览、App 和 QA 等矩阵允许的页面；不编辑业务数据 |
| Admin | 系统管理：角色维护、数据库清理、全局 App 删除；前端只进入 `/admin`，不承担 RM 审批职责 |

这是职责摘要，不是权限实现。角色、页签与命名能力以 [access_control.json](shared/access_control.json) 为准；所有权、阶段、锁定与申请状态还会进一步限制操作。后端鉴权是最终边界，前端按钮仅用于展示。

App 工作台和 Jenkins 失败查询中，Owner 默认勾选“只看我的”，RM 默认查看全部。Jenkins 查询的 RM“只看我的”默认不勾选，链接中的明确筛选条件优先。

总览在统计卡片旁展示当前所选周期的组件、版本和说明，内容来自 RM 在周期管理填写的当月环境（Driver、SDK、PyTorch 等），随版本选择和显式刷新更新；不再显示独立的发布时间线。上方仍展示报名截止、QA 截止和发布日。

App 详情默认打开「基本信息和app_info」，顶部为官方名称、Owner、类型和 App 类型；官方名称与 Owner 仍仅 RM 可改。「资料」保留官方 URL、描述、手册和测试说明等内容，不再显示「本期报名」tab。单个 App 的「立即同步app_info」位于「最新 app_info」标题旁。有未保存修改时需先保存或取消再同步；切换 App、周期或刷新前会提示确认放弃修改。

基本信息底部「正式版发布历史」列出该 App 的全部有效发布，每个 App/周期只保留一份，按周期发布日倒序展示周期、App 版本、整组交付镜像和发布时间。每个周期可有多条镜像，按报名锁定 app_info 的启用 `app_build` 区分 OS、CPU 架构、Python/PyTorch 标签及 release/dbg 镜像类型，逐条显示包含 digest 的完整镜像名。早期迁移仅写入最新正式版的情况，从已冻结周期的成功发布报名补充历史，不新增正式版记录；同一 App 版本在不同周期分别列出；解锁删除该周期的原记录和清单，重新发布保存替换后的最终结果。切换 App、重新进入基本信息和手动刷新都会更新历史。

顶部「批量报名」仅列本人名下 CICD 运行中的 App；应用标题栏「报名」可为当前 App 单独报名（RM 可为其他 Owner 的 App 操作）。弹窗选择仍在报名期的版本，展示 RM 填写的组件环境（Driver、SDK、PyTorch 等），确认后按最新 app_info 的 app_version 报名，默认纳入全部启用测试项，不指定基础镜像和计划 feature。已报名、缺少有效 app_info 或有未处理停止申请的 App 不可勾选。批量报名在一个事务内完成，任一 App 校验失败则整批不写入并显示错误；取消不写入。成功后切换到所选周期并更新报名状态。

应用标题栏「撤销报名」仅向该 App 的 Owner/RM 显示，作用于当前选择的周期；确认后撤销有效报名并关闭待处理范围扩大申请。发布锁定后不能撤销；报名截止后仍可撤销，但不能重新报名。重新报名会清空上次 QA 结论和整组交付镜像。

app_info 同步成功与报名版本跟随分别显示：仓库新版本会正常保存；报名截止后扩大测试范围时，报名暂用原锁定版本，应用标题下持续提示「待 QA 审批」及扩大项。QA 拒绝后仍提示未获同意；已发布周期的历史版本差异不作此提醒。手动、RM 全量和每日自动同步共用这套规则，钉钉范围扩大通知需启用对应通知渠道。真实同步失败另以红色提示显示，切换详情 tab 后保留。

其他页面：开发 WIKI，以及「AI助手」页签下的五个子页签 Jenkins 失败查询、CICD 助手（旧版 V1 只保留直达地址）、JIRA agent、周报、MCP，各有自己的地址与权限。完整导航见 [routeConfig.ts](web/src/routes/routeConfig.ts)。

## 月度发布怎么运行

1. RM 每月新建一个版本：版本名手工填写；报名截止默认当月 1 日，QA 截止默认当月最后一天减 5（如 10/26、11/25），发布日默认当月最后一天，均可修改。新版本初始为空，上个版本的当月环境复制过来作为起点。
2. 报名截止前，RM 维护当月环境（组件/版本）和基础镜像；报名记录包含基础镜像、目标版本、计划 feature 和测试范围（取消勾选即不测），仅 CICD 运行中的 App 可报名。App 详情当前不显示报名编辑页签。
3. app_info 只来自仓库：每天（`APP_INFO_SYNC_TIME`，默认 02:00）仅检查处于「报名截止后、QA 截止前」的周期中有有效报名、且 CICD 运行中的 App，同一 App 跨周期报名只检查一次。报名未截止、QA 已截止、已发布锁定、已撤回或未报名都不触发每日 Git 拉取；每个 App 开始同步前重新检查窗口，避免批次执行过程中越过 QA 截止后继续拉取。分支头变化时取回 `app_info.json`，内容变化才存为新版本并重建组件依赖（`app_dependency` 字段）。扩大范围仍需 QA 审批。RM 全量同步仍覆盖全部运行中的 App，单 App 手动同步仍可使用。RM 在「周期管理 → app_info 同步状态」查看失败；不再手工上传。
4. 每条报名锁定一个 app_info 版本。报名截止前跟随最新版本；截止后不扩大范围的变化自动跟进（记审计），扩大范围（新增芯片、测试项、已有测试新增芯片、新增构建）则保持原版本并自动生成范围扩大申请，钉钉通知 QA。QA 同意后条目锁定到新版本；拒绝后同样的扩大不再重复申请。
5. 报名截止后，环境冻结，测试范围只能删减；Owner 发布前把计划 feature 更新为实际交付和已知问题。撤回报名在发布前任何阶段都允许。
6. QA 填结论（四个取值沿用）、备注，并在启用 app_build 的各构建行填写包含 digest 的完整交付镜像名，可上传日志并用 AI 分析（建议需 QA 保存才生效）。保存时校验构建归属和 digest 格式，并用完整仓库路径和 digest 实时查询 Harbor：镜像必须存在，OS 和 CPU 架构均以实际 tags 校验，至少一条 tag 匹配锁定 app_info 生成的完整构建 tag（含 OS、CPU 架构、版本及 Python/PyTorch 配置，允许 HPC 版本或 commit 以及末尾构建号）。release/dbg 按镜像名区分（分别为 `*-maca` / `*-maca-dbg`），须与所选构建一致，digest 也必须存在于该类型对应的仓库中。Python/PyTorch 标签须与所选构建完全一致，且必须在同一条实际 tag 中同时匹配，不能跨 tags 拼接。不使用 Harbor 的 architecture/os 元数据，也不信任输入引用中自带的 tag；缺少可用 tags、平台不符或 Harbor 查询失败均报错，整批不保存。单次保存内对相同镜像去重查询，不跨请求缓存；网络查询在写事务之外，写入前重新检查 QA/镜像/锁定版本及发布状态，拒绝并发覆盖。重新提交的未指定构建旧镜像必须先改填到明确构建行；旧标量写接口不能新增或修改非空交付字段以绕过校验。发布冻结整组交付镜像。旧单镜像数据保留为「未指定构建」，可移除并改填到正确构建行，不根据 digest 猜测 OS/架构。
7. QA 截止后 RM 发布：`qa_passed`、`has_issues`（备注进已知问题）记为已发布并写入正式版；`not_checked`、`cannot_release` 记为本期发布失败，App 保持原正式版。发布同时生成 final 文档并锁定；RM 可填写原因解锁更正，解锁会删除本期正式版记录、封存清单和 final 文档。

周期管理的基础镜像每行填写「完整镜像引用」和「说明」。历史查询和基础镜像均可直接粘贴 `docker pull 镜像引用`，输入时自动移除命令前缀，后端也会统一清理。支持 `仓库/镜像:tag` 和 `仓库/镜像@sha256:…`（64 位十六进制 digest，也可同时含 tag）。后端保存时拆分地址与 digest，名称取地址末段并保留 tag；已有分栏记录在界面合成为完整引用，存储字段及报名、查询用途保留。RM 可维护到发布前，发布后只读。

```mermaid
flowchart LR
    A[报名中] -->|报名截止| B[进行中]
    B -->|QA 截止| C[QA 已截止]
    C -->|RM 发布| D[已发布]
    D -->|RM 解锁更正| C
```

阶段由 [phases.py](app/domain/phases.py) 按日期和发布标志计算，各阶段允许的操作也只在那里定义。

## CICD 状态与发布参与

两件事分开记录、互不自动触发：

| 事实 | 存放 | 何时改变 |
| --- | --- | --- |
| CICD 运行状态 | `apps.cicd_status`：`running` / `stopped` | 只在启动/停止申请生效时（RM 批准，或派给 SPD 后交付） |
| 发布参与 | 某版本里有没有未撤回的报名 `entries` | Owner 报名、撤回 |

- 报名要求 App 为 `running`，且没有未处理完的停止申请；App 在任何未发布版本里有报名时不能提交停止申请（先撤回）。同一 App 同时最多一个未处理完的启停申请。
- 新 App 先提交新建申请，申请生效时才建立 App 记录，状态为 `running`，随后自动同步 app_info。
- `stopped` 的 App 视为停止维护：保留历史正式版和 digest 查询，不进累计手册和兼容验证清单，不做 app_info 自动同步。
- 审批可立即应用，也可派发 SPD，等交付确认再应用。SPD 可以退回；RM 可以重新派发、直接生效，或填写原因后拒绝退回申请。配置编辑与启停入口在 App 工作台的 CICD tab。

实现入口：[cycle_service.py](app/services/cycle_service.py)、[catalog_service.py](app/services/catalog_service.py)、[cicd_service.py](app/services/cicd_service.py)、[identity.py](app/identity.py)。

## 文档产物与查询

- 发布说明只列当月发布的 App；使用手册和 AI4Sci 手册覆盖所有运行中 App 的当前正式版，每月更新。手册资料（描述、文档字段、测试说明、社区信息）是可选的，在 App 工作台「资料」维护。
- 「QA → 兼容性验证」（`/dependencies`）合并原「兼容性测试基线」与「正式版兼容验证」。每个运行中 App 取最近一次有效成功发布，保留迁移历史回查；后续失败、未报名、撤回不覆盖此前成功版本。默认每个 App 一行，显示 Owner、App 版本、来源 HPC 周期、依赖组件及镜像/测试项/命令数量；展开查看各构建的 OS、CPU 架构、GPU 芯片、Python/PyTorch、release/dbg、完整 digest 镜像、该次发布 app_info 的 commit id 与完整命令文本。commit id 显示前 12 位，悬停查看完整值；筛选和 CSV 使用完整值，快照缺失时留空，不回退到开发版或镜像构建 commit。
- 兼容性验证支持组件、CPU 架构、OS、GPU 芯片、Python/PyTorch 配置与关键词筛选，CSV 导出与当前筛选一致，包含没有关联测试的镜像。组件及 PyTorch + Python 组合来自所选成功发布的同一 app_info 快照，不读取开发态依赖；组合不跨构建拼接。未声明依赖、依赖为空或无有效发布快照的 App 单列「范围待确认」，选择组件时仍保留，导出明确标注。从未发布也列出；缺少交付镜像保留命令模板，旧镜像不猜测平台，快照缺失不借用开发态命令。新发布直接读取封存命令；未封存的旧发布按历史配置与当前规则还原，并在明细注明「根据历史配置生成（未在发布时封存）」，CSV 标注清单来源。
- 成功发布时，在同一事务内为每条 `official_versions` 写入版本化 `test_manifest_json`，封存发布记录 ID、App/周期身份与环境、发布人/时间、完整 app_info 与 commit、组件依赖、启用构建、纳入/排除测试项、镜像组、完整 Docker 命令及原始 App 级 QA 结论/备注。缺少有效报名快照则整次发布回滚；缺失交付镜像允许保留并标注模板。兼容性清单及导出、按 App/已登记 digest 的历史接口优先读取封存内容，后续生成规则、开发配置或报名数据变化不改写已封存命令和 QA。同一 App/周期有数据库唯一约束；解锁删除该周期记录与清单，重新发布只保存替换后的最终清单；这不代表每个平台均已单独测试通过。
- 旧库升级新增可空清单列，清理已撤销、同周期重复及未锁定周期的旧正式版记录，保留每个 App/周期最新有效记录后移除撤销字段并建立唯一约束；不回填或伪造旧发布的封存时间。旧记录保留历史配置还原，缺少有效历史快照时明确提示；存在但版本不支持或损坏的清单报错，不静默重算。App 详情 `/api/apps/detail` 的 `official_versions[].test_manifest` 可读取各周期最终保留的完整清单。历史查询页面的 Harbor/Gerrit 镜像还原流程仍独立，统一发布详情页面留待后续。
- 「最新正式版」统一按发布周期的 `release_date` 选择，同日再按周期创建时间及创建顺序确定先后，不按版本名字符串、正式版记录 ID 或重新发布时间排序。更正旧周期不会覆盖已有较新周期；最新周期解锁时回退到此前成功周期，重新发布后恢复。周期列表、App 发布历史、兼容性清单与旧迁移记录回查共用这一顺序。
- 「开发态依赖」作为兼容性验证页的次级参考入口，仅列最新 app_info 的依赖、版本与 commit，不提供正式版镜像、命令或测试清单导出。切换入口重置筛选；显式刷新，不新增自动轮询。合并页面共用 `GET /api/lookup/latest-running` 的基线数据，原 `/api/dependencies` 正式版查询及 CSV 接口保留兼容。

- 「QA → 历史查询」默认展示「发布app列表」，下拉菜单仅列已发布并锁定的版本，默认最新版本。选中后自动列出该版本发布成功的 App、当期 App 版本、包含 digest 的完整交付镜像名、QA 结论和备注；可按 App、镜像或 QA 结果筛选。历史记录缺少完整镜像信息时明确标注，已停止维护的 App 仍保留在历史发布列表。解锁的版本不再提供查询，重新发布后按更正结果展示。「按 App」界面已移除，旧接口保留兼容。
- 历史查询的「按镜像 digest」输入完整镜像引用（`harbor.sh.mxcr.io/项目/镜像@sha256:…`），优先按仓库地址与 digest 匹配本地有效发布记录。同一镜像用于多个周期时列出全部记录，默认最新周期，可切换查看各次发布的完整镜像组、纳入/排除测试项、原发布 App 级 QA、app_info commit 与原始命令；封存清单直接读取，不访问 Harbor/Gerrit 或重新生成命令。本地未封存旧发布明确标注历史配置还原，快照缺失则提示无法读取，不用外部结果替换已有发布记录。仅当没有匹配发布记录时，才从 Harbor tags 定位 commit，再从 Gerrit 还原该镜像的命令，并标为「根据历史配置生成」。app_info 来源链接跟随所选发布读取本地快照；外部还原结果仍按原 commit 从 Gerrit 读取。查询均只读，停止 App 的有效历史发布仍可查；解锁后的发布记录不再列入。

测试范围按 `测试项 → supported_chip 的构建别名 → app_build` 连接，展示 OS、CPU 架构和各平台的芯片；测试声明与构建声明的芯片取交集，禁用构建、未引用的平台及不匹配的芯片不生成命令。App 详情、QA 测试范围、测试命令表/导出和历史镜像查询共用该规则。界面中的 CPU 架构简写为 x86/ARM，不附加构建别名和架构括号；QA 测试命令表/CSV 用 `commit_id` 展示报名锁定 app_info 的原始 commit，构建别名仅在含有 OS/架构以外的信息时保留。历史镜像查询展示该镜像匹配构建的 X86/ARM 芯片总览。报名截止后，即使测试名、芯片名不变，为已有测试新增 OS/架构/构建配置上的芯片组合，也需 QA 同意后才跟进。

QA 内容区的六个平级页签为「QA log」「本期发布app」「测试命令」「开发者社区产物」「历史查询」「兼容性验证」；历史查询和兼容性验证向 RM、Owner、QA 开放。

旧的按周期快照数据由一次性迁移（[monthly_migration.py](app/db/monthly_migration.py)）转换：只有最近一个已锁定版本写入正式版；迁移前须在旧网站处理完所有未完成的 CICD 申请。

## JIRA agent（数字员工）

JIRA agent 是按组配置的数字员工，第一阶段处理 HPC 组的 Bug 类工单：收集信息、分类、判断归属、复现、分析，属于本组且能修复时完成修复与验证。部署、配置和迁移步骤见 [JIRA agent 部署与使用](docs/jira-agent.md)，开发约束见 [jira-agent-dev 技能](.agents/skills/jira-agent-dev/SKILL.md)。

| 位置 | 职责 |
| --- | --- |
| 服务器 A（本网站） | 对话、消息、文件、排队、执行记录、JIRA 评论；通过 WebSocket + token 连接 B |
| 服务器 B（组的 `codex app-server`） | agent 本体；本组知识包（AGENTS.md、skills）、Codex 登录凭证、访问 C/D/E 的 SSH 凭证。B 上不部署适配服务 |
| 服务器 C/D/E | 复现、构建、测试环境，由 B 上的执行用户登录。交单时选择：agent 从 RM 维护的系统机器列表中自选，或用户指定多台 `user@host`（逐机上传公钥，统一检查 SSH 和共享数据，异常确认后提交） |

要点：只有当前 JIRA assignee 或 RM 能交单和发消息；工单收到 Codex cybersecurity risk warning 后，非 RM 操作锁定，只能由 RM 处理（owner 仍可查看）。锁定跨对话和 assignee 变更保留，删除历史对话不会解锁，部署时会补录历史错误/告警；agent **只在 JIRA 追加评论**，不改 assignee、状态或代码；每组并发由网站排队控制，网站重启不丢轮次。RM 可在“知识学习”中从 agent 处理过的 Jira 或添加的历史 Jira 批量学习，审核提案后人工更新独立 `jira_agent_skill` 仓库，分别登记入库和部署。知识学习见 [docs/jira-learning.md](docs/jira-learning.md)。完整的找单、对话、排队、学习与恢复规则见 [docs/jira-agent.md](docs/jira-agent.md)。

RM 可在新建对话时选择服务商（例如 OpenAI / DeepSeek）、模型和 effort；**同一对话固定服务商，切换服务商必须新建对话**。续办允许调整同一服务商的模型和 effort，排队或运行中不可修改。默认仍使用 B 的默认服务商；默认模型从 app-server 获取，额外服务商从组配置 `MODEL_CATALOGS` 指定的 B 侧模型目录读取，Key 仅保留在 B。模型和 effort 在对话内持续保留，新建对话恢复默认。分析结果不自动发布；用户在评论框点击“发送到 JIRA”，编辑正文并选择本轮产物后确认发送，编辑历史可查，已发送轮次禁止重复发送。每轮产物在可展开的回收状态栏中预览和打包下载。

实现入口：[jira_agent_service.py](app/services/jira_agent_service.py)（交单与对话规则）、[jira_agent_runner.py](app/services/jira_agent_runner.py)（排队与执行）、[codex_app_server.py](app/integrations/codex_app_server.py)（通用 Codex 协议客户端）、[jira_agent.py](app/domain/jira_agent.py)（结论 schema、提示词、评论格式）、[JiraAgentPage.tsx](web/src/features/jiraAgent/JiraAgentPage.tsx)，B 侧知识包与启动脚本在 [deploy/jira-agent/](deploy/jira-agent/)。

后续阶段（尚未实现）：按 component 路由到其他组的数字员工、适配类与优化类任务。

## 周报

现阶段是周报的 JIRA 更新检查；以后由 JIRA 数字员工按约定格式的 JIRA 更新自动生成周报，也放在这个页签。

RM 每周上传 PDE HPC Project Plan 周报（.xlsx，每人一页，A 列日期、B 列周报），网站给出每人的周报与 JIRA 问题清单，按 ISO 周留档，可导出 Markdown / CSV。规则沿用原本地脚本 `weekly_report_jira_check.py`：

| 问题 | 判定 | 谁来判 |
| --- | --- | --- |
| 周报日期异常 | 最上面一条的年份早于 2025、晚于今天 7 天以上，或不是本页最新日期 | 代码 |
| 周报未更新 | 最上面日期比报告日期早 3 天以上，或本周更新为空 | 代码 |
| 未写JIRA / JIRA ID不完整 | 工作项没有 JIRA ID；只写了数字、缺项目前缀 | LLM 拆工作项 |
| JIRA不存在 / JIRA无权限 | 接口返回 404 / 403（`[jira]` 的 token 无权浏览该项目） | 代码 |
| JIRA未更新 | 单子 assignee 是本人，窗口内本人没改字段、没写 comment / worklog、也不是新建；Closed / Resolved 照查；一个工作项挂多个本人的单，更新其一即可 | 代码 |
| 更新与周报不符 / 部分相符 | 本人在 JIRA 上的更新与周报描述对不上；拿不准按一致 | LLM |

要点：

- 报告日期默认取各页最上面日期的众数，可在分析前改；周由报告日期的 ISO 周决定（如 `2026-W41`）。日期有问题的页照常分析。
- 周报页 ↔ 账号只来自 RM 在页面“手工对应”里维护的表（页名不分大小写），可按“页名,JIRA 用户名[,不检查]”批量导入。Project、Template 等不是个人的页和不检查的人（如组长）也在这里登记并勾选“不检查”。表里没有的页提示“找不到对应的用户”，只查是否写了 JIRA ID。
- assignee 是别人的单子不查更新。
- 每周只留最后一版：同一周再分析会覆盖，分析失败或被 RM 中止都不动旧版；上传和分析各写一条 `audit`。分析中可点“中止分析”：立即断开进行中的 LLM 请求并停止（正在进行的 JIRA 查询不等待，几秒内自行结束），上传的周报保留到过期，可直接重新分析。分析在服务器后台运行，刷新页面或切走不影响；RM 回到周报页会自动接上正在进行的分析（可看进度、中止）。同一周同时只能有一个分析在跑。
- 周报文件不落盘：只在内存里解析，分析完成或上传 1 小时内没有分析就丢弃，库里只记文件名和大小。
- 网站对 JIRA 只读（GET issue），不评论、不改单，也不自动通知任何人。
- RM 和 Owner 能看各周结果；上传、分析和成员管理需要 `weekly-report.run` 能力（RM）。

实现入口：[weekly_report.py](app/domain/weekly_report.py)（解析、日期规则、问题判定）、[weekly_report_prompts.py](app/domain/weekly_report_prompts.py)（两个 prompt）、[weekly_report_service.py](app/services/weekly_report_service.py)（一次检查、入库、导出）、[weekly_report_jobs.py](app/services/weekly_report_jobs.py)（内存中的上传与后台作业）、[WeeklyReportPage.tsx](web/src/features/weeklyReport/WeeklyReportPage.tsx)。没有单独的配置节：JIRA 用 `[jira]`，大模型用 `[llm]`；JIRA 更新窗口默认 192 小时，在上传周报后的分析窗口里可改。

## MCP（AI助手 → MCP）

网站在 `POST /mcp` 提供 MCP Streamable HTTP 服务（只返回 JSON，不开 SSE），供 Claude Code、Codex 等客户端连接。页面地址是 `/mcp-console`（不能用 `/mcp`，否则浏览器刷新会打到 MCP 接口；浏览器直接访问 `/mcp` 会被跳转到页面）。「接入配置」生成个人 token（只显示一次，库里只存 SHA-256），并给出 Claude Code / Codex 的接入命令；「使用统计」展示调用量、失败率、按工具/分类/用户/token/客户端/日期的统计及调用日志，可按 token 筛选（只显示 token 名称和前缀）。

- 每人只有一个有效 token：生成新的会吊销旧的（鉴权也只认每人最新的一个）。网页「我的 token」只显示前几位，可重新生成或吊销。
- MCP 接入配置页面统一生成 HTTP 的一键安装命令、各客户端命令和手动配置地址，沿用浏览器当前域名及非默认端口，不使用 `[site] PUBLIC_BASE_URL` 覆盖，也不提供协议选择；网站继续通过原地址访问。服务器须开放对应 HTTP 入口，容器无需配置网站证书；HTTP 会明文传输账号密码和 token，仅用于受控内网。安装脚本按下载请求的协议生成后续账号请求及 MCP 地址，已安装客户端可复用原 token 更新配置，无需卸载。
- HTTPS 由 Nginx 等反向代理终止时，代理须覆盖设置 `proxy_set_header Host $http_host;` 和 `proxy_set_header X-Forwarded-Proto $scheme;`。MCP 返回的脚本、接入地址和附件传输链接会据此保留 HTTPS，避免登录 POST 请求误发到 HTTP 后遇到 301；不会使用 `X-Forwarded-Host` 替换目标主机，也不会将 HTTPS 降级到 HTTP。
- 一键配置：`bash <(curl -fsSL <网站>/api/mcp/setup.sh)`，菜单为 一键配置本机 / 查询 token / 重新生成 / 仅输出配置方法 / 卸载本机配置（也可带参数 `configure|status|regenerate|print|uninstall`）。经 `POST /api/mcp/account`（action=status|regenerate）用域账号密码操作，与登录页同一认证，首次登录同样按 LDAP 组建用户，但不建网站会话；同一用户名 10 分钟内失败 5 次后锁定。脚本直接改 `~/.claude.json` 与 `~/.codex/config.toml`（先备份为 `*.bak-release-mcp`），不依赖 claude/codex 命令，只装 VS Code 扩展也可用；第二台机器复用本机配置里或粘贴的已有 token，选择重新生成则旧 token 在所有机器失效。
- 按客户端配置已有 token：页面“手动配置”按 Claude Code、Codex、其他 harness 折叠展示。Claude/Codex 的命令使用 `MCP_CLIENTS=claude|codex bash <(curl -fsSL <网站>/api/mcp/setup.sh) use-token`，校验已有 token 后只更新指定客户端；未提供 `MCP_TOKEN` 时安全提示粘贴，不创建或重置 token。脚本仅完成一次性配置，客户端仍直接通过 HTTP 连接网站，无需安装本地转发服务。
- 安装结果先显示 MCP URL，再显示 token 提示；生成 token 时提示通过选项 4 查看全文，客户端的“已配置”状态按列对齐。安装结束不追加其他客户端的接入说明与文档链接，这些内容保留在选项 4 中。
- 确认等待期间每 15 秒发送 SSE 注释保活，并用 `X-Accel-Buffering: no` 禁用代理缓冲，避免等待用户输入时连接空闲断开、客户端只收到 `Transport closed`。用户名不匹配、拒绝、取消和超时均通过工具错误返回具体原因及 `structuredContent`（`status=not_executed`、`reason`、`retryable=false`），要求 agent 告知用户，不自动重试或代填用户名；再次尝试须由用户明确发起。确认失败日志保留目标工单号。真正的网络故障仍可能导致结果无法送达，不能仅凭断线判断 JIRA 是否已写入。
- 写操作的用户确认：服务端统一按 `read_only=False` 且 `destructive=True` 决定是否要求用户名确认，不依赖工具名或是否提供预览函数；只读或非 destructive 工具无需确认。客户端支持 MCP form elicitation 时，`tools/call` 以 SSE 发出 `elicitation/create`（300 秒超时）。`jira_add_comment` 要求用户手工输入域账号用户名，不预填，去除首尾空白并忽略大小写后与 token 所属账号完整匹配才发送；空值、类型错误、不匹配、拒绝、取消、超时均不发送，不支持 form elicitation 的客户端不能发评论。协议仍使用 `action=accept` 表示提交表单，但仅 accept 不足以发布。评论先通过与网站 JIRA agent 共用的 `app/domain/jira_format.py` 处理格式（当前为 JIRA wiki 行内代码间距修正，保留代码块，不做通用 Markdown 转换），客户端调用前先在对话中展示拟发送的完整评论；确认弹窗对短评论展示全文，长评论只展示最多 240 字符、6 行的摘要并明确标注截断，输入框标题直接提示输入本人域账号用户名，发布时仍使用完整正文并追加署名；空正文和超长内容在弹窗前拒绝。`jira_upload_attachment` 不请求 elicitation 确认，直接返回上传命令；签发票据和实际上传时仍核验权限。日志 `extra_json.confirm` 记录 `username_matched` / `username_mismatch` / `client_cannot_confirm` / `decline` / `cancel` / `timeout`。待确认请求在进程内，须单 worker；所有写操作要求有效、未结束且属于当前 token 的会话，批量调用被拒绝；确认回复绑定发起 token 和会话，提交前再次核验 token 与当前角色。
- 其他 harness 的配置文档链接和「能否弹确认」的核实结果维护在 [mcp_harnesses.json](shared/mcp_harnesses.json)（`confirm` 为 form/none/unknown），页面「手动配置」和脚本的配置/输出结果共用这份列表，对不支持的标注「不推荐」。
- 鉴权：`Authorization: Bearer rsmcp_...`。每次请求实时读取 token 对应用户的当前角色，须在 `shared/access_control.json` 的 `mcp` 页签内；吊销或角色变更在后续鉴权时生效，附件下载、上传及确认后的提交都会重新核验；上传还会重新检查当前 assignee 和风险锁。统计和日志 RM（`mcp.logs.view.all`）看全部，其他人只看自己。
- 工具：`jira_search`、`jira_pull_issue`（返回 issue.md 内容和附件下载命令，由客户端写入本地，默认 `jira/<KEY>/`；附件链接 30 分钟内有效，票据存在进程内，所以网站须保持单 worker）、`jira_add_comment`（只限当前 assignee 或 RM，沿用 JIRA agent 的风险锁；以 `[jira]` 账号发出，正文末尾注明网站用户和客户端）、`jira_upload_attachment`（同样只限 assignee 或 RM；MCP 读不到本机文件，所以返回一次性的 `curl --data-binary @文件 PUT /api/mcp/uploads/<票据>` 命令，由客户端执行，上传时再核对一次权限，单文件 50 MB，成功一次即失效、失败可重试，记为 `upload` 日志）。
- 评论和上传附件无需先调用 `jira_pull_issue`：已知工单编号且评论所需信息齐全时，按格式规范准备并展示正文后直接调用 `jira_add_comment`；已知工单编号和本地文件路径时，直接调用 `jira_upload_attachment` 并执行返回的上传命令。工单存在性和写权限由服务端实时检查，仅在任务需要补充工单上下文、历史评论或查找已有附件时才拉取。此调用策略通过服务端 instructions 和工具描述下发；发布后，客户端正常重连并重新获取 instructions 和工具列表即可获得，无需重新安装接入脚本，已缓存描述的会话需刷新连接。
- Jenkins 失败查询：`jenkins_search_failures` 支持日期、关键词、任务、应用、责任人、阶段、芯片等筛选与分页（默认每页 20，最多 100）；`jenkins_get_failure` 按记录 ID 返回后端保存的完整详情，包括已有的错误原因、关键错误片段、处理建议、责任变更历史及测试汇总。缺失字段不代表已诊断成功，也不等同于完整 console 日志。复用 `[cicd_agent]` 配置和网页查询权限，回收站仅 RM 可读；均为只读工具，不触发用户名确认、不重跑构建。无需更新客户端脚本，客户端重新发现工具后即可使用。
- Jenkins 原始错误：`jenkins_get_build_errors` 接受失败记录中的 `build_url`，默认读取构建信息和失败 JUnit case 的错误详情、堆栈及标准输出；`source=console` 按 `next_start` 分段读取原始控制台日志，`has_more` 表示仍有未读内容，`building` 仅表示构建仍在运行。`source=artifact` 配合 `artifact_path` 可读取构建归档列表中的自定义 JSON 测试报告。未发布测试报告时明确标记缺失，不宣称测试通过，也不把构建 SUCCESS 当作所有 case 通过。凭据配置在 `[cicd_agent]` 的 `JENKINS_BASE_URL`、`JENKINS_USERNAME`、`JENKINS_API_TOKEN`、`JENKINS_TIMEOUT_SECONDS`，每次调用现读；只允许配置的 Jenkins 构建地址，禁止跟随重定向，仅发 GET；TLS 使用系统受信 CA，支持已安装内部 CA 的部署环境。
- 文本附件：`jira_read_attachment(issue_key, attachment_id)` 在服务端核对附件归属后返回文本，下载上限 2 MiB、每页最多 500 行 / 24000 字符。默认识别 UTF-8 / 带 BOM 的 UTF-16，可显式指定 GB18030；二进制或超限附件仍使用下载功能。用返回的 `next_start_line`、`next_start_column` 和 `expected_sha256` 续读，超长行不会静默丢失。
- Jenkins 直接发现：`jenkins_list_jobs` 支持任务名称过滤、目录和分页；`jenkins_list_builds` 返回任务构建及链接，日期为北京时间的构建启动日期（首尾日均包含）。这两个工具直接访问 Jenkins，不依赖 CICD Agent。过滤应用于当前扫描页，空结果仍可能有 `next_offset`；目录通过 `parent_url` 逐层查询，不做无限递归。保留所有构建状态，不能据 SUCCESS 判定 case 全部通过。
- 按 case 取证：现有 `jenkins_get_build_errors` 增加 `case_name`，结合 JUnit、自定义 `cicd-test-results/*.json` 和日志返回指定 case 的结果、错误上下文、来源位置。单次最多扫描 1 MiB 日志、10 份自定义报告；`console.has_more` 为真时用 `console.next_start` 继续，可改 `source=console` 避免重复拉取报告。未找到仅代表当前扫描范围内未定位；纯文本提及和明确 case 起始标记分开标注，并行日志仍可能交错。原始日志分页读取保留。
- 跨系统关联：JIRA 拉取、失败记录、构建及 case 查询补充 `correlations`，包含明确记录的工单/构建链接、应用版本、镜像 tag/digest、仓库等字段，并附来源和 JSON 字段路径；JIRA 原生 issue link 与正文提及分别标注。没有新增聚合工具，不按名称或时间猜测同一故障；无关联字段不等于已证明不存在关联。实际数据是否完整仍取决于上游记录。
- HPC 专家方法：`skill_load`（只读，所有可用 MCP 的角色都能调）把 skill 的方法正文下发给客户端，先给主方法，`page=build|diagnosis` 按需再取参考页；当前提供 `cu-bridge` 和 `jira-format`；后者直接读取已有 `jira-issue-manager` skill 的 `references/jira-format.md`，仅含排版规则。读的是网站随附的知识包副本 `deploy/jira-agent/hpc/`（打包副本，不是正式发布源 `jira_agent_skill` 仓库）。服务端 `instructions` 里提示先调用它，Claude Code 与 Codex ≥ 0.167.1 会自动加载（Codex 0.166 不读服务端 instructions）。只能下发方法文本，不含可执行脚本。
- 扩展：在新模块里 `register(Tool(...))` 并在 `mcp_tools.load_tools()` 导入。工具声明 `category`、`read_only`、`destructive`、可选 `roles` 与 `log_args`（脱敏）；`confirm` 仅定制确认文案，不提供时使用工具名称和参数的通用预览，不会跳过 destructive 写操作的用户名确认。返回的 `ToolLog` 写入通用日志列 `target_type/target/extra_json`，无需改表即可进入统计页。
- 代码：[mcp.py](app/api/routers/mcp.py)、[mcp_protocol.py](app/services/mcp_protocol.py)、[mcp_tools.py](app/services/mcp_tools.py)、[mcp_jira_tools.py](app/services/mcp_jira_tools.py)、[mcp_skill_tools.py](app/services/mcp_skill_tools.py)、[mcp_service.py](app/services/mcp_service.py)；表 `mcp_tokens`、`mcp_sessions`、`mcp_call_logs`（主库）。日志目前不自动清理。

## 数据与文档产物

| 数据 | 存储与边界 |
| --- | --- |
| App | `apps`：全局仓库身份、别名和 CICD 配置 |
| 周期快照 | `snapshots`：以 `(release_id, app_id)` 为键，业务字段存于 `data_json` |
| 发布周期、时间线 | `releases` 与 `release_schedule` 是两张独立表；当前没有周期外键将时间线条目和 release 强制绑定 |
| 申请和交付 | `cicd_task_requests`；申请记录与 App 关联，部分跨周期信息存于 payload |
| QA 日志 | `qa_logs.content` 为 BLOB，每个 release 当前只保留一份，重新上传覆盖；旧 `storage_path` 用于兼容迁移 |
| 发布产物 | `artifacts` 以 `(release_id, kind)` 为键；同类草稿重新生成会覆盖，不是历史版本库 |
| 用户、会话、审计 | `users`、`sessions`、`audit`；这些数据不应整体导出到文档或测试夹具 |
| WIKI | `wiki_articles`、`wiki_images`，图片存于数据库 |
| 周报 | `weekly_report_weeks`（每个 ISO 周一行，重新分析覆盖）、`weekly_report_findings`、`weekly_report_members`、`weekly_report_items`（LLM 拆分结果缓存）；周报 xlsx 不保存 |
| CICD 助手会话 | 独立 SQLite 库，由 `ASSISTANT_DATABASE_URL` 指定；不写入主业务库 |
| JIRA agent | 独立 SQLite 库 `JIRA_AGENT_DATABASE_URL`（对话、轮次、时间线、文件索引）；上传文件与拉回的产物在 `JIRA_AGENT_DATA_DIR`；Codex thread 与工作目录在服务器 B |

常规生成的四类产物为 `release_note`、`manual`、`ai4sci`、`data`。Manager Review CSV 单独生成；测试范围 CSV 按需导出，不是 artifacts 的一个 kind。最终锁定生成上述四类 final 产物，不会自动把 Manager Review 升格为最终审批记录。

生成文档是一个时间点的结果，不能假定后续编辑已同步到旧草稿。C500/X201 发布说明、wiki 发布页和 AI4Sci 用户手册的更新使用 [文档整合技能](.agents/skills/c500-manual-integrator/SKILL.md)，先指定目标 release，避免误取另一个周期的数据。

## 代码结构与事实源

```text
app/api/routers/       HTTP、依赖和权限入口
app/services/          业务编排与事务；部分历史 SQL 尚未下沉
app/domain/            阶段、决策、门槛、权限与解析规则
app/repositories/      数据访问
app/db/                主库与助手库连接、建表和兼容处理
app/integrations/      LDAP / Gerrit / Jira / LLM / Codex app-server 集成
web/src/               React 页面、HTTP 客户端、查询缓存和 UI 状态
shared/                跨前后端权限、词表和 Gerrit 路径
deploy/jira-agent/     JIRA agent 服务器 B 的启动脚本与 HPC 知识包
deploy/jira-evaluation/ 测试初评 skill，随网站一起部署
.agents/skills/        开发、JIRA agent 开发与 C500/X201 文档工作流（.claude/skills 为指向它的软链接）
```

- 权限：`shared/access_control.json` → Python `domain/permissions.py` / React `lib/accessControl.ts`。
- 稳定词表和字段描述：`shared/domain_metadata.json` → 后端 `domain/shared_metadata.py` 及前端对应 lib。
- Gerrit 路径：`shared/integrations.json` → `app/config.py` / 前端 `lib/git.ts`；浏览器只取得它所需的非敏感配置。
- 运行时服务配置：`release_system.conf` → `app/runtime_config.py`，每次使用时现读。
- 时间：新写入为 `app/timeutil.py` 的北京时间无时区字符串；**旧库仍可能有 UTC ISO 数据**，不能对全部值统一加 8 小时。

`server.py`、`index.html`、`release_system/` 是旧实现与测试兼容参考，日常开发不修改，其行为也不能推翻当前实现。开发约束见 [release-system-dev 技能](.agents/skills/release-system-dev/SKILL.md)。

## 安装和本地开发

后端按 `pyproject.toml` 使用 Python 3.11+，依赖未锁定版本；前端依赖见 `web/package-lock.json`。

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cd web
npm ci
cd ..
```

开发实例使用独立数据库，下面启动一个空实例，再在界面导入测试数据：

```bash
export NO_PROXY=localhost,127.0.0.1
export no_proxy=localhost,127.0.0.1
export DB_PATH=/tmp/release-system-dev.db
export ADMIN_PASSWORD_FILE=/tmp/release-system-dev-admin.local
export ASSISTANT_DATABASE_URL=sqlite:////tmp/release-system-dev-assistant.db
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1
```

另一个终端从 `web/` 运行 `npm run dev`（5173，`/api` 代理到 8000），详见 [前端说明](web/README-web.md)。

首次初始化会创建 `app/domain/authn.py` 中的固定开发账号（没有仅开发环境启用的开关），部署前应处理。Admin 不存在时依次使用 `HPC_ADMIN_PASSWORD`、`ADMIN_PASSWORD_FILE` 指定的文件或随机口令。会话没有服务端过期策略。

生产形态由单个进程服务 API 和前端：在 `web/` 运行 `npm run build` 校验并打包内置测试用例、生成 `web_dist/`，再从仓库根启动 uvicorn。构建需要 Python 3；用例包位于 `app/resources/jira_test_cases/_packages/`，随 `app/` 一起部署。启用测试初评时，还需保留 `deploy/jira-evaluation/` 目录及其中的隐藏目录 `.agents/`。用例加载与维护见 [内置测试用例](docs/jira-agent.md#内置测试用例)。**保持单 worker**：QA 任务状态、JIRA agent 队列以及周报的上传和作业都在进程内。

## 集成配置

LDAP、JIRA、大模型、CICD Agent、网站对外地址和各组 JIRA 数字员工的配置统一放在根目录 `release_system.conf`（已 gitignore），分为 `[ldap]`、`[jira]`、`[llm]`、`[cicd_agent]`、`[proxy]`、`[site]`、`[jira_agent:<组名>]`、`[jira_learning:<组名>]`、`[jira_evaluation:<组名>]` 几节。大模型一节供 QA AI 分析和周报等所有功能共用，代码里统一经 [llm.py](app/integrations/llm.py) 的 `ask_json()` 调用；原来的 `[qa_llm]` 已改名为 `[llm]`，键名去掉 `QA_` 前缀，仍写旧节名时会提示如何改。复制 `release_system.conf.example` 起步，各键含义与必填规则见其中注释。配置在每次使用时现读，修改后即时生效。旧的 `ldap.conf` / `jira.conf` / `qa_llm.env` / `jira_agent.conf` 已不再读取，升级时把内容搬到对应节。

环境变量只放文件位置和少数开发参数（下表），由 `app/config.py` 的 `Settings` 从环境变量和根目录 `.env` 读取，改后需重启。前后端共用的约定在 `shared/*.json`；调优参数和固定业务规则（如派单 JIRA 的项目、Component、issue 类型和 ETA 字段）是代码常量。新增配置放在哪里见 [release-system-dev 技能](.agents/skills/release-system-dev/SKILL.md) 的“配置落点”。

| 配置入口 | 作用 |
| --- | --- |
| `DB_PATH` / `ADMIN_PASSWORD_FILE` | 主业务库与 Admin 初始口令文件 |
| `RUNTIME_CONF_PATH` | 上述统一配置文件路径，默认根目录 `release_system.conf` |
| `GERRIT_SSH_BASE_URL` | 覆盖共享的 Gerrit SSH origin；前端格式化也使用它时需重新构建 |
| `ASSISTANT_DATABASE_URL` | 助手会话库；当前仅支持 `sqlite:///` URL |
| `ASSISTANT_HISTORY_LIMIT` / `ASSISTANT_SUMMARY_*` | 助手上下文窗口及滚动摘要配置 |
| `JIRA_AGENT_DATABASE_URL` / `JIRA_AGENT_DATA_DIR` | JIRA agent 独立库与文件目录 |
| `JIRA_AGENT_RUNNER_ENABLED` | 是否在进程内运行 JIRA agent 队列 |
| `HPC_ADMIN_PASSWORD` | 首次创建 Admin 时使用的口令 |

## 只读检查、备份与验证

检查真实库用 SQLite URI `mode=ro` 和 `PRAGMA query_only=ON`。不要用 `app.db.connection.connect()` 或 `GET /api/state`，它们会建表、补默认账号或回写缺失项。

```python
import sqlite3
from pathlib import Path

uri = Path("release_system.db").resolve().as_uri() + "?mode=ro"
with sqlite3.connect(uri, uri=True) as conn:
    conn.execute("PRAGMA query_only=ON")
    print(conn.execute("SELECT COUNT(*) FROM apps").fetchone()[0])
```

在线备份用 SQLite backup API 或 `sqlite3 ... .backup`，不要直接复制活跃 WAL 数据库的主文件；主库、助手库、JIRA agent 库分别备份，备份不提交 Git。恢复、迁移和清理先在副本验证。

测试入口：后端 `python -m pytest -q`；前端 `npm run build`、`npm run lint`、`npm test`、`npm run test:e2e`。隔离参数与 E2E 端口见 [验证说明](.agents/skills/release-system-dev/references/verification.md)。

## 文档维护

README 负责产品、使用入口和运行方式；技能负责开发约束，`references/` 放按需读取的细节；部署手册与功能建议放 `docs/`。同一规则只写在一处，其他地方链接过去；不把一时的业务数量、特定模型名或机器路径写成永久要求。

## 钉钉通知

每种通知各用一个钉钉机器人，由 RM 在「周期管理 → 通知设置」页面逐项填写 `ACCESS_TOKEN`、加签 `SECRET` 和要 @ 的钉钉用户 ID，并可发送测试消息；保存后密钥只显示掩码，未配置或未启用的通知类型不发送。机器人须启用加签安全设置。通知类型：JIRA agent 轮次结束、Jenkins 失败数超阈值、测试范围扩大申请、范围扩大审批结果、app_info 同步失败汇总。`[proxy]` 的 `URL` 配置公共 HTTP/HTTPS 代理（例如 `http://proxy.sha-dc01.metax-tech.com:1080`），服务按需接入，目前钉钉和大模型（`[llm]`）使用，其他服务不自动切换；`LLM_BASE_URL` 是 `metax-tech.com`、`metax-internal.com` 或其子域名时大模型直连，不走代理；修改后下次发送或调用生效；留空或省略则直连，不读取环境代理变量。

- JIRA agent 每分钟检查完成、失败或意外中断的轮次，用工单号、本轮发起人的姓名和网页结论/操作标记发送一句话；姓名读取网站用户资料，缺失时回退显示该轮发起人的域账号。失败和意外中断附上简短原因（如 cybersecurity、model capacity、codex disconnected），未分类错误提示查看对话详情；交付不完整时明确提示，不报成功。主动取消不通知。首次启用只接收启用后结束的轮次，之后停机期间漏发的轮次会补发，每轮通知一次。
- Jenkins 在北京时间每天 10:00–20:00 的整点（包含 10:00 和 20:00，其他时段不查询、不告警）查询一次网页同源的 `/api/v1/failures/summary`，按责任组聚合，北京时间开始/结束日期均为当天，不附加用户筛选。所有 Code Owner 总错误数 **>20**、DevOps（SPD）总错误数 **>30** 时发送一条包含两项数量的通知；当天发送成功后不再发送，次日重新计算。首次启动不立即补查，等待下一个整点；允许时段内整点启动则立即查询。
- 成功回执保存在 JIRA agent 数据库的 `notification_receipts`，重启不会重复告警；失败在后续轮询重试，不影响 JIRA 处理。远端已接收但本地尚未记录时发生断网/退出，重试仍可能重复一条消息（钉钉没有幂等键）。首次启用前的旧轮次不补发。
- RM 登录后可访问 `/api/notifications/status` 查看是否启用、最近检查时间及脱敏错误；单 worker 运行，与 JIRA runner 的部署要求一致。Jenkins 未配置时跳过其检查。
