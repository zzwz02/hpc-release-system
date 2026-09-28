# 提交任务说明

`submit-example.py` 是 `remote-async-executor` 的提交任务脚本，用于把本地改动同步到远端并启动异步任务。

## 作用

该脚本演示一条端到端的提交流程：

- 读取 `remote-test.yaml`
- 生成 `job_id`
- 在本地创建 `.remote-test-jobs` 记录
- 将代码同步到远程主机
- 上传 `run.sh`、`wrapper.sh` 和 artifact 元数据
- 通过 `tmux` 启动远程 wrapper

## 统一调用顺序

本地控制层脚本通常按下面顺序配合使用：

```text
submit -> status/logs -> result
```

如果任务需要中途中断，则改为：

```text
submit -> status/logs -> cancel
```

当前文档对应的是第一步：`submit`。

它被有意设计成原型：

- 它更偏向可读性，而不是边界条件覆盖
- 它假设本地具备可用的 Python 环境
- 它依赖 `python`、`ssh`，以及 `rsync` 或 `scp` 之一
- 如果要解析 `remote-test.yaml`，还需要先安装 [requirements.txt](../requirements.txt) 中的 Python 依赖

推荐先执行：

```bash
python -m pip install -r remote-async-executor/requirements.txt
```

## 用法

示例：

```bash
python scripts/submit-example.py \
  --target-dir /path/to/skill-or-feature \
  --config /path/to/remote-test.yaml
```

历史 CLI 参数保留如下；本项目拒绝 host/port/user 覆盖，须修改 YAML 并重新批准。record-dir/job-label 可用：

```bash
  --host 10.0.0.8
  --port 2222
  --user dev
  --record-dir /path/to/custom/.remote-test-jobs
  --job-label my-skill
```

## 本地输出

脚本会写入：

- `submit.json`
- `run.sh`
- `artifacts.txt`

目录位置：

```text
<record_dir>/jobs/<job_id>/
```

## 认证方式

`submit-example.py` 会读取 `remote-test.yaml` 中 `execution` 段定义的认证方式。

支持模式：

- `ssh_key`
- `password_env`
- `password_prompt`
- `password_inline` 为上游历史模式，本项目拒绝

推荐顺序：

- `ssh_key`：最推荐，适合长期使用和自动化
- `password_env`：适合 CI 或本地自动化，不把密码写进 YAML
- `password_prompt`：适合人工临时执行
- `password_inline`：禁止使用，包括临时测试；用凭据引用替代

常见前置要求：

- `ssh_key`：可选填写 `execution.private_key_path`，不填则走系统默认 SSH key
- `password_env`：需要提前设置 `execution.password_env_var` 指向的环境变量，默认是 `REMOTE_ASYNC_EXECUTOR_PASSWORD`
- `password_prompt`：脚本运行时会提示输入密码
- 不填写 `execution.password`；提交须有 [project-integration.md](project-integration.md) 中的精确动作批准

## 说明

- 生成的 `run.sh` 会上传到远程任务目录，并由 wrapper 执行。
- 本地保留一份 `run.sh` 有助于排查“这次究竟提交了什么”。
- 本文件说明 submit；配套 status/logs/result/cancel 脚本已随完整目录迁入，参见主 Skill 导航。生产停机证明等限制见接入契约。
