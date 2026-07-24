# 部署流程

## 1. 安装并登录 wrangler

```bash
# 安装（若未装）
npm install -g wrangler

# 登录
wrangler login

# 验证
wrangler whoami
```

`wrangler whoami` 会显示登录账号和 Account ID，记下 Account ID（后续注册子域名要用）。

## 2. 初始化项目

```bash
mkdir my-ai-proxy && cd my-ai-proxy
cp <skill-dir>/templates/worker-proxy.js .
cp <skill-dir>/templates/wrangler.jsonc .
```

按需修改：
- `wrangler.jsonc` 的 `name`（Worker 名，会出现在 URL 里）
- `worker-proxy.js` 的配置区（见 [configuration.md](configuration.md)）

## 3. 注册 workers.dev 子域名

每个 Cloudflare 账号需要一个 `workers.dev` 子域名（账号级，只需注册一次）。最终 URL 格式：

```
https://<worker-name>.<subdomain>.workers.dev
```

### 用脚本注册

```bash
bash <skill-dir>/scripts/register-subdomain.sh <your-subdomain>
```

脚本会自动从 wrangler 配置读取 OAuth token 和 Account ID，调用 Cloudflare API 注册。

### 手动注册

```bash
# 从 wrangler 配置读 token
TOKEN=$(grep oauth_token ~/.wrangler/config/default.toml | sed 's/.*= "\(.*\)"/\1/')
# 或 macOS
TOKEN=$(grep oauth_token ~/Library/Preferences/.wrangler/config/default.toml | sed 's/.*= "\(.*\)"/\1/')

# Account ID 从 wrangler whoami 获取
ACCOUNT_ID="your-account-id"

curl -X PUT "https://api.cloudflare.com/client/v4/accounts/$ACCOUNT_ID/workers/subdomain" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"subdomain":"your-subdomain"}'
```

### 子域名被占用

返回 `Subdomain 'xxx' is unavailable` 表示被别人占了，换一个名字重试。

### 子域名已存在想更换

一个账号只能绑一个 workers.dev 子域名。想换名需要先删除再创建：

```bash
curl -X DELETE "https://api.cloudflare.com/client/v4/accounts/$ACCOUNT_ID/workers/subdomain" \
  -H "Authorization: Bearer $TOKEN"
```

然后重新 PUT 新名字。

> 删除子域名会影响已部署的 Worker URL，需重新部署。

## 4. 部署 Worker

```bash
bash <skill-dir>/scripts/deploy.sh
# 或直接
wrangler deploy
```

成功输出示例：

```
Deployed ai-proxy triggers
  https://<worker-name>.<subdomain>.workers.dev
Current Version ID: xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
```

## 5. 验证部署

```bash
URL="https://<worker-name>.<subdomain>.workers.dev"
TOKEN="your-secret-token"

# 健康检查
curl -H "Authorization: Bearer $TOKEN" $URL/health

# 模型列表
curl -H "Authorization: Bearer $TOKEN" $URL/v1/models
```

## 6. 后续更新

### 本地改代码后重新部署

```bash
wrangler deploy
```

### 从网页端拉取最新代码

如果在 Cloudflare Dashboard 的 Quick Edit 里改了代码，可以拉回本地：

```bash
bash <skill-dir>/scripts/pull-remote.sh <worker-name>
```

## 常见部署问题

详见 [troubleshooting.md](troubleshooting.md)。