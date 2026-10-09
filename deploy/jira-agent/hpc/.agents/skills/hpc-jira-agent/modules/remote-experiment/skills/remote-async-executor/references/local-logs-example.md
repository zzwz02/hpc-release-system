# 日志查询说明

`logs-example.py` 是 `remote-async-executor` 的日志查询脚本，用于抓取远程任务日志。

## 作用

该脚本演示远程日志抓取流程：

- 读取本地 `submit.json`
- 获取远程 `stdout.log` 的 tail
- 获取远程 `stderr.log` 的 tail
- 合并写入本地 `latest.log`
- 输出简短摘要和本次抓取到的 tail

## 统一调用顺序

本地控制层脚本通常按下面顺序配合使用：

```text
submit -> status/logs -> result
```

如果任务需要中途中断，则改为：

```text
submit -> status/logs -> cancel
```

当前文档对应的是运行中的日志查询步骤：`logs`。

## 用法

示例：

```bash
python scripts/logs-example.py \
  --job-dir /path/to/.remote-test-jobs/jobs/<job_id>
```

或者：

```bash
python scripts/logs-example.py \
  --submit-json /path/to/.remote-test-jobs/jobs/<job_id>/submit.json
```

自定义 tail 行数：

```bash
python scripts/logs-example.py \
  --job-dir /path/to/.remote-test-jobs/jobs/<job_id> \
  --lines 100
```

## 依赖

该原型依赖：

- `python`
- `ssh`

## 本地输出

脚本会写入：

- `latest.log`

写入到所选本地任务目录下。

## 认证说明

`logs-example.py` 会沿用 `submit.json` 关联配置文件中的认证方式，不需要额外传认证参数。

使用密码类模式时要注意：

- `password_env`：拉日志前仍需保证环境变量存在
- `password_prompt`：拉日志时会再次提示输入密码
- `password_inline`：本项目拒绝；使用 SSH key 或环境变量凭据引用
