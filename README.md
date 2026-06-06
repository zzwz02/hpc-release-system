# HPC App 发布信息协作系统

面向 HPC App 发布周期的内部协作工具，用来把发布范围、Owner 文档、`app_info.json`、QA 结果、Manager Review、最终文档产物和 CICD 交付申请集中到同一个 Web 界面中管理。

当前代码是一个轻量级 Python Web 服务：后端使用 `http.server.ThreadingHTTPServer` + SQLite，前端是单文件 `index.html`，并带有 LDAP、Jira、OpenAI-compatible LLM、Excel 解析等集成功能。

## 系统状态机

![HPC App 发布信息协作系统状态机](./release_system_state_machine.svg)

## 主要能力

- **发布周期管理**：首次 CSV 初始化、从上一版克隆新 release、维护 app freeze / doc deadline、最终 lock / unlock。
- **App 工作台**：Owner/RM 维护 release 决策、基本信息、文档字段、测试说明、`app_info.json` 上传或 Gerrit 拉取。
- **发布闸门**：按 release snapshot 计算待补项；最终产物只纳入满足条件的 release app。
- **QA 协作**：导出测试范围 CSV、上传 QA log、批量标注 QA 状态、查看 QA 变更记录。
- **AI 辅助 QA**：可调用 OpenAI-compatible 本地/内网 LLM 对已上传 QA log 给出状态建议，结果需人工确认后才写入。
- **发布产物**：生成 Release Note、HPC Manual App 章节、AI4Sci User Guide App 章节、`release_data.json`、Manager Review CSV。
- **CICD 工作台**：Owner 提交 CICD 新建/修改申请；RM/Admin 审批；可立即生效或下发 SPD 交付；支持 Jira issue 自动创建。
- **开发 WIKI**：RM/Admin 维护 Markdown 文章和图片，Owner/RM 可阅读。
- **成员与认证**：本地账号、Admin 角色管理、可选 LDAP 登录和自动角色映射。

## 技术栈

| 层 | 技术 |
| --- | --- |
| 后端 | Python 3.10+，`ThreadingHTTPServer` |
| 数据库 | SQLite，WAL 模式 |
| 前端 | 原生 HTML/CSS/JavaScript，单页应用，轮询 `/api/state` |
| 认证 | PBKDF2-HMAC-SHA256 本地口令、HTTP-only session cookie；可选 LDAP |
| 集成 | Gerrit `git ls-remote` / `git archive`、Jira REST、OpenAI-compatible Chat API |
| 测试 | `unittest`、PowerShell 静态检查 |

Python 依赖见 [requirements.txt](./requirements.txt)：`ldap3`、`openpyxl`、`openai`。前端 Markdown 预览使用本地 vendor 文件：

- `assets/vendor/marked-18.0.5.umd.js`
- `assets/vendor/dompurify-3.4.8.min.js`

## 快速开始

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements.txt
python3 server.py
```

默认监听 `http://127.0.0.1:8765`。也可以指定地址和端口：

```bash
python3 server.py --host 0.0.0.0 --port 9000
```

首次启动会确保默认本地账号存在，并创建 `admin` 账号。Admin 初始口令来源优先级：

1. 环境变量 `HPC_ADMIN_PASSWORD`
2. `admin_password.local`
3. 自动生成并写入 `admin_password.local`

默认开发账号：

| 用户名 | 口令 | 角色 |
| --- | --- | --- |
| `rm` | `rm` | RM |
| `owner_test` | `owner_test` | Owner |
| `qa` | `qa` | QA |
| `spd_test` | `spd_test` | SPD |
| `guest` | `guest` | Guest |
| `admin` | 见 `admin_password.local` | Admin |

生产或共享环境请立即替换默认口令，并通过 Admin 的「系统管理 -> 成员管理」调整角色。

## 可选配置

### LDAP 登录

复制 [ldap.conf_demo](./ldap.conf_demo) 为 `ldap.conf` 并按环境修改。服务启动时读取一次配置，修改后需要重启。

首次 LDAP 登录会自动创建本地用户，角色根据 `memberOf` 映射：

- `dl.pde_sc*` / `dl.pde_sa*` -> Owner
- `dl.sw_qa*` -> QA
- `dl.sw_spd*` -> SPD
- 其他 -> Guest

后续可由 Admin 手动调整角色。

### QA AI 分析

复制 [qa_llm.env.example](./qa_llm.env.example) 为 `qa_llm.env`，或直接设置环境变量：

```text
QA_LLM_BASE_URL=http://10.x.x.x:8000/v1
QA_LLM_MODEL=your-model-name
QA_LLM_API_KEY=
```

系统会把已上传 QA log 与当前 release 的测试清单一起发送给 OpenAI-compatible Chat API。AI 输出只作为 QA 页面上的建议，保存前需要人工核对。

### Jira 自动建单

复制 [jira.conf.example](./jira.conf.example) 为 `jira.conf`。当 RM/Admin 审批 CICD 申请并选择「下发 SPD 执行交付」且启用自动创建 Jira 时，系统会尝试创建 Jira issue；失败不会阻塞审批。

### Gerrit

`/api/app-info/fetch` 和批量拉取依赖本机 `git` 命令以及对应 Gerrit SSH/HTTP 凭据。`/api/gerrit/plan` 只生成推送计划，不执行真实 push；需要以下环境变量才会返回 ready：

```text
HPC_DOCS_GERRIT_REMOTE=...
HPC_RELEASE_DATA_GERRIT_REMOTE=...
```

## 角色

- **RM**：管理 release、deadline、Gerrit 信息、测试范围导出、发布文档和 Manager Review CSV、最终锁定/解锁、CICD 审批。
- **Owner**：维护自己负责的 app，提交 release 决策、文档、测试说明、`app_info.json` 和 CICD 申请。
- **QA**：上传 QA log、标注 QA 状态、使用 AI 分析建议。
- **SPD**：处理被下发的 CICD 交付申请，可标记交付或退回。
- **Admin**：管理用户角色、清空业务数据、删除 app、维护系统级配置入口。
- **Guest**：只读查看发布状态和 QA 信息。

## 核心模型

### Release Snapshot

`apps` 表只保存全局身份：`id`、`git_url`、`git_branch`、别名和创建信息。官方名称、类型、官方 URL、描述、文档目标、Owner、release 决策、文档字段、测试说明、`app_info`、QA 状态等都保存在每个 release 的 snapshot 中。

因此同一个 app 在不同 release 中可以有不同版本、Owner、文档和 QA 状态。新建 release 会从上一版克隆 snapshot，并重置 QA 状态。

### Release 决策

- `release`：进入正式发布、文档生成和 QA。
- `cicd_only`：仅纳入 CICD/infra 管控，不进入正式文档和 QA。
- `stopped`：本轮停止发布或停止维护。

### 阶段权限

阶段由北京时间 deadline 和 lock 状态实时派生：

1. **before_app_freeze**：可新增 app、调整 release 决策、编辑文档和上传 `app_info`。
2. **after_app_freeze**：不能新增或升回 `release`；仍可下调决策、编辑文档和上传不扩大 QA 范围的 `app_info`。
3. **after_doc_deadline**：文档、表单、`app_info` 冻结；仍可下调 release 决策；QA 可继续操作。
4. **released_locked**：全部冻结，仅 RM 可解锁。

### 可发布条件

最终 Release Note / Manual 中纳入的 app 必须满足：

- release 决策为 `release`
- Owner 已确认
- 文档类待补项为空
- QA 状态为 `qa_passed` 或 `has_issues`

`has_issues` 不阻塞发布，QA 问题说明会合并到已知限制中。QA 未测试或不可发布会作为提示/闸门状态展示。

## 典型流程

1. RM 在「周期管理」导入初始化 CSV，或从上一版克隆新 release。
2. RM 设置 app freeze deadline 和 doc deadline。
3. Owner 在「App 工作台」维护本 release 的 app 信息、文档、`app_info.json` 和测试说明，并提交 Owner 确认。
4. RM 导出测试范围 CSV 给 QA。
5. QA 上传 log，必要时使用 AI 分析建议，核对后保存 QA 状态。
6. RM 刷新发布文档和 Manager Review CSV。
7. Manager review / Gerrit merge 完成后，RM 执行最终 Lock Release。
8. 如需跟踪构建交付，Owner/RM/Admin 在「CICD 工作台」提交或审批 CICD 任务。

## 主要 API

- 认证：`POST /api/login`、`POST /api/login/ldap`、`POST /api/logout`、`GET /api/me`、`GET /api/ldap/status`
- 状态：`GET /api/state`
- Release：`POST /api/import-initial`、`POST /api/releases/create`、`POST /api/releases/deadlines`、`POST /api/releases/final-lock`、`POST /api/releases/final-unlock`
- App：`POST /api/apps/new`、`POST /api/apps/update`、`POST /api/app-info`、`POST /api/app-info/fetch`、`POST /api/app-info/fetch-all`
- QA：`POST /api/qa/status-batch`、`POST /api/qa/upload-log`、`POST /api/qa/analyze-log/start`、`GET /api/qa/analyze-log/status`、`GET /api/qa-log/download`、`GET /api/qa-reports`
- 产物：`POST /api/artifacts/generate`、`POST /api/artifacts/manager-review`、`GET /api/artifacts/<kind>`、`GET /api/test-scope.csv`、`POST /api/gerrit/plan`
- WIKI：`GET /api/wiki/articles`、`GET /api/wiki/articles/<id>`、`POST /api/wiki/articles/save`、`POST /api/wiki/articles/pin`、`POST /api/wiki/articles/delete`、`POST /api/wiki/images/upload`
- CICD：`GET /api/cicd/tasks`、`GET /api/cicd/requests`、`POST /api/cicd/requests/submit`、`POST /api/cicd/requests/approve`、`POST /api/cicd/requests/reject`、`POST /api/cicd/requests/cancel`、`POST /api/cicd/requests/deliver`
- 管理：`GET /api/admin/users`、`POST /api/admin/users/set-role`、`POST /api/admin/clear-db`、`POST /api/admin/apps/delete`
- 审计：`GET /api/app-audit`

常见返回码：`401` 未登录，`403` 无权限，`400/500` 业务错误或服务端错误。

## 目录结构

```text
release-system/
├── server.py                    # HTTP 服务、路由、LDAP/Gerrit/Jira 调用入口
├── index.html                   # 单页前端
├── requirements.txt             # Python 依赖
├── release_system/
│   ├── core.py                  # DB schema、发布流程、QA、CICD、artifact 生成
│   ├── jira_client.py           # Jira REST client
│   ├── llm.py                   # OpenAI-compatible LLM client
│   └── wiki/core.py             # 开发 WIKI 数据逻辑
├── tests/
│   ├── test_core.py
│   ├── ldap_group_test.py
│   ├── jira_test.py
│   └── static_checks.ps1
├── assets/
│   ├── MACA_SDK_release_plan.jpg
│   └── vendor/
├── test_data/
├── ldap.conf_demo
├── jira.conf.example
├── qa_llm.env.example
└── release_system_state_machine.svg
```

运行时可能生成：

- `release_system.db`、`release_system.db-wal`、`release_system.db-shm`
- `qa_logs/`
- `admin_password.local`
- `release_system_admin_backup_*.sqlite`
- `ldap.conf`、`jira.conf`、`qa_llm.env`

这些本地文件已在 [.gitignore](./.gitignore) 中忽略。

## 测试

```bash
python3 -m unittest tests.test_core
```

PowerShell 静态检查：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File tests\static_checks.ps1
```

Linux/macOS 上如果命令名是 `pwsh`，可替换为：

```bash
pwsh -NoProfile -ExecutionPolicy Bypass -File tests/static_checks.ps1
```

LDAP / Jira 连通性脚本需要先准备对应配置和依赖，再按需手动运行：

```bash
python3 tests/ldap_group_test.py <domain_user> --config ldap.conf
python3 tests/jira_test.py --dry-run --config jira.conf
```

## 开发注意

- 写接口和 helper 时优先用 `core.transaction(conn)` 包住完整业务操作，成功时统一提交，异常时统一 rollback。
- 发布相关写入应走 `core.can(...)` / `core.require_can(...)` 的阶段权限模型。
- `app_info.json` 会派生芯片、版本、测试命令和差异记录；过 app freeze 后不能扩大 QA 范围。
- 最终 lock 生成的 final artifacts 视为不可变；预览 artifact 可在未锁定状态下刷新。
- SQLite 适合单实例、小规模内部使用；高并发写入或多实例部署需要重新评估数据库和锁模型。
