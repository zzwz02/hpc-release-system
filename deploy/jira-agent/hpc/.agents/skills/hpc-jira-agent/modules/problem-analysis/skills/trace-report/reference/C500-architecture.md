# 曦云 C500 芯片架构

<!-- tl;dr: C500 hardware architecture — 104 AP × 4 PEU, Warp=64, 64KB Shared Memory/AP, 5-instruction dispatch, memory hierarchy (register→SM→L2→DRAM), C500 vs A100 comparison, microbenchmark reference values (SM latency ~64 cycle, DRAM bandwidth ~1843 B/cycle) -->

## 读取门槛

只有报告结论依赖 C500 硬件机制解释、需要解释硬件并行性/存储子系统，或用户要求 C500/A100 架构对比时，才读取本文。普通指标诊断先读 `reference/quick-diagnose.md`，不要因为出现 C500 关键词就默认读取本文。

本章介绍 GPU 架构的编程抽象，以及对应的硬件执行过程。

## 软件并行性

GPU 将线程组织为层次结构：

| 层级 | 说明 |
|------|------|
| **Grid** | 3D 逻辑数组，跨整个 GPU，每次 Kernel Launch 对应一个 Grid |
| **Block** | 3D 数组，位于单个 Grid 内。Block 内线程可通过 Shared Memory 通信，通过 `__syncthreads()` 同步 |
| **Warp** | 64 个线程（C500），同步执行，共享控制流和指令调度。调度最小单元 |
| **Thread** | 最小执行单元 |

> 公司内很多文档使用 "wave"，但本教程统一使用 "Warp"。

## 硬件并行性

软件抽象与硬件的映射关系：

| 软件抽象 | 硬件单元 | 说明 |
|----------|----------|------|
| Grid | GPU | 一次执行一个或多个 Grid |
| Block | AP (加速处理器) | C500 有 104 个 AP |
| Warp | PEU (处理单元) | 每个 AP 有 4 个 PEU |
| Thread | Lane | 每个 PEU 有 16 个 Lane |

### AP 内部结构

每个 AP 有三种执行单元：
- **标量执行单元 (STE)**: 执行标量指令，处理 BlockIdx 等 uniform 数据和控制流
- **向量执行单元 (MTE)** / PEU: 执行向量计算和访存指令
- **矩阵执行单元 (MMA)**: 执行矩阵乘法指令

**指令发射**：每周期最多发射 5 条指令：
1. 1 Scalar ALU / 1 Scalar Memory
2. 1 MMA / 1 Vector ALU
3. 1 Vector Memory (Global Load/Store/Atomics)
4. 1 Shared Memory
5. 1 MISC (BRA 等控制流)

## 存储子系统

GPU 采用层级存储结构，类似认知科学的"三层记忆模型"：

| 存储层级 | 类比 | 容量 | 延迟 | 带宽 |
|----------|------|------|------|------|
| 寄存器堆 | 工作记忆 | 512KB/AP (64 lane × 2048×32-bit) | ~1-4ns | 最高 |
| 共享内存 (WSM) | 短期记忆 | 64KB/AP | ~60 cycle | 128B/cycle/AP |
| Vector L1 Cache | 短期记忆 | 32KB/AP (默认关闭) | ~60 cycle | - |
| L2 Cache | 长期工作区 | 8MB (所有 AP 共享) | ~200ns | 4096B/cycle |
| 全局内存 (DRAM) | 长期记忆 | 64GB | >400ns | ~1843B/cycle |

### 数据通路类型

- **半双工**: DRAM ↔ L2，同一时刻只能一个方向传输
- **全双工**: L2 ↔ VL1 ↔ 寄存器、Shared Memory ↔ 寄存器，读写可同时进行

### 向量访存流程 (`ldg_xxx`)

1. 指令发射 → warp 的 `gvm_count` +1
2. AP 发送地址到 Vector L1，每 16 线程对齐到 128B 聚合成一笔 **transaction**
3. L1 检查命中（默认关闭，直接发到 L2）
4. L2 检查命中
5. 内存控制器访问 DRAM，逐级返回数据
6. 数据写入寄存器，`gvm_count` -1

## 数据局部性

GPU 利用三种局部性：

- **时间局部性**: 缓存近期多次使用的数据到更近的存储层
- **空间局部性**: 连续地址合并访问，一个 Cacheline (128B) 包含相邻数据
- **线程局部性**: 寄存器/共享内存/L1/L2 分别对应不同范围的并行资源

> GPU 性能优化的核心：充分开发存储子系统的三类局部性。

## Microbenchmark 参考值

| 微架构参数 | C500 参考值 | A100 参考值 | 测试程序 |
|------------|-------------|-------------|----------|
| Warp Size | 64 | 32 | `system/gpu_info` |
| AP/SM 数量 | 104 | 108 | `system/gpu_info` |
| Shared Latency | 64 cycle | 28 cycle | `shared/shared_latency` |
| Shared Bandwidth | 128 B/cycle | 128 B/cycle | `shared/shared_latency` |
| L2 Latency | 170 cycle | 220 cycle | `cache/l2_latency` |
| L2 Bandwidth | 4096 B/cycle | 5120 B/cycle | `cache/l2_bandwidth` |
| DRAM Latency | 420 cycle | 400 cycle | `memory/dram_latency` |
| DRAM Bandwidth | 1843 B/cycle | 1950 B/cycle | `memory/dram_bandwidth` |

## Little's Law

$$L = \lambda \times W$$

- **L**: 系统中的任务数
- **λ**: 任务到达率（对应带宽）
- **W**: 平均等待时间（对应延迟）

延迟与带宽相互影响：增加并发请求可能在带宽固定时增加延迟。Microbenchmark 测试需保证足够并发来触及带宽上限。

## 值得注意的区别

- Nvidia GPU 的 Warp Size = 32；C500 的 Warp Size = 64
- C500 默认关闭 Vector L1 Cache（L2 直接服务所有请求）
- C500 Shared Memory 延迟高于 A100（64 vs 28 cycle），但带宽相同
- C500 L2 带宽低于 A100（4096 vs 5120 B/cycle）

## C500 vs A100 架构对比

### A100 (GA100) 架构

- **7 GPC** (GPU Processing Clusters)，两种规格:
  - 2 组含 7 TPC/GPC
  - 5 组含 8 TPC/GPC
- 每个 TPC 含 2 个 SM
- 总共: 2×7×2 + 2×8×5 = **108 SM**
- 每个 SM: 64 FP32 CUDA Cores, 4 Tensor Cores (3rd-gen)
- HBM2 显存, 512-bit 内存总线

### C500 (XCore) 架构

- **8 DPC** (Data Processing Clusters)
- 每个 DPC 含最多 **13-14 AP** (Accelerator Processor)
- 每个 AP 含 **4 PEU** (Processing Element Unit)
- 总共: 4 × 13 × 8 = **416 PEU** (104 AP)
- 每个 PEU 可容纳 8 个 Wave, Wave 有独立 PC 指针
- PEU 相当于 CPU Core, 负责执行 Wave 指令

### 术语映射

| 概念 | C500/MACA | NVIDIA/CUDA |
|------|-----------|-------------|
| 计算集群 | DPC | GPC |
| 多处理器 | AP | SM |
| 执行单元 | PEU | CUDA Core / SP |
| 线程束 | Wave (64) | Warp (32) |
| 向量寄存器 | mtreg | Vector Register |
| 标量寄存器 | sreg | Scalar Register |

### AP 指令发射能力

C500 每个 AP 每周期最多发射 **5 条指令**:

| 槽位 | 指令类型 |
|------|----------|
| Slot 1 | 1 Scalar ALU 或 1 Scalar Memory |
| Slot 2 | 1 MMA 或 1 Vector ALU |
| Slot 3 | 1 Vector Memory (Global Load/Store/Atomics) |
| Slot 4 | 1 Shared Memory (Load/Store) |
| Slot 5 | 1 MISC (BRA 等控制流) |
