# 状态查询说明

`status-example.py` 是 `remote-async-executor` 的状态查询脚本，用于查看远程任务当前状态。

## 作用

该脚本演示远程状态查询流程：

- 读取本地 `submit.json`
- 解析远程任务位置
- 获取远程 `status.json`
- 获取远程 `heartbeat.txt`
- 按需检查记录中的 `tmux` session 是否仍然存在
- 在本地写入 `status.cache.json`
- 输出简短、可读的状态摘要

## 统一调用顺序

本地控制层脚本通常按下面顺序配合使用：

```text
submit -> status/logs -> result
```

如果任务需要中途中断，则改为：

```text
submit -> status/logs -> cancel
```

当前文档对应的是运行中的查询步骤：`status`。

## 用法

示例：

```bash
python scripts/status-example.py \
  --job-dir /path/to/.remote-test-jobs/jobs/<job_id>
```

或者：

```bash
python scripts/status-example.py \
  --submit-json /path/to/.remote-test-jobs/jobs/<job_id>/submit.json
```

## 依赖

该原型依赖：

- `python`
- `ssh`

如果本地存在 `python3`，它还会估算 heartbeat 的年龄，并标记为 fresh 或 stale。

## 本地输出

脚本会写入：

- `status.cache.json`

写入到所选本地任务目录下。

## 认证说明

`status-example.py` 会根据 `submit.json` 中记录的 `config_path`，回读 `remote-test.yaml` 里的认证配置。

这意味着：

- 如果提交时使用 `ssh_key`，状态查询会继续使用同一套 key 配置
- 如果提交时使用 `password_env`，查询前仍需准备对应环境变量
- 如果提交时使用 `password_prompt`，查询时会再次提示输入密码
- 如果历史提交使用 `password_inline`，本项目拒绝继续读取内联密码；需人工核对并迁移到凭据引用
