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
| 新增 app | 可（release / cicd_only / stopped 任选） | 可（只能 cicd_only / stopped） | 可（只能 cicd_only / stopped） | 不可 |
| 修改 release 决策 | 可（任意切换） | 可（只能下调，不能升回 release） | 可（只能下调，不能升回 release） | 不可 |
| 编辑文档 / 表单 / app_info | 可 | 可 | 不可（已冻结） | 不可 |

「编辑文档 / 表单 / app_info」包括：上传或从 Gerrit 拉取 `app_info.json`、确认 app_info diff、填写文档字段与各 `test_cmd` 的测试说明、修改 `App类型` / `描述`、提交 Owner 确认。

## 为发布，各角色每阶段的必做事项

只列「为顺利发布而必须完成」的事；可做但非必须的不列。

| 阶段 | Owner | RM | QA |
|---|---|---|---|
| App 冻结前 | 确认本轮发布范围 —— 要发布的 app（含新增）必须在此阶段设为 `release`，冻结后不能再加入 | 创建 / 克隆 release，设置「App 冻结」和「Doc」两个 deadline | — |
| App 冻结后 | 为每个 `release` app 补齐 `App类型`、`描述`、`app_info.json`、文档字段、各 `test_cmd` 测试说明，并「提交 Owner 确认」 | 导出测试范围 CSV 交给 QA | 开始测试每个 `release` app，上传 QA log，标注 QA 状态 |
| Doc deadline 后 | — | 生成 Manager Review CSV 交部门 manager 审核；通过后执行 Final Lock | 完成全部 `release` app 的 QA 标注（Final Lock 前必须全部标注完） |

Final Lock 后整个 release 冻结，三方都无更多事项。

## Owner 操作指引

1. 登录后进入「App 工作台」，选中 app，点「修改」进入编辑态。
2. 补齐 `App类型`、`描述`，上传或从 Gerrit 拉取 `app_info.json` 并确认 diff，填写文档字段和各 `test_cmd` 的测试说明。
3. 滚到页面底部，点「保存并提交 Owner 确认」。
4. 若弹窗提示仍有待办 / 门禁项，补齐后再次提交。

## Manager 要看什么

RM 生成的 `Manager Review CSV` 覆盖本 release 全部 app，重点字段：

- **是否可发布 / 不可发布原因**：该 app 是否进入最终文档、未进入的原因。
- **QA状态 / 已知限制**：QA 结论，以及 `has_issues` app 的发布风险说明。
- **release_decision / 版本号 / Owner**：发布范围确认。

## 最终输出

Final Lock 后冻结本 release 的全部 snapshot，并生成 release note RST、HPC Manual RST、AI4Sci User Guide RST、release-data JSON。锁定后如需修正，由 RM 解锁处理，或放到下一个 release。
