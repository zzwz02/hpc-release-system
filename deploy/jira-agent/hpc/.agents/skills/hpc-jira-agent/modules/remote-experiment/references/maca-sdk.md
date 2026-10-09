# MACA SDK 与工具准备

需要获取 SDK、编译器或 restricted 工具，或核对诊断工具适用版本时读取。

## SDK 版本选择

当前使用 **MACA 3.0**。SDK 有 **Master、Release、Dev** 三类版本：**Release** 是实际发布版本，**Master** 是日常开发基线，Dev 只在工单或原环境明确使用时选择。复现必须匹配原问题的版本类型、分支和完整构建号；发布问题使用对应的 Release SDK，日常开发基线问题使用对应的 Master SDK。只有为验证明确假设时才在 Release 与 Master 之间做受控对照，不能用 Master、Dev 或最新包替代原发布环境；只匹配“3.0”不足以确认工具兼容。

## 获取匹配 SDK / restricted 工具

SDK 和所需编译器工具从 [MACA 3.0 制品平台](https://devopsportal.sh.metax-internal.com/?portal=maca-product#/artifactory/maca3.0/maca-master-3.0) 获取。链接指向 **Master** 目录；Release 环境需在平台中选择对应 Release 分支、完整版本及构建号，不从 Master 页面猜测 Release/Dev 的 URL 或下载直链。

1. 记录复现环境实际 SDK/compiler/runtime、驱动、VBIOS、GPU/目标架构，以及系统发行版和 CPU 架构；以实际加载组件和构建记录为准。
2. 查已有安装和本任务缓存。缺工具时，下载匹配版本的 SDK 或 **restricted 附加软件包**，不能假定基础 SDK 已带齐 restricted 工具。
3. 记录分支、完整版本/构建号、制品名称、下载地址和校验值（平台提供时核对）；解包到本任务目录，使用工具绝对路径，保留原运行环境。

| 工具 | 包内位置 | 用途 |
|---|---|---|
| `llvm-mc`、`mxobjdump` | `restricted/mxgpu_llvm/bin` | 解码 debug info、反汇编 binary/so |
| trapTool | `restricted/trapTool` | 按随包 `README.md` 执行 trap 分析 |

部分包在 `restricted/Tools/` 下放置 `mxgpu_llvm/bin` 和 `trapTool`，路径以实际包为准。先查解包目录和帮助，不能因一个默认路径不存在就认定工具缺失，也不能误用系统自带、不支持 MXC 的 LLVM。工具版本不匹配或目标不受支持时，先解决匹配问题，不把解码失败解释成 kernel 缺陷。

平台需在可访问内网的已授权环境使用。无法访问或无下载权限时，记录缺少的具体分支、构建号和工具，继续分析现有材料；不声称已取得或验证工具。

## Kernel trap 工具选择

| 版本或条件 | 选择 |
|---|---|
| Master `master-20251229-920` 及之后 | trap debug 优先使用 mcSanitizer，按匹配版本随包说明确认用法 |
| Release / Dev | 查本分支匹配版本的发布说明，不能把 Master 构建号直接当作其功能门槛 |
| 支持直接 kernel-name 日志 | 优先读取 error 日志中的 kernel 名；旧版构建 `20241210-260` 之后已具备该能力，具体分支仍需核对 |
| 手工日志与反汇编定位 | 核对 MACA SDK release 2.31.0.0 及之后、VBIOS 1.25.0 或更高的支持条件，并确认目标版本实际具备该能力 |
| mcSanitizer 不可用，或需要离线复核 | 使用匹配 restricted 工具执行 kernel trap 手工流程；trapTool 按其包内 README 使用 |

诊断步骤见 [kernel-trap-analysis](../../problem-analysis/skills/kernel-trap-analysis/SKILL.md)。没有直接 kernel-name 日志的旧环境才按需使用其旧版定位参考，不向 MACA 3.0 构建默认添加历史调试选项。
