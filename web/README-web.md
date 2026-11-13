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

- QA 内容区共用一排平级标签：QA log、本期发布app、测试命令、开发者社区产物、历史查询、组件依赖。前四项用 `/qa?tab=mark|release|test|community` 保留选中项，支持刷新和浏览器前进/后退；后两项仍使用 `/lookup`、`/dependencies`，仅 RM、Owner、QA 可访问，前后端共用权限矩阵。所有直达链接都保持 QA 顶栏选中。
- 历史查询默认打开「发布app列表」，排在「按镜像 digest」之前，移除「按 App」界面。`GET /api/lookup/releases` 返回已发布锁定周期（最新优先），选择版本自动请求 `/api/lookup/release?release_id=…`，展示当期发布成功的 App、版本、完整镜像引用及 QA 结果/备注。失败和撤回报名不列入；缺失或无效镜像信息保留原字段并明确标注。版本与列表查询键含账号、角色和周期，进入时重取，另提供显式刷新；不请求全量 `/api/state`。表格支持关键词筛选，跨周期加载或出错时清除旧版显示。旧 `/api/lookup/app`、`/api/lookup/digest` 接口保留兼容。
- 历史查询镜像模式调用 `GET /api/lookup/image?image=完整镜像引用`，展示 Harbor tags、commit、app_info 来源与固定到 digest 的 Docker 命令；由后端只读查询 Harbor/Gerrit，不依赖镜像的报名记录。来源链接在新页打开 `/api/lookup/app-info?image=…&commit_id=…`，按原 commit 从已登记仓库读取并展示原始 JSON；复用历史查询权限，不重新解析 Harbor tags，不写业务记录。
- App、QA、历史镜像查询的测试范围使用后端 `test.platforms`，逐行显示 OS、CPU 架构（x86/ARM）、支持芯片及命令，不从 `supported_chips` 的并集推断平台。镜像的两项芯片总览只来自实际匹配的镜像构建。QA 测试命令表/CSV 用 `commit_id` 替换 `base_image`，取当期锁定 app_info 版本的 commit；保留 OS、支持芯片和 Python/PyTorch label，构建别名与 OS/架构完全重复时省略整列，包含其他配置时保留。列结构变化时清除按列筛选和排序，避免索引错位。
- 最新 app_info 的 OS 与 CPU 架构分别使用独立信息框。QA 测试命令表中的 `commit_id` 显示前 12 位，悬停查看完整 ID；筛选、排序和 CSV 导出仍使用完整值。
- 组件依赖沿用组件分组与筛选，正式版视图的下拉项包含从 `official.app_info_version_id` 指向的同一启用构建提取的 PyTorch + Python 组合，名称简写为 `torch 2.10 + py312`。复用构建平台解析的标签选项及顶层默认值，缺任一标签时不生成组合；同一 App 的相同组合只计一次，禁用构建与停止 App 不列入。兼容验证 CSV 按相同组合筛选，依赖列保留普通组件及所有组合；无正式版的 App 不导出。`app_dependency` 的声明状态仍单独展示。

兼容验证默认只使用当前正式版：组件声明、Python/PyTorch 组合与命令均来自 `official.app_info_version_id`，镜像来自同一正式版冻结记录，不读取开发态依赖索引。开发分支更新不会改变旧正式版分组。独立「开发态依赖」视图只展示最新 app_info 的依赖和 commit，不混入正式版镜像、命令，不提供兼容清单导出；切换来源清空组件筛选。全量导出包含未声明依赖及显式空依赖的正式版；快照缺失或归属错误会明确提示并拒绝导出，不回退到开发版本。

- 各组件使用默认关闭的 `details`；其中每个 App 的镜像与测试命令也默认折叠，点击 App 名称独立展开，支持键盘操作，折叠时保留 Owner、HPC release 版本及镜像、命令数量。未声明依赖的 App 同样处理；版本列「最新hpc release版本」仅显示 `official.release_name`。`official.images` 消费最新发布的完整镜像快照，复用 `DeliveryImages` 展示全部 OS/CPU/标签/镜像类型；`tests` 由发布时锁定 app_info 和未排除测试生成，按相同构建标识绑定交付镜像并输出固定 digest 的 Docker 命令。缺少对应交付镜像时保留模板并明确提示，不把未指定构建的旧镜像套到所有平台。CSV 去掉 App 官方版本列，增加平台、镜像类型、标签、芯片和镜像来源，包含所有命令及没有关联测试的镜像。
- MCP 使用统计的时间范围、用户和 Token 筛选在桌面端横向并排，选择框按内容设置宽度，不各占整行。
- 周期选择使用共享 `selectedReleaseId`，跳转 App 时保留正确周期；不把不同 release 的编辑草稿或 QA 建议混用。
- 总览和 App 工作台工具栏的报名截止、QA 截止、发布日横向并排，标签在上、日期在下；空间不足时按完整日期项换行。
- 总览统计卡片旁显示所选周期 `release.environment` 的组件、版本、说明，取代独立发布时间线；仅展示，随共享周期选择和显式刷新更新，不单独请求或轮询。
- 周期管理基础镜像每行两个输入：完整镜像引用、说明；提交 `image: {id?, reference, note}`，名称/地址/digest 由后端解析。历史查询和基础镜像输入使用 `stripDockerPullPrefix` 即时移除 `docker pull`，后端共享镜像解析器也接受该前缀。读取模型保留原字段，显示及编辑时组合引用；校验失败保留输入并显示错误。
- 保留未保存编辑保护，选中 App 后详情应直接可见，长列表与详情按需独立滚动。
- App 详情默认「基本信息和app_info」，不显示报名 tab。四项基本信息单独保存，资料保存不提交这四项，避免跨 tab 覆盖。单 App「立即同步app_info」位于「最新 app_info」标题旁，无 app_info 时也显示该标题与同步入口；范围扩大和同步失败提醒位于 tab 外，切换 tab 后保留，提醒只随显式刷新或写后重取更新。
- 底部「正式版发布历史」消费 `/api/apps/detail` 的完整 `official_versions`：正式版表与尚无正式版行的已冻结成功发布报名合并，按发布时间倒序，保留撤销记录，不按 App 版本字符串去重。由报名补充的行 `id=null`，使用 `entry_id` 保持稳定的行标识；历史查询不写入正式版表，不改变当前正式版和兼容清单。进入基本信息时重取，工作台手动刷新同时失效当前 App 详情；加载失败单独提示。
- `DeliveryImages` 在 QA、App 发布历史、历史发布列表共用，按 OS/CPU、镜像类型、Python/PyTorch 展示每个完整镜像引用。QA 的 `delivery_builds` 来自报名锁定 app_info 的启用 app_build；提交 `deliverable_images` 与 `delivery_revision`，只写已填写的构建行，支持直接粘贴 `docker pull`。后端验证构建归属、重复行和 sha256，使用事务及 revision 阻止并发覆盖。`entries.deliverable_images_json` 保存当前镜像组，`official_versions.images_json` 保存发布时快照；旧标量字段保留首条供旧调用方读取。旧单条数据以「未指定构建」显示，不推断平台；所有 digest 均可查询。
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

正式版历史验证使用同一后端命令加 `--legacy-history`，执行 `APP_HISTORY_E2E_URL=http://127.0.0.1:18788 npx playwright test e2e/app-official-history.spec.ts`。验证早期迁移记录的补充、发布周期切换、失败及撤回过滤、解锁重新发布后的撤销标记和刷新回读，以及 Owner 和窄屏展示。

多镜像和撤销报名验证使用后端参数 `--platforms`，执行 `MULTI_DELIVERY_E2E_URL=http://127.0.0.1:18788 npx playwright test e2e/multi-delivery.spec.ts`。覆盖 Owner 取消/确认撤销、QA 上传 log 并按 x86/ARM 保存两条 digest、发布冻结、App 历史及历史查询回读。每日同步仅限「报名截止后、QA 截止前」周期中有效报名的运行中 App；报名未截止、QA 已截止和发布锁定周期不触发自动拉取，同 App 跨周期去重；手动和 RM 全量同步保持原范围。

报名弹窗验证使用同一后端命令加 `--signup`，并执行 `APP_SIGNUP_E2E_URL=http://127.0.0.1:18788 npx playwright test e2e/app-signup.spec.ts`。覆盖批量候选筛选、组件环境、取消、跨周期选择、提交后状态、打开弹窗后新增停止申请的整批回滚，以及 RM 单个代报名和窄屏布局。

QA 导航与权限验证使用不带 `--signup` 的同一隔离后端，执行 `QA_NAV_E2E_URL=http://127.0.0.1:18788 npx playwright test e2e/qa-navigation.spec.ts`。覆盖 Owner/QA 子页签、历史查询、组件依赖导出及深链接，以及 Guest/SPD 的界面和接口拒绝访问。

组合依赖验证使用隔离后端参数 `--dependencies`，执行 `DEPENDENCIES_E2E_URL=http://127.0.0.1:18788 npx playwright test e2e/dependencies.spec.ts`。覆盖 Owner/QA 的组件及 App 默认收起、独立展开/收起和键盘操作、组合配对与去重、HPC release 版本、停止 App 排除、全部平台镜像及固定 digest 的命令、缺失镜像模板、CSV 仅导出已发布 App，以及窄屏展示。

基础镜像验证使用同一隔离后端，执行 `BASE_IMAGE_E2E_URL=http://127.0.0.1:18788 npx playwright test e2e/base-images.spec.ts`。覆盖完整引用新增及解析回读、旧记录编辑、格式错误保留、digest 查询、跨周期显示和发布后只读。

总览组件信息验证使用加 `--signup` 的隔离后端，执行 `DASHBOARD_COMPONENTS_E2E_URL=http://127.0.0.1:18788 npx playwright test e2e/dashboard-components.spec.ts`。覆盖跨周期展示、RM 修改环境后显式刷新、保留顶部日期，以及 Owner/QA/Guest 的只读展示。

镜像历史查询验证使用同一隔离后端加 `--lookup`，执行 `IMAGE_LOOKUP_E2E_URL=http://127.0.0.1:18788 npx playwright test e2e/image-lookup.spec.ts`。上游使用本地 Harbor/Gerrit 响应，验证完整引用提交、历史版本命令、固定 digest、数据挂载、错误后清除旧结果及角色权限。

跨平台范围验证使用隔离后端参数 `--lookup --platforms`，执行 `TEST_PLATFORMS_E2E_URL=http://127.0.0.1:18788 npx playwright test e2e/test-platforms.spec.ts`。同一份包含不同 OS/x86/ARM 芯片组合的 app_info 验证 App 详情、QA 范围和命令以及 x86/ARM 镜像查询。

发布列表验证使用隔离后端参数 `--releases`，执行 `PUBLISHED_APPS_E2E_URL=http://127.0.0.1:18788 npx playwright test e2e/published-apps.spec.ts`。验证 RM/Owner/QA 默认入口、已冻结版本筛选、跨版本的完整镜像和 QA 结果、空版本、关键词筛选及窄屏；所有发布操作只用于临时库造数。
