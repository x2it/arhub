#!/bin/sh
# Paperless post-consume 回调：OCR 完成后通知网关登记密级 + 建语义索引（事件驱动增量）。
#
# Paperless-ngx v3 以**环境变量**传递文档信息、不传位置参数（DOCUMENT_ID 等），
# v2 才是 $1=document_id。这里以 DOCUMENT_ID 为主、$1 为兼容兜底。
#
# 通知网关做两件事：
#   1) 登记密级（fail-closed 前提：每份档案必有 doc_security 记录，否则检索不到）
#   2) 入队语义索引任务
DOC_ID="${DOCUMENT_ID:-$1}"
GATEWAY_URL="${GATEWAY_URL:-http://gateway:8700}"
INTERNAL_TOKEN="${INTERNAL_TOKEN:-}"
[ -z "$DOC_ID" ] && exit 0
# fire-and-forget：失败不影响 Paperless 主流程，索引器周期对账会兜底补齐。
RESP=$(curl -s -o /dev/null -w 'HTTP %{http_code}' --max-time 5 \
  -H "X-Internal-Token: $INTERNAL_TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"doc_id\": $DOC_ID, \"op\": \"upsert\"}" \
  "$GATEWAY_URL/internal/index" 2>/dev/null)
echo "post-consume doc_id=$DOC_ID gateway=$GATEWAY_URL resp=[$RESP]" >&2
exit 0
