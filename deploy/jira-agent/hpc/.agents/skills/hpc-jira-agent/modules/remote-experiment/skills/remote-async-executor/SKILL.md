---
name: remote-async-executor
description: 使用已配置的 remote-test.yaml 将一次性任务提交到获准远端主机或容器，跟踪状态和日志并回收产物。
---

# 远端异步任务

用于需要 job ID、持续监控和产物回收的长任务。普通 SSH 实验可直接由[远端实验模块](../../SKILL.md)执行。

## 1. 准备

先读[接入契约](references/project-integration.md)，核对任务 staging、`remote-test.yaml`、获准主机/目录/命令和预期产物。已有配置直接复用；缺少配置时按[总入口缺失条件规则](../../../../SKILL.md#缺失条件的处理)处理，不让该工具的缺口阻断其他可行方法。

- YAML 模板与字段：[示例](references/remote-test-yaml-example.yaml)、[字段说明](references/remote-test-yaml-guide.md)；集成其他任务时读[接入清单](references/integration-checklist.md)。
- `submit` / `cancel` 需要外部提供、匹配配置指纹和精确目标的 `REMOTE_EXECUTION_APPROVAL_FILE`。不改 CLI 目标覆盖批准，不自造批准文件。
- 只同步无凭据的专用 staging；`local_paths` 当前必须为 `[.]`。rsync/scp 差异、known_hosts 和容器要求以接入契约为准。Python 依赖见 [requirements.txt](requirements.txt)。

## 2. 提交、观察、回收

从本 Skill 目录执行：

```bash
python3 scripts/submit-example.py --target-dir <staging> --config <approved-yaml>
python3 scripts/status-example.py --job-dir <返回的job_dir>
python3 scripts/logs-example.py --job-dir <返回的job_dir>
python3 scripts/result-example.py --job-dir <返回的job_dir>
```

| 动作 | 必须保留或核对 | 详细参考 |
|---|---|---|
| submit | 新 job ID、配置指纹、命令和目标；新改动另交新任务 | [提交](references/local-submit-example.md) |
| status / logs | 远端状态、心跳和 stdout/stderr；本地只作缓存 | [状态](references/local-status-example.md)、[日志](references/local-logs-example.md) |
| result | job ID、退出码、批准产物清单、实际路径与回收完整性 | [回收](references/local-result-example.md) |
| cancel | 精确 job 的取消授权与实际进程停止情况 | [取消](references/local-cancel-example.md) |

## 3. 判断结果

远端任务目录是状态来源；`run.sh` 执行目标命令，[wrapper](references/wrapper-contract.md)记录生命周期，生成规则见 [run-script-contract](references/run-script-contract.md)。缺少目标容器时失败，不能静默改为宿主执行。

业务命令失败看真实退出码和日志；SSH 中断、心跳过期或结果文件缺失属于基础设施异常线索，不能据此认定任务已停止。`cancel_sent`、tmux 消失或 wrapper 终态也不证明所有后代进程停止，复用资源前核实。`timeout_hint_sec` 只是提示，不是强超时。

回收结果交回当前调查；wrapper 成功不代表业务验收通过，不自动重交失败任务。
