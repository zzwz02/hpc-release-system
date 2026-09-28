# 常见 Issue Type 参考

以下内容用于帮助理解常见的 Jira `issue type` 名称及其大致用途。

注意：
- 这些类型仅作为常见参考，不代表所有项目都支持。
- 实际可用的 `issue type`、必填字段和字段类型，应以 `scripts/get_create_meta.py` 的查询结果为准。
- `Sub-task` 类类型通常只适用于子任务场景，不能直接替代普通问题类型。

## 常规 Issue Type

- `Improvement`：已有功能或任务的改进、增强。
- `Requirement`：尚待开发的新产品需求。
- `Feature`：尚待开发的新产品功能。
- `Task`：需要完成的一般任务。
- `Bug`：影响或阻止产品功能的问题。
- `Story`：Jira Software 的用户故事类型，通常用于需求拆解。
- `Epic`：较大的用户故事或需求集合，通常需要拆分为多个子项。
- `Request`：请求类问题类型，示例说明中提到用于某些 Market 项目场景。
- `Category`：用于组织多个 Epic，示例说明中提到仅用于部分 K8S 项目。
- `Internal RAT`：用于 SW RAT 需求输入。
- `PRS`：产品需求规格类问题。
- `Review`：文档或内容评审流程。
- `PRJ_TASKA`：示例说明中提到是 CCPM 项目下的任务类型。
- `SW_Request`：示例说明中提到是 NPF 项目中的软件请求类型。
- `Knowledge`：用于部分团队在 Jira 中维护知识条目。
- `Project`：面向研发的客户项目类问题。
- `KRs`：Key Result Set。

## 子任务 Issue Type

- `Chip_Review`：示例说明中提到是 NPF 项目中 `Feature` 的子任务类型。
- `Release`：单次发布任务。
- `Sub-doc`：文档更新或编写类子任务。
- `Sub-code`：编码类子任务。
- `Sub-test`：编写或执行测试用例的子任务。
- `Sub_Requirement`：二级需求子任务。
- `Sub_Story`：CCPM Story 的子任务。
- `Sub-task`：通用子任务类型。
- `KR`：Key Result 子任务。

## 使用建议

- 创建普通 issue 前，先确认当前项目支持哪些常规 `issue type`。
- 创建子任务前，先确认父问题类型与当前项目是否支持对应的子任务类型。
- 若用户只给出模糊描述，应优先结合项目上下文和创建元数据，再决定使用 `Bug`、`Task`、`Feature`、`Requirement` 等类型。
