---
name: cloudflare-ai-proxy
description: Deploy a Cloudflare Worker as an AI API request proxy that routes to multiple upstream providers with secret-token auth, model aliases, streaming, and OpenAI/Anthropic-compatible model list. Use when the user wants to set up an AI API relay/forwarder on Cloudflare Workers, proxy requests to multiple upstream AI providers, or deploy a multi-model gateway. Covers wrangler setup, workers.dev subdomain registration, configuration, deployment, and client integration (Claude Code, cc-switch).
---

# Cloudflare AI Proxy

部署一个 Cloudflare Worker 作为 AI API 请求中转代理：一个端点统一转发到多个上游 AI 提供商，支持暗号鉴权、模型别名、流式响应、OpenAI/Anthropic 兼容的模型列表。

## When to use

- 用户想在 Cloudflare Workers 上搭一个 AI API 中转/转发代理
- 用户想让一个端点路由到多个上游 AI 提供商（多个上游渠道）
- 用户想给 AI API 加一层暗号鉴权 + 多渠道兜底
- 用户想对接 Claude Code / cc-switch / 桌面客户端到自定义 AI 端点

## Prerequisites

- 已安装 wrangler 并登录（`wrangler login`，验证 `wrangler whoami`）
- 有 Cloudflare 账号
- 本地有 curl（用于验证）

## Workflow

### Step 1: 初始化项目

在工作目录下创建项目，复制模板：

```bash
mkdir my-ai-proxy && cd my-ai-proxy
cp <skill-dir>/templates/worker-proxy.js .
cp <skill-dir>/templates/wrangler.jsonc .
```

按需修改 `wrangler.jsonc` 里的 `name`（即 Worker 名，会出现在最终 URL 里）。

### Step 2: 配置代理

编辑 `worker-proxy.js` 的配置区，填入自己的值：

- `SECRET_TOKEN` — 自定义暗号
- `PROVIDERS` — 上游渠道的 baseUrl
- `MODELS` — 每个模型可用的渠道 + API Key
- `DEFAULT_FALLBACK` — 兜底渠道 + 别名映射

详见 [references/configuration.md](references/configuration.md)。

### Step 3: 注册 workers.dev 子域名

首次部署需要一个 `workers.dev` 子域名（账号级，只需注册一次）：

```bash
bash <skill-dir>/scripts/register-subdomain.sh <your-subdomain>
```

详见 [references/deployment.md](references/deployment.md)。

### Step 4: 部署

```bash
bash <skill-dir>/scripts/deploy.sh
# 或直接 wrangler deploy
```

部署成功后会输出 `https://<worker-name>.<subdomain>.workers.dev`。

### Step 5: 验证

```bash
# 健康检查
curl -H "Authorization: Bearer <your-secret-token>" https://<your-url>/health

# 模型列表
curl -H "Authorization: Bearer <your-secret-token>" https://<your-url>/v1/models

# 对话请求（Anthropic 格式）
curl -X POST https://<your-url>/v1/messages \
  -H "Authorization: Bearer <your-secret-token>" \
  -H "Content-Type: application/json" \
  -H "anthropic-version: 2023-06-01" \
  -d '{"model":"<model-name>","max_tokens":1024,"messages":[{"role":"user","content":"hi"}]}'
```

> 注意：请求必须发到 `/v1/messages`（Anthropic）或 `/v1/chat/completions`（OpenAI），**不要发到根路径 `/`**，否则会返回上游首页。

### Step 6: 对接客户端

详见 [references/client-integration.md](references/client-integration.md)。

- **Claude Code**：设置 `ANTHROPIC_BASE_URL` + `ANTHROPIC_API_KEY`
- **cc-switch**：配置 base URL + key + model
- **其他桌面客户端**：Cherry Studio / ChatBox 等

## Key concepts

- **路由格式**：`model` 字段支持 `"model:provider"`（显式指定渠道）或 `"model"`（走 DEFAULT_FALLBACK 兜底）
- **别名机制**：`DEFAULT_FALLBACK` 里 key 不在 MODELS 中即为别名（如 `Model-A` → `model-a:provider-a`），代理会改写 body 里的 model 为规范名再转发
- **大小写不敏感**：model 名统一转小写匹配，但发给上游时用规范名
- **双格式兼容**：`/v1/models` 返回体同时含 OpenAI 和 Anthropic 字段，两端客户端都能解析

## Reference docs

- [configuration.md](references/configuration.md) — 配置详解：PROVIDERS / MODELS / DEFAULT_FALLBACK / 别名
- [deployment.md](references/deployment.md) — 部署流程：wrangler、子域名注册、部署命令
- [client-integration.md](references/client-integration.md) — 客户端对接：Claude Code、cc-switch、桌面客户端
- [troubleshooting.md](references/troubleshooting.md) — 常见问题排查

## Helper scripts

- [scripts/register-subdomain.sh](scripts/register-subdomain.sh) — 注册 workers.dev 子域名
- [scripts/deploy.sh](scripts/deploy.sh) — 部署 Worker
- [scripts/pull-remote.sh](scripts/pull-remote.sh) — 从已部署 Worker 拉取最新代码

## Do not

- 不要在模板里填真实 API Key 后分享，始终用占位符
- 不要把请求发到根路径 `/`，必须用 `/v1/messages` 或 `/v1/chat/completions`
- 不要在 skill 里硬编码用户的子域名或 Worker 名