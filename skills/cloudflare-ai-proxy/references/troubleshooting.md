# 问题排查

## 返回 400：`Unknown model "x". No fallback configured.`

**现象**：调用 `/v1/messages` 返回 400，报文是上面这句。

**原因**：请求里的模型名既不在 `MODELS` 里，也没有配 `DEFAULT_FALLBACK` 或 `MODEL_ALIASES`。

**解决**：按需选一种 ——

```toml
# 1. 模型确实存在，只是没配默认渠道
[vars.DEFAULT_FALLBACK]
model-a = "model-a:provider-a"

# 2. 客户端发的是别名（如 Claude Code 的 claude-sonnet-4-5）
[vars.MODEL_ALIASES]
"claude-*" = "model-a"
```

## 返回 400：`Provider "p" is not available for model "m". Available: ...`

**原因**：`model:provider` 里的渠道没有挂在这个模型下（或者别名里固定了一个该模型没有的渠道）。

**解决**：报文里 `Available:` 后面列出了该模型真正可用的渠道，照它改；或者去 `MODELS` 里补一段 `[[vars.MODELS.<model>]]`。

## 返回 404：`Only the Anthropic POST /v1/messages endpoint is supported.`

**现象**：往 `/v1/chat/completions` 发请求返回 404。

**原因**：这是**有意设计**。代理只承诺 Anthropic 下游协议，OpenAI 兼容仅作为上游协议使用。

**解决**：改用 `POST /v1/messages`，并把请求体换成 Anthropic Messages 格式。如果你的客户端只支持 OpenAI 格式，见 [client-integration.md](client-integration.md) —— 需要换客户端或在前面加一层转换。

## 返回 404：`Token counting is not available ...`

**现象**：调用 `/v1/messages/count_tokens` 返回 404。

**原因**：预期行为。Claude Code 会调这个接口，代理明确告知不可用，客户端会自动回退到本地估算。

**解决**：不用管。这不是故障。

## 返回 403：`Unauthorized: wrong secret token.`

**原因**：凭证与 `SECRET_TOKEN` 不一致。注意校验是**精确相等**，不是子串包含。

**解决**：确认以下任一种写法且值正确 ——

```bash
-H "Authorization: Bearer <SECRET_TOKEN>"
-H "Authorization: <SECRET_TOKEN>"
-H "x-api-key: <SECRET_TOKEN>"
```

如果连 `GET /health`、`GET /v1/models` 也返回 403，那是正常的：**校验发生在路由之前**，这两个接口同样需要带凭证。只有 `OPTIONS` 预检免校验。

若返回的是 **500** 而不是 403，那是另一回事：`SECRET_TOKEN` 压根没配置。

## 返回 500：`Configuration error: ...`

**现象**：请求返回 500，报文以 `Configuration error:` 开头。

**原因**：配置有问题。报文**只含变量名，不含变量值**（这是有意设计）。

几个实际报文：

| 报文 | 原因 |
|---|---|
| `Env var PROVIDERS is not valid JSON` | `[vars]` 里的 JSON 变量格式坏了 |
| `Provider "p" has an invalid baseUrl: Invalid URL` | `baseUrl` 不是绝对 http(s) URL，或带了 query/hash |
| `DEFAULT_FALLBACK.m references unknown model "ghost"` | 兜底指向了 `MODELS` 里没有的模型 |
| `MODEL_ALIASES.sonnet references unknown model "ghost"` | 别名指向了 `MODELS` 里没有的模型 |
| `Model "m" contains duplicate provider "p"` | 同一模型下重复挂了同一个渠道 |
| `Secret "X" (referenced by token_ref) is not configured` | 引用的密钥变量不存在（见下一条） |

**关键**：配置是**在请求到达时才校验**的，所以

> **部署成功 ≠ 配置正确。** 用 `wrangler dev` 在本地复现，或在线上用 `wrangler tail` 看日志。

## 500：`Secret "X" (referenced by token_ref) is not configured`

**原因**：`token_ref` / `key_ref` 填的是**变量名**，而 `[vars]` 里没有这个变量。

**解决**：

```bash
wrangler secret put X          # 立即生效，不用重新部署
# 或者写进 wrangler.toml 的 [vars] 后重新部署
```

**排查提示**：这个错误只在请求**真的路由到那个模型**时才出现 —— `loadConfig` 阶段不校验密钥是否存在。所以某个模型平时好好的，一换到另一个渠道就 500，多半是那个渠道的密钥没配。

## 返回 502：`Upstream error: ...`

**原因**：上游连不上或上游返回了非 2xx。上游的错误状态码和类型会被透传（429 映射成 `rate_limit_error`）。

**排查**：

```bash
wrangler tail <worker-name>     # 看 Worker 日志，含被丢弃字段的 console.debug
```

**本地开发时特别说明**：用 `wrangler dev` 测试时，占位符域名（`api.provider-a.example.com` 之类）解析不了，必然返回 502。**这恰恰说明认证、配置、路由、URL 拼接全都通过了** —— 是配置正确时的预期结果，不是 bug。

## 别名不生效 / 优先级不符预期

**排查步骤**：

1. 先用 `model:provider` 直连，确认模型本身可达
2. 再单独测别名，排除是模型的问题还是别名的问题

**匹配规则**（按顺序）：

1. 精确匹配优先于通配
2. 通配之间，去掉 `*` 后**更长**的模式优先（更具体者胜）
3. 长度相同时，按 `MODEL_ALIASES` 里的声明顺序取先出现的
4. 匹配前模型名会转小写，并剥掉尾部的 `[1m]` 这类后缀
5. 每个键最多只能有**一个** `*`，写两个会加载失败

## 别名没有出现在 `/v1/models` 里

**现象**：配了 `MODEL_ALIASES`，但调用 `/v1/models` 看不到它们。

**原因**：预期行为。`/v1/models` 列的是 `MODELS` 的键，加上 `DEFAULT_FALLBACK` 里不在 `MODELS` 的键；**别名一律不列出**。

**解决**：不用管，别名照常可用。判断模型是否可用应该以实际调用为准，而不是列表。

## 上游报 `model not found`，但模型名明明配对

**原因**：这是架构限制 —— 发给上游的模型名会被**统一转成小写**。如果上游的模型名大小写敏感（如 `GLM-4.6`、`Llama-3.3-70B`），就无法原样表达。

**解决**：目前没有配置项能绕过。只能确认上游是否有全小写的等价模型名，或在中间加一层名称转换。

## 改了 `wrangler.toml` 但没生效

**原因**：`[vars]` 的变更需要重新部署才生效。

**解决**：

```bash
wrangler deploy
```

如果同时还在 dashboard 上配过变量，两边会冲突 —— dashboard 上的旧 vars 会被 `wrangler deploy` 删除，除非打开了 `keep_vars = true`。推荐让配置来源只有 `wrangler.toml` 一处。

## 返回 405 `Only GET, POST, and OPTIONS requests are supported.`

**原因**：用了 PUT / DELETE / HEAD 之类的方法。响应里带 `Allow: GET, POST, OPTIONS` 头。

**解决**：改用正确的方法。

## CORS 预检失败

**现象**：浏览器报 preflight 失败。

**原因**：请求带了不在白名单里的自定义头。

**解决**：白名单是 `Content-Type, Authorization, x-api-key, anthropic-version, anthropic-beta`。需要额外自定义头的话，改 `src/http.js` 里 `corsHeaders()` 的 `Access-Control-Allow-Headers`。

> `OPTIONS` 预检**不需要**凭证，它在认证之前就被处理了。

## SSL handshake failure（curl error 35 / HTTP 000）

**现象**：curl 报 `sslv3 alert handshake failure`。

**原因**：本地开了 Cloudflare WARP 或 VPN，DNS 解析到 `198.18.x.x`，SSL 握手被拦截。

**解决**：

- 临时关掉 WARP/VPN 再测
- 或绕过 DNS：

```bash
curl --resolve "<your-domain>:443:162.159.137.232" --noproxy '*' https://<your-url>/v1/models
```

- 或用 `wrangler tail` 验证 Worker 是否收到请求（不依赖本地 SSL）：

```bash
wrangler tail <worker-name> --format=json &
curl -H "Authorization: Bearer <TOKEN>" https://<your-url>/v1/models
```

## `workers.dev subdomain is unavailable`

**原因**：该名字已被其他 Cloudflare 用户占用。

**解决**：换一个名字重试。

## `Account already has an associated subdomain`

**原因**：一个账号只能绑一个 workers.dev 子域名。

**解决**：先 DELETE 再 PUT 新名字：

```bash
curl -X DELETE "https://api.cloudflare.com/client/v4/accounts/$ACCOUNT_ID/workers/subdomain" \
  -H "Authorization: Bearer $TOKEN"
```

> 删除子域名会让已部署的 Worker URL 失效，需要重新部署。

## `You need to register a workers.dev subdomain`

**现象**：`wrangler deploy` 报此错。

**原因**：账号还没注册 workers.dev 子域名。

**解决**：先注册（见 [deployment.md](deployment.md) 第 4 步），再部署。
