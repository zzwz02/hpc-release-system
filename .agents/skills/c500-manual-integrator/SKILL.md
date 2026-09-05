---
name: c500-manual-integrator
description: Integrate release-system artifacts and versioned App data into C500/X201 RST manuals or HPC release notes, preserving chip scope, usage content, provenance, and Sphinx validity.
---

# C500 / X201 发布文档整合

用于把指定发布周期的系统数据整合到外部 RST 手册和发布说明，或渲染这些文档。系统导出的 Markdown 是输入，RST 手册有自己的历史结构；不要直接用自动草稿覆盖整份手册。

## 先明确来源与目标

- 用户指定的 release 优先。未指定时，检查可用 release 与产物，选择符合“当前草稿”或“已发布文档”语义的周期；只有存在会改变结果的歧义时才询问。
- 同一周期内优先使用对应 final；无 final 时使用该周期草稿并说明生成时间。不能为了拿到 final 而悄悄换成旧 release。
- 比较快照与产物生成时间/内容，识别过期草稿。产物是生成时刻的结果，不是实时数据库视图。
- 根 `release_system.db` 可是真实业务数据，只读检查用 SQLite `mode=ro`；不要调用应用 `connect()`、`/api/state` 或生成接口来完成只读导出。
- 不导出 users、sessions、集成凭据或无关审计。导出的文档/数据也属于业务资料，放在指定工作目录或 `/tmp`，不顺手提交原始数据库。

脚本 [export_release_manual_artifacts.py](scripts/export_release_manual_artifacts.py) 的 CLI 使用普通读写 SQLite 连接，当前仅执行查询；为保持真实库只读约束，先用 SQLite backup API 从只读源生成副本，再把副本传给脚本。在线 WAL 库不要直接复制主文件。

```bash
python .agents/skills/c500-manual-integrator/scripts/export_release_manual_artifacts.py \
  /tmp/release-doc-source.sqlite \
  --release-id TARGET_RELEASE_ID \
  --out-dir /tmp/release-manual-export
```

`TARGET_RELEASE_ID` 必须替换成实际选定 ID。脚本省略 `--release-id` 时对 manual、ai4sci 分别按 final 和生成时间排序，可能选中不同周期；有明确目标时总是传 ID，并检查两个 `.artifact.json` 的周期一致性。

## RST 目标与范围

默认外部文档根目录为 `/remote_home/zhawu/c500_rest_doc/module_pde`；路径可由用户指定。先确认文件实际存在，不把本机默认路径视为所有环境的约定。

| 相对文档根目录的路径 | 合并内容 |
| --- | --- |
| `C500_Docs/HPC_Manual/source/HPC_Manual_CN.rst` | DockerHub HPC APP 章节；按当前章节结构定位，不仅按历史章节号 |
| `X201_Docs/HPC_Manual/source/X201_HPCManual_CN.rst` | X201 范围的 HPC APP |
| `C500_Docs/AI4Sci_User_Guide/source/C500_AI4SciUserGuide_CN.rst` | 框架/库与模型章节，历史上为第 5、6 章 |
| `C500_Docs/HPC_Release_Notes/source/MACA_HPC_release_notes_CN.rst` | MACA 范围变更和发布列表 |
| `X201_Docs/HPC_Release_Notes/source/X201_HPC_release_notes_CN.rst` | X201 范围变更和发布列表 |

外部 RST 写入仅在用户任务包含该整合工作时执行；本仓库文档/技能维护并不授权修改外部手册。

## 合并方法

1. 阅读目标 RST、产物 `.raw.md` 和 `.artifact.json`，用目标周期快照补充每版本的支持芯片与来源。脚本 `.entries.json` 是粗略名称归并结果，不含可靠的每版本芯片映射。
2. 以 App 身份、别名和版本证据核对同名条目。确认确为同一 App 后每个手册保留一个条目，版本自然排序并以 `、` 分隔；不能因为归一化名称相同就合并不同仓库/分支。
3. MACA/C500 手册新合入非 X201 芯片支持的版本，X201 手册新合入 X201 版本。双范围 App 可以出现在两边，但版本分别过滤，不能把各版本芯片简单取并集。
4. 手册保留有依据的历史 App 和说明；某 App 不在当前产物中，可能是停止、缺项或尚未确认，不足以删除历史条目。清理历史内容须符合用户明确范围。
5. HPC APP 条目通常保留中文介绍、版本、官方网址；AI4Sci 还保留镜像/二进制使用方法以及有来源的环境和测试说明。选定版本间用法有差异时标明适用版本，不能只保留归并时遇到的第一份用法。
6. 分类按 [classification.md](references/classification.md) 与目标文档已有准确结构；不确定项在工作记录说明，不能用臆测补事实。
7. 发布说明额外遵循 [release_notes.md](references/release_notes.md)：变更列表与完整发布列表分开，缺席不自动等于停止。

来源冲突先检查周期、final 状态、完整性与时间。目标 release 有明确非空事实时据其更新；不以空值覆盖已有可追溯内容。无法判定的冲突留下待核实项，不把“DB 永远覆盖 RST”作为机械规则。

## 导出与渲染验证

导出脚本生成 `manual/ai4sci.raw.md`、`.artifact.json`、`.entries.json`，以及 HPC 简版、AI4Sci 完整版 RST 草稿。它不是完整 Markdown→RST 转换器，也不负责芯片过滤或版本间用法合并，草稿需要人工核对。

渲染使用 [render_c500_manual_html.py](scripts/render_c500_manual_html.py)：

```bash
python .agents/skills/c500-manual-integrator/scripts/render_c500_manual_html.py \
  --out-dir /tmp/c500_manual_html --clean --sphinx-build sphinx-build
```

可用 `--docs-root` / `--x201-docs-root` 覆盖外部目录，`--strict` 将 Sphinx 警告视为错误。默认输出每文档一个独立 HTML；`--preview-folders` 输出预览目录，`--plain-sphinx` 仅在预览目录模式使用。`--clean` 会删除脚本管理的输出目录，确认它是生成物目录。

保持 RST 标题层级，中文标题下划线按显示宽度处理；重复外链使用匿名链接 `` `名称 <url>`__ ``。检查表格、目录、代码块、相对链接、版本范围和支持芯片，记录构建警告；不自动忽略新的 Sphinx 警告。交付说明目标周期、来源生成时间、修改文件与验证结果，不声称已推送/发布，除非任务确实执行了该动作。
