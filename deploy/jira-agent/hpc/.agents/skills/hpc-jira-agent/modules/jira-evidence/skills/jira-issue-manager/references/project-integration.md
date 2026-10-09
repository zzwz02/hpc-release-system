# Jira 接入约定

本组件提供 Jira 读取与写入接口；操作范围以当前任务授权为准。

## 当前员工如何调用

1. 采集员工收到精确 issue key，确认当前工作范围，读取详情、字段映射和全部分页评论。
2. 使用凭据代理注入 `JIRA_TOKEN_FILE`（仓库外、只有操作者可读）或 `JIRA_TOKEN`；已有凭据可以直接复用，不重新申请。
3. `JIRA_BASE_URL` 指定 Jira；`JIRA_ALLOWED_ORIGIN` 可进一步固定允许的 scheme/host/port。传 `--base-url` 更换服务器时也须同步核对允许 origin。现网 HTTP 会明文传输 PAT，仅能在组织明确批准的受控网络中使用；生产优先 HTTPS，不禁用证书验证。
4. 从本 Skill 根目录执行 `python3 scripts/get_issue_details.py --issue-id HPC-123`。输出中的 `raw_fields` 和 `field_definitions` 是证据，展示层自定义字段以 ID 为身份；保留脚本缩进、空行、字段空值和评论可见性。
5. 附件先从详情获取元数据，下载器重新核对该 issue 的附件 URL、同源和预算，拒绝跳转与覆盖。成功记录 SHA-256。把原始输出放任务私有证据目录，不提交 Git、不直接完整贴评论。
6. 本工程评论由 [Jira 材料模块](../../../SKILL.md) 的 `scripts/comment.py` 负责事件标识、工单范围、去重和回读。启动器按本次明确授权设置 `JIRA_COMMENT_ISSUE`；未授权只生成草稿。底层 `add_comment.py` 不提供幂等保证，不用于自动重试。
7. 本轮报告及必要复现/定位材料由该模块的 `scripts/artifact.py` 交付；启动器按用户授权配置当前工单和本轮交付目录，无需逐文件批准。该范围不授权下述通用写脚本，也不包含任意源码或跨工单材料。

## 通用写脚本的保留与批准

`create_issue.py`、`update_issue.py`、`upload_attachment.py` 和 `add_comment.py` 保留原功能，默认 fail-closed。写操作需要外部操作者提供 `JIRA_WRITE_APPROVAL_FILE`，且逐字段匹配；唯一可选替代是下述单工单阶段评论策略：

```json
{
  "approval_id": "review-123",
  "approved_by": "authorized-operator",
  "expires_at": "2030-01-01T00:00:00+00:00",
  "method": "POST",
  "url": "https://jira.example.invalid/rest/api/2/issue/HPC-123/comment",
  "action": "add_comment",
  "target": "HPC-123",
  "payload_sha256": "SHA256_OF_CANONICAL_PAYLOAD"
}
```

载荷哈希算法是 `json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()` 的 SHA-256。创建的 target 是项目 key；其它操作是精确 issue key。附件载荷摘要为 `{"filename": ..., "sha256": ...}`；其余是实际 JSON 请求体。批准到期或内容变化必须重新审核。

批准文件是外部审阅凭据，不是模型可以自行生成的许可。当前脚本检查不能防止拥有相同 OS 账户权限的恶意程序改批准文件，因此生产必须把批准服务/凭据放在员工不可写的边界。批准不是一次性幂等回执；重试仍由发布 Skill 管理。

原通用脚本还保留 `JIRA_COMMENT_POLICY_FILE` 单工单评论策略兼容能力；本工程不配置或依赖它。若外部系统使用该兼容接口，须依据 `scripts/common.py` 中实际字段校验另行集成，不能与本工程的 `JIRA_COMMENT_ISSUE` 混为同一种机制。策略不能授权 update/transition/attachment/create，也不是跨进程权限网关。

## 验收与边界

- 本轮工程外 QA 只访问 loopback 测试服务器，验证真实读取、分页、阻断行为，不额外交付根级运行代码。
- 本目录 `tests/` 是完整原 Jira 集成入口，默认禁用。`JIRA_INTEGRATION_TESTS=explicitly-approved` 仅表示测试意图，不能代替每个写动作批准；只允许隔离测试项目。
- 输出任何 `success=false` 都是失败，不能作为下一业务步骤的完成证据。
- 不自动变更 assignee/status/labels、关闭 Jira、创建关联单、上传源码。通用操作存在与本员工获准使用是两件事。

当前未提供 Jira 生命周期自动关闭、强隔离凭据网关、大附件断点续传或跨分页原子快照。
