import {
  OPENAI_CAPABILITIES,
  ProtocolConversionError,
  mapStopReason,
  parseToolArguments,
  readProviderReasoning
} from "./protocol.js";
import { corsHeaders, requestId, transformedHeaders } from "./http.js";

const encoder = new TextEncoder();

export function openAIStreamToAnthropic(response, route, { thinkingEnabled }) {
  const decoder = new TextDecoder();
  const state = {
    started: false,
    current: null,
    nextIndex: 0,
    toolBlocks: new Map(),
    stopReason: null,
    usage: null,
    upstreamDone: false,
    sawChunk: false,
    closed: false,
    failed: false,
    toolsFlushed: false
  };
  let buffer = "";
  let reader = null;
  let heartbeat = null;
  let cancelled = false;
  let currentController = null;
  let controllerClosed = false;

  function closeController() {
    if (controllerClosed || cancelled) return;
    controllerClosed = true;
    currentController.close();
  }

  function event(name, data) {
    if (!cancelled && !controllerClosed) {
      currentController.enqueue(encoder.encode(`event: ${name}\ndata: ${JSON.stringify(data)}\n\n`));
    }
  }

  function ensureStarted() {
    if (state.started) return;
    state.started = true;
    event("message_start", {
      type: "message_start",
      message: {
        id: "msg_" + requestId(),
        type: "message",
        role: "assistant",
        model: route.modelName,
        content: [],
        stop_reason: null,
        stop_sequence: null,
        usage: { input_tokens: 0, output_tokens: 0 }
      }
    });
  }

  function closeBlock() {
    if (!state.current) return;
    event("content_block_stop", { type: "content_block_stop", index: state.current.index });
    state.current = null;
  }

  function openBlock(type, init) {
    state.current = { type, index: state.nextIndex++ };
    event("content_block_start", {
      type: "content_block_start",
      index: state.current.index,
      content_block: init
    });
  }

  function ensureBlock(type, init) {
    if (state.current && state.current.type !== type) closeBlock();
    if (!state.current) openBlock(type, init);
  }

  function emitThinking(reasoning) {
    ensureStarted();
    ensureBlock("thinking", { type: "thinking", thinking: "" });
    event("content_block_delta", {
      type: "content_block_delta",
      index: state.current.index,
      delta: { type: "thinking_delta", thinking: reasoning }
    });
  }

  function emitText(text) {
    ensureStarted();
    ensureBlock("text", { type: "text", text: "" });
    event("content_block_delta", {
      type: "content_block_delta",
      index: state.current.index,
      delta: { type: "text_delta", text }
    });
  }

  function collectToolCall(toolCall) {
    const index = Number.isInteger(toolCall.index) ? toolCall.index : 0;
    let block = state.toolBlocks.get(index);
    if (!block) {
      block = { id: "", name: "", args: "" };
      state.toolBlocks.set(index, block);
    }
    if (toolCall.id) block.id = toolCall.id;
    if (toolCall.function?.name) block.name = toolCall.function.name;
    if (toolCall.function?.arguments) block.args += toolCall.function.arguments;
  }

  function flushBufferedTools() {
    if (state.toolsFlushed || !state.toolBlocks.size || state.failed) return;
    state.toolsFlushed = true;
    closeBlock();
    for (const [toolIndex, block] of [...state.toolBlocks.entries()].sort((a, b) => a[0] - b[0])) {
      if (!block.id || !block.name) {
        throw new ProtocolConversionError(`OpenAI tool call ${toolIndex} is missing id or name.`);
      }
      parseToolArguments(block.args, `OpenAI tool call ${block.id}`);
      const index = state.nextIndex++;
      event("content_block_start", {
        type: "content_block_start",
        index,
        content_block: { type: "tool_use", id: block.id, name: block.name, input: {} }
      });
      event("content_block_delta", {
        type: "content_block_delta",
        index,
        delta: { type: "input_json_delta", partial_json: block.args || "{}" }
      });
      event("content_block_stop", { type: "content_block_stop", index });
    }
  }

  function handleChunk(parsed) {
    if (parsed?.error) {
      failStream(parsed.error.message || "Upstream streaming error");
      return;
    }
    if (parsed?.object !== "chat.completion.chunk") return;
    state.sawChunk = true;
    if (parsed.usage) state.usage = parsed.usage;
    const choice = Array.isArray(parsed.choices) ? parsed.choices[0] : null;
    if (!choice) return;
    if (choice.finish_reason) state.stopReason = choice.finish_reason;
    const delta = choice.delta || {};
    const reasoning = readProviderReasoning(delta);
    if (
      thinkingEnabled
      && OPENAI_CAPABILITIES.reasoning.enabled !== false
      && typeof reasoning === "string"
      && reasoning !== ""
    ) {
      emitThinking(reasoning);
    }
    if (typeof delta.content === "string" && delta.content !== "") emitText(delta.content);
    if (Array.isArray(delta.tool_calls)) {
      for (const toolCall of delta.tool_calls) collectToolCall(toolCall);
    }
    if (choice.finish_reason === "tool_calls" || choice.finish_reason === "function_call") {
      try {
        flushBufferedTools();
      } catch (err) {
        failStream(err.message);
      }
    }
  }

  function failStream(message) {
    if (state.failed || state.closed || cancelled) return;
    state.failed = true;
    closeBlock();
    event("error", {
      type: "error",
      error: { type: "api_error", message: message || "Upstream streaming error" }
    });
  }

  function finishStream() {
    if (state.closed || state.failed) return;
    if (!state.upstreamDone) {
      failStream("Upstream stream closed before [DONE].");
      return;
    }
    if (!state.sawChunk) {
      failStream("Upstream returned an empty OpenAI stream.");
      return;
    }
    try {
      flushBufferedTools();
    } catch (err) {
      failStream(err.message);
      return;
    }
    if (!state.stopReason && state.toolBlocks.size) state.stopReason = "tool_calls";
    closeBlock();
    ensureStarted();
    event("message_delta", {
      type: "message_delta",
      delta: { stop_reason: mapStopReason(state.stopReason), stop_sequence: null },
      usage: { output_tokens: state.usage?.completion_tokens || 0 }
    });
    event("message_stop", { type: "message_stop" });
    state.closed = true;
  }

  function processEvent(eventText) {
    const parsedEvent = parseSSEEvent(eventText);
    const { name, data } = parsedEvent;
    if (name === "error") {
      let parsed = null;
      try {
        parsed = JSON.parse(data || "{}");
      } catch {
        // Keep the original text in the error message when it is not JSON.
      }
      failStream(parsed?.error?.message || parsed?.message || data || "Upstream streaming error");
      return false;
    }
    if (data == null) return true;
    if (data === "[DONE]") {
      state.upstreamDone = true;
      return false;
    }
    let parsed;
    try {
      parsed = JSON.parse(data);
    } catch {
      failStream("Upstream returned invalid streaming JSON.");
      return false;
    }
    handleChunk(parsed);
    return !state.failed;
  }

  function drain(input) {
    // Rewriting the whole buffered remainder costs a full copy per read; most
    // upstreams are LF-only, so only pay for it when a CR is actually present.
    let remaining = input.indexOf("\r") !== -1
      ? input.replace(/\r\n/g, "\n").replace(/\r/g, "\n")
      : input;
    let index;
    while ((index = remaining.indexOf("\n\n")) !== -1) {
      const eventText = remaining.slice(0, index);
      remaining = remaining.slice(index + 2);
      if (!processEvent(eventText)) return "";
    }
    return remaining;
  }

  const stream = new ReadableStream({
    start(controller) {
      currentController = controller;
      ensureStarted();
      const interval = OPENAI_CAPABILITIES.stream.heartbeatIntervalMs || 15000;
      if (OPENAI_CAPABILITIES.stream.heartbeat !== false && interval > 0) {
        heartbeat = setInterval(() => {
          if (!state.closed && !state.failed && !cancelled) event("ping", { type: "ping" });
        }, interval);
      }
      reader = response.body?.getReader();
      if (!reader) {
        failStream("Upstream returned an empty OpenAI stream.");
        cleanup();
        closeController();
        return;
      }
      pump();
    },
    cancel() {
      cancelled = true;
      cleanup();
      return reader?.cancel();
    }
  });

  async function pump() {
    try {
      while (!cancelled && reader && !state.upstreamDone && !state.failed) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        buffer = drain(buffer);
      }
      buffer += decoder.decode();
      if (!state.failed && buffer) buffer = drain(buffer + "\n\n");
      if (!state.failed && state.upstreamDone) {
        await reader.cancel();
        finishStream();
      } else if (!state.failed) {
        failStream("Upstream stream closed before [DONE].");
      }
    } catch (err) {
      if (!cancelled) failStream(err.message || "Upstream streaming error");
    } finally {
      cleanup();
      closeController();
    }
  }

  function cleanup() {
    if (heartbeat) {
      clearInterval(heartbeat);
      heartbeat = null;
    }
  }

  const headers = transformedHeaders(response, "text/event-stream");
  headers.set("Cache-Control", "no-cache");
  for (const [key, value] of Object.entries(corsHeaders())) headers.set(key, value);
  return new Response(stream, { status: response.status, statusText: response.statusText, headers });
}

// Terminal Anthropic events are far smaller than this; the cap stops a rogue
// upstream from forcing a large parse per event.
const INSPECT_DATA_LIMIT = 4096;

export function guardAnthropicStream(response) {
  const decoder = new TextDecoder();
  let reader = null;
  let cancelled = false;
  let controllerClosed = false;
  let pending = "";
  let sawMessageStop = false;
  let sawError = false;
  let sawEvent = false;

  function closeController(controller) {
    if (controllerClosed || cancelled) return;
    controllerClosed = true;
    controller.close();
  }

  function inspectEvent(eventText) {
    const { name, data } = parseSSEEvent(eventText);
    if (name || data != null) sawEvent = true;
    if (name === "message_stop") sawMessageStop = true;
    if (name === "error") sawError = true;
    if (data == null || data.length > INSPECT_DATA_LIMIT) return;
    // Terminal events are tiny and always carry one of these tokens, either in
    // an `event:` line or in the `"type"` field. Text deltas that merely mention
    // "error" cost one extra parse, but no terminal event is ever missed.
    if (eventText.indexOf("message_stop") === -1 && eventText.indexOf("error") === -1) return;
    try {
      const parsed = JSON.parse(data);
      if (parsed?.type === "message_stop") sawMessageStop = true;
      if (parsed?.type === "error") sawError = true;
    } catch {
      // Preserve malformed upstream data; the client can decide how to handle it.
    }
  }

  function inspect(chunk, flush = false) {
    pending += decoder.decode(chunk || new Uint8Array(), { stream: !flush });
    if (pending.indexOf("\r") !== -1) {
      pending = pending.replace(/\r\n/g, "\n").replace(/\r/g, "\n");
    }
    let index;
    while ((index = pending.indexOf("\n\n")) !== -1) {
      inspectEvent(pending.slice(0, index));
      pending = pending.slice(index + 2);
    }
    if (flush && pending) {
      inspectEvent(pending);
      pending = "";
    }
  }

  function errorBytes(message) {
    return encoder.encode(`event: error\ndata: ${JSON.stringify({
      type: "error",
      error: { type: "api_error", message }
    })}\n\n`);
  }

  const stream = new ReadableStream({
    start(controller) {
      reader = response.body?.getReader();
      if (!reader) {
        controller.enqueue(errorBytes("Upstream returned an empty Anthropic stream."));
        closeController(controller);
        return;
      }
      (async () => {
        try {
          while (!cancelled) {
            const { value, done } = await reader.read();
            if (done) break;
            // Once message_stop is seen the failure branch below can no longer
            // trigger, so scanning the rest of the stream is pure waste.
            if (!sawMessageStop) inspect(value);
            controller.enqueue(value);
          }
          if (!sawMessageStop) inspect(null, true);
          if (!sawError && !sawMessageStop && !cancelled) {
            controller.enqueue(errorBytes(
              sawEvent
                ? "Upstream closed before a terminal Anthropic message_stop event."
                : "Upstream returned an empty Anthropic stream."
            ));
          }
          closeController(controller);
        } catch (err) {
          if (!cancelled && !sawError) controller.enqueue(errorBytes(`Upstream stream failed: ${err.message}`));
          closeController(controller);
        }
      })();
    },
    cancel() {
      cancelled = true;
      return reader?.cancel();
    }
  });

  const headers = transformedHeaders(response, "text/event-stream");
  headers.set("Cache-Control", "no-cache");
  for (const [key, value] of Object.entries(corsHeaders())) headers.set(key, value);
  return new Response(stream, { status: response.status, statusText: response.statusText, headers });
}

export function parseSSEEvent(eventText) {
  let name = "";
  // A single `data:` line stays a string; only multi-line payloads allocate.
  let data = null;
  const length = eventText.length;
  let start = 0;
  while (start <= length) {
    let end = eventText.indexOf("\n", start);
    if (end === -1) end = length;
    if (eventText.charCodeAt(start) === 58) {
      // Comment line; ignored by spec.
    } else if (eventText.startsWith("event:", start)) {
      name = eventText.slice(start + 6, end).trim();
    } else if (eventText.startsWith("data:", start)) {
      const part = eventText.slice(start + 5, end).trimStart();
      if (data === null) data = part;
      else if (Array.isArray(data)) data.push(part);
      else data = [data, part];
    }
    if (end === length) break;
    start = end + 1;
  }
  if (data === null) return { name, data: null };
  return { name, data: (Array.isArray(data) ? data.join("\n") : data).trim() };
}
