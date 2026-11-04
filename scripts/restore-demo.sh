#!/usr/bin/env bash
# 可选：把随包附带的 8 份演示档案导入全新的 PostgreSQL 栈。
# 全新部署默认是空库；执行本脚本后即可复现检索/密级/审计等全部演示效果。
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
[ -f export/manifest.json ] || { echo "!! 缺少 export/manifest.json，演示数据不在本包内"; exit 1; }

echo "==> 等待 Paperless 就绪"
for i in $(seq 1 36); do
  docker compose exec -T webserver curl -sf -o /dev/null http://localhost:8000/api/ 2>/dev/null && break
  sleep 5
done

echo "==> 清空 media/documents（原件已包含在 export 内）"
rm -rf media/documents
docker compose exec -T webserver bash -c 'rm -rf /usr/src/paperless/media/documents'

echo "==> 导入档案（document_importer）"
docker compose exec -T webserver bash -c \
  'cd /usr/src/paperless/src && python3 manage.py document_importer ../export --no-progress-bar'

echo "==> 重建全文索引 + 中文 trigram 索引"
docker compose exec -T webserver bash -c \
  'cd /usr/src/paperless/src && python3 manage.py document_index reindex --no-progress-bar' || true
bash scripts/ensure-indexes.sh

echo "==> 触发语义索引对账（indexer 会补齐密级并生成向量）"
docker compose restart indexer >/dev/null
for i in $(seq 1 24); do
  n=$(docker compose exec -T db psql -U "${POSTGRES_USER:-archive}" -d "${POSTGRES_DB:-archive}" \
      -t -A -c "select count(*) from archive.doc_security" 2>/dev/null | tr -d ' \n' || echo 0)
  case "${n:-}" in ''|*[!0-9]*) n=0 ;; esac
  [ "$n" -ge 8 ] && break
  sleep 5
done

echo "✅ 演示数据已载入：$(docker compose exec -T db psql -U "${POSTGRES_USER:-archive}" -d "${POSTGRES_DB:-archive}" -t -A -c 'select count(*) from archive.documents_document') 份档案"
