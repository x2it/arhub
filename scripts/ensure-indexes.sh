#!/usr/bin/env bash
# 补建中文关键词检索所需的 pg_trgm 索引。
# initdb 阶段 Paperless 尚未建表，索引只能在 webserver migrate 完成后创建，故独立成脚本并循环等待。
# 幂等；失败只影响检索性能（退化为 ILIKE 全表扫描），不阻断启动。
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
[ -f .env ] && { set -a; source .env; set +a; }
DBUSER="${POSTGRES_USER:-archive}"
DBNAME="${POSTGRES_DB:-archive}"

SQL="
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE INDEX IF NOT EXISTS idx_doc_title_trgm ON archive.documents_document USING gin (title gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_doc_content_trgm ON archive.documents_document USING gin (content gin_trgm_ops);
"

for i in $(seq 1 30); do
  if docker compose exec -T db psql -U "$DBUSER" -d "$DBNAME" -v ON_ERROR_STOP=1 -c "$SQL" >/dev/null 2>&1; then
    echo "    中文 trigram 索引就绪（${i}x5s）"
    exit 0
  fi
  sleep 5
done
echo "!! trigram 索引补建失败：Paperless 表可能尚未创建，功能不受影响但中文检索会变慢" >&2
exit 0
