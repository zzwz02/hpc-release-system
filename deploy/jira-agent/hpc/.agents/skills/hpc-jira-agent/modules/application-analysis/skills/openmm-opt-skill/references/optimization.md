# OpenMM MACA 优化

此参考文件用于记录 OpenMM 在 MACA 上的专属性能优化工作。优化主循环是：编译 -> 运行 Benchmark -> 判定性能 -> 判定正确性。

## 修改范围

- 优化代码修改仅限 OpenMM 源码的 `platforms/` 目录。
- `examples/`、`tests/`、`plugins/`、构建脚本和 benchmark 脚本可以作为运行入口或参考，但不要作为优化修改目标，除非用户明确扩大范围。
- 每轮优化只引入一个主要优化思路，便于归因、回退和交付候选补丁。

## 标准命令

编译命令和 Unit Test 命令与 [adaptation.md](adaptation.md) 保持一致。

### CMake 配置

```bash
mkdir -p build && cd build && cmake_maca -DOPENMM_BUILD_CUDA_LIB=ON -DCMAKE_HAVE_LIBC_PTHREAD=ON -DOPENMM_BUILD_CUDA_COMPILER_PLUGIN=OFF -DCMAKE_INSTALL_PREFIX=$OPENMM_PATH ..
```

### 编译和安装

```bash
cd build && make_maca -j install
```

### Benchmark

```bash
cd examples && sh run_stmv.sh
```

### 单项 Unit Test

常规优化轮次优先运行单项 unit test，提高迭代效率：

```bash
cd build && ./TestCudaNonbondedForce Single
```

### 全部 Unit Test

每累计三轮有效优化必须运行一轮全量 unit test；高风险修改也可以提前运行：

```bash
cd build && make_maca test || ctest --return-failed --output-on-failure
```

## 性能指标

- Benchmark 性能单位为 `ns/day`。
- `ns/day` 越高越好。
- 每次 benchmark 必须记录原始输出、解析出的 `ns/day`、运行命令、源码版本、MACA 环境和运行参数。

## Baseline

收到 OpenMM 优化任务后，先建立 baseline，再做任何优化修改。

1. 使用标准 CMake 和编译命令构建 baseline。
2. 运行 `cd examples && sh run_stmv.sh`，记录 baseline `ns/day`。
3. 运行单项 unit test 验证 baseline 正确性。
4. 若 baseline 正确性失败，停止性能优化，先转为适配或正确性修复。
5. baseline 通过后记录源码版本、命令、环境、benchmark 输出、`ns/day`、正确性结果和后续优化日志位置。

## Trace 与瓶颈分析

baseline 建立后，先结合 benchmark、源码和已有性能数据分析瓶颈；需要补充证据且资源条件允许时，再使用已接入的 [trace-report](../../../../problem-analysis/skills/trace-report/SKILL.md) 采集 `run_stmv.sh`。`MACA_LAUNCH_BLOCKING=3` 仅作为诊断候选，先核对当前 SDK 语义；它可能改变并发行为，相关计时不能混入原 baseline 或端到端性能结论。

- trace 入口保持为同一 benchmark 命令：`cd examples && sh run_stmv.sh`。
- 优化方向必须能回到 trace 证据、benchmark 指标、源码分析或 MACA 硬件机制。
- 如果热点能映射到具体 kernel 或算子，参考 `maca_operator_skill` 的优化方法选择候选策略。
- 不要只凭总 `ns/day` 猜测瓶颈；trace 失败或证据不足时，记录失败原因，再补采或降低结论置信度。

## 优化迭代

每轮优化按以下顺序执行：

1. 基于 trace、benchmark 或源码分析提出一个优化假设。
2. 只修改 `platforms/` 目录下与该假设直接相关的文件。
3. 重新编译和安装。
4. 运行 benchmark，解析 `ns/day`。
5. 判定性能：候选版本的 `ns/day` 应高于 baseline 和当前最佳版本；若波动明显，保持相同命令和环境复测。
6. 判定正确性：
   - 常规轮次运行单项 unit test。
   - 每累计三轮有效优化运行一轮全部 unit test。
   - 若全量 unit test 失败，将最近三轮有效优化视为可疑范围，优先回退或二分定位。
7. 记录结论：
   - 性能提升且正确性通过，标记为有效优化，保存候选 patch、哈希和验证记录，由 owner 审核提交；agent 不执行 `git commit` 或 `git push`。
   - 编译失败、benchmark 失败、`ns/day` 未提升或正确性失败，标记为无效尝试，记录原因并回退或放弃该方向。

连续多轮没有收益，或当前证据已无法解释新瓶颈时，先复核测量条件与失败假设；仅在补采能区分下一步方向且条件允许时重新 trace。条件不足时记录边界，不强制重采。

## 正确性门禁

- 优化必须保证正确性，不能接受只提升 `ns/day` 但 unit test 失败的修改。
- 常规轮次最低正确性门禁是 `./TestCudaNonbondedForce Single` 通过。
- 第 3、6、9 轮等累计有效优化后，必须运行全量 unit test。
- 全量 unit test 是阶段性正确性门禁；失败时不要继续叠加优化。

## 记录要求

优化过程必须记录有效和无效尝试，便于回溯调试和多轮会话衔接。日志至少包含：

- baseline 命令、环境、源码版本、`ns/day` 和正确性结果。
- 已有性能证据、主要瓶颈和下一步优化假设；实际采集 trace 时附产物路径。
- 每轮修改的 `platforms/` 文件、优化假设、benchmark `ns/day`、正确性测试类型和结果。
- 有效优化计数，以及最近一次全量 unit test 对应的源码版本。
- 有效修改对应的候选 patch、哈希和验证记录；补丁包含获准新增文件。
- 无效尝试的失败原因、回退状态和后续是否禁止重复尝试。
