# HPC 发布系统接入

仅在访问本发布系统时读取。入口：<https://hpc-release.swlab.metax-tech.com/apps>。

本文件为网站部署模式的接入说明，遵守项目根 `AGENTS.md` 的资源服务范围。工单与账号信息以网站同步材料为准；不直接调用 Jira 用户、组件或关联工单接口。发布目录和 Gerrit 的只读服务访问不改变网站指定的实验执行机。

## 访客登录

用户明确允许在本 Skill 保存该站点的共享访客账号：用户名 `guest`，密码 `guest`，选择**内建账号**，不是 LDAP。仅用于 `https://hpc-release.swlab.metax-tech.com` 的访客读取，不尝试登录 Jira、Harbor、SSH 或其他域名。

账号失效时报告接入缺口，不尝试其他账户。会话 Cookie 只在本次受限会话使用，不写入 Skill/Git/Jira；不要输出登录响应头或完整账号资料。正常校验 HTTPS，不默认跳过证书验证；当前主机不可达时，可使用本次已授权且信任配置正常的网络环境，不能从本文件推断任意 SSH 主机授权。

## 最短查询路径

以下请求于 2026-09-21 核验，后续需兼容站点变化。使用已有 HTTP 工具保持会话，无需整站浏览或新增 MCP。

本部署访问此目录时，单独绕过模型出网代理并直连该站点。例如 curl 使用 `--noproxy hpc-release.swlab.metax-tech.com`；Python 使用仅访问本站的专用 opener，配置 `urllib.request.ProxyHandler({})` 并保留 Cookie 与同源重定向检查。不要因此清除模型进程的全局代理，也不使用未授权跳板机。

1. `POST /api/login`，JSON 为 `{"username":"guest","password":"guest"}`，保持 Cookie。
2. `GET /api/state`，读取当前批次、可见批次及应用记录；使用 `Cache-Control: no-cache` 等方式请求重新校验，避免仅使用浏览器缓存。
3. 仅当 Jira 指定其他批次或当前批次不足以匹配时，从响应的 `releases` 解析真实 ID，再 `GET /api/state?release_id=<URL 编码后的真实 ID>`；核对返回的 `release.id/name`，不能只因 HTTP 200 就认定取到了目标版本。
4. 关联记录并仅输出下表字段。当前接口返回整批数据，摘要在本地提取；未验证的单应用或字段过滤参数（如 `?app=`、`?fields=`）不可使用。
5. 查询完成后 `POST /api/logout`（空 JSON 对象），丢弃会话；不要输出完整 `/api/state` 正文到终端或 Jira。

不随跨域重定向发送凭据/Cookie。401/403、登录页 HTML、超时或结构变化返回不可用；必要时用 `/api/me` 核验当前登录身份。若接口变化但浏览器可用，在页面重新选择目标发布批次、清除仅看本人/状态/关键词等筛选，用相同字段查询；不自动修改发布条目，也不无限重试。

## 字段与来源

把 `apps[]` 的 `id` 与 `release.snapshots[应用 ID]` 关联，只提取当前工单所需记录。`apps[]` 是本次返回的基础信息，切换历史批次不代表仓库/分支也是历史冻结值；历史 revision 仍需结合工单、镜像、制品清单或对应发布资料核对。

| 信息 | 字段及解释 |
|---|---|
| 查询批次 | `release.id`、`release.name`；可选批次来自 `releases`。请求历史 ID 后必须核对返回批次。 |
| 应用身份 | `apps[].id`、`apps[].aliases`、snapshot 的 `official_name`。 |
| 应用版本 | snapshot 的 `version`，与发布批次名、`maca_version` 分别核对。 |
| 仓库/分支 | `apps[].git_url`、`apps[].git_branch`；保留原值，可能是 repo manifest 路径而非完整 Git URL。当前默认分支不证明历史源码身份。 |
| 发布维护人 | snapshot 的 `owners`，提供应用维护或协调线索，不自动等于 compiler、UMD、数学库或通信库的缺陷负责人；`created_by` 不是维护人。 |
| 发布流程状态 | `owner_confirmed` 表示该快照是否完成 Owner 确认；`owner_added` 表示相关测试项来源。它们不核验人员身份，QA 状态与 `release_decision` 也不用于排除本组范围。`stopped` 仅表示条目发布决策，支持边界需结合所报版本及维护依据确认；未知枚举保留原值。 |

资源准备时再按需读取匹配 snapshot 的 `doc`、`test_docs`、`maca_version` 及构建/硬件条件，核对实际类型；初判只保留摘要，文档命令不构成执行授权。

## 应用范围判定

按[主流程](../../../SKILL.md#确认应用范围)使用本节：先以名称/别名及仓库或源码身份筛选，再核对版本和批次。确认应用范围不要求登录源码服务。

| 查询结果 | 记录与下一步 |
|---|---|
| 应用身份唯一，所报版本及指定批次匹配 | 记录已确认的 HPC 应用范围，交回主流程做故障技术归因。 |
| 应用已收录，但版本未匹配或目标批次无 snapshot | 保留基础记录，分别写“应用已收录”和“所报版本范围待确认”；按工单线索核对历史批次，不以当前版本替代。 |
| 相近名称或多个版本/仓库候选 | 用别名、源码身份、版本或发布资料区分，不取第一条。历史核验中 LAMMPS 曾有 `patch_4May2022` 与 `stable_22Jul2025` 两条记录，此例不作为当前查询缓存。 |
| 工单未给出版本 | 可以确认已匹配的应用身份，版本标记未知，不写成版本已匹配。 |
| 已查批次内未找到 | 核对别名和相关历史批次，记录“截至本次查询，在已查可见批次内未找到”，保留范围待确认。访客目录可能缺少历史或受限记录，不据此断言系统从未包含。 |
| 登录、网络或结构异常 | 记录失败原因，不声称查询已完成；复用已核验记录或其他明确维护依据，继续不受影响的诊断。 |

网站材料中的明确维护记录或既有交接证据可以补充目录，需记录来源及冲突；新应用、示例或未收录版本按实际维护证据确认，不仅凭目录缺项排除。目录记录与故障责任的关系按主流程判断。

## 负责人线索与留证

目录的 `owners` 不证明 Jira 账号有效。需要由应用 owner 接手时，仅使用网站材料中已核验的适用账号，注明发布维护或协调职责；缺少依据时写“BLAS 维护者”“MCCL 维护者”“对应编译器维护者”等具体角色，由网站或人工补齐账号。网站已同步的关联工单和组件负责人也可作为线索；未同步部分按缺口处理，不直接查询 Jira。主受理或协调人不自动等于根因负责人。

在当前 `STATUS.md` / `evidence/` 记录查询地址与时间、已查批次 ID/名称、匹配应用 ID/名称/版本、仓库与分支、维护人及发布状态、匹配依据、冲突或缺口和下一步。只保存本工单的提取结果，不留 Cookie、完整账号资料或全站清单；报告与结构化结论按[交付规范](../../jira-evidence/references/result-delivery.md)概述依据。记录复用及重新查询的时机由主流程决定。

## Gerrit 仓库核验

当发布记录的 `git_url` 指向 HPC 内部仓库时，默认 Gerrit 入口为 `gerrit.sh.metax-internal.com:29418`、用户为 `xshi`，使用运行环境已有的已授权 SSH key。该身份沿用本部署已授权配置，仅用于当前任务所需 `PDE/HPC/` 仓库的服务读取，不保存私钥或明文下载令牌；认证失败时报告凭据或权限缺口，不换用其他账号。

`git_url` 若是完整地址则保留原值；若仅为 `hpc_*` 等相对仓库名，将 `PDE/HPC/<git_url>` 作为候选路径，并通过 Gerrit 项目列表和 `git ls-remote` 核验，不能仅凭名称直接认定：

```bash
ssh -p 29418 xshi@gerrit.sh.metax-internal.com gerrit ls-projects -p PDE/HPC/
git ls-remote ssh://xshi@gerrit.sh.metax-internal.com:29418/PDE/HPC/<repo>.git <ref>
```

上述 SSH 用于 Gerrit 服务查询，Git 传输仅用于 ls-remote、clone/fetch；不登录通用 shell、不在服务主机执行构建/实验，也不把该服务主机写入结论的 `machine` 字段。

记录最终仓库路径、ref 和解析到的 commit。发布页当前 `git_branch` 只用于寻找候选 ref；历史 revision 仍需结合 Jira、镜像、制品清单或发布资料确认。源码下载和工作目录约束继续使用 [git-clone](../skills/git-clone/SKILL.md)，不得由查询动作扩大为提交或推送权限。
