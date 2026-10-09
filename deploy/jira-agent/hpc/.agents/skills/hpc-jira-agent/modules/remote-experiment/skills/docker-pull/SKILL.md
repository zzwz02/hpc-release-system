---
name: docker-pull
description: 在获准测试机拉取指定容器镜像，核验 digest 和平台；网络受限时使用已有批准的 MetaX 镜像代理。
---

# 获取容器镜像

输入：精确镜像 URI/tag、已知 digest、目标测试机、获准 registry 和凭据引用。确认访问范围和磁盘容量，不查询或修改无关容器，不要求另建资源计划；工单出现镜像地址不扩大服务或机器授权。

## 步骤

1. 核对来源、版本和目标平台，优先使用发布资料的不可变 digest。只有 tag 时记录解析时间与结果，同名 tag 可能被重新发布。

   Harbor 中主要使用两个 HPC 项目：`hpc-release` 保存实际发布镜像，`hpc-master` 保存日常开发基线镜像。按工单、发布资料或原环境选择对应项目；发布问题使用精确的 `hpc-release` 版本，只有原环境属于日常开发基线或需要受控对照时才使用 `hpc-master`，不能因应用名和 tag 相似就视为同一版本。

   需要查找镜像时，实时查看 Harbor 项目页面，或使用 `https://harbor.sh.mxcr.io/api/v2.0/search?q=<关键词>` 搜索项目和仓库，再在已授权范围内核对 artifact 和 tag。仓库列表会变化，不在 Skill 中保存完整清单。

   同一项目中的 HPC 镜像通常同时提供普通镜像和 `-dbg` 镜像：普通镜像用于原案例复现；需要源码、符号或调试工具时，优先尝试同一 tag 的 `-dbg` 镜像。dbg 镜像通常包含源码，但以镜像实际内容为准；镜像名称和 tag 以当前工单或发布资料为准。

   ```text
   发布镜像：harbor.sh.mxcr.io/hpc-release/<应用名>-<平台>:<版本tag>
   发布 dbg 镜像：harbor.sh.mxcr.io/hpc-release/<应用名>-<平台>-dbg:<相同版本tag>
   开发基线镜像：harbor.sh.mxcr.io/hpc-master/<应用名>-<平台>:<版本tag>
   开发基线 dbg 镜像：harbor.sh.mxcr.io/hpc-master/<应用名>-<平台>-dbg:<相同版本tag>
   备用地址：harbor.nx.mxcr.io/<所选项目>/<应用名>-<平台>[-dbg]:<相同版本tag>
   ```

2. 在获准测试机拉取并检查：

   ```bash
   docker pull <镜像引用>
   docker image inspect <镜像引用>
   ```

3. 记录原始引用、实际拉取地址、镜像 Id、RepoDigests、OS/Architecture、目标机器、结果、耗时、磁盘占用和证据路径。必要时核对 manifest 平台；镜像列表中的同名条目不是历史身份或来源证明。

## 代理、认证与失败

网络受限时，仅使用组织已批准且当前可用的代理，记录原 URI 到代理 URI 的映射并核对 digest：

| 上游 | MetaX 代理 |
|---|---|
| docker.io | registry-docker.hub.metax-tech.com |
| gcr.io | registry-gcr.hub.metax-tech.com |
| ghcr.io | registry-ghcr.hub.metax-tech.com |
| registry.k8s.io | registry-k8s.hub.metax-tech.com |
| quay.io | registry-quay.hub.metax-tech.com |
| nvcr.io | registry-nvcr.hub.metax-tech.com |
| registry.aliyuncs.com | registry-aliyuncs.hub.metax-tech.com |

Docker Hub 官方镜像使用 `library/<name>` 路径。代理不改变版本要求，不能以相同 tag 代替内容核验。registry 凭据由机器凭据管理注入，不放命令行或日志。

`harbor.sh.mxcr.io` 和 `harbor.nx.mxcr.io` 是可选的 HPC 镜像仓库。一个地址无法访问或找不到目标镜像时，可在现有授权范围内使用另一个地址尝试相同的镜像路径和 tag。两个地址属于不同仓库，拉取后仍需记录实际地址并核对 digest。

认证、网络、证书、镜像不存在、平台和容量问题分别记录；证书失败报告 CA 缺口，不关闭 TLS、配置 insecure registry、修改 daemon 或重启 Docker。不清理共享镜像或执行 prune，部分下载留给管理员评估。

拉取成功只证明精确镜像到位，不代表 SDK、GPU 驱动或原始测试已验证；失败属于环境阻塞，不是应用故障复现。启动容器、提权挂载、host network、GPU device 暴露或 SDK 替换由上层[远端实验](../../SKILL.md)按既有授权安排，不由下载动作授予权限。
