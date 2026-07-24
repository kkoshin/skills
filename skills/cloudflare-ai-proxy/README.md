# cloudflare-ai-proxy

一个 [Claude Code Skill](https://docs.claude.com/en/docs/claude-code/skills)，用于在 Cloudflare Workers 上部署 AI API 请求中转代理。

## 这个 skill 做什么

通过这个 skill，你可以在几分钟内部署一个 Cloudflare Worker，作为 AI API 请求的中转代理：

- **统一端点**：一个 URL 转发到多个上游 AI 提供商（多个上游渠道）
- **暗号鉴权**：自定义 Secret Token，防止未授权调用
- **多渠道路由**：支持 `"model:provider"` 格式显式选择渠道，或走默认兜底
- **模型别名**：如 `Model-A` 自动映射到 `model-a`，body 改写后再转发
- **流式响应**：透传 SSE / streaming
- **CORS 支持**：可从浏览器端调用
- **模型列表**：`/v1/models` 兼容 OpenAI / Anthropic 格式
- **健康检查**：`/health` 端点

## 快速开始

### 1. 安装 skill

把本目录放到 Claude Code 的 skills 路径下（或软链）：

```bash
ln -s /path/to/cloudflare-ai-proxy ~/.claude/skills/cloudflare-ai-proxy
```

### 2. 在 Claude Code 里触发

告诉 Claude Code：

> 帮我部署一个 Cloudflare Worker 作为 AI API 中转代理，路由到多个上游提供商

Claude Code 会自动加载本 skill 并按工作流引导你完成部署。

### 3. 前置条件

- 已安装 [wrangler](https://developers.cloudflare.com/workers/wrangler/) 并登录（`wrangler login`）
- 有 Cloudflare 账号

## 目录结构

```
cloudflare-ai-proxy/
├── SKILL.md              # skill 入口
├── README.md             # 本文件
├── templates/            # 可部署的模板
│   ├── worker-proxy.js   # 代理脚本（占位符版）
│   └── wrangler.jsonc    # wrangler 配置
├── references/           # 深度参考文档
│   ├── configuration.md
│   ├── deployment.md
│   ├── client-integration.md
│   └── troubleshooting.md
└── scripts/              # 辅助脚本
    ├── register-subdomain.sh
    ├── deploy.sh
    └── pull-remote.sh
```

## 文档

- [配置详解](references/configuration.md)
- [部署流程](references/deployment.md)
- [客户端对接](references/client-integration.md)
- [问题排查](references/troubleshooting.md)

## License

MIT