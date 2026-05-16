# 发布流程简明说明

本文面向 app owner 和部门 manager，说明一次 release 从确认范围到最终锁定的流程，以及每个角色在每个阶段可以做什么。

## 角色

- **RM（Release Manager）**：管理 release 生命周期 —— 建/克隆 release、设 deadline、导出测试范围、生成报告、最终锁定。
- **Owner**：app 负责人，维护自己名下 app 的发布信息。
- **QA**：测试并标注每个 `release` app 的质量结论。
- **Admin**：系统维护（备份/清空数据库、删除 app），与发布流程无关，不在下表中。

## 发布决策（三态）

每个 app 在一个 release 里有一个 release 决策，决定它参与到哪一步：

- **release**：参与 QA 和正式发布，进入 release note / HPC Manual / AI4Sci User Guide。
- **cicd_only**：仅作 CICD/infra 跟踪，不进 QA，不生成文档。
- **stopped**：本轮停止发布或停止维护。

只有 `release` 决策的 app 才需要补齐文档、接受 QA。

## 可发布条件

一个 app 进入最终 release note / manual，必须同时满足：

1. release 决策为 `release`；
2. Owner 已提交确认；
3. 文档、`app_info`、test_cmd 说明等门禁项已补齐；
4. QA 状态为 `qa_passed` 或 `has_issues`。

`has_issues` 仍会发布，但 QA 写的问题会附加到该 app 的「已知限制」。`cannot_release` 不发布。「待补/门禁」清单只作提示，不阻塞报告生成和最终锁定。

## 时间节点

一次 release 有四个关键节点，按北京时间计算，过点即生效：

1. **Release 创建** —— RM 从上一版克隆数据，设置 release 名称和两个 deadline。
2. **App 冻结 deadline** —— 发布范围（哪些 app 以 `release` 参与）从此冻结。
3. **Doc deadline** —— 文档、表单、`app_info` 从此冻结。
4. **Final Lock** —— RM 在 manager review 后执行，整个 release 冻结。

这把时间分成四个阶段，下表是每个角色在每个阶段能做的事。

## 角色 × 阶段 操作表

| 阶段 | RM | Owner | QA |
|---|---|---|---|
| **App 冻结前** | 设置/调整 release 名称与 deadline；导出测试范围 CSV；生成预览 RST 与 Manager Review CSV。也可代任意 app 执行下方 Owner 的所有编辑操作 | 新增 app（`release`/`cicd_only`/`stopped` 任选）；编辑自己 app 的文档字段、App类型、描述、测试说明；上传或从 Gerrit 拉取 `app_info.json`；任意调整 release 决策；提交 Owner 确认 | 对 `release` 决策的 app 标注 QA 状态（`qa_passed`/`has_issues`/`cannot_release`）；上传 QA log |
| **App 冻结后**（未过 Doc deadline） | 与上一阶段相同；但 release 决策只能从 `release` 降级，不能再设回 `release` | 与上一阶段相同；但新增 app 只能选 `cicd_only`/`stopped`；release 决策只能从 `release` 降级，不能再升回 `release` | 与上一阶段相同 |
| **Doc deadline 后** | 不能再编辑文档/表单/`app_info`；可生成预览 RST、Manager Review CSV、导出测试范围 CSV；可执行 Final Lock。如需重新放开编辑，只能改 deadline | 文档、表单、`app_info`、release 决策全部冻结，只能查看 | 仍可标注 QA 状态、上传 QA log —— QA 是此阶段唯一在进行的工作 |
| **Final Lock 后** | 整个 release 冻结；如需修正可解锁 release | 只能查看 | 只能查看 |

> 注：「编辑」指改文档字段、测试说明、上传 `app_info`、确认 diff、提交 Owner 确认等。所有阶段、任何角色都能登录查看当前 release 状态。

## Owner 在每个 release 要做的事

1. 登录后进入「App 工作台」，点「修改」进入编辑态。
2. 确认每个 app 的 release 决策。决策为 `release` 的才需要继续往下。
3. 对 `release` app 补齐：
   - `App类型`、`描述（30字内）`；
   - 上传或从 Gerrit 拉取 `app_info.json`，并确认与上一版的 diff；
   - 文档字段（基本介绍、镜像/二进制使用方法、环境搭建、已知限制）；
   - 每个 `test_cmd` 的测试数据集、测试内容、结果查看方式、通过标准。
4. 滚到页面底部，点「保存并提交 Owner 确认」。
5. 若弹窗仍提示有待办/门禁项，按提示补齐后再次提交。

> 提示：务必在 App 冻结前确定好发布范围。冻结后，没设成 `release` 的 app 本轮就进不了发布，只能放到下一个 release。

## Manager 要看什么

RM 会生成 `Manager Review CSV`，覆盖本 release 的所有 app。重点字段：

- **是否可发布 / 不可发布原因**：该 app 是否进入最终文档，未进入的原因。
- **QA状态 / 已知限制**：QA 结论，以及 `has_issues` app 的发布风险说明。
- **release_decision**：该 app 本轮是否参与发布。
- **版本号 / Owner / 支持芯片类型**：发布范围确认信息。

## 最终输出

Final Lock 后，系统冻结本 release 的全部 snapshot，并生成：

- release note RST
- HPC Manual RST
- AI4Sci User Guide RST
- release-data JSON

锁定后原则上不再修改；如必须修正，由 RM 解锁处理，或放到下一个 release。
