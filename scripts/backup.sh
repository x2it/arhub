#!/usr/bin/env bash
# 商业级备份：PG 全库(pg_dump -Fc) + media/export/index + sha256 + 保留 7 天
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
set -a; source .env; set +a
STAMP=$(date +%Y%m%d-%H%M%S)
DIR="backups/$STAMP"
mkdir -p "$DIR"

echo "==> 1/3 pg_dump 全库（archive + archive 业务 schema + 向量）"
docker compose exec -T db pg_dump -Fc -U "$POSTGRES_USER" "$POSTGRES_DB" \
  > "$DIR/db.dump"
echo "    db.dump: $(du -h "$DIR/db.dump" | cut -f1)"

echo "==> 2/3 打包 media + export + Tantivy 索引"
tar czf "$DIR/files.tar.gz" media export data/index 2>/dev/null || \
  tar czf "$DIR/files.tar.gz" media export
echo "    files.tar.gz: $(du -h "$DIR/files.tar.gz" | cut -f1)"

echo "==> 3/3 校验和 + 清理（保留最近 7 份）"
sha256sum "$DIR/db.dump" "$DIR/files.tar.gz" > "$DIR/SHA256SUMS"
ls -1dt backups/*/ | tail -n +8 | xargs -r rm -rf
echo "✅ 备份完成: $DIR"
cat "$DIR/SHA256SUMS"
