# 环境与构建

本页适用于 cu-bridge 配套、构建与安装分析。命令中的路径、并行度和应用参数应替换为当前任务的真实值。

## 版本配套

先区分 MACA SDK 发布版本、cu-bridge 发布版本和对应用呈现的 CUDA 兼容版本。三者用途不同，不能互相代替。

1. 从 `${MACA_PATH}/Version.txt` 或安装包信息读取 SDK 版本；从配套源码的 `Version.txt`、安装包信息或部署记录确定 cu-bridge 身份。
2. 检查所用 cu-bridge 的根 `CMakeLists.txt` 中版本比较逻辑及配套要求，确认它要求 SDK 版本相等还是满足最低版本。版本比较可能只取前三段，仍应保留完整版本以区分补丁构建。
3. SDK 低于 cu-bridge 要求时，选择与目标 SDK 配套的 cu-bridge；不要删除版本检查或直接升级共享 SDK。SDK 版本更高也不能单独证明所需功能和 ABI 兼容。
4. 保留实际使用的 tag/commit、工具路径和配置日志，在这组环境上验证目标案例。不凭分支名称推断兼容范围。

安装目录未必包含 `Version.txt`，缺失时查包信息、部署记录、源码 commit 或关键文件校验值；不要用兼容 CUDA 版本猜测 cu-bridge 版本。

## 进程内环境

以下是候选构建的 Bash 示例，在应用工作副本根目录运行；不会修改 shell 启动文件。实际使用前先保留原命令和有关变量值。

```bash
(
  export MACA_PATH=/opt/maca
  export CUCC_PATH="$MACA_PATH/tools/cu-bridge"
  export CUDA_PATH="$CUCC_PATH"
  export CUCC_CMAKE_ENTRY=2
  export PATH="$CUCC_PATH/tools:$CUCC_PATH/bin:$MACA_PATH/mxgpu_llvm/bin:$MACA_PATH/bin:$PATH"
  export LD_LIBRARY_PATH="$MACA_PATH/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
  export CUBRIDGE_HOME="$(mktemp -d "$PWD/.cubridge.XXXXXX")"
  unset WCUDA_HOME USE_WCUDA_FOR_MULTI_JOB USE_CMAKE_MACA

  command -v cucc cmake_maca make_maca ninja_maca mxcc python3
  readlink -f "$CUCC_PATH/bin/cucc"
  cat "$MACA_PATH/Version.txt"
  mxcc --version
  cucc -V

  # 将所需的配置和编译命令放在这个子 shell 内，沿用同一 CUBRIDGE_HOME。
)
```

示例将选定工具链路径前置，便于避免其他版本抢先匹配。检查 `CUDA_HOME`、CMake cache 中的 toolkit 路径、`MXCC_PATH`、`MACA_CLANG_PATH` 是否仍指向另一套环境；只调整当前应用实际使用的项。`macainfo` 可检查 SDK 设备发现；它不能代替应用运行验证。

临时目录有几个容易混淆的实现细节：

- 默认 `CUBRIDGE_HOME` 基目录是 `$HOME`，多数构建包装器再拼接 `/cu-bridge`；编译中间脚本还会落在基目录的 `/_nvcc`。
- 非空 `WCUDA_HOME` 会覆盖 `CUBRIDGE_HOME`。`USE_WCUDA_FOR_MULTI_JOB` 还会按父进程号增加子目录；显式分配任务目录时不必同时启用它。
- `cmake_mock` 会重建其临时目录，因此不要共用其他任务的目录，也不要把应用源码放进该临时树。切换配置后先保存日志，再只清理本任务可丢弃的构建状态。
- `USE_CMAKE_MACA=1` 会启用 `make_maca` 中替换 `cmake` 可执行文件的处理路径。本部署禁止修改共享环境，因此不要启用该路径；采用明确的包装器入口。

## 构建入口

| 原项目方式 | cu-bridge 入口 | 需要检查 |
| --- | --- | --- |
| 单文件 `nvcc` | `cucc` | 原 include、define、链接参数是否保留及正确转换 |
| 原 Makefile | `make_maca` | 子 Make、绝对路径 `nvcc` 和自动重新配置 |
| CMake + Make | `cmake_maca` 后 `make_maca` | 配置和编译都走候选环境 |
| CMake + Ninja | `cmake_maca -G Ninja` 后 `ninja_maca` | 实际编译器、response file 和链接命令 |
| Autotools/configure | `pre_makefile ./configure` 后 `make_maca` | configure 探测到的编译器与库路径 |
| Python 包/扩展 | 检查其底层 CMake/Ninja/编译器入口 | 不能假设替换顶层命令就覆盖 `setup.py`/扩展构建 |

在已配置环境的候选目录内选择一条路线。`4` 是示例并行度，按获准资源调整；原项目必需参数继续保留。

```bash
# 单文件；main.cu 为当前应用源文件。
cucc main.cu -o main

# 原 Makefile 项目，在源码工作副本目录执行。
make_maca -j4

# CMake + Make，使用新的构建目录。
cmake_maca -S . -B build-maca -DCMAKE_BUILD_TYPE=Release
(cd build-maca && make_maca -j4 VERBOSE=1)

# CMake + Ninja，使用不同目录。
cmake_maca -S . -B build-maca-ninja -G Ninja -DCMAKE_BUILD_TYPE=Release
(cd build-maca-ninja && ninja_maca -j4 -v)

# Autotools；沿用应用原本需要的 configure 参数。
pre_makefile ./configure
make_maca -j4
```

这些是应用构建入口，不是构建 cu-bridge 自身的命令。`cmake --build`、IDE 或 Python 启动的构建是否经过包装器，需查实际子命令；不要把 CMake 模拟探测生成的产物当作真实应用编译产物。

## CUDA 兼容版本与目标架构

| 变量/参数 | 作用 | 判断边界 |
| --- | --- | --- |
| `CUCC_CMAKE_ENTRY=2` | `cmake_maca` 选择 `cmake_mock`；其他值走 `cmake_assist` | 模拟 toolkit 探测，常规源码迁移无需另装 NVIDIA Toolkit；项目若确实调用其他 NVIDIA 工具需另查 |
| `CUCC_CUDA_VERSION` | `cucc` 将指定值写入真实编译命令的 `CUDA_VERSION` 宏 | 按应用要求设置，并检查实际宏值与所选代码分支；不新增 API 实现 |
| `CUCC_TARGETS` | 指定 MACA 目标，逗号分隔后转换为 `--offload-arch=...` | 从目标设备与 SDK 能力确定值，不使用 `sm_80` 作为 MACA ISA |
| `CUCC_TARGETS_FROM_DEVICE` | 包装器设备探测后的内部结果 | 查 `multi_target.sh` 和实际输出，不将其当固定 GPU 型号表 |
| `MXCC_PATH` | 覆盖实际 `mxcc` 可执行文件路径 | 值是可执行文件，不是目录；未设时继续查 `MACA_CLANG_PATH`、`MACA_PATH` 等 |
| `WCUDA_DEBUG=1` | `cucc` 等脚本输出额外转换信息 | 同时保留实际构建日志，不能依赖一个变量覆盖所有工具 |

`cucc -V`、`tools/mock/mock_nvcc.py` 的探测输出和实际编译宏可能由不同逻辑生成；设置 `CUCC_CUDA_VERSION` 不代表所有版本输出都会同步变化。若 CMake 版本检测仍失败，分别核对检测变量、实际头文件宏、探测入口和最终编译参数。

无设备或跨目标构建需要显式指定 ISA 时，依据目标设备和编译器支持选择 `CUCC_TARGETS`。当包装器要求主版本目标时，使用对应的主版本形式（如 `xcore1000`），不要直接复制完整设备 ISA（如 `xcore1088`）。这些是格式示例，具体值须由目标环境确定。

`CMAKE_CUDA_ARCHITECTURES`/`-gencode` 会参与 CUDA 工程条件编译，cu-bridge 可能把它们映射为兼容宏；它们不等于实际生成的 MACA ISA。沿用已验证配置，检查转换后的 `--offload-arch` 与相关宏。不要盲目用 `native` 或固定某个 `sm_*` 绕过检测。

## cu-bridge 自身安装

已有 SDK 自带可用 cu-bridge 时先复用。只有任务确需安装且授权涵盖目标环境时，才执行源码安装；该部署通常只做现有环境检查和应用适配。

设置 `MACA_PATH`，准备所需 MACA 头文件/库，按源码中的 `cmake_minimum_required` 检查 CMake 版本；mock 入口还需要 `python3`。安装前检查根及各子目录的 CMake 文件，确认以下路径行为：

- `LIB_DEST_DIR` 可由根 CMake 直接设为 `${MACA_PATH}/lib`，供 `runtime_cu`、`symbol_cu`、`ToolsExt_cu` 的安装规则使用；逐项确认最终落盘位置。
- `CMAKE_INSTALL_PREFIX` 主要控制工具、头文件等；仅把 prefix 指到工作目录不能隔离所有写入。
- 构建阶段的 `softlink ALL` 可在 prefix 下创建目录和软链接，检查安装路径不能只看 install 阶段。
- 若 `LIB_DEST_DIR` 被普通 `set()` 赋值，不能假设 `-DLIB_DEST_DIR=...` 能覆盖它。

以下命令仅适用于获准的私有 SDK 副本/可丢弃容器，`MACA_PATH` 不能仍指向共享 SDK。源码需预先固定到配套 tag/commit，并位于独立工作副本。

```bash
export MACA_PATH=/path/to/private-maca
cmake -S /path/to/cu-bridge-source -B /path/to/cu-bridge-build \
  -DCMAKE_INSTALL_PREFIX=/path/to/private-cu-bridge
cmake --build /path/to/cu-bridge-build --parallel 4
cmake --install /path/to/cu-bridge-build
```

若没有可用私有安装环境，交付配套版本和安装影响分析，继续不依赖安装的诊断。不要递归执行 `sudo chmod`、向共享 `${CUCC_PATH}/bin` 新建 `nvcc` 软链接或覆盖系统 CMake。
