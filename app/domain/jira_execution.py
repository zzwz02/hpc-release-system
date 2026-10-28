"""Execution configuration and environment checks shared by Jira and test runs."""
from __future__ import annotations

import hashlib
import json
import posixpath

MAX_TARGETS = 32  # bound interactive key uploads and remote probes per submission


def digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def normalize(value: dict | None = None, *, machine: str = "") -> dict:
    from app.domain.jira_agent import parse_ssh_target
    value = value if value is not None else {
        "mode": "custom" if machine else "auto", "targets": [{"target": machine}] if machine else [],
    }
    mode = value.get("mode", "auto")
    policy = value.get("selection_policy", "as_needed")
    requirement = value.get("dataset_requirement", "auto")
    if mode not in ("auto", "custom") or policy not in ("all_required", "as_needed"):
        raise ValueError("请选择有效的机器来源与使用方式")
    if requirement not in ("auto", "required", "not_required"):
        raise ValueError("请选择有效的数据依赖")
    targets = []
    seen = set()
    for item in value.get("targets", []):
        target = parse_ssh_target(item.get("target", ""))
        user, host = target.split("@")
        target = f"{user}@{host.lower()}"
        if target in seen:
            raise ValueError(f"机器重复：{target}")
        seen.add(target)
        purpose = str(item.get("purpose", "")).strip()
        if len(purpose) > 500:
            raise ValueError("机器用途不能超过 500 字")
        targets.append({"target": target, "purpose": purpose})
    if mode == "auto" and targets:
        raise ValueError("自动模式不能指定机器")
    if mode == "custom" and not 1 <= len(targets) <= MAX_TARGETS:
        raise ValueError(f"请指定 1 至 {MAX_TARGETS} 台机器")
    return {"mode": mode, "selection_policy": "as_needed" if mode == "auto" else policy,
            "targets": targets, "dataset_requirement": requirement}


def configuration(row: dict) -> dict:
    raw = row.get("execution_config_json")
    return normalize(json.loads(raw) if raw else None, machine=row.get("machine", ""))


def legacy_machine(config: dict) -> str:
    targets = config["targets"]
    return targets[0]["target"] if config["mode"] == "custom" and len(targets) == 1 else ""



def legacy_snapshot(machine: str, machines: list[dict]) -> dict:
    targets = [machine] if machine else [m["ssh_target"] for m in machines]
    unknown = {"status": "unknown", "code": "historical", "message": "历史任务尚无环境检查记录"}
    return {"results": [{"target": target, "ssh": unknown, "dataset": unknown} for target in targets],
            "available_targets": targets, "dataset_path": "", "warning_ids": []}


def system_snapshot(targets: list[dict]) -> dict:
    return {"results": targets, "available_targets": [t["target"] for t in targets],
            "can_submit": bool(targets), "warning_ids": [], "requires_acknowledgement": False}


def dataset_policy(values: dict) -> dict:
    path = values.get("DATASET_PATH", "").strip()
    probe = values.get("DATASET_PROBE_RELATIVE_PATH", "").strip()
    expected = values.get("DATASET_PROBE_EXPECTED_VALUE", "").strip()
    try:
        sources = json.loads(values.get("DATASET_EXPECTED_SOURCES", "[]") or "[]")
    except ValueError as exc:
        raise ValueError("DATASET_EXPECTED_SOURCES 必须是 JSON 字符串数组") from exc
    if not isinstance(sources, list) or any(not isinstance(s, str) or not s.strip() for s in sources):
        raise ValueError("DATASET_EXPECTED_SOURCES 必须是非空来源的 JSON 数组")
    if path and (not path.startswith("/") or "\x00" in path):
        raise ValueError("DATASET_PATH 必须是绝对路径")
    if probe and (probe.startswith("/") or ".." in probe.split("/") or "\x00" in probe):
        raise ValueError("DATASET_PROBE_RELATIVE_PATH 必须是数据目录内的相对路径")
    if not path and (sources or probe or expected):
        raise ValueError("配置数据检查前必须填写 DATASET_PATH")
    if len(expected.encode()) > 4096:
        raise ValueError("DATASET_PROBE_EXPECTED_VALUE 不能超过 4096 字节")
    if expected and not probe:
        raise ValueError("DATASET_PROBE_EXPECTED_VALUE 需要探测文件路径")
    return {"path": posixpath.normpath(path) if path else "", "sources": sources,
            "probe": probe, "expected": expected}


def verdict(config: dict, results: list[dict]) -> dict:
    available = [r["target"] for r in results if r["ssh"]["status"] == "pass"]
    all_required = config["selection_policy"] == "all_required"
    blocked = not available or (all_required and len(available) != len(results))
    warnings = [f"{r['target']}:{check}" for r in results for check in ("ssh", "dataset")
                if r[check]["status"] != "pass"
                and not (check == "dataset" and (config["mode"] != "custom" or config["dataset_requirement"] == "not_required"))]
    return {"can_submit": not blocked, "warning_ids": warnings,
            "available_targets": available, "requires_acknowledgement": bool(warnings)}


def instructions(config: dict, snapshot: dict) -> str:
    if config["mode"] == "auto":
        from app.domain.jira_agent import machine_instructions
        return machine_instructions("", [{"ssh_target": r["target"], "description": r.get("purpose", "")}
                                          for r in snapshot.get("results", [])])
    rows = "\n".join(f"| `{r['target']}` | {r['ssh']['message']} | {r['dataset']['message']} |"
                     for r in snapshot.get("results", []))
    data_text = f"数据依赖：{config['dataset_requirement']}。数据目录：{snapshot.get('dataset_path') or '未配置'}。\n"
    check_header = "| 机器 | SSH | 共享数据 |\n| --- | --- | --- |"
    purposes = "\n".join(f"- {t['target']}：{t['purpose']}" for t in config["targets"] if t["purpose"])
    allowed = snapshot.get("available_targets", [])
    if len(config["targets"]) == 1:
        selection = f"用户指定的机器 `ssh {config['targets'][0]['target']}`。"
    else:
        selection = "用户指定的机器清单："
    listing = "\n".join("| `" + r["target"] + "` | " + " ".join(r.get("purpose", "").split()).replace("|", "\\|") + " |" for r in snapshot.get("results", []))
    return (
        f"执行配置（本轮授权与检查记录）：{selection}\n| 机器 | 说明 |\n| --- | --- |\n{listing}\n"
        f"允许连接的目标：{json.dumps(allowed, ensure_ascii=False)}。只在这些目标及账号上开展实验。\n"
        f"机器使用方式：{'全部需要；多机实验不可省略节点' if config['selection_policy'] == 'all_required' else '按任务需要选择一台或多台；不能降低原任务所需节点数'}。\n"
        f"{data_text}{purposes}\n{check_header}\n{rows}\n"
        "预检查通过不等于测试输入齐全。实验前复核实际容器内路径、数据版本与文件可读性；"
        "多节点实验还需验证节点通信、软件环境和资源。数据异常时仅继续不受影响的工作；"
        "用户同意继续不代表数据可用，不得用不同输入冒充验证。需要协助时记录具体机器和缺项，"
        "action_required=needs_help。按固定顺序取得多机 GPU 锁，失败释放已持有资源。\n"
        "已授权源码与制品服务按项目根 AGENTS.md 只读访问，服务主机不作为实验机器。\n"
        "按本轮 outputSchema 输出；包含 machines_used 时列出实际使用的全部 user@host（未使用则 []），旧 schema 不增添字段；machine 仅在恰好使用一台时填写，"
        "否则留空。逐台说明环境、命令与结果；预检查机器不计入实际实验机器。"
    )
