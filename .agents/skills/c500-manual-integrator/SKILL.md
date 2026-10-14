---
name: c500-manual-integrator
description: Update the C500/X201 HPC release notes and the per-release internal wiki page from release-system App data, and prune Apps no longer published to the developer community from the C500 AI4Sci user guide, preserving chip scope, usage content, provenance, and Sphinx validity.
---

# C500 / X201 发布文档整合

按指定发布周期的系统数据更新发布说明和内部 wiki 发布页，并从 AI4Sci 用户手册里删除不再在开发者社区发布的 App，或渲染、检查这些文档。系统数据是输入；目标文档有自己的历史结构和已发布文本，在现有文本上修改，不用自动草稿整份覆盖。

## 按任务读取

| 任务 | 再读 |
| --- | --- |
| 从 AI4Sci 用户手册删除不再在社区发布的 App | [ai4sci_guide.md](references/ai4sci_guide.md) |
| 更新 MACA / X201 发布说明 | [release_notes.md](references/release_notes.md) |
| 创建或更新 wiki 发布页 | 先按上一行完成发布说明，再读 [wiki.md](references/wiki.md) |
| 只渲染或检查 | 本文“渲染”和“验证与交付” |

本文其余各节对所有任务适用。

## 目标文档

外部文档仓库默认在 `/remote_home/zhawu/c500_rest_doc`，文档根目录是其下的 `module_pde`。路径可由用户指定；先确认文件存在，不把本机路径当作所有环境的约定。

| 相对文档根目录的路径 | 整合内容 |
| --- | --- |
| `C500_Docs/AI4Sci_User_Guide/source/C500_AI4SciUserGuide_CN.rst` | “AI For Science 加速库介绍”“AI For Science 模型集合介绍”两章：只删除不再在开发者社区发布的 App，其他不动 |
| `C500_Docs/HPC_Release_Notes/source/MACA_HPC_release_notes_CN.rst` | MACA 范围的本期变更和发布列表 |
| `X201_Docs/HPC_Release_Notes/source/X201_HPC_release_notes_CN.rst` | X201 范围的本期变更和发布列表 |
| 内部 wiki 的 `Release <版本>.x` 页面 | 两张变更表和开发者社区发布内容表 |

- 两份 HPC 用户手册（`C500_Docs/HPC_Manual`、`X201_Docs/HPC_Manual`）不更新：不随发布周期增删 App，也不改其中的版本号。
- 外部 RST 和 wiki 只在用户任务包含该项工作时写入；维护本仓库的文档或技能不构成授权。提交和推送同样只在用户要求时执行。
- 动手前 `git fetch`，从 `origin/dev_pde`（已发布文本）建本期工作分支 `HPC/<版本>`，在已发布文本上修改。
- 已经发布过的信息不允许有任何修改：发布说明里往期的变更章节、往期的 wiki 发布页一律不动，措辞、排序、格式都不改。发现往期写错了，只在交付说明里报告，不更正，本期也不照着错的写。AI4Sci 用户手册每期只删除不再在开发者社区发布的 App，见 [ai4sci_guide.md](references/ai4sci_guide.md)；新增由 Owner 自己加。
- 按标题定位章节，不按章节号；章节号会随手册调整变化。

## 数据来源

- 用户指定的 release 优先。未指定时检查可用 release，选择符合“当前草稿”或“已发布文档”语义的周期；只有存在会改变结果的歧义时才询问。
- 权威数据是目标周期的快照（`snapshots.data_json`，每个 App 版本一行）。`artifacts` 里每个周期每类产物只有一份：周期锁定后是 final，否则是会被重新生成覆盖的草稿。产物是生成时刻的结果，可能因生成时间、Owner 确认或文档缺项而少条目，不能代替快照里的发布决策；使用草稿时说明生成时间。不能为了拿到 final 而悄悄换成旧周期。
- 根目录 `release_system.db` 可能是真实业务数据，只读打开（SQLite `mode=ro`）。不调用应用的 `connect()`、`/api/state` 或生成接口来做只读导出。同名文件会被更新，用户要求重新查询时重新读，不复用之前导出的结果。
- 不导出 users、sessions、集成凭据或无关审计；需要 Owner 署名时只读用户名到显示名的对应。导出的文档和数据也是业务资料，放在指定工作目录或 `/tmp`，不提交原始数据库。

## App 身份、版本与芯片范围

- 比较条目时同时看 App ID、官方名称、别名、版本和仓库/分支。空格、下划线和大小写只用于找候选，不是合并依据；`Grid` 这类通用名称尤其要确认身份。不能因为归一化名称相同就合并不同仓库或分支。
- 名称沿用目标文档已有的准确写法；数据库写法不同时（文档 `AlphaFold 3`，数据库 `AlphaFold3`）不改文档，在对比结果里注明。旧名到新名的映射要有数据库别名、历史文档或用户说明支持；改名不算新发布。
- 芯片支持按版本分别取自快照的 `x86_chips` / `arm_chips`。MACA 文档只收非 X201 芯片，X201 文档只收 X201；X201-A 两份文档都不写；双范围的 App 可以两边都出现，但各版本分别过滤，不能把各版本的芯片取并集。支持范围不明的标记待核实，不猜。
- 类别自 3.10.0（含）起按发布系统中的类别（快照 `type`，清单见 `shared/domain_metadata.json` 的 `app_types`）填写，不做映射，也不擅自细化；发布说明和 wiki 都一样。类别服务于读者查找，不决定发布资格，也不能推导芯片支持。
- 来源冲突时先看周期、final 状态、完整性和时间。目标周期有明确的非空事实就据其更新；不以空值覆盖已有的可追溯内容。无法判定的留作待核实项，不把“数据库永远覆盖 RST”当成机械规则。

## 渲染

[render_c500_manual_html.py](scripts/render_c500_manual_html.py) 用各文档自己的 Sphinx 工程构建 HTML：

```bash
python .agents/skills/c500-manual-integrator/scripts/render_c500_manual_html.py \
  --out-dir /tmp/c500_manual_html --clean \
  --only maca_hpc_release_notes --only x201_hpc_release_notes --strict
```

- `--only` 选择文档，可重复；可选 `ai4sci_user_guide`、`maca_hpc_release_notes`、`x201_hpc_release_notes`，省略则全部构建。
- `--strict` 把 Sphinx 警告当作错误。两份发布说明当前零警告，应保持。AI4Sci 手册在已发布文本上就有 `changelog` toctree 无标题的警告，带 `--strict` 会直接失败；它改用不带 `--strict` 的构建，对比修改前后的警告。
- 默认每份文档输出一个独立 HTML（需要 `bs4`）。`--preview-folders` 输出预览目录，`--plain-sphinx` 只在该模式下生效。`--docs-root` / `--x201-docs-root` 覆盖外部目录。`--clean` 会删除所选文档在输出目录下的旧产物，确认它是生成物目录。

## 验证与交付

- 保持 RST 标题层级，中文标题下划线按显示宽度补足；重复外链用匿名链接 `` `名称 <url>`__ ``。
- 构建实际改过的文档，检查表格、目录、代码块、相对链接、版本和支持芯片。区分已有警告和新增警告，不忽略新增的。本地构建不代表对外发布。
- 用 `git diff` 确认往期内容一个字符都没有变，本次范围之外的内容（包括用户自己的修改）也没有被覆盖。
- 交付说明写明：目标周期及其锁定状态、数据查询或产物生成的时间、底稿分支、改了哪些文件、验证结果、留给用户判断的差异和待核实项。没有执行的动作（提交、推送、发布、wiki 写入）不说成已完成。
