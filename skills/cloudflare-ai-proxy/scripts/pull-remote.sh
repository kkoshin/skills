#!/usr/bin/env bash
# 从已部署的 Cloudflare Worker 拉取最新代码
# 用法: bash pull-remote.sh <worker-name> [output-file]
#
# 适用于在 Dashboard Quick Edit 里改了代码后，把改动同步回本地。
# 从 wrangler 配置自动读取 OAuth token 和 Account ID。

set -euo pipefail

WORKER_NAME="${1:-}"
OUTPUT_FILE="${2:-worker-proxy.js}"

if [[ -z "$WORKER_NAME" ]]; then
  echo "用法: bash $0 <worker-name> [output-file]"
  echo "例:   bash $0 ai-proxy"
  exit 1
fi

# 读取 wrangler OAuth token
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
  echo "✘ 无法读取 oauth_token"
  exit 1
fi

# 获取 Account ID
ACCOUNT_ID=$(curl -s "https://api.cloudflare.com/client/v4/accounts" \
  -H "Authorization: Bearer $TOKEN" | python3 -c "
import json, sys
data = json.load(sys.stdin)
if data.get('success') and data.get('result'):
    print(data['result'][0]['id'])
" 2>/dev/null || true)
if [[ -z "$ACCOUNT_ID" ]]; then
  echo "✘ 无法获取 Account ID"
  exit 1
fi

echo "→ 拉取 Worker: $WORKER_NAME"

# Cloudflare API 返回 multipart，提取第一个 part 的内容（即主脚本）
curl -s "https://api.cloudflare.com/client/v4/accounts/$ACCOUNT_ID/workers/scripts/$WORKER_NAME" \
  -H "Authorization: Bearer $TOKEN" | \
python3 -c "
import sys, re
content = sys.stdin.read()
match = re.search(r'Content-Disposition: form-data; name=\".+?\"\r?\n\r?\n(.+?)(?:\r?\n--|\Z)', content, re.DOTALL)
if match:
    open('$OUTPUT_FILE', 'w').write(match.group(1).strip())
    print('✓ 已写入 $OUTPUT_FILE')
else:
    print('✘ 无法解析脚本内容')
    sys.exit(1)
"