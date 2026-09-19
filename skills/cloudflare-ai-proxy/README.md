# cloudflare-ai-proxy

一个 [Claude Code Skill](https://docs.claude.com/en/docs/claude-code/skills)，用于在 Cloudflare Workers 上部署 AI API 请求中转代理。

## 这个 skill 做什么

通过这个 skill，你可以在几分钟内部署一个 Cloudflare Worker，作为 AI API 请求的中转代理：

- **统一端点**：一个 Anthropic Messages 端点（`POST /v1/messages`）转发到多个上游渠道
- **暗号鉴权**：自定义 Secret Token，防止未授权调用
- **多渠道路由**：支持 `"model:provider"` 显式选择渠道，或走默认兜底
- **协议转换**：上游是 OpenAI 协议时，双向转换请求、响应与 SSE 流 —— 于是 Claude Code 也能用上只讲 OpenAI 的模型
- **模型别名**：把 `claude-sonnet-4-5` 这类客户端模型名映射到你的模型，支持 `*` 通配符
- **流式响应**：Anthropic 上游原样透传；OpenAI 上游做 SSE 翻译（含心跳、工具调用缓冲、错误事件）
- **配置即文件**：全部配置在 `wrangler.toml` 的 `[vars]`，改配置不需要碰代码
- **模型列表**：`/v1/models` 返回体同时含 OpenAI 与 Anthropic 字段
- **CORS 支持**：可从浏览器端调用

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
- 本地有 Node.js（跑本地验证与回归测试）

## 目录结构

```
cloudflare-ai-proxy/
├── SKILL.md                # skill 入口
├── README.md               # 本文件
├── templates/              # 复制到用户项目里的模板
│   ├── src/                # Worker 源码（8 个模块）
│   │   ├── index.js        # 入口：认证、路由、错误编排
│   │   ├── config.js       # [vars] 解析校验、别名与路由索引
│   │   ├── protocol.js     # Anthropic ↔ OpenAI 转换
│   │   ├── streaming.js    # SSE 双向处理
│   │   ├── proxy.js        # 上游请求构造与分派
│   │   ├── http.js         # 认证、CORS、错误信封
│   │   ├── models.js       # /v1/models
│   │   └── json-scan.js    # 请求体 model 字段的字节级替换（CPU 优化）
│   ├── test/               # 协议转换回归测试
│   ├── wrangler.toml       # ← 全部配置的唯一来源
│   ├── package.json
│   └── .gitignore
├── references/             # 深度参考文档
│   ├── configuration.md
│   ├── deployment.md
│   ├── client-integration.md
│   └── troubleshooting.md
└── scripts/                # 辅助脚本
    ├── register-subdomain.sh
    └── deploy.sh
```

## 文档

- [配置详解](references/configuration.md)
- [部署流程](references/deployment.md)
- [客户端对接](references/client-integration.md)
- [问题排查](references/troubleshooting.md)

## License

MIT
