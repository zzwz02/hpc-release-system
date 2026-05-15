# 发布流程简明说明

本文面向 app owner 和部门 manager，说明一次 release 从确认范围到最终锁定的流程。

## 核心规则

- 只有决策为 `release` 的 app 会进入 QA 和最终发布判断。
- `cicd_only` 只做 CICD/infra 跟踪，不进 QA，不生成 RST。
- `stopped` 表示本轮停止发布或停止维护，不进 QA，不生成 RST。
- 最终可发布条件：`release` + Owner 已确认 + 文档/app_info/test_cmd 信息完整 + QA 状态为 `qa_passed` 或 `has_issues`。
- `has_issues` 可以发布，但 QA 填写的问题会作为“已知限制”给 manager review。
- `cannot_release` 不可发布。

## 时间点

| 时间点 | 作用 | Owner 动作 | RM / Manager 关注 |
| --- | --- | --- | --- |
| Release 创建 | 从上一版本克隆数据 | 查看自己名下 app | RM 设置 release 名称和 deadline |
| App 冻结前 | 确认本轮发布范围 | 新增 app/架构，选择 `release`、`cicd_only` 或 `stopped` | RM 导出 release app 测试范围 |
| App 冻结后 | 发布范围冻结 | 不能再把 app 切入 `release`，只能降为 `cicd_only` 或 `stopped` | QA 按 release 列表测试 |
| Doc 填写期 | 补齐发布文档信息 | 上传/拉取 `app_info.json`，补文档和测试说明，提交 Owner 确认 | 系统实时显示待办/门禁 |
| Doc deadline 后 | 文档和表单冻结 | 不再修改文档、表单、app_info | QA 仍可更新测试状态 |
| Manager Review | 审核发布范围和风险 | 解释不可发布原因或已知限制 | 看 Manager Review CSV |
| Final Lock | 最终冻结 | 本轮不可再改 | RM 生成最终 RST/release-data |

## Owner 需要做什么

1. 登录后进入“App 工作台”。
2. 确认每个 app 的 release 决策。
3. 对 `release` app 补齐 `App类型`、`描述（30字内）`、`app_info.json`、文档字段和测试说明。
4. 对 `app_info.json` 中的每个 `test_cmd` 说明测试数据集、测试内容、结果查看方式和通过标准。
5. 滚到页面底部，点击“保存并提交 Owner 确认”。
6. 如果弹窗提示仍有待办/门禁，按提示补齐后再次提交。

## Manager 需要看什么

RM 会生成 `Manager Review CSV`，覆盖本 release 的所有 app。

重点看这些字段：

- `release_decision`：是否参与本次发布。
- `是否可发布`：是否进入最终 release note / Manual / AI4Sci User Guide。
- `不可发布原因`：未发布原因。
- `QA状态`：QA 是否通过、是否存在问题、是否不可发布。
- `已知限制`：`has_issues` app 的发布风险说明。
- `版本号`、`Owner`、`支持芯片类型`：发布范围确认信息。

## 最终输出

Final Lock 后系统冻结本 release 的 snapshot，并生成：

- release note RST
- HPC Manual RST
- AI4Sci User Guide RST
- release-data JSON
- Manager Review CSV 记录

锁定后原则上不再修改；如必须修正，由 RM 解锁处理或放到下一 release。
