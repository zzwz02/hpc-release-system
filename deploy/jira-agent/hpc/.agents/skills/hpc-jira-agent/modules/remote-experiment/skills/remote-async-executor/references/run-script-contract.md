# Run Script 协议

`run.sh` 是由本地控制层为每个远程任务渲染出的、任务专属的执行入口脚本。

## 作用

`run.sh` 包含一次具体任务需要执行的精确命令。它被有意与 wrapper 分离，以便：

- wrapper 能保持可复用且稳定
- 执行细节保持简单，并只作用于当前任务
- 不同目标 skill 可以复用同一套 wrapper 协议

## 范围

`run.sh` 只应做在正确位置执行目标测试命令所必需的最小工作。

它不应负责：

- 写入 `status.json`
- 写入 `result.json`
- 管理 heartbeat
- 实现通用生命周期状态流转

这些职责属于 wrapper。

## 必需行为

`run.sh` 应当：

- 使用 `bash`
- 在命令出错时失败退出
- 切换到正确的工作目录
- 执行 `remote-test.yaml` 中声明的命令
- 以实际命令退出码退出

推荐 shell 选项：

```bash
#!/usr/bin/env bash
set -euo pipefail
```

## 宿主机模式

在宿主机模式下，`run.sh` 应在宿主机工作目录执行。

示例：

```bash
#!/usr/bin/env bash
set -euo pipefail

cd /home/dev/work/my-new-skill
bash scripts/test.sh
```

## 容器模式

在容器模式下，`run.sh` 应通过 `docker exec` 在容器内挂载工作目录执行。

示例：

```bash
#!/usr/bin/env bash
set -euo pipefail

docker exec my-dev-container bash -lc 'cd /workspace/my-new-skill && bash scripts/test.sh'
```

## 输入来源

本地控制层应基于以下字段渲染 `run.sh`：

- `execution.target`
- `execution.workdir`
- `container.name`
- `container.workdir`
- `test.command`

这些字段来自用户提供的 `remote-test.yaml` 以及本次用户显式覆盖项。

## 与 Wrapper 的关系

正常启动关系如下：

1. 本地控制层上传或渲染 `run.sh`
2. 本地控制层上传 `wrapper.sh` 或引用共享版本
3. wrapper 启动
4. wrapper 执行 `run.sh`
5. wrapper 记录生命周期文件和结果文件

wrapper 应将 `run.sh` 视为该任务的唯一子命令入口。
