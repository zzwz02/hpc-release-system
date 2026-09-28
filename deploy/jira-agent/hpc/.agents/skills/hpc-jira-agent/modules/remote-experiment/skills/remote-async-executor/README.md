# remote-async-executor

简短中文名：远程执行器

## 基本信息

- 作者：PDE-AI Solution-AI Enablement
- 版本号：1.0.0

## 这是什么

`remote-async-executor` 是一个通用远程执行与监控底座，用来把本地开发中的功能、脚本或 skill，同步到指定远程服务器后异步执行，并在执行过程中持续查看状态、日志，最终把结果和产物回收到本地。

它更适合“一次性远程异步任务”场景，例如：

- 远程测试新开发的 skill 或功能
- 在远程宿主机或容器里跑脚本
- 执行长时间任务并持续跟踪状态
- 回收日志、报告、结果文件等 artifacts

## 核心能力

- 将本地改动同步到指定远程服务器
- 支持在宿主机或容器中执行任务
- 默认按异步任务方式运行，支持长任务托管
- 提供稳定的 `job_id`
- 支持 `submit`、`status`、`logs`、`result`、`cancel`
- 支持把远程执行结果和 artifacts 拉回本地

## 它不负责什么

- 不负责定义业务测试逻辑
- 不负责自动猜测测试命令
- 不负责复杂调度或工作流编排

换句话说，它负责“怎么远程跑、怎么记录、怎么回收”，而具体“跑什么”由目标功能或目标 skill 提供。

## 快速入口

建议从下面这些文件开始看：

- [SKILL.md](SKILL.md)：完整说明、触发方式、关键约定
- [requirements.txt](requirements.txt)：本地 Python 依赖
- [references/remote-test-yaml-example.yaml](references/remote-test-yaml-example.yaml)：可直接复制修改的配置模板
- [references/remote-test-yaml-guide.md](references/remote-test-yaml-guide.md)：YAML 字段说明
- [references/integration-checklist.md](references/integration-checklist.md)：给其他 skill 接入时的检查清单

## 本地脚本

当前目录下的 `scripts/` 提供了本地控制层脚本：

- `submit-example.py`：提交远程任务
- `status-example.py`：查询任务状态
- `logs-example.py`：查看远程日志
- `result-example.py`：获取最终结果和 artifacts
- `cancel-example.py`：取消正在运行的任务

推荐调用顺序：

```text
submit -> status/logs -> result
```

如果需要中途中断：

```text
submit -> status/logs -> cancel
```
