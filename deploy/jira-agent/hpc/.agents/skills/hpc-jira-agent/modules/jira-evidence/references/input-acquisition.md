# 工单输入与规范化

本部署显式使用 `load_issue.py --source local`，读取网站同步材料并输出统一工单对象。材料刷新由网站完成；独立 REST 模式与维护接口另见[维护参考](standalone-input.md)，不属于当前执行流程。

部署工作目录支持：

```text
<workspace>/
├── issue.json     # 网站结构化快照（优先读取，旧目录可没有）
├── issue.md       # 工单阅读版；旧目录的兼容输入
├── attachments/   # Jira 附件，由部署端准备
└── uploads/       # 本次任务的用户补充文件
```

`issue.md` 的首个一级标题必须包含当前 Jira ID。本地文件仅接受工作目录内的普通文件；符号链接、越界路径和无法核对的附件记录为部分获取或冲突，不作为可信输入。

## 网站 JSON 快照

本地模式优先读取 `issue.json`，仅在它不存在时解析旧 `issue.md`。JSON 文件的格式为 `schema_version: 1`、`issue_markdown_sha256` 和 `issue` 三个字段；`issue` 含工单身份、原文、自定义字段、字段定义、评论、附件及 `acquisition`。自定义字段以 `customfield_*` ID 为键，保留 `name`、`value`、`raw`、`schema`，空值与空数组不丢弃；评论保留 Jira ID、作者、原文、更新时间及可见性信息。

`acquisition.parts` 包含 `issue`、`description`、`custom_fields`、`field_definitions`、`comments`、`attachment_metadata`；评论记录 `collected`、`reported_total`、页信息和失败原因。`complete` 仅表示当前账号可见范围的采集覆盖，不表示跨请求原子快照或问题信息充分。字段定义失败时保留字段 ID 与值；分页失败或达到预算时保留已获取评论并标记 partial/unavailable。本地加载不能把 partial 升级为 complete。

若 `issue.md` 同时存在，加载器校验其 SHA-256 与 JSON 记录一致。JSON 损坏、版本未知、工单身份不符、覆盖统计无效或两文件不一致时返回明确错误，不偷偷回退旧 Markdown 或 REST。由网站重新同步这一轮材料；不自行删除 JSON 以掩盖失败。仅有 JSON 时仍可本地读取。

附件元数据保留下载失败记录；仅使用通过本地路径、普通文件及大小检查的文件。`uploads/` 继续实时扫描；未列在本轮 Jira 附件清单中的本地文件保留在 `local_only_attachments`，不冒充本轮 Jira 附件。

## 本地 Markdown 边界与完整性

本地快照按“描述 → 附件 → 评论”排列，支持上述中文标题及 `Description`、`Attachments`、`Comments`。正文中的其他二级、三级标题保留为内容；Markdown 的反引号/波浪号代码围栏以及 Jira 的 `{code}`、`{code:语言}`、`{noformat}` 内部标题不参与栏目或评论边界识别。每条评论仍以代码块外的 `### 作者 @ 时间` 开始。

旧快照没有独立字段边界，正文中与栏目同名的标题可能无法可靠区分。栏目重复、顺序异常、代码块未闭合或有无法归入评论的文字时，解析结果为 `partial`，`acquisition.conflicts` 说明原因，`source_documents[].text` 保留完整原文。遇到这些情况先读取原文核对，不把部分解析字段当作完整内容。缺少栏目时也保留原文并将缺少的部分标为 `unavailable`。

该完整性表示当前本地快照的解析覆盖，不证明网站已经采集了 Jira 的所有字段或所有分页评论。

## 统一输出

统一入口：

```bash
python3 scripts/load_issue.py \
  --issue-id <JIRA-ID> \
  --source local \
  --bundle-dir <workspace> \
  > <run>/evidence/jira.json
```

输出顶层字段为：

- `success`：是否至少获得可识别的工单材料。
- `completeness`：`complete` 或 `partial`，表示本次获取覆盖情况，不表示问题信息已经充分。
- `data`：统一工单对象。

`data` 提供以下稳定字段：

- 身份与元数据：`key`、`summary`、`issue_type`、`status`、`priority`、`project`、`components`、`labels`、`assignee`、`reporter`、`created`、`updated`。
- 内容：`description`、`comments`、`custom_fields`、`raw_fields`、`field_definitions`。
- 文件：`attachments`、`uploads`、`source_documents`。本地可用文件带 `local_path`、`size`、`sha256` 或 `content_check`；REST 附件保留下载 URL。
- 获取记录：`acquisition.source`、`acquisition.status`、`acquisition.parts`、`acquisition.conflicts`。

下游不得通过输入来源推断字段缺失。先查看 `acquisition.parts` 区分“没有该内容”“未采集到”和“读取失败”。
