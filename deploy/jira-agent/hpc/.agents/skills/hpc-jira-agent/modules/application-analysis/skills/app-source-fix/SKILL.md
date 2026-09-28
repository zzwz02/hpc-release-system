---
name: app-source-fix
description: 在已确认属于 HPC 应用源码、构建或测试内容，且需要形成候选代码修复时，定位发布对应仓库与构建环境，生成 patch 并验证原案例；不提交代码或代替其他组件团队修复。
---

# HPC 应用源码修复

仅在[主流程](../../../../SKILL.md#3-按归属处理)已确认 `ownership.belongs_to_us=yes`，且缺陷位于本组应用源码、构建脚本或测试内容时使用。复用已有范围和责任记录；归属未确认时返回问题分析。

## 处理规则

1. 复用主流程按[发布目录接入](../../../remote-experiment/references/release-catalog.md)核验的应用范围、版本、仓库、分支和发布资料；目标变化或记录不足时补充查询。源码修复前另行核验仓库与目标 ref。发布页面的当前分支不能单独证明历史版本所用 revision；优先从 Jira、镜像、制品清单或发布资料确定基线 commit。
2. 查看仓库中的 Dockerfile 及其直接引用的构建脚本、参数和依赖。先在工单对应的 release 镜像复现；需要源码编译、符号或调试工具时，使用与其应用版本、SDK、构建参数及源码基线匹配的 dbg 镜像。不能只凭镜像名含 `-dbg` 判定两者匹配。
3. 在独立候选目录中修改，只变更 HPC 负责内容。构建方式以已核验的 Dockerfile/发布资料为准；镜像拉取和源码克隆分别复用 [docker-pull](../../../remote-experiment/skills/docker-pull/SKILL.md) 与 [git-clone](../../../remote-experiment/skills/git-clone/SKILL.md)。
4. 先运行能区分根因的针对性验证，再以原 Jira 的命令或等价 release 环境验证原案例。只在 dbg 环境通过，不能视为原问题已经解决。
5. 从已确认基线生成候选补丁，包含新增文件且排除无关改动。候选目录应检出该基线；不能用未核验的当前 `HEAD` 代替工单基线。先核对 `git status --short --untracked-files=all`，列出本次修复的全部路径（新增、修改、删除；重命名包含旧、新路径）。
   对其中尚未跟踪的新文件逐个指定路径执行 `git add -N -- ...`，使其进入 diff；无新增文件时跳过。该操作只登记 intent-to-add，不提交代码。不要用 `git add -N .` 批量纳入日志或临时产物。补丁写到候选源码仓库之外的交付目录。

   以下为 Bash 示例，执行前替换基线、文件清单和绝对路径；`added` 仅列未跟踪的新文件，无新增时设为 `added=()`：

   ```bash
   set -euo pipefail
   baseline='替换为已确认的基线提交'
   files=(src/existing.cpp src/new_file.cpp)
   added=(src/new_file.cpp)
   patch='/绝对路径/artifacts/JIRA-KEY-fix.patch'
   clean='/同一基线的干净工作区绝对路径'

   baseline=$(git rev-parse --verify "${baseline}^{commit}")
   test "$(git rev-parse HEAD)" = "$baseline"
   git status --short --untracked-files=all
   if (("${#added[@]}")); then
     git add -N -- "${added[@]}"
   fi
   git diff --check "$baseline" -- "${files[@]}"
   git diff --name-status "$baseline" -- "${files[@]}"
   git diff --binary --full-index "$baseline" -- "${files[@]}" > "$patch"
   test -s "$patch"
   git apply --numstat "$patch"

   # 验证接收方工作区的基线与干净状态，再检查补丁可应用性
   test "$(git -C "$clean" rev-parse HEAD)" = "$baseline"
   test -z "$(git -C "$clean" status --porcelain --untracked-files=all)"
   git -C "$clean" apply --check "$patch"
   ```

   相对基线的 diff 同时覆盖已暂存与未暂存的改动。交付前核对补丁文件清单与预期修复文件一致，尤其是新增文件、删除项和重命名两端；检查差异内容，避免同一文件夹带无关修改。`git apply --check` 只验证可应用性，不能替代完整性核对或第 4 步的原案例验证。

6. 按[Jira 结果交付](../../../jira-evidence/references/result-delivery.md)提交当前轮次结论，说明仓库、分支与基线 commit、改动文件、如何修复、为何能解决已定位根因，以及针对性验证和原案例验证结果；将 patch 作为交付材料。

## 边界

- Gerrit 服务为 `gerrit.sh.metax-internal.com:29418`，账号和 SSH key 使用本次运行环境已有的授权配置；不把私钥或临时凭据写入 Skill、仓库、镜像或 Jira。
- 不执行 `git commit`、`git push`，不创建 Gerrit change。候选修复只交付 patch，由代码 owner 审核和提交。
- 没有源码修改授权时仅完成只读定位与修复建议；缺少匹配源码、镜像或验证条件时明确记录缺口，不用未验证修改替代结论。
