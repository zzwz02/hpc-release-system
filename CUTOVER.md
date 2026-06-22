# 生产切换手册（CUTOVER RUNBOOK）

切换目标：将运行中的旧服务（`server.py` + SQLite `release_system.db`）替换为新服务（`app/main.py` FastAPI + 迁移后的 DB）。

> **原则：全程不碰线上 DB 原文件。** 所有操作先在副本上完成，验证通过后才把进程切换到迁移后的 DB。

---

## 0. 准备工作

```bash
# 确认旧服务还在跑
pgrep -fa 'python.*server\|uvicorn'

# 确认当前 DB 路径（通常与 server.py 同目录）
ls -lh release_system.db
```

---

## 1. 备份线上 DB

```bash
# 时间戳备份，保留原文件不动
cp release_system.db release_system.db.bak-$(date +%Y%m%d-%H%M%S)

ls -lh release_system.db.bak-*
```

---

## 2. 迁移演练（dry-run — 只看报告，不写文件）

```bash
python3 -u tools/migrate_db.py \
    release_system.db \
    release_system.db.dryrun \
    --dry-run \
    2>&1 | tee /tmp/migrate-dryrun.log

# 审阅报告（期望值，offline env）：
#   linked:   96    — cicd_tasks 已关联 apps.id
#   derived:  ~12   — 短名匹配成功
#   orphan:   ~2    — git_url 无法解析（可接受）
#   D-1:      ~5    — 单 app 多 task 冲突（保留最近，其余 orphan）
#   manifest: 8     — .xml repo 需 Gerrit 网络，保留 NULL app_id
#   no UNIQUE conflicts, no FK violations
cat /tmp/migrate-dryrun.log
```

如报告中有意外数字，停止并排查后再继续。

---

## 3. 在副本上执行真正的迁移

```bash
# 在副本上操作；使用 SQLite online backup 生成一致快照
rm -f /tmp/release_system_candidate.db \
      /tmp/release_system_candidate.db-wal \
      /tmp/release_system_candidate.db-shm \
      /tmp/release_system_candidate.db.migrated \
      /tmp/release_system_candidate.db.migrated-wal \
      /tmp/release_system_candidate.db.migrated-shm

python3 - <<'EOF'
import sqlite3
src = sqlite3.connect("release_system.db")
dst = sqlite3.connect("/tmp/release_system_candidate.db")
src.backup(dst)
dst.close()
src.close()
EOF

python3 -u tools/migrate_db.py \
    /tmp/release_system_candidate.db \
    /tmp/release_system_candidate.db.migrated \
    2>&1 | tee /tmp/migrate-run.log

cat /tmp/migrate-run.log
```

---

## 4. 验证迁移后的 DB

```bash
python3 - <<'EOF'
import sqlite3, json, sys

src_db = "/tmp/release_system_candidate.db"
dst_db = "/tmp/release_system_candidate.db.migrated"
src = sqlite3.connect(src_db)
dst = sqlite3.connect(dst_db)
src.row_factory = sqlite3.Row
dst.row_factory = sqlite3.Row
dst.execute("PRAGMA foreign_keys = ON")

# FK 检查
fk_err = dst.execute("PRAGMA foreign_key_check").fetchall()
if fk_err:
    print("FK VIOLATION:", fk_err); sys.exit(1)

# 行数对比（目标库不能少于源库；cicd_tasks 可能因 derived 任务变多）
for t in ["apps", "cicd_tasks", "cicd_task_requests", "releases", "snapshots"]:
    src_n = src.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
    dst_n = dst.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
    print(f"  {t}: src={src_n} dst={dst_n} rows")
    if dst_n < src_n:
        print(f"  ERROR: {t} row count decreased")
        sys.exit(1)

# JSON 字段可解析
bad = 0
for row in dst.execute(
    "SELECT release_id, app_id, data_json FROM snapshots WHERE data_json IS NOT NULL"
).fetchall():
    try:
        json.loads(row["data_json"])
    except Exception as e:
        print(f"  BAD JSON snapshot {row['release_id']}/{row['app_id']}: {e}")
        bad += 1
if bad:
    sys.exit(1)

# cicd_tasks 关联率
total = dst.execute("SELECT count(*) FROM cicd_tasks").fetchone()[0]
linked = dst.execute("SELECT count(*) FROM cicd_tasks WHERE app_id IS NOT NULL").fetchone()[0]
print(f"  cicd_tasks linked: {linked}/{total}")

# 时区：Beijing time columns 不含 +08:00 suffix (naive strings)
sample = dst.execute("SELECT created_at FROM releases LIMIT 1").fetchone()
if sample and ("+08:00" in str(sample[0]) or "Z" in str(sample[0])):
    print("  WARN: timestamp still has TZ suffix:", sample[0])

print("Validation PASSED")
EOF
```

---

## 4.5 清理旧 CICD 表（切换前必须执行）

新设计中，CICD 配置信息以 `apps` 表中的扩展列为准；旧版独立 CICD 任务表只作为迁移过程的输入，不应带入最终切换库。

> 注意：不要在第 2/3 步之前删除旧 CICD 表。当前迁移脚本仍需要读取旧 `cicd_tasks` 来生成迁移报告和完成校验。应在迁移验证通过后、替换 `release_system.db` 前清理候选库。

```bash
python3 - <<'EOF'
import sqlite3
from pathlib import Path

from app.db.connection import init_db

db = Path("/tmp/release_system_candidate.db.migrated")
if not db.exists():
    raise SystemExit(f"missing migrated DB: {db}")

conn = sqlite3.connect(db)
try:
    # 删除旧版独立 CICD 数据表。当前 FastAPI 代码仍保留这些表名作为
    # 审批/通知流程的兼容存储，后面的 init_db 会重建空表，但旧数据不会保留。
    conn.execute("PRAGMA foreign_keys = OFF")
    for table in ["cicd_notifications", "cicd_task_requests", "cicd_tasks"]:
        conn.execute(f"DROP TABLE IF EXISTS {table}")
    conn.commit()
    conn.execute("VACUUM")

    # 重建空的兼容表和索引，避免新服务启动后因缺表失败。
    init_db(conn)

    for table in ["cicd_tasks", "cicd_task_requests", "cicd_notifications"]:
        count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        print(f"{table}: {count} rows")
        if count != 0:
            raise SystemExit(f"{table} should be empty after cleanup")
finally:
    conn.close()
EOF
```

预期输出：

```text
cicd_tasks: 0 rows
cicd_task_requests: 0 rows
cicd_notifications: 0 rows
```

---

## 5. 切换服务

### 5.1 停止旧服务

```bash
# 找到旧服务 PID（避免 pkill 自杀）
OLD_PID=$(pgrep -f 'python.*[s]erver\.py' || true)
echo "Old server PID: $OLD_PID"
test -n "$OLD_PID" && kill $OLD_PID
sleep 2
```

### 5.2 替换 DB（原子替换）

```bash
# 把候选 DB 移到服务目录（原 DB 保留 .bak 不动）
cp /tmp/release_system_candidate.db.migrated release_system.db.new
rm -f release_system.db-wal release_system.db-shm
mv release_system.db.new release_system.db
```

### 5.3 启动新服务（单进程）

```bash
# 确认 web_dist 已编译（cd web && npm run build）
ls web_dist/index.html

# 单进程启动（不要 --workers N；SQLite 不支持多进程写）
export no_proxy=localhost,127.0.0.1
export NO_PROXY=localhost,127.0.0.1

python3 -m uvicorn app.main:app \
    --host 127.0.0.1 \
    --port 3577 \
    --workers 1 \
    --log-level info \
    >> /tmp/uvicorn.log 2>&1 &

NEW_PID=$!
echo "New server PID: $NEW_PID"
sleep 3
curl --noproxy '*' -fsS http://127.0.0.1:3577/api/ldap/status | python3 -m json.tool
```

---

## 6. 快速冒烟验证

```bash
# 以 rm/rm 登录，检查主要数据
SESSION=$(curl -sc /tmp/cookies.txt -s \
    --noproxy '*' \
    -X POST http://127.0.0.1:3577/api/login \
    -H 'Content-Type: application/json' \
    -d '{"username":"rm","password":"rm"}' | python3 -c "import sys,json; print(json.load(sys.stdin).get('ok','?'))")
echo "Login ok=$SESSION"

# 拉取 /api/state — apps + snapshots
curl --noproxy '*' -fsS -sb /tmp/cookies.txt http://127.0.0.1:3577/api/state \
    | python3 -c "import sys,json; d=json.load(sys.stdin); print('apps:', len(d.get('apps',[])))"

# 拉取 CICD 任务
curl --noproxy '*' -fsS -sb /tmp/cookies.txt http://127.0.0.1:3577/api/cicd/tasks \
    | python3 -c "import sys,json; d=json.load(sys.stdin); print('tasks:', len(d.get('tasks',[])))"
```

---

## 7. 关于 8 个 manifest（.xml）App 的 Gerrit 要求

迁移报告中的 **8 个 repo_type='repo' 的 `.xml` manifest 任务** 需要连接：

```
sw-gerrit-devops.metax-internal.com:29418
```

才能解析 `git_url`。在无网络环境（当前 CI/测试机）下，这些任务的 `app_id` 保持 `NULL`（已记录为 `manifest_unresolved`）。

当前 `tools/migrate_db.py` 没有单独的“只补 manifest”模式；manifest 解析发生在完整迁移流程的
Pass-1 身份解析阶段。因此：

- 若切换前能拿到 Gerrit 网络，优先在有网环境重新执行第 2/3/4 步，并使用新生成的
  `/tmp/release_system_candidate.db.migrated` 切换。
- 若已在无网环境切换，这些 manifest 任务会作为 orphan 保留，后续需要新增一个专门的补链脚本或
  手工按 `(git_url, git_branch)` 关联后再更新 `cicd_tasks.app_id`。

新建 app 使用 CICD-first 向导时同样需要 Gerrit 网络才能拉取 `app_info.json`。若 Gerrit 不可达，向导会显示**已解析 Gerrit 身份**（git_url @ git_branch）和跳过按钮；可先创建 App 再手动补填文档信息。

---

## 8. 回滚

若新服务异常，立即回滚：

```bash
NEW_PID=$(pgrep -f 'uvicorn [a]pp\.main:app' || true)
test -n "$NEW_PID" && kill $NEW_PID

# 恢复旧 DB
rm -f release_system.db-wal release_system.db-shm
cp release_system.db.bak-<timestamp> release_system.db

# 重启旧服务（按原部署方式）
python3 server.py &
```

---

## 检查清单

| 步骤 | 完成 |
|------|------|
| 线上 DB 已备份（.bak 时间戳） | ☐ |
| dry-run 报告无意外数字 | ☐ |
| 副本迁移成功（无报错） | ☐ |
| PRAGMA foreign_key_check 为空 | ☐ |
| JSON 字段全部可解析 | ☐ |
| 旧 CICD 表已清空并重建为空表 | ☐ |
| 旧服务已停止 | ☐ |
| 新 DB 已替换（原子 mv） | ☐ |
| 新服务单进程启动（--workers 1） | ☐ |
| /api/ldap/status 返回正常 | ☐ |
| apps + tasks 行数正常 | ☐ |
| 8 个 manifest 任务（Gerrit 有网再处理） | ☐ |
