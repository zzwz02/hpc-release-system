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

知识学习初版的范围与独立服务配置见[Jira 知识学习](jira-learning.md)。

## 执行机器与数据预检查

正式交单、单次测试与跨用例批次共用预检查。页面点击“开始检查”后通过 NDJSON 实时展示逐机结果：系统机器只检查 SSH，自填机器额外检查共享数据；全部通过则提交，有异常则显示“返回修改 / 重新检查 / 继续可执行工作”。必需机器 SSH 不通不能提交，候选池可排除失败机器但须确认；数据异常只允许继续具备条件的工作，不能把缺数据的实验报告为通过。共享数据依赖由 agent 按任务自动判断，页面不提供依赖选项。自动模式在交单和出队复核时均不探测共享数据；实际实验仍按任务核对输入。

在 `[jira_agent:<组名>]` 配置 `DATASET_PATH`（HPC 为 `/pde_hpc/dataset`）、`DATASET_EXPECTED_SOURCES`（JSON 来源列表）、`DATASET_PROBE_RELATIVE_PATH`（已有小型只读文件）及可选 `DATASET_PROBE_EXPECTED_VALUE`（预期文本）。未配置时明确显示未验证。探测由 B 经 SSH 使用实际账号执行：检查路径可访问、所属文件系统来源、探测文件读取与标识；兼容父目录挂载及目录内符号链接，自动挂载选择实际文件系统而非 autofs 占位；拒绝探测文件越出数据目录。目标需 Python 3 和 findmnt，缺工具或超时为未确认。探测不创建文件、不递归扫描、不自动挂载。

`POST /api/jira-agent/execution-preflights` 接收 `agent_group`、`context`（jira/test/suite）、结构化 `execution`、逐机 `key_sessions`，返回不可变结果及一次性 `preflight_id`。`Accept: application/x-ndjson` 可接收逐机进度；普通 JSON 请求返回最终结果。检查最长有效 10 分钟，并受各 SSH 凭证剩余有效期限制；过期的目标可在面板逐台重新授权。修改机器清单或测试版本后重新检查。关闭检查面板会取消在途探测；已完成结果本身不创建任务。

交单与测试请求带 `preflight_id`、`request_id`、`acknowledged_issue_ids`。后端核对用户、组、工单与负责人或用例版本、配置及有效期；在创建任务的同一事务中消费结果和所有 SSH 凭证，失败回滚。重复的同一请求返回已创建结果，改变内容或复用凭证被拒绝。旧客户端直接交单会提示刷新。

对话的 `execution_config_json` 保存机器配置，`execution_snapshot_json` 保存提交时证据及用户确认；每轮独立保存环境复核和 `machines_used_json`。旧单机字段兼容读取，多台机器不拼接到 SSH 字符串。出队重新检查；自动模式首轮只保留原快照与当前系统清单的交集，续办新轮次使用当前清单。对话中机器配置固定，换机需新建对话；运行中追加消息不触发重新授权。网站重启恢复已有远端轮次时使用其已存范围，不重新启动实验。

基础检查不保证具体数据齐全或容器可读；agent 在实际实验前复核。最新结果显示在时间线。历史批次的输入和 schema 保留，旧结果仍可查看。

## 使用流程

1. 在 **JIRA agent** 页左侧查找工单，点击后右侧打开这张工单：
   - 还没有对话时，底部是交单输入框。JIRA assignee 或 RM 填写交单说明（可选），可以附文件，选择执行机器，然后点 **交给 agent**。执行机器二选一，续办沿用：
     - **agent 从系统机器列表自动选择**：agent 按工单需要从本组系统机器中按需选择一台或多台，网站完整结论记录实际使用的机器，只有影响判断或交接时才进入 JIRA 评论。系统机器由 RM 在页面右上角 **系统机器** 中维护（`user@host` 和说明），RM 负责提前在 B 上配好到这些机器的免密登录。列表为空时不能选自动。
     - **用户指定机器**：通过“添加机器”逐行填写 `user@host` 和可选机器类型（如 C500 / C600），最多 32 台，不加入系统机器列表。agent 根据任务从指定清单中选择一台或多台，满足任务所需节点数。**每次**点 **交给 agent** 后，在执行环境检查中点击 **输入密码并检查**，逐台进入 **机器登录授权**。确认授权并点击 **开始登录** 后，按提示输入该机器账号的登录密码：网站经 app-server 在 **B 上** 运行 `ssh-copy-id`，把 B 执行账号的公钥追加到对方的 `~/.ssh/authorized_keys`，再从 B 用 BatchMode 测试免密登录。多台机器逐台授权，随后统一检查执行环境。公钥仍在对方机器上时 ssh-copy-id 会跳过，不再要求密码。密码只转发给 ssh-copy-id，不保存、不记录。上传后 agent 可以免密登录该账号并执行命令，公钥不会随对话结束失效（删掉 authorized_keys 里注释为 B 公钥注释的那一行才能撤销），所以**必须使用专用测试账号，不要用个人账号**。
   - 已有该 assignee 的进行中对话时，直接显示这个对话；历史对话在标题旁的下拉菜单里切换，只读查看。
   - 点 **新建对话** 先进入交单草稿，不会立即交单；点 **交给 agent** 并确认后，当前对话结束，新建对话。
   - JIRA 评论中的对话链接会自动打开对应工单和对话。

   左侧列表按网站角色区分：
   - **Owner · 我的工单**：只显示 QA 用户组 `dl.sw_qa` 报单、当前指派给本人且状态不是 Resolved/Closed 的 Bug，按标题和创建时间倒序；Owner 没有编号或 JQL 搜索框。QA 用户组可通过 `[jira] JIRA_AGENT_OWNER_QA_REPORTER_GROUP` 覆盖。
   - **Owner · agent 处理过**：显示本人曾负责或创建的交单会话，以及本人作为 reporter 的已交单工单，包括已关闭的。仅作为 reporter 时可查看处理记录，不能因此获得交单或续办权限。
   - **RM · 查找**：留空时显示 QA 提交、当前或曾经分配给本组成员、尚未 Resolved/Closed 的 Bug，按创建时间倒序。HPC 默认 JQL 为 `type = Bug AND reporter in membersOf("dl.sw_qa") AND (assignee in membersOf("pde_hpc") OR assignee was in membersOf("pde_hpc")) AND status not in (Resolved, Closed) ORDER BY created DESC`；可按一个 JIRA 编号或任意 JQL 查询服务令牌有权访问的工单。一次只能填一个编号；查询多张请用 JQL。
   - **RM · agent 处理过**：显示全部交给过 agent 的工单（含已关闭的），可按编号、标题、状态或人员在列表中过滤。

   后端对 Owner 的列表、工单直达链接和交单接口执行同一范围检查；交单及续办仍要求当前 JIRA assignee 或 RM。JIRA 令牌无权读取的工单不会显示。

   RM 可切换到 **知识学习**，从 agent 处理过的工单中勾选一张或多张，也可添加历史 Jira。多选创建一个联合学习任务，综合各张工单生成提案，由 RM 审核后人工更新 skill。
2. 网站按排队顺序执行：同步 issue.json、issue.md、JIRA 附件和上传文件到 B 的工作目录，新建 Codex thread，按 AGENTS.md 与 skills 完成分类、归属判断、复现、分析和修复。
3. 页面时间线实时显示 agent 消息、执行的命令（含退出码和输出）、文件修改和结构化结论。
4. 分析完成并成功回收全部交付文件后，网站按本轮设置在 JIRA **只追加评论**：一句直接回答本轮问题的加粗结论、自然组织的正文及具体详细记录链接。正文按需包含关键对照、结论边界和集中表达的行动请求。完整报告、补丁和日志留在网站；用户索要或交付必需的操作命令可以保留在评论。agent 不修改 assignee、状态或代码。
   - 交单或发消息时取消勾选 **本轮结论发布到 JIRA 评论**，这一轮结论只在网页显示，不发评论（用于只想了解详情、让 agent 继续分析的场景）。选择在当前对话内持续保留；手动修改后再发消息，以这一轮最后一次发送的选择为准，后续轮次继续沿用。新建对话恢复默认勾选。
   - 分析完成但所列产物缺失、路径越界、超过 10 MiB 或读取失败时，页面显示 **交付不完整**，保留分析结论、已回收文件及逐文件错误，暂停评论发布。当前对话最新一轮可点 **重试回收文件**；只读取文件，不重新执行模型。已回收且校验完整的文件复用，全部成功后沿用原轮次的评论选择。
   - 文件回收期间不能发起续办或替换当前对话；已发起后续轮次或关闭的历史对话不能重试旧轮次，避免读取被后续执行覆盖的工作文件。
5. assignee 阅读评论后自行决定：接受修改并 resolve、转交，或在网站补充信息，让 agent 在同一对话中继续（新一轮沿用同一 thread 和工作目录）。

对话规则：

- RM 可选择当前最新对话或历史对话，点击 **删除对话** 并确认。删除该对话全部轮次、事件、分析结果和本地文件，无法恢复；同时清理 B 上该对话的整个工作目录，已发布的 JIRA 评论保留。先清理 B，再删除网站记录和本地文件；B 端失败也继续删除网站记录和本地文件，并提示未清理的 `机器IP或主机名:完整目录路径`，由管理员手动清理；目录已不存在时视为清理成功。目录必须与当前组配置及对话生成路径完全一致，否则跳过远程清理并提示；组配置不可用时提示机器未知及所属组，不阻止删除网站记录。迁移过 B 或工作目录时需先确认原配置。仍在排队、执行、回收文件或发布评论的对话不能删除；删除过程中不能续办、重试交付或发布评论。删除后切换到剩余的当前对话，无当前对话则选择最近的历史对话，没有剩余对话则返回交单表单。

- 对话属于交单时的 JIRA assignee，RM 代操作时 owner 仍是 assignee。每次发消息都会实时核对 JIRA assignee。
- Codex 返回 cybersecurity risk warning（包括错误事件或轮次失败）后，按 JIRA 编号持久锁定非 RM 操作：owner 仍可查看，但不能交单、新建对话、发消息、取消或重试交付/评论，仅 RM 可处理。更换 assignee、重建/删除对话和网站重启都不会解除锁定；升级时自动补录已有错误和告警中的风险记录。
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
  - 评论尚未发送：重启后继续发布；发送过但结果不确定：先回读核对，不盲目重发。
  - 分析已保存但文件回收中断：保留分析，标记“交付不完整”，由用户单独重试文件回收。
  只有 B 的 app-server 也重启过，或重启时连不上 B，才标记为“已中断”，发送消息可以继续。续办时如果 B 上还有未结束的上一轮，网站会先中断它。
- 网站刚重启、正在重新接管某一轮时（包含有限重连与远端状态回读），这个对话的“取消本轮”“发送”“新建对话”暂时不可用，页面会提示，接管完成后恢复。已关闭对话的轮次不会被重新执行。
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
   SKILL_REPO=/path/to/jira_agent_skill SKILL_COMMIT=<完整提交SHA> \
     PACK_DIR=$HOME/hpc-jira-agent deploy/jira-agent/sync-pack.sh
   ```
   知识包目录会被初始化为 git 仓库，Codex 以它为项目根，自动加载 AGENTS.md 和 `.agents/skills`。修改知识或 skills 时，在独立的 `jira_agent_skill` 仓库中修改、验证并提交，再指定该提交重新同步。当前主入口为 `.agents/skills/hpc-jira-agent/SKILL.md`，按需调用工单材料、远端实验、问题分析和应用分析模块。
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

## 简短评论与同轮恢复

每轮完整 JSON 的 `comment` 包含 `kind`、`outcome`、`body`、`evidence_indices`（最多两项零起始证据索引）、`commands`、`link_text`。数字员工标识用普通文本，避免方括号被 JIRA 识别为错误链接。首句直接表达技术结论并加粗；正文使用自然段或列表，将必要事实、结论边界、修正和具体请求按阅读顺序组织，不生成重复的“依据/待办”标题。`body` 使用 JIRA wiki 文本（空行分段、`* ` 列项、`{{符号}}` 代码），网站只补身份、首句样式、必要命令和可信的对话链接，不再追加完整结果中的 `assistance`、`ownership`、`next_steps`。证据索引仅供追溯，完整结构化结果和产物仍在网站保留。

旧结果、旧 schema 在途任务保留原投影兼容路径；缺少有效投影时保守使用原摘要、根因和修复说明，不从证据数组机械取前两项。已保存、已发布的正文不重新生成。续办提示包含上一轮结果摘要和已发布评论；历史验证需注明来源。

评论末尾由网站统一添加横向分隔线（JIRA wiki 标记 `----`），并保留声明：“本评论由 JIRA 数字员工根据本轮分析自动生成，不会修改 assignee、状态或代码；后续操作由 assignee 审核决定。”模型不要在正文中重复该声明。

首句加正文通常 200～360 字符，附身份、链接与固定声明后整条通常约 400～600 字符，无最低长度和固定段数；必要命令和复杂交接可适当增加。原始 trace 编号、分配块大小、详细环境清单和材料审计过程留在报告，不逐项反驳用户本轮未提出的误解。解释性追问的回答本身就是交付，不要求 owner 再转述、确认或审核；只有确有未完成且需他人处理的事项才提出请求。阻塞只写状态与具体请求，修正旧判断明确说明修正内容。只保留会影响判断或下一步的未确认项，不罗列所有未做工作。采集数量、目录核验、完整输出和补丁默认留在网站，用户要求或影响交接时可保留。长度与投影警告仅记网站时间线，不重跑分析，也不扣留真实阻塞通知。

`CODEX_WS_URL` 在创建任务前检查 ws/wss 协议、主机、端口与非法嵌入账号；限时必须大于 0。认证及可达性继续由健康检查提供。确定性配置错误应先修正再交单。

断线先接回原 Codex turn；模型容量不足只有在最终失败、远端 idle，且完整 items 确认尚未执行任何工具时才允许重新开始内部尝试。已执行工具可能留下后台实验，不自动重跑。两类恢复共用每业务轮最多两次的持久预算与退避，app-server 的 `willRetry` 不叠加网站重试。用户取消、权限或配置错误不重试。`started_at`、业务 `turn_id`、机器授权和本轮最后的 `post_comment` 保持有效，steer 和内部尝试不额外计业务轮次。

## 评论发布防重

新增 `jira_agent_comment_publications` 持久保存固定正文和发送状态，并在评论末尾保留业务轮次记录编号，与固定声明合为同一行。已冻结的旧正文继续原样用于发布和核对。发送前记录意图，明确的 HTTP 拒绝才允许重发；超时、断线、缺少返回 ID 和不确定的服务端错误都进入待确认。页面显示“核对发布结果”，仍复用原 `/comment/retry` 接口，但待确认状态下只回读，不再次 POST。

回读核对全部可见评论页、当前发布账号、轮次编号和已保存正文。分页不完整、相同编号正文被修改、作者不符或找到多条匹配时保留待确认。没有找到评论也不证明原 POST 没有延迟生效，因此不自动重发。历史 pending/failed 没有发送审计时按不确定结果处理，先核对原正文，不改历史 JIRA 评论；长期不确定需运维结合 JIRA 审计确认。

发送有并发保护；网站重启核对 sending/uncertain，成功后补记评论 ID。测试运行及 `post_comment=false` 继续禁止发布。该机制避免盲目重发，不宣称跨数据库和 JIRA 的事务原子性。

## 文件交付状态与重试接口

`jira_agent_turns.status=completed` 表示分析完成；`delivery_status` 独立记录 `collecting` / `incomplete` / `complete`，`delivery_errors_json` 保存文件路径和错误。旧库启动时增量补列，历史行保留空状态，不追溯更改已有结论或评论。API 的轮次视图提供 `delivery_status` 和 `delivery_errors`；对话派生状态增加 `delivering`、`delivery_incomplete`。

`POST /api/jira-agent/turns/{turn_id}/delivery/retry` 仅允许有查看权限且仍为当前 Jira assignee 或 RM 的用户操作；对话须仍开放、归属未变，且该轮是最新的分析完成但交付不完整轮次。数据库条件更新防止并发回收，返回 `{turn: ...}`；文件仍未收齐时返回保留分析的 `incomplete` 轮次及错误。成功回收后才按 `post_comment` 决定是否发布评论；评论失败仍使用原评论重试接口。

### 对话服务商、模型与 effort

RM 在新建对话交单时可选择服务商、模型及该模型支持的 effort。默认仍沿用 B 上的默认服务商（本地配置为 OpenAI / GPT plan）。**同一对话固定 provider，切换 OpenAI / DeepSeek 必须新建对话**，对应新的 Codex thread。续办可调整同一 provider 下的模型和 effort；排队或运行中不能调整。普通用户没有选择入口，后端也校验 RM 权限及 provider、模型、effort 的组合。

模型来源：

- 默认服务商使用 B 的 `model/list` 和 `config/read`，组 `MODEL` 可覆盖默认模型。
- 其他 provider 须先在 B 的用户 `~/.codex/config.toml` 配置 URL 与认证。Key 只保留在 B，网站接口不返回或传入 Key。
- A 的 `release_system.conf` 在对应 `[jira_agent:<组>]` 节增加可选 `MODEL_CATALOGS`，例如 `MODEL_CATALOGS = {"deepseek": "/home/hpc-agent/.codex/deepseek-models.json"}`。路径为 **B 上的绝对路径**，文件使用 Codex 官方模型目录格式（`models` / `slug` / `default_reasoning_level` / `supported_reasoning_levels`）。目录的第一个可见模型是该 provider 的默认模型。
- A 通过 app-server 的 `fs/readFile` 读取目录，只向网页返回 provider 名称、模型标识和 effort 选项，不返回目录中的提示词或 provider 凭据。额外目录无法读取时，页面显示对应错误，其余可用服务商仍可选择。
- 新建对话及续办时把对应 `modelProvider` 和 `config.model_catalog_json` 传给 `thread/start` / `thread/resume`。额外目录的 provider 禁用内置 web search，避免默认假设第三方 Responses 端点支持该工具。默认服务商保留 B 上原配置。

provider 保存在 `jira_agent_conversations.model_provider`；模型与 effort 保存在 `jira_agent_turns.model` / `effort`，每轮实际值回填记录。模型、effort 和发布评论的选择在同一对话内保留，刷新页面、网站重启不重置，新建对话恢复默认。旧库初始化时补列；旧对话的 provider 从已有 thread 确认，不能用今日默认值覆盖已有 thread 的服务商。

执行端检查 `thread/start` / `thread/resume` 响应顶层的实际 `modelProvider` 是否与对话一致，若不一致则拒绝发送新轮次。不要仅检查 `thread.modelProvider`：它是持久化元数据，未必反映当前运行配置。当前 Codex 对已加载的 thread 可能忽略 resume 的 provider 覆盖；网站不提供解除订阅后换 provider 的操作，以保持对话上下文和执行规则简单。



## 测试用例

RM 在工单对话中选择“保存为测试用例”，核对名称、工单内容、输入文件和任务要求后保存，不需要填写人工验收标准或人工参考。也可从“测试用例”入口手动新建。导入读取分析服务上当前保存的 `issue.json`、附件及首轮上传文件和要求，不重新查询 Jira。多轮对话的快照可能已刷新，保存前应删除带有答案的评论、历史分析报告和已有修复。

用例页面不显示版本选择或编号，修改和启动测试使用最新保存的输入。每次测试仍保留当时的输入，查看历史结果时可展开“本次测试输入”。只有一个分析组时自动使用并隐藏选择框；选机与正式交单一致：默认由 agent 从本组系统机器中选择，也可自填 `user@host`，上传 SSH 公钥并通过连接验证后启动。结果显示实际执行机器。“高级设置”只配置模型和推理强度，默认使用分析组模型和 `xhigh`。测试统一使用分析服务当前的 skill，不支持指定仓库版本。每次点击“开始测试”只执行一次，需要重测时再次启动，历史结果保留。每次保存输入哈希、skill 文件快照、提示词及输出格式，后续更新服务 skill 不会改变已排队的测试。每次运行使用新的工作目录和 Codex 会话，与真实工单共用提示词、skill 加载方式以及复现、定位和交付流程，不追加“离线”或限制发布目录查询的测试规则。测试用例与真实工单列表分开，网站不会为测试查询 Jira、创建工单或发布评论，评论重试接口也会拒绝测试运行。

测试启动时直接复制分析服务当前的 `AGENTS.md` 和 `.agents/skills`，保存 UTF-8 文本资源、脚本执行权限和快照哈希，包含尚未提交的部署修改。采集失败时提示重试。Codex 地址、连接凭据和超时沿用所选分析组；模型与推理强度由测试请求指定，地址变化会阻止旧批次继续启动。

每次启动保存当时的可选机器范围；可能使用同一主机的测试逐次执行，仍计入分析组并发限制。真实 GPU 资源独占继续由现有 skill 的锁和环境检查负责，测试队列不等于整台机器无人使用。网站重启会恢复持久化队列和运行中的会话。环境、镜像、驱动等实际信息以运行证据为准；本功能不负责重置机器，也不保证外部镜像标签不变。

测试记录按时间倒序显示在表格中，列出开始时间、模型与推理强度、执行状态和人工评价。表格下方一次只展开一条详情，再次点击可收起；打开用例时默认收起历史记录，点击“展开”后查看；正在执行的测试仍自动显示，带具体运行编号的链接直接打开对应记录。刷新页面保留所选记录或全部收起的状态。“用例内容”展示标题和任务要求，用例描述与原始数据默认折叠，展开后的长内容在窗口内滚动；导出、关闭和修改按钮集中放在底部；历史记录中的“本次测试输入”仍可折叠。“开始测试／再次测试”打开选机与启动配置弹窗。排队、执行和文件回收期间隐藏启动入口。

详情先展示人工评价，再展示分析结论、交付文件和可折叠的 agent 处理过程。查看已结束的历史记录时，处理过程默认收起；执行中默认展开，手动收起后仍继续更新状态和结论。机器、skill 和当时的输入放在“本次配置”中。结论与补丁集中展示，不在处理过程里重复列出；文件回收不完整时保留结论，可单独重试回收。

人工评价支持通过、部分符合、不符合、环境受限、平台故障或已取消，填写时需要说明依据。“已完成”只表示执行结束，是否通过由人工判断。收起或切换记录时保留尚未提交的评价，保存后的更正保留历史记录；执行或文件交付未完成不能标记通过。首版不允许给测试对话追加提示，避免把追问后的结果算作一次完成；需要调整要求时修改并保存输入后再测。

输入和运行记录保存在网站数据库，只有输入和 skill 被写入运行目录。旧版本已保存的验收标准和人工参考仍保留，不发送给模型。目录和会话隔离不等同于容器权限隔离：如需防止执行账号读取其他历史任务，应为测试单独部署 Codex 服务、限制其挂载，并配置为独立分析组。不要把已有答案放到该服务可访问的实验目录中。首版每个用例版本的输入文件总量上限为 20MB。

### 用例包导出与导入

RM 在“用例内容”中点击“导出用例”，下载 ZIP。导出固定使用窗口中对应的已保存输入，包含 `manifest.json`（格式版本、文件清单和 SHA-256）、`case.json`（名称、工单快照、任务要求和来源）、`attachments/` 和 `uploads/` 下的实际文件。已有人工参考单独放在 `review.json`，不会写入模型工作目录。包中不自动收集测试结果、运行对话、skill、账号授权或服务配置。good/bad 镜像地址及运行命令保留在工单内容中，镜像本体仍从仓库拉取。

在用例列表点击“导入用例”，上传 ZIP 后先预览正文、评论及附件，再确认保存。原始 JSON 可以展开编辑，已有人工参考只读展示。上传、预览和取消均不保存用例、不启动任务；保存后沿用当前系统的选机、模型、skill 和测试流程，不需要原 Jira 或原会话可用。原 Jira 编号仅记录来源，新的用例 ID 由目标系统生成。

相同输入会提示并打开已有用例，不增加记录；同名但输入不同需改名保存。保存时再次检查，防止同时导入产生重复记录。首版不通过导入覆盖已有用例，旧输入和历史结果保持不变。重复判断使用当前保存输入的哈希，不因用例名称或人工参考不同而复制相同测试输入。

只接受格式版本 1 的用例包。文件清单、内容校验、路径及附件登记必须一致；拒绝重复路径、软链接、加密包、缺失文件和超限内容。包仅在内存中解析，不解压到服务器目录。输入附件仍限 100 个、总计 20MB；ZIP 及解压后内容总量上限为 41MB，给正文和参考资料留出空间。经过反向代理时，需允许至少 41MB 的请求体。

接口为 `GET /api/jira-agent/testing/cases/{case_id}/versions/{version_id}/package`（下载）和 `POST /api/jira-agent/testing/packages/preview`（请求体为 ZIP 原始字节，返回待保存内容及重复提示）。确认保存复用 `POST /api/jira-agent/testing/cases`，携带 `package_import: true`；所有接口均要求 RM 权限。

### 内置测试用例

仓库在 `app/resources/jira_test_cases/` 保存 Amber 内存增长、ESM3 测试失败和 HPL 单卡性能下降三个用例。每个目录的名称是固定标识，`case.json` 保存名称、工单输入、任务要求和来源，附件放在对应目录中。这里只保存用例内容，不包含测试对话、运行记录、分析结果或人工评价；镜像仍从描述中的 Harbor 地址获取。

`web/` 下执行 `npm run build` 时，会先校验用例并生成 `_packages/`，再构建前端。用例格式与网页导出的 ZIP 一致。部署时连同 `app/` 一起复制这部分后端资源，不放入公开的 `web_dist/`。也可在仓库根单独执行 `python3 tools/build_jira_test_cases.py`；`--output` 可指定临时构建目录。生成的包不提交 Git，源文件变更后需要重新构建。

网站启动时优先加载 `_packages/`，源码开发未生成包时直接校验并加载源目录。首次部署自动创建用例，列表标记“内置”，没有历史测试记录，不会自动启动任务或访问 Jira。新建、修改、导入、导出仍使用原有功能。加载失败时页面给出提示，并保留原数据库内容。

相同输入已存在时沿用原用例及其历史记录；重复启动不增加用例。仓库更新后，网站未修改的用例自动保存新输入，旧测试继续引用原输入。网站已经修改过的内容保留，并提示内置用例有更新，人工核对后再修改。仓库移除用例不会删除网站已有数据。网页保存不会回写仓库；需要作为默认用例发布时，再将核对后的输入更新到源目录。

默认资源目录可用 `JIRA_TESTING_CASES_DIR` 指向其他目录，用于隔离验证或单独挂载用例资源。生成包部署只需在该目录保留 `_packages/`。目录中的额外文件、缺失附件、重复输入和损坏的包会导致校验失败，不会部分覆盖已有用例。

### 产物预览与下载

本轮产物支持 Markdown、代码高亮和纯文本预览，最多显示前 1 MiB；二进制或不支持的编码需下载查看。可下载单个文件，或在产物收齐后打包下载本轮 ZIP。访问权限与原文件下载一致。
