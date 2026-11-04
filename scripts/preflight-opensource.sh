#!/usr/bin/env bash
# 开源合规自检：推送到公开仓库前运行，拦截密钥、私有链接、占位符、本机路径与运行时产物。
# 仅检查已被 git 跟踪的文件；运行前请确保 `git status` 中无遗漏的敏感未跟踪文件。
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

git rev-parse --is-inside-work-tree >/dev/null 2>&1 || { echo "!! 当前目录不是 git 仓库"; exit 2; }

FAIL=0
bad() { echo "  ✗ $*"; FAIL=1; }
ok()  { echo "  ✓ $*"; }

echo "== 1/5 私有痕迹 / 占位符 / 密钥扫描（git 跟踪文件，排除本脚本）=="
BAD_RE='sharecode=|app\.workbuddy\.host|a05067b3fca6f00d1|DZPSMRLKOB|/workspace/|/root/\.codebuddy|BEGIN [A-Z0-9 ]*PRIVATE KEY|AKIA[0-9A-Z]{16}|ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|<owner>|<repo>|REPO_URL|<your-host>|<your-fork-url>'
SCAN="$(mktemp)"; trap 'rm -f "$SCAN"' EXIT
if git grep -n -I -E "$BAD_RE" -- . ':!scripts/preflight-opensource.sh' > "$SCAN" 2>/dev/null; then
  bad "发现私有链接 / 占位符 / 绝对路径 / 密钥痕迹："
  sed 's/^/      /' "$SCAN" | head -20
else
  ok "无私有链接、无占位符、无本机绝对路径、无私钥"
fi

echo "== 2/5 敏感文件是否被跟踪 =="
SENS_RE='(^|/)\.env$|admin_credentials|\.dump$|\.pem$|id_rsa|(^|/)data/|(^|/)hf-cache/|(^|/)backups/|(^|/)frontend/dist/'
if git ls-files | grep -E "$SENS_RE" > "$SCAN" 2>/dev/null; then
  bad "以下敏感/运行时文件被纳入版本库："
  sed 's/^/      /' "$SCAN" | head -20
else
  ok "密钥、凭据、数据与构建产物均未入库"
fi

echo "== 3/5 必需开源文档 =="
for f in LICENSE NOTICE README.md AGENTS.md DEPLOY.md CONTRIBUTING.md CODE_OF_CONDUCT.md SECURITY.md CHANGELOG.md .gitignore; do
  [ -f "$f" ] || bad "缺少 $f"
done
if [ "$FAIL" -eq 0 ]; then ok "LICENSE / NOTICE / 治理与部署文档齐全"; fi

echo "== 4/5 被跟踪的大文件（> 5MB）=="
BIG="$(git ls-files -z | xargs -0 -r stat -c '%s %n' 2>/dev/null | awk '$1 > 5242880 {print $2}' | head -5 || true)"
if [ -n "$BIG" ]; then
  bad "存在大文件，请改用 Git LFS 或外部托管：$BIG"
else
  ok "无超大二进制文件"
fi

echo "== 5/5 .gitignore 关键规则 =="
for rule in '^\.env$' '^data/' '^hf-cache/' '^frontend/dist/' '^backups/'; do
  grep -Eq "$rule" .gitignore || bad ".gitignore 缺少规则：$rule"
done
if [ "$FAIL" -eq 0 ]; then ok "忽略规则完整"; fi

echo ""
if [ "$FAIL" -ne 0 ]; then
  echo "[失败] 开源合规自检未通过，请勿推送公开仓库"
  exit 1
fi
echo "[通过] 开源合规自检通过"
