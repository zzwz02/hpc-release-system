# Release Note Reference

Use this reference when updating C500/MACA or X201 HPC release notes from release-system data or from the current RST release-list inventory.

## Files

- MACA release note: `/remote_home/zhawu/c500_rest_doc/module_pde/C500_Docs/HPC_Release_Notes/source/MACA_HPC_release_notes_CN.rst`
- X201 release note: `/remote_home/zhawu/c500_rest_doc/module_pde/X201_Docs/HPC_Release_Notes/source/X201_HPC_release_notes_CN.rst`
- Release DB: `/remote_home/zhawu/release-system/release_system.db`

## Source Priority

1. Use complete release-system data for the target release when available.
2. If `release_system.db` only has placeholder or incomplete snapshots, do not overwrite good RST data with empty DB data. Use the current release-list tables in the RST as the current inventory and say that the DB was incomplete in working notes.
3. Use historical `新增特性及变更` sections in the same release note to decide whether an item is new, restored, changed, or stopped.

## Document Scope

X201 release note:

- Contains only X201 release information.
- In `发布列表`, every `支持芯片系列` cell must be exactly `X201`.
- In the 3.8.0-style `新增特性及变更` table, module names should be unsuffixed: `AI for Science模型与框架`, `HPC APP`, `工具`, `停止发布`.
- Do not add `（X201系列）` to module names in X201 release notes.

MACA release note:

- Excludes X201 content and X201-only apps.
- In `发布列表`, remove `X201` from `支持芯片系列`; drop a row only if it becomes X201-only.
- In `新增特性及变更`, do not use `通用GPU系列`. Split rows by chip series, following the existing 3.7.0 style:
  - `X206系列`
  - `X301系列` for X301/X302
  - `C500系列` for C500/C588
  - `C600系列` for C600/C600U
  - `N300系列` for N260/N300

## Change Table Rules

`新增特性及变更` must list changes only. `发布列表` is where the full inventory belongs.

Classification names in release notes must use the same source as the manuals:

- Read `references/classification.md` before assigning HPC APP or AI4Sci framework/model categories.
- HPC APP categories must match `HPC_Manual_CN.rst` chapter 10.
- AI for Science framework/model categories must match `C500_AI4SciUserGuide_CN.rst` chapters 5 and 6.
- If the current manuals and `references/classification.md` differ, inspect the manuals and update `references/classification.md` so release notes and manuals continue to share one classification reference.

Include a row when:

- The app/model/tool is in the current release list and was not active in the same document/same chip series before this release: write `首次发布...`.
- The app/model/tool previously existed, was stopped, and appears again: write `恢复发布...`.
- A new version is introduced for an existing app while older versions remain or stop: write explicit version text such as `新增v2026.1版本发布，停止v2025.2版本发布`.
- A previously active app/model/tool is absent from the current release list for the same document/same chip series: write `停止发布`.

Do not include a row when:

- The app appears in the current release list but was already active and no version/support/status change is documented.
- The only evidence for a stop comes from another document scope. For example, X201/M200 history does not justify a MACA stop row.
- The app has no prior release evidence in the same release note history and no release-system stop record tied to that document scope.

Avoid broad labels like `发布/更新HPC框架/工具`; split into `首次发布`, `恢复发布`, `新增...版本`, or `停止发布` when possible.

## Known Pitfalls

- Kokkos and RAJA existed in X201 history (`HTHPCC-M200-2.19.0.2`); do not list them in X201 3.8.0 changes unless there is an actual version/support change.
- `Grid` and `mpi-operator` have X201 history but no MACA release-note history; do not add them as MACA stop rows without a MACA-scope source.
- A line like `停止发布v2023.1版本` is a version stop, not necessarily an application stop.
- App names may differ historically. Normalize obvious aliases when comparing:
  - `PhengLie` -> `PHengLEI`
  - `ParaView & Vtk-m` -> `ParaView & VTK-m`
  - `PyG_lib` -> `PyG`
  - `AImodels` / `aimodes` -> `ai-models`
  - `Shoc` -> `SHOC`

## Table Style

- Use `.. table::` grid tables with the local `:widths:` and `:class: longtable` style.
- Do not use `.. list-table::`.
- Consecutive rows with the same module should use grid-table row spans:
  - First row contains the module text.
  - Continuation rows have a blank first cell.
  - Separators between continuation rows use spaces in the first-column segment, e.g. `+      +----+`.
- Keep `发布列表` as normal grid tables; row spans are only needed where the existing style uses repeated modules.

## Validation

Build both documents after release-note edits:

```bash
/remote_home/zhawu/.local/bin/sphinx-build -b html -d /tmp/c500_hpc_release_notes_doctree \
  /remote_home/zhawu/c500_rest_doc/module_pde/C500_Docs/HPC_Release_Notes/source \
  /tmp/c500_hpc_release_notes_html

/remote_home/zhawu/.local/bin/sphinx-build -b html -d /tmp/x201_hpc_release_notes_doctree \
  /remote_home/zhawu/c500_rest_doc/module_pde/X201_Docs/HPC_Release_Notes/source \
  /tmp/x201_hpc_release_notes_html
```

Treat table parsing warnings, missing blank lines after tables, malformed row spans, and accidental X201 content in MACA as fixable errors.
