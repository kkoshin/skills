# 问题排查

## 请求返回上游官网 HTML

**现象**：curl 调用返回上游的官网页面，而非 API 响应。

**原因**：请求发到了根路径 `/`，代理透传给上游根路径，上游返回首页。

**解决**：用完整 API 路径：
- Anthropic 格式：`/v1/messages`
- OpenAI 格式：`/v1/chat/completions`

```bash
# ❌ 错误
curl -X POST https://<your-url>/ -d '{"model":"model-b",...}'

# ✅ 正确
curl -X POST https://<your-url>/v1/messages -d '{"model":"model-b",...}'
```

## `model "Model-A" not found` 404

**现象**：传大写别名（如 `Model-A`）时上游返回 404。

**原因**：别名只做了路由匹配，但 body 里的 `model` 字段还是大写，上游对模型名大小写敏感。

**解决**：确保用最新模板的 `proxyRequest`——它会把 body.model 改写为规范名（小写）再转发。关键代码：

```js
if (route.modelName && body.model !== route.modelName) {
  body.model = route.modelName;
}
```

`resolveRoute` 必须回传 `modelName`（规范小写名）。

## SSL handshake failure (curl error 35)

**现象**：curl 报 `sslv3 alert handshake failure` 或 `HTTP_CODE: 000`。

**原因**：本地开启了 Cloudflare WARP 或 VPN，DNS 解析到 `198.18.x.x`（WARP 的本地代理 IP），SSL 握手被拦截。

**解决**：
- 临时关掉 WARP/VPN 再测试
- 或 curl 加 `--resolve` 绕过 DNS：

```bash
curl --resolve "<your-domain>:443:162.159.137.232" --noproxy '*' https://<your-url>/v1/models
```

- 或用 `wrangler tail` 验证 Worker 是否收到请求（不依赖本地 SSL）：

```bash
wrangler tail <worker-name> --format=json &
curl ...  # 发请求
# wrangler tail 会打印请求日志，确认 Worker 已响应
```

## `workers.dev subdomain is unavailable`

**现象**：注册子域名时返回 `Subdomain 'xxx' is unavailable`。

**原因**：该名字已被其他 Cloudflare 用户占用。

**解决**：换一个名字重试。

## `Account already has an associated subdomain`

**现象**：注册新子域名时返回此错误。

**原因**：一个账号只能绑一个 workers.dev 子域名，已有就不能再加。

**解决**：先 DELETE 再 PUT 新名字：

```bash
curl -X DELETE "https://api.cloudflare.com/client/v4/accounts/$ACCOUNT_ID/workers/subdomain" \
  -H "Authorization: Bearer $TOKEN"
# 然后 PUT 新名字
```

> 删除子域名会让已部署的 Worker URL 失效，需重新部署。

## `You need to register a workers.dev subdomain`

**现象**：`wrangler deploy` 报此错，部署失败。

**原因**：账号还没注册 workers.dev 子域名。

**解决**：先注册子域名（见 [deployment.md](deployment.md) 步骤 3），再部署。

## `Unauthorized: wrong secret token` 403

**现象**：调用返回 403。

**原因**：`Authorization` 头里的暗号和 `SECRET_TOKEN` 不匹配。

**解决**：确认请求头是 `Authorization: Bearer <your-secret-token>`，且值和 `worker-proxy.js` 里的 `SECRET_TOKEN` 一致。

## 别名出现在 /v1/models 但调用报错

**现象**：模型列表里有别名（如 `Model-A`），但调用时 404。

**原因**：`DEFAULT_FALLBACK` 里别名的 value 指向的模型名不在 `MODELS` 里。

**解决**：确认别名的 value 模型名部分是 `MODELS` 里已存在的 key（小写）。例如：

```js
// ✅ 正确：model-a 在 MODELS 里
"Model-A": "model-a:provider-a"

// ❌ 错误：model-x 不在 MODELS 里
"Model-A": "model-x:provider-a"
```