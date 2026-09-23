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
     - **自填 `user@host`**：不加入系统机器列表。先点 **上传 SSH 公钥**，确认风险提示后在弹出的终端里输入该账号密码：网站经 app-server 在 **B 上** 运行 `ssh-copy-id`，把 B 执行账号的公钥追加到对方的 `~/.ssh/authorized_keys`，再从 B 用 BatchMode 测试免密登录，通过后才能交单。密码只转发给 ssh-copy-id，不保存、不记录。上传后 agent 可以免密登录该账号并执行命令，公钥不会随对话结束失效（删掉 authorized_keys 里注释为 B 公钥注释的那一行才能撤销），所以**必须使用专用测试账号，不要用个人账号**。
   - 已有该 assignee 的进行中对话时，直接显示这个对话；历史对话在标题旁的下拉菜单里切换，只读查看。
   - 点 **新建对话** 先进入交单草稿，不会立即交单；点 **交给 agent** 并确认后，当前对话结束，新建对话。
   - JIRA 评论中的对话链接会自动打开对应工单和对话。

   搜索框规则：
   - 留空：默认列表。普通用户为自己名下未关闭的工单（`assignee = <本人> AND status != Closed`），RM 为本组 JIRA 用户组成员的未关闭工单（`assignee in membersOf(<JIRA_MEMBERS_GROUP>)`）。
   - 填一个 JIRA 编号（`项目KEY-数字`）：只显示这张工单，找不到时单独提示。一次只能填一个编号；要查多张请用 JQL（如 `key in (MC3-1, MC3-2)`）。不识别工单网址。
   - 其他内容：作为 JQL 查询，JQL 写错时显示 JIRA 返回的错误。

   RM 在列表标题旁可以切到 **agent 处理过**：列出所有交给过 agent 的工单（含 JIRA 已关闭的），按 agent 最近活动排序，显示当前 JIRA 状态、最近一次对话的结论和对话数，可按编号、标题、状态或人员过滤。点击打开该工单最近的对话。
2. 网站按排队顺序执行：同步 issue.md、JIRA 附件和上传文件到 B 的工作目录，新建 Codex thread，按 AGENTS.md 与 skills 完成分类、归属判断、复现、分析和修复。
3. 页面时间线实时显示 agent 消息、执行的命令（含退出码和输出）、文件修改和结构化结论。
4. 本轮结束后，网站在 JIRA **只追加评论**：结论、证据、补丁和下一步建议。agent 不修改 assignee、状态或代码。
   - 交单或发消息时取消勾选 **本轮结论发布到 JIRA 评论**，这一轮结论只在网页显示，不发评论（用于只想了解详情、让 agent 继续分析的场景）。选择按轮次生效：运行中或排队中再发消息，以这一轮最后一次发送的选择为准；下一轮重新选择。
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
  只有 B 的 app-server 也重启过，或重启时连不上 B，才标记为“已中断”，发送消息可以继续。续办时如果 B 上还有未结束的上一轮，网站会先中断它。
- 网站刚重启、正在重新接管某一轮时（通常几秒，连不上 B 时最多十几秒），这个对话的“取消本轮”“发送”“新建对话”暂时不可用，页面会提示，接管完成后恢复。已关闭对话的轮次不会被重新执行。
- 中断（取消、超时）只结束 Codex 本轮，已经启动的 shell 命令会继续跑完（Codex 0.153 实测），强制停止 B 的 app-server 时子进程也会留下。
- GPU 等机器资源由 B 上知识包约定的 `flock` 锁控制（见 AGENTS.md）。

## 部署服务器 B（HPC agent）

以下以 hpc 执行用户在 B 上操作为例（MVP 阶段 A、B、C 同为 10.2.118.75，使用 zhawu 账号）。

1. **Codex 登录**：`codex login`，生成 `~/.codex/auth.json`。凭证只存在于 B。
2. **SSH 访问 C/D/E**：为 hpc 用户生成密钥（`ssh-keygen -t ed25519 -C hpc-jira-agent@<B>`），同目录必须有 `.pub`（只有私钥时用 `ssh-keygen -y -f <私钥> > <私钥>.pub` 补出），并把私钥路径写进 A 的 `SSH_KEY_PATH`。系统机器列表中的机器由 RM 提前配好免密（`ssh -o BatchMode=yes <user@host> true` 能通过），再在页面 **系统机器** 中登记。自填机器用的 `ssh-copy-id` 在 B 上运行，B 需要安装它（openssh-client 自带）。
3. **同步知识包**（在仓库检出目录执行）：
   ```bash
   PACK_DIR=$HOME/hpc-jira-agent deploy/jira-agent/sync-pack.sh
   ```
   知识包目录会被初始化为 git 仓库，Codex 以它为项目根，自动加载 AGENTS.md 和 `.agents/skills`。修改知识或 skills 时，改仓库中的 `deploy/jira-agent/hpc/`，评审后重新同步。
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

1. 在 `release_system.conf`（模板 `release_system.conf.example`，已 gitignore）中为每个组写一节 `[jira_agent:<组名>]`：

   | 键 | 说明 |
   | --- | --- |
   | `CODEX_WS_URL` | B 的地址，如 `ws://10.2.x.x:4510` |
   | `CODEX_WS_TOKEN` | 与 B 上 `ws-token` 文件内容一致 |
   | `WORKSPACE_ROOT` | B 上工作目录根的绝对路径，如 `/home/hpc-agent/hpc-jira-agent/workspaces` |
   | `COMPONENTS` | 该组负责的 JIRA component，用于选择数字员工 |
   | `JIRA_MEMBERS_GROUP` | 该组的 JIRA 用户组（如 `pde_hpc`），RM 的默认工单列表按 `membersOf` 查询 |
   | `SSH_KEY_PATH` | B 上执行账号的 SSH 私钥绝对路径，同目录须有 `.pub`；自填机器上传的就是这把公钥 |
   | `MAX_CONCURRENT`、`TURN_TIMEOUT_SECONDS` | 并发上限和每轮限时 |

2. 同一文件的 `[jira]` 节提供 JIRA 地址和 token，用于读取工单、下载附件、发布评论。
3. 可选：在同一文件的 `[site]` 中填写 `PUBLIC_BASE_URL`（网站对外地址），JIRA 评论会附对话链接。
4. 可选环境变量：
   - `JIRA_AGENT_DATABASE_URL`：默认 `sqlite:///jira_agent_tasks.db`。
   - `JIRA_AGENT_DATA_DIR`：上传文件和拉回产物的存放目录，默认 `jira_agent_data/`。
5. 网站保持单 worker 运行（队列 runner 在进程内）。A 到 B 若经过 HTTP 代理，把 B 的地址加入 `NO_PROXY`。
6. 打开 **JIRA agent** 页，或调用 `GET /api/jira-agent/health` 确认连通。

## 把 B 迁移到独立服务器

只需要换启动用户和地址、token，以及 A 的配置：

1. 在新 B 上创建 hpc 用户，完成上面“部署服务器 B”的 1–5 步（Codex 登录、SSH 凭证、同步知识包、生成 token、启动）。
2. 修改 A 的 `release_system.conf` 中该组的 `[jira_agent:<组名>]`：`CODEX_WS_URL` 改为新 B 地址，`CODEX_WS_TOKEN` 改为新 token，`WORKSPACE_ROOT` 改为新用户的目录。
3. 必要时把新 B 的地址加入 A 的 `NO_PROXY`（改了 `NO_PROXY` 需重启网站；配置文件本身保存即生效）。
4. 已有对话的 Codex thread 和工作目录保存在旧 B 上，迁移后需要为这些工单新建对话。
