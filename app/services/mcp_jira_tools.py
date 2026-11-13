"""MCP tools — JIRA (category ``jira``).

The MCP server runs on the website, so it cannot write to the caller's
disk.  ``jira_pull_issue`` therefore returns the issue as Markdown plus a
download command per attachment (short-lived ticket links served by
/api/mcp/downloads/...), and tells the agent where to save them.

Reads use the [jira] service account's visibility, like the JIRA agent page.
Comments are posted by that account too, so the body gets a footer naming
the website user; only the current assignee or RM may comment (the JIRA
agent rule, including its cybersecurity risk lock).
"""
from __future__ import annotations

import json
import re
import shlex
import urllib.error
from typing import Any
from urllib.parse import quote

from app.api.errors import AuthzError
from app.domain.jira_format import format_jira_comment
from app.services.mcp_evidence import correlations
from app.integrations import jira
from app.services import jira_agent_service, mcp_service
from app.services.mcp_tools import Tool, ToolContext, ToolError, ToolLog, ToolResult, register

CATEGORY = "jira"
SEARCH_MAX_RESULTS = 50
COMMENT_MAX_CHARS = 32_000
# Keep the elicitation form reachable on clients that render the message above it.
COMMENT_PREVIEW_MAX_CHARS = 240
COMMENT_PREVIEW_MAX_LINES = 6
# Custom field values longer than this are cut in issue.md.
CUSTOM_FIELD_MAX_CHARS = 500
_UNSAFE_NAME = re.compile(r"[^\w.\-+()\[\] ]+", re.UNICODE)


def _key(value: Any) -> str:
    try:
        return jira_agent_service.normalize_issue_key(str(value or ""))
    except ValueError as exc:
        raise ToolError(str(exc)) from exc


def _jira_call(fn, *args, issue_key: str = "", **kwargs):
    """Run a JIRA client call, turning transport errors into ToolError."""
    try:
        return fn(*args, **kwargs)
    except jira.JiraQueryError as exc:
        raise ToolError(f"JQL 无效：{exc}") from exc
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403, 404) and issue_key:
            raise ToolError(f"JIRA 工单 {issue_key} 不存在或无权访问") from exc
        raise ToolError(f"JIRA 返回 HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise ToolError(f"无法连接 JIRA：{exc.reason}") from exc
    except RuntimeError as exc:  # [jira] not configured
        raise ToolError(str(exc)) from exc


def _person(value: dict | None) -> str:
    if not value:
        return "未分配"
    return f"{value.get('display_name') or value.get('name')} ({value.get('name')})"


# ─────────────────────────────────────────────────────────────
# jira_search
# ─────────────────────────────────────────────────────────────

def _search(ctx: ToolContext, args: dict) -> ToolResult:
    query = str(args.get("query") or "").strip()
    limit = max(1, min(int(args.get("max_results") or 20), SEARCH_MAX_RESULTS))
    if not query:
        jql = f'assignee = "{jira.agent_username(ctx.user)}" AND status != Closed ORDER BY updated DESC'
    elif re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*-\d+", query):
        jql = f"key = {query.upper()}"
    else:
        jql = query
    ctx.log.extra = {"jql": jql}
    found = _jira_call(jira.search_issues, jql, max_results=limit)
    lines = [f"JQL: {jql}", f"共 {found['total']} 条，显示 {len(found['issues'])} 条", ""]
    lines += ["| Key | 类型 | 状态 | 优先级 | Assignee | 更新 | 标题 |", "| --- | --- | --- | --- | --- | --- | --- |"]
    for item in found["issues"]:
        lines.append(
            f"| {item['key']} | {item['issue_type']} | {item['status']} | {item['priority']} | "
            f"{(item['assignee'] or {}).get('name', '')} | {item['updated'][:10]} | "
            f"{item['summary'].replace('|', '/')} |"
        )
    return ToolResult.text("\n".join(lines), log=ToolLog(
        summary=f"{len(found['issues'])}/{found['total']} 条",
        extra={"jql": jql, "returned": len(found["issues"]), "total": found["total"]},
    ))


register(Tool(
    name="jira_search",
    category=CATEGORY,
    title="搜索 JIRA",
    description=(
        "Search JIRA issues. `query` may be empty (my open issues), one issue key such as MC3-7672, "
        "or any JQL. Returns a Markdown table of key, type, status, priority, assignee, updated, summary."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Empty, an issue key, or JQL."},
            "max_results": {"type": "integer", "minimum": 1, "maximum": SEARCH_MAX_RESULTS, "default": 20},
        },
    },
    handler=_search,
))


# ─────────────────────────────────────────────────────────────
# jira_pull_issue
# ─────────────────────────────────────────────────────────────

def _field_text(value: Any) -> str:
    if isinstance(value, dict):
        value = value.get("value") or value.get("name") or value.get("displayName") or json.dumps(value, ensure_ascii=False)
    elif isinstance(value, list):
        value = ", ".join(_field_text(item) for item in value)
    text = str(value).replace("\n", " ")
    return text if len(text) <= CUSTOM_FIELD_MAX_CHARS else text[:CUSTOM_FIELD_MAX_CHARS] + "…"


_IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".gif", ".bmp", ".svg", ".webp")


def attachment_link(filename: str, attachment_id: str, jira_base: str = "") -> str:
    """Wiki-markup link to one attachment by id; unlike [^name] it cannot hit a same-named file."""
    base = jira_base or jira.browse_url().removesuffix("/browse/")
    label = filename.replace("|", "/").replace("[", "(").replace("]", ")")
    return f"[{label}|{base}/secure/attachment/{attachment_id}/{quote(filename, safe='')}]"


def attachment_reference(filename: str, attachment_id: str, jira_base: str = "") -> str:
    """How to cite an attachment in a comment, as told to the agent."""
    lines = [f"评论中引用（按附件 ID 精确链接，推荐）：{attachment_link(filename, attachment_id, jira_base)}"]
    if filename.lower().endswith(_IMAGE_SUFFIXES):
        lines.append(f"在评论中显示图片：!{filename}!（工单内有同名图片时可能显示成另一张）")
    return "\n".join(lines)


def render_issue_markdown(issue: dict) -> str:
    out = [
        f"# {issue['key']} {issue['summary']}",
        "",
        f"- 链接：{issue['url']}",
        f"- 类型：{issue['issue_type']}　状态：{issue['status']}　优先级：{issue['priority']}",
        f"- 项目：{issue['project']}　组件：{', '.join(issue['components']) or '-'}　标签：{', '.join(issue['labels']) or '-'}",
        f"- Assignee：{_person(issue['assignee'])}　Reporter：{_person(issue['reporter'])}",
        f"- 创建：{issue['created']}　更新：{issue['updated']}",
        "",
        "## 描述",
        "",
        issue["description"] or "（无）",
    ]
    custom = [(v["name"], _field_text(v["value"])) for v in (issue.get("custom_fields") or {}).values()
              if v.get("value") not in (None, "", [], {})]
    if custom:
        out += ["", "## 自定义字段", ""] + [f"- {name}：{text}" for name, text in sorted(custom)]
    out += ["", f"## 评论（{len(issue['comments'])}）", ""]
    for comment in issue["comments"]:
        out += [f"### {comment['author']} · {comment['created']}", "", comment["body"] or "", ""]
    if issue["attachments"]:
        jira_base = issue["url"].rsplit("/browse/", 1)[0]
        out += ["## 附件", "", "评论里引用附件时用每行末尾的链接写法（按附件 ID，不会指错同名附件）。", ""] + [
            f"- ID={a['id']} {a['filename']}（{a['size']} bytes，{a['mime_type']}）：`{attachment_link(a['filename'], a['id'], jira_base)}`"
            for a in issue["attachments"]
        ]
    acquisition = issue.get("acquisition") or {}
    if acquisition.get("status") == "partial":
        out += ["", f"> 注意：拉取不完整：{json.dumps(acquisition.get('parts'), ensure_ascii=False)}"]
    return "\n".join(out).rstrip() + "\n"


def _safe_filename(name: str, attachment_id: str, seen: set[str]) -> str:
    clean = _UNSAFE_NAME.sub("_", name).strip(" .") or f"attachment-{attachment_id}"
    if clean.casefold() in seen:
        clean = f"{attachment_id}-{clean}"
    seen.add(clean.casefold())
    return clean


def _pull(ctx: ToolContext, args: dict) -> ToolResult:
    key = _key(args.get("issue_key"))
    ctx.log.target_type, ctx.log.target = "jira_issue", key
    target_dir = str(args.get("target_dir") or "").strip() or f"jira/{key}"
    with_attachments = args.get("include_attachments", True) is not False
    issue = _jira_call(jira.get_issue_snapshot, key, issue_key=key)
    markdown = render_issue_markdown(issue)

    commands, seen = [], set()
    if with_attachments and issue["attachments"]:
        attach_dir = f"{target_dir.rstrip('/')}/attachments"
        commands.append(f"mkdir -p {shlex.quote(attach_dir)}")
        for attachment in issue["attachments"]:
            ticket = mcp_service.issue_ticket(
                ctx.caller, ctx.session, issue_key=key, filename=attachment["filename"],
                content_url=attachment["content_url"],
            )
            local = f"{attach_dir}/{_safe_filename(attachment['filename'], attachment['id'], seen)}"
            url = f"{ctx.base_url}/api/mcp/downloads/{ticket}"
            commands.append(f"curl -fsSL -o {shlex.quote(local)} {shlex.quote(url)}")

    guide = [
        f"已拉取 {key}（{len(issue['comments'])} 条评论，{len(issue['attachments'])} 个附件）。",
        f"请在用户的本地工作目录保存到 `{target_dir}/`（用户另有指定时按用户的目录）：",
        f"1. 把下一段内容原样写入 `{target_dir}/issue.md`。",
    ]
    if commands:
        guide += [
            "2. 运行下面的命令下载附件（链接 "
            f"{mcp_service.DOWNLOAD_TICKET_SECONDS // 60} 分钟内有效，无需额外认证）：",
            "```bash", *commands, "```",
        ]
    elif issue["attachments"]:
        guide.append("附件未下载（include_attachments=false）。")
    return ToolResult.text("\n".join(guide), markdown,
                           json.dumps({"correlations": correlations(issue, issue["url"])}, ensure_ascii=False), log=ToolLog(
        target_type="jira_issue", target=key,
        summary=f"{len(issue['comments'])} 评论 / {len(issue['attachments'])} 附件",
        extra={"comments": len(issue["comments"]), "attachments": len(issue["attachments"]),
               "attachment_links": max(0, len(commands) - 1),
               "acquisition": (issue.get("acquisition") or {}).get("status", "")},
    ))


register(Tool(
    name="jira_pull_issue",
    category=CATEGORY,
    title="拉取 JIRA 到本地",
    description=(
        "Fetch one JIRA issue (fields, description, custom fields, all comments, attachment list) so it "
        "can be saved locally. The result says which files to write: issue.md with the returned Markdown, "
        "and curl commands that download each attachment into <target_dir>/attachments. "
        "Default target_dir is jira/<ISSUE-KEY> under the current working directory. "
        "Use jira_read_attachment with listed attachment ID to read text without curl. "
        "Recorded correlation fields and explicit JIRA/Jenkins links are returned with provenance."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "issue_key": {"type": "string", "description": "e.g. MC3-7672"},
            "target_dir": {"type": "string", "description": "Local directory to save into; default jira/<KEY>."},
            "include_attachments": {"type": "boolean", "default": True},
        },
        "required": ["issue_key"],
    },
    handler=_pull,
))


# ─────────────────────────────────────────────────────────────
# jira_add_comment
# ─────────────────────────────────────────────────────────────

def check_can_write(user: dict, key: str, action: str = "评论") -> dict:
    """Live JIRA read + the JIRA agent rule: current assignee or RM, risk lock respected."""
    issue = _jira_call(jira.get_issue, key, issue_key=key)
    try:
        jira_agent_service.check_risk_lock(user, key)
    except AuthzError as exc:
        raise ToolError(str(exc)) from exc
    if not jira_agent_service.can_act(user, issue):
        raise ToolError(
            f"只有当前 JIRA assignee 或 RM 可以{action}；{key} 的 assignee 是 "
            f"{jira_agent_service.assignee_name(issue) or '无'}"
        )
    return issue


def _prepare_comment(ctx: ToolContext, args: dict) -> dict:
    _key(args.get("issue_key"))
    body = format_jira_comment(str(args.get("body") or "").strip())
    if not body:
        raise ToolError("评论内容不能为空")
    if len(body + comment_footer(ctx)) > COMMENT_MAX_CHARS:
        raise ToolError(f"评论超过 {COMMENT_MAX_CHARS} 字符")
    return {**args, "body": body}


def _confirm_comment(ctx: ToolContext, args: dict) -> str:
    key = _key(args.get("issue_key"))
    body = str(args.get("body") or "").strip()
    preview = "".join(body.splitlines(keepends=True)[:COMMENT_PREVIEW_MAX_LINES])[:COMMENT_PREVIEW_MAX_CHARS]
    if len(preview) < len(body):
        return (f"即将在 JIRA {key} 发布评论（全文 {len(body)} 字符）。"
                "请输入你本人的域账号用户名确认发送；发布后无法撤回。\n\n"
                f"评论摘要：\n{preview.rstrip()}\n"
                "…（摘要已截断，完整正文请在对话中核对；发送时保留全文。）\n"
                "请输入本人域账号用户名：")
    return (f"即将在 JIRA {key} 发布下面的评论。"
            "请输入你本人的域账号用户名，匹配发布账号后发送；发布后无法撤回。\n\n"
            f"{body}")


def comment_footer(ctx: ToolContext) -> str:
    return f"\n\n----\n_由 {ctx.actor} 通过 {ctx.client_name}（发布系统 MCP）发布_"


def _comment(ctx: ToolContext, args: dict) -> ToolResult:
    key = _key(args.get("issue_key"))
    ctx.log.target_type, ctx.log.target = "jira_issue", key
    body = str(args.get("body") or "").strip()
    if not body:
        raise ToolError("评论内容不能为空")
    if len(body) > COMMENT_MAX_CHARS:
        raise ToolError(f"评论超过 {COMMENT_MAX_CHARS} 字符")
    issue = check_can_write(ctx.user, key)
    comment_id = _jira_call(jira.add_comment, key, body + comment_footer(ctx), issue_key=key)
    url = f"{issue['url']}?focusedCommentId={comment_id}"
    return ToolResult.text(f"已在 {key} 发布评论（ID {comment_id}）：{url}", log=ToolLog(
        target_type="jira_issue", target=key, summary=f"comment {comment_id}",
        extra={"comment_id": comment_id, "chars": len(body)},
    ))


register(Tool(
    name="jira_add_comment",
    category=CATEGORY,
    title="评论 JIRA",
    description=(
        "Append a comment (JIRA wiki markup) to an issue. Only the issue's current assignee or an RM may "
        "comment. Before drafting, load skill_load(skill=\"jira-format\"), including draft-only requests. "
        "The server applies shared JIRA wiki formatting before confirmation and posts that same body. "
        "Before calling, show the complete proposed comment in the conversation for the user to review. "
        "The confirmation dialog shows only a bounded excerpt for long comments; the full body is posted. "
        "To cite an attachment, use the [name|url] link that jira_pull_issue or the upload output "
        "gives (it targets the attachment id); [^name] may hit a same-named file. "
        "Before posting, the user must type their own domain username in the MCP elicitation form. "
        "The server compares it with the token owner (trimmed, case-insensitive). Missing or mismatched "
        "usernames, cancellation and timeout do not post. Clients without form elicitation cannot post. "
        "The comment is posted by the release-system JIRA account with a footer naming the token owner."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "issue_key": {"type": "string", "description": "e.g. MC3-7672"},
            "body": {"type": "string", "description": "Comment text in JIRA wiki markup."},
        },
        "required": ["issue_key", "body"],
    },
    handler=_comment,
    prepare=_prepare_comment,
    read_only=False,
    destructive=True,          # a published comment cannot be withdrawn
    confirm=_confirm_comment,
))


# ─────────────────────────────────────────────────────────────
# jira_upload_attachment
# ─────────────────────────────────────────────────────────────

def _upload(ctx: ToolContext, args: dict) -> ToolResult:
    """Check permission now and hand back a one-time upload command for the local file."""
    key = _key(args.get("issue_key"))
    ctx.log.target_type, ctx.log.target = "jira_issue", key
    file_path = str(args.get("file_path") or "").strip()
    if not file_path:
        raise ToolError("需要本地文件路径 file_path")
    filename = str(args.get("filename") or "").strip() or file_path.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]
    filename = _UNSAFE_NAME.sub("_", filename).strip(" .")[:200]
    if not filename:
        raise ToolError("无法确定附件文件名，请传 filename")
    ctx.log.extra = {"filename": filename}
    check_can_write(ctx.user, key, "上传附件")
    ticket = mcp_service.issue_upload_ticket(ctx.caller, ctx.session, issue_key=key, filename=filename)
    url = f"{ctx.base_url}/api/mcp/uploads/{ticket}"
    # No -f: on failure the server's plain-text reason is what the agent needs to read.
    command = (f"curl -sS -X PUT -H 'Content-Type: application/octet-stream' "
               f"--data-binary @{shlex.quote(file_path)} {shlex.quote(url)}")
    limit_mb = mcp_service.UPLOAD_MAX_BYTES // (1024 * 1024)
    text = "\n".join([
        f"已核对权限。请在用户本机运行下面的命令，把 `{file_path}` 作为 `{filename}` 上传到 {key}"
        f"（不超过 {limit_mb} MB；链接 {mcp_service.UPLOAD_TICKET_SECONDS // 60} 分钟内有效，成功一次后失效）：",
        "```bash", command, "```",
        "输出「已上传到 …附件 ID …」即成功；输出「上传失败：…」或其他内容即失败，按原因处理"
        "（网络等临时错误可直接重试同一命令，链接已用过或过期则重新调用本工具）。",
        "附件以发布系统 JIRA 账号上传；需要说明来源时，再用 jira_add_comment 发一条评论。",
    ])
    return ToolResult.text(text, log=ToolLog(
        target_type="jira_issue", target=key, summary=f"上传链接 {filename}", extra={"filename": filename},
    ))


register(Tool(
    name="jira_upload_attachment",
    category=CATEGORY,
    title="上传 JIRA 附件",
    description=(
        "Attach a file from the user's machine to a JIRA issue. Only the issue's current assignee or an RM "
        "may upload. This tool checks permission and returns a one-time curl command; run it locally to send "
        "the file (the MCP server cannot read local files). No elicitation confirmation is required for "
        "attachments. Permission is checked both when issuing the command and when receiving the upload."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "issue_key": {"type": "string", "description": "e.g. MC3-7672"},
            "file_path": {"type": "string", "description": "Path of the local file to upload."},
            "filename": {"type": "string", "description": "Attachment name in JIRA; default is the file's name."},
        },
        "required": ["issue_key", "file_path"],
    },
    handler=_upload,
    read_only=False,
    destructive=False,         # adds a file; changes nothing already on the issue
))
