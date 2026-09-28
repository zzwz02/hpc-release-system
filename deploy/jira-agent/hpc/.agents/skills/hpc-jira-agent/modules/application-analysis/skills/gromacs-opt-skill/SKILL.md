---
name: gromacs-opt-skill
description: "面向 MetaX MACA 平台的 GROMACS 适配、构建验证工作流。适用于在 MACA 上移植、编译、启用、验证、性能剖析、基准测试或优化 GROMACS 应用工作流。"
---

# GROMACS MACA 适配与优化

将此 skill 用于 GROMACS 专属 MACA 工作。通用 HPC MACA 指南保存在 `$hpc-opt-skill` 中；GROMACS 源码布局、编译选择、验证用例、基准测试和调优记录保存在此处。

## 任务导航

- 移植、编译、安装、单元测试、正确性验证：阅读 [references/adaptation.md](references/adaptation.md)。
- 性能剖析、benchmark 和调优：先使用 `$hpc-opt-skill` 的通用优化流程；只有在已有 GROMACS 专属性能证据后再沉淀新的优化参考。
- GROMACS 专属工作前需要通用 MACA HPC 策略时，使用 `$hpc-opt-skill`。
