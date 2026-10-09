# remote-test.yaml 中文说明模板

本文件用于说明 `remote-async-executor` 所要求的 `remote-test.yaml` 应如何编写。
它是对示例文件 [remote-test-yaml-example.yaml](remote-test-yaml-example.yaml) 的解读，不改变任何字段契约。
当前示例文件本身已经包含中文注释，可直接复制修改。

## YAML 三件套怎么用

围绕 `remote-test.yaml`，建议这样选文档：

- [remote-test-yaml-example.yaml](remote-test-yaml-example.yaml)：适合直接复制后修改
- 当前文档：适合查字段含义、必填项和填写建议
- [integration-checklist.md](integration-checklist.md)：适合在正式接入前做自检

## 作用

`remote-test.yaml` 是远程测试配置契约，用来定义：

- 要同步哪些本地文件
- 同步到远程宿主机的哪个目录
- 在宿主机还是容器里执行
- 实际执行什么测试命令
- 最终要回收哪些 artifacts

如果没有这份 YAML，`remote-async-executor` 不应自行猜测测试逻辑。

## 最小必填项

至少应包含以下字段：

- `sync.remote_workspace`
- `execution.target`
- `execution.host`
- `execution.port`
- `execution.user`
- `test.command`

当 `execution.target=container` 时，还应提供：

- `container.name`
- `container.workdir`

## 字段说明

### 顶层字段

`skill_name`

- 用于标识当前被测试的 skill 或功能名称
- 推荐填写目录名或功能名
- 主要用于阅读和定位，不直接控制执行流程

### `sync`

定义文件同步策略。

`sync.local_paths`

- 要从本地同步的路径列表
- 通常可写为：

```yaml
local_paths:
  - .
```

`sync.exclude`

- 同步时排除的路径模式
- 推荐至少排除：
  - `.git`
  - `__pycache__`
  - `.remote-test-jobs`

`sync.remote_workspace`

- 远程宿主机上的工作区目录
- 代码会先同步到这里
- 容器通常通过挂载这个目录访问代码

`sync.transfer`

- 文件传输方式
- 推荐值：
  - `rsync`
  - `scp`
- 一般优先写 `rsync`

### `execution`

定义远程执行位置和执行方式。

`execution.target`

- 表示在何处执行测试命令
- 可选值：
  - `container`
  - `host`

`execution.use_tmux`

- 是否通过 `tmux` 托管任务
- 长时间运行任务建议设为 `true`

`execution.host`

- 远程测试服务器地址

`execution.port`

- SSH 连接端口
- 默认值通常为 `22`
- 如果远程服务器使用了非默认端口，必须显式填写

`execution.user`

- SSH 用户名

`execution.auth_mode`

- 远程认证模式
- 支持以下取值：
  - `ssh_key`
  - `password_env`
  - `password_prompt`
  - `password_inline`：上游历史接口，本项目拒绝；改用凭据引用
- 推荐顺序：
  - `ssh_key` 最推荐
  - `password_env` 适合自动化
  - `password_prompt` 适合临时人工执行
  - 本项目禁止 `password_inline`，包括临时测试

`execution.private_key_path`

- 当 `execution.auth_mode=ssh_key` 时可选
- 用于指定私钥路径
- 不填写时使用系统默认 SSH key

`execution.password_env_var`

- 当 `execution.auth_mode=password_env` 时可选
- 默认值为 `REMOTE_ASYNC_EXECUTOR_PASSWORD`

`execution.password`

- 上游历史字段，本项目禁止填写；使用 `password_env_var` 或 SSH key 引用
- 临时测试也不能填写，不得提交或共享内联密码

`execution.shell`

- 远程命令使用的 shell
- 常用值为：
  - `bash`

`execution.workdir`

- 宿主机模式下的工作目录
- 若未单独指定，一般可与 `sync.remote_workspace` 保持一致

`execution.timeout_hint_sec`

- 对任务预计时长的提示
- 当前更多用于文档表达和后续扩展，不一定被每个原型脚本严格消费

### `container`

当 `execution.target=container` 时使用。

`container.name`

- 容器名称或容器标识

`container.workdir`

- 容器内的工作目录
- 通常是宿主机 `remote_workspace` 挂载进去后的路径

### `test`

定义实际执行的测试命令。

`test.command`

- 远程实际执行的命令
- 例如：

```yaml
test:
  command: bash scripts/test.sh
```

该字段必须明确，不能依赖自由文本描述。

### `artifacts`

定义测试结束后希望回收的结果文件。

- 每个路径一般写成相对路径
- 路径应与实际测试输出保持一致
- 例如：

```yaml
artifacts:
  - logs/test.log
  - reports/result.json
```

## 容器模式示例

这是最常见的写法：

```yaml
skill_name: my-new-skill

sync:
  local_paths:
    - .
  exclude:
    - .git
    - __pycache__
    - .remote-test-jobs
  remote_workspace: /home/dev/work/my-new-skill
  transfer: rsync

execution:
  target: container
  use_tmux: true
  host: 10.0.0.8
  port: 22
  user: dev
  auth_mode: ssh_key
  private_key_path: ~/.ssh/id_rsa
  shell: bash
  workdir: /home/dev/work/my-new-skill
  timeout_hint_sec: 1800

container:
  name: my-dev-container
  workdir: /workspace/my-new-skill

test:
  command: bash scripts/test.sh

artifacts:
  - logs/test.log
  - reports/result.json
```

适用场景：

- 代码同步到宿主机
- 容器通过 volume 挂载宿主机目录
- 测试命令在容器内执行

## 宿主机模式示例

当测试命令不需要进入容器时，可以这样写：

```yaml
skill_name: my-host-test

sync:
  local_paths:
    - .
  exclude:
    - .git
    - __pycache__
    - .remote-test-jobs
  remote_workspace: /home/dev/work/my-host-test
  transfer: rsync

execution:
  target: host
  use_tmux: true
  host: 10.0.0.9
  port: 22
  user: dev
  auth_mode: password_env
  password_env_var: REMOTE_ASYNC_EXECUTOR_PASSWORD
  shell: bash
  workdir: /home/dev/work/my-host-test
  timeout_hint_sec: 1200

test:
  command: bash scripts/test_host.sh

artifacts:
  - logs/host-test.log
```

适用场景：

- 任务直接在宿主机上执行
- 不依赖容器环境

## 推荐填写原则

- `remote_workspace` 与 `execution.workdir` 尽量保持一致，减少理解成本
- 容器模式下，`container.workdir` 应与容器内挂载路径一致
- `test.command` 要尽量稳定、明确、可复现
- `artifacts` 只声明真正需要回收的业务产物，避免无意义下载
- 远程 job 目录下的 `stdout.log` 和 `stderr.log` 由框架默认回收，不需要写进 `artifacts`
- `exclude` 中要排除 `.remote-test-jobs`，避免把本地缓存再次同步回远端
- 优先使用 `auth_mode=ssh_key`
- 如果必须使用密码，使用 `password_env`；不使用 `password_inline`

## 常见错误

- 缺少 `execution.host` 或 `execution.user`
- 服务器使用了非默认 SSH 端口，但没有填写 `execution.port`
- 需要密码认证，但没有正确配置 `execution.auth_mode`
- 使用 `password_env` 却没有提供对应环境变量
- `execution.target=container` 时漏写 `container.name` 或 `container.workdir`
- `test.command` 只写了意图，没有写可执行命令
- `artifacts` 路径与真实输出路径不一致
- 容器内工作目录与宿主机挂载路径不匹配

## 与其他文件的关系

- 配置示例见 [remote-test-yaml-example.yaml](remote-test-yaml-example.yaml)
- `submit` 原型会读取该 YAML 并生成 `run.sh`
- `wrapper` 负责记录运行状态，不负责解释该 YAML 的业务含义
