# 异常类型与 trap 控制

用于判断为何进入 trap handler、解释异常日志和选择下一项区分实验。所有开关与寄存器语义都需匹配实际 SDK、驱动及目标架构；先保留原运行结果，再做独立诊断对照。

## 进入 trap 的原因

| 入口 | 如何判断 |
|---|---|
| 显式 trap / assert | 核对应用、内联汇编及诊断插桩；新增断言会产生自己的 trap |
| 异常触发 | 对齐驱动异常类型、kernel 身份、debug info 和异常启用状态 |
| debug mode | shader 单步可在每条指令执行后进入 handler；频繁进入不等于持续发生业务异常 |

`wave_status.trap_en` 控制 trap 启用。为 1 时，trap 指令可跳转到 handler；为 0 时，trap 指令被视为 `snop`。kernel object 的 `COMP_PROGRAM_RESOURCE2` 控制相关初始配置。

debug mode 由 kernel object 的 `COMP_PROGRAM_RESOURCE1` 控制。在该模式下，handler 内执行的指令不会递归进入 handler，`kill`、`endk` 也不触发这类单步进入。不要把 debug mode、编译调试信息和日志级别当作同一个开关。

## 异常掩码与运行开关

`mode.excp_en` 控制 maskable 异常；对应位启用且 `trap_en=1` 时，该类异常可使 shader 进入 handler。unmaskable 异常不能通过这些分类掩码关闭；分类掩码与全局 trap 开关不是同一层控制。

未主动修改时，异常掩码的初始值由 kernel object 的 `COMP_PROGRAM_RESOURCE2` 决定；shader 中的 `set_hwreg` 可以改变相应位。因此仅查看 shell 环境变量不足以证明 kernel 的实际异常掩码。分析已有寄存器/产物信息即可，不将直接改写寄存器作为常规排查步骤。

| `MACA_TRAP_HANDLER` | 含义 |
|---|---|
| `0` | 完全关闭 trap 功能 |
| `1` | 默认模式：报告 unmaskable 异常和 maskable 中的致命异常 |
| `2` | 报告所有异常 |

使用前核对当前版本是否支持及实际生效情况。需要扩大报告范围时，仅对本轮目标命令设置 `MACA_TRAP_HANDLER=2`，保存 stdout/stderr 和实际退出码；不要修改共享 shell、宿主或后续基准的全局设置。报告增多不等于故障增多，性能和时序也可能改变。

`0` 只表示关闭检测/处理，不能证明故障消失，也不作为修复。没有 trap 日志时同时核对全局开关、分类掩码和日志完整性，不能直接判定 kernel 正常。

## 按异常类型缩小范围

| 异常 | 含义与下一步 |
|---|---|
| Fed Error | 读取尚未初始化的寄存器。核对寄存器初始化路径、固件及 kernel 配置，必要时与 firmware 维护者联查；不能仅凭名称认定硬件损坏 |
| Illegal Instruction | 指令编码或操作数非法。比较编译产物、装载后的设备代码与实际执行位置，区分生成错误、装载/解释错误及代码被意外覆盖 |
| Memory Violation | 地址偏移、访问范围或对齐条件不满足。结合实际访存指令、地址计算、访问宽度、对象生命周期及 shared memory 范围检查 |
| Xnack Error | ATU 地址转换失败。核对指针有效性、映射、设备归属、释放时机和越界；应用、compiler、runtime/driver 都可能是候选 |
| Out Of Range | 寄存器寻址越界。检查相对寻址状态、寄存器声明数量和实际操作数，见下节 |
| Numeric Error | 包括除零、invalid、overflow、underflow、inexact、input denormal；结合输入和计算结果判断是否违反本案数值要求 |
| Fue Error | 与硬件运行中的随机电路扰动有关的低概率异常。保留设备、时间与复现证据，必要时结合设备诊断联查，不从单次日志推断永久硬件故障 |
| Timeout Error | kernel 处于 halt 状态过久。检查调试暂停、设备进展与等待关系；区别于外层作业超时 |

Numeric Error 在同一 kernel 中可能只在首次触发该类异常时通知 driver，后续重复触发不再逐次通知；日志条数不能当作异常操作次数。invalid、inexact 或 denormal 也不能一概忽略：需核对有效输入、首个异常中间值、输出和本案判据，必要时转[正确性分析](../../correctness-analysis/SKILL.md)。

### 访存与对齐

- Memory Violation 和 Xnack 都与访存有关，但分别描述访问约束与地址转换，不能相互等同。内存指令可能延迟报告异常，须回查匹配点此前执行的相关 load/store/atomic 及地址生成指令。
- 对齐以实际指令为准：一般访存常见 32-bit（4 字节）要求；`LD_U16` / `ST_B16` 对应 2 字节，`LD_U8` / `ST_B8` 对应 1 字节；原子操作按参与数据宽度对齐，64-bit atomic 需要 8 字节。目标架构另有要求时按实际规范核对。
- 同时满足语言/ABI 的类型对齐要求，不能把普通指令的 4 字节条件推广到所有 C/C++ 类型。packed 结构可能改变普通访问的指令组合，却不能保证内部 64-bit 成员满足 atomic 的 8 字节对齐要求。
- shared memory 需查看 BSM 指令的基址、偏移及完整访问范围，不能只检查两者相加后的值。适用架构要求基址和偏移均非负；在 `BSM_SIZE=64 KiB` 的目标上，访问末端必须落在该地址范围内，也不能越过实际分配范围。容量不可直接推广到所有目标。
- 只有在指令证据指向 BSM offset 生成问题且当前编译器支持时，才用 `-mllvm -metaxgpu-disable-bsm-offset=1` 做单变量对照。保留前后反汇编、正确性及原案结果；该选项是条件性规避/诊断手段，不能直接证明 compiler 根因或认定已修复。
- 仿真/model 的异常覆盖范围可能不同；若不支持 Xnack 上报，无报错不能替代真机验证，代码区被误写还可能表现为 illegal instruction 或静默计算错误。

### 寄存器越界

Out Of Range 的相关检测依赖 mtreg 相对寻址状态：包括 mtreg 访问越界，以及 `SMOVRS_B32/64`、`SMOVRD_B32/64` 引起的 streg 越界。`set_rel_on` / `set_rel_off` 控制 mtreg 相对寻址；未开启时，某些内联汇编越界行为无法由硬件保证，也可能没有异常提示。

需要核对寄存器分配时，在独立构建中使用当前编译器支持的 `--save-temps` 保留 `.s`，比较 mtreg/streg 声明数量、相对寻址计算和实际寄存器编号。编译器生成代码越界与应用内联汇编自行引用超出声明数量的寄存器是不同候选；不要仅凭 Out Of Range 将责任交给 compiler。

## 诊断插桩的边界

新增 assert 或暂时缩减语句可用于区分候选，但会改变执行路径；只有维持原触发路径的原案例验证才能支持修复结论。

assert 失败本身会使用 trap，必须标记为诊断断言事件，并与插桩前的原始故障对应。不要将它的 debug info 误当成原故障位置，也不要因此丢弃全部原始日志。

怀疑指令被覆盖时，优先比较编译产物和设备实际代码。确需用 `get_pc` 配合断言检查指令字节时，先确认目标架构的 PC 语义、编码和可读性；不照搬其他版本的指令常量。插桩构建重新采集自己的 trap 证据，并保留未插桩对照。
