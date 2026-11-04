#!/usr/bin/env bash
# 恢复演练：restore.sh backups/<stamp> → 恢复 PG + 文件 → 验证断言
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
[ -d "${1:-}" ] || { echo "用法: restore.sh backups/<stamp>"; exit 1; }
DIR="$1"; set -a; source .env; set +a
echo "==> 1/3 校验备份完整性"
(cd "$DIR" && sha256sum -c SHA256SUMS)
echo "==> 2/3 恢复 PG 与文件"
docker compose exec -T db psql -U "$POSTGRES_USER" -d postgres \
  -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname='$POSTGRES_DB' AND pid<>pg_backend_pid()" >/dev/null
docker compose exec -T db dropdb -U "$POSTGRES_USER" --if-exists "$POSTGRES_DB"
docker compose exec -T db createdb -U "$POSTGRES_USER" "$POSTGRES_DB"
cat "$DIR/db.dump" | docker compose exec -T db pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --no-owner
rm -rf media export data/index
tar xzf "$DIR/files.tar.gz"
echo "==> 3/3 重启栈并验证"
docker compose restart webserver semantic indexer gateway
sleep 20
code=$(curl -s -o /dev/null -w "%{http_code}" -m 5 "http://127.0.0.1:${GATEWAY_PORT:-8700}/ready" || true)
[ "$code" = "200" ] || { echo "!! 恢复后 /ready=$code"; exit 1; }
echo "✅ 恢复完成且 /ready=200"
