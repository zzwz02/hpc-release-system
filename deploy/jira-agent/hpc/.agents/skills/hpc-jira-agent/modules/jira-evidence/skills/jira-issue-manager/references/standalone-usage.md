# 独立 Jira REST 工具（维护参考）

仅供上游独立集成和维护使用。发布网站部署由网站统一访问 Jira，不执行这里的读写命令。下面的脚本路径均相对于 `jira-issue-manager` 目录；上层 `comment.py` / `artifact.py` 指 `jira-evidence/scripts/` 中的独立发布工具。

## 1. 核对配置与目标

读取[接入契约](project-integration.md)。确认当前工单、`JIRA_BASE_URL`、`JIRA_ALLOWED_ORIGIN` 和已有 `JIRA_TOKEN_FILE`；不在命令、文档或日志中放 token。

## 2. 选择接口

从本 Skill 目录执行对应脚本，参数先查 `--help`：

| 操作 | `scripts/` 下的脚本 | 使用条件与参考 |
|---|---|---|
| JQL 检索 | `search_issues.py` | [JQL 语法](jql_guide.md) |
| 工单详情 / 评论 | `get_issue_details.py` / `get_comments.py` | 保留原文、字段 ID 和分页；检查[读取完整性](acquisition.md) |
| 字段 / 创建元数据 | `get_fields.py` / `get_create_meta.py` | 创建或更新前核对必填项、类型和可选值 |
| 附件下载 | `download_attachment.py` | 使用当前工单的真实 URL；[下载规则与失败诊断](attachments.md) |
| 创建 / 更新字段或负责人 | `create_issue.py` / `update_issue.py` | 当前状态、精确目标与载荷批准；[类型](issue_types.md)、[创建模板](create_issue_templates.md) |
| 底层评论 / 上传 | `add_comment.py` / `upload_attachment.py` | 保留的通用接口；独立模式的日常发布使用上层 `comment.py` / `artifact.py` |

```bash
python3 scripts/get_issue_details.py --issue-id <JIRA-ID>
python3 scripts/get_comments.py --issue-id <JIRA-ID>
python3 scripts/download_attachment.py --issue-id <JIRA-ID> --url <工单附件URL> --output-dir <证据目录>
```

## 3. 检查结果

- 脚本返回 `success` 及 `data` 或 `error`；读取完整性另看 `completeness` 和 `data.acquisition.parts`。未读到不等于没有。
- 附件元数据、文件下载和内容解读分别记录。下载器核对工单归属、同源、大小与内容，失败保留回执，不更换认证方式盲试。
- 通用写入需要外部提供匹配的 `JIRA_WRITE_APPROVAL_FILE`，规则见接入契约；上层评论/附件授权不代替该批准。写后回读，POST 结果不确定时先对账，不盲目重试。

API 字段细节见 [jira_api.md](jira_api.md)。真实 Jira 集成测试默认不执行；只有隔离测试项目及对应授权齐全时才按[测试说明](test_scripts.md)运行。离线测试见工程 README。
