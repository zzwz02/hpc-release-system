#!/usr/bin/env python3
"""Check trace-report reference routing guardrails.

This is a static anchor check for documentation routing contracts. It does not
interpret prose with an LLM, execute profiling commands, or read artifacts.
"""

from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "SKILL.md"
REFERENCE = ROOT / "reference"


def read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise RuntimeError(f"cannot read {path}: {exc}") from exc


def require_contains(failures: list[str], path: Path, text: str, needle: str, reason: str) -> None:
    if needle not in text:
        failures.append(f"{path.relative_to(ROOT)} missing {reason}: {needle!r}")


def has_read_gate(text: str) -> bool:
    anchors = (
        "读取门槛",
        "何时读取",
        "When to read",
        "For routine finalization",
        "先按 `^## TR-...$`",
        "低 token 使用方式",
        "只有",
        "不要全文读取",
    )
    return any(anchor in text for anchor in anchors)


def main() -> int:
    failures: list[str] = []
    skill_text = read(SKILL)

    require_contains(failures, SKILL, skill_text, "每次触发本 skill 时先读本文件", "SKILL-first rule")
    require_contains(failures, SKILL, skill_text, "Step 3 诊断基础文档", "Step 3 diagnosis foundation rule")
    require_contains(failures, SKILL, skill_text, "DEVELOPER_README.md", "developer README routing guard")
    require_contains(failures, SKILL, skill_text, "不属于 agent workflow 文档", "developer README exclusion")
    require_contains(failures, SKILL, skill_text, "Source-latency 的唯一常规特例", "source-latency exception boundary")
    require_contains(failures, SKILL, skill_text, "该特例不具备传递性", "source-latency non-transitive boundary")

    reference_files = sorted(REFERENCE.glob("*.md"))
    if not reference_files:
        failures.append("reference directory has no markdown files")
    for path in reference_files:
        text = read(path)
        if not has_read_gate(text):
            failures.append(f"{path.relative_to(ROOT)} missing read-gate anchors")

    quick_collect = read(REFERENCE / "quick-collect.md")
    require_contains(
        failures,
        REFERENCE / "quick-collect.md",
        quick_collect,
        "不要因普通 `collect-run`、普通 `validate` 或启用 source-latency 默认读取本文",
        "02-collection anti-overread rule",
    )
    require_contains(
        failures,
        REFERENCE / "quick-collect.md",
        quick_collect,
        "Source-latency 读取不具传递性",
        "source-latency non-transitive rule",
    )
    require_contains(
        failures,
        REFERENCE / "quick-collect.md",
        quick_collect,
        "Password SSH 最小规则",
        "password SSH quick rule",
    )
    require_contains(
        failures,
        REFERENCE / "quick-collect.md",
        quick_collect,
        "密码不得写入 active config",
        "password non-persistence rule",
    )

    quick_finalize = read(REFERENCE / "quick-finalize.md")
    require_contains(
        failures,
        REFERENCE / "quick-finalize.md",
        quick_finalize,
        "常规 finalize 先读本文，不默认读取 `07-report-template.md` 或 `08-llm-followup-diagnosis.md`",
        "quick-finalize anti-overread rule",
    )
    require_contains(
        failures,
        REFERENCE / "quick-finalize.md",
        quick_finalize,
        "只在本文无法完成下一步决策时读取深层文档",
        "quick-finalize deep-read gate",
    )

    quick_diagnose = read(REFERENCE / "quick-diagnose.md")
    for needle in (
        "`reference/04-analysis-dimensions.md` 和 `reference/06-diagnosis-playbook.md` 是诊断基础，必须读取",
        "读取 `reference/04-analysis-dimensions.md`",
        "读取 `reference/06-diagnosis-playbook.md`",
        "按需扩展文档读取门槛",
    ):
        require_contains(
            failures,
            REFERENCE / "quick-diagnose.md",
            quick_diagnose,
            needle,
            "quick-diagnose mandatory 04/06 rule",
        )
    analysis_dimensions = read(REFERENCE / "04-analysis-dimensions.md")
    diagnosis_playbook = read(REFERENCE / "06-diagnosis-playbook.md")
    for path, text in (
        (REFERENCE / "04-analysis-dimensions.md", analysis_dimensions),
        (REFERENCE / "06-diagnosis-playbook.md", diagnosis_playbook),
    ):
        require_contains(failures, path, text, "Step 3 诊断基础文档", "Step 3 foundation read gate")
        require_contains(failures, path, text, "`workflow_state: needs_llm_followup`", "needs_llm_followup read gate")
        require_contains(failures, path, text, "不得在采集前", "no pre-collection read rule")

    troubleshooting = read(REFERENCE / "09-error-troubleshooting.md")
    require_contains(
        failures,
        REFERENCE / "09-error-troubleshooting.md",
        troubleshooting,
        "用标题精确搜索读取对应详情块",
        "TR-code local-read rule",
    )

    if failures:
        for failure in failures:
            print(f"[reference-routing] fail: {failure}", file=sys.stderr)
        return 1
    print("[reference-routing] ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
