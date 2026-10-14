"""Prompts for the two LLM steps of the weekly report check.

Ported unchanged from the local weekly_report_jira_check.py.  Everything else
in the check is decided by code, see app/domain/weekly_report.py.
"""
from __future__ import annotations

# The script sent each prompt as a single user message; llm.ask_json also needs
# a system message.
SYSTEM = "你是研发周报检查助手，严格按用户给出的格式只输出 JSON。"

# Step 1: split one person's "本周更新" into work items and find their JIRA ids.
LLM_PROMPT = """下面是一位工程师周报中的「本周更新」。请把它拆成工作项，并找出每个工作项对应的 JIRA ID。

规则：
- 工作项以顶层编号条目为单位（如 1. / 2. / 1、/【...】开头）；顶层条目下的子项（a. / 1) / i. / 1.1 等）属于同一个工作项，不要拆开。
- 例外：顶层标题只是「其他」「Jira：」这类没有具体内容的容器时，才用其下的子条目作为工作项。
- 只处理本周更新，忽略「下周计划」。没有实际内容的条目（如只有标题、空行）不算工作项。
- 一个工作项的 JIRA ID 指标题或正文里与该工作相关的 JIRA 单号（如 PHS-11452、MC3-15593、CCPM-9551），包括「已提jira MC3-xxxx」这种正文里提到的单号。
- 规范化写法：「JIRA PHS 11452」「PHS11452」都写成 PHS-11452；字母部分大写；原文的拼写（如 CCMP）不要纠正。
- 只有数字没有项目前缀的（如「JiraID 11372」），放到 incomplete_ids，原样写数字。
- 不要编造原文里没有的单号。

只输出 JSON，不要其他文字，格式：
{"items": [{"title": "工作项简称（20字以内）", "summary": "本周这一项做了什么（80字以内，保留关键结论和数据）", "jira_ids": ["PHS-11452"], "incomplete_ids": []}]}

本周更新：
<<<
%s
>>>"""

# Step 2: does the person's JIRA activity reflect the work item?
CONSIST_PROMPT = """判断一位工程师在 JIRA 上的更新，是否反映了他周报里对这项工作的描述。

周报工作项：%s
周报内容：%s

本人在统计窗口内对相关 JIRA 的更新（comment、字段修改、worklog）：
<<<
%s
>>>

判断标准：
- 一致：JIRA 更新里能看到周报所说的主要进展或主要结论即可，不要求细节、数据、计划逐条对上。
- 部分一致：JIRA 只有状态 / 字段变化（或只有附件、链接），没有任何文字说明进展。
- 不一致：JIRA 更新与周报描述的工作无关，或周报的主要进展在 JIRA 里完全看不到。
- 拿不准时判为「一致」。

只输出 JSON：{"verdict": "一致|部分一致|不一致", "reason": "一句话理由（40字以内）"}"""

VERDICT_MISMATCH = "不一致"
VERDICT_PARTIAL = "部分一致"
