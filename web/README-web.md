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

现有按秒轮询只有这些：顶栏 CICD 通知角标（`TabNav`，所有已登录页面）、CICD 页的待审批/待交付列表与计数、QA AI 任务进度、JIRA agent 运行中对话的时间线、上传公钥终端及学习活动任务、测试用例页面中尚未结束的批次（5 秒，仅获取运行状态，不重复拉取输入文件）、周报检查运行中的作业进度。批量 Gerrit 拉取与 CICD 助手用流式接口。不要靠新增定时器修复局部失效问题。

查询键应区分身份、周期、过滤参数与响应形状。学习查询按账号隔离，退出、401 和重新登录时清理学习缓存，账号切换时重建页面清除草稿。其他查询仍未统一清理；维护认证或缓存时应处理这个已知缺口，不能认为切换账号天然隔离。

App 工作台请求 `include_allowed_actions=1`，其他 `/api/state` 调用不一定带此参数；现有页面存在共享查询键，修改时核对缓存实际来源。后端返回的 `allowed_actions` 用于展示，写接口仍应依据当前用户与当前资源重新鉴权。

HTTP 类型声明不会验证真实 JSON。改接口时对照 router/service、前端 API 包装和调用处，验证提交后回读；mock 的字段名与后端保持一致。`/api/me` 返回 `{user: ...}`，无会话时也是 HTTP 200、`user: null`。

## 交互与显示

- QA 内容区共用一排平级标签：QA log、本期发布app、测试命令、QA report、开发者社区产物、历史查询、兼容性验证。前五项用 `/qa?tab=mark|release|test|report|community` 保留选中项，支持刷新和浏览器前进/后退；后两项仍使用 `/lookup`、`/dependencies`，仅 RM、Owner、QA 可访问，前后端共用权限矩阵。所有直达链接都保持 QA 顶栏选中。
- 历史查询默认打开「发布app列表」，排在「按镜像 digest」之前，移除「按 App」界面。`GET /api/lookup/releases` 返回已发布锁定周期（最新优先），选择版本自动请求 `/api/lookup/release?release_id=…`，展示当期发布成功的 App、版本、完整镜像引用及 QA 结果/备注。失败和撤回报名不列入；缺失或无效镜像信息保留原字段并明确标注。版本与列表查询键含账号、角色和周期，进入时重取，另提供显式刷新；不请求全量 `/api/state`。表格支持关键词筛选，跨周期加载或出错时清除旧版显示。旧 `/api/lookup/app`、`/api/lookup/digest` 接口保留兼容。
- 「兼容性验证」（`/dependencies`）合并原历史查询基线与正式版兼容验证；组件筛选作为同一 App 清单的过滤条件。`CompatibilityPane` 统一消费 `GET /api/lookup/latest-running`，每个运行中 App 选最近有效成功发布（含迁移历史回查），默认一行展示 Owner、App/HPC 版本及镜像/测试项/命令数量，说明仅保留在展开后的镜像明细，展开显示平台、镜像、该次发布 app_info 的 commit id 与完整命令文本。commit id 位于镜像列后，显示前 12 位、悬停显示完整值，筛选与 CSV 使用完整值；缺少有效快照时显示空值，不使用开发版或镜像构建 commit。组件、CPU 架构、OS、GPU 芯片、Python/PyTorch 配置及关键词筛选共同作用于明细，CSV 导出当前结果。未声明依赖、依赖为空、缺少快照或尚未发布的 App 单列「范围待确认」；选择组件仍保留，导出标注范围状态。后续失败、未报名或撤回不替换此前成功版。权限与查询键按账号/角色隔离，进入与手工刷新更新，该面板不请求全量 state 或新增自动轮询（共用布局仍会初始化周期状态）；加载或失败时隐藏旧清单并禁用导出。
- 历史查询镜像模式使用 `POST /api/lookup/images {images: string[]}`，支持多行粘贴和 `.txt`/`.csv` 上传（最多 500 镜像）。每次实时执行 Harbor tags → commit → Gerrit app_info；App 版本可含连字符。repo tags 通过同平台、同构建号的发布版本 tag 和登记 manifest 路径定位公开固定 XML，解析其中 app_info 项目的完整 commit；响应 `commit_source/manifest_urls` 标明来源，归档不可用或不固定 commit 时明确报错。分支归属不明确时，允许使用 Gerrit 唯一精确匹配该 commit 的分支 HEAD，否则保留错误；不使用本地发布清单生成命令。请求内去重并最多 16 个并发读取，同一仓库/commit 只拉取及展开一次。HTTP/TLS 与 Gerrit SSH 连接按批次复用；同一 manifest 仓库的 XML 批量读取，固定归档去重，不跨请求缓存内容，结束后关闭连接。历史 HPCC 镜像支持 `*-hpcc` / `*-hpcc-dbg`，仅在仓库定位和 tag 模式匹配时对应 MACA 名称；不修改实际镜像引用，也不混合 release/dbg 或放宽 OS/架构/Python/Torch 标签校验。返回 `images[]` 来源概要、`errors[]` 单镜像错误、`duplicate_count` 及 `columns/rows` 平面表，行按声明的平台/GPU/测试项展开，不能从芯片并集补组合。表格复用 QA「测试命令」的字号、间距、表头排序指示、固定首列和逐列筛选框，支持全表与逐列组合筛选、排序和 CSV；commit 显示前 12 位，悬停、筛选和导出保留完整值。`test_result/test_digest/test_env` 按七字段匹配历史结果，无记录留空。新查询、账号/角色及入口切换清除旧结果，不轮询。单镜像 `GET /api/lookup/image` 同样始终实时生成。app_info 来源链接固定 image/commit 从 Gerrit 读取；显式附带发布参数的旧来源链接仍读取选定的封存 app_info。
- QA「测试命令」使用 `QaTestCommandsPane`，通过 `GET /api/qa/test-commands?release_id=…&hpc_version=…` 手动查询。只查询 app_info 生成的 MACA release/dbg 仓库，不展开 HPCC 别名；缺少 MACA 镜像时保留 no_image。输入具体补丁版本或 latest；latest 按当前发布周期的前三段版本选择最大数字补丁版本。Harbor tags 分页、请求内并发读取后，经实际 digest/commit/Gerrit app_info 生成命令，website_docker_cmd 使用与该行 HPC 版本及构建匹配的实际 image:tag@digest（优先无构建号 tag，否则取最大实际构建号）；QA report 也按每行保存的 HPC 版本选择 tag。缺镜像行保留为 no_image，上游失败另报错误。返回固定 columns/rows 顺序与根 README 相同，使用 py_label、torch_label、website_docker_cmd，以及六个始终空白的 qa_* 列。不轮询或跨请求缓存，切换周期/账号或改输入清除旧结果；迟到响应不覆盖新页面。
- QA「QA report」（`/qa?tab=report`）独立于周期选择；`GET /api/qa/test-results?search=&offset=0&limit=50` 返回同一列格式和最近结果，查询键含账号/角色/关键词/逐列筛选/排序/分页。全表关键词、逐列筛选及排序均在后端作用于全部结果，再按每页 50 行返回；输入筛选防抖 350ms，条件变化回到第一页。元数据筛选仅为当前页实时生成命令，命令正文或全表关键词筛选则实时生成全部候选命令后匹配。测试命令与结果使用 FlatCommandTable 的字号、间距、排序、固定首列和逐列筛选样式。`GET /api/qa/test-results/export` 使用同一筛选和排序条件导出全部命中记录，不受分页限制；两个接口共用 `search`、`filter`、JSON 对象 `column_filters`、`sort_by`、`sort_direction=asc|desc`。上传 `POST /api/qa/test-results/upload {filename,content_base64}` 仅 QA/RM 可用，首页测试行，后续环境页原样保留并核对 qa_env 页签名；qa_result 保留数字或文本，qa_status 限 pass/fail 或空。保存前逐项核对前 12 列身份、HPC 版本、Python/Torch 标签和 perf_golden/perf_unit，以及 QA 命令实际 tag/digest；缺列或不匹配会整份拒绝并提示字段/行号。数值基准按数值比较并保留 0。全部通过后按七字段加 hpc_version/py_label/torch_label 的十字段键保存，不同补丁或 Python/Torch 配置分别保留，同一组合再次上传覆盖；未填写 QA 列的行跳过。hpc_version 和 qa_digest_gerrit_commit_id 由真实镜像解析，website_docker_cmd 每次根据 qa_digest → Harbor tags → commit → Gerrit app_info 实时生成，上传该列的命令不作为来源，失败时不回退；页面明确说明。QA 命令保留原文。表格另显示提交时间、提交人和原 Excel 下载；外部失败时仍显示已存结果与 QA 命令，并在网站命令栏提示错误。旧 Excel 列名和历史记录只读映射兼容，不改写原工作簿。
- App、QA 测试范围继续使用后端 test.platforms 的 OS/CPU/GPU 引用关系；版本查询按声明的合法组合展开，不能从芯片并集补组合。原 `/api/qa-reports` 保留本期发布信息及旧命令表供既有调用方读取，当前测试命令页面使用新的版本查询接口。

- 兼容性验证的组件声明及 PyTorch + Python 组合来自所选发布的同一 app_info 快照；组合取同一启用构建的两个 label，不跨构建拼接，缺任一标签不生成，按 App 去重。新发布的依赖、平台、镜像、测试排除项和最终命令均优先取 `official_versions.test_manifest_json` 的封存清单，`manifest_source=sealed`；不再调用当前命令生成器重算。CSV 包含没有关联测试的镜像；未填镜像明确标记模板，未指定构建的旧镜像不猜测平台，快照缺失不借用开发版。旧发布不自动补封存，`manifest_source=reconstructed` 表示按历史配置与现行规则还原，明细说明和 CSV 清单来源均明确标注；`unavailable` 表示历史快照缺失/归属错误，`unpublished` 表示尚未发布。已存在但版本不支持或损坏的封存清单会报错，不回退生成命令。原始 QA 按 App 级封存，每个 App/周期只保留一份最终记录；解锁删除原记录及清单，重新发布保存替换结果，不保留修订或撤销状态。
- 「开发态依赖」仅作为次级参考入口，组件分组默认折叠，展示最新 app_info 的依赖、版本和 commit，不混入正式版镜像或命令，不提供清单导出。切换入口重新挂载表单并清空筛选。旧 `/api/dependencies?source=official` 与 `/api/dependencies/compat.csv` 保留兼容，合并页面不再分别请求它们。
- MCP 使用统计的时间范围、用户和 Token 筛选在桌面端横向并排，选择框按内容设置宽度，不各占整行。
- 周期选择使用共享 `selectedReleaseId`，跳转 App 时保留正确周期；不把不同 release 的编辑草稿或 QA 建议混用。
- 总览和 App 工作台工具栏的报名截止、QA 截止、发布日横向并排，标签在上、日期在下；空间不足时按完整日期项换行。
- 总览统计卡片旁显示所选周期 `release.environment` 的组件、版本、说明，取代独立发布时间线；仅展示，随共享周期选择和显式刷新更新，不单独请求或轮询。
- 周期管理基础镜像每行两个输入：完整镜像引用、说明；提交 `image: {id?, reference, note}`，名称/地址/digest 由后端解析。历史查询和基础镜像输入使用 `stripDockerPullPrefix` 即时移除 `docker pull`，后端共享镜像解析器也接受该前缀。读取模型保留原字段，显示及编辑时组合引用；校验失败保留输入并显示错误。
- 保留未保存编辑保护，选中 App 后详情应直接可见，长列表与详情按需独立滚动。
- App 详情默认「基本信息和app_info」，不显示报名 tab。四项基本信息单独保存，资料保存不提交这四项，避免跨 tab 覆盖。单 App、全量和每日 app_info 同步复用公共 Gerrit 读取层；全量/每日批次最多 16 个 App 并行拉取、调用线程串行入库，同批相同仓库/分支/commit 的读取去重。新一批重新检查源，HEAD 不变时跳过文件读取；写入前检查来源/运行状态/同步版本，避免旧读取覆盖新状态。单 App「立即同步app_info」位于「最新 app_info」标题旁，无 app_info 时也显示该标题与同步入口；范围扩大和同步失败提醒位于 tab 外，切换 tab 后保留，提醒只随显式刷新或写后重取更新。
- 底部「正式版发布历史」消费 `/api/apps/detail` 的完整 `official_versions`：正式版表与尚无正式版行的已冻结成功发布报名合并，每个 App/周期一份，按周期发布日倒序，同日按周期创建顺序，不按 App 版本字符串去重，不显示撤销状态。由报名补充的行 `id=null`，使用 `entry_id` 保持稳定的行标识；读取不写入正式版表；当前正式版和历史查询使用相同的周期顺序，重发旧周期不会覆盖较新周期。进入基本信息时重取，工作台手动刷新同时失效当前 App 详情；加载失败单独提示。
- `DeliveryImages` 在 QA、App 发布历史、历史发布列表共用，按 OS/CPU、镜像类型、Python/PyTorch 展示每个完整镜像引用。QA 的 `delivery_builds` 来自报名锁定 app_info 的启用 app_build；提交 `deliverable_images` 与 `delivery_revision`，只写已填写的构建行，支持直接粘贴 `docker pull`。后端验证构建归属、重复行和 sha256，并按完整仓库路径与 digest 实时查询 Harbor：镜像须存在，OS 和 CPU 架构均以实际 tags 校验，至少一条 tag 匹配锁定 app_info 的完整构建 tag（含 OS、CPU 架构、版本、Python/PyTorch，支持 HPC 版本/commit 和末尾构建号）；release/dbg 按镜像名区分（分别为 `*-maca` / `*-maca-dbg`），须与所选构建一致，digest 也必须存在于该类型对应的仓库中。Python/PyTorch 标签须与所选构建完全一致，且必须在同一条实际 tag 中同时匹配，不能跨 tags 拼接。不使用 Harbor 的 architecture/os 元数据，输入 tag 不能作为验证依据。缺少可用 tags、平台不符或查询失败明确报错，整批不保存，页面保留草稿供修正。相同镜像只在本次保存内去重查询，不跨请求缓存；外部查询不占写事务，写入前重读发布状态并用 revision 防止并发覆盖。重新提交未指定构建的旧镜像需先改填到明确构建行；旧标量接口不能绕过平台校验。`entries.deliverable_images_json` 保存当前镜像组，`official_versions.images_json` 保存发布时快照；旧标量字段保留首条供旧调用方读取。旧单条数据以「未指定构建」显示，不推断平台；所有 digest 均可查询。
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

### MCP 配置页面隔离验证

后端运行 `python -m tests.run_mcp_setup_site --port 18785`；前端在 `web/` 运行 `API_TARGET=http://127.0.0.1:18785 npm run dev -- --host 127.0.0.1 --port 18786 --strictPort`。再执行 `MCP_SETUP_E2E_URL=http://127.0.0.1:18786 npx playwright test e2e/mcp-setup.spec.ts`。测试使用临时数据库验证客户端折叠、复制命令和生成 token 后填充，不访问真实集成。

### App 详情隔离验证

后端运行 `python -m tests.run_app_info_site --port 18787`；前端在 `web/` 运行 `API_TARGET=http://127.0.0.1:18787 npm run dev -- --host 127.0.0.1 --port 18788 --strictPort`。再执行 `APP_INFO_E2E_URL=http://127.0.0.1:18788 npx playwright test e2e/app-info.spec.ts`。使用临时数据库与本地 Gerrit 响应，验证 RM/Owner 保存和回读、权限、未保存修改保护、跨周期范围扩大提醒及同步失败；不访问真实仓库。

正式版历史验证使用同一后端命令加 `--legacy-history`，执行 `APP_HISTORY_E2E_URL=http://127.0.0.1:18788 npx playwright test e2e/app-official-history.spec.ts`。验证早期迁移记录的补充、发布周期切换、失败及撤回过滤、解锁删除旧清单、重新发布替换结果、重发旧周期仍保持新周期为最新正式版及刷新回读，以及 Owner 和窄屏展示。

多镜像和撤销报名验证使用后端参数 `--platforms`，执行 `MULTI_DELIVERY_E2E_URL=http://127.0.0.1:18788 npx playwright test e2e/multi-delivery.spec.ts`。覆盖 Owner 取消/确认撤销、QA 上传 log 并按 x86/ARM 保存两条 digest、发布冻结、App 历史及历史查询回读。每日同步仅限「报名截止后、QA 截止前」周期中有效报名的运行中 App；报名未截止、QA 已截止和发布锁定周期不触发自动拉取，同 App 跨周期去重；手动和 RM 全量同步保持原范围。

报名弹窗验证使用同一后端命令加 `--signup`，并执行 `APP_SIGNUP_E2E_URL=http://127.0.0.1:18788 npx playwright test e2e/app-signup.spec.ts`。覆盖批量候选筛选、组件环境、取消、跨周期选择、提交后状态、打开弹窗后新增停止申请的整批回滚，以及 RM 单个代报名和窄屏布局。

QA 导航与权限验证使用不带 `--signup` 的同一隔离后端，执行 `QA_NAV_E2E_URL=http://127.0.0.1:18788 npx playwright test e2e/qa-navigation.spec.ts`。覆盖 Owner/QA 子页签、历史查询、兼容性验证导出及深链接，以及 Guest/SPD 的界面和接口拒绝访问。

组合依赖验证使用隔离后端参数 `--dependencies`，执行 `DEPENDENCIES_E2E_URL=http://127.0.0.1:18788 npx playwright test e2e/dependencies.spec.ts`。覆盖 Owner/QA 的 App 默认收起、展开与键盘操作、正式版/开发态来源切换、组合配对与去重、范围待确认保留、平台组合筛选及导出一致、缺失镜像模板、无关联测试镜像、刷新失败隐藏旧清单，以及窄屏展示。

基础镜像验证使用同一隔离后端，执行 `BASE_IMAGE_E2E_URL=http://127.0.0.1:18788 npx playwright test e2e/base-images.spec.ts`。覆盖完整引用新增及解析回读、旧记录编辑、格式错误保留、digest 查询、跨周期显示和发布后只读。

总览组件信息验证使用加 `--signup` 的隔离后端，执行 `DASHBOARD_COMPONENTS_E2E_URL=http://127.0.0.1:18788 npx playwright test e2e/dashboard-components.spec.ts`。覆盖跨周期展示、RM 修改环境后显式刷新、保留顶部日期，以及 Owner/QA/Guest 的只读展示。

镜像历史查询验证使用同一隔离后端加 `--lookup`，执行 `IMAGE_LOOKUP_E2E_URL=http://127.0.0.1:18788 npx playwright test e2e/image-lookup.spec.ts`。上游使用本地 Harbor/Gerrit 响应，验证完整引用提交、历史版本命令、固定 digest、数据挂载、错误后清除旧结果及角色权限。

跨平台范围验证使用隔离后端参数 `--lookup --platforms`，执行 `TEST_PLATFORMS_E2E_URL=http://127.0.0.1:18788 npx playwright test e2e/test-platforms.spec.ts`。同一份包含不同 OS/x86/ARM 芯片组合的 app_info 验证 App 详情、QA 范围和命令以及 x86/ARM 镜像查询。

发布列表验证使用隔离后端参数 `--releases`，执行 `PUBLISHED_APPS_E2E_URL=http://127.0.0.1:18788 npx playwright test e2e/published-apps.spec.ts`。验证 RM/Owner/QA 默认入口、已冻结版本筛选、跨版本的完整镜像和 QA 结果、空版本、关键词筛选及窄屏；所有发布操作只用于临时库造数。

兼容性验证的浏览器验证使用隔离后端 `python -m tests.run_app_info_site --port 18787 --latest-running`，前端 `API_TARGET=http://127.0.0.1:18787 npm run dev -- --host 127.0.0.1 --port 18788 --strictPort`，执行 `LATEST_RUNNING_E2E_URL=http://127.0.0.1:18788 npx playwright test e2e/latest-running-releases.spec.ts`。验证 RM/QA/Owner 访问、后续失败回退、多 OS/CPU/镜像类型、从未发布、停止 App 排除、筛选与 CSV 导出。后端回归见 `tests/test_latest_running_releases.py`。

已发布镜像的实时 digest 浏览器验证使用后端参数 `--lookup --image-publications`，执行 `IMAGE_PUBLICATIONS_E2E_URL=http://127.0.0.1:18788 npx playwright test e2e/image-publications.spec.ts`；与既有 `image-lookup.spec.ts` 共用隔离服务，验证 RM/Owner/QA 始终使用实时 Harbor/Gerrit、官方 App 名称、固定 commit 来源、GPU 展开和窄屏。后端回归见 `tests/test_image_publications.py`。

QA report 验证使用后端参数 `--lookup --qa-commands`，执行 `QA_RESULTS_E2E_URL=http://127.0.0.1:18788 npx playwright test e2e/qa-results.spec.ts`。完整验证版本/latest 查询、统一列 CSV、no_image、QA 列留空、Excel 回填与校验拒绝、覆盖保存及原环境页字节不变；后端 `tests/test_qa_commands.py` 另覆盖 HPC/Python/Torch 十字段独立保存与旧结果转换，`tests/test_qa_results.py` 验证旧表格兼容与权限。

QA report 全量筛选验证：隔离后端加 `--lookup --qa-report-pagination`，在隔离 Vite 运行 `QA_REPORT_FILTERS_E2E_URL=http://127.0.0.1:5176 npx playwright test e2e/qa-report-filters.spec.ts`。65 条样本验证首屏之外的匹配、条件组合、分页重置、实时命令关键词及全量 CSV 排序导出。
