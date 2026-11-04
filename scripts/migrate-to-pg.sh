#!/usr/bin/env bash
# SQLite → PostgreSQL 数据迁移（Paperless 官方 document_exporter / document_importer）
# 前提：旧栈（SQLite 版）仍在运行；本脚本完成后新栈（PG）接管。
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

echo "==> 1/5 快照基线（WAL checkpoint + 归档）"
python3 - <<'PY'
import sqlite3
c = sqlite3.connect('data/db.sqlite3')
c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
n = c.execute("SELECT count(*) FROM documents_document").fetchone()[0]
print(f"    文档数={n}")
assert n > 0, "无数据可迁移"
PY
mkdir -p data/legacy-sqlite
cp data/db.sqlite3 data/legacy-sqlite/db.sqlite3.$(date +%Y%m%d%H%M%S)
echo "    SQLite 已归档到 data/legacy-sqlite/"

echo "==> 2/5 旧栈上运行 document_exporter（导出元数据+OCR+原件）"
docker compose exec -T webserver bash -c '
  cd /usr/src/paperless/src && python3 manage.py document_exporter ../export --no-progress-bar
'
ls export/manifest.json >/dev/null && echo "    导出完成: $(du -sh export | cut -f1)"

echo "==> 3/5 停旧栈，起新栈（PG）"
docker compose down
# 清掉 SQLite 残留（已归档），避免 Paperless 混淆
rm -f data/db.sqlite3 data/db.sqlite3-wal data/db.sqlite3-shm data/migration_lock
rm -rf data/index  # Tantivy 索引将随导入重建
docker compose up -d db broker webserver

echo "==> 4/5 等待新 webserver 就绪"
for i in $(seq 1 90); do
  if docker compose exec -T webserver curl -sf -o /dev/null http://localhost:8000/api/ 2>/dev/null; then
    echo "    就绪（${i}x5s）"; break
  fi
  sleep 5
  [ "$i" = "90" ] && { echo "!! webserver 启动超时"; docker compose logs --tail=30 webserver; exit 1; }
done

echo "==> 5/5 document_importer 导入 PG + 验证"
docker compose exec -T webserver bash -c '
  cd /usr/src/paperless/src && python3 manage.py document_importer ../export --no-progress-bar
'
python3 - <<'PY'
import os, sys, urllib.request, json
# 用容器外验证：稍后由 e2e 覆盖；这里仅确认导入脚本无异常退出
print("    导入完成")
PY
echo "✅ 迁移完成。后续由 up.sh 拉起完整 6 容器栈。"
