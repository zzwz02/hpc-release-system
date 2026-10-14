# 发布 wiki 页面

每个发布周期除两份发布说明外，还要在内部 wiki 建一页记录。先完成发布说明，再写 wiki：wiki 的变更表直接取自发布说明，不另算一遍。发布说明的口径见 [release_notes.md](release_notes.md)。

## 位置与访问

- 站点 `https://sw-wiki.metax-tech.com`（XWiki 12.10.8，表单登录，没有个人 token）。发布页位于 `3. Organizations / PDE / Science Computing / C500-Release` 下，每个周期一个嵌套页面，空间名和标题均为 `Release <版本>.x`（如 `Release 3.10.0.x`），语法 `xwiki/2.1`，parent 为 `3\. Organizations.PDE.Science Computing.C500-Release.WebHome`。
- 认证使用用户提供的浏览器会话 cookie，本机约定存于 `~/.wiki_cookies_id`（一行裸 `JSESSIONID` 值）。以 `Cookie: JSESSIONID=<值>` 发送，不打印，不写入仓库或产物。返回 401 或跳转到 `/bin/login/` 表示会话过期，请用户更新该文件，不尝试其他凭据。
- 读写走 REST：`/rest/wikis/xwiki/spaces/3.%20Organizations/spaces/PDE/spaces/Science%20Computing/spaces/C500-Release/spaces/Release%20<版本>.x/pages/WebHome`。`GET` 加 `Accept: application/json` 取源码（`content` 字段）；`PUT` 一个 `application/xml` 的 `<page xmlns="http://www.xwiki.org">`（`title`、`parent`、`syntax`、`content`）创建或更新。该版本对 cookie 认证的 REST 写入不要求 CSRF token。
- 写入以用户账号署名并对团队可见，仅在任务包含 wiki 时执行。只写本期的页面，往期的发布页已经发布，不做任何修改。创建前 `GET` 确认 404。本期页面已存在时先读取当前源码并在其上修改：用户会直接在 wiki 上调整表格，不能用重新生成的内容覆盖这些修改。

## 页面结构

以上一周期页面的源码为模板，各段原样沿用，只替换表格内容：

1. `**仅列出与上一版本区别**`
2. `== X201 ==` 及 X201 变更表
3. `=== **MACA** ===`、芯片系列定义段落（没有新系列依据时原样沿用），随后是 MACA 变更表
4. `开发者社区发布内容` 标题、AI4Science 说明、钉钉文档《HPC发布列表》提示行，随后是社区表
5. `= 内部信息： =` 段落

### 变更表

内容就是两份发布说明本期的 `新增特性及变更` 表，条目和措辞逐字一致。表头 `|=模块|=应用/模型|=特性说明`，每行 `|模块|名称|说明`；同一模块只在第一行写模块名，后续行首格写一个空格（`| |名称|说明`）。

### 开发者社区发布内容表

七列：

```
|一级菜单|名称|Owner|类型|发布包|对应官方版本|描述
|(% rowspan="2" %)基础库|PhysicsNeMo|邓训喆|科学机器学习框架|镜像：1，二进制包：1|2.0.0|提供了面向曦云C500系列的PhysicsNeMo框架，……
|PyG|龚佳震|AI基础框架与推理工具|镜像：2，二进制包：2|v0.4.0|……
|AI4Biomedical|Boltz-2|陈亦奇|蛋白质与生物分子|镜像：1|v2.2.0|……
```

数据来自目标周期快照的 `community`，即 App 工作台“社区发布”一栏。只列 `release_decision=release` 且 `image_count` 或 `pkg_count` 大于 0 的条目。

| 列 | 来源 |
| --- | --- |
| 一级菜单 | `community.menu_level1`，原样 |
| 名称、对应官方版本 | `official_name`、`version` |
| Owner | 中文姓名。快照 `owners` 是域账号，沿用上一周期页面中同一 App 的姓名；新 App 无法确定中文名时写系统显示名，并在交付说明指出 |
| 类型 | 快照 `type`，即数据库标准分类 |
| 发布包 | `community.release_status` 原样，如 `镜像：1，二进制包：1`；不放 harbor / S3 链接 |
| 描述 | `community.description`，原样 |

- 同一一级菜单的行合并首格：第一行写 `|(% rowspan="N" %)菜单|…`，后续行省略首格；只有一行时不加 `rowspan`。菜单顺序沿用上一周期页面，新菜单排在后面；菜单内按名称升序（英文忽略大小写）。
- 社区发布情况由 app_info 中 `app_build.<别名>.pub` 的 `ai4s` 标记推导，不是手工填写。与上一周期页面相比消失的条目，以及有标记但本期不是 release 的条目，都在交付说明列出，由用户判断是否先修正 app_info；不自行补进表格。

## 验收

写入后读取渲染页（`/bin/view/…`），检查三张表的行数、列数和 `rowspan`，确认没有残留的原始语法（`[[`、`(%`、`|=`）。核对两张变更表与发布说明一致，社区表条目与当次只读查询的快照一致。交付说明给出页面 URL 和写入后的版本号。
