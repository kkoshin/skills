/**
 * Worker 入口。一次请求的流程：
 *
 *   1. OPTIONS        → CORS 预检，直接 204（不校验凭证）
 *   2. 校验 SECRET_TOKEN（未配置 → 500；不匹配 → 403）
 *   3. GET            → /health 健康检查、/v1/models 模型列表
 *   4. POST 路径闸门  → 只放行 /v1/messages；其余（含 /v1/chat/completions）404
 *   5. loadConfig(env) + resolveRoute(...) 解析出目标渠道
 *   6. proxyRequest(...) 转发；上游是 OpenAI 协议时做请求/响应双向转换
 *
 * 配置全部来自 wrangler.toml 的 [vars]，在请求路径上按 env 对象缓存解析结果。
 */
import { ConfigError, loadConfig, resolveRoute } from "./config.js";
import {
  anthropicErrorResponse,
  checkAuth,
  handleCORS,
  isCountTokensPath,
  isMessagesPath,
  jsonResponse,
  normalizeRequestPath
} from "./http.js";
import { handleModelsList } from "./models.js";
import { ProtocolConversionError } from "./protocol.js";
import { proxyRequest } from "./proxy.js";

export default {
  async fetch(request, env, ctx) {
    void ctx;
    if (request.method === "OPTIONS") return handleCORS();

    const url = new URL(request.url);
    const requestPath = normalizeRequestPath(url.pathname);

    if (typeof env?.SECRET_TOKEN !== "string" || env.SECRET_TOKEN.trim() === "") {
      return configErrorResponse(new ConfigError("Secret SECRET_TOKEN is not configured"));
    }
    if (!checkAuth(env, request)) {
      return anthropicErrorResponse(403, "authentication_error", "Unauthorized: wrong secret token.");
    }

    if (request.method === "GET") {
      let config;
      try {
        config = loadConfig(env);
      } catch (err) {
        return configErrorResponse(err);
      }
      if (requestPath === "/" || requestPath === "/health") {
        return jsonResponse({ status: "ok", service: "cloudflare-worker-proxy" });
      }
      if (requestPath === "/v1/models" || requestPath === "/models") {
        return handleModelsList(config);
      }
      return anthropicErrorResponse(404, "not_found_error", `No GET route for "${url.pathname}". Available: /v1/models`);
    }

    if (request.method !== "POST") {
      return anthropicErrorResponse(
        405,
        "invalid_request_error",
        "Only GET, POST, and OPTIONS requests are supported.",
        { Allow: "GET, POST, OPTIONS" }
      );
    }

    if (isCountTokensPath(requestPath)) {
      return anthropicErrorResponse(
        404,
        "not_found_error",
        "Token counting is not available for this provider; use the client fallback."
      );
    }

    if (!isMessagesPath(requestPath)) {
      return anthropicErrorResponse(
        404,
        "not_found_error",
        "Only the Anthropic POST /v1/messages endpoint is supported."
      );
    }

    // Read the body as text once. The clone() that used to be here teed the
    // stream and pinned a second full copy of the body in memory for the life
    // of the request, and proxyRequest never reads request.body (it only needs
    // url, headers and redirect), so the copy bought nothing.
    let text;
    try {
      text = await request.text();
    } catch (err) {
      return anthropicErrorResponse(400, "invalid_request_error", `Invalid request: ${err.message}`);
    }
    let body;
    try {
      body = JSON.parse(text);
    } catch (err) {
      return anthropicErrorResponse(400, "invalid_request_error", `Invalid request: ${err.message}`);
    }
    if (!body || typeof body !== "object" || Array.isArray(body)) {
      return anthropicErrorResponse(400, "invalid_request_error", "Request body must be a JSON object.");
    }
    if (typeof body.model !== "string" || body.model.trim() === "") {
      return anthropicErrorResponse(400, "invalid_request_error", 'Missing or invalid "model" field.');
    }

    let config;
    try {
      config = loadConfig(env);
      const route = resolveRoute(env, config, body.model);
      if (route.error) {
        return anthropicErrorResponse(400, "invalid_request_error", route.error);
      }
      return await proxyRequest(request, body, route, text);
    } catch (err) {
      if (err instanceof ConfigError) return configErrorResponse(err);
      if (err instanceof ProtocolConversionError) {
        return anthropicErrorResponse(err.status, "api_error", err.message);
      }
      return anthropicErrorResponse(502, "api_error", `Upstream error: ${err.message}`);
    }
  }
};

function configErrorResponse(error) {
  return anthropicErrorResponse(500, "api_error", `Configuration error: ${error.message}`);
}
