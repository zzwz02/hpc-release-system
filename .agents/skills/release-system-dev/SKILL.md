---
name: release-system-dev
description: Develop, debug, or review this HPC release collaboration repository using its current FastAPI/React architecture, release and CICD rules, and isolated verification workflow.
---

# 发布协作系统开发

适用于本仓库的功能开发、修复和代码审查。先读根目录 [README](../../../README.md) 了解产品与入口，再沿当前调用链确认行为；历史文档、注释与旧实现只提供线索。用户明确要求变更的规则可以调整，不能用旧规范否决已授权的工作。

## 按任务读取

- 发布决策、App、QA、审批/交付、最终产物：读 [workflows.md](references/workflows.md)，再看对应 domain、service 和测试。
- 前端：读 [web/README-web.md](../../../web/README-web.md)。交互修改要验证实际页面和真实请求形状。
- 测试、迁移、运行环境：读 [verification.md](references/verification.md)。文档修改只做相称的链接、语法和事实检查。
- C500/X201 RST 合并使用相邻的 `c500-manual-integrator` 技能，本技能不代替手册分类与版本规则。

## 修改落点与事实源

当前运行层为 `app/`、`web/`、`shared/`；离线工具在 `tools/`。日常变更保留 `server.py`、根 `index.html` 和 `release_system/` 为旧实现参考，不顺手重写它们。针对这些文件的明确用户要求另按任务处理。

- Router 负责 HTTP 与依赖鉴权；service 编排业务和事务；repository 负责数据访问；纯判断放 domain。现有 service 中仍有 SQL，不把“已完成全部分层”当成事实。
- 静态权限唯一来源为 `shared/access_control.json`；词表与字段描述为 `shared/domain_metadata.json`；集成默认配置为 `shared/integrations.json` 和 `app/config.py`。
- 修改常量、默认值、状态、身份或权限前，先全仓搜索同义定义。让消费者使用现有权威实现，不复制角色数组、状态谓词或仓库解析。
- 所有权、阶段、锁定与申请状态由后端组合静态权限。读取模型已有 `allowed_actions` 时前端消费它；后端仍需重新鉴权，缓存中的动作列表不是写入凭证。
- `app/identity.py` 处理仓库短路径、manifest 存储身份与 Git 解析。使用 App ID 做当前身份；涉及历史匹配同时检查仓库与分支，不能仅按 URL、名称或模型名合并。

## 数据与外部调用

仓库根 `release_system.db` 可能是真实业务库。分析用标准 `sqlite3` 的 URI `mode=ro`，加 `PRAGMA query_only=ON`；避免读取认证密钥、口令哈希、会话 token。不要用应用 `connect()` 或 `/api/state` 做严格只读检查，它们可能写入数据。

测试、迁移演练和故障复现使用临时库或一致性备份。自动化浏览器测试会写数据，不能连接真实业务实例。备份使用 SQLite backup API；助手库单独备份。只有任务范围包含真实数据变更时才执行对应迁移、清理或恢复。

新增写操作把读取最新状态、检查权限/锁定、条件更新及审计放在同一事务边界。考虑 SQLite 写锁和冲突处理；整份 JSON 写回应有版本校验，不能只因套了 `transaction()` 就认定不会丢失更新。保存冲突必须可见，禁止静默覆盖。

同步 Git、LDAP、HTTP 和重计算不要直接堵在 `async def` 的事件循环里；使用同步路由、线程池或异步客户端。网络请求不占用长时间数据库写事务。Jira 等不可回滚的外部副作用需考虑重试幂等、失败状态和补偿，不能仅凭本地事务宣称原子性。

## 前端与时间

- 保持按需刷新，除 QA AI 任务进度外不自行新增周期轮询。注意 `staleTime: Infinity` 下 `refetchOnMount: true` 不保证重新请求。
- 设计缓存时同时考虑用户身份、release、查询参数与返回形状；账号切换、401、写操作需要合适的取消/清理/失效。共用查询键不能对应不同权限或字段集合。
- 共享周期选择使用 `uiStore`；编辑表单保留未保存变更保护。密集列表采用可检索表格或主从布局，选中项目后详情应直接可见。
- Markdown 使用统一 `Markdown.tsx` + DOMPurify，不增加任意 HTML 注入出口。
- 新业务事件使用 `beijing_timestamp()`；deadline、日期分别使用对应归一化函数。日期输入使用 `DateInput`，提交 `YYYY-MM-DD`。
- 旧库可同时含带时区 ISO 与北京时间字符串。先统计列与嵌套字段的格式，再在副本制定迁移；禁止整库无差别加 8 小时或去掉 offset 后声称时区已转换。

## 完成标准

按 [verification.md](references/verification.md) 验证受影响行为，报告实际通过、失败和未执行项。前后端变更核对实际 payload / response，不能只通过双方各自的 mock。行为变更需要更新对应测试和文档，不删用例或放宽 golden 来掩盖错误。

保持工作区清晰，不提交数据库、配置密钥、备份、`node_modules/`、`web_dist/` 或临时报告。不要硬编码分支、测试数量、协作者模型或提交署名。用户未要求团队协作时单代理执行；未要求提交或部署时不把它们当成必需步骤。
