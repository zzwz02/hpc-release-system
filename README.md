# HPC App 发布信息协作系统

用于管理 HPC / AI4Sci App 的每月统一发布：Owner 自主报名、Gerrit app_info 自动同步、QA 结论与交付镜像 digest、CICD 申请与交付，以及发布文档产物。当前运行入口是 `app/main.py`（FastAPI）和 `web/src/main.tsx`（React）。

本文说明当前实现，规则以代码为准；拟建功能见 [发布流程改进建议](docs/release-process-roadmap.md)。仓库根的数据库可能是真实业务库，开发、测试和导出按下文隔离。

## 从哪里开始

| 使用者 | 日常入口与职责 |
| --- | --- |
| RM | 周期管理：新建月度版本、填当月环境和基础镜像、发布与解锁、app_info 同步状态、通知设置；CICD：审批、交付处理；发布文档：导出和检查 |
| Owner | App 工作台：为单个或本人名下多个运行中 App 报名、编辑分类和可选手册资料；提交新增 App、CICD 配置和启停申请 |
| QA | QA：处理测试范围扩大申请、查看测试范围与命令、上传 QA log、查看失败汇总、填写结论、交付镜像和各 App 的 QA comment |
| SPD | CICD：查看交付任务、确认交付或退回；Jenkins 失败查询和 CICD 助手辅助排查 |
| Guest | 查看总览、App 和 QA 等矩阵允许的页面；MCP 仅可读取 MC3 工单及其附件、加载技能，不编辑业务数据 |
| Admin | 系统管理：角色维护、数据库清理、全局 App 删除；前端只进入 `/admin`，不承担 RM 审批职责 |

这是职责摘要，不是权限实现。角色、页签与命名能力以 [access_control.json](shared/access_control.json) 为准；所有权、阶段、锁定与申请状态还会进一步限制操作。后端鉴权是最终边界，前端按钮仅用于展示。

App 工作台和 Jenkins 失败查询中，Owner 默认勾选“只看我的”，RM 默认查看全部。Jenkins 查询的 RM“只看我的”默认不勾选，链接中的明确筛选条件优先。

总览在统计卡片旁展示当前所选周期的组件、版本和说明，内容来自 RM 在周期管理填写的当月环境（Driver、SDK、PyTorch 等），随版本选择和显式刷新更新；不再显示独立的发布时间线。上方仍展示报名截止、QA 截止和发布日。

App 详情默认打开「基本信息和app_info」，顶部为官方名称、Owner、类型和 App 类型；官方名称与 Owner 仍仅 RM 可改。标题状态栏移除文档目标（如 HPC）和「正式版 x」标签；列表及详情状态栏显示所选周期的构建锁定状态。「资料」保留官方 URL、描述、手册和测试说明等内容，不再显示「本期报名」tab。单个 App 的「更新 commit 和 app_info」位于「最新 app_info」标题旁。有未保存修改时需先保存或取消再同步；切换 App、周期或刷新前会提示确认放弃修改。

基本信息底部「正式版发布历史」列出该 App 的全部有效发布，每个 App/周期只保留一份，按周期发布日倒序展示周期、App 版本、整组交付镜像和发布时间。每个周期可有多条镜像，按报名锁定 app_info 的启用 `app_build` 区分 OS、CPU 架构、Python/PyTorch 标签及 release/dbg 镜像类型，逐条显示包含 digest 的完整镜像名。早期迁移仅写入最新正式版的情况，从已冻结周期的成功发布报名补充历史，不新增正式版记录；同一 App 版本在不同周期分别列出；解锁删除该周期的原记录和清单，重新发布保存替换后的最终结果。切换 App、重新进入基本信息和手动刷新都会更新历史。

顶部「批量报名」仅列本人名下 CICD 运行中的 App；应用标题栏「报名」可为当前 App 单独报名（RM 可为其他 Owner 的 App 操作）。弹窗选择仍在报名期的版本，展示 RM 填写的组件环境（Driver、SDK、PyTorch 等），确认后按最新 app_info 的 app_version 报名，默认纳入全部启用测试项，不指定基础镜像和计划 feature。已报名、缺少有效 app_info 或有未处理停止申请的 App 不可勾选。批量报名在一个事务内完成，任一 App 校验失败则整批不写入并显示错误；取消不写入。成功后切换到所选周期并更新报名状态。

应用标题栏「撤销报名」仅向该 App 的 Owner/RM 显示，作用于当前选择的周期；确认后撤销有效报名并关闭待处理范围扩大申请。发布锁定后不能撤销；报名截止后仍可撤销，但不能重新报名。重新报名会清空上次 QA 结论和整组交付镜像。

app_info 同步成功与报名版本跟随分别显示：仓库新版本会正常保存；报名截止后扩大测试范围时，报名暂用原锁定版本，应用标题下持续提示「待 QA 审批」及扩大项。QA 拒绝后仍提示未获同意；已发布周期的历史版本差异不作此提醒。单 App 手动同步与报名截止前的 RM 全量同步共用这套规则；已取消每日自动同步，钉钉范围扩大通知需启用对应通知渠道。真实同步失败另以红色提示显示，切换详情 tab 后保留。

其他页面：开发 WIKI，以及「AI助手」页签下的五个子页签 Jenkins 失败查询、CICD 助手（旧版 V1 只保留直达地址）、JIRA agent、周报、MCP，各有自己的地址与权限。完整导航见 [routeConfig.ts](web/src/routes/routeConfig.ts)。

## 月度发布怎么运行

1. RM 每月新建一个版本：版本名手工填写；报名截止默认当月 1 日，QA 截止默认当月最后一天减 5（如 10/26、11/25），发布日默认当月最后一天，均可修改。新版本初始为空，上个版本的当月环境复制过来作为起点。
2. 报名截止前，RM 维护当月环境（组件/版本）和基础镜像；报名记录包含基础镜像、目标版本、计划 feature 和测试范围（取消勾选即不测），仅 CICD 运行中的 App 可报名。App 详情当前不显示报名编辑页签。
3. app_info 只来自仓库，已取消每日自动同步。RM 仅可在所选发布周期报名截止前，从 App 工作台或「周期管理 → app_info 同步状态」发起全量同步，检查全部 CICD 运行中的 App；报名截止后、QA 截止后和已发布周期均拒绝全量同步，接口也必须提供并校验 release_id。单个 App 的手动同步仍可使用，新 App 申请生效后的首次后台拉取保留（`APP_INFO_SYNC_ENABLED` 仅控制首次拉取）。分支头变化时取回 `app_info.json`，按最新已保存 app_info 的 commit 判断是否更新：commit 不变时跳过保存；commit 变化时保存当前 commit 和内容为新版本并重建组件依赖（`app_dependency` 字段），即使 JSON 内容相同也保存，不再按 JSON 内容去重。单 App 手动更新会重新核对文件；全量同步仍在分支头未变化时跳过文件读取。单 App 与全量同步共用 Gerrit 批次读取层：HEAD 查询与固定 commit 文件读取复用连接，同仓库/分支只检查一次、同 commit 文件只读取一次；全量同步最多 16 个 App 并行拉取，SQLite 写入在调用线程逐项提交。每次提交读取前及结果写入事务内重新检查所选周期的报名截止时间；执行中到达截止时间或截止设置被改动时停止后续更新，并在任务汇总中说明原因。写入前还会核对仓库、分支、运行状态与同步版本，拒绝旧结果覆盖中途变更或另一次同步的新结果。单 App 手动同步在截止后扩大测试范围时仍需 QA 审批；RM 在同步状态页查看失败，不再手工上传 app_info。
4. 每条报名锁定一个 app_info 版本。报名截止前跟随最新版本；截止后不扩大范围的变化自动跟进（记审计），扩大范围（新增芯片、测试项、已有测试新增芯片、新增构建）则保持原版本并自动生成范围扩大申请，钉钉通知 QA。QA 同意后条目锁定到新版本；拒绝后同样的扩大不再重复申请。
5. 报名截止后，环境冻结，测试范围只能删减；Owner 发布前把计划 feature 更新为实际交付和已知问题。撤回报名在发布前任何阶段都允许。
6. QA 填结论（四个取值沿用）、备注，并在启用 app_build 的各构建行按 `image:tag@digest` 填写完整交付镜像名（tag 和 digest 均必填），QA report 自动统计本周期 QA log 的 fail 项，按 App/GPU/测试项展示来源 QA comment 及具体 HPC/App 版本；QA 在各 App 卡片末尾填写独立的手工 QA comment，通过、未测试状态也保留备注。失败汇总不自动更改 App 的 QA 结论，所有 QA 状态的 comment 均为选填，允许留空，也可清空已保存的备注。保存时校验构建归属和 digest 格式，并用完整仓库路径和 digest 实时查询 Harbor：镜像必须存在，OS 和 CPU 架构均以实际 tags 校验，QA 填写的 tag 必须属于该 digest 的实际 tags，并且该条 tag 须匹配锁定 app_info 生成的完整构建 tag（含 OS、CPU 架构、版本及 Python/PyTorch 配置，允许 HPC 版本或 commit 以及末尾构建号）。release/dbg 按镜像名区分（分别为 `*-maca` / `*-maca-dbg`），须与所选构建一致，digest 也必须存在于该类型对应的仓库中。Python/PyTorch 标签须与所选构建完全一致，且必须在同一条实际 tag 中同时匹配，不能跨 tags 拼接。不使用 Harbor 的 architecture/os 元数据，逐项校验输入引用中自带的 tag；缺少可用 tags、平台不符或 Harbor 查询失败均报错，整批不保存。单次保存内对相同镜像去重查询，不跨请求缓存；网络查询在写事务之外，写入前重新检查 QA/镜像/锁定版本及发布状态，拒绝并发覆盖。重新提交的未指定构建旧镜像必须先改填到明确构建行；旧标量写接口不能新增或修改非空交付字段以绕过校验。发布冻结整组交付镜像。旧单镜像数据保留为「未指定构建」，可移除并改填到正确构建行，不根据 digest 猜测 OS/架构。
7. QA 截止后 RM 发布：`qa_passed`、`has_issues`（备注进已知问题）记为已发布并写入正式版；`not_checked`、`cannot_release` 记为本期发布失败，App 保持原正式版。发布同时生成 final 文档并锁定；RM 可填写原因解锁更正，解锁会删除本期正式版记录、封存清单和 final 文档。

QA report 已取消文件上传及页面上的旧日志 AI 分析入口，通过 `GET /api/qa/report?release_id=…` 一次读取页面状态、QA log 失败汇总、交付镜像展示和变更记录；首次打开可省略周期，由响应确定当前版本，不再额外读取 state 或逐 App 查询审计。失败汇总只读数据库，不访问 Harbor/Gerrit。以 qa_status=fail 为准；旧记录没有 qa_status 时仅用 qa_result 的 fail 文本补充，不凭数值或备注猜测性能回退。按唯一 app_id 和分支关联本期 App 卡片，保留实际测试的 App/HPC 版本；无法唯一关联或未在本期有效报名的记录单独展示，不隐藏。每个 App 的手填 comment 沿用 entries.qa_note，独立于 QA log 单行 qa_comment，随「保存 QA 状态」保存，任何 QA 状态均保留；保存时校验 revision，已发布周期只读。仅改 comment/结论时不重传或重验未改动的交付镜像。旧上传接口 `/api/qa/upload-log` 返回 410，引导上传 QA log；已有历史日志保留只读下载。 QA report 查看模式在同一响应的 `qa_report.delivery_images` 中将历史 image@digest 按锁定 app_info 的构建匹配 Harbor 实际 tag，显示 image:tag@digest；同请求去重并行查询 Harbor。已保存 tag 原样显示，缺少构建或查询失败明确提示；此查询不改写交付记录，编辑和保存仍使用 QA 输入的原值。QA report 的查看与编辑模式均列出报名锁定 app_info 的全部启用构建，未提供镜像的构建保留平台、release/dbg 及 Python/PyTorch 配置，并显示「未填写交付镜像」；空展示行不作为交付记录保存。每个 App 卡片正文首先显示 QA 状态，其后为默认折叠的 QA log 失败项，标题保留数量，点击展开详情。交付镜像也默认折叠，标题显示「已填写 / 应交付」构建数（如 `交付镜像（1/3）`），编辑时随草稿更新；未指定构建的旧镜像不计入已填写构建数。展示响应按 entry revision 核对，旧结果不覆盖新记录；页面、保存后的刷新和切换版本统一读取聚合接口；失败汇总与镜像解析的错误分别返回，某部分失败时保留其余结果及编辑草稿。

QA 的测试命令、按镜像 digest 查询及 QA log 表格以 `app_id` 为首列，替代旧 `app_name`；上传须使用网站导出的 ID，按 ID、分支、HPC/App 版本、平台、GPU、Python/PyTorch 和测试项分别校验与保存。官方名称仅用于展示，不参与测试结果身份匹配；同名不同仓库的 App 不合并。本期发布 App、历史发布 App 列表及兼容性验证保留官方名称，单独增加 `app_id` 列。旧报告读取时结合已存 commit、镜像仓库、版本和分支识别 ID，不能唯一识别的保留并提示重新上传；不改原始文件或数据库。

QA「测试命令」仅查询 MACA 镜像（`*-maca`、`*-maca-dbg`），不查询或回退到 HPCC；`latest` 也只根据 MACA tags 选取版本。选择发布周期后查询，固定使用 `latest`，不再显示 `hpc_version` 输入框；接口仍支持具体版本查询。`latest` 在所选周期的版本系列（如 `3.10.0.x`）内，为每个 App／构建分别按版本数字选择最大补丁号，不按 tag 写入顺序或字符串排序；OS、CPU 架构、Python/PyTorch 标签及 release/dbg 分别选取，同一表可同时包含多个实际 HPC 版本。每行 hpc_version 为该构建选中的实际版本；latest 下无可用镜像的构建保留带镜像占位符的完整测试命令，hpc_version 留空，不借用其他构建的版本。按该周期有效报名的锁定构建和测试范围展开 OS、CPU 架构、GPU、Python/PyTorch 与测试项；每次公开读取 Harbor 实际 tags，命中镜像后通过 digest → commit → Gerrit 历史 app_info 实时组装命令，镜像参数使用完整 `镜像:tag@digest`，tag 必须是该 digest 的实际 tag，且与行内 HPC 版本和构建配置一致；优先不带构建号的版本 tag，只有带构建号的 tag 时选择实际构建号最大的 tag。缺少镜像的组合保留，`website_docker_cmd` 使用镜像 tag 中的 `<hpc_version>` 占位符保留完整命令；上游查询或来源解析失败另报错误，不冒充缺镜像。页面将仅 GPU 不同的相同命令合并为一行（如 `C500, C600, X301`），隐藏 `py_label`、`torch_label` 两列；不同构建配置仍分别展示。CSV 保留每个 GPU 的独立行及完整列，以供 QA log 回填；未声明芯片时保留空芯片行。所有 `qa_*` 列始终为空，不预填 digest 或历史结果。

CSV 使用完整列顺序（网页隐藏 Python/PyTorch 标签列）：`app_id、git_branch、hpc_version、app_version、arch、OS、GPU、py_label、torch_label、test_name、perf_golden、perf_unit、website_docker_cmd、qa_digest、qa_docker_cmd、qa_result、qa_env、qa_status、qa_comment`。app_id 取工作台中的固定唯一标识，不填写官方名称。下载 CSV 后另存为 `.xlsx`，填写 QA 列，并添加与 `qa_env` 同名的环境页，从「QA log」上传。

历史查询的「按镜像 digest」支持每行一个完整 `镜像@sha256:…`，也可上传 `.txt` / `.csv` 镜像清单，每批最多 500 个。CSV 优先读取 `镜像@digest` 等镜像引用列；可带 tag，但输入 tag 不参与平台判断。每次从 Harbor 实际 tags 定位 commit，再实时读取 Gerrit 对应 commit 的 app_info，匹配镜像配置并按支持的 GPU 展开命令。App 版本含连字符时仍能识别 commit tag；repo 构建的实际 `repo` tag 与同平台、同构建号的发布版本 tag 配对后，按 App 登记的 manifest 路径读取 Jenkins 公开归档中的固定 XML，再读取 app_info linkfile 项目的完整 commit。归档地址由 `[hpc_manifest_archive] BASE_URL` 配置；缺少配对 tags、归档不存在、项目不符、revision 不是完整 SHA 或来源冲突均报错，不使用当前分支配置代替。返回来源中包含 `commit_source` 和 `manifest_urls`。登记来源无法唯一确定分支时，仅在 Gerrit 存在唯一一个 HEAD 精确匹配该 commit 的分支且能唯一关联一个已登记 app_id 时采用该分支，否则不生成可回填的结果行。此链路无需 CI 构建数据库账号。历史 `*-hpcc` / `*-hpcc-dbg` 与相应 MACA 命名关联到同一 App 构建，保留 release/dbg 区分，生成命令仍使用查询的实际镜像 digest；不使用发布封存命令或 Harbor 的 OS/architecture 元数据生成。单批对镜像、仓库/commit 去重，最多 16 个并发网络请求；HTTP/TLS 连接在批次内复用，Gerrit 按主机、端口和 SSH 账号隔离连接池，每个来源最多 2 条连接、每条最多 8 个并发通道。登记用 manifest XML 从同一仓库/版本一次批量读取，固定归档按 URL 去重。命令展开按仓库/commit 复用，不跨请求缓存文件、tags 或命令；批次结束关闭连接。失败镜像单列原因，成功部分仍可导出 CSV。CSV 首列使用 app_id，包含 App ID、分支、版本、平台、GPU、commit、测试项、命令与性能基准，另有 `test_result`、`test_digest`、`test_env`；按 `app_id、git_branch、app_version、arch、OS、GPU、test_name` 七个字段回填最近结果，未命中留空。匹配归一化首尾空白、架构别名、OS/GPU 大小写；app_id 和分支区分大小写；commit、digest、HPC 版本及 Python/Torch 不参与结果键。

QA「QA log」先选择发布周期（如 `3.9.0` / `3.10.0`），每个周期只保存一份当前报告。上传处明确提示整份替换，选择文件后弹窗确认所选周期、文件及清空旧结果和旧文件的范围；取消不上传。QA/RM 上传 `.xlsx`（最多 20 MB、测试页最多 20000 行）；第一个页签为测试详表，`qa_result` 支持数字（含 0）或文字，`qa_status` 限小写 pass/fail 或空，`qa_comment` 保留文字和换行。六个 QA 列全空的行跳过；上传结果同时显示所选发布周期的测试总数、文件条数、保存条数、跳过条数。版本测试总数沿用测试命令的有效报名锁定范围，按构建／GPU／测试项展开，包含缺镜像项，不查询 Harbor；文件条数为第一个页签非空测试行（不含表头、空行、环境页）；保存条数为去重后实际入库数，跳过条数＝QA 列全空行＋被后续重复行覆盖的行，因此文件条数＝保存条数＋跳过条数。另列两类跳过数量及空 QA 行按 App／分支汇总的明细，空 QA 行未校验、未入库。已填写行仍严格匹配 git_branch，并须提供 `qa_digest` 及与环境页同名的 `qa_env`；后续环境页及整份 Excel 原样保存。QA log 的 qa_env 可悬停或键盘聚焦预览该条结果所属工作簿的同名环境页，移开、失焦或 Escape 后关闭。预览只读取单元格文本，不执行公式、不归一化环境字段、不改写文件或入库结果；同名环境按 report_id 区分。首次按需请求，重复预览在当前账号/角色内复用。过大的环境页显示部分内容并保留原始 Excel 下载入口。保存前逐项核对前 12 列及 QA 命令的 tag/digest，hpc_version 必须属于所选发布周期及该 digest 的实际 tags；实际 HPC 版本与 `qa_digest_gerrit_commit_id` 随结果保存。全部校验通过后，在同一事务中删除该周期所有旧结果（包括其他补丁版本）和旧工作簿，再保存本次文件；例如先上传 case A，再上传只有 case B 的文件，最终只保留 case B。其他周期独立保存；校验失败、数据库写入失败均保留原报告，并发更新报错后需刷新重传。同一文件中不同 HPC 补丁、Python/Torch 及平台/测试组合分别保留，完全重复的十字段组合以最后一行为准。查询、筛选及 CSV 导出均限定所选周期，页面一次展示全部匹配结果，不分页。website_docker_cmd 始终按 qa_digest → Harbor tags → commit → Gerrit app_info 实时生成，不采用上传该列的内容，失败时不回退；QA 命令保留原文。上传不会自动修改 App 的发布结论或交付镜像。旧列名只读映射兼容，旧结果按已保存的 HPC 版本归属读取，首次重传时一并替换可确定归属的旧数据。

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

QA report 每个 App 的构建锁定状态显示在卡片顶部 QA 状态标签旁；「QA 状态」、下拉框和「锁定构建 / 解锁构建」按钮同行显示，锁定操作人和时间可悬停构建锁定标签查看。汇总标题右侧提供「全部解锁 / 全部锁定」，仅作用于当前版本全部有效报名 App，整批核对报名清单和锁定 revision 后在同一事务内保存；有并发变化时整批拒绝，不保存部分改动。每次批量操作仅写一条审计日志，显示实际变更的 App 数量，展开详情查看各 App 的原锁定状态和新状态；单 App 的变更记录也可查询到该批次中属于自己的明细。仅 QA / RM 可操作；编辑 QA 草稿时先保存或取消再改锁定状态。锁定以 `entries` 的 `(release_id, app_id)` 为范围：同一 App 在 `3.9.0` 锁定，不影响 `3.10.0`。默认未锁定，锁定只控制该版本的 CICD 构建，不更改 App 的全局 `cicd_status`、QA 结论、镜像或 app_info。撤回后重新报名保留该 App/版本的构建锁。最终发布在同一事务内将该周期全部报名记录（含发布失败和已撤回记录）上锁；发布失败时一起回滚。已发布周期禁止单独解锁构建，先由 RM 解锁周期更正，再由 QA / RM 按需解锁构建。数据库升级会为既有最终发布周期补齐构建锁。

CICD 脚本不在本仓库，由外部脚本在触发构建前读取数据库：`entries.build_locked` 为 `1` 表示禁止该 App 在该发布版本继续构建，`0` 表示未单独锁定。必须同时按 App ID 和发布周期 ID 查询，不能只按 App 名称或读取其他周期的状态。`build_lock_revision` 用于网站并发修改校验，`build_lock_updated_at` / `build_lock_updated_by` 记录最近操作，时间为北京时间；QA 变更记录及 App 变更记录保留锁定/解锁审计。只读查询示例：

```sql
SELECT e.app_id, e.release_id, r.name AS hpc_version,
       CASE WHEN r.released_locked = 1 OR e.build_locked = 1 THEN 1 ELSE 0 END AS build_locked,
       e.build_lock_updated_at, e.build_lock_updated_by
FROM entries AS e
JOIN releases AS r ON r.id = e.release_id
WHERE e.app_id = :app_id AND e.release_id = :release_id;
```

没有匹配行表示该 App 在该周期没有报名记录。外部脚本仍按自身流程处理报名及全局 CICD 运行状态；网站保存锁定数据，实际构建跳过由脚本执行。

## 文档产物与查询

- 发布说明只列当月发布的 App；使用手册和 AI4Sci 手册覆盖所有运行中 App 的当前正式版，每月更新。手册资料（描述、文档字段、测试说明、社区信息）是可选的，在 App 工作台「资料」维护。
- 「QA → 兼容性验证」（`/dependencies`）只纳入 CICD 仍在运行且存在有效成功发布的 App，按 App ID 判断，每个 App 取最近一次有效成功发布；从未成功发布的 App 不列入清单。按依赖组件及 Python/PyTorch 组合分类，展开组件、App 查看明细；同一 App 可属于多个分类，计数按 App ID 去重，CSV 不因分组重复导出。每个 QA report 交付镜像分别通过 digest → Harbor tags → commit → Gerrit app_info 实时生成平台、依赖和测试命令，显示该镜像自己的 app_info commit；不同镜像允许来自不同 commit。禁用构建不生成命令，最新 app_info 明确 enabled=false 的构建也从当前兼容性范围排除。镜像查询失败、commit 冲突、App/分支或构建不匹配时明确显示错误，不套用发布快照命令。发布记录与封存清单保留原样。历史交付缺 tag 时仅用 Harbor 实际 tags 补全 image:tag@digest；缺少交付镜像仍保留报名范围的模板，其镜像 commit 留空。
- 兼容性验证支持组件、CPU、OS、GPU 及关键词筛选，CSV 导出当前明细结果。选定组件后只显示该组件的分类与明细；SDK、driver 合为 `driver / SDK` 分组及筛选项，覆盖清单内全部已成功发布且仍在运行的 App，不要求声明依赖，仍可按平台和关键词筛选。不再显示范围待确认分区、提示及 CSV 范围状态列；未声明依赖的 App 可在 driver / SDK 中查看。每个合法构建／GPU／测试项单独一行，组合不跨构建拼接；有交付镜像的依赖来自镜像自己的历史 app_info。每行说明按同周期、同 App ID／分支／App 版本／平台／GPU／Python／PyTorch／测试项引用 QA log，列出具体 HPC 版本与 pass/fail；pass 不显示原因，其余状态显示 qa_comment，不同测试镜像显示「note: 测试镜像与 QA report 交付镜像不同，仅作历史参考」。页面、筛选及 CSV 使用相同说明。
- 成功发布时，在同一事务内为每条 `official_versions` 写入版本化 `test_manifest_json`，封存发布记录 ID、App/周期身份与环境、发布人/时间、完整 app_info 与 commit、组件依赖、启用构建、纳入/排除测试项、镜像组、完整 Docker 命令及原始 App 级 QA 结论/备注。缺少有效报名快照则整次发布回滚；缺失交付镜像允许保留并标注模板。按 App/已登记 digest 的历史接口优先读取封存内容；兼容性验证以封存记录定位交付镜像，再按各镜像实际 commit 实时生成，后续生成规则、开发配置或报名数据变化不改写已封存命令和 QA。同一 App/周期有数据库唯一约束；解锁删除该周期记录与清单，重新发布只保存替换后的最终清单；这不代表每个平台均已单独测试通过。
- 旧库升级新增可空清单列，清理已撤销、同周期重复及未锁定周期的旧正式版记录，保留每个 App/周期最新有效记录后移除撤销字段并建立唯一约束；不回填或伪造旧发布的封存时间。旧记录保留历史配置还原，缺少有效历史快照时明确提示；存在但版本不支持或损坏的清单报错，不静默重算。App 详情 `/api/apps/detail` 的 `official_versions[].test_manifest` 可读取各周期最终保留的完整清单。历史查询页面的 Harbor/Gerrit 镜像还原流程仍独立，统一发布详情页面留待后续。
- 「最新正式版」统一按发布周期的 `release_date` 选择，同日再按周期创建时间及创建顺序确定先后，不按版本名字符串、正式版记录 ID 或重新发布时间排序。更正旧周期不会覆盖已有较新周期；最新周期解锁时回退到此前成功周期，重新发布后恢复。周期列表、App 发布历史、兼容性清单与旧迁移记录回查共用这一顺序。
- 「开发态依赖」作为兼容性验证页的次级参考入口，仅纳入 CICD 运行中且曾报名的 App（含撤回报名），列出最新 app_info 的依赖、版本与 commit，不提供正式版镜像、命令或测试清单导出。切换入口重置筛选；显式刷新，不新增自动轮询。合并页面共用 `GET /api/lookup/latest-running` 的基线数据，原 `/api/dependencies` 正式版查询及 CSV 接口保留兼容。

- 「QA → 历史查询」默认展示「发布app列表」，下拉菜单仅列已发布并锁定的版本，默认最新版本。选中后自动列出该版本发布成功的 App、当期 App 版本、包含 digest 的完整交付镜像名、QA 结论和备注；可按 App、镜像或 QA 结果筛选。历史记录缺少完整镜像信息时明确标注，已停止维护的 App 仍保留在历史发布列表。解锁的版本不再提供查询，重新发布后按更正结果展示。「按 App」界面已移除，旧接口保留兼容。
- 历史查询的「按镜像 digest」始终从 Harbor tags、必要时固定 manifest 归档及 Gerrit 实时还原，规则见前述 QA 流程；兼容性验证复用同一公共批量解析链路，不访问 CI 数据库。发布详情及历史封存记录继续保留发布时依据。

测试范围按 `测试项 → supported_chip 的构建别名 → app_build` 连接，展示 OS、CPU 架构和各平台的芯片；测试声明与构建声明的芯片取交集，禁用构建、未引用的平台及不匹配的芯片不生成命令。App 详情、QA 测试范围、测试命令表/导出和历史镜像查询共用该规则。界面中的 CPU 架构简写为 x86/ARM，不附加构建别名和架构括号；QA 测试命令表/CSV 使用上述版本查询与统一列格式，构建与平台仍按 app_info 的引用关系展开。历史镜像查询展示该镜像匹配构建的 X86/ARM 芯片总览。报名截止后，即使测试名、芯片名不变，为已有测试新增 OS/架构/构建配置上的芯片组合，也需 QA 同意后才跟进。

QA 内容区的七个平级页签为「QA report」「本期发布app」「测试命令」「QA log」「开发者社区产物」「历史查询」「兼容性验证」；历史查询和兼容性验证向 RM、Owner、QA 开放。

旧的按周期快照数据由一次性迁移（[monthly_migration.py](app/db/monthly_migration.py)）转换：只有最近一个已锁定版本写入正式版；迁移前须在旧网站处理完所有未完成的 CICD 申请。

## JIRA agent（数字员工）

JIRA agent 是按组配置的数字员工，第一阶段处理 HPC 组的 Bug 类工单：收集信息、分类、判断归属、复现、分析，属于本组且能修复时完成修复与验证。部署、配置和迁移步骤见 [JIRA agent 部署与使用](docs/jira-agent.md)，开发约束见 [jira-agent-dev 技能](.agents/skills/jira-agent-dev/SKILL.md)。

| 位置 | 职责 |
| --- | --- |
| 服务器 A（本网站） | 对话、消息、文件、排队、执行记录、JIRA 评论；通过 WebSocket + token 连接 B |
| 服务器 B（组的 `codex app-server`） | agent 本体；本组知识包（AGENTS.md、skills）、Codex 登录凭证、访问 C/D/E 的 SSH 凭证。B 上不部署适配服务 |
| 服务器 C/D/E | 复现、构建、测试环境，由 B 上的执行用户登录。交单时选择：agent 从 RM 维护的系统机器列表中自选，或用户指定多台 `user@host`（逐机上传公钥，统一检查 SSH 和共享数据，异常确认后提交） |

要点：只有当前 JIRA assignee 或 RM 能交单和发消息；工单收到 Codex cybersecurity risk warning 后，非 RM 操作锁定，只能由 RM 处理（owner 仍可查看）。锁定跨对话和 assignee 变更保留，删除历史对话不会解锁，部署时会补录历史错误/告警；agent **只在 JIRA 追加评论**，不改 assignee、状态或代码；每组并发由网站排队控制，网站重启不丢轮次。RM 可在“知识学习”中从 agent 处理过的 Jira 或添加的历史 Jira 批量学习，审核提案后人工更新独立 `jira_agent_skill` 仓库，分别登记入库和部署。知识学习见 [docs/jira-learning.md](docs/jira-learning.md)。完整的找单、对话、排队、学习与恢复规则见 [docs/jira-agent.md](docs/jira-agent.md)。

RM 可在新建对话时选择服务商（例如 OpenAI / DeepSeek / MetaX）、模型和 effort；**同一对话固定服务商，切换服务商必须新建对话**。续办允许调整同一服务商的模型和 effort，排队或运行中不可修改。默认仍使用 B 的默认服务商；默认模型从 app-server 获取，额外服务商从组配置 `MODEL_CATALOGS` 指定的 B 侧模型目录读取，Key 仅保留在 B。MetaX 的 Codex 配置、模型目录和测试限制见 [部署说明](docs/jira-agent.md#metax-自建模型网关)。模型和 effort 在对话内持续保留，新建对话恢复默认。分析结果不自动发布；用户在评论框点击“发送到 JIRA”，编辑正文并选择本轮产物后确认发送，编辑历史可查，已发送轮次禁止重复发送。每轮产物在可展开的回收状态栏中预览和打包下载。

实现入口：[jira_agent_service.py](app/services/jira_agent_service.py)（交单与对话规则）、[jira_agent_runner.py](app/services/jira_agent_runner.py)（排队与执行）、[codex_app_server.py](app/integrations/codex_app_server.py)（通用 Codex 协议客户端）、[jira_agent.py](app/domain/jira_agent.py)（结论 schema、提示词、评论格式）、[JiraAgentPage.tsx](web/src/features/jiraAgent/JiraAgentPage.tsx)，B 侧知识包与启动脚本在 [deploy/jira-agent/](deploy/jira-agent/)。

后续阶段（尚未实现）：按 component 路由到其他组的数字员工、适配类与优化类任务。

## 周报

「AI助手 → 周报」包含「周报检查」和「自动周报」，可通过 `/weekly-report?tab=check`、`/weekly-report?tab=auto` 直达。旧的 `/weekly-report?week=…` 链接仍进入周报检查。

自动周报由 RM 选择 JIRA 组（默认 `pde_hpc`）、统计起止时间和周五的报告日期后发起。默认窗口为上周五 12:00 至本周六 00:00，周六前终点取当前时间，均按北京时间；单次最多 31 天。服务端使用 `[jira]` 查询本组相关工单、分页补齐评论和变更、沿 parent 补 Epic、读取 PHS Epic 与关联单，跳过只有排序等非实质变化的工单。窗口终点不含；终点之后编辑过的评论无法还原历史正文，跳过并在交付说明提示。没有实质更新时不生成空周报。

自动周报的模型调用全部经过统一 `llm.ask_json()`，每次现读 `release_system.conf` 的 `[llm]`，不依赖 Codex app-server 或本地令牌文件。流程为逐单提炼事实 → 按客户/PHS Epic 编写结构化初稿 → 代码校验真实工单号、来源覆盖、重复归属与字数 → 独立模型请求对照原始材料评审 → 修改并重验。逐来源提炼和独立复核各最多 4 路并发，合并时保持来源顺序；任一来源失败会停止同批剩余请求，不保存未完成周报。写作规则和姓名参考从 `jira-weekly-report` 移植至 `app/resources/weekly_report/`，随服务部署。上期使用同组、报告日期早于本期的最近一份已保存周报，沿用分类和顺序。

窗口内的附件在内存中读取：PDF、docx/pptx/xlsx、文本和图片，每件最多 10 MB，每任务最多下载 100 MB；PDF 最多 80 页、Excel 最多 2000 行/60 列，Office 解压内容受预算限制。图片每单最多最近 6 张，使用同一 `[llm]` 模型读取，需要该模型支持图片输入。单张识别最多 30 秒、仅尝试一次；首次超时、拒绝或格式错误后，本次任务停用后续图片识别，继续处理文本，逐件记录未识别附件。超预算、不支持、扫描 PDF 没有文本、图片识别失败等情况会写进交付说明，不猜测附件内容。资料过长会分段提炼；提炼总量超过预算时明确失败，要求缩小统计范围。

生成前先进行一次最多 15 秒的文本模型连接检查，失败即停止，不先拉取全部 JIRA。统一 LLM 层关闭 SDK 隐式重试，连接等待最多 10 秒，外部模型地址（包括 DeepSeek）按 `[proxy]` 配置连接，有配置则走代理，留空或省略则直连；单次调用另有总时限（流式心跳不能无限延长），确定的 400/401/403/404/422 不自动重试。任务在后台运行，通过 NDJSON 流显示阶段、工单号、附件文件名和 LLM 用量；刷新或离开后可重新接续，可中止。按报告日期所在的 ISO 周每周只保留一份（报告日期固定为该周周五，不按 JIRA 组另建副本），同周不允许同时生成。重复生成全部成功后，在同一事务中覆盖该周正文、交付说明、来源与用量，保留记录 ID 并递增 revision。失败或中止不保存任何未完成周报，也不改动该周已有结果；其他周历史记录保留。RM 可编辑正文/交付说明，保存有 revision 冲突校验；下一期参考保存后的正文。RM、Owner 可看历史与下载 `周报_<周五>_简洁版.md`（含交付说明），生成、编辑、中止需要现有 `weekly-report.run` 能力。JIRA 全程只读，不定时运行或自动发送。

自动周报入口为 `app/api/routers/auto_weekly_report.py`、`app/services/auto_weekly_report_service.py` 和 `web/src/features/weeklyReport/AutoWeeklyReportPane.tsx`；结果、来源摘要、独立评审及用量存于 `auto_weekly_reports`，运行中任务仍在单进程内。

RM 每周上传 PDE HPC Project Plan 周报（.xlsx，每人一页，A 列日期、B 列周报），网站给出每人的周报与 JIRA 问题清单，按 ISO 周留档，可导出 Markdown / CSV。规则沿用原本地脚本 `weekly_report_jira_check.py`：

| 问题 | 判定 | 谁来判 |
| --- | --- | --- |
| 周报日期异常 | 最上面一条的年份早于 2025、晚于今天 7 天以上，或不是本页最新日期 | 代码 |
| 周报未更新 | 最上面日期比报告日期早 3 天以上，或本周更新为空 | 代码 |
| 未写JIRA / JIRA ID不完整 | 工作项没有 JIRA ID；只写了数字、缺项目前缀 | LLM 拆工作项 |
| JIRA不存在 / JIRA无权限 | 接口返回 404 / 403（`[jira]` 的 token 无权浏览该项目） | 代码 |
| JIRA未更新 | 单子 assignee 是本人，窗口内本人没改字段、没写 comment / worklog、也不是新建；Closed 跳过更新及一致性检查，Resolved 仍检查；一个工作项挂多个本人未关闭的单，更新其一即可 | 代码 |
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

Guest 可登录 MCP 页面、创建或复用个人 token，只开放 `jira_search`、`jira_pull_issue`、`jira_read_attachment` 和 `skill_load`。JIRA 读取范围限定为 MC3：搜索在后端将完整 JQL 条件与 `project = MC3` 取交集，保留合法排序；读取详情、文本附件及附件下载票据均重新检查项目范围，非 MC3 请求在读取前拒绝，工单移出 MC3 后已有下载票据也不再可用。MC3 工单正文中的跨项目引用不授予被引用工单的读取权限。Guest 不能调用 Jenkins、评论或上传工具，即使是工单 assignee 也不能写入，旧上传票据在账号降为 Guest 后失效。工具列表和实际调用使用同一权限判断；Guest 的工具授权按 `shared/access_control.json` 的明确白名单执行，新增工具不会自动开放。Guest 仅能查看本人的使用记录。其他 MCP 角色保留原有权限。

网站在 `POST /mcp` 提供 MCP Streamable HTTP 服务（普通请求返回 JSON，用户名确认使用 SSE），供 Claude Code、Codex 等客户端连接。页面地址是 `/mcp-console`（不能用 `/mcp`，否则浏览器刷新会打到 MCP 接口；浏览器直接访问 `/mcp` 会被跳转到页面）。「接入配置」生成个人 token（只显示一次，库里只存 SHA-256），并给出 Claude Code / Codex 的接入命令；「使用统计」展示调用量、失败率、每日调用、按工具/用户的统计及调用日志，不展示会话数、按 token 表格、客户端分类和常操作对象统计。列表最多显示 10 行，超出后内部滚动；调用日志标题与筛选框水平排列。仍可按 token 筛选（只显示 token 名称和前缀）。`initialize` 握手不计入各项使用统计，也不出现在调用日志及其分页计数中；原始连接记录保留用于排障。

- 每人只有一个有效 token：生成新的会吊销旧的（鉴权也只认每人最新的一个）。网页「我的 token」只显示前几位，可重新生成或吊销。
- MCP 接入配置页面统一生成 HTTP 的一键安装命令、各客户端命令和手动配置地址，沿用浏览器当前域名及非默认端口，不使用 `[site] PUBLIC_BASE_URL` 覆盖，也不提供协议选择；网站继续通过原地址访问。服务器须开放对应 HTTP 入口，容器无需配置网站证书；HTTP 会明文传输账号密码和 token，仅用于受控内网。安装脚本按下载请求的协议生成后续账号请求及 MCP 地址，已安装客户端可复用原 token 更新配置，无需卸载。
- HTTPS 由 Nginx 等反向代理终止时，代理须覆盖设置 `proxy_set_header Host $http_host;` 和 `proxy_set_header X-Forwarded-Proto $scheme;`。MCP 返回的脚本、接入地址和附件传输链接会据此保留 HTTPS，避免登录 POST 请求误发到 HTTP 后遇到 301；不会使用 `X-Forwarded-Host` 替换目标主机，也不会将 HTTPS 降级到 HTTP。
- 一键配置：`bash <(curl -fsSL <网站>/api/mcp/setup.sh)`，菜单为 1 一键配置本机 / 2 查询完整 token / 3 重新生成 / 4 卸载本机配置（也可带参数 `configure|status|regenerate|uninstall`）。经 `POST /api/mcp/account`（action=status|regenerate）用域账号密码操作，与登录页同一认证，首次登录同样按 LDAP 组建用户，但不建网站会话；同一用户名 10 分钟内失败 5 次后锁定。脚本直接改 `~/.claude.json` 与 `~/.codex/config.toml`（先备份为 `*.bak-release-mcp`），不依赖 claude/codex 命令，只装 VS Code 扩展也可用；第二台机器复用本机配置里或粘贴的已有 token，选择重新生成则旧 token 在所有机器失效。 配置脚本以彩色区分提示、成功和错误，完整显示已生成或本机可读取的 token，并以加粗横幅提示跨环境复用和重新生成的失效规则；不再输出结尾的 Codex 重启提醒。服务器仍只存摘要，无法恢复未保存在本机的 token 原文。终端自动启用颜色，`NO_COLOR=1` 可关闭，`FORCE_COLOR=1` 可在重定向时保留颜色；API 数据及复制用的配置正文不含颜色控制字符。
- 按客户端配置已有 token：页面“手动配置”按 Claude Code、Codex、其他 harness 折叠展示。Claude/Codex 的命令使用 `MCP_CLIENTS=claude|codex bash <(curl -fsSL <网站>/api/mcp/setup.sh) use-token`，校验已有 token 后只更新指定客户端；未提供 `MCP_TOKEN` 时安全提示粘贴，不创建或重置 token。脚本仅完成一次性配置，客户端仍直接通过 HTTP 连接网站，无需安装本地转发服务。
- 安装结果先显示 MCP URL，完成后将完整 token 放在“重要提示”横幅内，与跨环境复用和重新生成失效的说明一起展示。选项 2 显示本机完整 token 及其有效状态；服务器只保存摘要，无法恢复未保存在本机的 token 原文。已有有效 token 时，一键配置中回车选择重新生成和菜单“重新生成”都会再次提示“其他机器上的旧 Token 将立即失效，是否继续？[y/N]”；回车或选 n 均取消，不生成 token，也不修改本机配置。菜单已移除“仅输出配置方法”；安装成功后不追加其他客户端的接入说明与文档链接。
- 确认等待期间每 15 秒发送 SSE 注释保活，并用 `X-Accel-Buffering: no` 禁用代理缓冲，避免等待用户输入时连接空闲断开、客户端只收到 `Transport closed`。用户名不匹配、拒绝、取消和超时均通过工具错误返回具体原因及 `structuredContent`（`status=not_executed`、`reason`、`retryable=false`），要求 agent 告知用户，不自动重试或代填用户名；再次尝试须由用户明确发起。确认失败日志保留目标工单号。真正的网络故障仍可能导致结果无法送达，不能仅凭断线判断 JIRA 是否已写入。
- 写操作的用户确认：服务端统一按 `read_only=False` 且 `destructive=True` 决定是否要求用户名确认，不依赖工具名或是否提供预览函数；只读或非 destructive 工具无需确认。客户端支持 MCP form elicitation 时，`tools/call` 以 SSE 发出 `elicitation/create`（300 秒超时）。`jira_add_comment` 要求用户手工输入域账号用户名，不预填，去除首尾空白并忽略大小写后与 token 所属账号完整匹配才发送；空值、类型错误、不匹配、拒绝、取消、超时均不发送，不支持 form elicitation 的客户端不能发评论。协议仍使用 `action=accept` 表示提交表单，但仅 accept 不足以发布。评论先通过与网站 JIRA agent 共用的 `app/domain/jira_format.py` 处理格式（当前为 JIRA wiki 行内代码间距修正，保留代码块，不做通用 Markdown 转换），客户端调用前先在对话中展示拟发送的完整评论；确认弹窗对短评论展示全文，长评论只展示最多 240 字符、6 行的摘要并明确标注截断，输入框标题直接提示输入本人域账号用户名，发布时仍使用完整正文并追加署名；空正文和超长内容在弹窗前拒绝。`jira_upload_attachment` 不请求 elicitation 确认，直接返回上传命令；签发票据和实际上传时仍核验权限。日志 `extra_json.confirm` 记录 `username_matched` / `username_mismatch` / `client_cannot_confirm` / `decline` / `cancel` / `timeout`。待确认请求在进程内，须单 worker；所有写操作要求有效、未结束且属于当前 token 的会话，批量调用被拒绝；确认回复绑定发起 token 和会话，提交前再次核验 token 与当前角色。
- 其他 harness 的配置文档链接和「能否弹确认」的核实结果维护在 [mcp_harnesses.json](shared/mcp_harnesses.json)（`confirm` 为 form/none/unknown），页面「手动配置」和脚本的配置/输出结果共用这份列表，对不支持的标注「不推荐」。
- Codex 没有用户名弹窗时，检查当前会话实际生效的审批策略：客户端声明支持 form elicitation 不保证策略允许显示。`approval_policy = "never"` 可能在显示前自动拒绝输入表单；服务端收到 `decline` / `cancel` 不能判断用户是否看到了弹窗，返回信息会说明这一点，Codex 客户端返回拒绝或取消时提示「客户端拒绝或取消，未执行。可能是用户拒绝，也可能是codex设置了Full Access模式。如果是后者，请改为Approve for me，或手动评论。」可在执行端 `~/.codex/config.toml`（设置 CODEX_HOME 时使用该目录）的顶层、所有表头之前替换审批键为 `approval_policy = { granular = { sandbox_approval = false, rules = false, mcp_elicitations = true } }`，保留原 `sandbox_mode`；需要完全访问时可配 `sandbox_mode = "danger-full-access"`。重启后开新会话，避免命令行 `--yolo`、profile、项目配置或客户端“完全访问”预设覆盖回 `never`；不支持细粒度策略的客户端可用 `on-request`。见 [Codex 官方配置参考](https://developers.openai.com/codex/config-reference/)。接入页和脚本给出指引，不自动修改用户审批/沙箱策略；服务端发布更新无需用户重新安装脚本或重发 token，客户端审批策略须在执行端配置。
- 鉴权：`Authorization: Bearer rsmcp_...`。每次请求实时读取 token 对应用户的当前角色，须在 `shared/access_control.json` 的 `mcp` 页签内；吊销或角色变更在后续鉴权时生效，附件下载、上传及确认后的提交都会重新核验；上传还会重新检查当前 assignee；MCP 评论和附件上传均不检查 JIRA agent 风险锁。统计和日志 RM（`mcp.logs.view.all`）看全部，其他人只看自己。
- 工具：`jira_search`、`jira_pull_issue`（默认只查询并返回工单字段、状态、描述、评论和附件列表，直接回答用户，不创建本地文件或下载附件；仅在用户明确要求下载/保存时传 `save_to_local=true`，才返回 issue.md 保存指引和附件下载命令。保存目录优先使用 `target_dir`，未指定时为 `jira/<KEY>/`，`include_attachments=false` 可只保存正文；查询模式下即使传了目录或附件选项也不签发下载票据。附件链接 30 分钟内有效，票据存在进程内，所以网站须保持单 worker）、`jira_add_comment`（只限当前 assignee 或 RM，经用户名确认后发送，不受 JIRA agent 的 cybersecurity risk 锁限制，也不解除该锁；以 `[jira]` 账号发出，正文末尾注明网站用户，发布来源统一为“通过发布系统 MCP 发布”）、`jira_upload_attachment`（同样只限 assignee 或 RM，不受 JIRA agent 风险锁限制，也不解除该锁；MCP 读不到本机文件，所以返回一次性的 `curl --data-binary @文件 PUT /api/mcp/uploads/<票据>` 命令，由客户端执行，上传时再核对一次权限，单文件 50 MB，成功一次即失效、失败可重试，记为 `upload` 日志）。
- 评论和上传附件无需先调用 `jira_pull_issue`：已知工单编号且评论所需信息齐全时，按格式规范准备并展示正文后直接调用 `jira_add_comment`；已知工单编号和本地文件路径时，直接调用 `jira_upload_attachment` 并执行返回的上传命令。工单存在性和写权限由服务端实时检查，仅在任务需要补充工单上下文、历史评论或查找已有附件时才拉取。此调用策略通过服务端 instructions 和工具描述下发；发布后，客户端正常重连并重新获取 instructions 和工具列表即可获得，无需重新安装接入脚本，已缓存描述的会话需刷新连接。
- Jenkins 失败查询：`jenkins_search_failures` 支持日期、关键词、任务、应用、责任人、阶段、芯片等筛选与分页（默认每页 20，最多 100）；`jenkins_get_failure` 按记录 ID 返回后端保存的完整详情，包括已有的错误原因、关键错误片段、处理建议、责任变更历史及测试汇总。缺失字段不代表已诊断成功，也不等同于完整 console 日志。复用 `[cicd_agent]` 配置和网页查询权限，回收站仅 RM 可读；均为只读工具，不触发用户名确认、不重跑构建。无需更新客户端脚本，客户端重新发现工具后即可使用。
- Jenkins 原始错误：`jenkins_get_build_errors` 接受失败记录中的 `build_url`，默认读取构建信息和失败 JUnit case 的错误详情、堆栈及标准输出；`source=console` 按 `next_start` 分段读取原始控制台日志，`has_more` 表示仍有未读内容，`building` 仅表示构建仍在运行。`source=artifact` 配合 `artifact_path` 可读取构建归档列表中的自定义 JSON 测试报告。未发布测试报告时明确标记缺失，不宣称测试通过，也不把构建 SUCCESS 当作所有 case 通过。凭据配置在 `[cicd_agent]` 的 `JENKINS_BASE_URL`、`JENKINS_USERNAME`、`JENKINS_API_TOKEN`、`JENKINS_TIMEOUT_SECONDS`，每次调用现读；只允许配置的 Jenkins 构建地址，禁止跟随重定向，仅发 GET；TLS 使用系统受信 CA，支持已安装内部 CA 的部署环境。
- 附件阅读：`jira_read_attachment(issue_key, attachment_id)` 核对权限与附件归属后，仅在内存中处理，不创建附件文件或自动下载到客户端。单件读取上限 2 MiB。文本默认识别 UTF-8 / 带 BOM 的 UTF-16，可指定 GB18030；每页最多 500 行 / 24000 字符，用 `next_start_line`、`next_start_column` 和 `expected_sha256` 续读。PNG/JPEG/WebP/GIF/BMP 图片每次返回一张原生 MCP `image` 内容块，原始图片字节完整保留，不在服务端缩放、压缩、转码、铺背景或移除动画帧，仍受单件 2 MiB 读取上限限制；元信息记录原图尺寸、字节数及帧数。客户端和模型可能自行缩放、只读取部分动画帧或拒绝超限/不支持的图片。客户端需支持图片输入，且仍可能在会话记录中保存 Base64，因此不保证客户端完全不落盘。PDF、Office、压缩包等暂未支持解析的二进制格式返回 `status=needs_user_action`、文件元信息和 `suggested_question`，明确正文未读取；LLM 应询问用户是否允许下载到本地解析，并说明会保存文件，取得同意后才走下载流程；不得把原始二进制 Base64 当文本交给模型。编码失败的潜在文本会提示按 `retry_encoding` 重试。附件正文/图片数据不写入网站调用日志，仅记录参数、摘要和处理元信息。
- Jenkins 直接发现：`jenkins_list_jobs` 支持任务名称过滤、目录和分页；`jenkins_list_builds` 返回任务构建及链接，日期为北京时间的构建启动日期（首尾日均包含）。这两个工具直接访问 Jenkins，不依赖 CICD Agent。过滤应用于当前扫描页，空结果仍可能有 `next_offset`；目录通过 `parent_url` 逐层查询，不做无限递归。保留所有构建状态，不能据 SUCCESS 判定 case 全部通过。
- 按 case 取证：现有 `jenkins_get_build_errors` 增加 `case_name`，结合 JUnit、自定义 `cicd-test-results/*.json` 和日志返回指定 case 的结果、错误上下文、来源位置。单次最多扫描 1 MiB 日志、10 份自定义报告；`console.has_more` 为真时用 `console.next_start` 继续，可改 `source=console` 避免重复拉取报告。未找到仅代表当前扫描范围内未定位；纯文本提及和明确 case 起始标记分开标注，并行日志仍可能交错。原始日志分页读取保留。
- 跨系统关联：JIRA 拉取、失败记录、构建及 case 查询补充 `correlations`，包含明确记录的工单/构建链接、应用版本、镜像 tag/digest、仓库等字段，并附来源和 JSON 字段路径；JIRA 原生 issue link 与正文提及分别标注。没有新增聚合工具，不按名称或时间猜测同一故障；无关联字段不等于已证明不存在关联。实际数据是否完整仍取决于上游记录。
- HPC 专家方法：`skill_load`（只读，所有可用 MCP 的角色都能调）把 skill 的方法正文下发给客户端，先给主方法，`page=build|diagnosis` 按需再取参考页；当前提供 `cu-bridge` 和 `jira-format`；后者直接读取已有 `jira-issue-manager` skill 的 `references/jira-format.md`，仅含排版规则。读的是网站随附的知识包副本 `deploy/jira-agent/hpc/`（打包副本，不是正式发布源 `jira_agent_skill` 仓库）。服务端 `instructions` 里提示先调用它，Claude Code 与 Codex ≥ 0.167.1 会自动加载（Codex 0.166 不读服务端 instructions）。只能下发方法文本，不含可执行脚本。
- 扩展：在新模块里 `register(Tool(...))` 并在 `mcp_tools.load_tools()` 导入。工具声明 `category`、`read_only`、`destructive`、可选 `roles` 与 `log_args`（脱敏）；`confirm` 仅定制确认文案，不提供时使用工具名称和参数的通用预览，不会跳过 destructive 写操作的用户名确认。返回的 `ToolLog` 写入通用日志列 `target_type/target/extra_json`，无需改表即可进入统计页。
- 代码：[mcp.py](app/api/routers/mcp.py)、[mcp_protocol.py](app/services/mcp_protocol.py)、[mcp_tools.py](app/services/mcp_tools.py)、[mcp_jira_tools.py](app/services/mcp_jira_tools.py)、[mcp_skill_tools.py](app/services/mcp_skill_tools.py)、[mcp_service.py](app/services/mcp_service.py)；表 `mcp_tokens`、`mcp_sessions`、`mcp_call_logs`（主库）。日志目前不自动清理。

## 组件兼容性结论

固定选项 `driver`、`sdk` 默认覆盖兼容性验证清单内全部 App（CICD 运行中且存在有效成功发布），不要求声明依赖；从未成功发布的 App 不再纳入新建清单，已有组件记录保持原范围。其他组件按正式版依赖筛选。打开新建表单时准备一次公共链路清单，同一表单提交复用该结果，不再重复解析全部镜像；凭据限当前账号/角色、有效期 5 分钟，提交时复核发布和 App 配置，变化或过期须刷新组件列表。普通兼容性/digest 查询仍实时读取。解析失败不创建残缺记录，表单显示具体 App 与原因，并提供手动重试。

「兼容性验证 → 组件兼容性结论」独立收集组件验证结果。QA/RM 点击新建，选择当前正式版依赖组件（含 Python/PyTorch 组合）、输入组件 MACA 版本；每个组件/版本保留一份记录，不同版本互不覆盖。新建时通过现有公共交付镜像链路生成当时各 App 最近一次成功发布的清单，保存 App ID、名称、App/HPC 版本、镜像来源和测试范围；进行中的发布不参与，后续发布或 app_info 更新不改写已有记录。表格按 App 填写 pass/fail 和选填 comment，逐 App 保存，带提交人、时间及并发版本校验；Owner 只读。可展开「测试范围」查看测试项、OS、CPU 架构与支持芯片，省略命令。列表支持关键词和逐列筛选；未保存修改在切换记录、页面和刷新前提示。已有记录查询不访问 Harbor/Gerrit，不更新发布 QA 状态或 QA log。存储为 `component_compatibility_reports` 和 `component_compatibility_apps`，不依附某个月度发布周期。

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
| 周报 | 检查：`weekly_report_weeks`（每个 ISO 周一行，重新分析覆盖）、`weekly_report_findings`、`weekly_report_members`、`weekly_report_items`（LLM 拆分缓存），xlsx 不保存；自动周报：`auto_weekly_reports`（每周一份，成功重复生成覆盖；正文编辑带 revision 校验） |
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

LDAP、JIRA、大模型、CICD Agent、网站对外地址和各组 JIRA 数字员工的配置统一放在根目录 `release_system.conf`（已 gitignore），分为 `[ldap]`、`[jira]`、`[llm]`、`[cicd_agent]`、`[hpc_manifest_archive]`、`[proxy]`、`[site]`、`[jira_agent:<组名>]`、`[jira_learning:<组名>]`、`[jira_evaluation:<组名>]` 几节。大模型一节供 QA AI 分析和周报等所有功能共用，代码里统一经 [llm.py](app/integrations/llm.py) 的 `ask_json()` 调用；原来的 `[qa_llm]` 已改名为 `[llm]`，键名去掉 `QA_` 前缀，仍写旧节名时会提示如何改。复制 `release_system.conf.example` 起步，各键含义与必填规则见其中注释。配置在每次使用时现读，修改后即时生效。旧的 `ldap.conf` / `jira.conf` / `qa_llm.env` / `jira_agent.conf` 已不再读取，升级时把内容搬到对应节。

配置值支持成对的单引号或双引号，例如 `CODEX_WS_URL = "ws://127.0.0.1:4510"`；也兼容 URL 协议冒号写成 `\:`，其他反斜杠保持原样。引号内的空白保留，`""` 和 `''` 均表示空值。

`[jira_agent:<组名>]` 可用 `MODEL_PROVIDER` 和 `MODEL` 设置本站新对话的默认服务商及模型，配置示例见 [对话模型设置](docs/jira-agent.md#对话服务商模型与-effort)。

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

旧业务库切换演练使用 [tools/cutover_release.py](tools/cutover_release.py)，在项目根目录、当前 Python 依赖环境执行：

```bash
python -m tools.cutover_release --source release_system.db --output release_system_cutover.db
```

脚本以只读连接做一致性备份，在临时副本完成月度模型迁移、原 QA summary 转换和网站上传校验、交付镜像补齐，最后输出新库。要求旧库仍含 `snapshots`、无未完成 CICD 申请；拒绝覆盖源库或已有输出。原 App ID、QA 发布结论/备注、用户、MCP、WIKI、原始 summary 和旧文档保留；发布日取锁定时间对应的北京时间日期。各成功发布周期补齐正式版历史及交付镜像，历史清单仍标为重建，不伪装为当时封存。组件兼容性结论为空，不读取其他演练库。

默认处理 `3.9.0` 和 `3.10.1`，交付上限分别为 `3.9.0.29` 和 `3.10.1.11`；可重复传入 `--cap 周期=上限` 指定处理范围。仅查询当前周期的 MACA release/dbg 仓库，每个启用构建按数字选择不超过上限的最新版本及构建号，无镜像留空。QA 测试镜像另按原表环境区的 **HPC Version** 上限追溯（不使用 Host SDK Version）；原表缺少 digest，匹配补齐的来源在 `migration_note` 中说明，不视作原始 digest 证据。历史测试涉及本期未报名或已停止 CICD 的 App 时仍追溯它自己的镜像，仅用于 QA log，不补造报名或交付记录。LAMMPS 原表 Branch 中的 `patch_*` / `stable_*` 是 App 版本：先按该版本区分 app_id，再匹配 `master`，不能把两个版本合并。

主机 OS 不冒充容器 OS。出现多个合法构建时，x86 排除 Kylin，有 Ubuntu 22 时优先选择 Ubuntu 22；仍不唯一、单位不符等行保留在问题清单，不将成绩复制到多个配置。GPU 不匹配的行跳过并单独记账，不列入待确认。唯一匹配但原表结果为空、`/` 等未测占位符的行写入 `qa_result=failed`、`qa_status=fail`，原结果和备注保留。其他行只转换明确的通过、失败及性能回退文字，数字结果不自动推断为 pass。已有正式版发布状态不因导入结果自动改变。

除新库外，在同名目录输出 `images.csv`（交付和 QA 候选镜像、实际版本、完整 `image:tag@digest`、commit）及 `qa_log.xlsx`（两个周期的结果页、原始环境页、迁移汇总、待确认问题、已跳过记录）。合并 Excel 用于核对；新库的 QA log 为每周期独立的标准工作簿，可从网站分别下载。原始 summary BLOB 仍在 `qa_logs` 中保留。脚本不启动网站、后台任务或通知，不访问 CI 数据库。

退出码 `0` 表示完成且无待确认匹配问题，`2` 表示输出已生成但仍有待确认数据，`1` 表示执行失败且未发布输出库。缺少交付镜像及旧列标题单独列明。不要在未检查问题清单时直接把演练库切为业务库。确认某行的构建后，可通过 `--overrides mapping.json` 重跑到新输出路径，例如 `{"3.9.0/C500/42": {"OS": "ubuntu22.04", "reason": "QA 核对原始记录确认"}}`；映射只缩小已有合法候选，不能绕过分支/GPU/测试项校验。正式切换时停止旧站写入，以最新业务库重新运行并验证，再切换数据库路径；助手/JIRA 独立库和运行配置需随部署另行保留。

测试入口：后端 `python -m pytest -q`；前端 `npm run build`、`npm run lint`、`npm test`、`npm run test:e2e`。隔离参数与 E2E 端口见 [验证说明](.agents/skills/release-system-dev/references/verification.md)。

## 文档维护

README 负责产品、使用入口和运行方式；技能负责开发约束，`references/` 放按需读取的细节；部署手册与功能建议放 `docs/`。同一规则只写在一处，其他地方链接过去；不把一时的业务数量、特定模型名或机器路径写成永久要求。

## 钉钉通知

每种通知各用一个钉钉机器人，由 RM 在「周期管理 → 通知设置」页面逐项填写 `ACCESS_TOKEN`、加签 `SECRET` 和要 @ 的钉钉用户 ID，并可发送测试消息；保存后密钥只显示掩码，未配置或未启用的通知类型不发送。机器人须启用加签安全设置。通知类型：JIRA agent 轮次结束、Jenkins 失败数超阈值、测试范围扩大申请、范围扩大审批结果、app_info 同步失败汇总。`[proxy]` 的 `URL` 配置公共 HTTP/HTTPS 代理（例如 `http://proxy.sha-dc01.metax-tech.com:1080`），服务按需接入，目前钉钉和大模型（`[llm]`）使用，其他服务不自动切换；`LLM_BASE_URL` 是 `metax-tech.com`、`metax-internal.com` 或其子域名时大模型直连，不走代理；修改后下次发送或调用生效；留空或省略则直连，不读取环境代理变量。

- JIRA agent 每分钟检查完成、失败或意外中断的轮次，用工单号、本轮发起人的姓名和网页结论/操作标记发送一句话；姓名读取网站用户资料，缺失时回退显示该轮发起人的域账号。失败和意外中断附上简短原因（如 cybersecurity、model capacity、codex disconnected），未分类错误提示查看对话详情；交付不完整时明确提示，不报成功。主动取消不通知。首次启用只接收启用后结束的轮次，之后停机期间漏发的轮次会补发，每轮通知一次。
- Jenkins 在北京时间每天 10:00–20:00 的整点（包含 10:00 和 20:00，其他时段不查询、不告警）查询一次网页同源的 `/api/v1/failures/summary`，按责任组聚合，北京时间开始/结束日期均为当天，不附加用户筛选。所有 Code Owner 总错误数 **>20**、DevOps（SPD）总错误数 **>30** 时发送一条包含两项数量的通知；当天发送成功后不再发送，次日重新计算。首次启动不立即补查，等待下一个整点；允许时段内整点启动则立即查询。
- 成功回执保存在 JIRA agent 数据库的 `notification_receipts`，重启不会重复告警；失败在后续轮询重试，不影响 JIRA 处理。远端已接收但本地尚未记录时发生断网/退出，重试仍可能重复一条消息（钉钉没有幂等键）。首次启用前的旧轮次不补发。
- RM 登录后可访问 `/api/notifications/status` 查看是否启用、最近检查时间及脱敏错误；单 worker 运行，与 JIRA runner 的部署要求一致。Jenkins 未配置时跳过其检查。
