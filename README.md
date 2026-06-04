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

开发 WIKI 的 Markdown 阅读/预览使用本地前端依赖，内网部署无需访问外部 CDN：

- `assets/vendor/marked-18.0.5.umd.js`：Markdown → HTML
- `assets/vendor/dompurify-3.4.8.min.js`：清理 Markdown 渲染出的 HTML

写接口和写 helper 应使用 `core.transaction(conn)` 包住完整业务操作；成功时只提交一次，失败时统一 rollback，避免 audit 与 snapshot 等相关写入出现半状态。

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
| `guest` | `guest` | Guest |
| `admin` | 见 `admin_password.local` | Admin |

## 角色

- **RM**：建/克隆 release、设置 deadline、改 Gerrit URL/branch、导出测试范围 CSV、生成 RST 与 Manager Review CSV、最终锁定/解锁。
- **Owner**：维护自己名下的 app —— 新增 app、维护本 release 的基本信息与文档、填测试说明、上传/拉取 `app_info.json`、从其他 release 复制信息、提交 Owner 确认。
- **QA**：对 `release` 决策的 app 标注 QA 状态、上传 QA log。
- **Admin**：备份并清空数据库、删除单个 app。

## 核心模型

### 信息粒度（per-release）

`apps` 表只存全局身份：`id`、`git_url`、`git_branch`、别名。其余信息 —— 官方名称、App 类型、官方 URL、描述、文档目标（HPC/AI4Sci）、Owner、release 决策、文档字段、测试说明、`app_info`、QA 状态 —— 都按 release 存在该 release 的 snapshot 里。因此同一个 app 在不同 release 中的这些字段相互独立：改动一个 release 不会影响已冻结的另一个 release。显示名由「官方名称 + 版本号」实时拼出，不再有独立的 `app.name`。

新建 release 从上一版克隆时，会整份继承上一版每个 app 的 snapshot。Owner/RM 也可在「App 工作台」用「从其他版本复制信息」按需把另一个 release 的基本信息 / 文档 / 测试说明 / release 决策填入当前表单；修改 release 决策时还会询问是否同步到后续 release。

### 发布决策（三态）

- `release`：参与 QA 和正式发布，进入 release note / HPC Manual / AI4Sci User Guide。
- `cicd_only`：仅作 CICD/infra 跟踪，不进 QA，不生成文档。
- `stopped`：本轮停止发布或停止维护。

### 时间阶段

每个 release 有两个北京时间 deadline，阶段由 deadline 和锁状态实时派生，不存状态字段：

1. **before_app_freeze**：可任意新增 app、任意调整决策、编辑文档。
2. **after_app_freeze**（过 app 冻结 deadline）：release 决策只能下调，不能再升回 `release`；新增 app 只能是 `cicd_only`/`stopped`；文档、表单仍可编辑；`app_info` 仍可重新上传/拉取，但不能新增芯片或测试 path（不扩大 QA 范围）。
3. **after_doc_deadline**（过 doc deadline）：文档、表单、`app_info` 冻结；但 release 决策仍可下调（`release`→`cicd_only`/`stopped`，不能升回 `release`），也仍可新增 `cicd_only`/`stopped` app；QA 可继续操作。
4. **released_locked**（最终锁定）：全部冻结，仅 RM 可解锁。

### 可发布条件

进入最终 release note / manual 的 app 必须满足：决策为 `release` + Owner 已确认 + 文档类待办为空 + QA 为 `qa_passed` 或 `has_issues`。`has_issues` 仍会发布，QA 写的问题会附加到「已知限制」。「待补/门禁」项仅作提示，不阻塞报告生成或最终锁定。

### 最终锁定

部门 manager review 报告并 merge 进 Gerrit 后，RM 执行 final lock：冻结本 release 全部 snapshot、生成最终 RST 与 release-data JSON。可由 RM 解锁。

## 工作流程概览

1. RM 在「初始化/周期」首次导入一份初始化 CSV（列含 类别 / id / 名称 / Owner / 类型 / 描述 / git_url / git_branch，每个 (git_url, git_branch) 为一个 app）；后续 release 从上一版克隆。
2. RM 设置当前 release 的名称与两个 deadline。
3. Owner 在「App 工作台」确认 release 决策，维护本 release 的基本信息、文档、`app_info.json`、test_cmd 说明（可「从其他版本复制信息」），提交 Owner 确认。
4. RM 导出测试范围 CSV 交给 QA。
5. QA 在「QA」页上传 log，标注 `qa_passed` / `has_issues` / `cannot_release`。
6. RM 刷新文档 RST 和 Manager Review CSV，供部门 manager 审核。
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
- QA：`POST /api/qa/status-batch`、`/api/qa/upload-log`；`GET /api/qa-log/download`
- 产物：`POST /api/artifacts/generate`、`/api/artifacts/manager-review`、`/api/gerrit/plan`；`GET /api/artifacts/<kind>`、`/api/test-scope.csv`
- 变更日志：`GET /api/app-audit`（按 app + release 过滤，含字段级 detail）
- 管理：`POST /api/admin/clear-db`、`/api/admin/apps/delete`

接口返回码：401 = 未登录，403 = 已登录但无权限，400/500 = 其他错误。

## 测试

```
python3.14 -m unittest tests.test_core
powershell -NoProfile -ExecutionPolicy Bypass -File tests\static_checks.ps1
```

当前 94 个单元测试 + 静态检查。

## 已知问题与限制

按影响排序，分组列出。

### 🚨 性能与并发（生产化前最需要解决）

1. **`refresh_missing_items` 在每次 `GET /api/state` 都跑** —— `server.py:state_payload` 每次状态请求都遍历当前 release 全部 snapshot 重算 `missing_items`，只要任一项与已存值不一致就 `save_snapshot` + `commit`。前端默认 5s 轮询，N 个登录用户 × 1/5s × M 个 app 会同时打到同一个 SQLite 写锁。  
   - 现象：用户多 / app 多时，UI 响应变慢，`save_snapshot` 与其它写路径排队等锁。  
   - 建议：把 `refresh_missing_items` 改成"只在写路径里更新"，GET 路径只读；或加 debounce / TTL 缓存。
2. **`fetch_all_app_infos_from_gerrit` 串行、无并发上限、无 per-app 超时** —— 100 个 app 全部串行；任何一个卡在 `git ls-remote` 都会阻塞整批。期间整张 release 的 snapshot 表持续写入。  
   - 建议：后台异步任务 + 进度回查接口；或固定并发数 + 单 app 超时。
3. **单 SQLite + WAL** —— 适合单实例小规模。多实例或高并发写需迁 PostgreSQL；目前所有写都走单连接的全局锁排队。

### 数据正确性

4. **多个写入路径"读-改-写"不在同一事务里（TOCTOU 覆盖）** —— `apply_app_info`、`qa_set_status`、`/api/apps/update` 的 mutate 路径都是先 `get_release(...)` 读快照（事务外），在内存里 mutate，最后才 `with transaction: save_snapshot(...)`。两个用户并发上传 / 改状态时，后写的会基于过期快照覆盖先写的，并发越多丢得越多。`refresh_missing_items` 在 GET 路径上跑（见上面第 1 条）会进一步放大这个窗口 —— 任何 5s 轮询的并发 GET 都可能把 Owner 正在写的字段刷回旧值。  
   - 建议：把读 + 校验 + 写包进同一个 `transaction(conn)`，必要时用 SELECT 加版本号字段实现乐观锁。
5. **`app_info` 重传不主动通知 QA** —— Owner 重传 / Gerrit 拉取后会自动失效 `owner_confirmed`（见 `apply_app_info`），但 `qa_status` 不会变；QA 不会被告知"app 代码已变动，需复测"。  
   - 建议：用 commit id 判定。Gerrit 拉取自动记录 commit_id；QA 标注状态时记录所测构建的 commit_id。两者不一致即给 snapshot 打 `qa_stale` 标记，QA 页 + 最终闸门按"需复测"处理。owner 上传缺 commit_id 时应保守提示。
6. **final lock 无阶段保护** —— RM 可对仍处于 `before_app_freeze` 的 release 直接最终锁定，把没冻结范围、Owner 没确认、QA 没跑的 snapshot 当成正式产物写出。  
   - 建议：要求至少 `after_doc_deadline`，或所有 `release` 决策的 app 都已 `owner_confirmed` + `qa_status≠not_checked`。
7. **`update_release_deadlines` 允许把 deadline 改成过去** —— RM 把 doc deadline 改成昨天，整 release 立即进入 `after_doc_deadline`，所有 Owner / RM 的写权限突然冻结，没有任何前置警告。  
   - 建议：UI 保存 deadline 前对比当前 phase 给出弹窗；或后端在 audit 写明 phase 变化。
8. **新增 app 时只按精确 `(git_url, git_branch)` 查重；RM 改 git 信息时完全不查重** —— `ssh://gerrit/foo` 与 `ssh://gerrit/foo/`、大小写、是否带 `.git` 后缀都会被当成不同的 app；`/api/apps/update` 的 `update_repo_if_needed` 没有任何 collision check（已修：现在会拒绝改成已存在的 `(git_url, git_branch)`）。  
   - 建议：SELECT 前归一化（小写、去尾斜杠、去 `.git`）。
9. **`delete_app` 只阻拦"已锁 release 中的 app"** —— 在 `after_doc_deadline`、QA 正在跑的 release 中删 app，snapshot 直接消失，预览 artifact 还在。建议在非空非 stopped 状态下拒绝删除或要求 RM 二次确认。

### 安全

10. **`/api/state` 把全部 app + 全部 snapshot 返给所有登录角色** —— QA 也能看到全部 Owner 草稿、Gerrit URL、QA issue note。对多团队场景是数据泄露面。  
    - 建议：按角色裁剪 payload（QA 只看 release-decision=release + 必要字段）。
11. **会话不过期** —— session token 创建后无 TTL，只有登出才失效；cookie 没设 `Secure`；登录无频率限制；默认账号明文口令。生产部署前必须修。
12. **未设置 CSP / X-Frame-Options / X-Content-Type-Options** —— 静态资源 + JSON 响应都没加。

### 集成 / 运维

13. **Gerrit 拉取依赖本地 `git archive --remote` + SSH 凭据**；失败时 `subprocess` stderr 摘要未带出到前端，只能看到笼统的 `CalledProcessError`。
14. **Gerrit 推送只输出推送计划骨架**，未执行真实 `git push`。
15. **审计 log 与 backup 文件不清理** —— `audit` 表无 retention；`release_system_admin_backup_*.sqlite` 每次 admin 操作都写一份，永不清理。

### 体验 / 维护

16. **`order_chips`（x201 排最后）逻辑在前后端各写一份** —— `core.py:order_chips` 与 `index.html:orderChips`，任何一边改了另一边没改就会前后端不一致。
17. **新增 app 时只能指定一个 owner** —— 多人维护的 app 要后续在 Owner 列里手动加。
18. **HTTP 层无集成测试** —— 当前测试都直接调 `core.*`，权限分支、过 deadline 阻断、事务回滚都没在 HTTP 路径下覆盖。

## 从 MVP 到完整产品的待完善项

### 认证与安全
- 用户管理界面（增删用户、改口令、改角色）；目前只有默认账号 + admin 引导。
- 会话过期与续期；cookie `Secure`；登录频率限制；移除明文默认口令。
- 引入更细的权限模型与 team 概念；接入 LDAP/SSO。

### 部署与运维
- 生产化部署：反向代理 + TLS、进程托管（systemd 等），替代 stdlib `http.server` 直接对外。
- 结构化日志、健康检查端点、基础 metrics。
- 备份恢复入口、定时备份、旧备份自动清理。

### 通知（系统核心目标）
- deadline 临近、release 决策变更、QA 标注「存在问题」/「不可发布」、Owner 确认提交等，主动推送邮件 / IM，而不是靠用户轮询页面。
- 给 Owner 一个"待我处理"的全局收件箱（按 app × deadline 倒序）。

### 集成
- Gerrit 推送实现真正的自动 push docs / release-data。
- `app_info.json` 拉取的凭据管理 + 失败 stderr 上送。

### 数据与并发
- SQLite → PostgreSQL（高并发场景）；附 schema migration 机制。
- CSV 导入容错；`app_info.json` 严格 schema 校验。
- 支持归档 release（目前只能创建和克隆）。

### 功能与体验
- 全局审计 / 变更视图（目前只有按 app + release 的变更日志）。
- 跨 release 同步目前是 Owner/RM 手动触发；尚无自动的"上游改动提醒下游"。
- 前端无构建，单文件 + 5 秒轮询；产品级应考虑 SSE/WebSocket 推送和更细的局部更新。
- 中英文案混杂，需统一。

### 测试与质量
- HTTP 层与前端集成测试；并发 / 锁竞争测试。

## 体验改善建议（待评估）

以下为已讨论、尚未实现的体验增强方向，记录备查。

### Owner 用 Markdown 编写文档

- Owner 填写 doc 字段时用 Markdown（多数人不熟 RST），界面提供实时预览。
- snapshot 仍只存 Markdown 原文；仅在 RM 生成 artifacts 时把 Markdown 转成 RST（官方 HPC Manual / AI4Sci 文档工程必须用 RST，MyST 不可选）。预览直接渲染所存的 Markdown。
- MD→RST 转换器有两条路，**转换必须是确定性的，不要用 AI**：
  - A：调用 `pandoc`（标准、健壮，几乎不用写转换代码）；代价是每个部署环境都要安装 pandoc，且文档生成会硬依赖它。
  - B：自写一个受限 Markdown 子集转换器（纯 stdlib、完全自包含）；代价是要维护、有边界情况。可行性不低 —— owner 字段都很短，且 MD 与 RST 在段落、加粗、列表上基本重合，真正要转的只有围栏代码块、标题、链接、行内代码。
  - 决策点：部署环境能否保证装有 pandoc。

### AI 辅助（需先决定是否引入外部 LLM API）

引入 AI 等于接受一个外部 API 依赖 + 调用成本 + 输出不确定性。总原则：**AI 只产出「建议」，由人复核；不做最终判定，也不做硬性闸门。**

QA 的 AI 分析默认从项目根目录的 `qa_llm.env` 读取配置，也支持直接设置环境变量；环境变量优先级高于文件。文件格式不依赖 shell，Windows 和 Linux 可共用：

```text
QA_LLM_BASE_URL=http://10.x.x.x:8000/v1
QA_LLM_MODEL=your-model-name
QA_LLM_API_KEY=
```

AI 分析调用使用 OpenAI Python SDK，生产环境需安装依赖：`python -m pip install openai`。LLM 调用使用流式返回，网页进度中会显示已接收的 token 数。部署时复制 `qa_llm.env.example` 为 `qa_llm.env` 并填写实际值；`qa_llm.env` 已被 `.gitignore` 忽略，不会提交密钥。若配置文件不放在项目根目录，可设置 `QA_LLM_ENV_FILE` 指向自定义路径，支持 `~`、`$HOME/...`、`%USERPROFILE%\...` 这类跨平台路径写法。服务会在每次 AI 分析调用时读取配置；如果运行环境里已设置同名环境变量，会覆盖文件中的值。

- **QA log 拟稿**：QA 上传 log 后，AI 起草 `qa_issue_note`、建议 `qa_status`，QA 复核后确认。不让 AI 直接定 pass/fail —— 那是 QA 的问责性判断；log 中细节要命（「0 failed」可能是没跑、「5 failed」可能全是已知 flaky）。约束：log 需能按 app 切分；大 log 有 token 成本与上下文上限。
- **doc 合理性提示**：在 RM 检查环节由 AI 提示 owner 所填文档是否像凑数，非阻塞、仅提示。注意 AI 只能判断「用没用心」，判断不了「对不对」—— 它是质量下限，不是正确性保证；正确性仍靠 RM review 和 QA 实测。
- 补充：挡明显的垃圾输入（如把基本介绍填成「a」）**不需要 AI** —— 几条确定性规则即可（最小长度、不能全空白、不能单字符重复、要求若干个词），零依赖零成本，可独立先做。
