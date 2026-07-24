# 客户端对接

代理支持两种 API 格式，路径不同：

| 格式 | 路径 | 适用客户端 |
|------|------|-----------|
| Anthropic | `/v1/messages` | Claude Code、cc-switch、Claude 桌面客户端 |
| OpenAI | `/v1/chat/completions` | ChatBox、Cherry Studio、OpenWebUI 等 |

> **重要**：不要把请求发到根路径 `/`，否则代理会透传到上游根路径，返回上游官网首页。必须用完整 API 路径。

## Claude Code

通过环境变量指向代理：

```bash
export ANTHROPIC_BASE_URL="https://<worker-name>.<subdomain>.workers.dev"
export ANTHROPIC_API_KEY="your-secret-token"  # 即 SECRET_TOKEN
```

然后正常使用 `claude` 命令。模型名在 Claude Code 的 model 设置里选代理支持的（如 `model-a`）。

> 如果 Claude Code 传的 model 名是它默认的 Claude 模型名（不在你的 MODELS 里），需要在代理里加对应别名或兜底。

## cc-switch

[cc-switch](https://github.com/farion1231/cc-switch) 是切换 Claude Code/Claude Desktop 配置的工具。在 cc-switch 里新增一个配置：

| 配置项 | 值 |
|--------|-----|
| API Base URL | `https://<worker-name>.<subdomain>.workers.dev` |
| API Key | `your-secret-token` |
| Model | 代理支持的模型名，如 `model-a` |

切换到该配置后，Claude Desktop / Claude Code 即可通过代理调用。

## 其他桌面客户端

### Cherry Studio / ChatBox / LibreChat / OpenWebUI

这些客户端支持自定义 OpenAI 或 Anthropic 兼容端点：

1. 新增自定义 provider / 自定义 API
2. API 地址填 `https://<worker-name>.<subdomain>.workers.dev`
3. API Key 填 `your-secret-token`
4. 模型名填代理支持的（从 `/v1/models` 获取）

### 浏览器端调用

代理已开启 CORS（`Access-Control-Allow-Origin: *`），可直接从浏览器 fetch：

```js
fetch("https://<your-url>/v1/chat/completions", {
  method: "POST",
  headers: {
    "Authorization": "Bearer your-secret-token",
    "Content-Type": "application/json",
  },
  body: JSON.stringify({
    model: "model-a",
    messages: [{ role: "user", content: "hi" }],
  }),
});
```

## curl 测试示例

### Anthropic 格式

```bash
curl -X POST https://<your-url>/v1/messages \
  -H "Authorization: Bearer your-secret-token" \
  -H "Content-Type: application/json" \
  -H "anthropic-version: 2023-06-01" \
  -d '{
    "model": "model-a",
    "max_tokens": 1024,
    "messages": [{"role": "user", "content": "hi"}]
  }'
```

### OpenAI 格式

```bash
curl -X POST https://<your-url>/v1/chat/completions \
  -H "Authorization: Bearer your-secret-token" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "model-b",
    "messages": [{"role": "user", "content": "hi"}]
  }'
```

### 显式指定渠道

在 model 名后加 `:provider`：

```bash
-d '{"model": "model-a:provider-b", ...}'
```