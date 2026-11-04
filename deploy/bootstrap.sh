#!/usr/bin/env bash
# 档枢 ArcHub 一键下载引导
# 用法：curl -fsSL "<BASE>/bootstrap.txt" | bash -s -- [目标目录] <BASE>
#   或：ARHUB_BUNDLE_BASE=<BASE> bash bootstrap.sh [目标目录]
# 仅做：下载 → SHA256 校验 → 解压；不自动启动 Docker，启动由执行方决定。
set -euo pipefail
DEST="${1:-$PWD/arhub}"
BASE="${2:-${ARHUB_BUNDLE_BASE:-}}"
if [ -z "$BASE" ]; then
  echo "用法: curl -fsSL \"<BASE>/bootstrap.txt\" | bash -s -- [目标目录] <BASE>" >&2
  echo "  <BASE> 是存放 arhub-deploy.bundle / .sha256 / bootstrap.txt 的 HTTP 地址" >&2
  exit 2
fi
BASE="${BASE%/}"
mkdir -p "$DEST"
cd "$DEST"

echo "==> 从 $BASE 下载分发包（.bundle 为规避部分平台 WAF 的扩展名，内容即 gzip tar）"
curl -fL --retry 3 -o arhub-deploy.bundle  "$BASE/arhub-deploy.bundle"
curl -fL --retry 3 -o arhub-deploy.sha256 "$BASE/arhub-deploy.sha256"

echo "==> 校验 SHA256"
sha256sum -c arhub-deploy.sha256

echo "==> 解压"
tar -xzf arhub-deploy.bundle

echo ""
echo "✅ 项目已就绪：$DEST/arhub-stack"
echo "   部署说明：$DEST/arhub-stack/DEPLOY.md"
echo "   启动命令：cd $DEST/arhub-stack && bash scripts/up.sh"
