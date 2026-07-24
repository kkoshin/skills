# 配置详解

`worker-proxy.js` 顶部有四个配置区，按需修改。

## 1. SECRET_TOKEN — 暗号

```js
const SECRET_TOKEN = "your-secret-token";
```

客户端请求时放在 `Authorization` 头里：

```bash
curl -H "Authorization: Bearer your-secret-token" https://<your-url>/v1/messages
```

代理校验 `Authorization` 头是否**包含**这个字符串，不匹配返回 403。

> 暗号不是 API Key，只是你自定义的访问口令，防止别人滥用你的代理。

## 2. PROVIDERS — 上游渠道

```js
const PROVIDERS = {
  "provider-a": { baseUrl: "https://api.example-a.com" },
  "provider-b": { baseUrl: "https://api.example-b.com" },
};
```

每个渠道定义一个 `baseUrl`。代理转发时会拼接 `baseUrl + 请求路径`。

**添加新渠道：**

```js
const PROVIDERS = {
  // ...已有渠道
  openai: { baseUrl: "https://api.openai.com" },
};
```

> 注意：baseUrl 是上游 API 的根地址，代理会把客户端的路径（如 `/v1/messages`）拼到后面。所以上游必须支持相同的路径结构。

## 3. MODELS — 模型路由表

```js
const MODELS = {
  "model-a": [
    { provider_id: "provider-a", auth_token: "sk-your-provider-a-key" },
    { provider_id: "provider-b", auth_token: "sk-your-provider-b-key" },
  ],
  "model-b": [
    { provider_id: "provider-b", auth_token: "sk-your-provider-b-key" },
  ],
};
```

每个模型挂一个或多个渠道，每个渠道有独立的 `auth_token`（即该渠道的 API Key）。代理转发时会用对应渠道的 token 替换 `Authorization` 头。

**添加新模型：**

```js
const MODELS = {
  // ...已有模型
  "grok-4.5": [
    { provider_id: "openai", auth_token: "sk-your-openai-key" },
  ],
};
```

## 4. DEFAULT_FALLBACK — 兜底 + 别名

```js
const DEFAULT_FALLBACK = {
  "model-a": "model-a:provider-a",
  "model-b": "model-b:provider-b",
  // 别名
  "Model-A": "model-a:provider-a",
};
```

两个作用：

### 4.1 兜底渠道

客户端只传模型名（不带 `:provider`）时，走这里指定的渠道：

```
客户端传 "model-b"  →  DEFAULT_FALLBACK["model-b"] = "model-b:provider-b"  →  走 provider-b
```

### 4.2 模型别名

当 key **不在 MODELS 里**时，视为别名。value 的模型名部分必须是 MODELS 里已存在的规范名（小写）：

```
客户端传 "Model-A"  →  DEFAULT_FALLBACK["Model-A"] = "model-a:provider-a"
  →  resolveRoute 解析出 modelName="model-a", providerId="provider-a"
  →  查 MODELS["model-a"] 找到配置
  →  proxyRequest 把 body.model 改写为 "model-a" 再转发给上游
```

**为什么需要别名改写 body？** 上游对模型名大小写敏感，`Model-A` 会 404，必须改成 `model-a`。

**添加新别名：**

```js
const DEFAULT_FALLBACK = {
  // ...已有条目
  "GPT-4O": "gpt-4o:openai",  // 假设 MODELS 里有 "gpt-4o"
};
```

> 别名不会自动出现在 `/v1/models` 列表里——代理会自动把 DEFAULT_FALLBACK 里不在 MODELS 中的 key 作为别名加入列表，告知客户端可用。

## 配置示例：完整流程

假设要新增一个 `qwen-max` 模型，走阿里云渠道，并支持 `Qwen-Max` 大写别名：

```js
// 1. 加渠道
const PROVIDERS = {
  // ...
  aliyun: { baseUrl: "https://dashscope.aliyuncs.com" },
};

// 2. 加模型
const MODELS = {
  // ...
  "qwen-max": [
    { provider_id: "aliyun", auth_token: "sk-your-aliyun-key" },
  ],
};

// 3. 加兜底 + 别名
const DEFAULT_FALLBACK = {
  // ...
  "qwen-max": "qwen-max:aliyun",
  "Qwen-Max": "qwen-max:aliyun",  // 别名
};
```

部署后，客户端传 `qwen-max`、`Qwen-Max`、`qwen-max:aliyun` 都能正确路由。