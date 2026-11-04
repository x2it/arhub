#!/usr/bin/env bash
# 预下载中文嵌入模型到 ./hf-cache。compose 中 semantic 服务强制 HF_HUB_OFFLINE=1，
# 缓存缺失会导致语义服务启动失败，因此 up.sh 在起栈前调用本脚本。
# 已带权重的缓存会直接跳过；网络不可用时请手工把模型目录放入 hf-cache 后重试。
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
[ -f .env ] && { set -a; source .env; set +a; }

CACHE="$ROOT/hf-cache"
mkdir -p "$CACHE"
if find "$CACHE" -type f \( -name '*.safetensors' -o -name 'pytorch_model.bin' \) | grep -q .; then
  echo "    模型已缓存，跳过下载"
  exit 0
fi

MODEL="${EMBED_MODEL:-BAAI/bge-small-zh-v1.5}"
ENDPOINT="${HF_ENDPOINT:-https://hf-mirror.com}"
echo "    下载 $MODEL -> hf-cache（约 90MB，首次需要联网）"

docker run --rm --user 0 \
  -e HF_HUB_OFFLINE=0 \
  -e HF_ENDPOINT="$ENDPOINT" \
  -e HF_HOME=/hf-cache \
  -e MODEL_NAME="$MODEL" \
  -v "$CACHE:/hf-cache" \
  archive-semantic:2.0 \
  sh -c "python -c 'import os;from huggingface_hub import snapshot_download;snapshot_download(os.environ[\"MODEL_NAME\"])' && chown -R 1000:1000 /hf-cache"

find "$CACHE" -type f \( -name '*.safetensors' -o -name 'pytorch_model.bin' \) | grep -q . \
  || { echo "!! 模型下载失败：检查 HF_ENDPOINT 连通性，或手工放置模型后重试"; exit 1; }
echo "    模型就绪"
