#!/usr/bin/env bash
# 工程化闭环：静态检查 → 单元测试 → 前端构建 → 起栈 → 端到端 → 压测
# 用法：bash scripts/ci.sh [--skip-load]
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
PY=${PYTHON:-python3}

echo "===== 1/6 静态检查（语法编译） ====="
$PY -m compileall -q gateway semantic shared scripts && echo "  OK"

echo "===== 2/6 单元测试 ====="
$PY -m pytest tests/unit -q 2>&1 | tail -5

echo "===== 3/6 前端构建 ====="
if [ -d frontend/node_modules ]; then
  (cd frontend && pnpm build 2>&1 | tail -4)
else
  echo "  跳过（未安装依赖，请先 cd frontend && pnpm install）"
fi

echo "===== 4/6 起栈并过 /ready 门禁 ====="
bash scripts/up.sh

echo "===== 5/6 端到端断言 ====="
$PY scripts/e2e_test.py

if [ "${1:-}" != "--skip-load" ]; then
  echo "===== 6/6 检索压测 ====="
  $PY scripts/load_test.py 8 200
else
  echo "===== 6/6 压测已跳过 ====="
fi

echo ""
echo "✅ CI 全流程通过"
