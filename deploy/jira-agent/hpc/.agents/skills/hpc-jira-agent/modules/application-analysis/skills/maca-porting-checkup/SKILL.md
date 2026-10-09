---
name: maca-porting-checkup
description: 依据目标 MACA/cu-bridge 的实际能力，评估 CUDA 应用的构建、接口、运行语义与功能兼容性，给出验证证据和迁移路线。
---

# MACA 应用移植体检

输入：源码/commit、目标平台、必需功能、构建与运行命令、正确性标准。先读[接入契约](references/project-integration.md)；局部诊断只检查与当前问题有关的内容。

## 1. 确认兼容性基线

- 查目标环境的 cu-bridge 头文件、库、wrapper、链接替换和符号。源码出现 CUDA/NVIDIA 名称不直接证明缺口。
- MACA 默认评估 CUDA/cu-bridge 路线；只有用户要求或证据确认该路线不可行时，才考虑 HIP/OpenCL 等后端，并说明功能影响。
- 按依赖角色和目标功能区分构建、链接、运行、正确性和性能风险；已有 fallback 不等于完整性能支持。深入判定用[评估细则](references/assessment.md)。

## 2. 验证构建路线

先保留原命令及失败。获准候选移植在独立目录使用 `cmake_maca`、`make_maca` 或 `ninja_maca`；候选通过不能改写为原始命令已通过。

CUDA 路径/版本探测失败时，可在候选实验中保存原变量后临时尝试：

```bash
export CUCC_PATH=/opt/maca/tools/cu-bridge
export PATH=$PATH:${CUCC_PATH}/tools:${CUCC_PATH}/bin
export CUCC_CMAKE_ENTRY=2
export CUDA_PATH=${CUCC_PATH}
```

记录改动和结果，实验后恢复原变量；仍失败时查实际 API/ABI 或工具版本，不自动升级共享 SDK。构建日志保留首个有效错误；成功时用链接日志、可信产物的 `ldd` 或 `readelf -d` 核验 MACA/cu-bridge 依赖。

## 3. 验证功能与语义

只实施已授权的局部改动；不能关闭必需功能制造通过。运行目标案例，记录退出码、输出和原正确性判据。编译成功不等于运行正确，第一次构建失败也不等于无法移植。

PTX、warp、同步、异步生命周期、JIT 和 GPU 生态依赖按[评估细则](references/assessment.md)查实际实现与替代路线；不机械替换 warp 宽度，不把性能 fast path 缺失写成正确性缺陷。

## 4. 输出

给出编译结论（成功 / 失败 / 未验证）、必要命令与证据、已解决项、剩余缺口及受影响功能。缺正确性标准写未验证；工作量使用 S/M/L/XL 或低/中/高，不估人天。

完整体检再使用[报告模板、patch 分类和命令参考](references/assessment.md#报告格式)。每个改动说明如何解决问题及验证结果；未实施建议与已验证修改分开。
