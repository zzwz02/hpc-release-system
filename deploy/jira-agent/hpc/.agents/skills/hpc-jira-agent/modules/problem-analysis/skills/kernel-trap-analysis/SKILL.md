---
name: kernel-trap-analysis
description: 定位 MACA GPU kernel trap。用于日志出现 traping kernel name、trapping kernelName、debug info、Xnack 地址转换异常或 kernel 除零等设备异常时，匹配 SDK 工具，选择 mcSanitizer、trapTool 或手工反汇编，定位 kernel、所属 binary/so 和候选源码范围。
---

# MACA Kernel Trap 定位

从本次异常日志定位 kernel，再把 debug info 与同一构建的设备代码及源码对应起来。沿用 [crash-analysis](../crash-analysis/SKILL.md) 的故障记录和原案验证要求；仅有卡死或 CPU 崩溃而没有设备 trap 线索时，先使用该通用分析方法。

开始前按[MACA SDK 与工具准备](../../../remote-experiment/references/maca-sdk.md)核对版本、获取匹配工具并选择 mcSanitizer、trapTool 或手工路径；以下为日志与反汇编定位流程。

先区分显式 trap/assert、异常触发和 debug mode 单步。trap kernel 是处理入口，待定位的是触发它的业务 kernel；进入处理入口本身不证明业务出错。需要判断触发原因、异常开关或日志覆盖范围时，读[异常类型与 trap 控制](references/exceptions.md)。

## 1. 从日志确定 trap kernel

保留原始应用/驱动日志及退出状态，关联时间、进程/rank、GPU、`Node ID`、`kernel index`、异常类型、`debug info` 和 kernel 名。可先筛选再查看上下文：

```bash
rg -n -i -C 6 'trapp?ing:|kernel[ _]?(name|index)|commandIndex|debug info:|exception happened|xnack|divide by 0' '<本次运行日志>'
```

不同版本的 error 日志可能采用 `traping: kernel name: <mangled-name>, kernel index: <index>`，或 `trapping: kernelName: <mangled-name>, commandIndex: <index>, trapType: <type>`。保留实际字段及拼写，不将 `kernel index` 与 `commandIndex` 跨版本混用。保留原始 mangled name，需要时另附 demangle 结果。MPI/多进程或多个异常交错时，将同一事件的 kernel、index 和字节串配对；不能混用不同进程或不同轮次的 debug info。

`Xnack(0x8)` 地址转换异常、除零等是故障签名；随后出现的 runtime disabled、signal wait failed 可能是后续错误，不能把最后一条报错直接当作起因。按[异常类型与排查方向](references/exceptions.md#按异常类型缩小范围)检查访存、寄存器、指令或数值条件，不从异常名称直接判定责任组件。

旧环境不能直接输出 kernel 名时，按需读取[旧版定位与兼容限制](references/legacy.md)。不要默认向 MACA 3.0 构建添加旧版编号选项，也不要直接将最后提交的 kernel 判为出错 kernel。

## 2. 找到包含该 kernel 的 binary / so

结合本次构建产物、链接记录、实际加载库路径及设备符号定位所属文件，记录绝对路径、摘要/Build-ID 和源码 revision。host 符号表未找到 kernel 不代表设备代码不存在；需要检查文件内嵌的设备代码。候选较多时先按实际加载关系缩小范围，不反汇编无关的整套 SDK。

需要源码映射时，对**包含目标 kernel 的设备编译单元**增加 `--generate-line-info` 并重编译，确认选项实际进入设备编译命令；只给 host 编译或最终链接添加选项不保证产生设备行号。重编译第三方 so 需要匹配源码和构建条件，不能用应用自身重编译代替。旧工具链的 `-device-debug` 选项及 debug mode 兼容要求见[旧版定位与兼容限制](references/legacy.md)，不直接替换当前流程中的 `--generate-line-info`。

保留原构建及失败日志，尽量保留其他构建条件。使用 line-info 构建重新运行原案例，核对实际加载的是新产物，并采集**该构建自己的** trap/debug info；不能把旧二进制的字节串或地址直接套到新二进制。无法重编译或新构建不复现时，仍可分析原产物的指令范围，但说明源码映射或复现限制。

## 3. 解码 debug info

复制本次事件 `debug info:` 后的十六进制字节串，保留顺序，去掉日志前缀。解码得到的指令数量以当前版本输出为准。下列命令中的工具名应替换为匹配 SDK 工具的绝对路径，或先确认 `PATH` 指向该版本；尖括号内为需要替换的输入。

默认目标为 `xcore1000` 时：

```bash
printf '%s\n' '<本次 debug info 的 0xNN,0xNN,... 字节串>' | llvm-mc --arch=mxc --disassemble
```

目标为 `xcore1100` 时，增加 `-mcpu=xcore1100`：

```bash
printf '%s\n' '<本次 debug info 的 0xNN,0xNN,... 字节串>' | llvm-mc --arch=mxc -mcpu=xcore1100 --disassemble
```

`--arch=mxc` 选择指令架构，`-mcpu` 选择该架构下的设备目标，并非宿主 CPU；仅在需要覆盖默认目标时指定。使用其他目标前核对实际编译目标及匹配版本工具的帮助，不按 GPU 产品名猜测，也不将默认值推广到所有版本。

保存实际命令、原始字节及解码结果；报 unknown instruction 或无法解码时，核对字节是否完整、工具版本和目标架构。Shell 语法检查只能确认命令的写法，不能证明该版本工具支持相关选项或能够正确解码。

## 4. 反汇编并匹配源码范围

使用与上述字节串对应的 binary 或 so，先创建本轮输出目录，保留工具输出和错误信息。`mxobjdump` 的工具路径同样需匹配 SDK 版本。

已确认默认目标为 `xcore1000` 时，按实际文件类型选择一条：

```bash
mxobjdump --print-code --source '<目标可执行文件>' > '<本轮输出目录>/binary.disasm.txt'
mxobjdump --print-code --source '<目标库.so>' > '<本轮输出目录>/library.disasm.txt'
```

非默认目标使用 `--target-kind`。先列出文件内的设备目标：

```bash
mxobjdump --list-elf '<目标 binary 或 so>'
```

从输出的 `Target Kind is ...` 中选择实际目标的设备 ELF 项，排除 `host-...` 和以 `-bc` 结尾的 bitcode 项。例如目标为 `xcore1100`、列表给出 `maca-mxc-metax-macahca--xcore1100` 时：

```bash
mxobjdump --target-kind=maca-mxc-metax-macahca--xcore1100 --print-code --source '<目标 binary 或 so>' > '<本轮输出目录>/target.disasm.txt'
```

`llvm-mc` 的 `-mcpu=xcore1100` 与 `mxobjdump` 的 `--target-kind` 是不同工具的选项，不能互换；`xcore1100` 也不能直接作为完整 target-kind。

先在输出中定位完整 kernel 符号，再在该函数范围内匹配解码后的指令序列及操作数，查看附近源码/行号和控制流。保留匹配地址、上下文和所有合理候选；重复指令可能命中多处，不能只取首个结果。无命中时先检查文件身份、架构、工具版本和字节完整性。

**匹配结果只说明 trap 的大致位置。** 访存异常可能延迟报告，实际错误可能在该位置或此前已执行的一段指令；若控制流曾回跳，也可能来自地址更靠后的指令。不能机械地将匹配到的 store 或最近源码行认定为首个错误。结合源代码、输入、访存边界、数据生命周期、算术条件或 sanitizer 结果继续区分候选；缺少行号/源码时只报告已支持的指令范围。

## 5. trapTool 的使用

从匹配 SDK 版本的 restricted 附加包中找到 `restricted/trapTool` 或 `restricted/Tools/trapTool`，先读取随包 `README.md`，确认入口、依赖、输入格式、目标支持和输出解释，再按文档执行。调用命令以当前包的说明为准，不假定不同构建的 CLI 参数一致。

保存工具版本、实际命令、输入日志与 binary 身份、退出码和结果文件。工具结果仍需与同一次运行的 kernel、指令及源码核对。缺 README、工具不兼容或无法运行时，说明限制并继续可用的手工路径；不把工具执行失败写成应用根因。

## 产出与验证

将环境/制品身份、trap 签名、原始 kernel 名、所属 binary/so、debug 字节及解码、反汇编候选和源码范围写入本任务 `evidence/`，在 `STATUS.md` 区分“定位到 kernel”“缩小到指令/源码范围”和“根因已验证”。有 mcSanitizer/trapTool 实测结果时附上对应材料，未执行则明确标注。记录 `MACA_TRAP_HANDLER`、debug mode 和新增断言的状态；断言自身触发的 trap 与原始故障分开标记，关闭 trap 或屏蔽异常后不再报错不能算修复。

归属和修复沿用[问题分析入口](../../SKILL.md)：kernel 名、异常种类或候选源码行都不能单独证明应用、compiler 或 UMD 责任。候选修复须用原命令、输入和验收标准回验；只有调试构建或工具模式下通过不等于原案修复。
