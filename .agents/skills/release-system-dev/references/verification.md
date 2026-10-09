# 隔离验证与运行注意事项

先按任务选验证范围，不把固定测试数量或某台机器的历史通过记录当成门槛。文档/技能修改检查事实、引用与格式；运行代码修改验证对应行为，涉及发布、权限和事务时增加跨边界回归。

## 保护真实数据

仓库根 `release_system.db` 可能是正在使用的业务库。`app.db.connection.connect()` 和应用 lifespan 会建表、补默认用户并运行兼容处理，只读分析用标准 SQLite `mode=ro`（示例见根 README），不能启动应用来“看一下库”。

有些 TestClient 用例只覆盖 `get_db`，不覆盖 lifespan 的 `settings.db_path`，所以先给进程设置独立的库与文件路径再跑测试：

```bash
verification_dir=$(mktemp -d /tmp/release-system-check.XXXXXX)
export DB_PATH="$verification_dir/main.db"
export ADMIN_PASSWORD_FILE="$verification_dir/admin.local"
export ASSISTANT_DATABASE_URL="sqlite:///$verification_dir/assistant.db"
export RUNTIME_CONF_PATH="$verification_dir/release_system.conf"
export JIRA_AGENT_DATABASE_URL="sqlite:///$verification_dir/jira_agent.db"
export JIRA_AGENT_DATA_DIR="$verification_dir/jira_agent_data"
export NO_PROXY=localhost,127.0.0.1
export no_proxy=localhost,127.0.0.1
python -m pytest -q
```

`RUNTIME_CONF_PATH` 指向不存在的文件即关闭 LDAP/JIRA/LLM/JIRA agent 集成，pytest 的 conftest 也会自动这样隔离。本地启动实例且不需要 JIRA agent 队列时可再设 `JIRA_AGENT_RUNNER_ENABLED=false`；跑 pytest 时不要设，JIRA agent 测试依赖它。需要配置的用例在 `tmp_path` 写一份 `release_system.conf` 并 monkeypatch `settings.runtime_conf_path`，不要读取或修改仓库根的真实配置。需要业务场景时使用 fixture 创建测试数据；网络集成单元测试使用可控替身。显式授权的真实集成验证另行指定目标，不能让普通测试向 Jira/Gerrit 写入数据。

## 后端与契约

- `python -m pytest -q`：完整后端套件；`pytest.ini` 的 `phase2` 标记是现存 HTTP golden 回放，不是尚未实现的占位符。
- `python -m pytest tests/golden/test_golden_replay.py -m phase2 -q`：针对临时 uvicorn 的 HTTP 回放，需要本机 socket 权限。
- 快速检查可选受影响 service/domain/repository 测试；记录选择范围，不能写成“全套通过”。

Golden 的部分样本已按当前业务有意更新。`tests/golden/capture.py` 仍启动旧 `server.py`，不可用它批量覆盖所有当前期望。流程见 [golden README](../../../../tests/golden/README.md)。新增/变更接口需要核对实际 payload、HTTP 状态和 response，前后端各自的 mock 通过不证明契约一致。

并发修复应验证可控交错和失败回滚；接口错误应验证错误状态、幂等和用户可理解的返回。避免只断言函数调用次数或复制实现中的条件表达式。

## 前端

从 `web/` 运行：

```bash
npm ci
npm run build
npm run lint
npm test
```

`build` 包括 TypeScript 和 Vite，会覆盖仓库根 `web_dist/`，如果服务正使用该目录，构建会影响实际页面。纯类型检查可用 `npx tsc --noEmit`。缺少已声明依赖时先区分安装状态与代码错误，不直接删掉引用来使检查通过。

浏览器验证需要受控测试实例。当前 [playwright.config.ts](../../../../web/playwright.config.ts) **不自动启动服务**，固定访问 `http://127.0.0.1:5176`。先用隔离库启动后端；在 web 终端运行：

```bash
API_TARGET=http://127.0.0.1:8000 npm run dev -- --host 127.0.0.1 --port 5176 --strictPort
```

再在同一环境配置的另一个终端从 `web/` 执行 `npm run test:e2e`。若 8000 已被业务实例占用，为隔离后端选择其他端口并同步修改 `API_TARGET`。不可因已有 5176/8000 服务就直接复用；smoke 用例会修改周期、App、QA、WIKI 等数据。

E2E 的 Admin 登录读取 `HPC_ADMIN_PASSWORD`，否则读取根 `admin_password.local`；给隔离后端和 E2E 显式设置同一个测试口令，避免误用真实口令文件。已有测试 helper 也可能过时，失败时对照真实 API 定位，不承诺 E2E 当前全部可用。

交互变更至少验证相关页面的真实提交、错误提示、身份切换、未保存编辑和跨 release 导航。截图用于确认布局与可见状态，不代替提交后回读。

## 技能与文档

重写技能后运行当前可用的 `skill-creator/scripts/quick_validate.py`，并人工核对 description、相对链接、脚本参数及权限边界。不必为文字变化运行全部业务测试。

检查仓库内相对链接与退休文件引用；示例命令只使用存在的脚本和配置参数。不为更新文档执行数据迁移、清库、Gerrit push、Jira 创建或真实 LLM 调用。

## 结果报告

区分通过、真实失败、环境阻塞和未执行。Socket/代理/缺依赖问题不等于产品缺陷，也不等于验证成功。环境限制不允许用放宽断言、删除测试或重置真实数据来绕过。

完成后检查 `git diff --check` 与 `git status --short`；确认没有意外改动旧参考代码，没有数据库、凭据、备份、构建产物或临时脚本进入变更。提交仅在任务需要时进行，按实际作者填写信息。
