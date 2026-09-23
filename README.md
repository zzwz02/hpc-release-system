# HPC App 发布信息协作系统

用于管理 HPC / AI4Sci App 的发布范围、Owner 文档、Gerrit app_info、QA 结果、CICD 申请与交付，以及发布文档产物。当前运行入口是 `app/main.py`（FastAPI）和 `web/src/main.tsx`（React）。

本文说明当前实现，规则以代码为准；拟建功能见 [发布流程改进建议](docs/release-process-roadmap.md)。仓库根的数据库可能是真实业务库，开发、测试和导出按下文隔离。

## 从哪里开始

| 使用者 | 日常入口与职责 |
| --- | --- |
| RM | 周期管理：初始化或克隆周期、设置 deadline、最终锁定；App 工作台：维护范围与信息；CICD：审批、交付处理；发布文档：导出和检查 |
| Owner | App 工作台：维护本人负责的 App、文档与测试说明，确认信息，提交新增或 CICD 配置申请 |
| QA | QA：查看测试范围与命令、批量标注结果、上传日志、使用 AI 分析建议 |
| SPD | CICD：查看交付任务、确认交付或退回；Jenkins 失败查询和 CICD 助手辅助排查 |
| Guest | 查看总览、App 和 QA 等矩阵允许的页面；不编辑业务数据 |
| Admin | 系统管理：角色维护、数据库清理、全局 App 删除；前端只进入 `/admin`，不承担 RM 审批职责 |

这是职责摘要，不是权限实现。角色、页签与命名能力以 [access_control.json](shared/access_control.json) 为准；所有权、阶段、锁定与申请状态还会进一步限制操作。后端鉴权是最终边界，前端按钮仅用于展示。

其他页面：开发 WIKI、Jenkins 失败查询、CICD 助手（旧版 V1 只保留直达地址）、JIRA agent。完整导航见 [routeConfig.ts](web/src/routes/routeConfig.ts)。

## 发布周期怎么运行

1. 首次使用由 RM 导入 CSV。`import_initial_rows()` 只允许在尚无 release 时初始化；同一仓库与分支的行合并为一个 App，没有仓库或分支的行跳过。
2. 后续周期由 RM 从最近一个 release 克隆。代码继承已有快照和 Owner 确认状态，重置 QA 状态、QA 备注、缺失项缓存及快照锁定标记；不会自动要求每个 Owner 重新确认。
3. Owner / RM 在 App 工作台维护发布决策、说明与配置。新增 App 走 CICD-first 流程，没有直接 `/api/apps/new` 创建接口。
4. 从 Gerrit 获取或上传 app_info，记录来源、commit 和同步时间，派生版本、芯片及测试说明。实际内容变化可能使 Owner 确认失效；重复拉取相同内容不会仅因拉取动作而取消确认。
5. QA 使用范围表、命令表和日志记录结果。AI 分析返回建议，最终状态仍由 QA / RM 保存；分析成功不等于自动批准发布。
6. RM 检查范围、缺项、QA、待处理 CICD 申请与产物，再最终锁定。需要修改时可以解锁，解锁会删除最终产物；目前没有独立的签字审批或不可覆盖的发布包版本库。

阶段由 [phases.py](app/domain/phases.py) 根据锁定标记和 deadline 计算，并非人工填写状态。

```mermaid
flowchart LR
    A[App 冻结前] -->|app freeze deadline| B[App 冻结后]
    B -->|doc deadline| C[文档截止后]
    A -->|RM 最终锁定| D[已最终锁定]
    B -->|RM 最终锁定| D
    C -->|RM 最终锁定| D
    D -->|RM 解锁| E[按当前 deadline 重新判断阶段]
```

- 冻结前可以新增发布范围。冻结后不能升级为 `release`，已在发布范围内的 app_info 更新也不能扩大受控 QA 范围。
- 文档截止前仍可维护文档、app_info 和 Owner 确认；截止后保留的操作包括符合规则的决策调整、CICD 配置、Gerrit 身份、QA 状态与日志。
- 最终锁定后阶段操作表为空，受阶段管控的修改全部拒绝。
- 空 deadline 表示尚未设置截止时间。日期输入提交 `YYYY-MM-DD`，后端 deadline 归一化为该日 `23:59`。

## 三个概念必须区分

| 概念 | 当前实现 |
| --- | --- |
| 发布决策 | `release`：计划进入发布/文档/QA；`cicd_only`：仅 CICD；`stopped`：停止维护/发布。后两者不进入当前生成的发布文档 |
| 文档收录资格 | `qualifies_for_docs()`：决策为 release、Owner 已确认、无文档类缺项。**QA 状态不是文档收录门槛** |
| QA 发布资格 | `qualifies_for_final()`：文档条件成立，且 QA 为 `qa_passed` 或 `has_issues`。Manager Review 使用此资格；`has_issues` 可以通过此门槛，不代表问题已关闭 |

规则见 [gates.py](app/domain/gates.py)、[qa.py](app/domain/qa.py) 和 [domain_metadata.json](shared/domain_metadata.json)。社区字段要求还由 [app_service.py](app/services/app_service.py) 的 `_missing_items_for()` 补充。

**最终锁定不是“全部 App 已通过 QA”的审批证明。** 当前锁定生成逻辑采用文档收录条件，缺项 App 可被排除在生成文档之外；它会检查特定未完成的 CICD 决策同步，但没有完整的发布签核流程。对外发布前应显式核对完整计划范围和实际收录范围。

## CICD 与 App 的关系

- `apps.id` 是当前业务身份。`cicd_task_requests.app_id` 关联 App，兼容字段 `task_id` 也保存同一 App ID。`cicd_tasks` 是遗留兼容表，不是当前任务事实源。
- CICD 状态由 App / release 快照和未完成请求派生；`release`、`cicd_only` 对应 Running，`stopped` 对应 Stopped。普通配置申请不允许直接修改 `status`。
- 新增和配置变更均先进入 pending，再由 RM 审批。RM 可以审批自己的申请，记录 `is_self_approved`。
- 审批可立即应用，也可派发 SPD，等交付确认再应用。SPD 可以退回；RM 可以重新派发、按规则应用退回申请，或填写原因后拒绝退回申请。
- CICD 工作台的任务配置是只读的，申请审批与交付操作仍在该页；配置编辑入口在 App 工作台的 CICD tab。
- Running / Stopped 边界变化会联动所有符合条件的未锁定周期，不能只考虑后续周期。启动升级、停止降级、拒绝回滚和冻结跨越的细节见 [业务流程开发约束](.agents/skills/release-system-dev/references/workflows.md)。
- 不提供 CICD Abandoned / 删除生命周期。全局 App 删除归 Admin 管理，代码会检查锁定周期引用。

实现入口：[cicd_service.py](app/services/cicd_service.py)、[decision_sync.py](app/domain/decision_sync.py)、[identity.py](app/identity.py)。仓库身份匹配必须同时考虑仓库与分支；manifest XML 的存储路径与解析后的 Git 身份不能混为一谈。

## JIRA agent（数字员工）

JIRA agent 是按组配置的数字员工，第一阶段处理 HPC 组的 Bug 类工单：收集信息、分类、判断归属、复现、分析，属于本组且能修复时完成修复与验证。部署、配置和迁移步骤见 [JIRA agent 部署与使用](docs/jira-agent.md)，开发约束见 [jira-agent-dev 技能](.agents/skills/jira-agent-dev/SKILL.md)。

| 位置 | 职责 |
| --- | --- |
| 服务器 A（本网站） | 对话、消息、文件、排队、执行记录、JIRA 评论；通过 WebSocket + token 连接 B |
| 服务器 B（组的 `codex app-server`） | agent 本体；本组知识包（AGENTS.md、skills）、Codex 登录凭证、访问 C/D/E 的 SSH 凭证。B 上不部署适配服务 |
| 服务器 C/D/E | 复现、构建、测试环境，由 B 上的执行用户登录。交单时选择：agent 从 RM 维护的系统机器列表中自选，或用户自填 `user@host`（每次交单都经网页终端从 B 上传公钥并测试免密登录） |

要点：只有当前 JIRA assignee 或 RM 能交单和发消息；agent **只在 JIRA 追加评论**，不改 assignee、状态或代码；每组并发由网站排队控制，网站重启不丢轮次。完整的找单、对话、排队与恢复规则见 [docs/jira-agent.md](docs/jira-agent.md)。

实现入口：[jira_agent_service.py](app/services/jira_agent_service.py)（交单与对话规则）、[jira_agent_runner.py](app/services/jira_agent_runner.py)（排队与执行）、[codex_app_server.py](app/integrations/codex_app_server.py)（通用 Codex 协议客户端）、[jira_agent.py](app/domain/jira_agent.py)（结论 schema、提示词、评论格式）、[JiraAgentPage.tsx](web/src/features/jiraAgent/JiraAgentPage.tsx)，B 侧知识包与启动脚本在 [deploy/jira-agent/](deploy/jira-agent/)。

后续阶段（尚未实现）：跟踪最终 root cause 与人工纠正的学习闭环、按 component 路由到其他组的数字员工、适配类与优化类任务。

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
| CICD 助手会话 | 独立 SQLite 库，由 `ASSISTANT_DATABASE_URL` 指定；不写入主业务库 |
| JIRA agent | 独立 SQLite 库 `JIRA_AGENT_DATABASE_URL`（对话、轮次、时间线、文件索引）；上传文件与拉回的产物在 `JIRA_AGENT_DATA_DIR`；Codex thread 与工作目录在服务器 B |

常规生成的四类产物为 `release_note`、`manual`、`ai4sci`、`data`。Manager Review CSV 单独生成；测试范围 CSV 按需导出，不是 artifacts 的一个 kind。最终锁定生成上述四类 final 产物，不会自动把 Manager Review 升格为最终审批记录。

生成文档是一个时间点的结果，不能假定后续编辑已同步到旧草稿。C500/X201 RST 合并使用 [文档整合技能](.agents/skills/c500-manual-integrator/SKILL.md)，指定目标 release 后再导出，避免误取另一个周期的 final 产物。

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
python -m pip install -r requirements.txt -r requirements-dev.txt
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

生产形态由单个进程服务 API 和前端：在 `web/` 运行 `npm run build` 生成 `web_dist/`，再从仓库根启动 uvicorn。**保持单 worker**：QA 任务状态和 JIRA agent 队列都在进程内。

## 集成配置

LDAP、JIRA、QA 大模型、CICD Agent、网站对外地址和各组 JIRA 数字员工的配置统一放在根目录 `release_system.conf`（已 gitignore），分为 `[ldap]`、`[jira]`、`[qa_llm]`、`[cicd_agent]`、`[site]`、`[jira_agent:<组名>]` 几节。复制 `release_system.conf.example` 起步，各键含义与必填规则见其中注释。配置在每次使用时现读，修改后即时生效。旧的 `ldap.conf` / `jira.conf` / `qa_llm.env` / `jira_agent.conf` 已不再读取，升级时把内容搬到对应节。

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

测试入口：后端 `python -m pytest -q`；前端 `npm run build`、`npm run lint`、`npm test`、`npm run test:e2e`。隔离参数与 E2E 端口见 [验证说明](.agents/skills/release-system-dev/references/verification.md)，golden 见 [golden 说明](tests/golden/README.md)。

## 文档维护

README 负责产品、使用入口和运行方式；技能负责开发约束，`references/` 放按需读取的细节；部署手册与功能建议放 `docs/`。同一规则只写在一处，其他地方链接过去；不把一时的业务数量、特定模型名或机器路径写成永久要求。
