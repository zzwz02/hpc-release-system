# 发布流程简明说明

本文面向 app owner 和部门 manager，说明一次 release 各阶段每个角色要做什么。

## 角色

- **RM（Release Manager）**：管理 release 生命周期 —— 建/克隆 release、设 deadline、导出测试范围、生成报告、最终锁定。
- **Owner**：app 负责人，维护自己名下 app 的发布信息。
- **QA**：测试并标注每个 `release` app 的质量结论。

## 发布决策（三态）

每个 app 在一个 release 里有一个 release 决策：

- **release**：参与 QA 和正式发布，进入 release note / HPC Manual / AI4Sci User Guide。
- **cicd_only**：仅作 CICD/infra 跟踪，不进 QA，不生成文档。
- **stopped**：本轮停止发布或停止维护。

只有 `release` 决策的 app 才需要补齐文档、接受 QA。

## 可发布条件

一个 app 进入最终 release note / manual，必须同时满足：决策为 `release` + Owner 已提交确认 + 文档 / `app_info` / test_cmd 信息齐全 + QA 状态为 `qa_passed` 或 `has_issues`。`has_issues` 仍发布，QA 写的问题进「已知限制」；`cannot_release` 不发布。

## 时间节点

一次 release 有四个节点，按北京时间计算，过点即生效：

1. **Release 创建** —— RM 从上一版克隆，设置两个 deadline。
2. **App 冻结 deadline** —— 发布范围（哪些 app 以 `release` 参与）从此冻结。
3. **Doc deadline** —— 文档、表单、`app_info` 从此冻结。
4. **Final Lock** —— RM 在 manager review 后执行，整个 release 冻结。

这把时间分为四个阶段：App 冻结前、App 冻结后、Doc deadline 后、Final Lock 后。

## Owner 操作 × 阶段：什么阶段能做什么

| Owner 操作 | App 冻结前 | App 冻结后 | Doc deadline 后 | Final Lock 后 |
|---|---|---|---|---|
| 新增 app | ✅（release / cicd_only / stopped 任选） | ⚠️（只能 cicd_only / stopped） | ⚠️（只能 cicd_only / stopped） | 🚫 |
| 修改 release 决策 | ✅（任意切换） | ⚠️（只能下调，不能升回 release） | ⚠️（只能下调，不能升回 release） | 🚫 |
| 上传 / 拉取 app_info | ✅ | ⚠️（不能新增芯片或测试 path，不扩大 QA 范围） | 🚫（已冻结） | 🚫 |
| 编辑文档 / 表单 | ✅ | ✅ | 🚫（已冻结） | 🚫 |

「编辑文档 / 表单」包括：填写文档字段与各 `test_cmd` 的测试说明、修改本 release 的基本信息、提交 Owner 确认。基本信息中 Owner 可改 `App类型` / `官方URL` / `描述`，`官方名称` / `文档目标` / `Owner` 列表由 RM 维护。上传 / 拉取 `app_info` 后系统会直接列出与上一版的 diff，无需再单独「确认 diff」；app freeze 后 `app_info` 仍可重新上传 / 拉取，但芯片集与测试 path 集只能持平或减少。

所有这些信息都是 per-release 的：同一个 app 在不同 release 里相互独立，改一个不会动到已冻结的另一个。每个 app 在每个 release 都有自己的变更记录。

## 为发布，各角色每阶段的必做事项

只列「为顺利发布而必须完成」的事；可做但非必须的不列。

| 阶段 | Owner | RM | QA |
|---|---|---|---|
| App 冻结前 | 确认本轮发布范围 —— 要发布的 app（含新增）必须在此阶段设为 `release`，冻结后不能再加入 | 创建 / 克隆 release，设置「App 冻结」和「Doc」两个 deadline | — |
| App 冻结后 | 为每个 `release` app 补齐基本信息、`app_info.json`、文档字段、各 `test_cmd` 测试说明，并「提交 Owner 确认」 | 导出测试范围 CSV 交给 QA | 开始测试每个 `release` app，上传 QA log，标注 QA 状态 |
| Doc deadline 后 | — | 生成 Manager Review CSV 交部门 manager 审核；通过后执行 Final Lock | 完成全部 `release` app 的 QA 标注（Final Lock 前必须全部标注完） |

Final Lock 后整个 release 冻结，三方都无更多事项。

## Owner 操作指引

1. 登录后进入「App 工作台」，选中 app，点「修改」进入编辑态。
2. 补齐基本信息（`App类型` / `官方URL` / `描述`），上传或从 Gerrit 拉取 `app_info.json`（系统会列出与上一版的 diff，直接看即可，无需单独确认），填写文档字段和各 `test_cmd` 的测试说明。
3. 需要时可点「从其他版本复制信息」，选另一个 release，把它的基本信息 / 文档 / 测试说明 / release 决策按需填入当前表单（仍需自己核对后保存）。
4. 滚到页面底部，点「保存并提交 Owner 确认」。
5. 若弹窗提示仍有待办 / 门禁项，补齐后再次提交。

## Manager 要看什么

RM 生成的 `Manager Review CSV` 覆盖本 release 全部 app，重点字段：

- **是否可发布 / 不可发布原因**：该 app 是否进入最终文档、未进入的原因。
- **QA状态 / 已知限制**：QA 结论，以及 `has_issues` app 的发布风险说明。
- **release_decision / 版本号 / Owner**：发布范围确认。

## 最终输出

Final Lock 后冻结本 release 的全部 snapshot，并生成 release note Markdown、HPC Manual Markdown、AI4Sci User Guide Markdown、release-data JSON。锁定后如需修正，由 RM 解锁处理，或放到下一个 release。
