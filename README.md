# skills

[![skills.sh](https://skills.sh/b/kkoshin/skills)](https://skills.sh/kkoshin/skills)

一组 Claude Code [Skills](https://docs.claude.com/en/docs/claude-code/skills)（技能包）。每个 skill 独立存放在 `skills/<name>/` 下，按需被 Claude Code 加载使用。

## 包含的 Skills

| Skill | 说明 |
|-------|------|
| [cloudflare-ai-proxy](./skills/cloudflare-ai-proxy) | 部署一个 Cloudflare Worker 作为 AI API 请求中转代理：一个 Anthropic Messages 端点统一转发到多个上游 AI 提供商，支持暗号鉴权、按 `model` / `model:provider` 路由、模型别名，以及上游为 OpenAI 协议时的双向协议转换（含 SSE 流）。全部配置在 `wrangler.toml` 的 `[vars]`。 |

## Skill 结构

```
skills/<skill-name>/
├── SKILL.md            # 入口：frontmatter(name+description) + 工作流
├── references/*.md     # 进阶文档（配置、部署、客户端对接、排错）
├── scripts/*.sh        # 可独立运行的辅助脚本
└── templates/*         # 复制到用户项目中的模板文件（占位符，非真实密钥）
```

- `SKILL.md` 的 `description` 决定 Claude 何时加载该 skill，所以描述要具体、触发词要全。
- 正文用编号步骤组织，细节拆到 `references/` 里，保持 `SKILL.md` 可扫读。

## 安装

推荐用 [skills](https://github.com/vercel-labs/skills) CLI（无需全局安装，直接 `npx`）：

```bash
npx skills add kkoshin/skills
```

常用选项：

```bash
# 只装某个 skill
npx skills add kkoshin/skills --skill cloudflare-ai-proxy

# 指定目标 agent（默认会询问）
npx skills add kkoshin/skills -a claude-code

# 非交互式 / CI 友好（全局安装 + 跳过确认）
npx skills add kkoshin/skills --skill cloudflare-ai-proxy -g -a claude-code -y
```

也支持完整 URL 或本地路径：`npx skills add https://github.com/kkoshin/skills`、`npx skills add ./skills`。

## 使用方式

装好后，Claude 会根据你的请求自动匹配并加载对应 skill。

例如想搭一个 AI API 中转代理，直接告诉 Claude「帮我在 Cloudflare Workers 上搭一个 AI API 中转代理」，它会加载 `cloudflare-ai-proxy` 并按 `SKILL.md` 的步骤执行。

> 手动安装：把仓库放到 Claude Code 能识别的 skills 路径下（项目级 `.claude/skills/` 或用户级 `~/.claude/skills/`），或在对话里直接引用某个 skill 的路径。

## 新增 Skill

1. 在 `skills/` 下新建目录，命名用 kebab-case。
2. 写 `SKILL.md`，frontmatter 必填 `name` 和 `description`。
3. 按需补充 `references/`、`scripts/`、`templates/`。
4. 模板里一律用占位符，不要写入真实密钥、子域名或 Worker 名。
