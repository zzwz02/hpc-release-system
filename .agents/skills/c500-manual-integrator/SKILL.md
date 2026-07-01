---
name: c500-manual-integrator
description: Merge C500 release-system documentation data into C500/X201 RST docs. Use when Codex must update HPC_Manual_CN.rst or X201_HPCManual_CN.rst DockerHub HPC APP sections, C500_AI4SciUserGuide_CN.rst chapters 5 and 6, MACA_HPC_release_notes_CN.rst, or X201_HPC_release_notes_CN.rst from release-system data; reconcile app/model versions; classify entries by discipline or chip series; keep release-note changes separate from full release lists; preserve AI4Sci usage methods; or render/validate the resulting Sphinx documentation.
---

# C500 Documentation Integrator

## Scope

Use this workflow for C500 documentation integration from the release system database:

- HPC manual artifact: `artifacts.kind = "manual"` / `hpc_manual_apps.md` -> `HPC_Manual_CN.rst` and `X201_HPCManual_CN.rst` DockerHub HPC APP sections.
- AI4Sci manual artifact: `artifacts.kind = "ai4sci"` / `ai4sci_user_guide_apps.md` -> `C500_AI4SciUserGuide_CN.rst` chapters 5 and 6.
- HPC release notes: release-system app/model/tool data -> `MACA_HPC_release_notes_CN.rst` and `X201_HPC_release_notes_CN.rst`.
- Prefer the latest `final=1` artifact. If none exists, use the latest generated artifact and explicitly mention that it is non-final.

## Manual Workflow

1. Inspect current files before editing:
   - `/remote_home/zhawu/c500_rest_doc/module_pde/C500_Docs/HPC_Manual/source/HPC_Manual_CN.rst`
   - `/remote_home/zhawu/c500_rest_doc/module_pde/X201_Docs/HPC_Manual/source/X201_HPCManual_CN.rst`
   - `/remote_home/zhawu/c500_rest_doc/module_pde/C500_Docs/AI4Sci_User_Guide/source/C500_AI4SciUserGuide_CN.rst`
   - `scripts/render_c500_manual_html.py` if rendering is requested.

2. Export release-system manual artifacts:

   ```bash
   python3 /remote_home/zhawu/release-system/.agents/skills/c500-manual-integrator/scripts/export_release_manual_artifacts.py \
     /remote_home/zhawu/release-system/release_system.db \
     --out-dir /tmp/c500_manual_artifacts
   ```

   Read the generated summaries and use them as source of truth for app names, versions, official URLs, and usage methods.

3. Merge versions by APP/model name:
   - If the same APP appears in multiple versions, write one RST entry.
   - Put all versions on one `版本：` line, sorted naturally and separated with `、`.
   - Do not create duplicate entries for version-only differences.

4. Resolve conflicts:
   - If RST and DB artifact conflict, DB artifact wins.
   - For missing entries in RST, add them.
   - Manual APP/model lists are append-only: do not remove an existing APP/model entry just because it is absent from the latest DB artifact or stopped in a future release.
   - If an APP/model stops publishing, preserve its existing manual information unless the user explicitly asks to remove historical content.

5. Scope manual entries by support chip:
   - `HPC_Manual_CN.rst` is for C500/MACA scope. Include an APP/model when it has at least one non-X201 support chip, such as `C500`, `C588`, `C600`, `C600U`, `N260`, `N300`, `X206`, `X301`, or `X302`.
   - `X201_HPCManual_CN.rst` is for X201 scope. Include an APP/model only when it supports `X201`.
   - If an APP/model supports both scopes, include it in both manuals. If it is MACA-only or X201-only, include it only in the matching manual.
   - When versions of the same APP/model have different support chips, keep one entry per manual but list only the versions supported by that manual's chip scope.
   - Apply the append-only rule within each manual's chip scope: do not remove historical entries unless explicitly requested, but do not newly add entries to the wrong chip-scope manual.

6. Apply document-specific rules:
   - HPC chapter 10: include Chinese introduction, version, official URL when available. Do not carry over image usage, binary usage, environment setup, or tests unless the user asks.
   - X201 HPC manual: follow the same HPC manual style, but keep APP entries and version lists scoped to X201 support.
   - AI4Sci chapters 5 and 6: preserve the prior manual style. Keep introduction, version, official URL, `镜像使用方法` when present, `二进制包使用方法` when present, and relevant setup/test usage if the artifact provides it.
   - AI4Sci chapter 5 is for frameworks/libraries; chapter 6 is for models.
   - AI4Sci chapter 6 must be grouped by discipline because model count is large.

7. Classify by discipline:
   - Use the current RST structure first.
   - If names are inaccurate, rename groups to match the artifact content.
   - Read `references/classification.md` for recommended categories and known APP/model placements.
   - Put unknown entries in the closest defensible discipline and mention uncertainty in the working notes, not in the final user-facing manual.

8. Maintain RST quality:
   - Use existing heading levels and local style.
   - Make Chinese heading underlines at least the display width of the heading; CJK characters count as width 2 for docutils.
   - Convert duplicate named external links like `` `官方文档 <url>`_ `` to anonymous links `` `官方文档 <url>`__ ``.
   - Keep generated prose concise and Chinese-only for HPC introductions.

9. Validate:
   - Run `/remote_home/zhawu/release-system/.agents/skills/c500-manual-integrator/scripts/render_c500_manual_html.py --clean --sphinx-build /remote_home/zhawu/.local/bin/sphinx-build`.
   - The default output is one standalone HTML file per document under `/tmp/c500_manual_html/*.html`.
   - Add `--preview-folders` only when preview-style output folders are needed; then open generated entry pages under `/tmp/c500_manual_html/*/index.html` and `/tmp/c500_manual_html/*/split_files/`.
   - Treat title underline and duplicate target warnings as fixable RST issues. The known `changelog` no-title warning can remain if keeping it out of the HTML table of contents is desired.

## Release Note Workflow

For release-note updates, read `references/release_notes.md` and `references/classification.md` before editing. Core rules:

1. Inspect both release notes before changing either file:
   - `/remote_home/zhawu/c500_rest_doc/module_pde/C500_Docs/HPC_Release_Notes/source/MACA_HPC_release_notes_CN.rst`
   - `/remote_home/zhawu/c500_rest_doc/module_pde/X201_Docs/HPC_Release_Notes/source/X201_HPC_release_notes_CN.rst`

2. Keep document scopes separate:
   - X201 release note contains X201-only content. Its release-list `支持芯片系列` column must contain only `X201`.
   - MACA release note excludes X201 and X201-only apps. Its release-list `支持芯片系列` column must not contain `X201`.

3. `新增特性及变更` is a delta section, not a full inventory:
   - Include only first releases, version additions/changes, restored releases, and confirmed stops.
   - Do not list an app just because it appears in the release list.
   - Do not use generic `发布/更新...` wording unless the source explicitly states a combined update that cannot be split.

4. `发布列表` is the full current inventory:
   - Keep all currently shipped apps/tools/models for that document scope.
   - Merge multiple versions of the same app into one row.

5. Classify entries with the same category source as the manuals:
   - Use `references/classification.md` for HPC APP and AI4Sci framework/model category names and known placements.
   - Release-note classification names must stay consistent with `HPC_Manual_CN.rst` chapter 10 and `C500_AI4SciUserGuide_CN.rst` chapters 5 and 6.

6. Maintain RST table style:
   - Use `.. table::` grid tables, not `.. list-table::`.
   - Merge repeated module cells with grid-table row spans, matching the existing release-note style.

7. Validate both release notes with Sphinx after edits. Use the project `sphinx-build` path when available:

   ```bash
   /remote_home/zhawu/release-system/.agents/skills/c500-manual-integrator/scripts/render_c500_manual_html.py \
     --clean --sphinx-build /remote_home/zhawu/.local/bin/sphinx-build
   ```

   Or build only the native Sphinx projects:

   ```bash
   /remote_home/zhawu/.local/bin/sphinx-build -b html -d /tmp/c500_hpc_release_notes_doctree \
     /remote_home/zhawu/c500_rest_doc/module_pde/C500_Docs/HPC_Release_Notes/source \
     /tmp/c500_hpc_release_notes_html

   /remote_home/zhawu/.local/bin/sphinx-build -b html -d /tmp/x201_hpc_release_notes_doctree \
     /remote_home/zhawu/c500_rest_doc/module_pde/X201_Docs/HPC_Release_Notes/source \
     /tmp/x201_hpc_release_notes_html
   ```

## Helper Script

Use `scripts/export_release_manual_artifacts.py` to inspect and export DB artifacts. It creates:

- `manual.raw.md` and `ai4sci.raw.md`: original artifact Markdown.
- `manual.entries.json` and `ai4sci.entries.json`: parsed and version-merged entries.
- `manual.hpc_chapter10_draft.rst`: RST draft suitable for HPC chapter 10 review.
- `ai4sci.full_draft.rst`: RST draft preserving usage sections for AI4Sci review.

Do not paste drafts blindly. Compare them against the existing RST structure, then merge with the document-specific rules above.
