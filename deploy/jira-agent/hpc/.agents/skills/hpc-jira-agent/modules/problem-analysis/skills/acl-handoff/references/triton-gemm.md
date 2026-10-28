# Triton 与 GEMM 附加材料

本参考按[ACL 模板来源](ticket-templates.md)整理。只在涉及相应优化时叠加；普通 Bug 的采集仍按其实际症状和目标决定。性能回退保留 Bug 分类。

## Triton 优化

Triton 优化须先满足[性能优化模板](ticket-templates.md#性能优化improvement)的公共信息与附件，并补充：

1. 提单前对相关 case 进行 config 搜索/autotune，记录搜索范围、实际尝试、正确性过滤、计时方式、最优 config 和结果。最优仅指本次已测试范围，不宣称全局最优；无执行资源/工具时标未完成，不能只给默认 config 就说已调优。
2. 提供可独立运行的 Triton 单测，覆盖交付对比表里的全部 case；保留源码/版本、输入 shape、dtype、布局、调用次数、构建/运行命令和正确性判据。
3. 每个 case 同时给 A100 和 C500/C550（或实际目标 chip）的性能与有效 config；两侧工作量、精度和计时口径可比，但各自最优 config 可不同。不能只给跨 case 的总体平均值。

对比表至少包含：

| 字段组 | 记录内容 |
|---|---|
| 案例身份 | case ID、shape、dtype、布局、batch size、训练/推理及阶段 |
| 性能 | A100 与目标 chip 的原始耗时/吞吐、单位、统计量和性能比；各自最优 config |
| 两侧 config | `BLOCK_M`、`BLOCK_N`、`BLOCK_K`、`GROUP_SIZE_M`、`num_stages`、`num_warps` |
| MACA 额外 config | `pipeline`、`scenario` |
| 其他影响性能的参数 | 全部可调参数及实际值，例如 MLA 的 `num_kv_splits`，不能限于上面常见参数 |

某 kernel 不含某字段时注明不适用及原因；适用但未知时标待补充，不填猜测值。保留逐 case 的完整 config、autotune 结果与测试证据，CSV/表格均可，只改变载体不遗漏内容。

原模板补充资料（未附正文，读取仍需遵守资源访问边界）：

- [Triton autotune config settings](http://sw-wiki.metax-tech.com/bin/view/3.%20Organizations/1.%20Software/MathLib_Framework/pytorch/profile/triton/Triton%20autotune%20config%20settings/)：config autotune 方法；未读取前不猜其具体命令。
- [fused_moe_w4a16_vllm_0.7.2_0319.xlsx](http://sw-wiki.metax-tech.com/bin/download/3.%20Organizations/1.%20Software/MathLib_Framework/Cross%20Team%20Work/ACL提单流程及模板/WebHome/fused_moe_w4a16_vllm_0.7.2_0319.xlsx?rev=1.1)：原模板对比表参考，无法读取不阻止按上述已提供字段整理实际数据。

## GEMM 性能

GEMM 优化报告需给出三个不同指标及其原始分子/分母。以下以 C500 为目标平台，实际为 C550/C600 等时按真实平台标注：

| 指标 | 计算与用途 |
|---|---|
| 整网相对 A100 性能 | 相同工作量下 `T_network_A100 / T_network_C500 × 100%`，表示整网性能情况 |
| GEMM 占整网时间比例 | 同一阶段、窗口内 `T_gemm_C500 / T_network_C500 × 100%`，评估 GEMM 对整网的重要性 |
| GEMM 相对 A100 性能 | 相同工作量下 `ΣT_gemm_A100 / ΣT_gemm_C500 × 100%`，评估 GEMM 本身的差异 |

若用吞吐，整网性能比对应 `throughput_C500 / throughput_A100`；不能把耗时比倒置。列出统计样本、调用次数、测量窗口、单位和原始数据，缺分子/分母时不计算。多 stream 或重叠 kernel 的累计耗时不能直接当作 wall time 占比；按时间线说明重叠和归属口径，分别列累计工作量与可解释的端到端贡献，不把超过 100% 的累计比率称作占用比例。

重要模型核对 shape log 的 API 调用次数与 profiler kernel name 调用次数在可解释的误差范围内一致：列出 API/shape 到 kernel（或集合）的对应关系、两侧计数、差异和原因。融合、多 kernel 实现、graph replay 可能不是一对一，必须解释映射；不能凭空设一个允许误差百分比或只比较总数。无法对齐时将代表性标为未验证，继续查窗口、预热、重放或漏采等差异。

涉及 decoding 时，优先提供只覆盖 decoding 的全量相关 shape 和次数，排除 warmup、prefill 等其它阶段；不能只抽热点若干 shape 代替全量。记录过滤依据和采集范围。为取得 shape 可以在诊断副本中去掉 warmup 或关闭 CUDA Graph（cudagraph），但需核对输入与调用覆盖，标记配置变化，正常性能基准仍使用原配置，不拿关闭 graph 的慢化作库回退证据。

原模板以 [MC3-5709](http://jira.metax-tech.com/browse/MC3-5709) 提示去 warmup、关闭 cudagraph 等注意事项。该工单只是来源线索；本 agent 不直接访问 Jira，未由网站提供正文时不声称已读取其中 reporter 与高礼的讨论。
