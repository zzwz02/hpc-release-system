# 接入清单

本清单用于说明：如果另一个 skill 或功能流程要接入 `remote-async-executor`，需要提前准备哪些内容。

本清单不引入新的协议字段，也不改变现有执行模型。
它只是把当前已经存在的要求收敛成一份更适合落地执行的列表。

## 阅读建议

如果你是第一次接入 `remote-async-executor`，建议这样看文档：

- 先看 [../SKILL.md](../SKILL.md) 了解整体定位和能力边界
- 再看 [remote-test-yaml-example.yaml](remote-test-yaml-example.yaml) 准备配置模板
- 对字段有疑问时查 [remote-test-yaml-guide.md](remote-test-yaml-guide.md)
- 最后回到当前清单做接入前自检

## 适用对象

适用于以下场景：

- 你正在开发一个新的 skill，希望它能调用 `remote-async-executor` 在远程环境测试
- 你正在开发某个功能，希望把远程测试、进度查询、日志回收、结果回收接入统一流程
- 你希望把“测试逻辑”和“远程执行/监控逻辑”分离

## 接入目标

接入完成后，目标 skill 或目标功能应具备这些能力：

- 本地改动可同步到用户指定的远程服务器
- 测试命令可在宿主机或容器内异步执行
- 可获得稳定的 `job_id`
- 可查询 `status`
- 可查询 `logs`
- 可获取 `result`
- 可执行 `cancel`

同时要明确本技能的任务模型：

- 一次 `submit` 对应一次独立远程任务
- 多次修改代码后再次测试，应提交多个独立任务轮次
- 不应假设同一个远程任务会在运行中持续接收新的本地改动

## 必备交付物

要接入 `remote-async-executor`，至少需要准备以下内容：

### 1. 目标目录

需要明确：

- 被测试功能所在目录，或
- 被测试 skill 所在目录

这是默认本地记录目录 `.remote-test-jobs` 的归属位置。

### 2. `remote-test.yaml`

必须提供一份 `remote-test.yaml`。

可直接参考：

- [remote-test-yaml-example.yaml](remote-test-yaml-example.yaml)
- [remote-test-yaml-guide.md](remote-test-yaml-guide.md)

最低必填字段：

- `sync.remote_workspace`
- `execution.target`
- `execution.host`
- `execution.port`
- `execution.user`
- `execution.auth_mode`
- `test.command`

当 `execution.target=container` 时还必须提供：

- `container.name`
- `container.workdir`

### 3. 明确的测试命令

必须准备一条明确、可执行、可复现的测试命令。

推荐要求：

- 可以直接在 shell 中执行
- 不依赖模糊的自然语言解释
- 最好已有稳定脚本入口，例如：
  - `bash scripts/test.sh`
  - `python3 scripts/run_smoke_test.py`

不推荐：

- “运行一下核心流程”
- “验证新功能是否正常”
- “按 README 执行测试”

### 4. 远程执行环境信息

必须明确远程执行环境：

- 远程主机地址
- SSH 端口
- SSH 用户
- 认证方式
- 宿主机工作目录
- 如果走容器模式，还要明确容器名称和容器内工作目录

如果这些信息本身不稳定，目标 skill 不应把它们写死在自由文本说明里，而应写入 `remote-test.yaml`。

### 5. 产物定义

如果需要回收业务日志、报告、结果文件，必须提前定义 `artifacts`。
远程 job 目录下的 `stdout.log` 和 `stderr.log` 属于框架内建日志，不需要写进 `artifacts`。

常见产物包括：

- `logs/test.log`
- `reports/result.json`
- `output/benchmark.md`

如果不定义，`result` 阶段就无法稳定回收这些文件。

## 推荐准备项

虽然不是绝对必需，但强烈建议提前准备：

### 1. 稳定的测试脚本入口

例如：

- `scripts/test.sh`
- `scripts/test_host.sh`
- `scripts/run_remote_eval.sh`

这样做的好处：

- 测试命令更稳定
- 更适合反复迭代
- 更容易排查失败

### 2. 明确容器挂载关系

如果是容器模式，应确认：

- 宿主机 `remote_workspace`
- 容器 `container.workdir`

二者确实通过 volume 挂载关联。

否则很容易出现：

- 文件已经同步到宿主机
- 但容器内根本看不到最新代码

### 3. 明确超时预期

建议填写：

- `execution.timeout_hint_sec`

这样后续在状态展示、任务判断和用户沟通中会更清晰。

## 接入前自检

在正式接入前，建议逐项确认：

- 我已经有目标目录
- 我已经有 `remote-test.yaml`
- YAML 中最小必填字段已经齐全
- 如果服务器不是默认 22 端口，`execution.port` 已正确填写
- 如果不是 SSH key 登录，`execution.auth_mode` 已正确配置
- 如果是容器模式，`container.name` 和 `container.workdir` 已填写
- `test.command` 是明确可执行的命令
- 远程服务器可通过 SSH 访问
- 远程宿主机目录可写
- 如果使用容器，目标容器存在且可执行命令
- 如果需要结果回收，`artifacts` 路径已定义

## 接入时应如何调用

目标 skill 不需要自己实现远程任务状态协议。
目标 skill 应做的是：

- 提供 `remote-test.yaml`
- 触发 `remote-async-executor` 的 `submit`
- 在需要时调用 `status`
- 在需要时调用 `logs`
- 在任务结束后调用 `result`
- 在需要中断时调用 `cancel`

为了减少歧义，建议目标 skill 在提示 agent 时不要只写裸词 `submit`、`status`、`logs`、`result`、`cancel`，而应显式带上技能名。

推荐写法：

```text
remote-async-executor:submit <target-name>
remote-async-executor:status <job_id>
remote-async-executor:logs <job_id>
remote-async-executor:result <job_id>
remote-async-executor:cancel <job_id>
```

如果使用自然语言，也建议写成：

- 使用 `remote-async-executor` 提交 `<目标 skill / 功能名>` 的远程测试
- 使用 `remote-async-executor` 查询 `job_id=<...>` 的状态
- 使用 `remote-async-executor` 查看 `job_id=<...>` 的日志
- 使用 `remote-async-executor` 获取 `job_id=<...>` 的结果
- 使用 `remote-async-executor` 取消 `job_id=<...>` 的任务

建议补足的上下文是：

- `submit`：至少给出目标 skill / 功能名，最好同时给出 `remote-test.yaml` 路径
- `status` / `logs` / `result` / `cancel`：至少给出 `job_id`

换句话说：

- 目标 skill 负责“测什么”
- `remote-async-executor` 负责“怎么远程跑、怎么记录、怎么回收”

## 不应该由目标 skill 负责的内容

以下内容不应由目标 skill 自己重新实现：

- 自定义一套远程 `status.json` 协议
- 自己维护 heartbeat 机制
- 自己定义与 `remote-async-executor` 不一致的 `job_id` 规则
- 自己把代码直接复制进容器文件系统作为主流程
- 自己重新发明日志抓取和结果抓取格式
- 自己假设“同一个远程任务可以被反复覆盖提交并继续运行”

这些都应复用 `remote-async-executor` 的现有约定。

## 常见接入错误

### 1. 只有测试意图，没有测试命令

例如：

- “验证功能”
- “跑一下这个 skill”

这不足以接入，必须落成 `test.command`。

### 2. 容器路径和宿主机路径不匹配

例如：

- YAML 写了宿主机路径
- 但容器里挂载的是另一套目录

这样会导致远程同步成功，但测试仍读取旧代码。

### 3. 忘记排除 `.remote-test-jobs`

如果不排除，本地缓存可能会被再次同步到远程，污染工作目录。

### 4. `artifacts` 路径与真实输出不一致

这样即使测试成功，`result` 也可能抓不到结果文件。

### 5. 把远程执行逻辑写进目标 skill 文本里，而不是 YAML

远程执行相关配置应尽量进入 `remote-test.yaml`，而不是散落在自然语言描述中。

### 6. 误以为同一个远程任务会自动接收后续本地改动

当前模型是“多轮独立提交”，不是“同一个任务热更新续跑”。
如果本地代码又改了，应重新 `submit`，生成新的 `job_id`。

## 最小接入示例

一套最小接入通常只需要：

1. 准备目标目录
2. 写好 `remote-test.yaml`
3. 确保远程服务器和容器环境可用
4. 用 `submit` 提交
5. 用 `status/logs/result` 跟进

## 相关参考

- YAML 模板：[remote-test-yaml-example.yaml](remote-test-yaml-example.yaml)
- YAML 字段说明：[remote-test-yaml-guide.md](remote-test-yaml-guide.md)
- Wrapper 协议：[wrapper-contract.md](wrapper-contract.md)
- Run Script 协议：[run-script-contract.md](run-script-contract.md)
- Submit 原型：[../scripts/submit-example.py](../scripts/submit-example.py)
- Status 原型：[../scripts/status-example.py](../scripts/status-example.py)
- Logs 原型：[../scripts/logs-example.py](../scripts/logs-example.py)
- Result 原型：[../scripts/result-example.py](../scripts/result-example.py)
- Cancel 原型：[../scripts/cancel-example.py](../scripts/cancel-example.py)
