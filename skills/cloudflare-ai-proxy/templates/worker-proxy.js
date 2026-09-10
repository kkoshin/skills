/**
 * Cloudflare Worker — AI API 请求转发代理
 *
 * 功能：
 *   1. 校验请求中的「暗号」（Secret Token），防止未授权调用
 *   2. 根据请求 body 中的 model 字段（支持 "model:provider" 格式），自动路由
 *   3. 替换 Authorization 头为对应渠道的 API Key
 *   4. 支持流式响应 (SSE / streaming)
 *   5. 支持 CORS（浏览器端调用）
 *   6. 支持模型别名（如 "Model-A" → "model-a"），别名请求自动改写 body 再转发
 *   7. 提供 /v1/models 模型列表（兼容 OpenAI / Anthropic 格式）和 /health 健康检查
 *
 * 部署方式：
 *   - 使用 wrangler deploy 部署（详见 skill 的 references/deployment.md）
 *   - 或在 Cloudflare Dashboard 中新建 Worker，粘贴本文件内容
 */

// ============================================================
// 配置区 — 按需修改
// ============================================================

/** 你的暗号：客户端请求时放在 Authorization 头里，例如 "Bearer your-secret-token" */
const SECRET_TOKEN = "your-secret-token";

/** 上游渠道定义：每个渠道的 baseUrl */
const PROVIDERS = {
  "provider-a": { baseUrl: "https://api.example-a.com" },
  "provider-b": { baseUrl: "https://api.example-b.com" },
  luna: { baseUrl: "https://api.luna.example.com" },
  // 添加新渠道：provider_id: { baseUrl: "https://..." }
};

/**
 * 模型路由表：每个模型可用的渠道列表
 * 同一个模型可以挂多个渠道，客户端可通过 "model:provider" 显式选择
 */
const MODELS = {
  "model-a": [
    { provider_id: "provider-a", auth_token: "sk-your-provider-a-key" },
    { provider_id: "provider-b", auth_token: "sk-your-provider-b-key" },
  ],
  "model-b": [
    { provider_id: "provider-b", auth_token: "sk-your-provider-b-key" },
  ],
  haiku: [
    { provider_id: "luna", auth_token: "sk-your-luna-key" },
  ],
};

/**
 * 默认兜底：客户端只传模型名（不带渠道）时，走这里指定的渠道
 * 格式：{ 模型名: "模型名:渠道ID" }
 *
 * 同时用于【别名映射】：key 不在 MODELS 里时即为别名，
 * value 的模型名部分必须是 MODELS 里已存在的规范名（小写）。
 * 例："Model-A": "model-a:provider-a"  →  客户端传 Model-A 时按 model-a 处理
 */
const DEFAULT_FALLBACK = {
  "model-a": "model-a:provider-a",
  "model-b": "model-b:provider-b",
  haiku: "haiku:luna",
  // 别名示例：大写名 → 规范名:渠道
  "Model-A": "model-a:provider-a",
};

// ============================================================
// Worker 入口
// ============================================================

export default {
  async fetch(request, env, ctx) {
    // ----- 1. CORS 预检请求 -----
    if (request.method === "OPTIONS") {
      return handleCORS();
    }

    // ----- 2. 安全校验：检查暗号 -----
    const authHeader = request.headers.get("Authorization") || "";
    if (!authHeader.includes(SECRET_TOKEN)) {
      return jsonError(403, "Unauthorized: wrong secret token.");
    }

    // ----- 3. GET 接口：模型列表 / 健康检查 -----
    if (request.method === "GET") {
      const url = new URL(request.url);

      // 健康检查
      if (url.pathname === "/" || url.pathname === "/health") {
        return jsonResponse({ status: "ok", service: "cloudflare-worker-proxy" });
      }

      // 模型列表 — 兼容 OpenAI / Anthropic 的 /v1/models 接口
      if (url.pathname === "/v1/models" || url.pathname === "/models") {
        return handleModelsList();
      }

      return jsonError(404, `No GET route for "${url.pathname}". Available: /v1/models`);
    }

    // ----- 4. POST 接口：解析路由并转发 -----
    try {
      if (request.method === "POST") {
        const cloned = request.clone();
        const body = await cloned.json();
        const modelField = body?.model;

        if (!modelField) {
          return jsonError(400, 'Missing "model" field in request body.');
        }

        const route = resolveRoute(modelField);
        if (route.error) {
          return jsonError(400, route.error);
        }

        return await proxyRequest(request, body, route);
      }

      return jsonError(405, "Only GET and POST requests are supported.");
    } catch (err) {
      return jsonError(400, `Invalid request: ${err.message}`);
    }
  },
};

// ============================================================
// 路由解析
// ============================================================

/**
 * 解析 model 字段，返回 { baseUrl, apiKey, modelName } 或 { error }
 *
 * model 字段支持两种格式：
 *   - "model-a:provider-b"  → 显式指定模型和渠道
 *   - "model-a"             → 仅指定模型，走 DEFAULT_FALLBACK 兜底
 *
 * 别名机制：DEFAULT_FALLBACK 的 key 不在 MODELS 里时视为别名，
 * 其 value 指向规范模型名（小写）。modelName 统一转小写后回传，
 * 供 proxyRequest 改写 body，确保上游收到标准化名称。
 */
function resolveRoute(modelField) {
  let modelName, providerId;

  if (modelField.includes(":")) {
    // 格式: "模型名:渠道ID"
    [modelName, providerId] = modelField.split(":", 2);
  } else {
    // 格式: "模型名" — 走兜底
    modelName = modelField;
    const fallback = DEFAULT_FALLBACK[modelName];
    if (!fallback) {
      return { error: `Unknown model "${modelName}". No fallback configured.` };
    }
    [, providerId] = fallback.split(":", 2);
  }

  // 统一转小写，支持大小写不敏感匹配（如 "Model-A" → "model-a"）
  modelName = modelName.toLowerCase();

  // 检查模型是否存在
  const modelConfig = MODELS[modelName];
  if (!modelConfig) {
    return { error: `Unknown model "${modelName}".` };
  }

  // 检查该模型下是否有指定渠道
  const providerConfig = modelConfig.find((p) => p.provider_id === providerId);
  if (!providerConfig) {
    const available = modelConfig.map((p) => p.provider_id).join(", ");
    return {
      error: `Provider "${providerId}" is not available for model "${modelName}". Available: ${available}`,
    };
  }

  // 检查渠道定义是否存在
  const provider = PROVIDERS[providerId];
  if (!provider) {
    return { error: `Unknown provider "${providerId}".` };
  }

  return {
    baseUrl: provider.baseUrl,
    apiKey: providerConfig.auth_token,
    modelName,
  };
}

// ============================================================
// 核心转发函数
// ============================================================

/**
 * 将请求转发到目标上游
 * @param {Request} request    原始请求
 * @param {object}  body       已解析的 JSON body
 * @param {object}  route      { baseUrl, apiKey, modelName }
 * @returns {Response}
 */
async function proxyRequest(request, body, route) {
  const url = new URL(request.url);
  const targetUrl = `${route.baseUrl}${url.pathname}${url.search}`;

  // 别名映射：将 body 中的 model 改写为规范名（小写），确保上游收到标准化名称
  // 仅在规范名与原始值不同时改写，避免无谓修改
  if (route.modelName && body.model !== route.modelName) {
    body.model = route.modelName;
  }

  // 构造新请求头：保留原始头，但替换 Authorization 和 Host
  const newHeaders = new Headers(request.headers);
  newHeaders.set("Authorization", `Bearer ${route.apiKey}`);
  newHeaders.set("Host", new URL(route.baseUrl).host);

  // 构造转发请求
  const newRequest = new Request(targetUrl, {
    method: request.method,
    headers: newHeaders,
    body: JSON.stringify(body),
    redirect: request.redirect,
  });

  // 发起转发 — 直接透传 Response（自动支持 streaming）
  const response = await fetch(newRequest);

  // 给响应加上 CORS 头
  const corsResponse = new Response(response.body, response);
  for (const [key, value] of Object.entries(corsHeaders())) {
    corsResponse.headers.set(key, value);
  }

  return corsResponse;
}

// ============================================================
// 辅助函数
// ============================================================

/** 返回 JSON 成功响应 */
function jsonResponse(data) {
  return new Response(JSON.stringify(data), {
    status: 200,
    headers: { "Content-Type": "application/json", ...corsHeaders() },
  });
}

/** 返回 JSON 错误响应 */
function jsonError(status, message) {
  return new Response(JSON.stringify({ error: message }), {
    status,
    headers: { "Content-Type": "application/json", ...corsHeaders() },
  });
}

/**
 * 返回支持的模型列表
 * 同时兼容 OpenAI 和 Anthropic 的 /v1/models 接口格式
 * 返回体同时包含两种格式字段，客户端各取所需
 *
 * 列表内容 = MODELS 里的模型 + DEFAULT_FALLBACK 里的别名
 * （别名不在 MODELS 里，但客户端可用其调用）
 */
function handleModelsList() {
  const created = Math.floor(Date.now() / 1000);
  const modelIds = Object.keys(MODELS);

  // 收集别名：DEFAULT_FALLBACK 中指向已有模型但自身不在 MODELS 里的条目
  const aliasIds = Object.keys(DEFAULT_FALLBACK).filter(
    (key) => !(key in MODELS)
  );

  const allIds = [...modelIds, ...aliasIds];

  const data = allIds.map((id) => ({
    // OpenAI 格式字段
    id,
    object: "model",
    created,
    owned_by: "proxy",
    // Anthropic 格式字段
    type: "model",
    display_name: id,
    created_at: new Date().toISOString(),
  }));

  return jsonResponse({
    // OpenAI 格式
    object: "list",
    // Anthropic 格式
    has_more: false,
    first_id: allIds[0] || null,
    last_id: allIds[allIds.length - 1] || null,
    // 通用数据
    data,
  });
}

/** CORS 响应头 */
function corsHeaders() {
  return {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type, Authorization",
    "Access-Control-Max-Age": "86400",
  };
}

/** 处理 OPTIONS 预检请求 */
function handleCORS() {
  return new Response(null, {
    status: 204,
    headers: corsHeaders(),
  });
}