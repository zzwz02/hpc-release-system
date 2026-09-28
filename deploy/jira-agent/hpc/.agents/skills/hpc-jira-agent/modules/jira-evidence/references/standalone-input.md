# 独立 Jira 输入模式（维护参考）

本文件用于维护上游独立运行接口；发布网站部署固定使用 `--source local`，不执行下述 REST 模式。脚本、参数及离线测试仍保留原实现。

## 输入模式

| 模式 | 行为 | 适用场景 |
|---|---|---|
| `auto` | 工作目录存在 `issue.json` 或 `issue.md` 时使用本地材料，否则读取 Jira REST | 独立使用的自动选择入口 |
| `local` | 只读取本地工作目录 | 隔离网络、固定快照或部署系统已完成采集 |
| `jira` | 只读取 Jira REST | 独立运行或需要实时工单内容 |
| `hybrid` | 同时读取两端，以 Jira REST 为当前主数据并核对本地材料 | 恢复会话、检查评论更新或校验部署快照 |

统一输出字段沿用[输入契约](input-acquisition.md#统一输出)。独立调用示例：

```bash
python3 scripts/load_issue.py \
  --issue-id <JIRA-ID> \
  --source auto \
  --bundle-dir <workspace> \
  > <run>/evidence/jira.json
```

以上命令从 `jira-evidence` 模块目录执行；`--source auto` 是独立接口默认行为，网站部署须显式指定 `local`。

## 混合模式规则

混合读取使用：

```bash
python3 scripts/load_issue.py \
  --issue-id <JIRA-ID> \
  --source hybrid \
  --bundle-dir <workspace> \
  > <run>/evidence/jira.json
```

- Jira REST 是工单字段和评论的当前主数据；本地目录提供已下载附件、用户上传和部署快照。
- 两端非空值不同则写入 `acquisition.conflicts`，不自动把差异解释成错误或选择性删除。
- REST 获取失败而本地材料有效时，返回 `success=true`、`completeness=partial`，并记录 `jira_api_unavailable`；需要发布评论或确认最新状态前仍应恢复 REST 访问。
- 本地文件按文件名和大小与 REST 附件匹配。无法匹配的文件保留为 `local_only_attachments`，不冒充 Jira 附件。

## 权限边界

- `local`/`auto` 读取本地材料不需要 Jira token。
- `jira`/`hybrid` 通过现有 REST 脚本读取，凭据只从 `JIRA_TOKEN_FILE` 获取。
- 获得本地快照、附件或 REST 读取权限，不代表获得评论、上传、转派、改状态或创建工单的权限；写操作仍按本次明确授权执行。

底层 REST 操作与写入批准契约见[独立 Jira 工具](../skills/jira-issue-manager/references/standalone-usage.md)。本参考不授予新的网络或写入权限。
