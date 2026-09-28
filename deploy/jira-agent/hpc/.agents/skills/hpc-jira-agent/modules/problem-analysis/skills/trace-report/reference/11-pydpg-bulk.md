# CycleTrace pydpg bulk 安装与启用

本文说明 `scripts/install_cycle_trace_pydpg_bulk.sh` 的用途、安装边界、运行期启用方式和常见错误。
这是可选的 CycleTrace `--kernel-probe` fast path，不属于标准 `collect-run` 必需步骤。

## 读取门槛

只有需要安装、启用或排查 pydpg bulk fast path 时，才读取本文。标准 `collect-run`、普通 CycleTrace 采集和普通报告诊断不默认读取本文。

## 作用

`install_cycle_trace_pydpg_bulk.sh` 是 ensure 型脚本：它会先判断目标 CycleTrace Python plugin
目录是否已经安装可用的 `pydpg_bulk` helper；如果已经安装，直接输出启用方式；如果没有安装完整，
再安装 `pydpg_bulk` helper，用于加速 kernel-probe 源码行映射写入。

脚本会执行以下操作：

- 定位包含 `code_mapper.py` 和 `pydpg.so` 的 CycleTrace share 目录。
- 检查 `pydpg_bulk.so`、`pydpg_bulk.cpp`、`code_mapper.py` patch 标记，以及可执行 bundled
  Python 下的 `import pydpg; import pydpg_bulk`。
- 使用目标环境中 CycleTrace 自带的 Python 3.12 headers 和 pybind11 headers 构建
  `pydpg_bulk.so`，仅在未完整安装时执行。
- 将 `pydpg_bulk.cpp` 和 `pydpg_bulk.so` 写入目标 CycleTrace share 目录，仅在未完整安装时执行。
- 原地 patch `code_mapper.py` 的 `DPGMap.apply`，仅在未完整安装且尚未 patch 时执行；在
  `CYCLE_TRACE_USE_PYDPG_BULK=1` 时优先调用 `pydpg_bulk.apply_mapped_tokens`。
- patch 前备份 `code_mapper.py`，备份名形如
  `code_mapper.py.bak_<timestamp>_before_pydpg_bulk`。
- 使用 CycleTrace bundled Python 校验 `import pydpg` 和 `import pydpg_bulk`；如果 bundled
  Python 不可执行，会跳过该校验并打印提示。

## 使用边界

该脚本只适合在需要 `ENABLE_SOURCE_LATENCY=true`、`cycle-trace-ng --kernel-probe` 且
kernel-probe 源码映射写入耗时明显时使用。普通三工具采集、非 source-latency 采集和离线分析不需要安装。

执行脚本会修改目标 CycleTrace 安装目录，因此执行前必须确认：

- 目标环境允许修改 CycleTrace share 目录。
- `CYCLE_TRACE_DIR` 指向当前 `CYCLE_TRACE_BIN` 匹配的 `share/cycle-trace` 目录。
- 目标目录可写。
- 目标环境有 `python3`、C++ 编译器，以及 CycleTrace bundled Python headers / pybind11 headers。

该脚本不会改变 `ENABLE_SOURCE_LATENCY` 的语义，也不会自动启用 source-latency。标准 `collect-run`
在 `ENABLE_SOURCE_LATENCY=true` 时会只读检查与 `CYCLE_TRACE_BIN` 匹配的 CycleTrace share 目录；
若已完整安装 helper，则自动为本次 `cycle-trace-ng --kernel-probe` 注入
`CYCLE_TRACE_USE_PYDPG_BULK=1` 和 `CYCLE_TRACE_PYDPG_BULK_STRICT=1`。若未安装完整，则不注入变量并
继续原 kernel-probe 路径。

## 触发安装的场景

标准 `collect-run` 不会自动安装 pydpg bulk helper。

只有显式执行 `scripts/install_cycle_trace_pydpg_bulk.sh` 时，脚本才会检查并在未完整安装时执行安装。
适用场景：

- 已启用或准备启用 `ENABLE_SOURCE_LATENCY=true`。
- 采集会进入 `cycle-trace-ng --kernel-probe`。
- kernel-probe 源码映射写入耗时明显，需要 fast path。
- 目标 CycleTrace `share/cycle-trace` 目录允许修改。
- 当前目录未满足完整安装判断。

如果 helper 已完整安装，脚本不重新编译、不重复 patch，只输出启用方式。标准 `collect-run` 只在
`ENABLE_SOURCE_LATENCY=true` 且检测到 helper 已完整安装时自动注入运行期变量。

## 安装与启用命令

推荐显式传入 CycleTrace share 目录，避免 `/opt/maca` 兼容软链接或多版本安装导致误判。脚本默认会
先检查是否已安装，未安装时再安装：

```bash
scripts/install_cycle_trace_pydpg_bulk.sh --cycle-trace-dir <cycle-trace-install>/share/cycle-trace
```

如果需要手工直接运行 `cycle-trace-ng --kernel-probe`，可以用 `--print-env` 把 fast path 变量导入当前 shell：

```bash
eval "$(scripts/install_cycle_trace_pydpg_bulk.sh --cycle-trace-dir <cycle-trace-install>/share/cycle-trace --print-env)"
```

`--print-env` 成功时 stdout 只输出：

```bash
export CYCLE_TRACE_USE_PYDPG_BULK=1
export CYCLE_TRACE_PYDPG_BULK_STRICT=1
```

普通日志和错误输出到 stderr，便于安全配合 `eval "$(...)"`。如果安装或校验失败，脚本返回非零，
不会输出用于启用 fast path 的 export。

也可以通过环境变量指定：

```bash
CYCLE_TRACE_DIR=<cycle-trace-install>/share/cycle-trace \
  scripts/install_cycle_trace_pydpg_bulk.sh
```

默认会依次查找 `/opt/maca/share/cycle-trace` 和 `/opt/maca-*/share/cycle-trace`，并选择同时包含
`code_mapper.py` 和 `pydpg.so` 的目录。

如需指定编译器：

```bash
CXX=<cxx> scripts/install_cycle_trace_pydpg_bulk.sh --cycle-trace-dir <cycle-trace-install>/share/cycle-trace
```

## 已安装判断依据

脚本认为已安装可用时，需要同时满足：

- `${CYCLE_TRACE_DIR}/code_mapper.py` 存在。
- `${CYCLE_TRACE_DIR}/pydpg.so` 存在。
- `${CYCLE_TRACE_DIR}/pydpg_bulk.cpp` 存在。
- `${CYCLE_TRACE_DIR}/pydpg_bulk.so` 存在且非空。
- `code_mapper.py` 包含 `CYCLE_TRACE_USE_PYDPG_BULK` 和 `pydpg_bulk.apply_mapped_tokens`。
- 如果 `${CYCLE_TRACE_DIR}/python/bin/python3.12` 可执行，`import pydpg; import pydpg_bulk`
  必须成功。

如果 bundled Python 不存在或无法直接执行，脚本会跳过 import validation，以静态文件和 patch 标记作为判断依据。

## 运行期变量

标准 `collect-run` 不要求用户手工设置下列变量；它会在 `ENABLE_SOURCE_LATENCY=true` 且检测到 helper
完整安装时自动注入。手工直接运行 `cycle-trace-ng --kernel-probe` 时，安装或确认成功后设置：

```bash
export CYCLE_TRACE_USE_PYDPG_BULK=1
```

需要确认 fast path 必须生效时，再设置：

```bash
export CYCLE_TRACE_PYDPG_BULK_STRICT=1
```

`CYCLE_TRACE_PYDPG_BULK_STRICT=1` 表示 `pydpg_bulk` 导入或执行失败时直接抛错；未设置 strict 时，
patched `code_mapper.py` 会打印 warning，并回退到原 Python 写入路径。

这两个变量是 `cycle-trace-ng --kernel-probe` 的运行期环境变量，不属于 active config 字段。标准
`collect-run` 只在 `ENABLE_SOURCE_LATENCY=true` 且实际进入 kernel-probe 时自动考虑它们。

## 连续多次运行结果

脚本连续运行的行为：

- 如果已满足安装判断，脚本不重新编译、不覆盖 `pydpg_bulk.so`、不重复 patch、不新增备份，
  直接输出启用方式或 `--print-env` exports。
- 如果只 patch 过但 `pydpg_bulk.so` 缺失，视为未完整安装，重新构建并安装。
- 如果 `pydpg_bulk.so` 存在但 `code_mapper.py` 未 patch，视为未完整安装，重新 patch。
- 首次 patch 会生成 `code_mapper.py.bak_<timestamp>_before_pydpg_bulk`；已经 patch 后不会继续生成新的
  patch 备份。
- 如果 import validation 失败，脚本返回非零，不输出启用变量。
- 如果 CycleTrace 版本升级或 `code_mapper.py` 被还原，脚本会按当前文件重新检查锚点并 patch。

## 常见错误

该脚本不属于标准 `collect-run` 错误链路，报错格式为 `[pydpg-bulk] ERROR: ...`，不会自动映射为
`[TR-*]`。

| 报错信息 | 处理方式 |
|---|---|
| `python3 not found` | 在目标环境安装或暴露 `python3` 后重跑安装脚本。 |
| `C++ compiler not found: <compiler>` | 安装可用 C++ 编译器，或设置 `CXX=<compiler>` 指向目标环境中的编译器。 |
| `CYCLE_TRACE_DIR does not contain code_mapper.py: <dir>` / `cannot find cycle-trace directory; pass --cycle-trace-dir DIR or set CYCLE_TRACE_DIR` | 传入包含 `code_mapper.py` 和 `pydpg.so` 的 CycleTrace share 目录，例如 `--cycle-trace-dir <cycle-trace-install>/share/cycle-trace`。 |
| `missing code_mapper.py: <path>` / `missing pydpg.so: <path>` / `missing bundled pybind11 headers: <path>` / `cannot find Python 3.12 headers under <path>` | 确认 `CYCLE_TRACE_DIR` 指向完整的 CycleTrace Python plugin 目录，必要时修复安装目录或兼容软链接。 |
| `cycle-trace directory is not writable; rerun with sufficient permissions: <dir>` / `code_mapper.py is not writable; rerun with sufficient permissions: <path>` | 使用具备目标目录写权限的用户执行；安装脚本会写入 `pydpg_bulk.cpp`、`pydpg_bulk.so` 并 patch `code_mapper.py`。 |
| `code_mapper.py does not import os; refusing automatic patch` / `cannot find expected DPGMap.apply anchor; inspect code_mapper.py manually` | 当前 `code_mapper.py` 版本与脚本预期 patch 锚点不一致，停止自动 patch；需人工检查 CycleTrace 版本和 `code_mapper.py` 结构。 |
