# HPC App 发布信息协作系统 MVP

这是一个无第三方 Python 依赖的本地 MVP。系统由 Python 标准库 HTTP 服务、SQLite 后端和浏览器 UI 组成；浏览器页面通过 `/api/*` 写入 SQLite，不再使用 `localStorage` 作为主数据源。

## 当前能力

- 本地账号/session 登录。默认 RM 账号：`rm` / `rm`。
- 首次初始化导入 release CSV 和 owner CSV。
- release CSV 中同名但版本/branch 不同的条目会作为独立 app variant 导入；同一版本/branch 的 x86 和 ARM 行会合并到同一个 snapshot。
- 首次维护 alias mapping 和 app-owner 映射。
- 后续 release 从上一版本克隆 app、owner、文档字段、测试说明、CICD 配置和 app-info 来源信息。
- 新增 app 申请只需要官方 app/模型名称、Gerrit URL、branch；提交者自动成为初始 owner。
- 上传新的 `app_info.json`，自动解析版本、X86/ARM 支持芯片、build/test target 和所有 `test_cmd`。
- 与上一版本 `app_info.json` 做 diff，并要求 owner 确认差异。
- 为每个 `app_info.json` `test_cmd` 维护测试数据集、测试内容、结果查看方式和通过标准。
- Owner 可新增 `app_info.json` 中没有的 owner-added 测试项，且同样必须补齐命令和测试说明。
- QA 准入检查会阻塞未确认 diff、缺失 `AppInfoSnapshot`、缺失测试说明、缺失文档字段或 owner 未确认的 release app。
- `cicd_only` app 必须填写 owner、git、app_info、CICD build/test 和 Infra 备注。
- QA 打开后普通关键字段修改会被拒绝；后续应扩展为 change request 审批工作流。
- Release lock 要求 release app 已准入 QA 且 QA passed。
- 预览 RST 可在 lock 前反复生成；最终 RST 只能由 Release lock 生成，lock 后快照和最终 artifacts 不可变。
- 生成 release note、HPC Manual、AI4Sci User Guide RST 和 release-data JSON。
- Artifact 生成和下载仅限 RM。
- Gerrit 推送流程有配置化阻断：未设置 `HPC_DOCS_GERRIT_REMOTE` 和 `HPC_RELEASE_DATA_GERRIT_REMOTE` 时不会假装推送成功；设置后 `/api/gerrit/plan` 会给出推送计划。

## 启动

当前 shell 可能还没把 Python 加入 `PATH`。如果 `python --version` 仍指向 Microsoft Store 占位符，可使用完整路径：

```powershell
C:\Users\zhawu\AppData\Local\Programs\Python\Python314\python.exe server.py --host 127.0.0.1 --port 8765
```

打开：

```text
http://127.0.0.1:8765
```

登录：

```text
username: rm
password: rm
```

## 使用流程

1. 登录后进入“初始化/周期”。
2. 导入：
   - `C:\Users\zhawu\Downloads\hpc_release_report_20260511-695_0512.csv`
   - `C:\Users\zhawu\Downloads\hpc_owner_list.csv`
3. 如需修正名称差异，在 alias mapping 文本框中输入每行一个映射，例如：

   ```text
   aimodels=ai-models
   boltz=Boltz-2
   vasp-openacc=VASP - OpenACC
   ```

4. 点击“导入初始化数据”。
5. 在“App 工作台”选择 app，上传对应 `app_info.json`，确认 diff，补齐文档和测试说明。
6. 点击“Owner确认”。
7. RM 运行 QA 准入检查，打开 QA。
8. QA 完成后，RM 在 app 详情中点击“QA通过”。
9. 所有 release app QA passed 后，RM 执行 Release Lock。
10. 在“RST”页面生成预览或查看最终 RST/release-data。

## 测试

```powershell
C:\Users\zhawu\AppData\Local\Programs\Python\Python314\python.exe -m unittest discover -s tests -p "test_*.py"
powershell -NoProfile -ExecutionPolicy Bypass -File tests\static_checks.ps1
```

## 限制

- 本地账号/session 已实现，但仍是 MVP 级别，不是 LDAP/SSO。
- Gerrit 拉取仍未接入真实服务；当前以上传 `app_info.json` 表示拉取结果。
- Gerrit 推送已有配置化阻断和推送计划骨架，尚未执行真实 Git push。
- SMTP/站内通知未实现。
- QA open 后关键字段修改当前直接拒绝；后续需要完整实现 change request：owner 提交、RM 审批、只允许删减、重新 QA 准入。
