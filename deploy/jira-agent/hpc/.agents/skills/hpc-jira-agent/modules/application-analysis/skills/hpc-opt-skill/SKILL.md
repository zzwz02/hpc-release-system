---
name: hpc-opt-skill
description: 为 MACA HPC 应用选择构建、适配、正确性验证和获准性能优化的方法。
---

# HPC 应用适配与优化方法

先读[接入契约](references/project-integration.md)，再按当前任务读取对应参考：

| 任务 | 参考 |
|---|---|
| 通用移植、编译和运行启用 | [adaptation.md](references/adaptation.md) |
| Profiling、基准测试和性能优化 | [optimization.md](references/optimization.md) |
| 应用专属工作流 | [应用模块导航](../../SKILL.md#按任务选择) |

## 要求与产出

- 正确性通过后再优化，保留原工作量和判据；优化性能结论来自已批准的独占 GPU 窗口，GPU 查询不能代替独占授权。
- 记录有效和无效实验、源码身份、变更及验证结果。无 Git 源码记录来源和文件哈希，不擅自初始化仓库。
- 源码改动和提交遵循本次授权；本组候选修复按 [app-source-fix](../app-source-fix/SKILL.md)交付 patch，由 owner 审核提交。
- 向当前工单返回构建/验证路线、已确认结论和限制。正式 Skill 的经验沉淀属于独立维护任务，不在交付后额外发起确认流程。
