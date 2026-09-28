# JIRA agent：部署与使用

JIRA agent 是按组配置的“数字员工”。网站（服务器 A）负责任务、对话、文件和执行记录；agent 本体是组服务器 B 上的 `codex app-server`，本组的知识、skills、Codex 登录凭证以及访问 C/D/E 的 SSH 凭证都放在 B 上。B 上不部署任何适配服务。

```
浏览器 ──► A 发布网站（/jira-agent）
            │  ws + capability token（JSON-RPC：thread/turn/fs）
            ▼
          B codex app-server（hpc 执行用户）
            ├── ~/hpc-jira-agent/AGENTS.md、.agents/skills   本组知识与 skills
            ├── ~/hpc-jira-agent/workspaces/<JIRA>-<对话ID>/  每个对话一个工作目录
            └── ~/.codex/auth.json、~/.ssh/                   本组执行身份 → C / D / E
```

## 使用流程

1. 在 **JIRA agent** 页左侧查找工单，点击后右侧打开这张工单：
   - 还没有对话时，底部是交单输入框。JIRA assignee 或 RM 填写交单说明（可选），可以附文件，选择执行机器，然后点 **交给 agent**。执行机器二选一，续办沿用：
     - **agent 从系统机器列表自动选择**：agent 按工单需要从本组系统机器中选一台，结论和 JIRA 评论写明实际使用的机器。系统机器由 RM 在页面右上角 **系统机器** 中维护（`user@host` 和说明），RM 负责提前在 B 上配好到这些机器的免密登录。列表为空时不能选自动。
     - **自填 `user@host`**：不加入系统机器列表。**每次**点 **交给 agent** 都会弹出风险提示，确认后在终端里输入该账号密码：网站经 app-server 在 **B 上** 运行 `ssh-copy-id`，把 B 执行账号的公钥追加到对方的 `~/.ssh/authorized_keys`，再从 B 用 BatchMode 测试免密登录，通过后自动交单。公钥仍在对方机器上时 ssh-copy-id 会跳过，不再要求密码。密码只转发给 ssh-copy-id，不保存、不记录。上传后 agent 可以免密登录该账号并执行命令，公钥不会随对话结束失效（删掉 authorized_keys 里注释为 B 公钥注释的那一行才能撤销），所以**必须使用专用测试账号，不要用个人账号**。
   - 已有该 assignee 的进行中对话时，直接显示这个对话；历史对话在标题旁的下拉菜单里切换，只读查看。
   - 点 **新建对话** 先进入交单草稿，不会立即交单；点 **交给 agent** 并确认后，当前对话结束，新建对话。
   - JIRA 评论中的对话链接会自动打开对应工单和对话。

   搜索框规则：
   - 留空：默认列表。普通用户为自己名下未关闭的工单（`assignee = <本人> AND status != Closed`），RM 为本组 JIRA 用户组成员的未关闭工单（`assignee in membersOf(<JIRA_MEMBERS_GROUP>)`）。
   - 填一个 JIRA 编号（`项目KEY-数字`）：只显示这张工单，找不到时单独提示。一次只能填一个编号；要查多张请用 JQL（如 `key in (MC3-1, MC3-2)`）。不识别工单网址。
   - 其他内容：作为 JQL 查询，JQL 写错时显示 JIRA 返回的错误。

   RM 在列表标题旁可以切到 **agent 处理过**：列出所有交给过 agent 的工单（含 JIRA 已关闭的），按 agent 最近活动排序，显示当前 JIRA 状态、最近一次对话的结论和对话数，可按编号、标题、状态或人员过滤。点击打开该工单最近的对话。
2. 网站按排队顺序执行：同步 issue.json、issue.md、JIRA 附件和上传文件到 B 的工作目录，新建 Codex thread，按 AGENTS.md 与 skills 完成分类、归属判断、复现、分析和修复。
3. 页面时间线实时显示 agent 消息、执行的命令（含退出码和输出）、文件修改和结构化结论。
4. 分析完成并成功回收全部交付文件后，网站按本轮设置在 JIRA **只追加评论**：结论、证据、补丁和下一步建议。agent 不修改 assignee、状态或代码。
   - 交单或发消息时取消勾选 **本轮结论发布到 JIRA 评论**，这一轮结论只在网页显示，不发评论（用于只想了解详情、让 agent 继续分析的场景）。选择按轮次生效：运行中或排队中再发消息，以这一轮最后一次发送的选择为准；下一轮重新选择。
   - 分析完成但所列产物缺失、路径越界、超过 10 MiB 或读取失败时，页面显示 **交付不完整**，保留分析结论、已回收文件及逐文件错误，暂停评论发布。当前对话最新一轮可点 **重试回收文件**；只读取文件，不重新执行模型。已回收且校验完整的文件复用，全部成功后沿用原轮次的评论选择。
   - 文件回收期间不能发起续办或替换当前对话；已发起后续轮次或关闭的历史对话不能重试旧轮次，避免读取被后续执行覆盖的工作文件。
5. assignee 阅读评论后自行决定：接受修改并 resolve、转交，或在网站补充信息，让 agent 在同一对话中继续（新一轮沿用同一 thread 和工作目录）。

对话规则：

- 对话属于交单时的 JIRA assignee，RM 代操作时 owner 仍是 assignee。每次发消息都会实时核对 JIRA assignee。
- 同一工单同一时间只有一个进行中的对话。同一 assignee 可以点 **新建对话**，旧对话变为只读。
- assignee 变更后旧对话自动结束（只读），新 assignee 交单时新建对话；即使转回原 assignee 也不复用旧对话。
- 运行中发送的消息直接补充给 agent（`turn/steer`）；排队中发送的消息合并到这一轮；结束后发送的消息开始新一轮。

排队与并发：

- 每组 B 同时运行的轮次不超过 `MAX_CONCURRENT`，其余轮次按先后排队；页面显示“前面还有 N 轮”。
- 每轮限时 `TURN_TIMEOUT_SECONDS`，从出队开始计算，到时中断；网站重启不重新计时。
- 网站重启（包括异常退出）不会丢掉排队中和运行中的轮次。Codex 的轮次不依赖网站连接，重启后网站重新连接 B：
  - 本轮仍在运行：继续接管，时间线补上停机期间的步骤；
  - 本轮已在停机期间结束：照常读取结论、拉回产物、发 JIRA 评论；
  - 本轮还没在 B 上开始：重新排队；
  - 评论停在“发布中”：重启后补发。
  - 分析已保存但文件回收中断：保留分析，标记“交付不完整”，由用户单独重试文件回收。
  只有 B 的 app-server 也重启过，或重启时连不上 B，才标记为“已中断”，发送消息可以继续。续办时如果 B 上还有未结束的上一轮，网站会先中断它。
- 网站刚重启、正在重新接管某一轮时（通常几秒，连不上 B 时最多十几秒），这个对话的“取消本轮”“发送”“新建对话”暂时不可用，页面会提示，接管完成后恢复。已关闭对话的轮次不会被重新执行。
- 中断（取消、超时）只结束 Codex 本轮，已经启动的 shell 命令会继续跑完（Codex 0.153 实测），强制停止 B 的 app-server 时子进程也会留下。
- GPU 等机器资源由 B 上知识包约定的 `flock` 锁控制（见 AGENTS.md）。

## 执行机与资源服务的权限

网站每轮提示限制编译、测试和调试的执行机与账号；项目根 `AGENTS.md` 单独规定已授权源码/制品服务的只读访问。Gerrit 项目与 refs 查询、clone/fetch、镜像及 SDK 下载不把服务主机变为执行机，服务地址也不填入结果的 `machine` 字段。沿用已有授权和服务身份，下载链接或可用凭据不能扩大访问范围。

Agent 只读取网站同步的 Jira 材料，不直接查询 Jira 用户、组件或关联工单。发布目录中的维护人属于线索；缺少账号核验依据时交付具体职责角色，由网站或人工补齐。

## 发布目录与应用范围

涉及发布应用的工单由 B 上主 skill 在最终归属判断前查询 HPC 应用发布目录，按应用名称/别名、仓库身份、应用版本与发布批次匹配。目录是应用维护范围的主要依据，故障责任仍需复现与定位证据支持；应用属于 HPC 不代表其依赖组件的缺陷也由 HPC 修复。

应用存在但版本未匹配、多个候选、未查到或目录不可用时，记录已核验内容、查询范围及缺口，不能仅凭未命中判定外组。后续分析和源码修复模块复用当前对话的范围记录；目标变化或证据冲突时重新查询。查询流程与字段映射见[发布目录接入](../deploy/jira-agent/hpc/.agents/skills/hpc-jira-agent/modules/remote-experiment/references/release-catalog.md#应用范围判定)。

发布目录查询对 `hpc-release.swlab.metax-tech.com` 单独直连，避免沿用模型出网代理；具体 HTTP 调用方法见上述接入说明。

本接入通过部署 skill 复用现有只读接口，由 agent 执行；报告和既有 `ownership.reasoning`、`evidence`、`next_steps` 字段保存依据，不增加网站后台自动判定、数据库字段或适配服务。

## 部署服务器 B（HPC agent）

以下以 B 上的 hpc 执行用户为例。

1. **Codex 登录**：`codex login`，生成 `~/.codex/auth.json`。凭证只存在于 B。
2. **SSH 访问 C/D/E**：为 hpc 用户生成密钥（`ssh-keygen -t ed25519 -C hpc-jira-agent@<B>`），同目录必须有 `.pub`（只有私钥时用 `ssh-keygen -y -f <私钥> > <私钥>.pub` 补出），并把私钥路径写进 A 的 `[jira_agent:<组名>] SSH_KEY_PATH`。系统机器列表中的机器由 RM 提前配好免密（`ssh -o BatchMode=yes <user@host> true` 能通过），再在页面 **系统机器** 中登记。自填机器用的 `ssh-copy-id` 在 B 上运行，B 需要安装它（openssh-client 自带）。
3. **同步知识包**（在仓库检出目录执行）：
   ```bash
   PACK_DIR=$HOME/hpc-jira-agent deploy/jira-agent/sync-pack.sh
   ```
   知识包目录会被初始化为 git 仓库，Codex 以它为项目根，自动加载 AGENTS.md 和 `.agents/skills`。修改知识或 skills 时，改仓库中的 `deploy/jira-agent/hpc/`，再重新同步。当前主入口为 `.agents/skills/hpc-jira-agent/SKILL.md`，按需调用工单材料、远端实验、问题分析和应用分析模块。
4. **生成 WebSocket token**：
   ```bash
   mkdir -p ~/.config/hpc-jira-agent && chmod 700 ~/.config/hpc-jira-agent
   python3 -c 'import secrets;print(secrets.token_urlsafe(32),end="")' > ~/.config/hpc-jira-agent/ws-token
   chmod 600 ~/.config/hpc-jira-agent/ws-token
   ```
5. **启动 app-server**：
   ```bash
   BIND_IP=<B 的局域网 IP> PORT=4510 PACK_DIR=$HOME/hpc-jira-agent deploy/jira-agent/start-app-server.sh
   ```
   脚本以 `approval_policy=never`、`sandbox_mode=danger-full-access` 启动：命令自动执行，权限边界是 B 上的 hpc 用户。可以用 `curl http://<B>:4510/readyz` 检查就绪。

systemd 示例（`/etc/systemd/system/hpc-jira-agent.service`）：

```ini
[Unit]
Description=HPC JIRA agent (codex app-server)
After=network-online.target

[Service]
User=hpc-agent
Environment=BIND_IP=10.2.x.x PORT=4510 PACK_DIR=/home/hpc-agent/hpc-jira-agent
ExecStart=/home/hpc-agent/release-system/deploy/jira-agent/start-app-server.sh
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

## 配置网站 A

1. 在 `release_system.conf` 中为每个组写一节 `[jira_agent:<组名>]`，各键（B 的地址与 token、工作目录根、component、JIRA 用户组、SSH 私钥路径、并发与每轮限时）的含义和必填规则见 `release_system.conf.example`。`CODEX_WS_TOKEN` 与 B 上 `ws-token` 文件内容一致。
2. 同一文件的 `[jira]` 提供 JIRA 地址和 token（读单、下载附件、发评论）；可选的 `[site] PUBLIC_BASE_URL` 让 JIRA 评论附对话链接。
3. 可选环境变量：`JIRA_AGENT_DATABASE_URL`（默认 `sqlite:///jira_agent_tasks.db`）、`JIRA_AGENT_DATA_DIR`（上传文件和拉回产物，默认 `jira_agent_data/`）。
4. 网站保持单 worker（队列 runner 在进程内）。A 到 B 若经过 HTTP 代理，把 B 的地址加入 `NO_PROXY`。
5. 打开 **JIRA agent** 页，或调用 `GET /api/jira-agent/health` 确认连通。

部署前可运行隔离回归 `tests/test_jira_agent_pack.py`；它验证网站生成的真实快照能被新 loader 读取，以及知识包首次安装完整。

启动后使用 [Codex app-server 的 skills/list](https://learn.chatgpt.com/docs/app-server#skills)，指定测试工作目录和 `forceReload: true`，核对主入口可见且无加载错误。这不启动模型回合，也不会操作 Jira。随后由人工在网页选工单、新建对话，首次可取消勾选“本轮结论发布到 JIRA 评论”，核对分类、执行机、报告下载与续办，再决定是否测试评论发布。

## 把 B 迁移到独立服务器

只需要换启动用户和地址、token，以及 A 的配置：

1. 在新 B 上创建 hpc 用户，完成上面“部署服务器 B”的 1–5 步（Codex 登录、SSH 凭证、同步知识包、生成 token、启动）。
2. 修改 A 的 `release_system.conf` 中该组的 `[jira_agent:<组名>]`：`CODEX_WS_URL` 改为新 B 地址，`CODEX_WS_TOKEN` 改为新 token，`WORKSPACE_ROOT` 改为新用户的目录。
3. 必要时把新 B 的地址加入 A 的 `NO_PROXY`（改了 `NO_PROXY` 需重启网站；配置文件本身保存即生效）。
4. 已有对话的 Codex thread 和工作目录保存在旧 B 上，迁移后需要为这些工单新建对话。

## 完整工单材料采集

每轮准备由 `get_issue_snapshot` 读取当前账号可见的原始字段及自定义字段，并通过独立评论接口分页采集；负责人/权限校验继续使用轻量 `get_issue`。采集请求共享最多 120 秒预算，评论最多 100 页，并限制响应与累计评论大小。缺字段名称时保留字段 ID 和原值；分页失败、总数变化、重复 ID、提前空页或达到预算时保留已取内容并标记 partial/unavailable。

网站生成版本为 1 的 `issue.json`，保留 `acquisition.parts`、自定义字段及原始评论，同时生成阅读版 `issue.md`。JSON 中记录 Markdown 的 SHA-256，防止断线/中断同步后混用两轮材料。skill 优先读取 JSON，只有不存在时使用旧 Markdown；无效 JSON 不静默降级。每轮重新采集，现有对话可在下一轮使用该能力，无需数据库迁移。

回归入口为 `tests/test_jira_agent_snapshot.py`（真实本地 HTTP 采集到 skill 读取）、`tests/test_jira_agent_pack.py` 与 `tests/test_jira_agent.py`。JSON 格式细节见部署 skill 的 `modules/jira-evidence/references/input-acquisition.md`。

## 文件交付状态与重试接口

`jira_agent_turns.status=completed` 表示分析完成；`delivery_status` 独立记录 `collecting` / `incomplete` / `complete`，`delivery_errors_json` 保存文件路径和错误。旧库启动时增量补列，历史行保留空状态，不追溯更改已有结论或评论。API 的轮次视图提供 `delivery_status` 和 `delivery_errors`；对话派生状态增加 `delivering`、`delivery_incomplete`。

`POST /api/jira-agent/turns/{turn_id}/delivery/retry` 仅允许有查看权限且仍为当前 Jira assignee 或 RM 的用户操作；对话须仍开放、归属未变，且该轮是最新的分析完成但交付不完整轮次。数据库条件更新防止并发回收，返回 `{turn: ...}`；文件仍未收齐时返回保留分析的 `incomplete` 轮次及错误。成功回收后才按 `post_comment` 决定是否发布评论；评论失败仍使用原评论重试接口。
