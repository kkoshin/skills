const HOP_BY_HOP_HEADERS = new Set([
  "connection",
  "content-encoding",
  "content-length",
  "keep-alive",
  "proxy-authenticate",
  "proxy-authorization",
  "te",
  "trailer",
  "transfer-encoding",
  "upgrade"
]);

export function checkAuth(env, request) {
  const header = (request.headers.get("Authorization") || "").trim();
  const match = header.match(/^Bearer\s+(.+)$/i);
  const token = (match ? match[1] : header).trim();
  const apiKey = (request.headers.get("x-api-key") || "").trim();
  return typeof env?.SECRET_TOKEN === "string"
    && env.SECRET_TOKEN !== ""
    && (token === env.SECRET_TOKEN || apiKey === env.SECRET_TOKEN);
}

// 只做尾斜杠归一化（/v1/messages/ 与 /v1/messages 等价）。
// 如果你的 Worker 挂在自定义域名的子路径下（例如 https://example.com/anthropic），
// 客户端发来的路径会带前缀，需要在这里先把前缀剥掉。见 references/deployment.md
// 的"自定义域名与路径前缀"一节。
export function normalizeRequestPath(pathname) {
  if (pathname.length > 1 && pathname.endsWith("/")) return pathname.replace(/\/+$/, "");
  return pathname;
}

export function isMessagesPath(pathname) {
  return pathname === "/v1/messages" || pathname === "/messages";
}

export function isCountTokensPath(pathname) {
  return pathname === "/v1/messages/count_tokens" || pathname === "/messages/count_tokens";
}

export function isEventStream(response) {
  return (response.headers.get("content-type") || "").toLowerCase().includes("text/event-stream");
}

export function buildUpstreamHeaders(request, route) {
  const headers = new Headers();
  for (const [key, value] of request.headers) {
    const lower = key.toLowerCase();
    if (HOP_BY_HOP_HEADERS.has(lower) || lower === "authorization" || lower === "x-api-key") continue;
    headers.set(key, value);
  }
  if (route.authType === "api-key") {
    headers.set("x-api-key", route.apiKey);
  } else {
    headers.set("Authorization", `Bearer ${route.apiKey}`);
  }
  return headers;
}

export function transformedHeaders(response, contentType = null) {
  const headers = new Headers(response.headers);
  for (const key of HOP_BY_HOP_HEADERS) headers.delete(key);
  if (contentType) headers.set("Content-Type", contentType);
  return headers;
}

export function withCORS(response) {
  for (const [key, value] of Object.entries(corsHeaders())) {
    response.headers.set(key, value);
  }
  return response;
}

export function passThroughResponse(response) {
  return withCORS(new Response(response.body, {
    status: response.status,
    statusText: response.statusText,
    headers: new Headers(response.headers)
  }));
}

export function jsonResponse(data, status = 200) {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "Content-Type": "application/json", ...corsHeaders() }
  });
}

export function anthropicErrorResponse(status, type, message, extraHeaders = {}) {
  return new Response(JSON.stringify({
    type: "error",
    error: { type, message }
  }), {
    status,
    headers: { "Content-Type": "application/json", ...corsHeaders(), ...extraHeaders }
  });
}

export async function mapUpstreamErrorToAnthropic(response) {
  let message = `Upstream returned HTTP ${response.status}`;
  let type = response.status === 429 ? "rate_limit_error" : "api_error";
  try {
    const body = await response.clone().json();
    message = body?.error?.message || body?.message || message;
    if (typeof body?.error?.type === "string") type = body.error.type;
  } catch {
    try {
      message = (await response.clone().text()) || message;
    } catch {
      // Keep the status-based message when the upstream body is unreadable.
    }
  }
  return anthropicErrorResponse(response.status, type, message);
}

export function handleCORS() {
  return new Response(null, {
    status: 204,
    headers: corsHeaders()
  });
}

export function corsHeaders() {
  return {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type, Authorization, x-api-key, anthropic-version, anthropic-beta",
    "Access-Control-Max-Age": "86400"
  };
}

export function requestId(prefix = "") {
  if (globalThis.crypto?.randomUUID) return `${prefix}${globalThis.crypto.randomUUID()}`;
  return `${prefix}${Math.random().toString(36).slice(2, 10)}${Math.random().toString(36).slice(2, 10)}`;
}
