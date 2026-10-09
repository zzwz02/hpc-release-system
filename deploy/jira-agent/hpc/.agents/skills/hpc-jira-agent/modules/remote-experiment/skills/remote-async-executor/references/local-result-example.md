# 结果回收说明

`result-example.py` 是 `remote-async-executor` 的结果回收脚本，用于获取远程结果、job 目录下的系统日志和 artifacts。

## 作用

该脚本演示远程结果回收流程：

- 读取本地 `submit.json`
- 获取远程 `result.json`
- 默认下载远程 job 目录下的 `stdout.log` 和 `stderr.log`
- 在本地写入 `result.cache.json`
- 按需将声明的 artifacts 下载到本地任务目录
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

当前文档对应的是任务结束后的结果回收步骤：`result`。

## 用法

示例：

```bash
python scripts/result-example.py \
  --job-dir /path/to/.remote-test-jobs/jobs/<job_id>
```

或者：

```bash
python scripts/result-example.py \
  --submit-json /path/to/.remote-test-jobs/jobs/<job_id>/submit.json
```

跳过 artifact 下载：

```bash
python scripts/result-example.py \
  --job-dir /path/to/.remote-test-jobs/jobs/<job_id> \
  --skip-artifacts
```

## 依赖

该原型依赖：

- `python`
- `ssh`
- `scp`

## 本地输出

脚本会写入：

- `result.cache.json`
- `stdout.log`
- `stderr.log`
- `artifacts/` when artifact download is enabled

写入到所选本地任务目录下。

## Artifact 路径与证据保护

- 以指纹已核对的 YAML `artifacts` 为允许清单，远端 `result.json` 不授予额外读取权限。结果的 job ID、产物清单和布尔 `exists` 必须匹配；缺项、额外项或重复项都会拒绝。
- 仅支持工作区内的普通文件及字面相对路径，路径段可含字母、数字、下划线和 `.@+-`。拒绝绝对路径、`.`/`..` 分段、空白、控制字符、反斜杠、通配符和 shell 元字符。其他命名或目录制品需在批准工作区准备明确的安全文件名/归档，再更新 YAML 并重新批准，不能自动猜测映射。
- 下载前在远端用 `realpath -e` 核对真实位置：业务产物必须仍在批准工作区，系统日志和结果 JSON 必须在对应 job 目录。符号链接指向目录外时拒绝；目录内链接解析到实际普通文件后回收。
- 先下载到私有临时文件，成功后通过目录文件描述符和 `O_NOFOLLOW` 在 job 目录内发布，拒绝本地符号链接和特殊文件。系统日志与产物均不覆盖已有不同内容；内容相同可复用，并记录实际 SHA-256。
- `result.cache.json` 是可刷新元数据，通过临时文件原子替换普通缓存文件，不跟随缓存符号链接。下载失败/缺失记录在各条目 `error` 中；`collection.complete=false` 且退出码为 1，不能把部分回收当成功。
- `--skip-artifacts` 仅收集日志，记录 `collection.artifacts_skipped=true`；即使远端声称已经下载，也不把它作为本地下载证据。重复收集不会重跑业务任务。

本地安全落盘需要 Linux/POSIX 的 `dir_fd`、`O_NOFOLLOW` 和硬链接能力，远端需要 `realpath`。不满足时失败，不退回不安全覆盖。远端真实路径检查与 SCP 不是一个原子操作，不能对抗检查后恶意并发替换远端目录；仍应在任务停止或可靠隔离后回收。本次修复不是同 OS 账号恶意进程下的强隔离，也未提供硬超时或下载大小上限。

## 离线验证

从组合包根目录运行：

```bash
python3 -B -m unittest discover -s modules/connectors/remote-async-executor/tests -p test_result_example.py -v
```

测试使用系统临时目录，不在 Skill 内创建工作资料。文件系统测试不需要网络；集成测试使用新生成的临时测试密钥和只绑定 `127.0.0.1` 的 sshd，实际运行 CLI、SSH 和 SFTP，不读取个人密钥或修改已有 SSH 服务。只有该临时测试 sshd 的 `StrictModes` 因 `/tmp` 父目录而关闭，客户端主机身份校验仍开启。缺少 OpenSSH server/client、`realpath` 或必要运行条件时集成测试明确跳过，不能将跳过视为通过。

## 认证说明

`result-example.py` 会沿用提交阶段记录下来的认证模式。

可选 `execution.known_hosts_path` 指向操作者已验证的独立主机公钥文件；不设置时沿用现有 SSH 默认位置。不会自动接受未知指纹或关闭客户端主机校验。

如果使用密码类模式：

- `password_env`：下载结果和 artifacts 前仍需准备环境变量
- `password_prompt`：获取结果时会再次提示输入密码
- `password_inline`：本项目拒绝；使用 SSH key 或环境变量凭据引用
