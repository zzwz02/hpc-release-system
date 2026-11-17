# 业务流程开发约束

修改月度发布、报名、app_info 同步、CICD、QA、发布与文档前读这里。功能说明以根目录 [README](../../../../README.md) 为准，本文只写开发时容易做错的约束。

## 数据归属

- App 的身份、CICD 配置、`cicd_status`、可选资料（官方名称、类型、Owner、文档字段、测试说明、社区、Sanity）都在 `apps` 表；没有按周期复制的 App 数据。
- `entries` 是 App 在某个版本的报名，`UNIQUE(release_id, app_id)`；撤回后再报名复用原行并清空计划、交付和 QA 字段。
- app_info 只来自仓库：`catalog_service.store_app_info()` 按内容哈希去重，新版本必须在同一事务里交给 `cycle_service.on_app_info_version()`。不要加手工上传入口。
- `official_versions` 的最新未撤销行是 App 的当前正式版；解锁版本时撤销该版本写入的行，不删除。

## 阶段

- 阶段只由 [phases.py](../../../../app/domain/phases.py) 的 `current_phase()` 计算，操作许可只查 `can()` / `require_can()`；不要在服务里比较日期。
- 报名截止前：报名、修改计划、增删测试项、编辑环境。截止后：测试范围只能删减，环境冻结。QA 截止后才能发布。发布后只读，只能解锁。
- 基础镜像在发布前都可维护；被有效报名引用时不能删除。

## 测试范围跟随与扩大申请

- 比较基准永远是条目当前锁定的版本，比较前两边都去掉 Owner 删掉的测试项（`cycles.scoped_parsed`）。
- 截止后扩大才生成申请；同样的扩大内容已在待处理中时取代旧申请但不重复通知；与上次被拒的内容完全相同则不再申请；不再扩大时自动关闭待处理申请并跟进。
- 每个条目同时最多一条 `pending` 申请。通知在事务提交后发送（`run_after_commit`）。

## CICD

- 启停申请只含 `status: {old, new}`，不能与配置修改混在一起；状态值小写 `running` / `stopped`。
- 报名检查 `running` 和无未完成停止申请；提交停止申请检查 `cycle_service.has_open_signup()`；停止申请生效时再断言一次（不变量，不是业务分支）。三处检查与写入在同一事务。
- 新建申请不创建 App；`_apply_create()` 在生效时建 App、写 CICD 配置、回填申请的 `app_id`，提交后后台同步 app_info。
- 报名、撤回、发布都不生成 CICD 申请；CICD 申请生效也不改报名。

## QA、发布与文档

- QA 结论、备注和交付物（镜像、digest、commit）写在 `entries`；保存结论即视为已审阅 AI 建议并删除它。
- 发布：`qa_passed` / `has_issues` → `released` 并写正式版（`has_issues` 的备注并入已知问题）；其余 → `failed`。final 文档在同一事务里生成。
- 发布说明只含本期已发布条目；手册覆盖所有 `running` 且有正式版的 App。草稿手册假设本期可发布条目已发布。

## 通知

- 每种通知类型一个钉钉机器人，存 `notification_channels`；类型在 `shared/domain_metadata.json` 的 `notification_kinds`。未启用即不发。不要从 `release_system.conf` 读机器人配置。

## 迁移

- 旧库（存在 `snapshots` 表）在 `init_db()` 时由 [monthly_migration.py](../../../../app/db/monthly_migration.py) 一次性转换；有未完成 CICD 申请时拒绝迁移。代码里不写兼容旧字段的分支。

## 优先覆盖的边界

- 报名截止前后的 app_info 变化（扩大 / 不扩大 / 改回）、QA 同意与拒绝后的再次变化。
- 已报名 App 提交停止申请；有停止申请时报名；停止申请生效时的不变量。
- 发布后解锁再发布，正式版不重复、旧行标记撤销。
- 新建申请被拒绝或取消时不留 App 记录。
