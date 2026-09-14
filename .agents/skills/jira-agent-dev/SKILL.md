---
name: jira-agent-dev
description: Develop, debug, or review the JIRA agent (per-group digital employee) in this repository — website-side conversations/queue/JIRA comments, the Codex app-server WebSocket client, the server-B knowledge pack (AGENTS.md + skills), and its isolated and end-to-end verification.
---

# JIRA agent 开发

适用于 JIRA agent 的功能开发、排障和审查。通用仓库约束（分层、真实数据保护、前端与时间规则）仍按 [release-system-dev](../release-system-dev/SKILL.md) 执行；产品说明见 [README](../../../README.md) 的 JIRA agent 一节，部署与迁移见 [docs/jira-agent.md](../../../docs/jira-agent.md)。

## 不可随意改变的边界

以下是用户明确定下的规则。新的明确需求可以调整，但不能在实现中顺手放宽：

- **A/B 分工**：网站（A）只管理对话、消息、文件、排队、执行记录和 JIRA 评论，通过 WebSocket + capability token 直连组服务器 B 上的 `codex app-server`。**B 上不写适配服务**，只有启动命令、知识包、Codex `auth.json` 和访问 C/D/E 的 SSH 凭证。沙箱与审批策略由 B 的启动参数决定，A 在 `thread/start`、`turn/start` 中不传 sandbox/approval。
- **只评论**：agent 不改 assignee、不转单、不 resolve、不提交代码；网站把结构化结论渲染成评论追加到 JIRA，由 assignee 决定下一步（human-in-the-loop）。
- **谁能操作**：只有当前 JIRA assignee 或 RM 能交单、发消息，每次写操作都实时读 JIRA 校验。查看权限为对话 owner、交单人或 RM。页签角色只来自 `shared/access_control.json` 的 `jira-agent`。
- **找单**（`GET /api/jira-agent/issues?q=`，分类逻辑在 `domain.parse_issue_query`）：
  - 留空时，普通用户看 `assignee = "<网站用户名>" AND status != Closed`，RM 看 `assignee in membersOf("<JIRA_MEMBERS_GROUP>")`。不能用 `currentUser()`，它指向 jira.conf 的 token 账号。
  - 每个词都匹配 `项目KEY-数字`（字母开头，允许数字和下划线）时逐个读单，找不到的列入 `missing`。
  - 其他输入原样作为 JQL，JIRA 返回 400 时把 `errorMessages` 回给用户。
  - 不识别工单网址，因为业务 JIRA 地址与测试环境不同。
  - 搜索使用 jira.conf 账号的可见范围，交单仍校验 assignee 或 RM。
- **对话隔离**：一个对话 = 一个 Codex thread + B 上一个工作目录，owner 是交单时的 assignee（RM 代操作时 owner 仍是 assignee）。
  - assignee 变更后旧对话关闭为 `superseded`（只读）；A→B→A 转回也新建对话，不复活旧对话。
  - 同一 assignee 可显式新建对话（`new_conversation`）。
  - 同一工单同一时间最多一个 open 对话，数据库部分唯一索引兜底。
- **跨组**：每组有自己的数字员工（`jira_agent.conf` 一个 section 对应一个 B），一个数字员工不跨组修复。当前只部署 HPC。
- **排队**：app-server 本身会并行执行不同 thread 的 turn，所以队列和并发上限必须在 A 上：每组 `MAX_CONCURRENT`，FIFO。机器资源（GPU）由知识包约定的 `flock` 锁控制，A 不感知选机。

## 代码地图

| 修改目标 | 入口 |
| --- | --- |
| HTTP 契约、请求体 | [app/api/routers/jira_agent.py](../../../app/api/routers/jira_agent.py) |
| 交单、续办、新建对话、assignee 变更、取消、评论重试、视图 | [app/services/jira_agent_service.py](../../../app/services/jira_agent_service.py) |
| 调度循环、单轮执行、输入同步、事件记录、产物拉回、评论发布、健康检查 | [app/services/jira_agent_runner.py](../../../app/services/jira_agent_runner.py) |
| 结论 JSON schema、组配置解析、工作目录路径、提示词、评论 wiki markup | [app/domain/jira_agent.py](../../../app/domain/jira_agent.py) |
| Codex app-server 通用客户端（无 JIRA 逻辑） | [app/integrations/codex_app_server.py](../../../app/integrations/codex_app_server.py) |
| JIRA 读单、下载附件、追加评论 | [app/integrations/jira.py](../../../app/integrations/jira.py) 末尾 |
| 表结构 / 数据访问 | [app/db/jira_agent_connection.py](../../../app/db/jira_agent_connection.py)、[app/repositories/jira_agent_repo.py](../../../app/repositories/jira_agent_repo.py) |
| 页面与 API 类型 | [web/src/features/jiraAgent/](../../../web/src/features/jiraAgent/)，样式在 `App.css` 的 `.jira-agent-*` |
| B 侧启动与知识包 | [deploy/jira-agent/](../../../deploy/jira-agent/)（`start-app-server.sh`、`sync-pack.sh`、`hpc/AGENTS.md`、`hpc/.agents/skills/`） |

Runner 在 FastAPI lifespan 中启停（`settings.jira_agent_runner_enabled`），网站必须保持单 worker。

## 数据模型与状态

- **`jira_agent_conversations`**：`status` 为 open/closed，`close_reason` 为 superseded/new_conversation；另有 `thread_id`、`workspace`、`thread_archived`。
- **`jira_agent_turns`**：
  - `status` 取 queued / running / completed / failed / cancelled / interrupted。queued 行就是持久队列，按 rowid FIFO；每个对话最多一个 queued 或 running（部分唯一索引）。
  - `comment_status` 为空 / pending / posted / failed，失败只重试发布，不重跑模型。
- **`jira_agent_events`**：时间线。`seq` 是显示顺序，`rev` 在每次插入或更新时递增，前端用 `?after=rev` 增量轮询；命令事件按 `item_id` 原地更新（started → completed）。
- **`jira_agent_files`**：`source` 为 jira/upload/artifact；`remote_path` 是 B 工作目录内的相对路径，`local_path` 只对上传和拉回的产物存在。
- **对话状态**：`state` 由对话与最新轮次派生：closed / idle / queued / running / waiting_review（最新轮 completed）/ failed / cancelled / interrupted。
- **运行阶段**：内存中 `ActiveTurn.phase` 为 preparing → starting → running → finishing。
  - 运行中（running）的消息走 `turn/steer`；preparing 阶段的消息合并进本轮输入；starting、finishing 阶段返回 409。
  - 取消：
    - running：调 `turn/interrupt`。
    - preparing：直接取消任务，此时 B 上还没有 turn。
    - starting：只记录停止原因，拿到 turnId 后立即 `turn/interrupt`。这时 `turn/start` 可能已到达 B，不能直接取消任务，否则 B 上的 turn 会继续跑。
    - 超时从出队开始计时，中断后等待 60 秒宽限。
  - 启动恢复：残留的 running 标为 interrupted，不自动重跑有副作用的工作。

## Codex app-server 协议要点

协议以 `codex app-server generate-json-schema --out <临时目录>` 生成的 schema 为准，升级 Codex 后先重新核对。实测过的行为：

- **认证**：`--ws-auth capability-token` 时，客户端在握手请求头带 `Authorization: Bearer <token>`；token 错误返回 HTTP 401。非回环地址监听必须开认证。
- **握手**：每个连接先 `initialize` 请求，再发 `initialized` 通知。
- **通知路由**：thread 的通知只发给 start/resume 它的连接，因此一轮执行期间保持同一连接，steer/interrupt 也经过这条连接发送。
- **用到的方法**：
  - 线程与回合：`thread/start`（cwd、developerInstructions、model、serviceName）、`thread/resume`（excludeTurns）、`thread/archive`、`turn/start`（input、outputSchema、model）、`turn/steer`（必须带 `expectedTurnId`）、`turn/interrupt`。
  - 文件：`fs/createDirectory`、`fs/writeFile` / `fs/readFile`（`dataBase64`，绝对路径）。
- **读取的通知**：`item/started`、`item/completed`（agentMessage / commandExecution / fileChange / reasoning 等）、`turn/completed`（status 为 completed / interrupted / failed）、`error`（`willRetry` 为 false 才记录）。delta 类通知不用。
- **结构化结论**：`outputSchema` 必须是 strict schema（每层 `additionalProperties: false`，所有属性 required）。最终 agentMessage 的文本就是 JSON；`parse_result` 兼容外层 ```json 包裹。
- **不处理的服务端请求**：审批请求和 `item/tool/call` 客户端不处理，统一回错误并记事件。如果出现，说明 B 没按 `approval_policy=never` 启动。

## 知识包（B 侧）开发

- 源文件在 `deploy/jira-agent/hpc/`，用 `sync-pack.sh` 同步到 B 的 `PACK_DIR`。该目录会被初始化为 git 仓库，Codex 以它为项目根，所以 `workspaces/` 下的每个工作目录都会加载 AGENTS.md 和 `.agents/skills`；启动脚本同时把它标为 trusted。
- AGENTS.md 放组职责、边界、机器清单与资源规则、证据规则、结论含义；流程细节放 skills（分类归属 → 复现修复 → 转交材料）。修改走 git 评审后再同步，不直接在 B 上改正式版本。
- 工作目录约定：`issue.md`（每轮刷新）、`attachments/`、`uploads/`、`work/`、`artifacts/`。`artifacts` 字段必须列出文件而不是目录，否则拉回会失败。
- 提示词中与组无关的框架（不操作 JIRA、真实证据、输出 schema）在 `domain/jira_agent.py` 的 `developer_instructions` / `build_turn_prompt`；组知识不写进网站代码。

## 验证

**单元与集成测试**：`python -m pytest -q tests/test_jira_agent.py`。

- 测试用真实 websockets 起一个假的 app-server（token 认证、thread/turn/steer/interrupt/fs、`hold` 计划），并替换 `jira.get_issue` / `download_attachment` / `add_comment`。
- 新增行为时扩展 `FakeCodex` 的计划，不要 mock 掉 runner。
- 权限矩阵测试在 `tests/test_access_control.py` 和 `web/src/lib/__tests__/accessControl.test.ts`。
- 前端：`web/src/features/jiraAgent/__tests__/JiraAgentPage.test.tsx`，外加 tsc、lint、build。

**隔离**：启动应用的 lifespan 会运行 runner，并按默认路径在仓库根创建 `jira_agent_tasks.db`。跑全量测试或本地实例时，除 [verification.md](../release-system-dev/references/verification.md) 的变量外，还要设置：

```bash
export JIRA_AGENT_DATABASE_URL="sqlite:///$verification_dir/jira_agent.db"
export JIRA_AGENT_DATA_DIR="$verification_dir/jira_agent_data"
export JIRA_AGENT_CONF_PATH="$verification_dir/jira_agent-disabled.conf"
export JIRA_AGENT_RUNNER_ENABLED=false   # 不需要队列时
```

**真实端到端**（只在用户授权的工单上做，它会在 JIRA 追加评论）：

1. 用 `deploy/jira-agent/start-app-server.sh` 启动 B，`curl http://<B>:<端口>/readyz` 就绪。
2. 用业务库的一致性副本（SQLite backup API）作为 `DB_PATH` 启动网站，设置 `JIRA_AGENT_PUBLIC_BASE_URL`，并把 B 的地址加入 `NO_PROXY`。
3. `GET /api/jira-agent/health` 确认连通后，交单（`POST /api/jira-agent/conversations`），轮询对话详情直到最新轮次结束，从 JIRA 回读评论，核对 status 和 assignee 未被修改。
4. 续办验证同一 `thread_id`；页面截图用 Playwright，时间线面板有 `max-height`，整页截图时需要去掉。

## 已知陷阱

- **代理**：本机设置了 `ALL_PROXY` 等代理变量，`websockets` 会走代理。A 连接 B 时必须让 B 的地址命中 `NO_PROXY`，否则连接失败或误走代理。
- **`pkill -f <模式>`**：模式会匹配执行它的 shell 自身，把 shell 杀掉。停止 app-server 用 `ss -ltnp` 找监听 PID 再 kill。
- **JIRA 评论排序**：`/rest/api/2/issue/<key>/comment` 不保证按请求参数排序，回读最新评论时按 `created` 自行排序。
- **测试单被“污染”**：MC3-7672 带有旧分支 agent 的修复附件和含根因的评论，agent 会读到，不能用它评估真实分析能力。
- **共享 GPU**：执行机 GPU 常被其他用户任务占用。知识包要求独占时 agent 会停下并给出 `needs_help`，这是预期行为；需要放宽时由 assignee 在对话中明确授权。
- **B 上的 Codex 配置**：B 上的 `~/.codex` 配置和用户级 skills 会带入 agent。正式部署使用专用 hpc 用户，避免混入个人配置。

## 后续阶段（未实现，改动前先确认需求）

- **学习闭环**：在权限范围内回读最终 root cause 和人工纠正，对比 `result_json`，沉淀案例，产出知识、skill、测试改进建议，经评审后更新知识包。
- **多组路由**：按 component 路由到其他组的 B；目标组没有数字员工时，评论建议人工接手。
- **更多任务类型**：适配类、优化类任务（接入外部 skill）。
