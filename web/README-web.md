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

后端必须另行启动，并使用隔离数据库。不要让开发页面误连真实业务实例。npm 脚本为本机请求设置了 `NO_PROXY` / `no_proxy`；自定义命令也需要正确绕过本机代理。

`npm run build` 执行 TypeScript 检查和 Vite 构建，覆盖根目录 `web_dist/`；FastAPI 存在该目录时会提供静态资源与 SPA 深链接回退。构建会改变使用该目录的服务所展示的页面。

## 代码导航

| 目录/文件 | 职责 |
| --- | --- |
| `src/api/` | 同源请求、登录 API 与 AuthContext；401 进入未登录状态 |
| `src/main.tsx` | React、QueryClient 和 AuthProvider 的全局装配 |
| `src/routes/` | 页签、路由与角色访问；Admin 非管理页深链接重定向到 `/admin` |
| `src/features/` | 周期、总览、App、QA、文档、CICD、Jenkins、助手、WIKI、管理页面 |
| `src/lib/` | 权限、时间、阶段、字段词表和格式化 |
| `src/store/uiStore.ts` | 共享周期选择、App 选择和编辑等 UI 状态 |
| `src/components/` | Markdown、日期输入、刷新条、表格与通用反馈 |

CICD 助手及 V2 页面调用 `/api/cicd-agent/*` 同源代理，服务地址由后端配置。V2 依赖 `@assistant-ui/react`，不能因为其他页面的单元测试通过就跳过完整类型检查和构建。

## 缓存与数据契约

全局 QueryClient 默认 `staleTime: Infinity`，关闭焦点、重连、挂载自动重取和轮询；部分页面有局部覆盖。页面进入不保证请求：`refetchOnMount: true` 对永久新鲜缓存通常不重取。显式刷新和写后失效负责让内容更新，`RefreshBar` 展示本段查询的 `dataUpdatedAt`。

QA AI 任务进度按秒查询是现有轮询例外；批量 Gerrit 拉取使用 NDJSON 流，CICD 助手也有流式接口。不要用增加全局定时器来修复局部失效问题。

查询键应区分身份、周期、过滤参数与响应形状。当前 AuthContext 的退出/401 只重置用户，没有清空所有查询及 UI 状态；维护认证或缓存时应处理这个已知缺口，不能认为切换账号天然隔离。

App 工作台请求 `include_allowed_actions=1`，其他 `/api/state` 调用不一定带此参数；现有页面存在共享查询键，修改时核对缓存实际来源。后端返回的 `allowed_actions` 用于展示，写接口仍应依据当前用户与当前资源重新鉴权。

HTTP 类型声明不会验证真实 JSON。改接口时对照 router/service、前端 API 包装和调用处，验证提交后回读；mock 的字段名与后端保持一致。`/api/me` 返回 `{user: ...}`，无会话时也是 HTTP 200、`user: null`。

## 交互与显示

- 周期选择使用共享 `selectedReleaseId`，跳转 App 时保留正确周期；不把不同 release 的编辑草稿或 QA 建议混用。
- 保留未保存编辑保护，选中 App 后详情应直接可见，长列表与详情按需独立滚动。
- Markdown 的 `dangerouslySetInnerHTML` 出口集中在 `Markdown.tsx` 的 DOMPurify 流程；业务组件使用该组件。
- 日期输入使用 `DateInput` 和统一归一化，显示/提交 `YYYY-MM-DD`。
- 新服务端时间是北京时间无 offset 字符串；旧数据可能仍含 UTC ISO。当前 formatter 不做完整的旧数据时区转换，禁止给所有值统一加 8 小时。

## 验证

```bash
npm run build
npm run lint
npm test
```

纯类型检查可用 `npx tsc --noEmit`，避免改动正在服务的构建产物。

Playwright 配置固定访问 5176，**不会自动启动前后端**。用隔离库启动后端，再运行 `npm run dev -- --host 127.0.0.1 --port 5176 --strictPort`，最后 `npm run test:e2e`。如果隔离后端不是 8000，同步设置 `API_TARGET`。

E2E 会实际写数据，先确认 5176 对应隔离实例；Admin 测试口令配置、后端 fixture 路径和网络限制见 [验证说明](../.agents/skills/release-system-dev/references/verification.md)。测试 helper 可能滞后于 API，出现失败应定位并报告，不能通过测试真实业务数据来绕过。
