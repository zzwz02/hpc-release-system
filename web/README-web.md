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
| `src/routes/` | 页签、路由与角色访问；`routeConfig.ts` 中同一 `group` 的路由合并为一个页签并显示为子页签（AI助手）；Admin 非管理页深链接重定向到 `/admin` |
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

- 周期选择使用共享 `selectedReleaseId`，跳转 App 时保留正确周期；不把不同 release 的编辑草稿或 QA 建议混用。
- 保留未保存编辑保护，选中 App 后详情应直接可见，长列表与详情按需独立滚动。
- Markdown 的 `dangerouslySetInnerHTML` 出口集中在 `Markdown.tsx` 的 DOMPurify 流程；业务组件使用该组件。
- 日期输入使用 `DateInput` 和统一归一化，显示/提交 `YYYY-MM-DD`。
- 新服务端时间是北京时间无 offset 字符串；旧数据可能仍含 UTC ISO。当前 formatter 不做完整的旧数据时区转换，禁止给所有值统一加 8 小时。

## 验证

`npm run build`、`npm run lint`、`npm test`；只做类型检查用 `npx tsc --noEmit`，不会改动正在服务的 `web_dist/`。E2E（Playwright 固定访问 5176，不自动启动前后端，会写数据）的启动步骤见 [验证说明](../.agents/skills/release-system-dev/references/verification.md)。


### 测试用例页面的隔离验证

后端运行 `.venv/bin/python -m tests.run_jira_testing_site --port 18781`，前端运行 `API_TARGET=http://127.0.0.1:18781 npm run dev -- --host 127.0.0.1 --port 18782 --strictPort`。后端使用临时数据库、临时 skill 仓库和本地 Codex 协议测试端，不执行真实 GPU 作业或访问 Jira。

需要检查默认用例展示时，在上述后端命令加 `--builtin-cases`，会将仓库内置输入加载到临时数据库，不带入部署环境中的测试记录。

从 `web/` 执行 `JIRA_TESTING_E2E_URL=http://127.0.0.1:18782 npx playwright test e2e/jira-testing.spec.ts`。未设置此变量时该测试跳过，不能将它指向实际部署。覆盖 RM 导入、启动配置与 SSH 验证、表格单条展开、评价草稿保留、历史输入绑定、排队与取消、过程折叠后的状态更新、文件回收重试和 Owner 拒绝访问。使用本地协议端验证页面与后端请求，不检验模型分析能力。
