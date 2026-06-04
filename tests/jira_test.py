#!/usr/bin/env python3
"""
jira_test.py — Jira REST API 连通性与建单测试脚本（纯 stdlib，无需额外安装依赖）

用法：
  python3 jira_test.py                           # 完整测试（会真实创建 issue）
  python3 jira_test.py --dry-run                 # 只验证鉴权，不建单
  python3 jira_test.py --title "[New] App1 【新发布项目】"
  python3 jira_test.py --config /path/to/other.conf

配置文件 jira.conf（与本脚本同目录，格式见 jira.conf.example）：
  JIRA_BASE_URL     = http://jira.metax-tech.com
  JIRA_TOKEN        = <个人访问令牌 PAT>
  JIRA_PROJECT      = SPD
  JIRA_ASSIGNEE     = m00930
  JIRA_PARENT_ISSUE = SPD-123   # 留空则创建顶级 Task；有值则创建子任务

执行步骤：
  1. 读取 jira.conf
  2. GET /rest/api/2/myself         — 验证鉴权和网络连通性
  3. GET /rest/api/2/project/{KEY}  — 发现该项目支持的 issue 类型
  4. POST /rest/api/2/issue         — 创建测试 issue（Task 或子任务）
  5. 输出创建后的 issue URL
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from datetime import date, timedelta
from pathlib import Path


# ─────────────────────────────────────────────────────────────
# 配置加载
# ─────────────────────────────────────────────────────────────

def load_config(path: str = "jira.conf") -> dict:
    cfg_path = Path(__file__).parent / path
    if not cfg_path.exists():
        sys.exit(
            f"[ERROR] 配置文件不存在: {cfg_path}\n"
            "请将 jira.conf.example 复制为 jira.conf 并填写真实值。"
        )
    config: dict = {}
    with open(cfg_path, encoding="utf-8") as f:
        for lineno, raw in enumerate(f, 1):
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                print(f"  [WARN] 第 {lineno} 行跳过（无 '='）: {line!r}")
                continue
            key, _, val = line.partition("=")
            config[key.strip()] = val.strip()

    missing = [k for k in ("JIRA_BASE_URL", "JIRA_TOKEN", "JIRA_PROJECT") if not config.get(k)]
    if missing:
        sys.exit(f"[ERROR] jira.conf 缺少必填项: {missing}")

    # 去除 base_url 末尾斜杠
    config["JIRA_BASE_URL"] = config["JIRA_BASE_URL"].rstrip("/")
    return config


# ─────────────────────────────────────────────────────────────
# HTTP 工具
# ─────────────────────────────────────────────────────────────

def jira_request(base_url: str, token: str, method: str, path: str,
                 body: dict | None = None) -> dict:
    url = base_url + path
    data = json.dumps(body, ensure_ascii=False).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Content-Type", "application/json; charset=utf-8")
    req.add_header("Accept", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            raw = resp.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")
        sys.exit(f"[ERROR] HTTP {e.code} {e.reason}  [{method} {path}]\n{detail}")
    except urllib.error.URLError as e:
        sys.exit(
            f"[ERROR] 连接失败: {e.reason}\n"
            f"  请确认 JIRA_BASE_URL={base_url!r} 可达，且在内网环境中运行。"
        )


# ─────────────────────────────────────────────────────────────
# 步骤
# ─────────────────────────────────────────────────────────────

def step_check_auth(cfg: dict) -> None:
    print("Step 1  验证鉴权 ...")
    me = jira_request(cfg["JIRA_BASE_URL"], cfg["JIRA_TOKEN"], "GET", "/rest/api/2/myself")
    display = me.get("displayName") or me.get("name") or "?"
    name = me.get("name") or me.get("accountId") or "?"
    print(f"  ✓ 已登录: {display} ({name})")


def step_discover_issue_types(cfg: dict) -> dict:
    """返回 {小写名称: 原始名称} 映射。"""
    project_key = cfg["JIRA_PROJECT"]
    print(f"Step 2  获取项目 {project_key} 的 issue 类型 ...")
    project = jira_request(
        cfg["JIRA_BASE_URL"], cfg["JIRA_TOKEN"],
        "GET", f"/rest/api/2/project/{project_key}"
    )
    types = {it["name"].lower(): it["name"] for it in project.get("issueTypes", [])}
    if not types:
        all_types = jira_request(
            cfg["JIRA_BASE_URL"], cfg["JIRA_TOKEN"],
            "GET", "/rest/api/2/issuetype"
        )
        types = {it["name"].lower(): it["name"] for it in all_types}
    print(f"  可用类型: {list(types.values())}")
    return types


def step_discover_eta_fields(cfg: dict) -> dict:
    """Step 3: 发现自定义 ETA 字段 ID。优先使用配置文件指定的值。"""
    print("Step 3  查找自定义 ETA 字段 ...")
    result: dict = {}
    # Config override takes priority
    if cfg.get("JIRA_FIELD_EXPECTED_ETA"):
        result["expected_eta"] = cfg["JIRA_FIELD_EXPECTED_ETA"]
        print(f"  (配置覆盖) Expected ETA = {result['expected_eta']}")
    if cfg.get("JIRA_FIELD_ESTIMATED_ETA"):
        result["estimated_eta"] = cfg["JIRA_FIELD_ESTIMATED_ETA"]
        print(f"  (配置覆盖) Estimated ETA = {result['estimated_eta']}")
    if len(result) == 2:
        return result
    # Auto-discover via GET /rest/api/2/field
    try:
        fields = jira_request(cfg["JIRA_BASE_URL"], cfg["JIRA_TOKEN"], "GET", "/rest/api/2/field")
        for f in fields:
            name = (f.get("name") or "").lower()
            fid  = f.get("id", "")
            if "expected_eta" not in result and "expected" in name and "eta" in name:
                result["expected_eta"] = fid
                print(f"  ✓ Expected ETA  → {fid!r}  (name={f.get('name')!r})")
            elif "estimated_eta" not in result and "estimated" in name and "eta" in name:
                result["estimated_eta"] = fid
                print(f"  ✓ Estimated ETA → {fid!r}  (name={f.get('name')!r})")
    except Exception as e:
        print(f"  [WARN] 字段自动发现失败: {e}")
    if not result:
        print("  [WARN] 未找到 ETA 字段。可在 jira.conf 手动配置")
        print("         JIRA_FIELD_EXPECTED_ETA = customfield_XXXX")
        print("         JIRA_FIELD_ESTIMATED_ETA = customfield_YYYY")
    return result


def _pick_type(types: dict, candidates: list) -> str | None:
    """从候选词列表中按优先级匹配 issue 类型名（不区分大小写）。"""
    for c in candidates:
        if c.lower() in types:
            return types[c.lower()]
    # 模糊匹配：包含关键词
    for keyword in candidates:
        for k, v in types.items():
            if keyword.lower() in k:
                return v
    return None


def step_create_issue(cfg: dict, types: dict, title: str, dry_run: bool,
                      eta_fields: dict | None = None) -> str | None:
    parent_key = cfg.get("JIRA_PARENT_ISSUE", "").strip()
    component  = cfg.get("JIRA_COMPONENT", "SPD_CICD")
    eta_val    = (date.today() + timedelta(days=2)).strftime("%Y-%m-%d")

    if parent_key:
        # ── 子任务 ──
        subtask_candidates = ["子任务", "subtask", "sub-task", "sub task", "子问题"]
        type_name = _pick_type(types, subtask_candidates)
        if not type_name:
            # 最后兜底：找含 task 的非顶级类型
            type_name = next((v for k, v in types.items() if "task" in k), None)
        if not type_name:
            sys.exit(
                f"[ERROR] 未找到子任务类型。\n"
                f"  可用类型: {list(types.values())}\n"
                "  请在 jira.conf 中添加 JIRA_ISSUE_TYPE_SUBTASK=<确切名称>"
            )
        # 允许配置覆盖
        if cfg.get("JIRA_ISSUE_TYPE_SUBTASK"):
            type_name = cfg["JIRA_ISSUE_TYPE_SUBTASK"]

        fields: dict = {
            "project":    {"key": cfg["JIRA_PROJECT"]},
            "summary":    title,
            "issuetype":  {"name": type_name},
            "parent":     {"key": parent_key},
            "components": [{"name": component}],
        }
        if cfg.get("JIRA_ASSIGNEE"):
            fields["assignee"] = {"name": cfg["JIRA_ASSIGNEE"]}
        print(f"Step 4  创建子任务 (parent={parent_key}, type={type_name!r}) ...")

    else:
        # ── 顶级 Task ──
        task_candidates = ["task", "任务", "故事", "story"]
        type_name = _pick_type(types, task_candidates) or next(iter(types.values()), "Task")
        if cfg.get("JIRA_ISSUE_TYPE_TASK"):
            type_name = cfg["JIRA_ISSUE_TYPE_TASK"]

        fields = {
            "project":    {"key": cfg["JIRA_PROJECT"]},
            "summary":    title,
            "issuetype":  {"name": type_name},
            "components": [{"name": component}],
        }
        if cfg.get("JIRA_ASSIGNEE"):
            fields["assignee"] = {"name": cfg["JIRA_ASSIGNEE"]}
        print(f"Step 4  创建顶级 Task (type={type_name!r}) ...")

    # ETA fields (today + 2 days)
    if eta_fields:
        exp = eta_fields.get("expected_eta")
        est = eta_fields.get("estimated_eta")
        if exp:
            fields[exp] = eta_val
        if est:
            fields[est] = eta_val
        if exp or est:
            print(f"  Expected/Estimated ETA: {eta_val}")
    print(f"  component: {component}")

    issue_body = {"fields": fields}

    if dry_run:
        print("  [DRY-RUN] 将 POST 以下内容（不实际创建）:")
        print("  " + json.dumps(issue_body, ensure_ascii=False, indent=4).replace("\n", "\n  "))
        return None

    result = jira_request(
        cfg["JIRA_BASE_URL"], cfg["JIRA_TOKEN"],
        "POST", "/rest/api/2/issue", issue_body
    )
    issue_key = result.get("key", "?")
    issue_url = f"{cfg['JIRA_BASE_URL']}/browse/{issue_key}"
    print(f"  ✓ 已创建: {issue_key}")
    print(f"  URL: {issue_url}")
    return issue_url


# ─────────────────────────────────────────────────────────────
# 入口
# ─────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Jira REST API 连通性与建单测试",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--dry-run", action="store_true",
                        help="只验证鉴权和 issue 类型，不实际创建 issue")
    parser.add_argument("--title", default="[New] TestApp 【新发布项目】",
                        help='issue 标题（默认: "[New] TestApp 【新发布项目】"）')
    parser.add_argument("--config", default="jira.conf",
                        help="配置文件路径（默认: jira.conf，相对于脚本目录）")
    args = parser.parse_args()

    print("=" * 60)
    print("  Jira API 测试脚本")
    print("=" * 60)

    cfg = load_config(args.config)
    print(f"  Base URL : {cfg['JIRA_BASE_URL']}")
    print(f"  Project  : {cfg['JIRA_PROJECT']}")
    print(f"  Assignee : {cfg.get('JIRA_ASSIGNEE') or '(未配置)'}")
    parent = cfg.get("JIRA_PARENT_ISSUE", "").strip()
    print(f"  Parent   : {parent or '(无，创建顶级 Task)'}")
    print(f"  Title    : {args.title}")
    print(f"  Dry-run  : {'是' if args.dry_run else '否（将真实建单）'}")
    print()

    step_check_auth(cfg)
    types      = step_discover_issue_types(cfg)
    eta_fields = step_discover_eta_fields(cfg)
    url        = step_create_issue(cfg, types, args.title, args.dry_run, eta_fields)

    print()
    if url:
        print(f"✓ SUCCESS — 已建单: {url}")
    else:
        print("✓ SUCCESS — 鉴权通过 (dry-run，未建单)")


if __name__ == "__main__":
    main()
