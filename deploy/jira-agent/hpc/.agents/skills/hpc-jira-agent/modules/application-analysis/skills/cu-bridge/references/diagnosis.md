# 故障定位与兼容性

按下面的路径定位构建转换、API 映射和运行行为，并核对目标环境的实际实现。先保存原错误，逐项改变候选条件，避免同时更换 SDK、编译器、依赖与应用参数而失去归因依据。

## 按失败阶段检查

| 现象 | 优先核对 | 下一步证据 |
| --- | --- | --- |
| 找不到 CUDA Toolkit / CUDA compiler | `CUCC_PATH`、`CUDA_PATH`、`CUCC_CMAKE_ENTRY`、`python3`、实际 `cmake_maca` 路径 | 新候选构建目录中的 configure 日志与 `CMakeCache.txt` |
| 找不到 `${CUCC_PATH}/bin/nvcc` | 是否有构建入口绕过包装器，是否硬编码路径 | 子进程完整命令；优先使用项目可配置的编译器入口 |
| CUDA 版本不符 | `cucc -V`、mock 输出、头文件宏与项目检测逻辑分别检查 | `CUCC_CUDA_VERSION` 是否出现在真实编译命令，不能只看版本字符串 |
| `please set env CUCC_TARGETS` / 设备架构探测失败 | 目标 ISA、容器设备可见性、`mx-smi`、`multi_target.sh` | 获准目标的 ISA 依据及转换后的 `--offload-arch` |
| 不识别参数、参数缺失或含空格定义出错 | 原命令、response/options file、`bin/conf*.json`、`conf_regex.yaml` | `cucc` 转换后的 `mxcc` 命令；某些 CUDA 参数会被过滤 |
| MPI 编译或运行异常 | 实际 `mpicc`/`mpicxx`/`mpirun` 及 `libmpi` 来源 | 编译与运行 MPI 是否同套；不要默认是 CUDA API 缺陷 |
| undefined reference / symbol lookup error | 符号映射、链接顺序、导出符号、实际加载库版本 | 失败符号、映射位置、ELF 依赖及目标库的符号表 |
| 多个构建互相丢文件或偶发失败 | `CUBRIDGE_HOME`、`WCUDA_HOME`、共享 cache | 每任务独立临时基目录，保存旧日志后单独复现 |
| configure 成功但没有可运行应用 | 是否仅跑过 mock 探测，真实 target 是否编译 | 完整构建日志、预期产物、加载和目标案例结果 |
| 编译通过但结果错误/卡死 | API 返回值、初始化、异步生命周期、warp/同步语义 | 最小相关探针和应用原案例，保留原精度判据 |

项目固定要求 `nvcc` 文件名时，可分析项目局部适配方案；不要直接给共享安装创建软链接。`cucc` 并不完整实现 nvcc 的所有选项，文件名替换只是构建入口处理。

## 编译参数与日志

在隔离候选环境启用 `WCUDA_DEBUG=1`，Make 使用 `VERBOSE=1`，Ninja 使用 `-v`。需要追踪时再对相关脚本局部使用 Bash trace，并注意日志不要包含凭据。

关键文件：

| 位置 | 用途 |
| --- | --- |
| `bin/cucc`、`bin/process_args.py` | 参数归类、过滤、替换和实际编译器调用 |
| `bin/conf.json`、`bin/conf_gnu.json`、`bin/conf_gfortran.json` | CUDA、主机 C/C++、Fortran 的转换规则 |
| `bin/conf_regex.yaml`、`tools_src/gomxcc/` | 编译命令后续转换 |
| `tools/cmake_maca`、`tools/cmake_mock`、`tools/cmake_assist` | 配置入口与探测环境 |
| `tools/link.json`、`tools/mock/stub_link.json` | 构建与模拟探测的工具/库映射；两者不能混为一份配置 |
| `${CUBRIDGE_HOME}/_nvcc` | 中间命令脚本；若被 `WCUDA_HOME` 覆盖则查有效基目录 |

`cucc` 会删除部分临时脚本，需及时保存仍存在的转换产物或调试输出。记录 wrapper 返回码的同时核查真实子命令和最终产物；不能只凭最后一条 shell 命令返回 0 判断构建成功。

保存管道日志时可使用 Bash 的 `pipefail`，避免 `tee` 掩盖编译失败。示例在已有隔离环境内、候选 Make 构建目录执行；部署要求锁时在外层包裹本次构建：

```bash
set -o pipefail
WCUDA_DEBUG=1 make_maca -j4 VERBOSE=1 2>&1 | tee build.log
```

## API 与动态库追踪

API 可能是宏映射、类型适配、非平凡 wrapper 或仅有声明。针对失败的具体符号检索兼容头文件和实现，不只匹配名称前缀。

```bash
# 将 cudaMalloc 换成当前失败的确切符号。
rg -n 'cudaMalloc' "$CUCC_PATH/include"
# 有配套源码时继续追踪实际实现。
rg -n 'wcudaMalloc|mcMalloc' /path/to/cu-bridge-source/src
readelf -d /path/to/application
nm -D --defined-only /path/to/actual-library.so
```

主要实现位置是 `include/bridge/runtime/`、`src/bridge/runtime/src/` 和 `src/symbol_cu/src/`；数学库查对应 `include/bridge/` 子目录。记录所需符号的参数、类型、返回值，以及是否存在 `NOT_SUPPORTED` 分支或功能限制。

以下是 CUDA 名称到 MACA/兼容库的典型映射。用目标环境的 `tools/link.json`、实际软链接和符号表核对；映射存在不代表完整功能已受支持：

| CUDA 名称 | 映射到的 MACA/兼容库 |
| --- | --- |
| `libcudart.so` | `libmcruntime.so` |
| `libcuda.so`、`libcuda.so.1` | `libsymbol_cu.so`；其下还有 `runtime_cu` wrapper |
| `libcublas.so`、`libcublasLt.so` | `libmcblas.so`、`libmcblasLt.so` |
| `libcufft.so`、`libcufftw.so` | `libmcfft.so`、`libmcfftw.so` |
| `libcurand.so`、`libcusolver.so`、`libcusparse.so` | `libmcrand.so`、`libmcsolver.so`、`libmcsparse.so` |
| `libcudnn.so`、`libnccl.so` | `libmcdnn.so`、`libmccl.so` |
| `libcupti.so`、`libnvToolsExt.so` | `libmcpti.so`、`libmcToolsExt.so` |
| `libnvrtc.so` | 映射配置指向 `libmcruntime.so`；仍需核对 `nvrtc_wrapper`、`mcrtc` 和实际符号路径 |

另有 `stubs` 目录映射，同名库在不同目录可能指向不同目标；库名带 `_static.a` 也不能证明落盘文件确为静态归档。用 `readlink -f`、`file`、符号表和实际链接命令核实，不把探测 stub 路径当运行时依赖。

`readelf -d` 显示声明依赖，不能独自证明运行时加载路径。可信的本任务产物可结合 `ldd`、运行日志或进程映射检查实际加载库。对 `dlopen`/`dlsym` 另查字符串和符号：源码中的库名字符串、Python 代码生成和外部二进制调用不一定被 cu-bridge 自动替换。

MPI 默认映射含 `${MACA_PATH}/ompi/bin/mpicc`、`mpicxx`。自定义 MPI 问题先确认编译/链接/启动路径及 GPU 通信支持；需要调整映射时只在授权覆盖的私有候选副本中评估，不能直接修改共享 `tools/link.json`。

## PTX、warp 与数值行为

- **内联 PTX**：先识别具体指令和参与的算法路径。转换工具位于 `tools_src/ptx2cpp/ptx2cpp`，用法是 `ptx2cpp input.cu output.cu`；在工作副本中输出到新文件并审阅差异。使用前核对工具是否存在、可执行且适配目标主机。
- **转换限制**：`wmma/mma/ldmatrix/stmatrix/mvmatrix` 等矩阵指令需专项检查，不能假设 ptx2cpp 能自动完成适配。不能转换时，评估保持语义的 C/C++、目标内建函数或已支持库路线，随后验证数值与性能。不能把该工具当成任意 PTX、cubin 或 SASS 的兼容层。
- **warp**：先核对目标设备、编译器与实际 `warpSize`，再检查逻辑分组、mask 位宽、shuffle 的 width、ballot 类型、lane 编号和部分活跃线程。对 64-lane 设备还要区分完整硬件 warp 与算法中的 32-lane 逻辑分组；仅扩大 mask 或全局替换 32 都不足以证明正确。
- **内存初始化**：`cudaMalloc` 分配的内存不会自动清零。依赖初始零值的代码应显式初始化并检查同步顺序；不能把一次观测到的零值或某种分配大小下的行为当作接口保证。
- **浮点差异**：保留原容差与参考结果，定位归约顺序、FMA、数学函数或初始化差异；不能通过扩大误差阈值让迁移通过。
- **异步与同步**：缩小到相关 stream/event、缓冲区生命周期与 kernel 同步范围；必要的诊断同步应标为诊断变更，不把其运行时间用作正式性能结果。

## JIT 与 Python/PyTorch

仓库包含 `nvrtc_wrapper.cpp` 和 `mcrtcCompileProgram` 调用，因此不能仅因出现 `nvrtc` 就断言无支持。检查实际生成的源码、编译选项、头文件、目标 ISA、module 输入格式和运行时加载链；NVRTC 名称存在不代表任意 NVIDIA PTX 都能执行。

只有任务涉及 Python 扩展或 PyTorch 时才深入该路线。先确认应用、框架、扩展和 SDK 的配套关系，再按实际构建入口定位；不同框架版本的构建脚本和选项可能不同。重点检查：

- `setup.py`、底层 CMake/Ninja 及 `torch/utils/cpp_extension.py` 的实际编译器与参数。
- `dlopen` 库名、设备字符串、warp 辅助函数、内联 PTX 和第三方组件。
- `USE_MACA` 可作为项目适配开关，但必须检查项目是否定义并消费它；仅传入同名变量不能启用 MACA 支持。
- Flash Attention、Jiterator、cuDNN v8 等选项按必需功能和实际支持判断；不能无条件关闭来换取构建通过。

已有 MACA 适配框架可用于对照构建配置与实现；是否采用由任务指定的版本、依赖和授权范围决定，不自动换仓库或重装框架。

## 责任判断所需证据

应用硬编码编译器、错误同步、缺少初始化或构建选项遗漏，可据复现判断应用侧责任。正确调用经 wrapper 转换后参数、类型或返回行为改变，则整理转换层最小复现。直接 MACA 调用仍失败时，继续收集编译器/运行库证据；一次失败或未找到声明都不足以自动判定责任。

交接保留源码/版本、输入与原判据、转换前后命令、相关符号/实现路径、首个有效错误、最小复现结果，以及影响的必需功能。明确“已证实”“待验证”，不替外组修改 SDK。
