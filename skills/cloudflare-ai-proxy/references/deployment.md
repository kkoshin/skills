# 部署流程

## 1. 安装并登录 wrangler

```bash
npm install -g wrangler

wrangler login
wrangler whoami     # 确认登录成功，并记下 Account ID（注册子域名要用）
```

## 2. 初始化项目

把模板整个目录结构复制到你的项目目录：

```bash
mkdir my-ai-proxy && cd my-ai-proxy
cp -R <skill-dir>/templates/. .
```

复制后目录长这样：

```
my-ai-proxy/
├── src/              # 8 个模块，不需要改
├── test/             # 转换回归测试
├── wrangler.toml     # ← 你的全部配置都在这里
├── package.json
└── .gitignore
```

改两处：

- `wrangler.toml` 的 `name`（Worker 名，会出现在最终 URL 里）
- `wrangler.toml` 的 `[vars]`（渠道、模型、密钥、别名）—— 见 [configuration.md](configuration.md)

## 3. 本地验证（别跳过这步）

配置错误**不会**让部署失败，而是在请求到达时才以 500 暴露出来。所以部署前先在本地跑一次：

```bash
wrangler dev
```

另开一个终端：

```bash
# 模型列表 —— 应该列出你配的模型
curl -s -H "Authorization: Bearer <你的 SECRET_TOKEN>" \
  http://127.0.0.1:8787/v1/models

# 路由测试 —— 用你自己的模型名
curl -s -X POST http://127.0.0.1:8787/v1/messages \
  -H "Authorization: Bearer <你的 SECRET_TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{"model":"<你的模型名>","max_tokens":16,"messages":[{"role":"user","content":"hi"}]}'
```

怎么解读结果：

| 返回 | 含义 |
|---|---|
| **502** | 认证、配置、路由、URL 拼接**全部通过**，只是上游拒绝了这个请求（本地网络到不了上游很正常）。这是配置正确时的预期结果 |
| **500** | 配置不自洽 —— 报错信息会指出是哪个变量 |
| **400** | 路由或别名有问题 —— 模型名不存在，或者该渠道没挂在这个模型下 |

这个"502 即成功"的判据很好用：一个探针就能区分三类故障。

也顺手跑一下回归测试：

```bash
npm run check
```

## 4. 注册 workers.dev 子域名

每个 Cloudflare 账号需要一个 `workers.dev` 子域名（账号级，只需注册一次）。最终 URL 格式：

```
https://<worker-name>.<subdomain>.workers.dev
```

### 用脚本注册

```bash
bash <skill-dir>/scripts/register-subdomain.sh <your-subdomain>
```

脚本会从 wrangler 配置里读 OAuth token 和 Account ID，直接调 Cloudflare API 注册。

### 手动注册

```bash
# macOS
TOKEN=$(grep '^oauth_token' ~/Library/Preferences/.wrangler/config/default.toml | sed 's/.*= *"\(.*\)".*/\1/')
# Linux
TOKEN=$(grep '^oauth_token' ~/.wrangler/config/default.toml | sed 's/.*= *"\(.*\)".*/\1/')

curl -X PUT "https://api.cloudflare.com/client/v4/accounts/$ACCOUNT_ID/workers/subdomain" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"subdomain":"your-subdomain"}'
```

### 子域名被占用

返回 `Subdomain 'xxx' is unavailable` 表示被别人占了，换一个名字重试。

### 想更换已有的子域名

一个账号只能绑一个 workers.dev 子域名。想换名要先删再建：

```bash
curl -X DELETE "https://api.cloudflare.com/client/v4/accounts/$ACCOUNT_ID/workers/subdomain" \
  -H "Authorization: Bearer $TOKEN"
```

然后重新 PUT 新名字。

> 删除子域名会让已部署的 Worker URL 失效，需要重新部署。

## 5. 部署

```bash
bash <skill-dir>/scripts/deploy.sh
# 或
wrangler deploy
# 或
npm run deploy
```

成功输出示例：

```
Uploaded my-ai-proxy (x.xx sec)
Deployed my-ai-proxy triggers
  https://<worker-name>.<subdomain>.workers.dev
```

## 6. 验证部署

```bash
URL="https://<worker-name>.<subdomain>.workers.dev"
TOKEN="<你的 SECRET_TOKEN>"

curl -s -H "Authorization: Bearer $TOKEN" $URL/health
curl -s -H "Authorization: Bearer $TOKEN" $URL/v1/models
```

注意 `/health` **也需要**带暗号。

## 7. 可选：GitHub 集成自动部署

不想每次手动 `wrangler deploy` 的话，可以接 GitHub：在 Cloudflare dashboard 里连上仓库，之后 **push 到指定分支即自动部署**。不需要配置 build command，wrangler 会直接读仓库里的 `wrangler.toml`。

几个要注意的点：

- **`name` 必须与已有 Worker 同名**，否则会新建一个 Worker，而不是更新原来的那个
- **构建/部署命令留空**即可（wrangler 自动识别）
- **`wrangler.toml` 会随仓库生效**，所以 `[vars]` 里的东西会进 git。密钥请用 `wrangler secret put`，不要明文写在 toml 里
- **dashboard 上残留的旧 vars 会被部署删除**。如果你之前在 dashboard 手配过变量，要么删掉它们（推荐，让配置来源只有一处），要么在 toml 里打开 `keep_vars = true`

## 8. 可选：自定义域名与路径前缀

### 挂到自己的域名

在 dashboard 里给 Worker 加 Custom Domain 或 Route 即可。加了之后客户端直接用你自己的域名当 Base URL。

### 挂在子路径下

如果你把 Worker 挂在 `https://example.com/anthropic` 这种**子路径**下，客户端发来的路径会带 `/anthropic` 前缀，而 Worker 只认 `/v1/messages`，会返回 404。

模板默认不做前缀剥离。需要的话，在 `src/http.js` 的 `normalizeRequestPath` 里加回三行：

```js
const PREFIX = "/anthropic";

export function normalizeRequestPath(pathname) {
  const stripped = (pathname === PREFIX || pathname.startsWith(PREFIX + "/"))
    ? (pathname.slice(PREFIX.length) || "/")
    : pathname;
  if (stripped.length > 1 && stripped.endsWith("/")) return stripped.replace(/\/+$/, "");
  return stripped;
}
```

## 9. 常见部署问题

见 [troubleshooting.md](troubleshooting.md)。
