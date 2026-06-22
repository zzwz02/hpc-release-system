# CICD App-backed Requests 二次切换手册

适用场景：已经完成原 `CUTOVER.md`，当前服务已运行 FastAPI 版本；现在把
`cicd_task_requests` 从旧 `task_id -> cicd_tasks` 模型迁移为
`app_id -> apps` 模型，并清空旧 `cicd_tasks` 业务数据。

目标状态：

- `apps` 保存 CICD 配置字段。
- `cicd_task_requests.app_id` 直接关联 `apps.id`。
- `cicd_task_requests.task_id` 仅保留为 API 兼容别名，值写成 `app_id`。
- `cicd_notifications` 保留，不按 App 删除；红点数量由有效 request 计算。
- `cicd_tasks` 不再作为业务数据源，可清空。

---

## 0. 停服务

```bash
PID=$(pgrep -f 'uvicorn [a]pp.main' || true)
echo "FastAPI PID: $PID"
test -n "$PID" && kill $PID
sleep 2
```

---

## 1. 备份当前 DB

```bash
cp release_system.db release_system.db.bak-cicd-app-requests-$(date +%Y%m%d-%H%M%S)
ls -lh release_system.db.bak-cicd-app-requests-*
```

---

## 2. Dry-run 检查 request 映射

```bash
python3 -u tools/migrate_cicd_requests_to_apps.py \
    release_system.db \
    --dry-run \
    2>&1 | tee /tmp/migrate-cicd-app-requests-dryrun.log
```

重点看：

```text
requests_total: N
requests_resolved: N
requests_unresolved: 0
```

如果 `requests_unresolved > 0`，说明有 request 无法映射到现有 App。通常是历史孤立流程。
确认可以丢弃后，正式迁移时加 `--drop-unresolved`。

---

## 3. 正式迁移

无孤立 request：

```bash
python3 -u tools/migrate_cicd_requests_to_apps.py \
    release_system.db \
    --clear-cicd-tasks \
    2>&1 | tee /tmp/migrate-cicd-app-requests.log
```

存在确认可丢弃的孤立 request：

```bash
python3 -u tools/migrate_cicd_requests_to_apps.py \
    release_system.db \
    --drop-unresolved \
    --clear-cicd-tasks \
    2>&1 | tee /tmp/migrate-cicd-app-requests.log
```

脚本会自动再生成一份：

```text
release_system.db.before-cicd-app-requests.bak
```

---

## 4. 验证 DB

```bash
python3 - <<'EOF'
import sqlite3, sys

conn = sqlite3.connect("release_system.db")
conn.row_factory = sqlite3.Row
conn.execute("PRAGMA foreign_keys = ON")

fk = conn.execute("PRAGMA foreign_key_check").fetchall()
if fk:
    print("FK VIOLATION:", fk)
    sys.exit(1)

cols = [r["name"] for r in conn.execute("PRAGMA table_info(cicd_task_requests)")]
print("request columns:", cols)
if "app_id" not in cols:
    raise SystemExit("missing cicd_task_requests.app_id")

bad = conn.execute("""
    SELECT COUNT(*)
    FROM cicd_task_requests r
    LEFT JOIN apps a ON a.id = r.app_id
    WHERE r.app_id IS NULL OR a.id IS NULL
""").fetchone()[0]
print("bad request app links:", bad)
if bad:
    raise SystemExit("request app link validation failed")

tasks = conn.execute("SELECT COUNT(*) FROM cicd_tasks").fetchone()[0]
print("legacy cicd_tasks rows:", tasks)
if tasks:
    raise SystemExit("legacy cicd_tasks should be empty")

print("Validation PASSED")
EOF
```

---

## 5. 重建前端并启动服务

```bash
cd web
npm install
npm run build
cd ..

export no_proxy=localhost,127.0.0.1
export NO_PROXY=localhost,127.0.0.1

python3 -m uvicorn app.main:app \
    --host 127.0.0.1 \
    --port 3577 \
    --workers 1 \
    --log-level info \
    >> /tmp/uvicorn.log 2>&1 &

sleep 3
curl --noproxy '*' -fsS http://127.0.0.1:3577/api/ldap/status | python3 -m json.tool
```

---

## 6. 冒烟验证

```bash
curl --noproxy '*' -sc /tmp/cookies.txt -s \
    -X POST http://127.0.0.1:3577/api/login \
    -H 'Content-Type: application/json' \
    -d '{"username":"rm","password":"rm"}'

curl --noproxy '*' -fsS -sb /tmp/cookies.txt \
    http://127.0.0.1:3577/api/cicd/tasks \
    | python3 -c "import sys,json; d=json.load(sys.stdin); print('tasks:', len(d.get('tasks',[])))"

curl --noproxy '*' -fsS -sb /tmp/cookies.txt \
    http://127.0.0.1:3577/api/cicd/requests \
    | python3 -c "import sys,json; d=json.load(sys.stdin); print('requests:', len(d.get('requests',[])))"
```

删除 App 后，关联的 `cicd_task_requests` 会被显式清理或通过外键级联清理；
`cicd_notifications` 不会被删除，红点会在重新计算 request 数量后自然消失。
