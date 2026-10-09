# MetaX GPU 命令参考

按需查询；命令适用性以当前机器的工具版本和帮助为准。设备复位、固件、时钟、功耗、虚拟化及链路修改需要独立授权，不属于日常工单诊断。

## 通用选项

```bash
-h, --help              # 打印帮助信息
-v, --version           # 打印版本信息
-j, --json              # 以JSON格式显示板卡信息
```

## 常用查询命令

### 基本信息
```bash
mx-smi                  # 归纳显示GPU主要信息（功耗、温度、版本、使用率等）
mx-smi -L               # 列出所有GPU设备及ID
mx-smi -i 0             # 查看指定GPU（0-based索引）
mx-smi -i 0,1,2,3       # 查看多个GPU
mx-smi -i all           # 查看所有GPU
```

### 温度
```bash
mx-smi --show-temperature               # 温度传感器读数（摄氏度）
```

### 功耗
```bash
mx-smi --show-board-power               # 板级电压、电流、功率
mx-smi --show-pmbus-power               # 芯片级电压、电流、功率
mx-smi --show-power-mode                # 电源模式（Normal/High）
mx-smi --show-persistence-mode          # GPU持久模式
```

### 性能与利用率
```bash
mx-smi --show-usage                     # GPU/VPU使用率
mx-smi --show-core-usage                # 单核利用率
mx-smi --show-clock                     # 常用IP时钟（XCORE、MC等）
mx-smi --show-clocks all                # 所有IP时钟信息
mx-smi --show-dpm cur                   # 当前性能等级
mx-smi --show-dpm all                   # 所有性能等级及对应时钟电压
mx-smi --show-dpm-max                   # 支持的最高性能等级
mx-smi --show-clk-tr                    # 降频原因信息
```

### 显存
```bash
mx-smi --show-memory                    # 显存使用情况
                                        # vis_vram: CPU可访问设备内存
                                        # vram: 设备内存
                                        # xtt: 设备可访问的主机内存
```

### PCIe与带宽
```bash
mx-smi --show-pcie                      # PCIe速度和带宽能力
mx-smi --show-pcie-bandwidth            # PCIe动态带宽 (MB/s)
mx-smi --show-hbm-bandwidth             # HBM动态带宽 (MB/s)
```

### 版本与硬件
```bash
mx-smi --show-version                   # MACA/BIOS/驱动/固件版本
mx-smi --show-vbios                     # VBIOS信息
mx-smi --show-hwinfo                    # 硬件信息（产品名、芯片系列、SN、PN等）
mx-smi --show-eeprom                    # EEPROM信息（板卡版本、序列号等）
mx-smi --show-sn                        # 芯片序列号和板卡序列号
mx-smi --show-sysinfo                   # 系统信息（GPU id、Node id、Render id、Card id）
```

### 进程与事件
```bash
mx-smi --show-process                   # 当前运行的进程信息
mx-smi --show-event {aer_ue|aer_ce|synfld|dbe|mmio|all}  # PCIe错误事件
mx-smi --count-ecc                      # ECC错误计数
```

### 不可用设备诊断
```bash
mx-smi --show-unavailable-reason        # 设备不可用原因
```

## 实时监控

```bash
mx-smi dmon --show-temperature -l 1000    # 每秒刷新温度
mx-smi dmon --show-usage -l 500           # 每500ms刷新利用率
mx-smi dmon --show-memory -c 10           # 采样10次后退出
mx-smi dmon --show-temperature --show-board-power --show-usage -l 500
```

支持的dmon子命令：`--show-temperature`, `--show-board-power`, `--show-usage`, `--show-memory`, `--total-memory`, `--show-index`, `--show-render`, `--show-pcie-bandwidth`, `--show-hbm-bandwidth`

## 拓扑结构
```bash
mx-smi topo -d                # GPU距离矩阵
mx-smi topo -t                # GPU拓扑树
mx-smi topo -m                # GPU通信矩阵（含CPU亲和性）
mx-smi topo -n                # GPU与网卡通信矩阵
```

## RAS（可靠性）

```bash
mx-smi ras --show-count -i 0     # 显示指定GPU的错误计数
mx-smi ras --show-status -i 0    # 显示指定GPU的错误状态
```

## MetaXLink

```bash
mx-smi mxlk --show                # MetaXLink带宽和速率
mx-smi mxlk --show-peer           # 连接的远端设备
mx-smi mxlk --show-state          # 连接状态（up/down）
mx-smi mxlk --set-state {0|1}     # 禁用/启用MetaXLink
mx-smi mxlk --show-traffic-stat   # 流量统计
mx-smi mxlk --show-aer            # 错误统计
```

## 虚拟GPU (VM)

```bash
mx-smi vm --show-vf               # 显示虚拟GPU
mx-smi vm --enable-vf 2           # 创建2个VF
mx-smi vm --disable-vf            # 移除所有VF
```

## sGPU（仅C500）

```bash
mx-smi sgpu                       # 显示所有sGPU设备
mx-smi sgpu --show-mode           # 显示sGPU模式
mx-smi sgpu --enable -i 0         # 启用sGPU功能
mx-smi sgpu --disable -i 0        # 禁用sGPU功能
mx-smi sgpu -i 0 --create         # 创建子设备（默认4GB显存，5%算力）
mx-smi sgpu -i 0 -n 3 --vram 4G --compute 20  # 创建3个sGPU
mx-smi sgpu -i 0 --set 0 --vram 8G --compute 30  # 修改子设备配置
mx-smi sgpu -r 0 -i 0             # 移除指定sGPU
mx-smi sgpu --show-usage          # sGPU利用率
mx-smi sgpu --show-memory         # sGPU显存
mx-smi sgpu --show-remain         # 剩余可分配额度
mx-smi sgpu --set-timeslice 20 -i 0  # 设置时间片（ms）
mx-smi sgpu --set-sched-class {0|1|2} -i 0  # 设置调度策略
                                        # 0: Best Effort（争抢）
                                        # 1: Fixed Share（固定配额）
                                        # 2: Burst Share（保证配额+弹性）
```

## 光模块（仅C500X）

```bash
mx-smi om --show-status -i 0      # 光模块电压、温度、状态
mx-smi om --show-info -i 0        # 光模块固件信息
mx-smi om --show-rx-status -i 0   # 详细RX状态
```

## ETH（仅C600）

```bash
mx-smi eth --show-bandwidth       # ETH动态带宽
mx-smi eth --show-usage           # ETH使用率
mx-smi eth --show-mac-addr        # ETH Mac信息
mx-smi eth --show-ras-count       # ETH错误计数
mx-smi eth --show-status          # ETH使能状态
```

## 设备管理（需root权限）

### 设备复位
```bash
mx-smi -i 0 -r                    # Warm reset
mx-smi -i 0 --flr                 # 函数级复位
```

### 性能设置
```bash
mx-smi --set-dpm-max xcore,7 -i 0      # 设置最大DPM级别
mx-smi --set-power-mode {0|1} -i 0     # 0:Normal, 1:High
mx-smi --set-persistence-mode {0|1} -i 0  # GPU持久模式
```

### 固件升级
```bash
mx-smi -u <vbios.bin>             # 升级所有设备VBios
mx-smi -U <vbios.bin> -i 0        # 强制升级指定设备
mx-smi --dump-vbios <file> -i 0   # 导出VBios
```

### ECC设置
```bash
mx-smi --set-ecc-state {0|1} -i 0     # 关闭/开启ECC
mx-smi --show-ecc-state               # 查看ECC状态
```

### 运维模式
```bash
mx-smi --show-op-mode           # 运维模式（Normal/Maintenance）
mx-smi --set-op-mode {0|1} -i 0 # 0:Normal, 1:Maintenance
```

### 电源管理（仅C600/X302）
```bash
mx-smi --show-board-power-limit     # 查看功耗设置
mx-smi --set-board-power-limit <W> -i 0  # 设置功耗值
```

### Firmware日志级别
```bash
mx-smi --show-fw-loglevel           # 查看日志级别
mx-smi --set-fw-loglevel <ip>,<level> -i 0  # 设置日志级别
                                        # level: 5:debug, 4:info, 3:warn, 2:error, 1:fatal
                                        # C500: smp0, smp1, ccx0, ccx1, ccx2, xcore
                                        # C600: smp0, smp1, xcore, eth
```

### 其他MISC命令
```bash
mx-smi misc --show-board-type           # 板卡类型
mx-smi misc --show-critical-event       # 严重错误信息
mx-smi misc --show-mmio-state           # MMIO状态
mx-smi misc --show-pcie-event --detail  # PCIe事件详情
mx-smi misc --clear-pcie-event -i 0     # 清除PCIe事件
mx-smi misc --collect-chip-sn           # 收集芯片SN
```

## 输出选项

```bash
mx-smi --show-memory -o memory.csv      # 输出为CSV格式
mx-smi -l 1000                          # 每秒循环输出
mx-smi -c 10                            # 采样10次后退出
mx-smi -t 60                            # 命令超时60秒
```

## GPU ID指定方式

`-i, --index` 参数支持：
- 索引：`0`, `1`, `2`
- 序列号：`GPU-xxx`
- UUID
- PCI地址：`0000:03:00.0`
- 组合：`0,1,5` / `0-2` / `0-4,6` / `all`

## 在容器内执行

```bash
# 基本查询
docker exec <容器名> mx-smi --summary
docker exec <容器名> mx-smi -i 0 --show-memory

# 实时监控
docker exec <容器名> mx-smi dmon --show-temperature -l 1000
```
