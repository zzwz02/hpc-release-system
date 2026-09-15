---
name: hpc-bug-repro
description: 在 HPC 执行机器上复现、分析、修复并验证 HPC 组负责的 Bug（CUDA/MACA 应用编译与运行问题）。归属判断为本组后使用；包含选机、GPU 锁、构建、复现、最小修复、补丁与验证证据的要求。
---

# HPC Bug 复现、修复与验证

## 1. 准备

- 执行机器以本轮提示为准：自动选择时从提示中的系统机器列表挑选（工单或评论指定的环境在列表中时优先），用户指定时只用那台。工单要求的机器不在可用范围内时，结论用 `needs_help`，请 assignee 新建对话时指定机器。
- 用 `nvidia-smi` 等命令确认 GPU 和工具链（例如 `nvcc --version`）可用，把关键版本信息记入证据。
- 把附件复制到 `work/` 后再修改；在 `work/original/` 保留一份未修改的原始文件，便于生成补丁。

## 2. 构建与复现

- 优先使用附件自带的构建脚本（例如 Makefile）；文件名带 `.txt` 等后缀时复制成原名使用，不要修改原始附件。
- 根据 GPU 架构选择编译参数（例如 A100 为 `sm_80` / `-arch=sm_80` / `CUDA_ARCH=80`），以附件脚本支持的方式传入。
- 占用 GPU 的命令一律用 flock 包裹：
  `flock -w 1800 /tmp/hpc-jira-agent-locks/<机器IP>-gpu0.lock ./app args`
- 严格按工单要求的命令复现原始问题，保存完整输出：
  `... 2>&1 | tee artifacts/logs/before-<case>.log`，并记录退出码（`echo "exit=${PIPESTATUS[0]}"`）。
- 无法复现时，记录尝试过的环境和命令，结论为 `cannot_reproduce`。

## 3. 分析根因

- 结合源码和输出定位根因，写到具体文件、行和原因。
- 区分“应用代码问题”（本组修复）和“下层组件问题”（转交）；发现属于其他组时切换到 `cross-team-handoff`。

## 4. 修复

- 最小改动，只改解决问题所需的代码。
- 不得删改或放宽原有的正确性、精度校验，不得通过修改测试参数掩盖问题。
- 生成补丁：`diff -u work/original/<file> work/<file> > artifacts/fix.patch`（多个文件时逐个追加）。

## 5. 验证

- 用修复后的代码重新构建，逐条执行工单要求的全部验收命令，保存到 `artifacts/logs/after-<case>.log`。
- 视情况补充边界用例（例如规模为 1、2 的幂附近、非整除的规模），同样保存日志。
- 所有要求的命令都通过，才可以给出 `fixed_pending_review`；否则如实说明哪些未通过。

## 6. 输出

- `evidence`：每条包含“说明 / 实际执行的命令 / 实际结果（关键输出与退出码）”，覆盖修复前复现和修复后验证。
- `artifacts`：列出 `artifacts/fix.patch` 和日志文件的相对路径。
- `next_steps`：给 assignee 的具体建议，例如“审阅 fix.patch，同意后合入并 resolve”。
