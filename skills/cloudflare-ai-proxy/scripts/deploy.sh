#!/usr/bin/env bash
# 部署 Worker 到 Cloudflare
# 用法: bash deploy.sh
#
# 在含 wrangler.toml 和 src/index.js 的项目根目录下运行
# （即复制了 <skill-dir>/templates/. 的目录）。

set -euo pipefail

# ── 定位 wrangler：优先本地安装，其次 PATH ──────────────────────
WRANGLER=""
if [[ -x "node_modules/.bin/wrangler" ]]; then
  WRANGLER="node_modules/.bin/wrangler"
elif command -v wrangler >/dev/null 2>&1; then
  WRANGLER="wrangler"
else
  echo "✘ 未找到 wrangler，请先: npm install -g wrangler"
  exit 1
fi

# ── 前置检查 ────────────────────────────────────────────────────
if [[ ! -f "wrangler.toml" ]]; then
  echo "✘ 当前目录找不到 wrangler.toml"
  echo "  请在项目根目录运行，或先复制 <skill-dir>/templates/. 到此处"
  exit 1
fi

if [[ ! -f "src/index.js" ]]; then
  echo "✘ 当前目录找不到 src/index.js"
  echo "  模板现在是一个多模块项目，需要复制整个目录结构："
  echo "    cp -R <skill-dir>/templates/. ."
  exit 1
fi

if ! "$WRANGLER" whoami >/dev/null 2>&1; then
  echo "✘ 未登录 Cloudflare，请先: wrangler login"
  exit 1
fi

# ── 语法闸门：坏代码不该走到部署这一步 ──────────────────────────
if command -v node >/dev/null 2>&1; then
  for f in src/*.js; do
    node --check "$f" || { echo "✘ 语法错误: $f"; exit 1; }
  done
  echo "✓ 语法检查通过"
fi

# ── 占位符提醒（只警告，不阻断）─────────────────────────────────
if grep -qE 'change-me-|sk-replace-with-|your-|<.*>' wrangler.toml 2>/dev/null; then
  echo
  echo "⚠ wrangler.toml 里似乎还有占位符没替换："
  grep -nE 'change-me-|sk-replace-with-|your-' wrangler.toml | sed 's/^/    /'
  echo "  确认无误再继续。"
  echo
fi

echo "→ 开始部署..."
"$WRANGLER" deploy

echo
echo "✓ 部署完成"
echo
echo "验证（注意 /health 也需要带暗号）："
echo "  curl -H 'Authorization: Bearer <SECRET_TOKEN>' https://<your-url>/health"
echo "  curl -H 'Authorization: Bearer <SECRET_TOKEN>' https://<your-url>/v1/models"
echo
echo "提示：配置错误不会让部署失败，而是在请求到达时才返回 500。"
echo "     若线上行为异常，用 'wrangler tail <worker-name>' 看日志。"
