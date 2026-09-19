# 客户端对接

这个代理**只提供 Anthropic 格式的下游接口**：

| 路径 | 说明 |
|---|---|
| `POST /v1/messages` | 唯一可用的推理接口（`/messages` 同义，尾斜杠会被归一化） |
| `GET /v1/models` | 模型列表 |
| `GET /health` | 健康检查 |

`POST /v1/chat/completions` 返回 **404**。OpenAI 兼容只用于**上游**（当某个渠道配了 `protocol = "openai"` 时的协议转换），不是对外的接口。所以只能对接支持自定义 Anthropic 端点的客户端。

所有请求都要带凭证：`Authorization: Bearer <SECRET_TOKEN>`、裸 `Authorization: <token>`、或 `x-api-key: <token>` 三种都认。

## Claude Code

用环境变量指向代理：

```bash
export ANTHROPIC_BASE_URL="https://<worker-name>.<subdomain>.workers.dev"
export ANTHROPIC_AUTH_TOKEN="<你的 SECRET_TOKEN>"
```

这两个变量在 Claude Code 里的区别：

| 变量 | Claude Code 发出的头 | 代理是否接受 |
|---|---|---|
| `ANTHROPIC_AUTH_TOKEN` | `Authorization: Bearer <token>` | ✅ |
| `ANTHROPIC_API_KEY` | `x-api-key: <token>` | ✅ |

**两个都能用**，值都填 `SECRET_TOKEN`。本文档统一用 `ANTHROPIC_AUTH_TOKEN`。

### 模型名怎么办

Claude Code 默认会发 `claude-sonnet-4-5` 这类官方模型名，而你的 `MODELS` 里显然没有它们。**模板没有内置别名表**，所以必须二选一：

**方案 A：在 `[vars.MODEL_ALIASES]` 里配别名**（推荐，客户端无感）

```toml
[vars.MODEL_ALIASES]
"claude-*" = "你的模型名"
```

这样 Claude Code 不需要任何额外设置，在 Worker 侧就把名字换掉了。

**方案 B：显式指定模型**

```bash
export ANTHROPIC_MODEL="你的模型名"
```

不配置的话，未知的 Claude 模型名会返回 **400**（不会静默路由到别处）。

> 客户端附加的上下文窗口后缀（如 `[1m]`）会在路由前自动剥掉，不需要特殊处理。

### count_tokens 返回 404 是正常的

Claude Code 会调用 `/v1/messages/count_tokens`。本代理返回 404 并说明"请用客户端估算"，客户端会自动回退到本地估算。这是预期行为，不是故障。

## cc-switch

[cc-switch](https://github.com/farion1231/cc-switch) 是切换 Claude Code 配置的工具。新增一个配置：

| 配置项 | 值 |
|---|---|
| API Base URL | `https://<worker-name>.<subdomain>.workers.dev` |
| API Key / Token | `<你的 SECRET_TOKEN>` |
| Model | 你的模型名（若已在 `MODEL_ALIASES` 里配了 `claude-*` 映射，可留空走默认） |

## Anthropic SDK

任何语言的官方 Anthropic SDK 都可以直接指向代理，因为 SDK 默认用 `x-api-key` 头，代理认：

```python
from anthropic import Anthropic

client = Anthropic(
    base_url="https://<worker-name>.<subdomain>.workers.dev",
    api_key="<你的 SECRET_TOKEN>",
)
```

## 其他客户端

代理只讲 Anthropic 协议，所以：

- **支持自定义 Anthropic 端点的客户端**（Cline、Continue 等）→ 直接把 Base URL 和 key 填成代理的即可。具体选项位置以各客户端自己的文档为准。
- **只支持 OpenAI 兼容端点的客户端** → **接不了**。典型的是 **OpenWebUI**：它的 Connections 只接受 OpenAI 格式端点，没有 Anthropic 选项。要在这种情况下使用，得在客户端和本代理之间再加一层 OpenAI→Anthropic 的转换（如 LiteLLM），或者换一个支持 Anthropic 的客户端。
- **Cherry Studio / ChatBox / LobeChat 一类桌面客户端** → 若其"Claude / Anthropic"提供商允许自定义 API 地址，就能直连；请在客户端里选 Anthropic 模式而不是 OpenAI 模式。各版本支持情况不同，以客户端自己的文档为准。
- **Claude 桌面客户端（Claude Desktop）** → 不支持自定义 Base URL，**不能**对接本代理。

## curl 测试示例

```bash
URL="https://<worker-name>.<subdomain>.workers.dev"
TOKEN="<你的 SECRET_TOKEN>"

# 健康检查
curl -s -H "Authorization: Bearer $TOKEN" $URL/health

# 模型列表
curl -s -H "Authorization: Bearer $TOKEN" $URL/v1/models

# 对话（裸模型名 → 走 DEFAULT_FALLBACK）
curl -s -X POST $URL/v1/messages \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -H "anthropic-version: 2023-06-01" \
  -d '{"model":"<模型名>","max_tokens":64,"messages":[{"role":"user","content":"hi"}]}'

# 显式指定渠道
curl -s -X POST $URL/v1/messages \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"model":"<模型名>:<渠道名>","max_tokens":64,"messages":[{"role":"user","content":"hi"}]}'

# 流式（-N 关闭 curl 缓冲，才能看到逐块输出）
curl -N -X POST $URL/v1/messages \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"model":"<模型名>","max_tokens":64,"stream":true,"messages":[{"role":"user","content":"hi"}]}'
```

## 浏览器端调用

代理已开启 CORS（`Access-Control-Allow-Origin: *`，允许的头为 `Content-Type, Authorization, x-api-key, anthropic-version, anthropic-beta`）：

```js
fetch("https://<worker-name>.<subdomain>.workers.dev/v1/messages", {
  method: "POST",
  headers: {
    "Authorization": "Bearer <你的 SECRET_TOKEN>",
    "Content-Type": "application/json",
    "anthropic-version": "2023-06-01",
  },
  body: JSON.stringify({
    model: "<模型名>",
    max_tokens: 64,
    messages: [{ role: "user", content: "hi" }],
  }),
});
```

> 浏览器里放暗号等于公开它 —— 任何访客都能从源码里读到。仅限自己本地调试使用。
