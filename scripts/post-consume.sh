#!/bin/sh
# Paperless post-consume 回调：OCR 完成后通知网关自动建语义索引（阶段5 事件驱动增量）。
# Paperless 传入参数: $1=doc_id ... （官方约定首个参数为 document id）
DOC_ID="$1"
GATEWAY_URL="${GATEWAY_URL:-http://gateway:8700}"
INTERNAL_TOKEN="${INTERNAL_TOKEN:-}"
[ -z "$DOC_ID" ] && exit 0
# 用容器内可用的 HTTP 客户端 fire-and-forget，失败不影响主流程（indexer 轮询兜底）
wget -q -O /dev/null --timeout=5 \
  --header="X-Internal-Token: $INTERNAL_TOKEN" \
  --post-data="{\"doc_id\": $DOC_ID, \"op\": \"upsert\"}" \
  --header="Content-Type: application/json" \
  "$GATEWAY_URL/internal/index" 2>/dev/null || true
exit 0
