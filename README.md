# HPC App 发布信息协作系统

一个零第三方依赖的 MVP，用一组时间节点协调 HPC App 的发布信息收集。

## 目标

- 让 release note 和各类 manual 始终与实际发布内容一致。
- 让 RM 与 infra 成员及时看到每个 app 的发布决策、文档、QA 状态变化。
- 用 deadline 驱动流程，减少口头通知和人工催办。

## 技术栈

| 层 | 技术 |
| --- | --- |
| 后端 | Python 3.14 标准库（`http.server.ThreadingHTTPServer`），无第三方依赖 |
| 数据库 | SQLite（WAL 模式） |
| 前端 | 单文件原生 HTML/CSS/JS（`index.html`），5 秒轮询刷新 |
| 认证 | PBKDF2-HMAC-SHA256 口令，会话 token 存 cookie |
| 测试 | `unittest` + PowerShell 静态检查 |

## 快速开始

需要 Python 3.14+。

```
python3.14 server.py                          # 默认 127.0.0.1:8765
python3.14 server.py --host 0.0.0.0 --port 9000
```

打开 `http://127.0.0.1:8765`。

首次启动会创建 `admin` 账号，口令来源优先级：环境变量 `HPC_ADMIN_PASSWORD` > `admin_password.local` 文件 > 自动生成并写入 `admin_password.local`。

默认账号（仅供开发，生产环境务必修改）：

| 用户名 | 口令 | 角色 |
| --- | --- | --- |
| `rm` | `rm` | RM |
| `owner_test` | `owner_test` | Owner |
| `qa` | `qa` | QA |
| `admin` | 见 `admin_password.local` | Admin |

## 角色

- **RM**：建/克隆 release、设置 deadline、导出测试范围 CSV、生成 RST 与 Manager Review CSV、最终锁定/解锁。
- **Owner**：维护自己名下的 app —— 新增 app、填文档与测试说明、上传/拉取 `app_info.json`、提交 Owner 确认。
- **QA**：对 `release` 决策的 app 标注 QA 状态、上传 QA log。
- **Admin**：备份并清空数据库、删除单个 app。

## 核心模型

### 发布决策（三态）

- `release`：参与 QA 和正式发布，进入 release note / HPC Manual / AI4Sci User Guide。
- `cicd_only`：仅作 CICD/infra 跟踪，不进 QA，不生成文档。
- `stopped`：本轮停止发布或停止维护。

### 时间阶段

每个 release 有两个北京时间 deadline，阶段由 deadline 和锁状态实时派生，不存状态字段：

1. **before_app_freeze**：可任意新增 app、任意调整决策、编辑文档。
2. **after_app_freeze**（过 app 冻结 deadline）：release 决策只能下调，不能再升回 `release`；新增 app 只能是 `cicd_only`/`stopped`；文档、表单、`app_info` 仍可编辑。
3. **after_doc_deadline**（过 doc deadline）：文档、表单、`app_info` 冻结；但 release 决策仍可下调（`release`→`cicd_only`/`stopped`，不能升回 `release`），也仍可新增 `cicd_only`/`stopped` app；QA 可继续操作。
4. **released_locked**（最终锁定）：全部冻结，仅 RM 可解锁。

### 可发布条件

进入最终 release note / manual 的 app 必须满足：决策为 `release` + Owner 已确认 + 文档类待办为空 + QA 为 `qa_passed` 或 `has_issues`。`has_issues` 仍会发布，QA 写的问题会附加到「已知限制」。「待补/门禁」项仅作提示，不阻塞报告生成或最终锁定。

### 最终锁定

部门 manager review 报告并 merge 进 Gerrit 后，RM 执行 final lock：冻结 snapshot、写入 app 元数据快照、生成最终 RST 与 release-data JSON。可由 RM 解锁。

## 工作流程概览

1. RM 在「初始化/周期」首次导入 release CSV、owner CSV、alias mapping；后续 release 从上一版克隆。
2. RM 设置当前 release 的名称与两个 deadline。
3. Owner 在「App 工作台」确认 release 决策，补齐文档、`app_info.json`、test_cmd 说明，提交 Owner 确认。
4. RM 导出测试范围 CSV 交给 QA。
5. QA 在「QA」页上传 log，标注 `qa_passed` / `has_issues` / `cannot_release`。
6. RM 生成预览 RST 和 Manager Review CSV，供部门 manager 审核。
7. manager review/merge 后，RM 执行最终 Lock Release。

面向 owner 和 manager 的简明流程见 [release_process_owner_manager.md](./release_process_owner_manager.md)，设计文档见 [release_system_plan.md](./release_system_plan.md)。

## 目录结构

```
release-system/
├── server.py                       # HTTP 服务与路由
├── release_system/
│   ├── __init__.py
│   └── core.py                     # 业务逻辑、DB schema、工作流
├── index.html                      # 单页前端
├── tests/
│   ├── test_core.py                # 单元测试
│   └── static_checks.ps1           # 静态检查
├── release_process_owner_manager.md
├── release_system_plan.md          # 设计文档
└── README.md
```

运行时还会生成 `release_system.db`（及 WAL 文件）、`qa_logs/`、`release_system_admin_backup_*.sqlite`、`admin_password.local`。

## 主要 API

- 认证：`POST /api/login`、`/api/logout`；`GET /api/me`
- 状态：`GET /api/state`
- Release：`POST /api/import-initial`、`/api/releases/create`、`/api/releases/deadlines`、`/api/releases/final-lock`、`/api/releases/final-unlock`
- App：`POST /api/apps/new`、`/api/apps/update`、`/api/app-info`、`/api/app-info/fetch`
- QA：`POST /api/qa/status`、`/api/qa/upload-log`；`GET /api/qa-log/download`
- 产物：`POST /api/artifacts/generate`、`/api/artifacts/manager-review`、`/api/gerrit/plan`；`GET /api/artifacts/<kind>`、`/api/test-scope.csv`
- 变更日志：`GET /api/app-audit`
- 管理：`POST /api/admin/clear-db`、`/api/admin/apps/delete`

接口返回码：401 = 未登录，403 = 已登录但无权限，400/500 = 其他错误。

## 测试

```
python3.14 -m unittest tests.test_core
powershell -NoProfile -ExecutionPolicy Bypass -File tests\static_checks.ps1
```

当前 56 个单元测试 + 静态检查。

## 已知问题与限制

以下为已识别、尚未修复的问题，按影响排序：

1. **`/api/apps/update` 出错时可能半写入** —— app 元数据先于 snapshot 落库并提交，若随后的 snapshot 校验失败，会留下「元数据已改、snapshot 未改」的不一致。当前前端不会触发该路径。
2. **文档类闸门用字符串前缀 `"QA "` 区分 QA 项** —— 若某个 `app_info` 的测试名恰好以 `QA ` 开头，其缺失项会被误排除出发布闸门。应改为结构化标记。
3. **`app_info` 重传不会作废已有的 QA 结论** —— Owner 在 QA 通过后重传 `app_info`，`qa_status` 不重置；最终闸门仍能拦截，但 QA 本人收不到提醒。
   - *建议方案*：用 commit id 判定。`app_info` 同步时记录来源 commit id（Gerrit 拉取自动记录，owner 上传时由 owner 手填）；QA 标注状态时记录所测构建的 commit id。两者不一致即说明 app 代码已变动，给 snapshot 打 `qa_stale` 标记，QA 页和最终闸门按「需复测」处理。commit id 反映 app 仓库的实际代码状态，比对 `app_info.json` 内容更可靠 —— 代码改了但 `app_info.json` 没改时，内容 diff 会漏掉。owner 上传（Gerrit 拉取失败时的兜底路径）缺 commit id 时应保守提示，不默认 QA 仍有效。
4. **`/api/app-audit` 无归属校验** —— 任何已登录用户可读取任意 app 的变更日志。
5. **final lock 无阶段保护** —— RM 可对仍处于 before_app_freeze 的 release 直接最终锁定。
6. **QA 批量保存非事务** —— 「保存 QA 状态」逐个 app 提交，中途失败会留下部分保存。
7. **会话不过期** —— session token 创建后无 TTL，只有登出才失效。
8. Gerrit 拉取依赖本地 `git archive --remote`；失败时页面提示，Owner 可上传 JSON 兜底。
9. Gerrit 推送只输出推送计划骨架，未执行真实 git push。

## 从 MVP 到完整产品的待完善项

### 认证与安全
- 用户管理界面（增删用户、改口令、改角色）；目前只有默认账号 + admin 引导，Admin 页只能清库/删 app。
- 会话过期与续期；cookie 增加 `Secure` 标志；移除明文默认口令。
- 修复 `/api/app-audit` 越权读取；引入更细的权限模型与 team 概念；接入 LDAP/SSO。

### 部署与运维
- 生产化部署：反向代理 + TLS、进程托管（systemd 等），替代 stdlib `http.server` 直接对外。
- 结构化日志、健康检查端点、基础 metrics。
- 备份恢复入口、定时备份、旧备份自动清理。

### 通知（系统核心目标）
- deadline 临近、release 决策变更、QA 标注「存在问题」/「不可发布」等，主动推送邮件 / IM，而不是靠用户轮询页面。

### 集成
- Gerrit 推送目前只输出命令计划（`gerrit_push_plan`），需实现真正自动推送 docs / release-data。
- `app_info.json` 拉取依赖本地 `git` 命令与网络，需要凭据管理。

### 数据与并发
- SQLite 适合单实例小规模；多实例或高并发写需迁移到 PostgreSQL 等。
- CSV 导入容错弱；`app_info.json` 缺乏严格 schema 校验。
- 支持删除 / 归档 release（目前只能创建和克隆）。

### 功能与体验
- 全局审计 / 变更视图（目前只有按 app 的变更日志）。
- doc 跨并行 release 的显式同步（目前仅建周期时一次性克隆）。
- 前端无构建，单文件 + 5 秒轮询；产品级应考虑 SSE/WebSocket 推送和更细的局部更新。
- 中英文案混杂，需统一。

### 测试与质量
- 增加 HTTP 层与前端集成测试、并发/锁竞争测试（目前只有 core 单元测试）。
