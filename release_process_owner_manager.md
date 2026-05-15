# 发布流程说明（Owner / 部门 Manager）

本文面向 app owner 和部门 manager，说明当前系统中的发布流程、关键时间点，以及各角色需要完成的动作。

## 1. 总体原则

- 每个 release 周期由 RM 创建，并设置两个关键 deadline：`App 冻结 deadline` 和 `Doc deadline`。
- 只有 release 决策为 `release` 的 app 会进入 QA 测试范围和最终发布判断。
- `cicd_only` 和 `stopped` 不进入 QA 表单，不生成 RST，不显示 QA 未测试；但会出现在 Manager Review CSV 中，方便 manager 查看全量状态。
- 最终发布只包含“可发布”app：release 决策为 `release`、Owner 已确认、文档/测试/app_info 门禁清空、QA 状态为 `qa_passed` 或 `has_issues`。
- `has_issues` 表示允许发布但带已知问题；QA 填写的问题说明会进入“已知限制”。

## 2. 关键时间点

| 时间点 | 含义 | Owner 要做什么 | Manager/RM 看到什么 |
| --- | --- | --- | --- |
| Release 创建 | RM 从上一版本克隆新周期 | 查看自己名下 app，确认是否参与本次 release | RM 设置 release 名称、App 冻结 deadline、Doc deadline |
| App 冻结 deadline 前 | 本 release 的 app 范围仍可调整 | 新增 app/架构；把 app 决策设为 `release`、`cicd_only` 或 `stopped`；确认 Gerrit URL 和 branch | RM 可导出 release app 测试范围 CSV |
| App 冻结 deadline 后 | 本 release 范围冻结 | 不能再把新增/非 release app 切入本次 release；仍可把 `release` 降为 `cicd_only` 或 `stopped` | QA 按 release app 列表测试 |
| Doc 填写窗口 | 通常由 RM 在官方 docs 截止前通知开放 | 上传或从 Gerrit 拉取 `app_info.json`；确认 diff；补齐 App类型、描述、文档字段和 test_cmd 说明 | 总览和 App 工作台实时显示待办/门禁 |
| Doc deadline | 文档和表单冻结 | Doc deadline 后不能再修改文档字段、表单字段或 app_info | QA 状态仍可在 QA 页面维护 |
| QA 测试期 | QA 根据 RM 导出的测试范围测试 | 关注 QA 反馈；如需修改关键字段，联系 RM 处理 | QA 上传 log，并标注 `qa_passed`、`has_issues` 或 `cannot_release` |
| RST 预览 | RM 生成发布文档预览 | 未就绪 app 不会进入 RST 预览，只会留在待办/门禁 | RST 预览只包含当前可发布 app |
| Manager Review | 部门 manager review 本次发布状态 | 必要时配合解释不可发布原因或已知限制 | Manager Review CSV 包含所有 app、是否可发布、不可发布原因、已知限制等字段 |
| Final Lock | manager review/merge 后 RM 锁定 release | 本 release 所有数据不可再改 | 系统冻结 snapshot，并生成最终 RST/release-data |

## 3. Owner 操作流程

1. 登录系统后进入“App 工作台”。
2. 只会看到自己名下的 app；多个 owner 共有的 app，所有 owner 都可以编辑。
3. 选择 app 后，页面默认只读；点击“修改”后才可编辑。
4. 如果本次不发布，选择 `cicd_only` 或 `stopped`。
5. 如果本次发布，选择 `release`，并补齐：
   - `App类型`
   - `描述（30字内）`
   - `app_info.json`，可上传或从 Gerrit 拉取
   - app_info diff 确认
   - 镜像使用方法
   - 二进制包使用方法
   - 环境搭建
   - 每个 `test_cmd` 的测试数据集、测试内容、结果查看方式、通过标准
6. 滚动到页面底部，点击“保存并提交 Owner 确认”。
7. 如果仍有缺失项，系统会弹窗列出待办/门禁；按提示补齐后再次提交。

## 4. 部门 Manager Review 看什么

RM 会在 RST 页生成 `Manager Review CSV`。该 CSV 覆盖当前 release 的所有 app，不只包含可发布 app。

建议 manager 重点查看：

- app 是否参与本次发布：`release_decision`
- 是否可发布：`是否可发布`
- 不可发布原因：`不可发布原因`
- QA 状态：`QA状态`
- 已知限制：`已知限制`
- 版本号、owner、支持芯片类型

判断规则：

- `是否可发布 = 是`：会进入最终 release note、HPC Manual 或 AI4Sci User Guide。
- `是否可发布 = 否`：不会进入最终 RST；原因会写在 `不可发布原因`。
- `has_issues`：仍可发布，但 manager 需要确认已知限制是否可接受。
- `cannot_release`：不可发布。

## 5. 最终输出

Final Lock 后，系统生成并冻结：

- release note RST
- HPC Manual app 章节 RST
- AI4Sci User Guide app 章节 RST
- release-data JSON
- Manager Review CSV 预览记录

锁定后，本 release 的 snapshot 不可变。如需修正，只能由 RM 解锁处理，或进入下一 release。
