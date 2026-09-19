---
name: cloudflare-ai-proxy
description: Deploy a Cloudflare Worker that puts multiple upstream AI providers behind one Anthropic Messages endpoint, routing by model name or model:provider and converting Anthropic requests plus SSE streams both ways when an upstream speaks OpenAI Chat Completions. Use when the user wants to set up an AI API relay, gateway or forwarder on Cloudflare Workers; unify several AI providers (Anthropic-compatible, OpenAI-compatible, DeepSeek-style) behind a single URL; point Claude Code, cc-switch or an Anthropic SDK at a custom base URL; or configure a Worker through wrangler.toml [vars] (PROVIDERS, MODELS, DEFAULT_FALLBACK, MODEL_ALIASES) with secret-token auth. Covers the multi-module worker template, wrangler setup, workers.dev subdomain registration, deployment (wrangler CLI and GitHub auto-deploy), configuration, client integration and troubleshooting.
---

# Cloudflare AI Proxy

部署一个 Cloudflare Worker 作为 AI API 中转代理：一个 Anthropic Messages 端点统一转发到多个上游渠道，支持暗号鉴权、按 `model` / `model:provider` 路由、模型别名，以及在上游是 OpenAI 协议时做双向协议转换（含 SSE 流）。

## When to use

- 用户想在 Cloudflare Workers 上搭一个 AI API 中转/转发代理
- 用户想让一个端点路由到多个上游 AI 提供商（多个上游渠道）
- 用户想把 Claude Code / cc-switch / Anthropic SDK 接到自定义端点
- 用户想把 OpenAI 协议的上游伪装成 Anthropic 端点给 Claude Code 用

## Prerequisites

- 已安装 wrangler 并登录（`wrangler login`，用 `wrangler whoami` 验证）
- 有 Cloudflare 账号
- 本地有 curl 和 Node.js（跑本地验证与回归测试）

## Workflow

### Step 1: 初始化项目

```bash
mkdir my-ai-proxy && cd my-ai-proxy
cp -R <skill-dir>/templates/. .
```

会得到 `src/`（8 个模块）、`test/`、`wrangler.toml`、`package.json`、`.gitignore`。改 `wrangler.toml` 里的 `name`（Worker 名，会出现在最终 URL 里）。

### Step 2: 配置 `[vars]`

`wrangler.toml` 的 `[vars]` 是**全部配置的唯一来源**：

- `SECRET_TOKEN` — 自定义暗号（不是上游 API Key）
- `PROVIDERS` — 上游渠道（`baseUrl` / `protocol` / `authType` / `key_ref`）
- `MODELS` — 每个模型可用的渠道（`provider_id` / `token_ref`）
- `DEFAULT_FALLBACK` — 裸模型名的默认渠道
- `MODEL_ALIASES` — 把客户端模型名映射到你的模型

详见 [references/configuration.md](references/configuration.md)。

### Step 3: 本地验证（别跳过）

配置错误**不会**让部署失败，而是到请求到达时才以 500 暴露。先本地跑一次：

```bash
wrangler dev
```

```bash
curl -s -H "Authorization: Bearer <SECRET_TOKEN>" http://127.0.0.1:8787/v1/models
curl -s -X POST http://127.0.0.1:8787/v1/messages \
  -H "Authorization: Bearer <SECRET_TOKEN>" -H "Content-Type: application/json" \
  -d '{"model":"<模型名>","max_tokens":16,"messages":[{"role":"user","content":"hi"}]}'
```

**判读**：**502 是好事** —— 说明认证、配置、路由、URL 拼接全通过，只是本地到不了上游。**500** = 配置不自洽，**400** = 路由或别名有问题。

### Step 4: 注册 workers.dev 子域名

账号级操作，只需一次：

```bash
bash <skill-dir>/scripts/register-subdomain.sh <your-subdomain>
```

详见 [references/deployment.md](references/deployment.md)。

### Step 5: 部署

```bash
bash <skill-dir>/scripts/deploy.sh
# 或 wrangler deploy
```

想要 push 即自动部署，见 deployment.md 的"GitHub 集成自动部署"一节。

### Step 6: 验证

```bash
URL="https://<worker-name>.<subdomain>.workers.dev"
TOKEN="<SECRET_TOKEN>"

curl -s -H "Authorization: Bearer $TOKEN" $URL/health
curl -s -H "Authorization: Bearer $TOKEN" $URL/v1/models
curl -s -X POST $URL/v1/messages -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"model":"<模型名>","max_tokens":64,"messages":[{"role":"user","content":"hi"}]}'
```

注意 `/health` 和 `/v1/models` **同样需要**带暗号。

### Step 7: 对接客户端

详见 [references/client-integration.md](references/client-integration.md)。

- **Claude Code**：`ANTHROPIC_BASE_URL` + `ANTHROPIC_AUTH_TOKEN`
- **cc-switch**：配置 base URL + key + model
- **Anthropic SDK**：`base_url` + `api_key`（SDK 发 `x-api-key`，代理认）

## Key concepts

- **路由格式**：`"model:provider"` 显式指定渠道；裸 `"model"` 走 `DEFAULT_FALLBACK`。数组顺序**不会**触发自动故障转移
- **协议转换**：渠道配 `protocol = "openai"` 时，Worker 把 Anthropic 请求转成 OpenAI Chat Completions 发出，并把响应与 SSE 流转回 Anthropic 格式。`protocol = "anthropic"`（默认）则原样透传
- **别名机制**：`MODEL_ALIASES` 把客户端模型名映射到你的模型，键支持一个 `*` 通配符，**精确优先于通配、更具体者优先**。别名不会出现在 `/v1/models` 里
- **模型名统一转小写**后发给上游，因此上游模型名大小写敏感时无法原样表达
- **配置唯一来源**：`wrangler.toml` 的 `[vars]`；改完必须重新部署
- **双格式模型列表**：`/v1/models` 返回体同时含 OpenAI 与 Anthropic 字段，两端客户端都能解析

## Reference docs

- [configuration.md](references/configuration.md) — 配置详解：`[vars]` 全字段、别名优先级、密钥管理、错误报文
- [deployment.md](references/deployment.md) — 部署流程：wrangler、本地验证、子域名注册、GitHub 自动部署、自定义域名
- [client-integration.md](references/client-integration.md) — 客户端对接：Claude Code、cc-switch、Anthropic SDK
- [troubleshooting.md](references/troubleshooting.md) — 常见问题排查

## Helper scripts

- [scripts/register-subdomain.sh](scripts/register-subdomain.sh) — 注册 workers.dev 子域名
- [scripts/deploy.sh](scripts/deploy.sh) — 部署 Worker

## Do not

- 不要在模板里填真实 API Key 后分享，始终用占位符
- 不要向本代理发 OpenAI 格式请求 —— `/v1/chat/completions` 返回 404，OpenAI 仅作为**上游**协议
- 不要在 skill 里硬编码用户的子域名或 Worker 名
