# 业务流程开发约束

这里是定位规则与保护回归的导航，不是独立规则引擎。字段词表读 `shared/domain_metadata.json`，阶段读 `app/domain/phases.py`，上下文权限读 `app/domain/access_actions.py` 和各服务入口；有矛盾时核对代码和用户当前要求。

## App 与周期快照

`apps` 保存全局身份/CICD 配置；`snapshots` 保存每周期的版本、说明、Owner、发布决策和 QA 等信息。主键分别为 App ID 与 `(release_id, app_id)`，不要把 App 名、版本或解析后的 URL 当作可无损替换的主键。

CICD-first 新建关联 App；当前运行时不依赖 `cicd_tasks`，也不生成 `CICD-xxxx`。兼容 `task_id` 值为 App ID。manifest 的存储路径可能是 `APP/.../*.xml`，实际读取 app_info 时通过 `app/identity.py` 解析并可刷新 manifest；存储身份、展示身份与网络目标分开核对。

克隆周期继承最近周期的快照，重置 QA 状态/备注、缺失项缓存和锁定标记，当前不会统一清除 Owner 确认。增加“每周期重新签认”需要作为明确业务变更，不能在维护文档时暗改。

主要入口：`release_service.create_release`、`cicd_service.cicd_first_new_app`、`app_service.update_snapshot`。

## 阶段与 app_info

`current_phase()` 优先看最终锁定，再看 doc deadline、app freeze deadline。deadline 为空视为没有截止时间。不要靠字符串排序或前端本地时间重新定义阶段。

- 冻结前允许新增发布范围。
- App 冻结后不能提升为 release；发布 App 的 app_info 更新不得扩大 `qa_scope_additions()` 检查的范围。
- Doc deadline 后不再允许文档/app_info/Owner 确认类修改；决策、CICD/Gerrit 配置、QA 操作各走细粒度规则。
- 最终锁定禁止受阶段表管控的修改。检查必须在实际写入所依据的最新状态上执行。

app_info 保存原始/解析内容、来源、commit 与同步时间。相对于当前快照有实际领域差异时，使已有 Owner 确认失效；相同内容重新上传不应失效。对上一周期的差异用于展示，不能误当成本次上传变化。`test_docs` 中有过时与 Owner 自增条目，更新时保留有效人工说明。

主要入口：`app_service._apply_app_info_core`、`domain/app_info.py`、`domain/phases.py`。批量 Gerrit 拉取在网络阶段并发、写入阶段串行，并以 NDJSON 返回逐 App 进度；不能跨线程共用请求数据库连接。

## CICD 审批与交付

所有新增/变更请求先 pending，再由具备能力的 RM 审批。RM 自审只设置 `is_self_approved`，不代表提交时自动审批。Admin 不参与该业务。

- 普通配置修改不接受 `status`。Running/Stopped 变化来自 App 发布决策同步，`origin=release_decision_sync`。
- `immediate` 审批立即应用；`dispatch_spd` 审批后等待交付。确认交付才能应用待交付 payload。
- SPD 退回需保留原因与历史。RM 的 `reject-returned` 必须有原因，保留 Jira 和退回信息，不应用 payload。
- 没有 CICD Abandoned/删除流。停用走 App 决策，全局删除走 Admin 并检查锁定周期引用。
- 再次提交、审批、交付与网络重试要按申请当前状态处理，防止重复应用和重复创建 Jira。

未完成的 CICD-first create、带 Jira 的 pending/returned 修改交付、未完成的 Running/Stopped 同步请求会阻止新的配置修改/状态同步。普通无 Jira 的工作台 pending 修改只有在显式 `replace_open=true` 且界面说明取消旧申请后才允许替换；状态同步请求不能被配置编辑替换。

主要入口：`cicd_service.ensure_can_open_cicd_modify_request`、`submit_request`、`approve_request` 及交付函数；纯请求分类在 `domain/cicd_requests.py`。

## 决策跨周期同步

当前决策为 `release` / `cicd_only` / `stopped`。前两者运行，后者停止。不要把全局 CICD 运行状态与某个周期的发布范围合并成一个字段。

- Running/Stopped 边界变化检查所有相关未锁定周期，包括较早周期。普通可选同步与强制边界同步的范围不同。
- `stopped → release/cicd_only`：提交时确定各周期决策并立即应用；发布/QA 规划标记为 CICD pending。审批或交付跨过 deadline 时不能重算这些决策；拒绝/取消要回滚。
- `release/cicd_only → stopped`：立即降低发布决策；对应停止请求不能由审批人拒绝，也不能取消。
- 新 CICD-first create 的初始快照是 stopped。拒绝/取消保留 App 和原因，同仓库/分支不可重复创建，仅按规则支持同名重试。
- 冻结后的目标周期不能新增 QA 发布范围；预览和提交必须使用同一领域判定，不能在浏览器重新推导。
- 同步受申请阻塞时，不得留下已改快照或新建同步申请的半成品。

主要入口：`domain/decision_sync.py`、`app_service.preview_decision_sync` / `sync_decision_to_later_releases`、`cicd_service.sync_decision_to_cicd`。

## QA、文档与锁定

QA 状态含 `not_checked`、`qa_passed`、`has_issues`、`cannot_release`；后两者要求问题说明。`has_issues` 在现有词表中仍被视为可发布，不能描述成“有问题必然阻止发布”。

`qualifies_for_docs()` 判断 release + Owner 确认 + 无文档缺项；`qualifies_for_final()` 在此基础上再判断 QA。当前发布说明和手册采用前者，Manager Review 的可发布列采用后者。社区缺项还受 App 的 CICD 社区产物配置影响。

最终锁定生成 `release_note/manual/ai4sci/data` 四种 final 产物，并检查特定未完成决策同步；没有“全体 App QA 通过才可锁定”的硬门槛。解锁删除 final 产物；Manager Review 独立生成。重构不能把这几种资格默默合并。

QA 日志当前每周期仅一份 BLOB，可被新上传替换；AI 任务在内存线程注册表里，输出建议后由人保存。结构化执行历史、证据签名、正式例外审批和持久化任务队列属于新增能力。

主要入口：`domain/gates.py`、`qa_service.py`、`qa_analysis_service.py`、`qa_jobs.py`、`artifact_service.py`。

## 优先覆盖的边界

改变上述流程时按影响选择用例：不同 Owner 权限、锁定与保存交错、不同字段并发更新、旧版本表单提交、冻结/截止边界、当前与较早未锁定周期、请求被阻塞、启动拒绝回滚、停止不能拒绝、审批/交付重试、Jira 成功但本地失败、账号切换缓存、草稿与当前数据差异。

不要把已有事务工具、权限矩阵或通过的普通用例当作这些边界已经得到验证。
