import {
  buildUpstreamHeaders,
  isEventStream,
  mapUpstreamErrorToAnthropic,
  passThroughResponse,
  jsonResponse
} from "./http.js";
import {
  anthropicRequestToOpenAI,
  isThinkingRequested,
  openAIResponseToAnthropic,
  ProtocolConversionError
} from "./protocol.js";
import { spliceTopLevelString } from "./json-scan.js";
import { guardAnthropicStream, openAIStreamToAnthropic } from "./streaming.js";

export async function proxyRequest(request, body, route, rawText = null) {
  const requestUrl = new URL(request.url);
  const upstreamProtocol = route.upstreamProtocol;
  const upstreamPath = upstreamProtocol === "openai" ? "/v1/chat/completions" : "/v1/messages";
  const targetUrl = buildTargetUrl(route.baseUrl, upstreamPath, requestUrl.search);
  const headers = buildUpstreamHeaders(request, route);
  const upstreamRequest = new Request(targetUrl, {
    method: "POST",
    headers,
    body: serializeUpstreamBody(upstreamProtocol, body, route, rawText),
    redirect: request.redirect
  });
  const response = await fetch(upstreamRequest);

  if (!response.ok) return mapUpstreamErrorToAnthropic(response);

  const wantStream = body.stream === true;
  if (upstreamProtocol === "openai") {
    if (wantStream && isEventStream(response)) {
      return openAIStreamToAnthropic(response, route, { thinkingEnabled: isThinkingRequested(body) });
    }
    let payload;
    try {
      payload = await response.json();
    } catch (err) {
      throw new ProtocolConversionError(`Upstream returned invalid JSON: ${err.message}`);
    }
    return jsonResponse(openAIResponseToAnthropic(payload, route, {
      thinkingEnabled: isThinkingRequested(body)
    }));
  }

  if (wantStream && isEventStream(response)) return guardAnthropicStream(response);
  return passThroughResponse(response);
}

// The OpenAI upstreams need a rebuilt object graph, so they always pay for a
// full serialize. Anthropic-native upstreams differ from the client's body only
// in the top-level `model`, so splice that one value into the original text and
// skip re-serializing the rest. Anything the splice cannot resolve falls back to
// the full serialization, so both paths produce the same upstream body.
function serializeUpstreamBody(upstreamProtocol, body, route, rawText) {
  if (upstreamProtocol === "openai") {
    return JSON.stringify(anthropicRequestToOpenAI(
      { ...body, model: route.modelName },
      { providerId: route.providerId }
    ));
  }
  const spliced = rawText === null ? null : spliceTopLevelString(rawText, "model", route.modelName);
  return spliced !== null ? spliced : JSON.stringify({ ...body, model: route.modelName });
}

function buildTargetUrl(baseUrl, pathname, search) {
  const base = new URL(baseUrl);
  const basePath = base.pathname.replace(/\/+$/, "");
  const path = pathname.startsWith("/v1/") && basePath.endsWith("/v1")
    ? pathname.slice("/v1".length)
    : pathname;
  base.pathname = `${basePath}/${path.replace(/^\/+/, "")}` || "/";
  base.search = search || "";
  return base.toString();
}
