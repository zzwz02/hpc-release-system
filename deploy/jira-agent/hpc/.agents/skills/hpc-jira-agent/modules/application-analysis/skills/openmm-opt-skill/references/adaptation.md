# OpenMM MACA 适配

此参考文件用于记录 OpenMM 在 MACA 上的专属适配工作。


## 工作流框架

1. 识别 OpenMM 版本、源码 checkout、Python 环境、`OPENMM_PATH` 和 MACA 软件栈，如果没有配置`OPENMM_PATH`，则将`OPENMM_PATH`配置为`/opt/openmm`。
2. 使用本文件中的 CMake 和编译命令构建 OpenMM。
3. 为提高调试效率，优先运行单项 unit test 验证局部正确性。
4. 单项 unit test 通过后，运行全部 unit test。
5. 适配目标是全部 unit test 通过；若失败，记录失败测试、命令、日志和最小复现路径，再做针对性修复。

## 标准命令

### CMake 配置

```bash
mkdir -p build && cd build && cmake_maca -DOPENMM_BUILD_CUDA_LIB=ON -DCMAKE_HAVE_LIBC_PTHREAD=ON -DOPENMM_BUILD_CUDA_COMPILER_PLUGIN=OFF -DCMAKE_INSTALL_PREFIX=$OPENMM_PATH ..
```

### 编译和安装

```bash
cd build && make_maca -j install
```

### 单项 Unit Test

优先用单项 unit test 快速调试正确性问题：

```bash
cd build && ./TestCudaNonbondedForce Single
```

### 全部 Unit Test

最终必须运行全部 unit test，并以全部通过作为适配合格标准：

```bash
cd build && make_maca test || ctest --return-failed --output-on-failure
```

## 合格标准

- CMake 配置成功。
- 编译和安装命令成功完成。
- 为提高效率，可以先调试单项 unit test 正确性。
- 最终适配合格标准是全部 unit test 通过。
- 若单项 unit test 通过但全量 unit test 失败，继续以失败的全量测试为准定位并修复。

## 已知适配要点

### NVRTC 与模块加载

- `platforms/cuda/src/CudaContext.cpp` 中，`nvrtcGetSupportedArchs()` 返回值不能假设已排序。MACA 环境中曾返回 `[80, 75, 70]`，应使用最大值而不是 `archs.back()` 选择编译器支持的最高架构。
- MACA 的 NVRTC 可能返回二进制代码而非纯文本 PTX。写临时/缓存 kernel 文件时必须按二进制写入，并使用实际 `ptxSize`，避免 `string(&ptx[0])` 在首个 null 字节截断导致 `CUDA_ERROR_INVALID_PTX (218)`。

### Warp Size 64 适配

- OpenMM 许多 CUDA kernel 逻辑以 32 线程 tile 为基础，但 MACA C500 的 warp/wave 为 64 线程。`SHFL` 宏需要使用 64 位 mask，并显式指定 `width=32`，把 shuffle 限定在 32 线程子组内。
- `BALLOT` 在 MACA 上返回 64 位结果，而 OpenMM 相关代码仍按 32 位 tile flag 使用。需要根据 `threadIdx.x & 32` 选择低 32 位或高 32 位，再截断为 32 位值。
- 不要把 `SYNC_WARPS` 简化为空操作。64 线程 wave 内包含两个 32 线程 tile，Amoeba tile-based kernel 会通过 shared memory 跨 tile 读写，缺少 `__syncwarp()` 可能引入数据竞争。

#### CudaContext.cpp 宏定义

具体修改点位于 `platforms/cuda/src/CudaContext.cpp` 的 CUDA 9+ `compilationDefines`。旧写法通常使用 32 位 mask，不能直接用于 MACA：

```cpp
compilationDefines["SHFL(var, srcLane)"] = "__shfl_sync(0xffffffff, var, srcLane);";
compilationDefines["BALLOT(var)"] = "__ballot_sync(0xffffffff, var);";
```

MACA 上应改为 64 位 mask，并保持 OpenMM 32 线程 tile 语义：

```cpp
compilationDefines["SYNC_WARPS"] = "__syncwarp();";
compilationDefines["SHFL(var, srcLane)"] = "__shfl_sync(0xffffffffffffffffULL, var, srcLane, 32);";
compilationDefines["BALLOT(var)"] = "((int)(((threadIdx.x & 32) ? (__ballot_sync(0xffffffffffffffffULL, var) >> 32) : __ballot_sync(0xffffffffffffffffULL, var)) & 0xffffffffU))";
```

### PTX 内联汇编替换

- MACA 编译器不支持部分 NVIDIA PTX asm 约束，例如 double 的 `"d"`、long long 的 `"l"`、half 的 `"=h"` 和 float 的 `"f"` 转换约束。
- `platforms/cuda/src/kernels/nonbonded.cu` 和 `plugins/amoeba/platforms/common/src/kernels/hippoNonbonded.cc` 中，`real_shfl(double)` 应使用 `__double2loint()` / `__double2hiint()` 拆分 double，再 shuffle 后用 `__hiloint2double()` 还原。
- `real_shfl(long long)` / `real_shfl(mm_long)` 应通过 `unsigned long long` 位操作拆分低/高 32 位，避免 `asm mov.b64`。
- `platforms/cuda/src/kernels/findInteractingBlocks.cu` 中，删除自定义 `__half` 与 PTX half conversion asm，改为包含 MACA 兼容的 `cuda_fp16.h` 并使用其中的 half 转换接口。

### 越界访问修复

- MACA 对非法地址访问更严格，不能依赖 NVIDIA GPU 对“先越界读取、后续再用边界条件保护”的容忍行为。
- `plugins/amoeba/platforms/common/src/kernels/multipoles.cc` 的 `computePotentialAtPoints` 曾因 `points` 只有 27 个元素但 kernel 以 128 线程块启动，在边界检查前读取 `points[point]` 触发 `CUDA_ERROR_ILLEGAL_ADDRESS (700)`。读取前必须先判断 `point < numPoints`，越界线程使用安全的零值占位。

### 验证锚点

- 快速回归优先使用 `./TestCudaNonbondedForce Single` 覆盖 nonbonded 相关修复。
- Amoeba 相关修复可使用 `./TestCudaAmoebaMultipoleForce Mixed` 做定向验证。
- 单项测试通过后仍必须跑全部 unit test；最终合格标准以全量 unit test 通过为准。
