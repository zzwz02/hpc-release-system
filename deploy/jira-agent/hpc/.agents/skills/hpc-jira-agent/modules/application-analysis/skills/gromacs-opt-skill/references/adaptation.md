# GROMACS MACA C500 适配

此参考文件面向可复用的 GROMACS 适配流程。它只记录最终代码修改点、构建参数和验证要求。

## 适用范围

- 目标平台：MetaX MACA ，通过 cu-bridge 使用 GROMACS CUDA 后端。
- 目标形态：启用 C500 64-wide wave；NBNXM 普通非键 kernel 使用 no-split 64-wide cluster-pair；PME CUDA kernel 参数化为 64-wide；Nonbonded FEP CUDA kernel 同 normal NB kernel 一样使用 `c_parallelExecutionWidth`。
- 通用规则来自 `$hpc-opt-skill/references/adaptation.md`：C500 wave 为 64、shuffle/sync mask 需要 64 bit、C500 不支持 NVIDIA PTX inline asm、验收状态使用 `PASS`/`FAIL`/`UNKNOWN`。

## 工作流

1. 确认 GROMACS 版本、baseline commit、源码目录、构建目录、安装目录、MACA SDK/cu-bridge 环境和验收标准。
2. 从 clean build 开始配置、编译、安装和测试；所有命令、日志路径、失败测试和最终测试总数都要记录。
3. 保存候选 patch、哈希和验证记录，在报告中说明适配原因；按 [源码修复要求](../../app-source-fix/SKILL.md)包含获准新增文件，由 owner 审核提交。agent 不执行 `git commit` 或 `git push`。

## 标准构建参数

在缺少完整 CUDA toolkit 的容器中，设置 `CUCC_CMAKE_ENTRY=2` 使用 cu-bridge mock CMake 入口；mock 模式下显式指定 CUDA 架构。

```bash
export CUCC_CMAKE_ENTRY=2
export GROMACS_SRC="${GROMACS_SRC:-/workspace/gromacs}"
export GROMACS_PATH="${GROMACS_PATH:-${GROMACS_SRC}/install}"

cd "$GROMACS_SRC"
rm -rf build
mkdir -p build "$GROMACS_PATH"
cd build
cmake_maca .. \
  -DGMX_GPU=CUDA \
  -DGMX_DOUBLE=off \
  -DCMAKE_INSTALL_PREFIX="$GROMACS_PATH" \
  -DGMX_SIMD=AVX2_256 \
  -DGMX_MPI=off \
  -DGMX_INSTALL_NBLIB_API=off \
  -DGMX_GPU_MACA=1 \
  -DGMX_GPU_NB_DISABLE_CLUSTER_PAIR_SPLIT=ON \
  -DGMX_CUDA_ARCHITECTURES=80
```

```bash
cd "$GROMACS_SRC/build"
make_maca -j16 tests
make_maca -j16 install
make_maca test -j16
```

`install` 不一定构建全部测试二进制，所以最终测试前显式构建 `tests`。验收以当前配置下所有已发现测试全部通过为准；不要用降低并发、跳过测试或只跑 smoke test 代替最终结论。

## 核心代码修改点

### 1. MACA 平台开关

添加或使用 `GMX_GPU_MACA` 作为 CMake/config 层面的 MACA 适配开关。它用于限制 no-split 等 CUDA 后端改动只在 MACA/cu-bridge 场景启用，避免改变上游 NVIDIA CUDA 默认行为。设备代码里优先用编译器定义的 `__MACACC__` 区分 MACA 编译路径。

### 2. PTX inline asm 替换

MACA 不支持 NVIDIA PTX inline assembly。处理原则：

- 保留 NVIDIA 原 PTX 路径。
- 用 `#ifdef __MACACC__` 为 MACA 提供 fallback。
- 替换 memory-ordering asm 时必须保持 acquire/release/relaxed 语义，不要只为了编译通过删除同步。

典型文件：`src/gromacs/domdec/gpuhaloexchange_impl_gpu.cu`。

| PTX 语义 | MACA fallback |
| --- | --- |
| `ld.acquire.gpu.global.u32` | volatile load 后 `__threadfence()` |
| `ld.acquire.sys.global.u64` | volatile load 后 `__threadfence_system()` |
| `ld.relaxed.sys.global.u64` | volatile load |
| `st.release.sys.global.u64` | `__threadfence_system()` 后 volatile store |
| `st.relaxed.sys.global.u64` | volatile store |
| `atom.inc.release.gpu.global.u32` | `__threadfence()` 后 `atomicInc()` |
| `prefetch.global.L1` | MACA 下禁用；这是性能 hint，不是正确性语义 |

注意：`atomicInc()` 要保留“返回旧值”和 modulo 行为；不要用 `atomicAdd()` 加 `atomicExch()` 拼装。

### 3. Warp/Wave 常量拆分

C500 硬件 wave 是 64，但 GROMACS CUDA 代码里仍有部分算法按 32-lane tile 组织。适配时必须把“硬件宽度”和“算法宽度”拆开。

在 `src/gromacs/gpu_utils/cuda_arch_utils.cuh`：

- `__MACACC__` 下设置 `warp_size=64`、`warp_size_log2=6`。
- NVIDIA CUDA 下保持 `warp_size=32`、`warp_size_log2=5`。
- `c_fullWarpMask` 在 MACA 下使用 64-bit 类型和值，例如 `unsigned long long` 和 `0xffffffffffffffffULL`。
- 新增或保留 `c_cudaLogicalWarpSize=32`，用于尚未完成 64-wide 改造的 CUDA 专用算法。

穿过 `__shfl*_sync`、`__ballot_sync` 或 helper API 的 mask 类型不能截断成 `unsigned int`。调用点优先使用 `auto activeMask = c_fullWarpMask` 或显式的 `CudaWarpMask`。

### 4. NBNXM no-split 64-wide

GROMACS 已有 `GMX_GPU_NB_DISABLE_CLUSTER_PAIR_SPLIT` 控制普通非键 NBNXM 的 cluster-pair 布局：

```cpp
#if GMX_GPU_NB_DISABLE_CLUSTER_PAIR_SPLIT
static constexpr int c_nbnxnGpuClusterpairSplit = 1;
#else
static constexpr int c_nbnxnGpuClusterpairSplit = 2;
#endif
```

CUDA 默认 split=2：一个 8x8 cluster-pair 被拆成两个 32-lane 半块。C500 64-wide 下应在 MACA 构建中启用 no-split，即 split=1，让 64 个 lane 覆盖完整 8x8 cluster-pair。

实现要点：

- CMake 配置使用 `-DGMX_GPU_NB_DISABLE_CLUSTER_PAIR_SPLIT=ON`。
- 对 CUDA 后端放开该选项时，要通过 `GMX_GPU_MACA` 或等价平台选择器限制在 MACA 上。
- NBNXM CUDA kernel 中 lane indexing、exclusion indexing、prune accounting、shared-memory `cj` preload offset、energy reduction 应使用 `c_parallelExecutionWidth`，不要继续写死 `warp_size` 或 32。
- `c_parallelExecutionWidth` 是编译期常量，来自 `sc_gpuParallelExecutionWidth(sc_layoutType)`；split=2 时为 32，no-split=1 时为 64。

### 5. PME 64-wide

GROMACS HIP PME 已经用 `parallelExecutionWidth` 模板参数支持 32/64 subgroup。CUDA/MACA PME 应参考这个结构，而不是简单全局替换常量。

实现要点：

- 在 `src/gromacs/ewald/pme_gpu_constants.h` 定义 `c_pmeCudaParallelExecutionWidth = warp_size`。因此 NVIDIA CUDA 为 32，MACA C500 为 64。
- 将 CUDA PME spread/solve/gather/spline 相关 kernel 和 helper 参数化为 `parallelExecutionWidth`。
- 用该参数控制 block size、`atomsPerWarp`、`warpIndex`、active warps、shuffle width、reduction buffer size 和 kernel pointer binding。
- `PmeGpuProgramImpl` 中的 PME kernel function pointer 要绑定到 `c_pmeCudaParallelExecutionWidth` 实例。

典型文件：

- `src/gromacs/ewald/pme_gpu_constants.h`
- `src/gromacs/ewald/pme_spread.cu`
- `src/gromacs/ewald/pme_solve.cu`
- `src/gromacs/ewald/pme_gather.cu`
- `src/gromacs/ewald/pme_gpu_calculate_splines.cuh`
- `src/gromacs/ewald/pme_gpu_program_impl.cu`

### 6. Nonbonded FEP 64-wide

Nonbonded FEP 是自由能微扰相关的非键 GPU kernel。GROMACS 中该 GPU 路径主要在 CUDA 后端实现；虽然 HIP 后端没有可直接复制的 FEP kernel，但 FEP CUDA kernel 的 work partition 与 normal nonbonded kernel 类似，算法本身不要求固定 32-lane。因此在 C500 64-wide/no-split 适配中，FEP 也应使用 `c_parallelExecutionWidth`，而不是继续写死 `c_cudaLogicalWarpSize=32`。

典型位置包括：

- `src/gromacs/nbnxm/cuda/nbfe_cuda.cu`
- `src/gromacs/nbnxm/cuda/nbfe_cuda_kernel.cuh`
- `src/gromacs/nbnxm/cuda/nbfe_foreign_cuda_kernel.cuh`
- `src/gromacs/nbnxm/cuda/nbnxm_cuda_kernel_utils.cuh`
- `src/gromacs/nbnxm/tests/freeenergygpukernel.cpp`

实现要点：

- host launch 中 `nriPerBlock` 使用 `blockSize / c_parallelExecutionWidth`，即一个 parallel execution group 处理一个 `ri`。
- device kernel 中 `wid_global`、`tid_in_warp`、`__shfl_sync` width、`cub::ShuffleIndex` width、j-list loop stride 都使用 `c_parallelExecutionWidth`。
- i-force、energy、`dV/dlambda` 归约必须同样使用 `c_parallelExecutionWidth`；不要只改 loop stride。
- `reduce_fep_force_i_warp_shfl` 应模板化为 `reduce_fep_force_i_warp_shfl<logicalWarpSize>`，归约轮数用 `StaticLog2<logicalWarpSize>`，写回条件用 `tidx % logicalWarpSize == 0`。
- 测试 helper 中的 FEP GPU launch 也要同步改成 `c_parallelExecutionWidth`，否则单测和生产 launch 路径不一致。

验证时至少跑 `NbnxmGpuTests` 和 `MdrunFEPTests`，再跑完整 `make_maca test -j16`。如果出现 FEP energy/force/`dV/dlambda` mismatch，不要降低容差或改参考答案；应回到线程到 pairlist、归约宽度、mask 类型和 atomic 写回路径逐项排查。

## 验证要求

最终结论必须基于可复现证据：

- clean configure 成功。
- `make_maca -j16 tests` 成功。
- `make_maca -j16 install` 成功。
- `make_maca test -j16` 下所有已配置单元测试通过。
- 未修改测试参考答案、容差或跳过列表。
- 记录测试总数、失败数、日志路径、已有基线 commit hash、候选 patch 哈希和 CMake 参数。

关键关注项包括 NBNXM、PME、FEP 相关单元测试和 mdrun 测试。全量单测通过是交付底线，但不等于穷尽所有生产体系；如需更高可信度，可补充 CPU vs MACA GPU 能量/力对照、官方 regression tests、小水盒/蛋白/PME/FEP smoke case。

## Review 清单

- MACA 构建会触达的 CUDA 路径中是否仍有未 guard 的 PTX asm。
- 64-bit mask 是否在函数参数、局部变量和 helper 中完整传播。
- NBNXM 普通非键是否使用 no-split 和 `c_parallelExecutionWidth`。
- PME 是否真正通过 `parallelExecutionWidth` 参数化 spread/solve/gather，而不是局部硬改。
- FEP 是否同 normal NB kernel 一样使用 `c_parallelExecutionWidth`，并且 force、energy、`dV/dlambda` 归约宽度也同步改动。
- atomic、fence、acquire/release/relaxed 语义是否被保留。
- NVIDIA CUDA 默认行为是否被 `__MACACC__`、`GMX_GPU_MACA` 或等价条件保护。
- 正确性结论是否来自全量测试日志，而不是“能编译”或“看起来合理”。