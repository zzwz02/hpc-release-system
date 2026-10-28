# ACL 日志采集

环境变量来自[模板来源](ticket-templates.md)。先识别实际组件和 MACA/库版本，再选择对应开关；开关不受支持、没有输出或生成文件位置不同，都要保留实际结果，不能将“已设置变量”等同于“已采集成功”。

## 分开保存诊断和性能运行

- 保存原始运行的命令、输入、环境和结果。在单独的同步诊断/shape 运行中设置 `MACA_LAUNCH_BLOCKING=1`，防止日志格式错乱；只打开需要的组件日志。
- 环境变量仅作用于本次进程、子 shell 或任务容器，不修改共享 profile/环境。使用单独输出目录并回收 stdout、stderr 和组件生成文件。
- 正常性能计时和 profiler 采集均与同步/shape 诊断分开。恢复原任务的正常配置，去除本轮加上的 blocking、详细日志和临时调优开关；不能把 `MACA_LAUNCH_BLOCKING=1` 或大量打印下的耗时当作正式基准。原工单配置本身含这些开关时保留原复现，并明确不同配置之间的可比性限制。
- profiler 数据有采样/插桩开销；未插桩端到端性能另留样本。记录 warmup、计时窗口、batch size、精度、训练/推理以及 prefill/decoding 阶段，不混用不同运行的分子与分母。

## 按组件选择

所有下列 shape/同步诊断运行均叠加 `export MACA_LAUNCH_BLOCKING=1`。

| 组件 | 本次运行需要的设置 | 回收内容 |
|---|---|---|
| BLAS / BLASLt | `export MXLOG_LEVEL=err,MCBLAS=info,MCBLASLT=info` | 屏幕日志与 API/shape 信息 |
| DNN | `export MCDNN_LOG_ENABLE=1`；`export MCDNN_LOG_LEVEL=5`；`export MCDNN_LOG_BACKDOOR=1` | 完整组件日志 |
| FlashAttn | `export MHA_LOG_ENABLE=1`；`export MHA_PRINT_PARA=ON`；适用版本使用 `export MHA_INPUT_COLLECTION=./` | 屏幕输出和生成的全部相关 YAML；模板说明从 2.31.x 开始建议优先使用 `MHA_INPUT_COLLECTION` 抓 shape |
| PyTorch | `export PYTORCH_ENABLE_PRINT_INFO=1` | 框架打印信息；此开关不能替代 PyTorch profiler |
| mctlassEx | `export MCTLASS_LOG_ENABLE=ON`；`export MXLOG_LEVEL=err,MCTLASSEX=debug` | 屏幕日志与实际生成材料 |
| mctlass | 模板未提供可用的环境变量 shape 采集方法 | 从测试输入/调用点等可靠来源单独记录 shape 和每个 shape 在整网中的调用次数 |

FlashAttn 使用 `./` 时记录实际工作目录，并在运行前后核对新生成的 YAML；不要为改变输出位置而破坏原命令的相对路径。当前版本不支持该变量时保留其它有效输出并说明限制。

BLAS 和 mctlassEx 都使用 `MXLOG_LEVEL`，不要连续 export 覆盖前者后误称两者日志均已开启。优先分别采集；确实需要同次关联且版本支持时合并为 `MXLOG_LEVEL=err,MCBLAS=info,MCBLASLT=info,MCTLASSEX=debug`，并核验输出。

## 重定向与退出码示例

下例说明在获准执行机、已持有 GPU 锁的实验中如何保存 BLAS 诊断。`bash ./reproduce.sh` 需替换为已核验的原复现命令，输出目录使用本任务的新目录；示例本身不是已执行证据。

```bash
acl_capture_dir="$PWD/acl-capture-blas"
mkdir -p "$acl_capture_dir"
(
  export MACA_LAUNCH_BLOCKING=1
  export MXLOG_LEVEL=err,MCBLAS=info,MCBLASLT=info
  set +e
  bash ./reproduce.sh > "$acl_capture_dir/stdout-stderr.log" 2>&1
  acl_run_rc=$?
  printf '%s\n' "$acl_run_rc" > "$acl_capture_dir/exit-code.txt"
  exit "$acl_run_rc"
)
```

将实际命令、工作目录和本次设置另记入证据，不记录含凭据的整份环境。hang 采集使用适合原案例的有限时长或现有异步执行机制，保留超时与自然退出的区别；超时本身不能证明原 hang 的根因。

## 可选性能尝试

只有与当前性能目标、组件、精度及版本相符时，才在独立对照中试用；逐项改变，保存前后性能与原精度判据下的结果，不作为默认启用配置。

| 场景 | 模板建议 |
|---|---|
| addmm | `export ENABLE_ADDMM_CUDA_LT=1`，尝试 BLASLt 融合 kernel |
| mcdnn FP32 | `export PYTORCH_ALLOW_CUDA_CUDNN_TF32=1`，尝试 TF32 kernel |
| mcblas FP32 | `export TORCH_ALLOW_TF32_CUBLAS_OVERRIDE=1`，尝试 TF32 kernel |

TF32 可能改变数值结果，只有原正确性/精度要求仍满足时才报告有效收益。支持情况和收益均需实测；一次开关改善只证明该对照配置的效果，不足以单独认定内部根因。
