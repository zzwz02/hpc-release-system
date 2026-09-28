# HPC Jira 数字员工

此目录是 `hpc_release_system` 的部署知识包。一个网站对话对应一个持续处理工单的 Codex 会话，由主 skill 按需调用材料读取、远端实验、问题分析和应用专项模块。

## 从哪里开始

- [能力与架构](docs/overview.md)：HPC agent 的执行环境、内部模块关系和完整处理流程。
- [主 SKILL](SKILL.md)：实际处理工单的入口与决策流程。
- [项目 AGENTS.md](../../../AGENTS.md)：本部署职责、权限、目录和资源规则。
- [网站交付规范](modules/jira-evidence/references/result-delivery.md)：报告、结构化结论及产物回收要求。

## 能力概览

读取网站同步的工单材料，核对应用范围，在获准环境复现并定位构建、运行、正确性和性能问题。本组问题形成候选补丁并验证，外部问题整理可复核的交接材料。各模块的细分能力与协作方式见架构总览，应用专项清单由 [application-analysis](modules/application-analysis/SKILL.md#按任务选择)统一维护。

报告、补丁和结构化结论交给网站；网站按本轮设置发布 Jira 评论，由 assignee 决定后续处理。独立 Jira 工具不在网站工单流程中调用。
