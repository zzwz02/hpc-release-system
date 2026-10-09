# 远端异步执行组件接入

本目录完整保留 wrapper、submit/status/logs/result/cancel 控制脚本、YAML 模板/说明、各动作示例与原离线 result 测试。它是作业执行组件，不是资源申请平台、租约服务或新的数字员工。

## 提交前由当前员工准备

选择本组件时准备：本次工单与实验标识、获准机器/资源、target-dir staging、精确 YAML、预期产物及时间/资源限制；原命令和输入身份保存在实验记录中。不要求另建全局阶段字段或资源计划。没有资源申请接口时使用人工提供的批准机器，不虚构“已申请”。普通 SSH 实验无需选用此组件。

1. YAML 的 host/user/port、container name、workdir、command 是执行契约，先审查是否在本组/本次范围；选择 container 时容器不存在必须失败，不切到 host。
2. `sync.remote_workspace` 是专用任务目录，不能是共享源码树。当前同步实现只支持整个 target-dir，`local_paths` 必须为 `[.]`；复杂选择先在本地构造审核过的 staging，而不是无视字段。exclude 被使用，但 scp fallback 不完整支持 rsync 的通配模式；自动敏感任务应预先确认 rsync 可用，或采用无秘密 staging。不要同步认证文件/无关目录。
3. SSH key 或环境密码引用，首次主机信任通过组织流程写 known_hosts；可选 `execution.known_hosts_path` 指定已验证的独立文件。脚本默认严格主机检查，不能用 `StrictHostKeyChecking=no` 绕过。
4. 外部操作者提供 `REMOTE_EXECUTION_APPROVAL_FILE`。submit 批准含下列字段，cancel 使用 `action=cancel` + 精确 `job_id` 替代 target_dir：

```json
{
  "approval_id": "lease-review-123",
  "approved_by": "resource-owner",
  "expires_at": "2030-01-01T00:00:00+00:00",
  "action": "submit",
  "config_sha256": "SHA256_OF_REMOTE_TEST_YAML_BYTES",
  "target_dir": "/approved/task/staging"
}
```

这里有效期表示提交/取消授权有效期，不是 GPU 租约自动续期。批准内容由外部操作者提供，当前同账户脚本检查不构成不可绕过的权限隔离。

## 动作和结果

在本 Skill 根目录，`python3 scripts/submit-example.py --target-dir <staging> --config <approved-yaml>` 返回新 job_id 和 job_dir。后续只对这个 job 调用 `status-example.py`、`logs-example.py`、`result-example.py` 的 `--job-dir`；取消另审批准后调用 `cancel-example.py`。

- 每次 submit 是新实验，不自动重复提交失败/超时命令。先读状态和日志区分基础设施失败与被测程序预期失败。
- `submit.json` 保存 YAML 指纹；查询时配置改变或目标不匹配立即拒绝，不能恢复到另一台机器。
- 收集 stdout/stderr、退出码、产物清单与命令/镜像/SDK 指纹；wrapper succeeded 只表示命令协议成功，不表示问题已解决。预期编译错误的非零退出可能是复现成功，由上层证据匹配判定。
- 对日志与产物中的凭据先脱敏再 Jira comment；原日志保持私有，不能将任意 artifact 路径当作可读取主机文件授权。
- 提交与回收都校验 YAML 的字面相对产物路径；回收核对 job ID、允许清单及远端真实路径。本地证据不跟随符号链接、不覆盖不同内容，记录实际摘要；任一所需文件回收失败返回非零。详细规则与重试方法见 [结果回收说明](local-result-example.md)。

## 停止确认与资源复用

心跳 stale、tmux 不存在、cancel 请求成功、wrapper 状态终结都不证明后台 GPU 命令和容器内后代进程停止。该上游原型的 PID/PGID 取消仍有 PID 复用及后代进程边界，**复用同一资源前不能只信它**；需要资源适配器的进程身份/停止证明，或人工核验。未确认停止前不启动冲突实验；纯 Skill 包不提供资源锁。

`timeout_hint_sec` 只是提示，不是强超时；长期无人值守仍要补资源硬超时、下载大小预算、带身份的终止与停止证明。产物静态路径与本地落盘约束已补充，但远端 realpath 与 SCP 之间仍非原子，不能代替任务隔离。当前可以在明确批准的人工监督任务中使用组件，不需要为了普通任务先重写成 MCP，但不得宣称生产强隔离已完成。
