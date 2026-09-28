# JQL 构造参考

JQL（Jira Query Language）用于检索 Jira 中的问题。
本文件用于帮助快速构造常见查询语句，适合配合 `scripts/search_issues.py` 使用。

注意：
- 不同 Jira 实例、项目和字段配置可能不同，部分字段名或字段值需要以实际系统为准。
- 复杂查询建议先写最小条件，再逐步增加 `AND`、`OR`、排序和时间过滤。
- 自定义字段是否可直接用于 JQL，需要以 Jira 实际配置和字段搜索能力为准。

## 基本结构

最基础的 JQL 由“字段 + 运算符 + 值”组成：

```jql
project = MC3
```

多个条件可以组合：

```jql
project = MC3 AND status = "Open"
```

也可以排序：

```jql
project = MC3 ORDER BY updated DESC
```

## 常用字段

- `project`：项目键，例如 `MC3`
- `summary`：标题
- `description`：描述
- `status`：状态
- `issuetype`：问题类型
- `assignee`：负责人
- `reporter`：报告人
- `priority`：优先级
- `labels`：标签
- `created`：创建时间
- `updated`：更新时间
- `resolution`：解决结果

## 常用运算符

- `=`：等于
- `!=`：不等于
- `~`：模糊匹配，常用于文本字段
- `!~`：不匹配
- `IN (...)`：在集合中
- `NOT IN (...)`：不在集合中
- `IS EMPTY`：为空
- `IS NOT EMPTY`：不为空
- `>=`、`<=`、`>`、`<`：时间或数值比较
- `ORDER BY`：排序

## 常见写法示例

### 按项目检索

```jql
project = MC3
```

### 按标题模糊匹配

```jql
project = MC3 AND summary ~ "测试"
```

### 按状态检索

```jql
project = MC3 AND status = "Open"
```

### 按问题类型检索

```jql
project = MC3 AND issuetype = "Bug"
```

### 按负责人检索

```jql
project = MC3 AND assignee = "m01052"
```

当前用户也可以写成：

```jql
assignee = currentUser()
```

### 按优先级检索

```jql
project = MC3 AND priority IN ("High", "Immediate")
```

### 按标签检索

```jql
project = MC3 AND labels = ai
```

多个标签：

```jql
project = MC3 AND labels IN (ai, release)
```

### 查询最近更新的问题

```jql
project = MC3 AND updated >= -7d
```

### 查询最近创建的问题

```jql
project = MC3 AND created >= -30d
```

### 查询未分配问题

```jql
project = MC3 AND assignee IS EMPTY
```

### 查询多个状态

```jql
project = MC3 AND status IN ("Open", "In Progress", "Reopened")
```

### 增加排序

```jql
project = MC3 AND status = "Open" ORDER BY updated DESC
```

## 常见检索模板

### 模板 1：某项目下标题包含关键词的问题

```jql
project = MC3 AND summary ~ "推理"
```

### 模板 2：当前用户待处理的问题

```jql
assignee = currentUser() AND status IN ("Open", "In Progress") ORDER BY priority DESC, updated DESC
```

### 模板 3：某项目下最近 7 天更新的 Bug

```jql
project = MC3 AND issuetype = "Bug" AND updated >= -7d ORDER BY updated DESC
```

### 模板 4：某负责人负责的高优先级问题

```jql
project = MC3 AND assignee = "m01052" AND priority IN ("High", "Immediate")
```

### 模板 5：某状态下未补充标签的问题

```jql
project = MC3 AND status = "Open" AND labels IS EMPTY
```

## 与 `search_issues.py` 配合使用

示例：

```bash
python3 scripts/search_issues.py \
  --jql 'project = MC3 AND summary ~ "测试" AND status = "Open"'
```

## 注意事项

- 文本模糊匹配通常使用 `~`，例如 `summary ~ "测试"`。
- 状态名、问题类型名等包含空格时，建议加双引号。
- `project = MC3` 这种项目键通常不需要引号。
- `ORDER BY` 一般放在 JQL 最后。
- 如果一条 JQL 太复杂，建议按下面顺序逐步构造：
  1. 先写 `project`
  2. 再加 `issuetype` 或 `status`
  3. 再加 `assignee`、时间、标签等过滤条件
  4. 最后加 `ORDER BY`
- 若检索结果异常，优先检查：
  - 字段名是否正确
  - 状态名/类型名是否与 Jira 中完全一致
  - 引号是否完整
  - 模糊匹配字段是否支持 `~`
