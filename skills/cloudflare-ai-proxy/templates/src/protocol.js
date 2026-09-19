/**
 * 所有 protocol = "openai" 的上游共用这一组能力开关。
 *
 * 模板有意不把它做成配置项：绝大多数部署用默认值就够了，而把它拆成
 * per-provider 配置会让 wrangler.toml 复杂一倍。确实需要按渠道微调时，
 * 直接改这里的值即可（改完重新部署）。
 */
export const OPENAI_CAPABILITIES = Object.freeze({
  // 是否把 Anthropic 的 tools / tool_choice / parallel_tool_calls 转成 OpenAI 形式。
  // 上游不支持时置 false，对应字段会被丢弃。
  tools: true,
  parallelToolCalls: true,
  toolChoice: true,
  // assistant 消息只有 tool_use、没有文本内容时，是否必须补一个空字符串 content。
  // 部分上游拒绝 content 为 null 的 assistant 消息。
  assistantToolContentRequired: true,
  reasoning: Object.freeze({
    // 是否把上游返回的思考内容翻译成 Anthropic 的 thinking 块。
    enabled: false,
    // 从上游响应的哪个字段读取思考内容。
    responseField: "reasoning_content",
    // 多轮工具调用时，是否把历史思考回放给上游。
    replayRequired: false
    // 可选旋钮（默认不设）：
    //   replayField  —— 回放时写回哪个字段，缺省用 responseField
    //   requestField —— 请求侧用哪个字段开启思考；不设则 thinking 不会被下发
    //   requestValue —— 与 requestField 搭配的值
  }),
  stream: Object.freeze({
    // 是否下发 stream_options.include_usage。部分上游不支持该字段。
    // 关掉时流式响应的 usage 会全为 0。
    usage: false,
    heartbeat: true,
    heartbeatIntervalMs: 15000
  })
});

export class ProtocolConversionError extends Error {
  constructor(message, status = 502) {
    super(message);
    this.name = "ProtocolConversionError";
    this.status = status;
  }
}

const SUPPORTED_ANTHROPIC_FIELDS = new Set([
  "model",
  "system",
  "messages",
  "max_tokens",
  "temperature",
  "top_p",
  "stop_sequences",
  "stream",
  "stream_options",
  "tools",
  "tool_choice",
  "parallel_tool_calls",
  "thinking"
]);

export function isThinkingRequested(body) {
  const type = body?.thinking?.type;
  return type === "enabled" || type === "adaptive";
}

export function anthropicRequestToOpenAI(body, { providerId = "unknown" } = {}) {
  const capabilities = OPENAI_CAPABILITIES;
  const oai = { model: body.model };
  if (body.stream != null) oai.stream = body.stream;
  if (body.max_tokens != null) oai.max_tokens = body.max_tokens;
  if (body.temperature != null) oai.temperature = body.temperature;
  if (body.top_p != null) oai.top_p = body.top_p;
  if (body.stop_sequences != null) oai.stop = body.stop_sequences;
  if (Array.isArray(body.tools) && capabilities.tools !== false) {
    oai.tools = body.tools.map(anthropicToolToOpenAI);
  }
  if (body.tool_choice && capabilities.toolChoice !== false && capabilities.tools !== false) {
    const toolChoice = mapToolChoice(body.tool_choice);
    if (toolChoice !== undefined) oai.tool_choice = toolChoice;
  }
  if (body.parallel_tool_calls != null && capabilities.parallelToolCalls !== false) {
    oai.parallel_tool_calls = body.parallel_tool_calls;
  }
  if (body.stream && capabilities.stream?.usage === true) {
    oai.stream_options = {
      ...(isRecord(body.stream_options) ? body.stream_options : {}),
      include_usage: true
    };
  }
  applyReasoningRequest(oai, body, providerId);

  const messages = [];
  if (body.system != null) {
    const system = typeof body.system === "string"
      ? body.system
      : Array.isArray(body.system)
        ? body.system.map((block) => block?.text || "").join("\n")
        : "";
    messages.push({ role: "system", content: system });
  }
  if (!Array.isArray(body.messages)) {
    throw new ProtocolConversionError('"messages" must be an array.', 400);
  }
  for (const message of body.messages) {
    pushAnthropicMessageToOpenAI(messages, message);
  }
  oai.messages = messages;

  for (const field of Object.keys(body)) {
    if (!SUPPORTED_ANTHROPIC_FIELDS.has(field)) logDroppedField(field, body, providerId);
  }
  if (body.thinking != null && !capabilities.reasoning?.requestField) {
    logDroppedField("thinking (no provider request mapping)", body, providerId);
  }
  return oai;
}

function applyReasoningRequest(oai, body, providerId) {
  if (!isThinkingRequested(body)) return;
  const reasoning = OPENAI_CAPABILITIES.reasoning;
  const field = reasoning.requestField;
  if (typeof field !== "string" || !/^[A-Za-z_][A-Za-z0-9_]*$/.test(field)) {
    if (reasoning.enabled === false) logDroppedField("thinking", body, providerId);
    return;
  }
  const value = reasoning.requestValue ?? reasoning.mode;
  if (value != null) oai[field] = value;
  else logDroppedField("thinking (missing provider request value)", body, providerId);
}

// console.* is synchronous I/O billed against the CPU budget, and this fired
// once per unknown field on every request. Log each distinct field once per
// isolate instead, capped so an unusual client payload cannot grow the set
// without bound. The details are diagnostic only, so dropping repeats is safe.
const MAX_DROPPED_FIELD_LOGS = 64;
const loggedDroppedFields = new Set();

function logDroppedField(field, body, providerId) {
  const key = `${providerId}\u0000${field}`;
  if (loggedDroppedFields.has(key) || loggedDroppedFields.size >= MAX_DROPPED_FIELD_LOGS) return;
  loggedDroppedFields.add(key);
  try {
    console.debug("[ai-proxy] dropped unsupported field", {
      field,
      model: body?.model,
      provider: providerId
    });
  } catch {
    // Logging must never change request behavior.
  }
}

function anthropicToolToOpenAI(tool) {
  if (!isRecord(tool)) throw new ProtocolConversionError("Each tool must be an object.", 400);
  return {
    type: "function",
    function: {
      name: tool.name,
      description: tool.description || "",
      parameters: tool.input_schema || { type: "object", properties: {} }
    }
  };
}

function mapToolChoice(toolChoice) {
  if (!toolChoice) return undefined;
  if (toolChoice.type === "auto") return "auto";
  if (toolChoice.type === "any") return "required";
  if (toolChoice.type === "none") return "none";
  if (toolChoice.type === "tool") {
    return { type: "function", function: { name: toolChoice.name } };
  }
  return undefined;
}

function pushAnthropicMessageToOpenAI(messages, message) {
  const capabilities = OPENAI_CAPABILITIES;
  if (!isRecord(message)) throw new ProtocolConversionError("Each message must be an object.", 400);
  const blocks = typeof message.content === "string"
    ? [{ type: "text", text: message.content }]
    : Array.isArray(message.content) ? message.content : [];

  if (message.role === "assistant") {
    const textParts = [];
    const toolCalls = [];
    let reasoning = "";
    for (const block of blocks) {
      if (!isRecord(block)) continue;
      if (block.type === "text") textParts.push(block.text || "");
      else if (block.type === "thinking") reasoning += block.thinking || "";
      else if (block.type === "tool_use") {
        toolCalls.push({
          id: block.id || "",
          type: "function",
          function: { name: block.name || "", arguments: JSON.stringify(block.input ?? {}) }
        });
      }
    }
    const msg = {
      role: "assistant",
      content: textParts.length || capabilities.assistantToolContentRequired !== false
        ? textParts.join("")
        : null
    };
    if (
      reasoning !== ""
      && capabilities.reasoning?.enabled !== false
      && capabilities.reasoning?.replayRequired !== false
    ) {
      msg[capabilities.reasoning?.replayField || capabilities.reasoning?.responseField || "reasoning_content"] = reasoning;
    }
    if (toolCalls.length && capabilities.tools !== false) msg.tool_calls = toolCalls;
    messages.push(msg);
    return;
  }

  if (message.role === "user") {
    const textParts = [];
    const imageParts = [];
    const toolResults = [];
    for (const block of blocks) {
      if (!isRecord(block)) continue;
      if (block.type === "text") textParts.push(block.text || "");
      else if (block.type === "image") {
        const part = anthropicBlockToOpenAIPart(block);
        if (part) imageParts.push(part);
      } else if (block.type === "tool_result") {
        toolResults.push({
          tool_use_id: block.tool_use_id,
          content: serializeToolResult(block.content)
        });
      }
    }
    for (const result of toolResults) {
      messages.push({ role: "tool", tool_call_id: result.tool_use_id, content: result.content });
    }
    if (textParts.length || imageParts.length) {
      if (imageParts.length) {
        const content = [];
        if (textParts.length) content.push({ type: "text", text: textParts.join("") });
        content.push(...imageParts);
        messages.push({ role: "user", content });
      } else {
        messages.push({ role: "user", content: textParts.join("") });
      }
    }
    return;
  }

  if (message.role === "system") {
    messages.push({ role: "system", content: normalizeOpenAIContent(message.content) });
    return;
  }
  throw new ProtocolConversionError(`Unsupported Anthropic message role "${message.role}".`, 400);
}

function serializeToolResult(content) {
  if (typeof content === "string") return content;
  if (Array.isArray(content)) {
    return content.map((part) => {
      if (part?.type === "text") return part.text || "";
      if (part?.type === "image") return "[Tool returned an image that cannot be represented by this upstream protocol.]";
      if (part?.type === "document") return "[Tool returned a document that cannot be represented by this upstream protocol.]";
      if (part == null) return "";
      return `[Tool returned unsupported content block: ${part.type || "unknown"}.]`;
    }).filter(Boolean).join("\n");
  }
  return JSON.stringify(content ?? "");
}

function normalizeOpenAIContent(content) {
  if (typeof content === "string") return content;
  if (Array.isArray(content)) {
    const parts = content.map(anthropicBlockToOpenAIPart).filter(Boolean);
    return parts.length ? parts : null;
  }
  return content;
}

function anthropicBlockToOpenAIPart(block) {
  if (!isRecord(block)) return null;
  if (block.type === "text") return { type: "text", text: block.text || "" };
  if (block.type === "image" && isRecord(block.source)) {
    const source = block.source;
    if (source.type === "base64") {
      return {
        type: "image_url",
        image_url: { url: `data:${source.media_type};base64,${source.data}` }
      };
    }
    if (source.type === "url" && source.url) {
      return { type: "image_url", image_url: { url: source.url } };
    }
  }
  return null;
}

export function openAIResponseToAnthropic(obj, route, { thinkingEnabled }) {
  if (!isRecord(obj) || !Array.isArray(obj.choices) || obj.choices.length === 0) {
    throw new ProtocolConversionError("Upstream response did not contain a completion choice.");
  }
  const choice = obj.choices[0];
  if (!isRecord(choice) || !isRecord(choice.message)) {
    throw new ProtocolConversionError("Upstream response did not contain a completion message.");
  }
  const message = choice.message;
  const content = [];
  const reasoning = readProviderReasoning(message);
  if (
    thinkingEnabled
    && OPENAI_CAPABILITIES.reasoning.enabled !== false
    && typeof reasoning === "string"
    && reasoning !== ""
  ) {
    content.push({ type: "thinking", thinking: reasoning });
  }
  if (message.content != null) pushTextBlocks(content, message.content);
  if (Array.isArray(message.tool_calls)) {
    for (const toolCall of message.tool_calls) {
      const input = parseToolArguments(toolCall.function?.arguments, `OpenAI tool call ${toolCall.id || ""}`);
      content.push({
        type: "tool_use",
        id: toolCall.id || "",
        name: toolCall.function?.name || "",
        input
      });
    }
  }

  const usage = isRecord(obj.usage) ? obj.usage : {};
  return {
    id: "msg_" + requestId(),
    type: "message",
    role: "assistant",
    model: route.modelName,
    content,
    stop_reason: mapStopReason(choice.finish_reason),
    stop_sequence: null,
    usage: {
      input_tokens: usage.prompt_tokens || 0,
      output_tokens: usage.completion_tokens || 0
    }
  };
}

function requestId() {
  if (globalThis.crypto?.randomUUID) return globalThis.crypto.randomUUID();
  return Math.random().toString(36).slice(2, 18);
}

export function readProviderReasoning(message) {
  const field = OPENAI_CAPABILITIES.reasoning.responseField || "reasoning_content";
  return message?.[field] ?? message?.reasoning_content ?? message?.reasoning;
}

export function parseToolArguments(raw, context = "Tool call", status = 502) {
  if (raw == null || raw === "") return {};
  if (typeof raw !== "string") {
    throw new ProtocolConversionError(`${context} arguments must be a JSON string.`, status);
  }
  try {
    const parsed = JSON.parse(raw);
    if (parsed === null || typeof parsed !== "object" || Array.isArray(parsed)) {
      throw new Error("expected a JSON object");
    }
    return parsed;
  } catch (err) {
    throw new ProtocolConversionError(`${context} arguments are invalid JSON: ${err.message}`, status);
  }
}

function pushTextBlocks(content, text) {
  if (typeof text === "string") {
    if (text !== "") content.push({ type: "text", text });
  } else if (Array.isArray(text)) {
    for (const part of text) {
      if (part && part.type === "text") content.push({ type: "text", text: part.text || "" });
    }
  }
}

export function mapStopReason(reason) {
  switch (reason) {
    case "stop": return "end_turn";
    case "length": return "max_tokens";
    case "tool_calls":
    case "function_call": return "tool_use";
    case "content_filter": return "end_turn";
    default: return "end_turn";
  }
}

function isRecord(value) {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
