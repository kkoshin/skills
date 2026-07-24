#!/usr/bin/env bash
# 注册 Cloudflare workers.dev 子域名
# 用法: bash register-subdomain.sh <subdomain>
#
# 从 wrangler 配置自动读取 OAuth token 和 Account ID。
# 一个账号只需注册一次。若已存在子域名会报错，需先删除。

set -euo pipefail

SUBDOMAIN="${1:-}"
if [[ -z "$SUBDOMAIN" ]]; then
  echo "用法: bash $0 <subdomain>"
  echo "例:   bash $0 my-ai-proxy"
  exit 1
fi

# 读取 wrangler OAuth token（兼容 macOS 和 Linux 的配置路径）
WRANGLER_CONFIG=""
for p in \
  "$HOME/Library/Preferences/.wrangler/config/default.toml" \
  "$HOME/.wrangler/config/default.toml" \
  "$HOME/.config/.wrangler/config/default.toml"; do
  if [[ -f "$p" ]]; then WRANGLER_CONFIG="$p"; break; fi
done
if [[ -z "$WRANGLER_CONFIG" ]]; then
  echo "✘ 找不到 wrangler 配置，请先 wrangler login"
  exit 1
fi

TOKEN=$(grep '^oauth_token' "$WRANGLER_CONFIG" | head -1 | sed 's/.*= *"\(.*\)".*/\1/')
if [[ -z "$TOKEN" ]]; then
  echo "✘ 无法从 $WRANGLER_CONFIG 读取 oauth_token"
  exit 1
fi

# 获取 Account ID
ACCOUNT_INFO=$(curl -s "https://api.cloudflare.com/client/v4/accounts" \
  -H "Authorization: Bearer $TOKEN")
ACCOUNT_ID=$(echo "$ACCOUNT_INFO" | python3 -c "
import json, sys
data = json.load(sys.stdin)
if data.get('success') and data.get('result'):
    print(data['result'][0]['id'])
" 2>/dev/null || true)
if [[ -z "$ACCOUNT_ID" ]]; then
  echo "✘ 无法获取 Account ID，请检查 wrangler whoami"
  exit 1
fi

echo "→ Account ID: $ACCOUNT_ID"
echo "→ 注册子域名: $SUBDOMAIN"

RESULT=$(curl -s -X PUT "https://api.cloudflare.com/client/v4/accounts/$ACCOUNT_ID/workers/subdomain" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"subdomain\":\"$SUBDOMAIN\"}")

SUCCESS=$(echo "$RESULT" | python3 -c "import json,sys; print(json.load(sys.stdin).get('success', False))" 2>/dev/null || echo "False")
if [[ "$SUCCESS" == "True" ]]; then
  echo "✓ 子域名注册成功: $SUBDOMAIN.workers.dev"
else
  echo "✘ 注册失败:"
  echo "$RESULT" | python3 -c "import json,sys; print(json.load(sys.stdin).get('errors'))" 2>/dev/null || echo "$RESULT"
  echo
  echo "常见原因:"
  echo "  - 子域名已被占用 → 换个名字"
  echo "  - 账号已有子域名 → 先 DELETE 再重试"
  exit 1
fi