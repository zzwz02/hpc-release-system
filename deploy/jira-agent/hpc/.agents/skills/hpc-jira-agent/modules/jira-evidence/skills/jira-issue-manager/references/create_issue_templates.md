# 常见创建模板

以下模板用于帮助快速组织 `create_issue.py` 所需的 `fields-json`。

注意：
- 这些模板基于当前样例整理，只适合作为起点参考。
- 不同 `project`、不同 `issue type` 的必填字段可能不同。
- 实际创建前，仍应优先使用 `scripts/get_create_meta.py` 查询当前项目和问题类型下的创建元数据。
- 自定义字段既可以直接传字段 ID，也可以传可读字段名；脚本会自动尝试映射为对应字段 ID。

## 通用基础模板

适用于大多数普通 issue 的基础结构：

```json
{
  "project": { "key": "MC3" },
  "summary": "问题标题",
  "description": "问题描述",
  "issuetype": { "name": "Feature" }
}
```

常见字段说明：
- `project`：项目键。
- `summary`：问题标题。
- `description`：问题描述。
- `issuetype`：问题类型，例如 `Bug`、`Task`、`Feature`、`Requirement`。

## Feature 模板

适合作为常见 `issue type` 为 `Feature` 的创建模板：

```json
{
  "project": { "key": "MC3" },
  "summary": "测试创建问题标题",
  "description": "这是测试创建的问题描述",
  "issuetype": { "name": "Feature" },
  "components": [
    { "name": "PDE_AI" }
  ],
  "assignee": { "name": "gyyan" },
  "versions": [
    { "name": "SW_SDK_3.6.0.16" }
  ],
  "Expected ETA": "2026-03-30"
}
```

字段说明：
- `components`：对应 Jira 中的 `Component/s`，通常是数组。
- `assignee`：负责人，通常使用对象形式。
- `versions`：对应 Jira 中的 `Affects Version/s`，通常是数组。
- `Expected ETA`：示例中的可读自定义字段名，脚本会自动映射到对应字段 ID。

## Bug 模板

适合作为常见 `issue type` 为 `Bug` 的创建模板：

常见必填字段包括：
- `Issue Type`
- `Summary`
- `Priority`
- `Component/s`
- `Affects Version/s`
- `Project`

可参考如下模板：

```json
{
  "project": { "key": "MC3" },
  "summary": "Bug 标题",
  "description": "Bug 描述",
  "issuetype": { "name": "Bug" },
  "priority": { "name": "High" },
  "components": [
    { "name": "PDE_AI" }
  ],
  "versions": [
    { "name": "SW_SDK_3.6.0.16" }
  ],
  "Severity": { "value": "Major" }
}
```

说明：
- `priority` 在部分项目中虽然必填，但可能有默认值，是否必须显式传入以实际创建元数据为准。
- `Severity` 在当前 `MC3 + Bug` 的创建元数据中出现，但是否必填仍需以实时查询结果为准。
- 若 Jira 更偏向按 `id` 提交选项字段，也可以把对象改成 `{ "id": "..." }`。

## Task 模板

普通任务可从基础模板开始，再按项目要求补充字段：

```json
{
  "project": { "key": "MC3" },
  "summary": "任务标题",
  "description": "任务描述",
  "issuetype": { "name": "Task" },
  "components": [
    { "name": "PDE_AI" }
  ]
}
```

## 使用建议

- 创建前先确定 `project` 和 `issuetype`，再查询创建元数据。
- 若创建失败，优先检查：
  - 必填字段是否缺失
  - 字段类型是否正确
  - `components`、`versions` 这类字段是否使用了数组结构
  - 选项字段是否应传 `name`、`value` 或 `id`
- 若用户只提供自然语言需求，建议先根据场景判断更像 `Bug`、`Task`、`Feature` 还是 `Requirement`，再结合创建元数据生成最终模板。
