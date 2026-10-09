# 共享 GPU 环境与卡死取证

经跳板机访问测试机、其他作业共享 GPU，或需要对疑似卡死采集现场证据时使用。

## 1. 确认观察位置

记录连接与进程关系：

```text
分析机 → 跳板机 → 测试机 → 容器 → rank PID → GPU
```

在测试机确认 `hostname`、容器身份和 GPU 列表；跳板机不一定是运行负载的机器。使用已有 SSH 认证，不在参数、脚本、历史或报告中写密码。连接形式示例：

```bash
ssh -J user@jump-host workload-user@workload-host 'hostname; mx-smi'
```

## 2. 确认作业归属

每轮使用独立容器名和产物目录，记录：

- 容器 ID、镜像 digest 和容器初始 PID。
- 从 `docker top`、进程树或 cgroup 确认的各 rank PID。
- 各 rank 可见设备与 GPU 工具中的 PID 映射。
- 启动前已存在的其他 GPU 进程。

用容器/cgroup 归属结合 PID 判断，不只匹配可执行文件名；相同 `mpirun` 名称和 PID 复用会造成误判。

## 3. 解释 GPU 状态

使用测试机实际提供的工具。MetaX 可从 `mx-smi` 开始，依赖特定参数前先查该版本帮助，保存带时间戳的输出。

GPU 忙可能来自其他作业；一次空闲采样不能证明卡死。判断本轮作业因 GPU 无进展而停滞时，应同时核对：

- 本轮 rank 仍存活。
- 多次采样间日志或进度计数不变。
- 分配给这些 rank 的 GPU 持续没有相关工作。
- rank/线程栈或等待通道持续指向同一停滞依赖。

MPI 程序比较所有 rank。在集合通信中等待的 rank，可能只是受到另一个 rank 在数据交换、事件等待、队列提交或错误处理中停滞的影响。

## 4. 清理前保留现场

至少采集两次带时间戳的快照，区分停滞与缓慢调优。进程观察限定本轮容器或已核验 PID：

```bash
date -Ins
docker top <本轮容器> -eo pid,ppid,stat,wchan:32,etime,cmd
mx-smi
```

对本轮各 rank，在权限范围内读取：

```bash
sed -n '1,80p' /proc/<pid>/status
cat /proc/<pid>/wchan
cat /proc/<pid>/stack
cat /proc/<pid>/maps
```

需要线程信息时，对已确认的 PID 使用 `ps -L -p <pid>`。只有获准且不会明显破坏证据时才附加调试器，并记录附加操作对时序的影响。

超时依据正常版本的阶段耗时和波动设定。超时后先取证，再只停止本轮确切容器或 PID；不使用宽泛的 `pkill`、不终止无关 rank、不删除共享缓存、不复位共享 GPU。
