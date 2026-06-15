# 生产切换手册（CUTOVER RUNBOOK）

切换目标：将运行中的旧服务（`release_system/server.py` + SQLite `release_system.db`）替换为新服务（`app/main.py` FastAPI + 迁移后的 DB）。

> **原则：全程不碰线上 DB 原文件。** 所有操作先在副本上完成，验证通过后才把进程切换到迁移后的 DB。

---

## 0. 准备工作

```bash
# 确认旧服务还在跑
pgrep -fa 'python.*server\|uvicorn'

# 确认当前 DB 路径（通常与 server.py 同目录）
ls -lh release_system/release_system.db
```

---

## 1. 备份线上 DB

```bash
# 时间戳备份，保留原文件不动
cp release_system/release_system.db \
   release_system/release_system.db.bak-$(date +%Y%m%d-%H%M%S)

ls -lh release_system/release_system.db.bak-*
```

---

## 2. 迁移演练（dry-run — 只看报告，不写文件）

```bash
python3 tools/migrate_db.py \
    --source release_system/release_system.db \
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
# 在副本上操作
cp release_system/release_system.db /tmp/release_system_candidate.db

python3 tools/migrate_db.py \
    --source /tmp/release_system_candidate.db \
    2>&1 | tee /tmp/migrate-run.log

cat /tmp/migrate-run.log
```

---

## 4. 验证迁移后的 DB

```bash
python3 - <<'EOF'
import sqlite3, json, sys

db = "/tmp/release_system_candidate.db"
con = sqlite3.connect(db)
con.row_factory = sqlite3.Row

# FK 检查
fk_err = con.execute("PRAGMA foreign_key_check").fetchall()
if fk_err:
    print("FK VIOLATION:", fk_err); sys.exit(1)

# 行数对比（不能比备份少太多）
for t in ["apps", "cicd_tasks", "cicd_task_requests", "releases", "snapshots"]:
    n = con.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
    print(f"  {t}: {n} rows")

# JSON 字段可解析
bad = 0
for row in con.execute("SELECT id, app_info FROM snapshots WHERE app_info IS NOT NULL").fetchall():
    try:
        json.loads(row["app_info"])
    except Exception as e:
        print(f"  BAD JSON snapshot {row['id']}: {e}")
        bad += 1
if bad:
    sys.exit(1)

# cicd_tasks 关联率
total = con.execute("SELECT count(*) FROM cicd_tasks").fetchone()[0]
linked = con.execute("SELECT count(*) FROM cicd_tasks WHERE app_id IS NOT NULL").fetchone()[0]
print(f"  cicd_tasks linked: {linked}/{total}")

# 时区：Beijing time columns 不含 +08:00 suffix (naive strings)
sample = con.execute("SELECT created_at FROM releases LIMIT 1").fetchone()
if sample and ("+08:00" in str(sample[0]) or "Z" in str(sample[0])):
    print("  WARN: timestamp still has TZ suffix:", sample[0])

print("Validation PASSED")
EOF
```

---

## 5. 切换服务

### 5.1 停止旧服务

```bash
# 找到旧服务 PID（避免 pkill 自杀）
OLD_PID=$(pgrep -f 'python.*server\.py\|uvicorn release_system')
echo "Old server PID: $OLD_PID"
kill $OLD_PID
sleep 2
```

### 5.2 替换 DB（原子替换）

```bash
# 把候选 DB 移到服务目录（原 DB 保留 .bak 不动）
cp /tmp/release_system_candidate.db release_system/release_system.db.new
mv release_system/release_system.db.new release_system/release_system.db
```

### 5.3 启动新服务（单进程）

```bash
# 确认 web_dist 已编译（cd web && npm run build）
ls web_dist/index.html

# 单进程启动（不要 --workers N；SQLite 不支持多进程写）
NO_PROXY=localhost,127.0.0.1 \
uvicorn app.main:app \
    --host 127.0.0.1 \
    --port 8000 \
    --workers 1 \
    --log-level info \
    >> /tmp/uvicorn.log 2>&1 &

NEW_PID=$!
echo "New server PID: $NEW_PID"
sleep 3
curl -s http://127.0.0.1:8000/api/health | python3 -m json.tool
```

---

## 6. 快速冒烟验证

```bash
# 以 rm/rm 登录，检查主要数据
SESSION=$(curl -sc /tmp/cookies.txt -s \
    -X POST http://127.0.0.1:8000/api/login \
    -H 'Content-Type: application/json' \
    -d '{"username":"rm","password":"rm"}' | python3 -c "import sys,json; print(json.load(sys.stdin).get('ok','?'))")
echo "Login ok=$SESSION"

# 拉取 /api/state — apps + snapshots
curl -sb /tmp/cookies.txt http://127.0.0.1:8000/api/state \
    | python3 -c "import sys,json; d=json.load(sys.stdin); print('apps:', len(d.get('apps',[])))"

# 拉取 CICD 任务
curl -sb /tmp/cookies.txt http://127.0.0.1:8000/api/cicd/tasks \
    | python3 -c "import sys,json; d=json.load(sys.stdin); print('tasks:', len(d.get('tasks',[])))"
```

---

## 7. 关于 8 个 manifest（.xml）App 的 Gerrit 要求

迁移报告中的 **8 个 repo_type='repo' 的 `.xml` manifest 任务** 需要连接：

```
sw-gerrit-devops.metax-internal.com:29418
```

才能解析 `git_url`。在无网络环境（当前 CI/测试机）下，这些任务的 `app_id` 保持 `NULL`（已记录为 `manifest_unresolved`）。

**切换后操作**（有 Gerrit 网络时）：

```bash
# 在 Gerrit 可达的环境执行
python3 tools/migrate_db.py \
    --source release_system/release_system.db \
    --resolve-manifests \
    2>&1 | tee /tmp/migrate-manifest.log
```

新建 app 使用 CICD-first 向导时同样需要 Gerrit 网络才能拉取 `app_info.json`。若 Gerrit 不可达，向导会显示**已解析 Gerrit 身份**（git_url @ git_branch）和跳过按钮；可先创建 App 再手动补填文档信息。

---

## 8. 回滚

若新服务异常，立即回滚：

```bash
NEW_PID=$(pgrep -f 'uvicorn app\.main')
kill $NEW_PID

# 恢复旧 DB
cp release_system/release_system.db.bak-<timestamp> \
   release_system/release_system.db

# 重启旧服务（按原部署方式）
python3 release_system/server.py &
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
| 旧服务已停止 | ☐ |
| 新 DB 已替换（原子 mv） | ☐ |
| 新服务单进程启动（--workers 1） | ☐ |
| /api/health 返回正常 | ☐ |
| apps + tasks 行数正常 | ☐ |
| 8 个 manifest 任务（Gerrit 有网再处理） | ☐ |
