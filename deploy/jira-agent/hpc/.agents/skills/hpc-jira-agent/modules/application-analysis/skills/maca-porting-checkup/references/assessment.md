# MACA 移植评估细则

用于依赖或架构语义的深入判断，以及完整移植体检报告。基础流程见[技能入口](../SKILL.md)。

## 深度分析规则

- 先分清失败路线和迁移路线：`UPSTREAM_DIRECT` 表示直接按上游 CUDA/NVIDIA 生态构建；`MACA_ADAPTED` 表示使用 MACA wrapper、shim、替代 backend 或功能裁剪后的路线；`FORWARD_PORT` 表示从可用旧版本/相近实现前向迁移。报告可以记录当前失败路线，但推荐路线和工作量必须按最可行路线评估。
- 每个依赖都先判定角色：header-only、build-time、link-time、runtime、Python-only、test-only 或 optional。再判断是否当前目标必需、影响哪些 target/用户功能、是否能升级/降级/guard/shim/替换 backend。
- 每个失败项都拆成 build、link、runtime、correctness、feature、performance 维度。性能 fast path 缺失不能直接写成 correctness 高风险；有 fallback 时 correctness 风险通常为低，性能另评。
- 对库 API 和架构 fast path 先查保护逻辑：版本宏、`dlsym`、dynamic load、`try/catch`、capability probe、fallback path、generic path、CPU path、可关闭 option。源码已有保护时，风险应降级为验证项或性能项。
- 按交付层级给结论：C++ core、CLI/C API、主要功能模块、Python/server/packaging、完整性能优化。不要用一个失败模块把整个 APP 判为 blocked。
- 首个真实错误必须实证；后续风险可以基于 include graph、CMake target、源码调用点推断，但要标注为预测风险，不能替代编译或运行证据。
- 通用运行时语义也要扫：cooperative launch/grid sync、spin lock/atomic CAS loop、stream ordering、async free、CUDA graph、runtime JIT/NVRTC/nvJitLink、zero-length descriptor、null device pointer、shared memory 上限。

## 判定规则

### 状态

- `NOT_VERIFIED`：未验证。
- `CONFIGURE_ENV_BLOCKED`：入口环境未过，且合理修复仍未推进。
- `CONFIGURE_PROGRESSED`：配置已推进到下一层错误。
- `BUILD_FAIL_<REASON>`：wrapper 编译/链接失败。
- `BUILD_PASS`：wrapper 编译通过。
- `BUILD_PASS_WITH_PATCH`：源码最小修复后 wrapper 编译通过。
- `LINK_PASS_MACA_LIBS`：最终产物链接到 MACA/cu-bridge 库。
- `RUNTIME_PASS_PARTIAL`：代表性运行通过，完整正确性未覆盖。
- `RUNTIME_FAIL_<REASON>`：运行失败且原因已定位。
- `CORRECTNESS_UNKNOWN`：缺少正确性标准或未验证。

### 版本、依赖与功能

- 版本不一致不能只按版本号判风险；看实际 API、类型、函数签名和可升降级性。
- CMake/wrapper/版本探测差异按实际 API/ABI 证据评估，候选环境变量与参数探针需和原始复现分离。版本升级只能在批准的隔离环境执行；未经验证不能把“可降级检查”当作低风险结论。真实 API/ABI 缺失且必要功能无可行路线时才据证据升高风险。
- GPU 生态组件先查本机 SDK、cu-bridge、MACA 安装或用户提供的支持矩阵。无本机证据时可写 `UNKNOWN`，但要说明受影响 target 和功能。
- 分析 GPU 生态组件时区分“首个真实编译错误”和“后续独立风险”。例如 CCCL/libcu++ 的 driver API 缺失、RMM 的 API/版本门槛、RAFT 的 bf16/fp8 类型冲突应分别描述，不能合并成一个 RAPIDS 栈风险。
- 依赖不可用时必须说明删减或降级的用户功能，不只写“可禁用”。
- 如果依赖只服务可选模块、测试、Python 包装、server、performance fast path 或特定 backend，应写清当前交付层级是否受阻；不能把非核心层级的问题写成全局阻塞。
- CMake 的 `CUDA20`、`CUDA17` 表示 CUDA 源文件 C++20/C++17 语言标准，不是 CUDA Toolkit 20/17。

### 架构语义

- inline PTX/asm 先判断替代路线。若可用 C/C++、runtime API、cu-bridge 头文件、MACA 内建或 MACA 汇编保持 correctness，风险为低，性能另评；只有语义不可替代或必须保留 NVIDIA fast path 时升为中/高。
- 架构 fast path 如 MMA、ldmatrix、cp.async、TMA、tensor map 等，如果首阶段可禁用或走 generic/SIMT path，correctness 风险可低，完整功能/性能风险另列。
- warp/shfl 不要机械从 32 改 64。若代码使用逻辑 warp32 且 MACA 对 32-bit mask 按 width=32 兼容，只需要提示正确性存在低风险；混用 32/64 lane 假设、显式 64-bit mask 或全 wave64 协作才列核心风险。
- cooperative groups、global barrier、warp 内 spin lock、跨 kernel ordering 和 async lifetime 问题通常不会在编译期暴露；若源码存在这些模式，列为运行时语义验证项，并给出最小触发测试。

### 分类

- `SUPPORTED_BY_CUBRIDGE`：本机有覆盖证据，需要验证但不是风险。
- `BUILD_WRAPPER_NEEDED`：构建未走 MACA wrapper 或硬编码工具链。
- `VERSION_OR_API_ADAPTATION`：版本/API/签名差异，需要升级、降级、guard 或 shim。
- `GPU_STACK_DEP_BLOCKER`：GPU 生态依赖缺失且当前目标无法绕过。
- `FEATURE_DISABLED_BY_PORT`：迁移需要删减或降级功能。
- `ARCH_SEMANTICS_RISK`：架构语义或性能路径需评估。
- `UNSUPPORTED_IN_CUBRIDGE`：本机证据显示不支持。
- `UNKNOWN`：源码、标准、依赖或本机证据不足。

## 报告格式

完整移植体检使用以下 0–9 章结构；工单中的局部兼容检查只返回相关结论和证据，不要求另写完整体检报告。

```text
MACA 移植体检报告

0. 目录

1. 结论摘要
   - 当前编译状态：必须先写 编译结论：成功/失败/未验证，再写状态码和一句话证据
   - 核心移植风险
   - 风险级别
   - 推荐迁移路线

2. 体检对象与验证环境
   - 源码来源 / 本地路径 / 版本或 commit
   - 本次体检范围
   - 环境、工具链、wrapper、GPU、SDK 版本

3. 编译路线以及当前编译状态
   - 配置命令、编译命令、必要运行命令
   - 编译结果：成功/失败/未验证
   - 已解决问题记录：环境问题、构建问题、代码问题分别列出；每项写初始现象、解决方法、证据、验证结果
   - 成功：贴成功日志片段、最终产物路径、`ldd` / `readelf -d` / 链接日志中的 MACA/cu-bridge 库证据
   - 失败：贴关键失败日志片段，包含首个真实错误和失败 target
   - CONFIGURE / BUILD / LINK / RUNTIME / CORRECTNESS 状态

4. cu-bridge 证据
   - 已检查路径
   - 覆盖库/工具/API
   - 明确不支持标记
   - 仍需验证项

5. 源码规模与 GPU/CUDA 使用地图
   - 文件数、代码行数、统计口径
   - kernel / launch / runtime-driver API / CUDA 库 / 头文件生态
   - 构建工具链依赖
   - 架构假设

6. 主要风险清单
   - 先给总表：ID / 风险项 / 风险等级 / 影响范围 / 当前目标是否阻塞 / 依据 / 建议
   - 再逐项说明证据、影响和修复路线

7. 非风险但需验证项
   - cu-bridge 已覆盖或编译已通过但仍需运行/正确性确认的项

8. Patch 候选列表
   - 已应用 patch：APPLIED + AUTO_SAFE / AUTO_NEEDS_TEST / MANUAL_ONLY，附验证结果
   - 候选 patch：PROPOSED + AUTO_SAFE / AUTO_NEEDS_TEST / MANUAL_ONLY，附预期验证命令
   - 不要把环境处理过程只放在这里；环境处理细节放第 3 章，这里只列需要保留或可生成的 patch

9. 工作量评估与迁移路线
   - S / M / L / XL
   - 评估理由
   - 未完成验证：unit test / run 命令、正确性标准、必要时 benchmark / profiling 计划
   - 有序迁移步骤
   - 分层目标：C++ core / CLI-C API / 主要功能模块 / Python-server-packaging / 性能优化
```

摘要要求：

- “当前编译状态”只写结论和核心状态，不解释非风险项。
- 不在摘要中写“不是某库缺口”“cu-bridge 已覆盖所以不是风险”等排除性说明。
- cu-bridge、PTX、warp/shfl 等细节放后续章节。

发现项可用这个紧凑格式：

```text
发现：<问题>
位置：<file:line 或日志>
分类：<分类>
风险：<低/中/高，区分 correctness 和性能>
证据：<最小证据>
建议：<首阶段 correctness 路线 + 后续性能路线>
可 patch 性：AUTO_SAFE / AUTO_NEEDS_TEST / MANUAL_ONLY
置信度：高/中/低
```

已解决问题记录可用这个格式，放在第 3 章：

```text
已解决：<环境/构建/代码问题>
初始现象：<日志或命令输出>
处理动作：<命令、配置参数、源码修改或依赖调整>
修改范围：<文件/target/环境变量；无源码修改则写无>
验证结果：<下一阶段通过、错误推进到新层、或构建成功>
保留方式：<已应用 patch / 临时环境设置 / 无需保留>
```

## Patch 规则

- `AUTO_SAFE`：语义风险低的构建、路径、脚本或诊断工具修改。
- `AUTO_NEEDS_TEST`：机械上可行但必须编译/运行验证的源码或构建修改。
- `MANUAL_ONLY`：kernel 算法、架构 fast path、数值容忍度、不可替代库 API、用户可见结果或性能策略修改。

每个 patch 绑定一个发现项，并说明验证命令和结果。第 8 章同时允许列 `APPLIED` 和 `PROPOSED`，但必须区分：`APPLIED` 是本轮已经改过并验证过的修改，`PROPOSED` 是尚未实施或尚未验证的候选。只有 wrapper 编译通过且必要运行/正确性验证通过后，才能称为成功 patch。

## 报告前自查

- 是否把第一次 build 失败当成最终移植结论？
- 是否查过源码内的 fallback、feature flag、optional dependency、backend 抽象和 shim 空间？
- 是否按依赖角色和交付层级判断影响范围？
- 是否把性能 fast path 缺失误写成 correctness 高风险？
- 是否把 cu-bridge 已覆盖项写进主要风险？
- 是否对缺失依赖说明了受影响用户功能？
- 是否区分了实证错误和预测风险？
- 是否明确写出编译是成功、失败还是未验证？
- 是否记录了本轮已经解决的环境/构建/代码问题，以及每项是如何解决和验证的？
- Patch 候选列表是否区分了 `APPLIED` 和 `PROPOSED`，没有把临时环境处理误写成源码 patch？

## 常用命令

搜索 GPU/CUDA 使用：

```bash
rg -n "__global__|__device__|<<<|cuda[A-Z]|cu[A-Z]|cublas|cudnn|cufft|curand|cusolver|cusparse|nccl|npp|nvjpeg|nvrtc|nvtx|cupti" .
rg -n "nvidia-smi|nvprof|nsys|ncu|nvcc|CUDA_HOME|CUDA_PATH|/usr/local/cuda|sm_[0-9]+|compute_[0-9]+|__CUDA_ARCH__" .
rg -n "warpSize|0xffffffff|__shfl|__syncwarp|cooperative_groups|cp\\.async|ldmatrix|mma\\.sync|asm volatile|asm\\(" .
rg -n "option\\(|ENABLE_|DISABLE_|BUILD_|SKIP_|fallback|Fallback|dlsym|dynamic_load|find_package\\(|FetchContent|plugin|backend|factory|shim" .
rg -n "cudaLaunchCooperativeKernel|this_grid\\(|grid_group|atomicCAS|while\\s*\\(.*atomic|cudaGraph|nvrtc|nvJitLink|cudaStreamBeginCapture|cudaMallocAsync|cudaFreeAsync|sharedMemPerBlock|MaxDynamicSharedMemory" .
```

统计源码规模：

```bash
command -v cloc || command -v scc || command -v tokei || true
find . -type f \( -name '*.cu' -o -name '*.cuh' -o -name '*.cpp' -o -name '*.cc' -o -name '*.cxx' -o -name '*.c' -o -name '*.h' -o -name '*.hpp' -o -name '*.hh' -o -name 'CMakeLists.txt' -o -name '*.cmake' -o -name '*.sh' \) -print0 | xargs -0 wc -l | tail -n 1
```

收集 cu-bridge 证据：

```bash
find "$HOME/cu-bridge/CUDA_DIR" -maxdepth 3 \( -name 'nvcc' -o -name '*.so*' -o -name '*.a' -o -name '*.h' \)
find /opt/maca/tools/cu-bridge -type f | head
rg -n "mcErrorNotSupported|MC_ERROR_NOT_SUPPORTED|TODO|skipped|not support|not implemented" /opt/maca/tools/cu-bridge $HOME/cu-bridge 2>/dev/null
rg -n "\"-lcublas\"|\"-lcudnn\"|\"-lnccl\"|\"-lcufft\"|\"-lcurand\"|\"-lcusolver\"|\"-lcusparse\"|\"-lnpp|\"-lnvjpeg\"|\"-lnvrtc\"" /opt/maca/tools/cu-bridge/bin/conf*.json 2>/dev/null
readelf -Ws /opt/maca/lib/*.so 2>/dev/null | rg "<symbol-or-api>"
```
