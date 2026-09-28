# 旧版定位与兼容限制

仅在匹配版本缺少直接 kernel-name 日志或需要复核旧环境时读取。MACA 3.0 优先使用当前版本工具和[主流程](../SKILL.md)；版本能力选择见[MACA SDK 与工具准备](../../../../remote-experiment/references/maca-sdk.md)。

## 用 kernel index 关联旧版日志

1. 确认目标编译器支持 `--addition-kernel-info`，且本次不使用 debug mode；对包含目标 kernel 的设备编译单元添加该选项并重编译。
2. 用新构建重新运行原案例，仅为该轮目标进程设置 `MXLOG_LEVEL=Debug`，保存完整 stdout/stderr 和退出码，再从日志中筛选 `kernel_index`、`kernel_name` 与 trap 的 `kernel index is ...`。
3. 在同一进程/rank、设备和运行轮次内将异常 index 与 kernel 名映射配对。编号不是跨进程或跨构建的稳定身份；多 kernel 并发时不能仅凭一个数值关联。

离线筛选示例：

```bash
rg -n -C 4 'kernel index|kernel_index|kernel_name|traping:' '<本轮完整日志>'
```

只给应用添加选项不能改变已经编译好的 so。若故障 kernel 位于未按该选项重编译的 so，打印出的 index 可能是随机值或缺失，不能据此归因；须重编译实际包含 kernel 的库，或使用匹配版本的其他定位能力。

## 与 debug mode 的兼容关系

- `--addition-kernel-info` 与 debug mode 不兼容，不组合使用。
- 使用该旧版 debug mode 时，`DEBUG_TRACE_WAVE` 必须设为非负整数，并且不能使用 `--addition-kernel-info`；具体数值含义和适用范围按目标版本确认，不猜测 wave 编号。
- 日志级别 `MXLOG_LEVEL=Debug` 不等于 shader debug mode。不要因开启 debug 日志就推断 kernel 已处于单步模式。

## 旧版设备调试信息

部分旧工具链使用 `-device-debug` 添加设备调试信息。仅在匹配版本明确支持时采用，检查真实设备编译命令和生成产物；不将它与 `--generate-line-info` 视作跨版本等价选项，也不默认同时添加二者。

调试构建可能改变代码生成、时序和资源使用。沿用主流程要求，保留原构建，重新复现并采集新构建的字节串与 binary/so；不拿原日志的指令地址映射新产物。

## 已弃用的同步与最后提交 kernel 推断

逐个 launch 后同步、再从日志取最后提交 kernel 的方法不作为当前流程。多 stream、多个提交线程或并发 kernel 会使“最后提交”与“实际出错”不一致，新增同步还可能隐藏原竞态。

只有在直接 kernel-name 日志、匹配工具及有效 index 映射都不可用时，才考虑在独立诊断构建中增加同步以缩小候选；按已完成/失败的同步边界判读，并回到原并发条件验证。

旧版多队列分析还可能需要将 `HostQueue::loop` 中的设备 node id、提交线程 id 与异常日志对齐。node id 或某线程的最后一条提交日志只能帮助缩小范围，不能单独证明某个 kernel 触发了 trap；不要为了套用该方法默认暂停所有线程或改变原应用的同步关系。
