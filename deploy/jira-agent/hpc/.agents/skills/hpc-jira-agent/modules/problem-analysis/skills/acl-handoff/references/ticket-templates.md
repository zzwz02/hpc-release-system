# ACL 提单字段与材料

来源：用户提供的《[ACL 提单流程及模板](https://sw-wiki.metax-tech.com/bin/view/3.%20Organizations/PDE/AI/Features/AI0012%3A%20MACA-%20AI%20Training-%20HuggingFace/性能分析及优化需求反馈/ACL提单流程及模板/)》正文，页面标注 Zongguang Lu 于 2026-05-08 17:56 修改；本次按所提供版本整理，未在线核验后续变更。内容包括性能、Bug、新增需求、Triton 和 GEMM 要求。关联 Wiki/附件/Jira 链接是出处或补充线索，未提供的内容不能当作已读证据。

本参考列提单建议；本 agent 只生成草稿与交接材料，不操作 Jira 字段。通用执行与交付规则见[ACL 入口](../SKILL.md)。

## 公共字段

| 字段 | 模板要求与填写依据 |
|---|---|
| Project | `Maca 3.0`；不要猜项目 KEY 或字段 ID |
| effect chip | 实际 C500/C600/C550 等型号，保留原 SKU、fuse、显存等信息，不从机器名推断 |
| Assignee | 原模板建议 `WeilongDing` 或对应组件 leader；仅作为历史候选。按网站已核验资料填账号，否则写“ACL 对应组件负责人（账号待核验）” |
| Component | 原文性能模板为 `SW_ACL`，Bug/Feature 为 `sw_acl`；保留差异并由网站材料/owner 核对目标实例实际值，不猜两个组件或其 ID |

## 性能优化：Improvement

仅性能 regression 可作为性能类 Bug；普通性能优化用 Improvement。

- Summary：`【项目名】/【网络名】【训练/推理】【chip SKU，如 C500 new fuse id 2M / C500 64M / C550】性能优化需要达到 A100 的 xx%`。非 A100 目标按任务约定替换；目标缺失时留待补充，不能拿当前测量值代替目标。
- Priority：Normal；重点客户紧急交付可建议 high，immediate 需事先与领导沟通，记录已有沟通依据，不代替提交人批准。
- Description 按下表填写。

| 内容 | 必须说明的信息 |
|---|---|
| 目标与时间 | 整网相对 A100 的性能目标（或约定平台标准）、最终交付日期 |
| 软件 | 当前 MACA 版本、driver、VBIOS；使用 Python 时写 Python 版本；使用 PyTorch 时写 torch 2.1/2.4/2.6 等实际版本及日期/构建号 |
| 硬件 | MACA 测试硬件、chip/SKU、单卡或多卡及卡数 |
| A100 来源 | 自测或客户提供；自测写 40G/80G 等型号、功率及测试条件；客户数据写来源和已知条件 |
| 整网对比 | 各平台原始延迟/吞吐、单位、工作量、精度、阶段、计时/统计口径及计算后的百分比 |
| 库级对比 | 当前被提单库占整网比例、与 A100 对应库的性能比；推理须逐个提供性能差的 batch size 对应数据，不能仅给一个总平均值 |
| 交付方式 | 临时包测试或合入指定分支，写明确包/分支需求 |
| mcblas/mcdnn | kernel selector 刷新前后性能、采用的配置及结果 YAML；工具不可用时标缺口 |

外部客户需求须记录原始 CCPM Jira 与目标单的 `relates to` 关联建议；内部需求不强制。原单号从网站材料取得，未知时标待补，不直接查询或建立关联。

附件按适用条件准备：

| 附件/命名建议 | 要求 |
|---|---|
| `profiling-A100-网络名.log`（可附 `.json`） | PyTorch 场景使用 PyTorch profiler 输出；与 MACA 相同参数，文本日志必备，JSON 不强制 |
| `profiling-{chip}-网络名-maca版本-日期.log`（可附 `.json`） | 同上，能对应本次整网/库级对比；保留真实版本和日期 |
| `shape-Acl-网络名-maca版本.txt` | 按[日志采集](log-collection.md)生成；大日志可 7z 无损压缩，按网站文件大小规则交付，供 owner 附到 Jira |
| `cufft-项目名-.cpp` 或实际测试脚本 | cufft 相关问题提供可运行测试脚本和构建/执行方式 |
| kernel selector 结果 YAML | 涉及 mcblas/mcdnn 时，用 restricted 包的 `customized_opt_tools`，按所带 README 刷性能并附 YAML；不猜工具命令或格式 |
| Triton 单测 | 涉及 Triton kernel 时提供；优化同时执行[Triton 要求](triton-gemm.md#triton-优化) |
| mctlass shape 与调用次数表 | 原模板说明 mctlass 暂不支持用环境变量打印 shape，需单独提供每个 shape 及其整网调用次数；不要套用 mctlassEx 的日志开关 |

原生 C++/非 PyTorch 应用如不适用 PyTorch profiler，应说明原因，交付真实的等价 profiling 和 API/shape 证据，并注明需由接手方确认替代材料是否满足要求；不为满足附件名编造 PyTorch 日志。FlashAttn 采集生成的 YAML 和屏幕输出要一并交付。

可选性能尝试见[日志参考](log-collection.md#可选性能尝试)，不是所有提单的必做前置条件；TF32 尝试必须仍满足原正确性/精度验收。

## Bug 修复：Bug

- Summary：`【项目名】/【网络名】【chip SKU】【组件名】问题描述`，例如 hang、coredump、结果比对不正确、性能回退。
- Description：现象截图；有打屏/输出日志时同时给截图和日志。记录 MACA 包版本、VBIOS、硬件、chip、单卡/多卡和卡数。截图未取得就标缺失，不能用文字转图片冒充现场截图。
- 打开 `MACA_LAUNCH_BLOCKING=1` 单独复现，查看 trap kernel 信息并附全日志；无 trap 或同步运行改变现象时如实记录，同时保留原始运行。不能把开启同步后不再触发写成已修复。
- 明确填写 ETA；未知时记录由谁补充，不能代替组件方承诺时间。

| 条件 | 附件要求 |
|---|---|
| 所有 Bug | 按 ACL 需要和已定位组件保存日志，只开相关组件，避免互相干扰 |
| 结果比对不正确 | 尽量提供实际输入、参考/实际结果 dump、dtype、shape、判据和误差；无法提供时说明原因 |
| 性能回退 | 正常/异常版本的 profiling、可比条件和性能结果，明确 ACL 算子导致回退的依据；只有整包版本变化不能单独证明 ACL 责任 |
| 整网问题 | 尽量提供独立最小复现；本部署同时遵守公共外组交接要求，验证案例与原问题的关系，无法完成时保留尝试与限制 |

Bug 不自动要求普通性能优化的全部 A100 材料；性能回退以可比正常/异常基线为核心，工单同时要求跨平台或优化时再叠加对应内容。

## 新增需求：Feature

- Summary：`【项目名】/【网络名】【组件名】新增需求（新增 API / 需支持 xx）`。
- 提单前最好先与对应组件讨论具体需求；记录已讨论的结论或“待对齐”，不要声称已获支持。
- Description：新增需求内容、期待 ETA；内容较多时写分阶段的时间节点与交付项；验证方式使用实际场景 shape，性能需求明确对比对象（如 A100 的某个库）与指标。
- 新增 API 还须给出名称和完整参数列表，各参数 datatype、含义和输入/输出方向，返回值及语义，主要功能逻辑。适用时附流程图和线下对齐文档；缺少文档时不能编造已对齐结论。

不把现有 API 的错误调用直接归为新需求；先核对已有支持范围与实际调用。Feature 的验收信息不等于已验证实现。
