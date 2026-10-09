---
name: application-analysis
description: 为 HPC 应用修复、适配和优化选择专属构建与验证方法，包括 MACA 兼容性检查、cu-bridge、OpenMM 和 GROMACS。
---

# HPC 应用专项

输入：应用与源码身份、任务目标、已有诊断、允许改动的范围。产出：所选方法的结论、候选变更、验证结果及未支持项。

应用与方法导航以本模块下表为准，其他入口引用本表；新增专项先核对实际目录与适用条件。

## 按任务选择

| 任务 | 子 Skill | 产出 |
|---|---|---|
| 已确认的本组源码、构建或测试问题，需要候选修复 | [app-source-fix](skills/app-source-fix/SKILL.md) | 匹配基线、patch、针对性与原案例验证 |
| 新应用或目标平台接入 | [app-adaptation](skills/app-adaptation/SKILL.md) | 兼容缺口、候选构建、目标功能验证 |
| 通用 MACA 构建、适配或获准优化 | [hpc-opt-skill](skills/hpc-opt-skill/SKILL.md) | 构建路线、正确性与性能证据 |
| CUDA/MACA 接口、链接和语义兼容检查 | [maca-porting-checkup](skills/maca-porting-checkup/SKILL.md) | 支持范围、缺口、探针和迁移建议 |
| cu-bridge wrapper、工具链选择或 CUDA→MACA 迁移故障 | [cu-bridge](skills/cu-bridge/SKILL.md) | 构建路径、环境隔离、接口映射诊断和目标案例验证 |
| OpenMM 专属问题 | [openmm-opt-skill](skills/openmm-opt-skill/SKILL.md) | 专属构建、工作负载和验证方法 |
| GROMACS 专属问题 | [gromacs-opt-skill](skills/gromacs-opt-skill/SKILL.md) | 专属构建、运行和瓶颈分析方法 |

`maca-porting-checkup` 用于整体兼容性与迁移缺口评估；`cu-bridge` 用于具体 wrapper、工具链和映射故障。按当前问题选用，已有兼容性结论可以复用。

## 执行要求

- 只加载匹配任务的专项；故障诊断可与[问题分析](../problem-analysis/SKILL.md)组合，应用报错不直接证明应用自身负责。
- 进入本组修复前，复用[主流程](../../SKILL.md#3-按归属处理)已确认的应用范围和责任判断。
- 获准改动在独立候选目录完成，保留原复现版本。具体修复、适配和优化分别按对应专项验收。
- 返回与当前任务有关的证据，复用已有实验。完整专项报告模板仅用于完整专项任务。
- 优化验收遵守所选专项的测量条件；这些条件不扩大为所有共享机器诊断的前置要求。
