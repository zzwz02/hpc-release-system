---
name: problem-analysis
description: 按 HPC 故障现象选择构建、崩溃或卡死、kernel trap、正确性、性能、运行期编译及回退分析方法，形成可验证的定位和归属证据。
---

# HPC 问题分析

输入：工单目标、原命令与输入、环境身份、已有实验。产出：已定位范围、有效证据、未排除项及下一动作，更新到现有 `STATUS.md` 和 `evidence/`。

## 按现象选方法

| 现象 | 子 Skill | 要回答的问题 |
|---|---|---|
| 配置、编译、链接、加载或启动失败 | [build-runtime-analysis](skills/build-runtime-analysis/SKILL.md) | 首先失败的阶段及实际产物/依赖是什么？ |
| 崩溃、异常退出、GPU 故障、卡死或超时 | [crash-analysis](skills/crash-analysis/SKILL.md) | 异常点、等待关系或资源终止原因是什么？ |
| MACA kernel trap、traping kernel name 或设备异常 debug info | [kernel-trap-analysis](skills/kernel-trap-analysis/SKILL.md) | 哪个 kernel、所属 binary/so 及哪段指令/源码与 trap 对应？ |
| 输出错误、精度偏差、NaN/Inf、不稳定 | [correctness-analysis](skills/correctness-analysis/SKILL.md) | 判据是什么，首个差异在哪里？ |
| 延迟、吞吐或扩展性不达标 | [performance-analysis](skills/performance-analysis/SKILL.md) | 测量是否可比，主要瓶颈在哪里？ |
| 运行期 JIT、重复编译或缓存异常 | [runtime-recompile-analysis](skills/runtime-recompile-analysis/SKILL.md) | 是否真实重复编译，能否解释原现象？ |
| 版本或配置变化后恶化 | [regression-analysis](skills/regression-analysis/SKILL.md) | 哪个受控变量改变结果？ |

回退是可叠加的对照方法，没有旧版本仍按症状诊断。新平台接入使用[应用适配](../application-analysis/skills/app-adaptation/SKILL.md)；应用语义和构建方法按需读取[应用专项](../application-analysis/SKILL.md)。

## 实验决策与收敛

1. 尚未复现时，用[远端实验](../remote-experiment/SKILL.md)匹配原现象；环境准备失败不等于应用故障复现。
2. 实验前说明待区分的候选或未知项，以及结果将改变哪个判断。控制必要变量，保留环境、命令、输出和退出码。
3. 实验后先检查有效性，再更新假设。加载失败或不兼容的混合版本不能用于排除候选；无区分力时调整实验，必要的稳定性、反向对照和原案回验可以重复。
4. 达到任务要求的定位深度后，按[总入口](../../SKILL.md)继续修复、交接或交付；追加分析应能解决尚存的证据问题。profiling 仅在能回答该问题时选用，见 [trace-report](skills/trace-report/SKILL.md)。

## 调整方向与归属

- 根据证据切换方法：构建中编译器崩溃可组合 crash；性能测量发现错误结果先转正确性；无进展先区分慢与卡死。
- 环境、资源、输入、应用、compiler、UMD、数学库和通信库均可成为候选。栈顶、报错名称、版本差异或参数规避，不能单独证明责任。
- 归属判断复用[主流程的范围核对与处理规则](../../SKILL.md#3-按归属处理)。外部库被不受支持的方式调用，不能只凭栈顶判为外部缺陷。
- 区分“已复现”“版本相关”“组件因果定位”“机制已解释”。组件交互不能写成各组件独立故障，最小案例通过不能替代原案例验证。

交付证据应串起原始问题、缩小范围的关键实验和当前结论；每项实际改变判断的实验按[分析路径](../jira-evidence/references/result-delivery.md#分析路径)记录，报告格式见[网站结果交付](../jira-evidence/references/result-delivery.md#报告与证据)。材料不足按[缺失条件规则](../../SKILL.md#缺失条件的处理)处理。
