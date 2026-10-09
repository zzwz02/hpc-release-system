---
name: remote-experiment
description: 在获准远端环境准备镜像与源码，执行 HPC 实验，记录环境身份、命令、输出、退出码和资源状态。
---

# 远端 HPC 实验

输入：本次获准主机与 SSH 身份、可写目录、原命令、所需镜像/源码。产出：可复建的环境说明和实验记录。控制环境与 GPU 测试机分开，GPU 查询、镜像准备和实验在测试机执行。

## 1. 准备环境

- 实验只在网站本轮提示中的执行机与账号上进行，复用该执行账号已有 SSH key 和 known_hosts；远端目录为 `/tmp/hpc-jira-agent/<对话工作目录名>/`。新建容器使用 `hpc_jira_` 前缀并带工单、对话和实验标识，只操作本轮确切容器。
- 核对实际 GPU 工具（可能是 `mx-smi`）、设备映射、video 组及 `MACA_VISIBLE_DEVICES`；按平台和原案准备，不照抄控制环境。
- 资源先查网站同步的工单材料；缺少应用发布版本、源码或 owner 时查[发布目录](references/release-catalog.md)。发布目录、源码仓库和制品平台属于资源服务，其只读范围遵守项目根 `AGENTS.md`；服务主机不能代作实验执行机。发布记录是资源和职责线索，不是根因证据；guest 账号不代表 Harbor 权限。
- 获取 MACA SDK、编译器或 restricted 工具，以及核对版本和诊断工具适用条件时，读[MACA SDK 与工具准备](references/maca-sdk.md)。

| 需要 | 子 Skill |
|---|---|
| 拉取镜像并确认来源、digest 和平台 | [docker-pull](skills/docker-pull/SKILL.md) |
| 获取指定源码版本 | [git-clone](skills/git-clone/SKILL.md) |
| 按当前问题查询 MetaX 内网官方文档 | [metax-help-docs](skills/metax-help-docs/SKILL.md) |
| 查询 GPU、驱动、资源和并发状态 | [metax-gpu-smi-tool](skills/metax-gpu-smi-tool/SKILL.md) |
| 用结构化作业协议提交、监控和回收长任务 | [remote-async-executor](skills/remote-async-executor/SKILL.md) |

普通 SSH 或已有可追踪后台任务可直接使用；异步组件的 YAML 和批准文件仅在选择该组件时需要。

## 2. 执行实验

1. 保留原命令，注明挂载、GPU 编号、日志路径等必要调整。附件和原始源码保留未修改副本，候选改动单独保存。
2. 每组对照单独保存输出，记录固定项、变化项及实际加载组件。按实验目的隔离或核验缓存，不清理共享缓存。
3. 保存应用实际退出码；管道不能用 `tee` 的退出码代替。后台任务记录任务/容器 ID、日志位置和最终状态。
4. 所有占用 GPU 的构建和测试按项目根 `AGENTS.md` 使用目标机 `/tmp/hpc-jira-agent-locks/<机器地址>-gpu<N>.lock` 的 `flock` 锁，后台任务也需在实际运行期间持锁。锁超时将 `action_required` 设为 `needs_help`，在 `assistance` 写明所需资源，不能绕过。记录并发与干扰，不能停止他人作业，也不能把获准共享测试当作无干扰测量。

## 可复建的环境说明

按本案需要记录以下配置，未知项写明未知：

| 层次 | 必要信息 |
|---|---|
| 硬件与资源 | GPU 型号、数量、显存和可用资源；影响结果时补充拓扑、CPU/内存、并发状态 |
| 系统与驱动 | 宿主 OS/内核和 GPU 驱动；容器 OS 单列 |
| 应用与软件栈 | 应用版本/commit、输入身份、SDK 构建号、相关 compiler/UMD/库；替换组件记录来源及实际加载版本或摘要 |
| 执行条件 | 镜像地址与 digest，或无容器安装来源；设备映射、挂载、必要变量、构建和运行参数 |

应用发布版本、镜像 tag 和容器 SDK 分开记录，good/bad 标签映射到具体身份。区分实测配置、已确认必要条件和未验证替代配置；主机 IP 相同或 GPU/SDK 相同均不证明环境等价。

主机地址和容器名留在执行记录；对外交付给出准备方法，不依赖原调查容器。只采集相关环境变量，不输出完整 `export` 或凭据。资源无法取得时按[缺失条件规则](../../SKILL.md#缺失条件的处理)记录影响，继续可行的独立实验。
