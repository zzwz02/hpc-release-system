# Wrapper 协议

远程 wrapper 是 `remote-async-executor` 启动的每个异步任务的稳定生命周期管理者。

## 作用

wrapper 的存在，是为了让目标 skill 和目标功能命令不必各自实现一套任务状态协议。所有远程任务都应遵循同一套状态模型、文件布局和结果契约。

## 输入

wrapper 应接收足够的信息，以便：

- 知道远程任务目录的位置
- 知道生成后的 `run.sh` 在哪里
- 知道当前任务的 `job_id`
- 知道宿主机侧工作目录路径
- 知道 artifact 相对路径应基于哪个宿主机目录解析
- 知道调用方是否通过 `tmux` 启动了它

概念性调用示例：

```bash
bash wrapper.sh <job_dir> <job_id> <run_script> [artifacts_file] [artifact_root]
```

## 必需文件

wrapper 负责在远程任务目录下创建和更新以下文件：

- `meta.json`
- `status.json`
- `stdout.log`
- `stderr.log`
- `result.json`
- `heartbeat.txt`

## 文件语义

### `meta.json`

启动时写入一次，之后除非需要补全缺失字段，否则保持稳定。

推荐字段：

```json
{
  "job_id": "20260412-153045-my-skill-a1b2c3",
  "created_at": "2026-04-12T15:30:45+08:00",
  "timezone": "Asia/Shanghai",
  "host": "10.0.0.8",
  "user": "dev",
  "execution_target": "container",
  "container_name": "my-dev-container",
  "command": "bash scripts/test.sh",
  "tmux_session": "rjob-20260412-153045-my-skill-a1b2c3"
}
```

### `status.json`

在每次阶段切换和终态退出时更新该文件。

推荐字段：

```json
{
  "job_id": "20260412-153045-my-skill-a1b2c3",
  "state": "running",
  "current_stage": "testing",
  "progress": "2/4",
  "start_time": "2026-04-12T15:30:45+08:00",
  "update_time": "2026-04-12T15:42:10+08:00",
  "exit_code": null
}
```

允许的 `state` 取值：

- `queued`
- `syncing`
- `starting`
- `running`
- `collecting`
- `succeeded`
- `failed`
- `canceled`

### `stdout.log`

追加记录目标命令的标准输出。

### `stderr.log`

追加记录目标命令的标准错误输出。

### `heartbeat.txt`

当任务仍在活动时，定时刷新该文件，例如每 15 到 30 秒一次。文件内容可以只是一个 ISO 时间戳。

### `result.json`

当被包装命令进入终态时写入一次。

推荐字段：

```json
{
  "job_id": "20260412-153045-my-skill-a1b2c3",
  "state": "succeeded",
  "exit_code": 0,
  "finished_at": "2026-04-12T16:05:01+08:00",
  "summary": "Remote test completed successfully",
  "artifacts": [
    {
      "path": "logs/test.log",
      "exists": true
    },
    {
      "path": "reports/result.json",
      "exists": true
    }
  ]
}
```

## 生命周期

wrapper 应遵循以下生命周期：

```text
queued -> starting -> running -> collecting -> succeeded|failed|canceled
```

如果控制层希望单独体现同步进度，可以在 wrapper 启动前先写 `syncing`。一旦 wrapper 启动，它就拥有从 `starting` 开始的生命周期状态控制权。

## Heartbeat

heartbeat 循环应当：

- 在目标命令开始前启动
- 在命令进入终态后停止
- 避免在子进程已退出后继续无限运行，从而掩盖失败

如果 heartbeat 已过期，但 `status.json.state` 仍显示 `running`，客户端应将其视为可疑状态并明确提示用户。

## 退出处理

wrapper 必须：

- 捕获子进程退出码
- 写入终态 `status.json`
- 写入 `result.json`
- 停止 heartbeat 更新

终态映射：

- 退出码 `0` -> `succeeded`
- 非零退出码 -> `failed`
- 被显式取消而中断或终止 -> `canceled`

## Artifact 收集

wrapper 应检查 `remote-test.yaml` 中声明的 artifact 路径，并记录各 artifact 是否存在。它不需要负责把这些文件上传到别处，是否回收由本地控制层决定。

如果 artifact 路径是相对路径，wrapper 应按宿主机侧工作区根目录解析，而不是按 wrapper 启动时的当前目录解析。对于 `submit-example.py`，这个根目录就是 `sync.remote_workspace`。

## 职责分离

wrapper 负责生命周期记录。

生成出的 `run.sh` 负责具体执行细节，例如：

- 宿主机模式：`cd <workdir> && <command>`
- 容器模式：`docker exec <container> bash -lc "cd <container_workdir> && <command>"`

目标命令不应感知这些任务状态文件的存在。
