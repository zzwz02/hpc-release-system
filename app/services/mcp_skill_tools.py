"""MCP tools — HPC expert skills (category ``skill``).

``skill_load`` hands the calling agent the method text of one skill, so a
Claude Code / Codex session gets the same working method the JIRA agent uses
instead of answering about MetaX products from pretrained knowledge or public
pages.  The main page comes first; a reference page is fetched on demand, so a
session only pays for what it reads.

Skills are readable by every role that may use MCP. Entries may expose a
whole skill or a focused reference page from an existing skill.

The text is read from the knowledge pack shipped with the website
(``deploy/jira-agent/hpc/``).  That is the packaging copy, not the formal
release source (the separate ``jira_agent_skill`` repository), so what an
agent gets here matches what this deployment delivers, not necessarily the
newest knowledge commit.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from app.services.mcp_tools import Tool, ToolContext, ToolError, ToolLog, ToolResult, register

CATEGORY = "skill"
_PACK_ROOT = Path(__file__).resolve().parents[2] / "deploy/jira-agent/hpc/.agents/skills/hpc-jira-agent"

SKILLS: dict[str, dict[str, Any]] = {
    "cu-bridge": {
        "folder": _PACK_ROOT / "modules/application-analysis/skills/cu-bridge",
        "summary": "使用 MetaX cu-bridge 把 CUDA 源码应用迁移到 MACA：SDK 配套、cucc 与 "
                   "CMake/Make/Ninja 构建、API 与库映射，以及迁移中的编译、链接、运行问题定位。",
        # page name -> file inside the skill folder; the agent may only ask for these
        "pages": {"build": "references/build.md", "diagnosis": "references/diagnosis.md"},
    },
    "jira-format": {
        "folder": _PACK_ROOT / "modules/jira-evidence/skills/jira-issue-manager",
        "main": "references/jira-format.md",
        "summary": "JIRA wiki 评论格式：标题、列表、代码块、行内代码、链接和表格。",
        "pages": {},
    },
}


def _skill(name: Any) -> dict[str, Any]:
    skill = SKILLS.get(str(name or "").strip())
    if not skill:
        raise ToolError(f"未知 skill，可选：{'、'.join(SKILLS)}")
    return skill


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ToolError(f"读取 skill 文件失败：{exc}") from exc


def _body(skill: dict[str, Any]) -> str:
    """Read the selected skill entry or reference without YAML front matter."""
    text = _read(skill["folder"] / skill.get("main", "SKILL.md"))
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) == 3:
            text = parts[2].lstrip("\n")
    return text


def _load(ctx: ToolContext, args: dict) -> ToolResult:
    name = str(args.get("skill") or "").strip()
    skill = _skill(name)
    page = str(args.get("page") or "").strip()
    ctx.log.target_type, ctx.log.target = "skill", f"{name}/{page}" if page else name
    if page:
        relative = skill["pages"].get(page)
        if not relative:
            raise ToolError(f"未知参考页 {page}，可选：{'、'.join(skill['pages'])}")
        text = f"# {name} / {page}（{relative}）\n\n{_read(skill['folder'] / relative)}"
    else:
        pages = "、".join(f'page="{key}"' for key in skill["pages"])
        text = f"已加载 skill **{name}**。按下面的方法开展工作。"
        if pages:
            text += (f'需要细节时再调用 skill_load（skill="{name}"，{pages}）。'
                     '参考页用本工具读取，不要去找本地文件。')
        text += f"\n\n---\n\n{_body(skill)}"
    return ToolResult.text(text, log=ToolLog(
        target_type="skill", target=ctx.log.target,
        summary=f"{len(text)} 字符", extra={"skill": name, "page": page or "main", "chars": len(text)},
    ))


register(Tool(
    name="skill_load",
    category=CATEGORY,
    title="加载方法与格式规范",
    description=(
        "Load the authoritative MetaX HPC method for a topic before doing the work. "
        "For JIRA comment formatting, call skill=\"jira-format\" without page before drafting "
        "or publishing, including draft-only requests; it reuses jira-issue-manager's format reference. "
        "Available skills: cu-bridge — porting CUDA source applications to MACA with cu-bridge "
        "(SDK/cu-bridge version matching, cucc, cmake_maca/make_maca/ninja_maca builds, API and "
        "library mapping, and triage of migration build, link and runtime failures). "
        "Call it with skill=\"cu-bridge\" and no page for the main method, then again with "
        "page=\"build\" (environment and build system) or page=\"diagnosis\" (compile, link, API, "
        "MPI, PTX, warp, JIT and Python extension problems) when you need those details. "
        "Pretrained knowledge and public pages about these products are often outdated, so read "
        "this before answering, writing build commands or searching the web."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "skill": {"type": "string", "enum": sorted(SKILLS), "description": "Skill name."},
            "page": {"type": "string", "enum": sorted({page for skill in SKILLS.values() for page in skill["pages"]}),
                     "description": "Optional reference page; omit it on the first call."},
        },
        "required": ["skill"],
    },
    handler=_load,
))
