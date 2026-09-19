# 配置详解

全部配置都在 **`wrangler.toml` 的 `[vars]`** 里。改配置 = 改这个文件 + 重新部署；代码里没有任何需要改的常量。

## 0. 配置是怎么到 Worker 里的

`wrangler.toml` 里用 TOML 子表写的四个配置，部署时会被 wrangler 上传为 **JSON 类型变量**。运行时 `env.PROVIDERS` 等已经是解析好的对象（Worker 也兼容字符串形式，两种都认）。

三个必须记住的写法规则：

| 规则 | 说明 |
|---|---|
| 裸键写在子表**之前** | `[vars]` 下的 `SECRET_TOKEN = "..."` 这类裸键，必须放在所有 `[vars.*]` 子表前面。TOML 里一旦打开子表，后面的裸键会被解析进那个子表里 |
| 带 `.` 的键要加引号 | `[[vars.MODELS."example-3.5"]]`。不加引号 TOML 会把点解析成嵌套表 |
| 改完要重新部署 | `wrangler deploy`（或用 GitHub 集成则 push）。`[vars]` 的变更不会自动生效 |

## 1. `SECRET_TOKEN` — 访问口令

客户端调用时带的暗号。**不是你上游的 API Key**，是你自己定的一个口令。

```toml
[vars]
SECRET_TOKEN = "change-me-secret-token"
```

校验是**精确相等**（不是子串包含），且支持三种头部写法：

```bash
Authorization: Bearer <SECRET_TOKEN>   # 推荐
Authorization: <SECRET_TOKEN>          # 裸 token 也可以
x-api-key: <SECRET_TOKEN>              # Anthropic SDK 默认用这个
```

不匹配返回 **403** `authentication_error`。`SECRET_TOKEN` 没配置返回 **500**。

> `GET /health` 和 `GET /v1/models` **同样需要**带暗号 —— 校验发生在路由之前。只有 `OPTIONS` 预检免校验。

## 2. `PROVIDERS` — 上游渠道

```toml
[vars.PROVIDERS.provider-a]
baseUrl = "https://api.provider-a.example.com/anthropic"

[vars.PROVIDERS.provider-b]
baseUrl = "https://api.provider-b.example.com/v1"
protocol = "openai"
authType = "api-key"
key_ref = "PROVIDER_B_KEY"
```

| 字段 | 必填 | 说明 |
|---|---|---|
| `baseUrl` | 是 | 上游根地址。必须是绝对 http(s) URL，且**不能带 query 或 hash**。转发时把请求路径拼在后面；如果它本身以 `/v1` 结尾而请求路径也是 `/v1/...`，不会重复拼接 |
| `protocol` | 否 | `"anthropic"`（默认）或 `"openai"`。选 `openai` 时 Worker 会把 Anthropic 请求转成 OpenAI Chat Completions 发出，并把响应/SSE 流转回 Anthropic 格式 |
| `authType` | 否 | `"bearer"`（默认，发 `Authorization: Bearer <key>`）或 `"api-key"`（发 `x-api-key: <key>`） |
| `key_ref` | 否 | 该渠道默认密钥所在的**变量名**（不是密钥本身）。见 [§6 密钥管理](#6-密钥管理) |

## 3. `MODELS` — 模型路由表

```toml
[vars.MODELS]

[[vars.MODELS.model-a]]
provider_id = "provider-a"
token_ref = "PROVIDER_A_KEY"

[[vars.MODELS.model-a]]
provider_id = "provider-b"
```

每个模型一段，每段挂一个可用渠道。字段：

| 字段 | 必填 | 说明 |
|---|---|---|
| `provider_id` | 是 | 必须是 `PROVIDERS` 里存在的渠道（大小写不敏感）。同一模型下不允许重复 |
| `token_ref` | 否 | 该条目的密钥变量名。**缺省时回退**到该渠道的 `key_ref` |
| `protocol` | 否 | 覆盖渠道级的 `protocol`。例如某渠道整体是 OpenAI，但某个模型原生支持 Anthropic，就在这里单独写 `protocol = "anthropic"` |

三个容易踩的点：

- **数组顺序不等于故障转移**。同一个模型挂多个渠道只是"这些渠道都可用"，具体走哪个只由 `model:provider`、别名、或 `DEFAULT_FALLBACK` 决定。上游挂了不会自动切到下一个。
- **发给上游的模型名是 `MODELS` 的键（小写）**。客户端发 `Model-A` 或 `model-a:provider-b`，转发出去的都是 `model-a`。
- **由此带来一个限制**：模型名会被统一转成小写，所以上游模型名**大小写敏感**时（如 `GLM-4.6`、`Llama-3.3-70B`）无法原样表达 —— `MODELS` 的键会被小写化后发出。遇到这种情况只能在中间加一层转换。

## 4. `DEFAULT_FALLBACK` — 裸模型名的默认渠道

```toml
[vars.DEFAULT_FALLBACK]
model-a = "model-a:provider-a"
"example-3.5" = "example-3.5:provider-b"
```

客户端只传模型名（不带 `:provider`）时走这里。格式固定为 `"模型:渠道"`。

- 目标模型必须存在于 `MODELS`，且该渠道必须真的挂在那个模型上，否则加载配置时报 500
- 键应当写成 `MODELS` 里的模型名。写成一个不在 `MODELS` 里的名字**不会生效**（除非同时配了同名别名）
- 不配置 = 客户端用裸模型名请求时返回 **400**

## 5. `MODEL_ALIASES` — 模型别名

把客户端发来的模型名映射到 `MODELS` 里的模型。主要用途是让 Claude Code 之类只会发官方模型名（`claude-sonnet-4-5`、`claude-opus-4-1`…）的客户端，接到你自己的模型上。

```toml
[vars.MODEL_ALIASES]
sonnet = "model-a"
"claude-sonnet-*" = "model-a"
haiku = "model-a:provider-b"
```

- 值可以是 `"model"`（渠道走 `DEFAULT_FALLBACK`）或 `"model:provider"`（同时**固定渠道**）
- 键支持**一个** `*` 通配符；写两个会报错
- 目标模型必须存在于 `MODELS`（带 `:provider` 时该渠道也必须可用），否则加载配置时报 500
- **不配置这一节 = 没有别名**。模板没有内置别名表，所以不配就会让 Claude Code 的默认模型名返回 400

### 匹配优先级

1. **精确匹配永远优先于通配**
2. 通配之间，**去掉 `*` 之后更长的模式优先**（即更具体者优先）
3. 长度相同时，按 `MODEL_ALIASES` 里的**声明顺序**取先出现的
4. 匹配前会把模型名转小写，并剥掉尾部的上下文窗口后缀（`claude-sonnet-4-5[1m]` → `claude-sonnet-4-5`）

## 6. 密钥管理

`token_ref` / `key_ref` 填的都是**变量名**，不是密钥本身。这样密钥只在一个地方出现。

```toml
[vars]
PROVIDER_A_KEY = "sk-your-real-key"     # ← 明文，不推荐
```

**推荐改用 Secrets**，密钥不会进 git：

```bash
wrangler secret put PROVIDER_A_KEY
```

生产部署前请确认：这个文件会进 git，明文密钥会永久留在提交历史里。

> **引用了但没配置的密钥 → 500**，错误信息只包含**变量名**，不会泄露值。这是有意设计。

## 7. 常见改法

### 新增一个渠道

```toml
# 1. 声明密钥变量（写在所有子表之前）
[vars]
NEWPROVIDER_API_KEY = "sk-..."

# 2. 加渠道
[vars.PROVIDERS.newprovider]
baseUrl = "https://api.newprovider.example.com"

# 3. 给每个支持的模型加一段
[[vars.MODELS.model-a]]
provider_id = "newprovider"
token_ref = "NEWPROVIDER_API_KEY"

# 4. 想让它成为默认渠道就改这里
[vars.DEFAULT_FALLBACK]
model-a = "model-a:newprovider"
```

### 接 Claude Code 的模型名

```toml
[vars.MODEL_ALIASES]
"claude-*" = "model-a"
```

一条通配符就能兜住所有 `claude-*` 名字。要区分对待就写多条更具体的：

```toml
sonnet = "model-a"
haiku  = "model-b"
"claude-*" = "model-a"    # 剩下的都进 model-a
```

### 加一个 OpenAI 协议的渠道

```toml
[vars.PROVIDERS.someopenai]
baseUrl = "https://api.someopenai.example.com/v1"
protocol = "openai"          # 关键：开启双向协议转换
authType = "api-key"
key_ref = "SOMEOPENAI_KEY"
```

### 把某个模型固定到某个渠道

直接改 `DEFAULT_FALLBACK`：

```toml
[vars.DEFAULT_FALLBACK]
model-a = "model-a:provider-b"
```

## 8. OpenAI 上游的行为常量

给 `protocol = "openai"` 渠道用的能力开关，住在 **`src/protocol.js` 的 `OPENAI_CAPABILITIES`**。模板有意不把它做成配置项 —— 绝大多数部署用默认值就够。

默认值（保守档）：

| 开关 | 默认 | 作用 |
|---|---|---|
| `tools` / `parallelToolCalls` / `toolChoice` | `true` | 是否转换工具相关字段 |
| `assistantToolContentRequired` | `true` | assistant 消息只有 `tool_use`、没有文本时，是否补空字符串 `content` |
| `reasoning.enabled` | `false` | 是否把上游思考内容翻译成 Anthropic `thinking` 块 |
| `reasoning.replayRequired` | `false` | 多轮工具调用时是否把历史思考回放给上游 |
| `stream.usage` | `false` | 是否下发 `stream_options.include_usage` |
| `stream.heartbeat` | `true` | 流式响应期间是否发 `event: ping` 心跳（间隔 15s） |

需要按渠道微调时直接改这个常量，重新部署即可。

> 因为 `stream.usage` 默认关闭，**流式响应的 `output_tokens` 会是 0**。想要用量统计就在上游支持的前提下把它打开。

## 9. 校验与错误

配置**在请求到达时才校验**（按 `env` 对象缓存解析结果）。这意味着：

> **部署成功 ≠ 配置正确。** 一个错误的 `token_ref` 会让部署照样成功，直到某个请求真的路由到那个模型才返回 500。配完务必用 `wrangler dev` 本地验证一次。

各类错误的实际报文（已实测）：

| 状态码 | 触发条件 | 报文示例 |
|---|---|---|
| 400 | 未知模型，且没配 `DEFAULT_FALLBACK` | `Unknown model "nope". No fallback configured.` |
| 400 | 渠道没挂在该模型下 | `Provider "x" is not available for model "m". Available: provider-a, provider-b` |
| 403 | 暗号不匹配 | `Unauthorized: wrong secret token.` |
| 500 | 配置本身有问题 | `Configuration error: Env var PROVIDERS is not valid JSON` |
| 500 | `DEFAULT_FALLBACK` 指向不存在的模型 | `Configuration error: DEFAULT_FALLBACK.m references unknown model "ghost"` |
| 500 | 别名指向不存在的模型 | `Configuration error: MODEL_ALIASES.sonnet references unknown model "ghost"` |
| 500 | 密钥变量未配置 | `Configuration error: Secret "MISSING_KEY" (referenced by token_ref) is not configured` |
| 502 | 上游连不上或上游报错 | `Upstream error: Network connection lost.` |

500 的报文**只含变量名，不含变量值**。
