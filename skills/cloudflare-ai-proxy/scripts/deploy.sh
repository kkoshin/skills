#!/usr/bin/env bash
# 部署 Worker 到 Cloudflare
# 用法: bash deploy.sh
#
# 在含 wrangler.jsonc 和 worker-proxy.js 的目录下运行。

set -euo pipefail

if ! command -v wrangler >/dev/null 2>&1; then
  echo "✘ 未安装 wrangler，请先: npm install -g wrangler"
  exit 1
fi

if [[ ! -f "wrangler.jsonc" && ! -f "wrangler.toml" ]]; then
  echo "✘ 当前目录找不到 wrangler.jsonc 或 wrangler.toml"
  echo "  请在项目根目录运行，或先复制 templates/wrangler.jsonc"
  exit 1
fi

if [[ ! -f "worker-proxy.js" ]]; then
  echo "✘ 当前目录找不到 worker-proxy.js"
  echo "  请先复制 templates/worker-proxy.js"
  exit 1
fi

echo "→ 开始部署..."
wrangler deploy

echo
echo "✓ 部署完成"
echo
echo "验证:"
echo "  curl -H 'Authorization: Bearer <your-secret-token>' https://<your-url>/health"
echo "  curl -H 'Authorization: Bearer <your-secret-token>' https://<your-url>/v1/models"