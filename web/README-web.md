# 发布协作系统前端

React 18、TypeScript、Vite、TanStack Query、zustand 与 react-router。产品和业务流程见 [根 README](../README.md)，角色/操作能力来源为 [共享权限矩阵](../shared/access_control.json)。

## 安装与启动

从 `web/` 执行：

```bash
npm ci
npm run dev
```

普通 Vite 开发默认使用 5173，`/api` 代理到 `http://127.0.0.1:8000`。目标由启动进程的 `API_TARGET` 覆盖：

```bash
API_TARGET=http://127.0.0.1:8001 npm run dev -- --host 127.0.0.1 --port 5173 --strictPort
```

后端需另行用隔离数据库启动。npm 脚本已为本机请求设置 `NO_PROXY`，自定义命令也要绕过本机代理。

`npm run build` 先通过 Python 3 校验并打包内置测试用例，再执行类型检查和 Vite 构建，覆盖根目录 `web_dist/`。用例包位于后端的 `app/resources/jira_test_cases/_packages/`，随 `app/` 部署；FastAPI 从 `web_dist/` 提供页面与 SPA 深链接回退，所以构建会直接改变正在使用该目录的服务。

## 代码导航

| 目录/文件 | 职责 |
| --- | --- |
| `src/api/` | 同源请求、登录 API 与 AuthContext；401 进入未登录状态 |
| `src/main.tsx` | React、QueryClient 和 AuthProvider 的全局装配 |
| `src/routes/` | 页签、路由与角色访问；`routeConfig.ts` 中同一 `group` 的路由合并为一个顶栏页签（QA、AI助手），QA 的平级工具标签由 `QaTabs` 在内容区渲染；Admin 非管理页深链接重定向到 `/admin` |
| `src/features/` | 周期、总览、App、QA、文档、CICD、Jenkins、助手、JIRA agent、周报、WIKI、管理页面 |
| `src/lib/` | 权限、时间、阶段、字段词表和格式化 |
| `src/store/uiStore.ts` | 共享周期选择、App 选择和编辑等 UI 状态 |
| `src/components/` | Markdown、日期输入、刷新条、表格与通用反馈 |

CICD 助手页面调用 `/api/cicd-agent/*` 同源代理，服务地址在 `release_system.conf` 的 `[cicd_agent]`。V2 依赖 `@assistant-ui/react`，改动后需完整类型检查和构建。

## 缓存与数据契约

全局 QueryClient 默认 `staleTime: Infinity`，关闭焦点、重连、挂载自动重取和轮询。进入页面不保证请求：`refetchOnMount: true` 对永久新鲜缓存通常不重取。内容靠显式刷新和写后失效更新，`RefreshBar` 显示查询的 `dataUpdatedAt`。

现有按秒轮询只有这些：顶栏 CICD 通知角标（`TabNav`，所有已登录页面）、CICD 页的待审批/待交付列表与计数、JIRA agent 运行中对话的时间线、上传公钥终端及学习活动任务、测试用例页面中尚未结束的批次（5 秒，仅获取运行状态，不重复拉取输入文件）、周报检查运行中的作业进度。批量 Gerrit 拉取与 CICD 助手用流式接口。不要靠新增定时器修复局部失效问题。

查询键应区分身份、周期、过滤参数与响应形状。学习查询按账号隔离，退出、401 和重新登录时清理学习缓存，账号切换时重建页面清除草稿。其他查询仍未统一清理；维护认证或缓存时应处理这个已知缺口，不能认为切换账号天然隔离。

App 工作台请求 `include_allowed_actions=1`，其他 `/api/state` 调用不一定带此参数；现有页面存在共享查询键，修改时核对缓存实际来源。后端返回的 `allowed_actions` 用于展示，写接口仍应依据当前用户与当前资源重新鉴权。

HTTP 类型声明不会验证真实 JSON。改接口时对照 router/service、前端 API 包装和调用处，验证提交后回读；mock 的字段名与后端保持一致。`/api/me` 返回 `{user: ...}`，无会话时也是 HTTP 200、`user: null`。

## 交互与显示

- 「AI助手 → 周报」分为「周报检查」和「自动周报」，通过 `?tab=check|auto` 保留入口，旧的 `?week=…` 仍打开检查。自动周报使用 `/api/weekly-report/auto/*`，列表与详情按账号/角色缓存，进入及显式刷新时重取；运行进度使用 NDJSON 流，显示当前工单、附件文件名和图片识别时限，返回页面接续，无新增轮询。RM 可选择组与北京时间窗口生成、取消、编辑及下载；Owner 只读历史和下载。编辑有未保存保护及 revision 冲突提示，账号切换清除草稿和进度连接。页面参数为 `group/since/until/report_date`，每周一份，重复生成成功后覆盖该周结果（保留 ID、递增 revision 并刷新详情缓存），失败或中止不保存未完成内容；其他周历史保留。结果包括正文、交付说明、来源和 LLM 用量。

- QA「测试命令」不再显示 hpc_version 输入框，查询和刷新固定请求 latest，实际 HPC 版本显示在查询按钮旁；相同命令且其他字段完全一致的 GPU 合并显示，隐藏 py_label、torch_label 两列，CSV 保留原始逐 GPU 行及完整列供 QA log 回填；「开发者社区产物」的 SDK 版本标签、输入框、获取和下载按钮同一行，窄屏按控件换行。测试命令结果区不重复标题，CSV 导出位于工具栏右侧；「QA log」在顶部并排保留查询结果、刷新结果和导出 CSV，表格内不重复刷新或导出。两页去掉表格上方的全表关键词筛选框，保留逐列筛选；QA log 仍可使用顶部关键词查询。

- 新建组件记录时固定提供合并的 `driver / SDK`（component=`driver-sdk`），同一 MACA 版本共用一份联合结论，标注“全部 N 个 App”，覆盖兼容性验证清单中的全部已成功发布且仍在运行的 App；从未成功发布的 App 不再纳入新记录，已有 driver/sdk 独立记录保持原范围和结论，列表与标题标注为历史独立记录；新建接口兼容 driver/sdk 旧参数并归一到 `driver-sdk`，同版本重复创建返回 409。组件列表响应带 `preview_id`，创建 POST 一并提交，仅在当前账号/角色下复用该表单 5 分钟内的解析结果；服务端复核配置变化，避免再跑完整 Harbor/Gerrit。普通实时查询不使用此暂存结果。组件查询失败后不自动重跑整批，表单展示具体原因并提供重试/刷新入口。

- 「兼容性验证」新增「组件兼容性结论」子 tab，直达 `/dependencies?tab=components`，原兼容性清单和开发态依赖入口保留。QA/RM 通过 `GET /api/component-compatibility/components` 查询当前正式版组件后，`POST /api/component-compatibility {component,component_version}` 新建固定 App/测试范围快照；重复组件版本返回 409。列表与详情分别 GET `/api/component-compatibility`、`/{id}`，仅读库。`POST /{id}/apps/{app_id} {qa_status,qa_comment,revision}` 逐 App 保存，状态只允许 pass/fail，comment 永远选填，冲突保留草稿。Owner 查看时无编辑入口。表格复用 QA 的表格/筛选样式，测试范围复用 `TestScopeTable showCommand={false}`；窄屏按 App 重排。查询键按账号、角色、记录隔离，进入和手动刷新更新，不轮询；新建和保存的迟到响应不进入其他账号，页面切换和刷新有草稿保护。后端测试见 `tests/test_component_compatibility.py`；浏览器使用隔离后端 `--dependencies` 和 `COMPONENT_CONCLUSIONS_E2E_URL` 执行 `e2e/component-conclusions.spec.ts`。

- QA log 的 qa_env 渲染为可悬停/键盘聚焦的环境页入口；`GET /api/qa/test-results/{report_id}/environment?name=…` 只读返回原始工作簿同名环境页的单元格文本，不执行公式或改写原文件。首次打开才请求，以账号、角色、report_id、环境页名为缓存键，不轮询；不同工作簿的同名环境独立，切换账号即隐藏旧预览。预览通过 portal 固定在视口内，避免被报告表格滚动容器裁切；移开、失焦、Escape、滚动或调整窗口后关闭。失败原因在预览框显示；完整内容可下载原始 Excel。

- QA 内容区共用一排平级标签：QA report、本期发布app、测试命令、QA log、开发者社区产物、历史查询、兼容性验证。前五项用 `/qa?tab=mark|release|test|report|community` 保留选中项，支持刷新和浏览器前进/后退；后两项仍使用 `/lookup`、`/dependencies`，仅 RM、Owner、QA 可访问，前后端共用权限矩阵。所有直达链接都保持 QA 顶栏选中。
- 历史查询默认打开「发布app列表」，排在「按镜像 digest」之前，移除「按 App」界面。`GET /api/lookup/releases` 返回已发布锁定周期（最新优先），选择版本自动请求 `/api/lookup/release?release_id=…`，展示当期发布成功的 App、版本、完整镜像引用及 QA 结果/备注。失败和撤回报名不列入；缺失或无效镜像信息保留原字段并明确标注。版本与列表查询键含账号、角色和周期，进入时重取，另提供显式刷新；不请求全量 `/api/state`。表格支持关键词筛选，跨周期加载或出错时清除旧版显示。旧 `/api/lookup/app`、`/api/lookup/digest` 接口保留兼容。
- QA report 不再上传文件或自动读取旧日志 AI 建议；旧上传接口返回 410，原日志下载保留。`GET /api/qa/report?release_id=…` 一次返回页面状态、失败汇总、交付镜像展示及 QA 审计；子部分错误在 `qa_report.errors` 分别标明，不隐藏其他结果。页面不再另查 state、failure-summary、delivery-images 或逐 App 审计，原读取接口保留兼容。初次无周期时由该响应初始化共享选择，App 的 ReleaseSeeder 跳过该入口，避免重复请求；查询键按账号、角色及周期隔离，保存、刷新和重新进入均只取一次。失败汇总按周期读取当前保存的 QA log，只汇总 qa_status=fail（缺状态的旧记录用 qa_result=fail 文本补充），不查询 Harbor/Gerrit；同 App/分支下同 GPU、平台、HPC/App 版本和原因的测试项合并展示，保留实际测试版本。无法关联本期有效报名的失败项单独列出。结果查询键包含身份与周期，进入、显式刷新和 QA log 上传后更新，无轮询，加载/报错时隐藏旧失败项。每个 App 卡片最后提供手填 QA comment，所有 QA 状态均为选填，允许留空或清空旧备注，独立于报告中的 qa_comment，随 QA 状态保存到 qa_note，通过/未测试也保留；已发布只读，冲突保留草稿并提示刷新。仅修改 comment/结论时提交 delivery_revision，不重传未改动的镜像，避免重复 Harbor 校验。QA report 查看与编辑模式均按 entry.delivery_builds 展示全部启用构建，缺少交付镜像时显示「未填写交付镜像」，保留 OS、CPU、release/dbg 与 Python/PyTorch 配置；占位行只参与展示，不写入交付数据。每个 App 卡片正文首先显示 QA 状态，其后为默认折叠的 QA log 失败项，标题保留数量，点击展开详情。
- 「兼容性验证」（`/dependencies`）统一消费 `GET /api/lookup/latest-running`，仅显示 CICD 运行中且存在有效成功发布的 App，每个 App 选最近一次有效成功发布；从未成功发布的 App 不列入清单。与开发态依赖一致，按依赖组件和 Python/PyTorch 组合显示折叠分类，再展开 App 查看镜像和命令。分类针对每个镜像实际依赖，同一 App 可属于多个分类；计数按 App ID 去重，CSV 不重复分组行。每个交付镜像分别通过公共批量 digest → Harbor tags → commit → Gerrit app_info 实时解析，平台、测试项、命令和依赖取自该镜像的 commit；明细 commit id 位于镜像列后，显示前 12 位，悬停及 CSV 保留完整值。不同镜像允许不同 commit，单镜像来源冲突/查询失败明确列出并清空命令，不套用其他快照。仅排除当前 app_info 明确 enabled=false 的构建，不借用最新开发态命令或依赖；发布清单不改写。没有交付镜像的构建保留报名模板并标注，镜像 commit 留空。明细中仅 GPU 不同而其余字段一致的记录合并显示，GPU 以逗号分隔，命令数按合并行统计；不同平台、镜像、构建、commit、命令或 QA 说明分别保留。先筛选后合并，CSV 保留筛选后的逐 GPU 行，使用 QA report 的 image:tag@digest；历史缺 tag 仅由实际 Harbor tags 补全。说明按同周期、同 App ID／分支／App 版本／平台／GPU／Python／PyTorch／测试项引用 QA log 的 HPC 版本和 pass/fail，pass 隐藏原因，其余显示 qa_comment；不同测试镜像标注历史参考。已识别的 Python/PyTorch 组合直接归类；选定组件只显示对应分组并默认展开。SDK、driver 合为 `driver / SDK` 分组及筛选项，覆盖清单内全部 App，共用 shared/domain_metadata.json 的 universal_components 和 universal_component_group 定义，不要求依赖声明。取消 Python/PyTorch 筛选框，保留组件、CPU、OS、GPU、关键词筛选并同步作用于明细和 CSV。不显示范围待确认分区、行内提示及 CSV 范围状态列；未声明依赖及解析失败的记录仍可在 driver / SDK 下查看，具体解析错误保留。权限与查询键按账号/角色隔离，进入与手工刷新更新，不新增轮询；加载或失败时隐藏旧清单并禁用导出。
- 历史查询镜像模式使用 `POST /api/lookup/images {images: string[]}`，支持多行粘贴和 `.txt`/`.csv` 上传（最多 500 镜像）。每次实时执行 Harbor tags → commit → Gerrit app_info；App 版本可含连字符。repo tags 通过同平台、同构建号的发布版本 tag 和登记 manifest 路径定位公开固定 XML，解析其中 app_info 项目的完整 commit；响应 `commit_source/manifest_urls` 标明来源，归档不可用或不固定 commit 时明确报错。分支归属不明确时，允许使用 Gerrit 唯一精确匹配该 commit 的分支 HEAD，否则保留错误；不使用本地发布清单生成命令。请求内去重并最多 16 个并发读取，同一仓库/commit 只拉取及展开一次。HTTP/TLS 与 Gerrit SSH 连接按批次复用；同一 manifest 仓库的 XML 批量读取，固定归档去重，不跨请求缓存内容，结束后关闭连接。历史 HPCC 镜像支持 `*-hpcc` / `*-hpcc-dbg`，仅在仓库定位和 tag 模式匹配时对应 MACA 名称；不修改实际镜像引用，也不混合 release/dbg 或放宽 OS/架构/Python/Torch 标签校验。返回 `images[]` 来源概要、`errors[]` 单镜像错误、`duplicate_count` 及 `columns/rows` 平面表，行按声明的平台/GPU/测试项展开，不能从芯片并集补组合。表格复用 QA「测试命令」的字号、间距、表头排序指示、固定首列和逐列筛选框，支持全表与逐列组合筛选、排序和 CSV；commit 显示前 12 位，悬停、筛选和导出保留完整值。`test_result/test_digest/test_env` 按七字段匹配历史结果，无记录留空。新查询、账号/角色及入口切换清除旧结果，不轮询。单镜像 `GET /api/lookup/image` 同样始终实时生成。app_info 来源链接固定 image/commit 从 Gerrit 读取；显式附带发布参数的旧来源链接仍读取选定的封存 app_info。
QA 的测试命令、按镜像 digest 查询及 QA log 表格以 `app_id` 为首列，替代旧 `app_name`；上传须使用网站导出的 ID，按 ID、分支、HPC/App 版本、平台、GPU、Python/PyTorch 和测试项分别校验与保存。官方名称仅用于展示，不参与测试结果身份匹配；同名不同仓库的 App 不合并。本期发布 App、历史发布 App 列表及兼容性验证保留官方名称，单独增加 `app_id` 列。旧报告读取时结合已存 commit、镜像仓库、版本和分支识别 ID，不能唯一识别的保留并提示重新上传；不改原始文件或数据库。

- QA「测试命令」使用 `QaTestCommandsPane`，通过 `GET /api/qa/test-commands?release_id=…&hpc_version=…` 手动查询。只查询 app_info 生成的 MACA release/dbg 仓库，不展开 HPCC 别名；缺少 MACA 镜像时保留 no_image。输入具体补丁版本或 latest；latest 在当前发布周期的版本系列内，按 App 身份及完整构建镜像模式分别选择最大数字补丁版本（含 OS/架构/Python/Torch/release/dbg），每行填写各自实际 hpc_version，无可用镜像的构建为 no_image 且版本留空。响应 hpc_version 为 latest 或指定版本，hpc_versions 列出实际选中版本；页面显示各版本，CSV 保留逐行版本。相同 digest 的多个平台/补丁别名去重读取，生成时仅使用当前构建及其选中版本，避免跨构建串用或重复展开。Harbor tags 分页、请求内并发读取后，经实际 digest/commit/Gerrit app_info 生成命令，website_docker_cmd 使用与该行 HPC 版本及构建匹配的实际 image:tag@digest（优先无构建号 tag，否则取最大实际构建号）；QA log 也按每行保存的 HPC 版本选择 tag。缺镜像行保留为 no_image，上游失败另报错误。返回固定 columns/rows 顺序与根 README 相同，使用 py_label、torch_label、website_docker_cmd，以及六个始终空白的 qa_* 列。不轮询或跨请求缓存，切换周期/账号或改输入清除旧结果；迟到响应不覆盖新页面。
- QA「QA log」（`/qa?tab=report`）使用 QA 顶部的共享发布周期选择。GET `/api/qa/test-results?release_id=…&search=&all_rows=true` 及 `/export` 均限定所选周期，再执行关键词查询、逐列筛选和排序。页面一次展示全部匹配结果，不分页；`all_rows=true` 让后端复用全量查询，不受默认 50 行或分页上限 200 行限制，未提供该参数的旧调用仍保持分页。查询键含账号/角色/周期/筛选/排序和全量标记。切换周期重置查询和筛选，重新进入取最新数据。上传 `POST /api/qa/test-results/upload {release_id,filename,content_base64,revision?}` 仅 QA/RM 可用；上传时前 12 列及 QA 命令的 tag/digest 仍逐项校验，所有有效行的 HPC 版本必须属于所选周期。每个周期只保留一个当前工作簿，全部通过后事务内删除该周期原结果与工作簿再写入，文件中未出现或 QA 列为空的旧测试行也删除，其他周期保留。校验或写入失败保留原报告；revision 及写入前复核防止并发覆盖。上传处展示醒目的整份替换提示，选择文件后用统一确认弹窗显示周期、文件名及删除旧结果/旧文件的范围；取消不发上传请求，确认期间切换周期则不提交。上传响应包含 release_name、release_test_count（有效报名锁定范围按构建/GPU/测试项展开，含缺镜像项，不访问 Harbor）、total_rows（测试页非空行数）、rows（实际保存数）、skipped_rows（空 QA 行数）、duplicate_rows（被后续重复行覆盖的数量）及 skipped_apps（空 QA 行按 App/分支汇总）。页面同时显示版本测试总数、文件条数、保存数及总跳过数（skipped_rows + duplicate_rows，保证文件条数 = 保存数 + 总跳过数），并列出空 QA 与重复行的分类计数和清除的旧结果数，可展开查看跳过明细，明确跳过行未校验、未入库；已填写行仍严格匹配 branch。全部为空则报错说明跳过数量，不替换原报告。上传成功后清空筛选并显示全部结果。环境页原样保留，qa_env 与页签名对应；qa_result 保留数字或文本，qa_status 限 pass/fail 或空。website_docker_cmd 每次从 qa_digest 实时重建，qa_docker_cmd 保留上传原文；查询另显示提交时间、提交人和当前 Excel 下载。FlatCommandTable 的全量筛选及排序在后端完成，输入防抖 350ms；导出包含所选周期全部匹配记录，使用同组 search/filter/column_filters/sort_by/sort_direction 条件。
- App、QA 测试范围继续使用后端 test.platforms 的 OS/CPU/GPU 引用关系；版本查询按声明的合法组合展开，不能从芯片并集补组合。原 `/api/qa-reports` 保留本期发布信息及旧命令表供既有调用方读取，当前测试命令页面使用新的版本查询接口。

- 兼容性验证的组件声明及 PyTorch + Python 组合来自所选发布的同一 app_info 快照；组合取同一启用构建的两个 label，不跨构建拼接，缺任一标签不生成，按 App 去重。新发布的依赖、平台、镜像、测试排除项和最终命令均优先取 `official_versions.test_manifest_json` 的封存清单，`manifest_source=sealed`；不再调用当前命令生成器重算。CSV 包含没有关联测试的镜像；未填镜像明确标记模板，未指定构建的旧镜像不猜测平台，快照缺失不借用开发版。旧发布不自动补封存，`manifest_source=reconstructed` 表示按历史配置与现行规则还原，明细说明和 CSV 清单来源均明确标注；`unavailable` 表示历史快照缺失/归属错误，`unpublished` 表示尚未发布。已存在但版本不支持或损坏的封存清单会报错，不回退生成命令。原始 QA 按 App 级封存，每个 App/周期只保留一份最终记录；解锁删除原记录及清单，重新发布保存替换结果，不保留修订或撤销状态。
- 「开发态依赖」仅作为次级参考入口，同样只纳入 CICD 运行中且曾报名的 App（含已撤回报名），组件分组默认折叠，展示最新 app_info 的依赖、版本和 commit，不混入正式版镜像或命令，不提供清单导出。切换入口重新挂载表单并清空筛选。旧 `/api/dependencies?source=official` 与 `/api/dependencies/compat.csv` 保留兼容，合并页面不再分别请求它们。
- MCP 对 Guest 开放接入配置与个人使用统计；可用工具从后端 `/api/mcp/info` 获取，Guest 只显示三个只读 JIRA 工具及 `skill_load`。页面根据接口的 `jira_read_projects` 显示仅可读取 MC3 的范围，按实际工具列表展示操作示例，不显示无权使用的评论、上传示例。工具权限和项目限制由服务端执行，客户端重连获取最新工具列表和 instructions 即可，无需重装接入脚本。
- MCP 使用统计的时间范围、用户和 Token 筛选在桌面端横向并排，选择框按内容设置宽度，不各占整行。 页面保留总调用、工具调用、使用人数、失败卡片及每日/按工具/按用户统计，移除会话数、按 token 表格、客户端分类和常操作对象面板。统计列表与调用日志按实际行高计算 10 行显示上限，超出后在各自容器内滚动，表头保持固定；展开日志详情不增加容器高度。调用日志标题不换行，与筛选框保持同一行并水平对齐。
- 周期选择使用共享 `selectedReleaseId`，跳转 App 时保留正确周期；不把不同 release 的编辑草稿或 QA 建议混用。
- 总览和 App 工作台工具栏的报名截止、QA 截止、发布日横向并排，标签在上、日期在下；空间不足时按完整日期项换行。
- 总览统计卡片旁显示所选周期 `release.environment` 的组件、版本、说明，取代独立发布时间线；仅展示，随共享周期选择和显式刷新更新，不单独请求或轮询。
- 周期管理基础镜像每行两个输入：完整镜像引用、说明；提交 `image: {id?, reference, note}`，名称/地址/digest 由后端解析。历史查询和基础镜像输入使用 `stripDockerPullPrefix` 即时移除 `docker pull`，后端共享镜像解析器也接受该前缀。读取模型保留原字段，显示及编辑时组合引用；校验失败保留输入并显示错误。
- 保留未保存编辑保护，选中 App 后详情应直接可见，长列表与详情按需独立滚动。
- App 详情默认「基本信息和app_info」，不显示报名 tab。四项基本信息单独保存，资料保存不提交这四项，避免跨 tab 覆盖。已取消每日 app_info 自动同步；单 App 与全量同步复用公共 Gerrit 读取层，全量批次最多 16 个 App 并行拉取、调用线程串行入库，同批相同仓库/分支/commit 的读取去重。新一批全量同步重新检查源，HEAD 不变时跳过文件读取；写入前检查来源/运行状态/同步版本，避免旧读取覆盖新状态。单 App「更新 commit 和 app_info」重新核对仓库 commit 和内容，commit 不变时跳过保存；commit 变化时即使 JSON 内容相同也保存新版本；按钮位于「最新 app_info」标题旁，无 app_info 时也显示该标题与同步入口；范围扩大和同步失败提醒位于 tab 外，切换 tab 后保留，提醒只随显式刷新或写后重取更新。
- 底部「正式版发布历史」消费 `/api/apps/detail` 的完整 `official_versions`：正式版表与尚无正式版行的已冻结成功发布报名合并，每个 App/周期一份，按周期发布日倒序，同日按周期创建顺序，不按 App 版本字符串去重，不显示撤销状态。由报名补充的行 `id=null`，使用 `entry_id` 保持稳定的行标识；读取不写入正式版表；当前正式版和历史查询使用相同的周期顺序，重发旧周期不会覆盖较新周期。进入基本信息时重取，工作台手动刷新同时失效当前 App 详情；加载失败单独提示。
- `DeliveryImages` 在 QA、App 发布历史、历史发布列表共用，按 OS/CPU、镜像类型、Python/PyTorch 展示每个完整镜像引用。表格中每个镜像分两行：第一行四列平台信息，第二行横跨四列显示或填写完整镜像名，长引用自动换行，卡片内无需横向滚动；历史发布列表的 compact 展示保持不变。QA 的 `delivery_builds` 来自报名锁定 app_info 的启用 app_build；提交 `deliverable_images` 与 `delivery_revision`，只写已填写的构建行，要求 QA 按 `image:tag@digest` 填写，支持直接粘贴 `docker pull`。后端验证构建归属、重复行和 sha256，并按完整仓库路径与 digest 实时查询 Harbor：镜像须存在，OS 和 CPU 架构均以实际 tags 校验，QA 填入的 tag 必须存在于该 digest 的实际 tags 中，且该条 tag 须匹配锁定 app_info 的完整构建 tag（含 OS、CPU 架构、版本、Python/PyTorch，支持 HPC 版本/commit 和末尾构建号）；release/dbg 按镜像名区分（分别为 `*-maca` / `*-maca-dbg`），须与所选构建一致，digest 也必须存在于该类型对应的仓库中。Python/PyTorch 标签须与所选构建完全一致，且必须在同一条实际 tag 中同时匹配，不能跨 tags 拼接。不使用 Harbor 的 architecture/os 元数据，输入 tag 须逐项校验。保存不自动补齐或替换输入；QA report 的查看模式通过聚合响应 `qa_report.delivery_images`，按锁定构建和原交付 digest 读取 Harbor 实际 tag，补全历史 image@digest 的显示；已存 tag 保留。与兼容性验证共用请求内去重、并行读取规则；不改数据库、不改变编辑输入或仅改 comment 时的提交内容。展示数据按 entry revision 核对，过期数据提示刷新，失败保留原镜像并显示原因；镜像读取失败时仍返回 QA log 失败汇总。查询键含账号/角色/周期，进入和显式刷新更新，不轮询。缺少可用 tags、平台不符或查询失败明确报错，整批不保存，页面保留草稿供修正。相同镜像只在本次保存内去重查询，不跨请求缓存；外部查询不占写事务，写入前重读发布状态并用 revision 防止并发覆盖。重新提交未指定构建的旧镜像需先改填到明确构建行；旧标量接口不能绕过平台校验。`entries.deliverable_images_json` 保存当前镜像组，`official_versions.images_json` 保存发布时快照；旧标量字段保留首条供旧调用方读取。旧单条数据以「未指定构建」显示，不推断平台；所有 digest 均可查询。
- 应用标题栏「撤销报名」仅对可编辑 App 的 Owner/RM、当前有效报名且未发布锁定的周期显示；取消不提交，确认后回读报名状态。后端在同一写事务中检查权限/锁定并撤销；报名截止后不能重新报名，发布后不提供入口。
- 报名入口为工具栏「批量报名」和应用标题栏「报名」。弹窗按账号、角色和周期独立查询最新状态及环境；切换周期清除勾选，成功后失效共享 state/CICD 缓存并跳到报名周期。批量接口只接收本人负责的 App，整批原子提交；单 App 沿用报名接口及 RM 代报名权限。
- Markdown 的 `dangerouslySetInnerHTML` 出口集中在 `Markdown.tsx` 的 DOMPurify 流程；业务组件使用该组件。
- 日期输入使用 `DateInput` 和统一归一化，显示/提交 `YYYY-MM-DD`。
- 新服务端时间是北京时间无 offset 字符串；旧数据可能仍含 UTC ISO。当前 formatter 不做完整的旧数据时区转换，禁止给所有值统一加 8 小时。

## 验证

`npm run build`、`npm run lint`、`npm test`；只做类型检查用 `npx tsc --noEmit`，不会改动正在服务的 `web_dist/`。E2E（Playwright 固定访问 5176，不自动启动前后端，会写数据）的启动步骤见 [验证说明](../.agents/skills/release-system-dev/references/verification.md)。


### 测试用例页面的隔离验证

后端运行 `.venv/bin/python -m tests.run_jira_testing_site --port 18781`，前端运行 `API_TARGET=http://127.0.0.1:18781 npm run dev -- --host 127.0.0.1 --port 18782 --strictPort`。后端使用临时数据库、临时 skill 仓库和本地 Codex 协议测试端，不执行真实 GPU 作业或访问 Jira。

需要检查默认用例展示时，在上述后端命令加 `--builtin-cases`，会将仓库内置输入加载到临时数据库，不带入部署环境中的测试记录。

从 `web/` 执行 `JIRA_TESTING_E2E_URL=http://127.0.0.1:18782 npx playwright test e2e/jira-testing.spec.ts`。未设置此变量时该测试跳过，不能将它指向实际部署。覆盖 RM 导入、启动配置与 SSH 验证、表格单条展开、评价草稿保留、历史输入绑定、排队与取消、过程折叠后的状态更新、文件回收重试和 Owner 拒绝访问。使用本地协议端验证页面与后端请求，不检验模型分析能力。

### 自动周报隔离验证

后端运行 `python -m tests.run_auto_weekly_report_site --port 18793`；前端在 `web/` 运行 `API_TARGET=http://127.0.0.1:18793 npm run dev -- --host 127.0.0.1 --port 18792 --strictPort`。执行 `AUTO_WEEKLY_E2E_URL=http://127.0.0.1:18792 npx playwright test e2e/auto-weekly-report.spec.ts`，使用临时库和 JIRA/LLM 替身验证真实生成、历史、下载、编辑回读、冲突保留草稿、切换未保存保护、刷新后接续、中止/失败不覆盖、Owner 权限和窄屏，另验证图片超时后跳过后续图片、继续生成并提示未识别附件。后端回归为 `tests/test_auto_weekly_report.py` 和 `tests/test_llm.py`，不访问真实 JIRA 或模型。

### MCP 配置页面隔离验证

后端运行 `python -m tests.run_mcp_setup_site --port 18785`；前端在 `web/` 运行 `API_TARGET=http://127.0.0.1:18785 npm run dev -- --host 127.0.0.1 --port 18786 --strictPort`。再执行 `MCP_SETUP_E2E_URL=http://127.0.0.1:18786 npx playwright test e2e/mcp-setup.spec.ts`。测试使用临时数据库验证客户端折叠、复制命令和生成 token 后填充，不访问真实集成。

### App 详情隔离验证

后端运行 `python -m tests.run_app_info_site --port 18787`；前端在 `web/` 运行 `API_TARGET=http://127.0.0.1:18787 npm run dev -- --host 127.0.0.1 --port 18788 --strictPort`。再执行 `APP_INFO_E2E_URL=http://127.0.0.1:18788 npx playwright test e2e/app-info.spec.ts`。使用临时数据库与本地 Gerrit 响应，验证 RM/Owner 保存和回读、权限、未保存修改保护、跨周期范围扩大提醒及同步失败；不访问真实仓库。

正式版历史验证使用同一后端命令加 `--legacy-history`，执行 `APP_HISTORY_E2E_URL=http://127.0.0.1:18788 npx playwright test e2e/app-official-history.spec.ts`。验证早期迁移记录的补充、发布周期切换、失败及撤回过滤、解锁删除旧清单、重新发布替换结果、重发旧周期仍保持新周期为最新正式版及刷新回读，以及 Owner 和窄屏展示。

多镜像和撤销报名验证使用后端参数 `--platforms`，执行 `MULTI_DELIVERY_E2E_URL=http://127.0.0.1:18788 npx playwright test e2e/multi-delivery.spec.ts`。覆盖 Owner 取消/确认撤销、QA 查看 QA log 失败汇总并按 x86/ARM 保存两条 digest、发布冻结、App 历史及历史查询回读。RM 全量同步仅在所选周期报名截止前可用，App 工作台和周期管理均禁用截止后的入口；POST `/api/app-info/sync-all {release_id}` 后端及后台任务会复核所选周期的阶段，执行中截止则停止后续保存，并在任务汇总说明原因。状态接口不再返回 daily 计划，页面明确说明已取消每日同步。单 App 手动同步及新建 App 生效后的首次拉取保留。后端 `tests/test_app_info_sync_policy.py` 覆盖截止边界、强制请求、权限和执行前复核，`tests/test_app_info_parallel_sync.py` 覆盖并发拉取途中截止后的结果丢弃；`e2e/app-info.spec.ts` 验证两个页面入口、release_id 提交及接口拒绝。

QA report 历史镜像显示验证使用隔离后端参数 `--platforms --qa-delivery-display`，执行 `QA_DELIVERY_DISPLAY_E2E_URL=http://127.0.0.1:18788 npx playwright test e2e/qa-delivery-display.spec.ts`。覆盖实际 tag 补全、只改 comment 不提交镜像、数据库保留原引用、查询失败提示与跨周期隔离；后端回归见 `tests/test_qa_delivery_display.py`。

报名弹窗验证使用同一后端命令加 `--signup`，并执行 `APP_SIGNUP_E2E_URL=http://127.0.0.1:18788 npx playwright test e2e/app-signup.spec.ts`。覆盖批量候选筛选、组件环境、取消、跨周期选择、提交后状态、打开弹窗后新增停止申请的整批回滚，以及 RM 单个代报名和窄屏布局。

QA 导航与权限验证使用不带 `--signup` 的同一隔离后端，执行 `QA_NAV_E2E_URL=http://127.0.0.1:18788 npx playwright test e2e/qa-navigation.spec.ts`。覆盖 Owner/QA 子页签、历史查询、兼容性验证导出及深链接，以及 Guest/SPD 的界面和接口拒绝访问。

组合依赖验证使用隔离后端参数 `--dependencies`，执行 `DEPENDENCIES_E2E_URL=http://127.0.0.1:18788 npx playwright test e2e/dependencies.spec.ts`。覆盖 Owner/QA 的 App 默认收起、展开与键盘操作、正式版/开发态来源切换、组合配对与去重、两个入口均排除未报名 App、SDK/driver 全量范围、组件精确筛选、隐藏范围待确认、GPU 展开、QA report 镜像优先及 QA log 版本/结论/原因说明、平台组合筛选及导出一致、缺失镜像模板、无关联测试镜像、刷新失败隐藏旧清单，以及窄屏展示。

基础镜像验证使用同一隔离后端，执行 `BASE_IMAGE_E2E_URL=http://127.0.0.1:18788 npx playwright test e2e/base-images.spec.ts`。覆盖完整引用新增及解析回读、旧记录编辑、格式错误保留、digest 查询、跨周期显示和发布后只读。

总览组件信息验证使用加 `--signup` 的隔离后端，执行 `DASHBOARD_COMPONENTS_E2E_URL=http://127.0.0.1:18788 npx playwright test e2e/dashboard-components.spec.ts`。覆盖跨周期展示、RM 修改环境后显式刷新、保留顶部日期，以及 Owner/QA/Guest 的只读展示。

镜像历史查询验证使用同一隔离后端加 `--lookup`，执行 `IMAGE_LOOKUP_E2E_URL=http://127.0.0.1:18788 npx playwright test e2e/image-lookup.spec.ts`。上游使用本地 Harbor/Gerrit 响应，验证完整引用提交、历史版本命令、固定 digest、数据挂载、错误后清除旧结果及角色权限。

跨平台范围验证使用隔离后端参数 `--lookup --platforms --qa-commands`，执行 `TEST_PLATFORMS_E2E_URL=http://127.0.0.1:18788 npx playwright test e2e/test-platforms.spec.ts`。同一份包含不同 OS/x86/ARM 芯片组合的 app_info 验证 App 详情、QA 范围和命令以及 x86/ARM 镜像查询。

发布列表验证使用隔离后端参数 `--releases`，执行 `PUBLISHED_APPS_E2E_URL=http://127.0.0.1:18788 npx playwright test e2e/published-apps.spec.ts`。验证 RM/Owner/QA 默认入口、已冻结版本筛选、跨版本的完整镜像和 QA 结果、空版本、关键词筛选及窄屏；所有发布操作只用于临时库造数。

兼容性验证的浏览器验证使用隔离后端 `python -m tests.run_app_info_site --port 18787 --latest-running`，前端 `API_TARGET=http://127.0.0.1:18787 npm run dev -- --host 127.0.0.1 --port 18788 --strictPort`，执行 `LATEST_RUNNING_E2E_URL=http://127.0.0.1:18788 npx playwright test e2e/latest-running-releases.spec.ts`。验证 RM/QA/Owner 访问、后续失败回退、多 OS/CPU/镜像类型、曾报名但尚未发布、从未报名及已停止 App 排除、筛选与 CSV 导出。后端回归见 `tests/test_latest_running_releases.py`。

已发布镜像的实时 digest 浏览器验证使用后端参数 `--lookup --image-publications`，执行 `IMAGE_PUBLICATIONS_E2E_URL=http://127.0.0.1:18788 npx playwright test e2e/image-publications.spec.ts`；与既有 `image-lookup.spec.ts` 共用隔离服务，验证 RM/Owner/QA 始终使用实时 Harbor/Gerrit、官方 App 名称、固定 commit 来源、GPU 展开和窄屏。后端回归见 `tests/test_image_publications.py`。

QA log 验证使用后端参数 `--lookup --qa-commands`，执行 `QA_RESULTS_E2E_URL=http://127.0.0.1:18788 npx playwright test e2e/qa-results.spec.ts`。完整验证默认 latest 查询、统一列 CSV、no_image、QA 列留空、Excel 回填与校验拒绝、按周期整份替换、case A/B 清除、周期切换及原环境页字节不变；后端 `tests/test_qa_commands.py` 覆盖同一文件内 HPC/Python/Torch 十字段独立保存，`tests/test_qa_report_versions.py` 覆盖周期隔离、旧文件清理、回滚及并发冲突，`tests/test_qa_results.py` 验证旧表格兼容与权限。

QA log 全量筛选验证：隔离后端加 `--lookup --qa-report-pagination`，在隔离 Vite 运行 `QA_REPORT_FILTERS_E2E_URL=http://127.0.0.1:5176 npx playwright test e2e/qa-report-filters.spec.ts`。65 条样本验证全部行直接展示、条件组合、实时命令关键词及全量 CSV 排序导出；后端另用 235 行验证全量读取不受分页上限限制。

QA 紧凑工具栏验证使用隔离后端参数 `--lookup --qa-commands`，执行 `QA_COMPACT_E2E_URL=http://127.0.0.1:18788 npx playwright test e2e/qa-compact-toolbars.spec.ts`，验证测试命令移除版本输入框且真实请求 latest、社区产物标签/输入框/按钮同行、请求参数及窄屏换行；社区外部下载服务使用浏览器替身。
