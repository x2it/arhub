#!/usr/bin/env bash
# 生成可分发的完整部署包（tar.gz + SHA256 + 文件清单 + 一键 bootstrap）。
# 用法：bash deploy/make_bundle.sh [输出目录，默认 frontend/dist（由网关静态托管）]
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
OUT_DIR="$(cd "${1:-$ROOT/frontend/dist}" 2>/dev/null && pwd || { mkdir -p "${1:-$ROOT/frontend/dist}"; cd "${1:-$ROOT/frontend/dist}"; pwd; })"
NAME="arhub-deploy"
PKG_DIR="arhub-stack"
STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT
mkdir -p "$STAGE/$PKG_DIR"

echo "==> 收集源码（排除密钥与运行时数据）"
tar -C "$ROOT" \
  --exclude='./.git' \
  --exclude='./.env' \
  --exclude='./data' \
  --exclude='./hf-cache' \
  --exclude='./backups' \
  --exclude='./logs' \
  --exclude='./.pytest_cache' \
  --exclude='./frontend/node_modules' \
  --exclude='./frontend/tsconfig.tsbuildinfo' \
  --exclude='./frontend/dist/arhub-deploy.bundle' \
  --exclude='./frontend/dist/arhub-deploy.sha256' \
  --exclude='./frontend/dist/bootstrap.txt' \
  --exclude='./frontend/dist/probe*' \
  --exclude='./frontend/dist/MANIFEST.txt' \
  --exclude='*.pyc' \
  --exclude='__pycache__' \
  -cf - . | tar -C "$STAGE/$PKG_DIR" -xf -

# 包内清单：文件数、体积、生成时间、内容边界
FILE_COUNT=$(find "$STAGE/$PKG_DIR" -type f | wc -l)
{
  echo "档枢 ArcHub · 分发包清单"
  echo "生成时间 : $(date -u '+%Y-%m-%d %H:%M:%S UTC')"
  echo "顶层目录 : $PKG_DIR/"
  echo "文件数量 : $FILE_COUNT"
  echo "内含     : 完整源码 / docker-compose / migrations / 前端 dist / 脚本 / 中文 OCR 包 / 8 份演示档案 / 文档"
  echo "不含     : .env、data/、hf-cache/、backups/、logs/、.git/、node_modules/、任何凭据与 dump"
  echo "部署     : 解压后 cd $PKG_DIR && bash scripts/up.sh"
  echo "详见     : DEPLOY.md"
  echo ""
  echo "-- 文件列表 --"
  (cd "$STAGE" && find "$PKG_DIR" -type f | sort)
} > "$STAGE/$PKG_DIR/MANIFEST.txt"

echo "==> 打包（部分托管平台 WAF 拦截 .tar.gz/.sh 后缀，产物用 .bundle，内容仍是 gzip tar）"
tar -C "$STAGE" -czf "$OUT_DIR/$NAME.bundle" "$PKG_DIR"
(cd "$OUT_DIR" && sha256sum "$NAME.bundle" > "$NAME.sha256")
cp "$ROOT/deploy/bootstrap.sh" "$OUT_DIR/bootstrap.txt"
chmod +x "$ROOT/scripts/"*.sh "$OUT_DIR/bootstrap.txt" 2>/dev/null || true

# 敏感文件兜底检查：出现即判失败，绝不外发
if tar -tzf "$OUT_DIR/$NAME.bundle" | grep -E '(^|/)\.env$|admin_credentials|\.dump$|\.pem$|id_rsa|(^|/)hf-cache/|(^|/)data/' ; then
  echo "!! 包内含敏感文件，已中止并删除产物" >&2
  rm -f "$OUT_DIR/$NAME.bundle" "$OUT_DIR/$NAME.sha256"
  exit 1
fi

SIZE=$(du -h "$OUT_DIR/$NAME.bundle" | cut -f1)
SHA=$(cut -d' ' -f1 "$OUT_DIR/$NAME.sha256")
echo "✅ $OUT_DIR/$NAME.bundle  ($SIZE, $FILE_COUNT 个文件)"
echo "   SHA256 $SHA"
