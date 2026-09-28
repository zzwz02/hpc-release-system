# 取消任务说明

`cancel-example.py` 是 `remote-async-executor` 的取消任务脚本，用于中断正在运行的远程任务。

## 作用

该脚本演示远程取消流程：

- 读取本地 `submit.json`
- 在存在时停止远程 `tmux` session
- 尝试回读最新的远程 `status.json`
- 在本地写入 `cancel.cache.json`
- 输出简短摘要

## 统一调用顺序

本地控制层脚本通常按下面顺序配合使用：

```text
submit -> status/logs -> result
```

如果任务需要中途中断，则改为：

```text
submit -> status/logs -> cancel
```

当前文档对应的是中断流程：`cancel`。

## 用法

示例：

```bash
python scripts/cancel-example.py \
  --job-dir /path/to/.remote-test-jobs/jobs/<job_id>
```

或者：

```bash
python scripts/cancel-example.py \
  --submit-json /path/to/.remote-test-jobs/jobs/<job_id>/submit.json
```

## 依赖

该原型依赖：

- `python`
- `ssh`

## 本地输出

脚本会写入：

- `cancel.cache.json`

写入到所选本地任务目录下。

## 说明

- 脚本优先取消记录中的 `tmux` session。
- 如果 wrapper 能正确处理信号并更新 `status.json`，那么回读到的远程状态会体现为 `canceled`。
- 如果取消后无法回读远程状态，本地缓存仍会记录“已发送取消请求”。
## 认证说明

`cancel-example.py` 会沿用提交阶段的认证方式。

取消动作本身也需要能够登录远程机器，因此：

- `password_env`：执行取消前仍需准备密码环境变量
- `password_prompt`：取消时会再次提示输入密码
- `password_inline`：本项目拒绝，先改为凭据引用并重新核对目标；取消须有精确 job 批准
