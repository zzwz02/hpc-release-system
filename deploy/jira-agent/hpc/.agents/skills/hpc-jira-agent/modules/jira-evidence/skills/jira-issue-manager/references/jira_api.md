# Jira 接口参考

请求示例中的 `<TARGET_USER>` 为参数占位符，执行前替换为已确认的目标用户；字段值以目标项目的当前元数据为准。

`{issue_id}` 代表当前任务已确认的实际问题编号。issue_id 为字符串，常见格式：`C500-xxx`、`C600-xxx`、`CCPM-xxx`、`PAE-xxx`、`MC3-xxx`。

## 认证准备
- 使用这些接口前，需由用户提供 Jira 的 `Personal Access Token`。
- `Personal Access Token` 的申请方式：进入 Jira 系统个人 `profile` 页面，在 `Personal Access Tokens` 选项下选择 `create token`。

## 创建问题
- 接口：`http://10.2.201.98:8080/rest/api/2/issue`
- 方法：`POST`
- 认证：`Bearer Token`
- Content-Type：`application/json`
- 返回结构统一为 `success + data`
- 说明：创建问题时，`fields` 中的自定义字段既可以直接传 `customfield_xxxxx`，也可以传 Jira 字段列表中的可读字段名；脚本会先查询 `/rest/api/2/field` 并自动映射为对应字段 ID。
- 创建前会自动查询创建元数据，预检当前项目和问题类型下缺失的必填字段；对 Jira 已有默认值的必填字段不会重复拦截。
- body 示例：
```json
{
  "fields": {
    "project": { "key": "CCPM" },
    "summary": "测试问题标题",
    "description": "问题描述内容",
    "issuetype": { "name": "Support" },
    "customfield_10500": ["测试Customer"],
    "customfield_10115": "2026-03-30",
    "customfield_10501": { "id": "11227" },
    "customfield_10600": "123",
    "customfield_10502": { "id": "11110" }
  }
}
```

- 使用可读字段名的示例：
```json
{
  "fields": {
    "project": { "key": "CCPM" },
    "summary": "测试问题标题",
    "description": "问题描述内容",
    "issuetype": { "name": "Support" },
    "Expected ETA": "2026-03-30"
  }
}
```

## 搜索问题
- 接口：`http://10.2.201.98:8080/rest/api/2/search`
- 方法：`GET`
- 认证：`Bearer Token`
- 常用查询参数示例：
  - `jql=project = CCPM AND summary ~ "测试" AND status = "Open"`
  - `startAt=0`
- 完整请求示例：`http://10.2.201.98:8080/rest/api/2/search?jql=project = CCPM AND summary ~ "测试" AND status = "Open"&startAt=0`
- 默认返回精简字段：`key`、`summary`、`status`、`assignee`、`priority`、`created`、`description`
- 返回结构统一为 `success + data`

## 接取任务（分配）
- 接口：`http://10.2.201.98:8080/rest/api/2/issue/{issue_id}`
- 方法：`PUT`
- 认证：`Bearer Token`
- Content-Type：`application/json`
- 说明：
  - 该操作本质上是更新 issue 的 `assignee` 字段。
  - “接取任务”通常表示把问题分配给当前处理人。
  - “分配任务”通常表示把问题分配给指定用户。
  - `{issue_id}` 为当前任务已确认的实际问题编号。
- body 示例：
```json
{
  "fields": {
    "assignee": { "name": "<TARGET_USER>" }
  }
}
```

## 修改字段
- 接口：`http://10.2.201.98:8080/rest/api/2/issue/{issue_id}`
- 方法：`PUT`
- 认证：`Bearer Token`
- Content-Type：`application/json`
- 返回结构统一为 `success + data`
- 说明：更新问题时，`fields` 中的自定义字段既可以直接传 `customfield_xxxxx`，也可以传 Jira 字段列表中的可读字段名；脚本会先查询 `/rest/api/2/field` 并自动映射为对应字段 ID。
- body 示例：
```json
{
  "fields": {
    "description": "新xxx描述"
  }
}
```

- 使用可读字段名的示例：
```json
{
  "fields": {
    "description": "新xxx描述",
    "Expected ETA": "2026-03-30"
  }
}
```

## 添加评论
- 接口：`http://10.2.201.98:8080/rest/api/2/issue/{issue_id}/comment`
- 方法：`POST`
- 认证：`Bearer Token`
- Content-Type：`application/json`
- 返回结构统一为 `success + data`
- body 示例：
```json
{
  "body": "这是评论内容 - From API"
}
```

## 获取评论列表
- 接口：`http://10.2.201.98:8080/rest/api/2/issue/{issue_id}/comment`
- 方法：`GET`
- 认证：`Bearer Token`
- 返回结构统一为 `success + data`

## 获取问题详情
- 接口：`http://10.2.201.98:8080/rest/api/2/issue/{issue_id}`
- 方法：`GET`
- 认证：`Bearer Token`
- 说明：
  - 结合 `/rest/api/2/field` 做字段映射，将自定义字段转换为可读名称。
  - 默认补充评论列表与附件列表。
  - 附件列表中的 `url` 对应 Jira 原始附件对象里的 `content`。
- 返回结构统一为 `success + data`

## 自定义字段清洗规则

- 默认先通过 `/rest/api/2/field` 建立字段 ID 到字段名的映射，再输出自定义字段。
- `option` 类型优先提取可读值，例如返回对象中的 `value`。
- 用户类型优先提取显示名，例如 `displayName`。
- 数组类型会逐项做归一化，并过滤空值。
- 嵌套对象会尽量压缩为可读内容；无法安全压缩时保留必要结构。
- 空壳值会被过滤，例如空对象、空数组、空字符串、`"{}"`、`"[]"`。

## 噪声字段过滤

- 当前默认过滤的字段名包括：`Rank`、`Development`。
- 这些字段通常不适合作为业务输出，容易引入噪声或无意义内容。

## 获取字段列表（含自定义字段映射名）
- 接口：`http://10.2.201.98:8080/rest/api/2/field`
- 方法：`GET`
- 认证：`Bearer Token`
- 默认返回字段映射摘要：字段 ID、字段名、是否为自定义字段、字段类型
- 返回结构统一为 `success + data`
- 如需原始字段列表，可使用 `--raw`

## 获取创建元数据
- 接口：`http://10.2.201.98:8080/rest/api/2/issue/createmeta`
- 方法：`GET`
- 认证：`Bearer Token`
- 常用查询参数示例：
  - `projectKeys=MC3`
  - `issuetypeNames=Feature`
  - `expand=projects.issuetypes.fields`
- 说明：
  - 用于查询指定项目和问题类型下创建 issue 时可填写的字段信息。
  - 返回结果适合用来确认必填字段、字段类型和可选值。
  - 默认会对 `allowedValues` 做摘要输出，仅保留数量和部分示例；如需完整原始创建元数据，可使用 `--raw`。
  - 返回结构统一为 `success + data`
  - 如需原始创建元数据，可使用 `--raw`

## 上传附件
- 接口：`http://10.2.201.98:8080/rest/api/2/issue/{issue_id}/attachments`
- 方法：`POST`
- 认证：`Bearer Token`
- 返回结构统一为 `success + data`
- 额外头：`X-Atlassian-Token: no-check`
- Content-Type：`multipart/form-data`
- 说明：以 `file` 表单字段上传二进制文件。

## 下载附件
- 输入：问题详情接口返回的附件 `url`
- 方法：`GET`
- 认证：`Bearer Token`
- 返回结构统一为 `success + data`
- 说明：
  - 直接使用附件 `url` 下载文件内容。
  - `url` 对应 Jira 原始附件对象中的 `content`。
  - 下载时在输出目录下按 `issue_id` 创建子目录，便于归档和检索。
