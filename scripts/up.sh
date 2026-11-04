#!/usr/bin/env bash
# 档枢 ArcHub 一键启动：生成密钥 → 镜像 → 预载模型 → 6 容器 → 补索引 → /ready 门禁
# 适用：全新机器（Docker + docker compose v2 + python3），无需预先准备任何密钥或模型。
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

# ---------- 0) 目录 + 首次密钥 ----------
mkdir -p data media/documents consume export samples logs backups frontend/dist hf-cache
# 容器内以非 root(uid 1000) 运行：网关要写 admin_credentials.txt，webserver 要写 media/consume。
# 内网单机部署下对这些数据目录放宽权限；如需更严格，改为 chown 1000:1000 亦可。
chmod 0777 data media consume export backups hf-cache 2>/dev/null || true

if [ ! -f .env ]; then
  echo "==> 首次启动：生成随机密钥到 .env（chmod 600）"
  GEN=$(python3 -c "import secrets;print(secrets.token_urlsafe(24))")
  PG=$(python3 -c "import secrets;print(secrets.token_urlsafe(18))")
  SK=$(python3 -c "import secrets;print(secrets.token_urlsafe(32))")
  IT=$(python3 -c "import secrets;print(secrets.token_urlsafe(24))")
  sed -e "s|CHANGE_ME_runtime_generated|PLACEHOLDER|g" .env.example > .env
  sed -i -e "s|^POSTGRES_PASSWORD=.*|POSTGRES_PASSWORD=$PG|" \
         -e "s|^PAPERLESS_DBPASS=.*|PAPERLESS_DBPASS=$PG|" \
         -e "s|^PAPERLESS_SECRET_KEY=.*|PAPERLESS_SECRET_KEY=$SK|" \
         -e "s|^JWT_SECRET=.*|JWT_SECRET=$GEN|" \
         -e "s|^INTERNAL_TOKEN=.*|INTERNAL_TOKEN=$IT|" .env
  chmod 600 .env
  echo "    密钥已生成"
fi
set -a; source .env; set +a

# ---------- 0.5) 前端产物（源码仓库不含 dist，首次自动构建） ----------
# 分发包内已自带 frontend/dist，此步会直接跳过；从 git clone 部署时才需要 Node。
if [ ! -f frontend/dist/index.html ]; then
  if command -v pnpm >/dev/null 2>&1; then
    echo "==> 首次构建前端（源码仓库不含 dist）"
    (cd frontend && (pnpm install --frozen-lockfile || pnpm install) && pnpm build)
  else
    echo "!! frontend/dist 缺失且未检测到 pnpm：Web 工作台暂不可用，API 仍可正常使用"
    echo "   稍后安装 Node 22 + pnpm 后执行：cd frontend && pnpm install && pnpm build"
  fi
fi

# ---------- 1) 基础镜像（多源兜底） ----------
ensure_image() {
  local img="$1"; shift
  docker image inspect "$img" >/dev/null 2>&1 && return 0
  for src in "$@"; do
    echo "    拉取 $img <- $src"
    if docker pull "$src" >/dev/null 2>&1; then
      docker tag "$src" "$img"; return 0
    fi
  done
  echo "!! 镜像拉取失败: $img"; return 1
}
echo "==> 1/7 检查基础镜像"
ensure_image "postgres:16-alpine" "docker.1panel.live/library/postgres:16-alpine" "postgres:16-alpine"
ensure_image "redis:7-alpine" "redis:7-alpine"
ensure_image "${PAPERLESS_IMAGE:-ghcr.io/paperless-ngx/paperless-ngx:latest}" \
  "ghcr.nju.edu.cn/paperless-ngx/paperless-ngx:latest"

# ---------- 2) 构建自研镜像 ----------
echo "==> 2/7 构建自研镜像"
if [ "${1:-}" = "--rebuild" ] || ! docker image inspect archive-gateway:2.0 >/dev/null 2>&1; then
  docker compose build gateway
fi
if [ "${1:-}" = "--rebuild" ] || ! docker image inspect archive-semantic:2.0 >/dev/null 2>&1; then
  docker compose build semantic
fi

# ---------- 3) 预载中文嵌入模型（compose 强制 HF_HUB_OFFLINE=1，必须先落盘） ----------
echo "==> 3/7 预载中文嵌入模型"
bash scripts/prefetch-model.sh

# ---------- 4) 起栈 ----------
echo "==> 4/7 启动 6 容器（healthcheck 门禁依赖）"
docker compose up -d

# ---------- 5) s6 解标记 + 等 Paperless 真正起来 ----------
# paperless-ngx 镜像在部分环境下不会自动拉起内部服务，且初始化耗时较长，
# 单次解标记容易打早；这里循环重试直到 Paperless API 返回 200。
echo "==> 5/7 确保 Paperless 内部服务已启动"
PL_READY=0
for i in $(seq 1 36); do
  docker compose exec -T webserver bash -c '
    rm -f /run/service/svc-*/down 2>/dev/null
    s6-svc -u /run/service/svc-* 2>/dev/null
  ' >/dev/null 2>&1 || true
  if docker compose exec -T webserver curl -sf -o /dev/null http://localhost:8000/api/ 2>/dev/null; then
    PL_READY=1; echo "    Paperless 就绪（${i}x10s）"; break
  fi
  sleep 10
done
if [ "$PL_READY" != "1" ]; then
  echo "!! Paperless 未就绪，最近日志："
  docker compose logs --tail=20 webserver
  exit 1
fi

# ---------- 6) 中文检索索引补建（Paperless migrate 完成后才有表） ----------
echo "==> 6/7 补建中文检索 trigram 索引"
bash scripts/ensure-indexes.sh

# ---------- 7) /ready 门禁 ----------
echo "==> 7/7 等待网关 readiness"
GATEWAY_READY=0
for i in $(seq 1 120); do
  code=$(curl -s -o /tmp/ready.json -w "%{http_code}" -m 5 "http://127.0.0.1:${GATEWAY_PORT:-8700}/ready" || true)
  if [ "$code" = "200" ]; then GATEWAY_READY=1; echo "    网关就绪（${i}x5s）"; break; fi
  sleep 5
done
if [ "$GATEWAY_READY" != "1" ]; then
  echo "!! 网关未就绪，最后探测: $(head -c 300 /tmp/ready.json 2>/dev/null)"
  docker compose ps
  exit 1
fi

echo ""
echo "✅ 全栈就绪  http://127.0.0.1:${GATEWAY_PORT:-8700}"
echo "   API 文档  http://127.0.0.1:${GATEWAY_PORT:-8700}/api/docs"
echo "   管理员初始密码: data/admin_credentials.txt（首次登录后请立即修改）"
if [ -f export/manifest.json ]; then
  echo "   可选：bash scripts/restore-demo.sh  载入 8 份演示档案"
fi
