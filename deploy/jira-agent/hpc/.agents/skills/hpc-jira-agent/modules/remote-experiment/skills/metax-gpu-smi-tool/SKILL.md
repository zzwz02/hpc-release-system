---
name: metax-gpu-smi-tool
description: 查询 MetaX GPU 的设备、驱动、显存、利用率、进程、拓扑和错误状态，为远端实验记录资源条件。
---

# MetaX GPU 状态查询

## 1. 确认工具和设备

在获准测试机检查可用工具，可能为 `ht-smi` 或 `mx-smi`；先读当前版本的帮助。不要因名称不同判定 GPU 不可用，也不要假设两者参数完全一致。

## 2. 按问题观察

以下为 `mx-smi` 常用查询；其它版本按实际支持参数执行：

| 需要 | 命令 |
|---|---|
| GPU 列表与摘要 | `mx-smi -L` / `mx-smi` |
| 版本、显存和利用率 | `mx-smi --show-version` / `--show-memory` / `--show-usage` |
| 并发进程 | `mx-smi --show-process` |
| 拓扑 | `mx-smi topo -t` |
| 温度和时钟 | `mx-smi --show-temperature` / `--show-clock` |
| ECC / RAS | `mx-smi --count-ecc` / `mx-smi ras --show-count -i <GPU>` |

需要持续采样、PCIe/带宽、设备不可用原因或已单独授权的管理操作时，再查[完整命令参考](references/commands.md)。

## 3. 返回观察结果

记录采样时间、设备身份、版本、资源占用及与本案有关的异常。利用率高、显存高或一次错误计数不能单独证明根因，也不能代替 GPU 独占授权。

日常调查只读查询；复位、固件升级、时钟/功耗、虚拟化和链路配置均不在普通诊断范围，不影响他人作业。
