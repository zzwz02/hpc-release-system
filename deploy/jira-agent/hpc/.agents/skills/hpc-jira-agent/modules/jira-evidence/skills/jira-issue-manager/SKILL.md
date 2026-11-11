---
name: jira-issue-manager
description: 维护保留的独立 Jira REST 脚本及其采集、写入批准契约；发布网站部署的工单流程使用上层本地材料模块。
---

# 独立 Jira 工具维护入口

本网站部署不调用这些 REST 读写工具。处理当前工单时使用[网站材料模块](../../SKILL.md)读取本地快照，结果由网站处理，不配置 Jira token。

仅在明确维护上游独立接口时读取[独立使用说明](references/standalone-usage.md)，再按具体操作查看接入、采集、附件、字段或测试参考。脚本与测试保留原实现；维护接口的存在不扩大当前部署权限。

起草 JIRA 正文或仅检查排版时，读取[评论格式](references/jira-format.md)。该参考页也由发布系统 MCP 的 `skill_load(skill="jira-format")` 直接提供，格式用途不要求使用上述独立脚本。
