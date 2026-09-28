# 测试脚本调用示例

以下为真实 Jira 集成测试，不属于离线测试。执行前按入口 Skill 的要求取得授权，`JIRA_ISSUE` 使用独立测试工单，`JIRA_ASSIGNEE` 使用已确认的目标用户，`JIRA_ATTACHMENT_URL` 从该测试工单读取；不对业务工单执行写测试。

测试脚本统一通过命令行参数传值，`Jira Personal Access Token` 通过 `--access-token` 传入。

## 获取字段列表

```bash
python3 tests/test_get_fields.py \
  --access-token "YOUR_PERSONAL_ACCESS_TOKEN"
```

## 检索问题

```bash
python3 tests/test_search_issues.py \
  --access-token "YOUR_PERSONAL_ACCESS_TOKEN" \
  --jql "project = CCPM AND summary ~ \"测试\""
```

## 创建问题

```bash
python3 tests/test_create_issue.py \
  --access-token "YOUR_PERSONAL_ACCESS_TOKEN" \
  --project-key CCPM \
  --summary "测试创建问题标题" \
  --description "这是测试创建的问题描述" \
  --issue-type Support
```

说明：当前测试脚本会额外传入一个可读自定义字段名 `Expected ETA`，用于验证创建问题时会自动映射到对应的 `customfield_xxxxx`。

## 接取任务

```bash
python3 tests/test_assign_issue.py \
  --access-token "YOUR_PERSONAL_ACCESS_TOKEN" \
  --issue-id "${JIRA_ISSUE:?设置当前工单编号}" \
  --assignee "${JIRA_ASSIGNEE:?设置已确认的目标用户}"
```

## 更新字段

```bash
python3 tests/test_update_issue.py \
  --access-token "YOUR_PERSONAL_ACCESS_TOKEN" \
  --issue-id "${JIRA_ISSUE:?设置当前工单编号}" \
  --description "这是通过测试脚本更新后的描述"
```

说明：当前测试脚本会额外传入一个可读自定义字段名 `Expected ETA`，用于验证更新问题时会自动映射到对应的 `customfield_xxxxx`。

## 添加评论

```bash
python3 tests/test_add_comment.py \
  --access-token "YOUR_PERSONAL_ACCESS_TOKEN" \
  --issue-id "${JIRA_ISSUE:?设置当前工单编号}" \
  --comment "这是通过测试脚本添加的评论"
```

## 获取评论列表

```bash
python3 tests/test_get_comments.py \
  --access-token "YOUR_PERSONAL_ACCESS_TOKEN" \
  --issue-id "${JIRA_ISSUE:?设置当前工单编号}"
```

## 获取问题详情

```bash
python3 tests/test_get_issue_details.py \
  --access-token "YOUR_PERSONAL_ACCESS_TOKEN" \
  --issue-id "${JIRA_ISSUE:?设置当前工单编号}"
```

## 获取创建元数据

```bash
python3 tests/test_get_create_meta.py \
  --access-token "YOUR_PERSONAL_ACCESS_TOKEN" \
  --project-key MC3 \
  --issue-type Feature
```

## 上传附件

```bash
python3 tests/test_upload_attachment.py \
  --access-token "YOUR_PERSONAL_ACCESS_TOKEN" \
  --issue-id "${JIRA_ISSUE:?设置当前工单编号}" \
  --file "C:/path/to/file.txt"
```

## 下载附件

```bash
python3 tests/test_download_attachment.py \
  --access-token "YOUR_PERSONAL_ACCESS_TOKEN" \
  --issue-id "${JIRA_ISSUE:?设置当前工单编号}" \
  --url "${JIRA_ATTACHMENT_URL:?设置当前工单返回的附件地址}" \
  --output-dir "./downloads"
```
