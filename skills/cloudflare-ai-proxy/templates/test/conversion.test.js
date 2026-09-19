/**
 * 协议转换回归测试。
 *
 * 只盖三件事：Anthropic → OpenAI 的请求转换、OpenAI → Anthropic 的响应转换、
 * 以及流式 SSE 的翻译。这三处是最容易在改动中悄悄坏掉的地方，也是这个模板
 * 相比"纯转发"多出来的全部价值。
 *
 * 运行：npm test（或 npm run check，它包含语法检查）
 */
import test from "node:test";
import assert from "node:assert/strict";

import {
  OPENAI_CAPABILITIES,
  anthropicRequestToOpenAI,
  openAIResponseToAnthropic
} from "../src/protocol.js";
import { openAIStreamToAnthropic } from "../src/streaming.js";

// ── 夹具 ────────────────────────────────────────────────────────

// 每个 chunk 都必须带 object: "chat.completion.chunk"，否则翻译层会当作噪音跳过
// （见 src/streaming.js 的 handleChunk）。这里统一补上，夹具里只写 choices。
function chunk(choice) {
  return { object: "chat.completion.chunk", choices: [choice] };
}

function sseResponse(events) {
  const text = events.map((event) => {
    if (typeof event === "string") return event;
    const name = event.event ? `event: ${event.event}\n` : "";
    const payload = Object.prototype.hasOwnProperty.call(event, "data") ? event.data : event;
    return `${name}data: ${JSON.stringify(payload)}\n\n`;
  }).join("");
  return new Response(text, {
    status: 200,
    headers: { "Content-Type": "text/event-stream" }
  });
}

async function eventsOf(response) {
  const text = await response.text();
  return text.trim().split("\n\n").filter(Boolean).map((chunk) => {
    const event = chunk.match(/^event: (.+)$/m)?.[1] || null;
    const data = chunk.match(/^data: (.+)$/m)?.[1];
    return { event, data: data ? JSON.parse(data) : null };
  });
}

const route = { providerId: "provider-b", modelName: "model-a" };

// ── 请求转换：Anthropic → OpenAI ────────────────────────────────

test("把 system / stop_sequences / tools 转成 OpenAI 形状", () => {
  const oai = anthropicRequestToOpenAI({
    model: "model-a",
    system: "be brief",
    max_tokens: 64,
    temperature: 0.5,
    stop_sequences: ["STOP"],
    tool_choice: { type: "any" },
    parallel_tool_calls: false,
    messages: [{ role: "user", content: "hi" }],
    tools: [{
      name: "Read",
      description: "read a file",
      input_schema: { type: "object", properties: { path: { type: "string" } } }
    }]
  });

  assert.equal(oai.model, "model-a");
  assert.equal(oai.max_tokens, 64);
  assert.equal(oai.temperature, 0.5);
  assert.deepEqual(oai.stop, ["STOP"]);
  assert.equal(oai.tool_choice, "required", "Anthropic 的 any 对应 OpenAI 的 required");
  assert.equal(oai.parallel_tool_calls, false);
  assert.deepEqual(oai.messages[0], { role: "system", content: "be brief" });
  assert.equal(oai.tools[0].type, "function");
  assert.equal(oai.tools[0].function.name, "Read");
  assert.deepEqual(oai.tools[0].function.parameters.properties, { path: { type: "string" } });
});

test("tool_result 拆成独立的 role:tool 消息，图片转成 data URL", () => {
  const oai = anthropicRequestToOpenAI({
    model: "model-a",
    max_tokens: 64,
    messages: [
      {
        role: "user",
        content: [
          { type: "text", text: "look" },
          { type: "image", source: { type: "base64", media_type: "image/png", data: "AAAA" } }
        ]
      },
      {
        role: "assistant",
        content: [{ type: "tool_use", id: "call_1", name: "Read", input: { path: "a" } }]
      },
      {
        role: "user",
        content: [{ type: "tool_result", tool_use_id: "call_1", content: "file contents" }]
      }
    ]
  });

  const userWithImage = oai.messages[0];
  assert.equal(userWithImage.content[0].type, "text");
  assert.equal(userWithImage.content[1].type, "image_url");
  assert.equal(userWithImage.content[1].image_url.url, "data:image/png;base64,AAAA");

  const assistant = oai.messages[1];
  assert.equal(assistant.tool_calls[0].function.name, "Read");
  assert.equal(assistant.tool_calls[0].function.arguments, '{"path":"a"}');

  assert.deepEqual(oai.messages[2], {
    role: "tool",
    tool_call_id: "call_1",
    content: "file contents"
  });
});

test("默认不向上游回放历史 thinking（replayRequired 为 false）", () => {
  assert.equal(OPENAI_CAPABILITIES.reasoning.replayRequired, false);
  const oai = anthropicRequestToOpenAI({
    model: "model-a",
    max_tokens: 64,
    messages: [{
      role: "assistant",
      content: [
        { type: "thinking", thinking: "hidden chain" },
        { type: "text", text: "answer" }
      ]
    }]
  });
  const assistant = oai.messages[0];
  assert.equal(assistant.content, "answer");
  assert.equal(assistant.reasoning_content, undefined, "默认不把历史思考写回上游");
});

// ── 响应转换：OpenAI → Anthropic ────────────────────────────────

test("把 OpenAI 响应转回 Anthropic 消息，并映射 stop_reason 与 usage", () => {
  const message = openAIResponseToAnthropic({
    choices: [{ finish_reason: "length", message: { content: "hello" } }],
    usage: { prompt_tokens: 7, completion_tokens: 3 }
  }, route, { thinkingEnabled: false });

  assert.equal(message.type, "message");
  assert.equal(message.role, "assistant");
  assert.equal(message.model, "model-a");
  assert.deepEqual(message.content, [{ type: "text", text: "hello" }]);
  assert.equal(message.stop_reason, "max_tokens");
  assert.deepEqual(message.usage, { input_tokens: 7, output_tokens: 3 });
});

test("tool_calls 转成 tool_use 块，arguments 解析成对象", () => {
  const message = openAIResponseToAnthropic({
    choices: [{
      finish_reason: "tool_calls",
      message: {
        content: null,
        tool_calls: [{ id: "call_9", type: "function", function: { name: "Read", arguments: '{"path":"b"}' } }]
      }
    }]
  }, route, { thinkingEnabled: false });

  assert.deepEqual(message.content, [
    { type: "tool_use", id: "call_9", name: "Read", input: { path: "b" } }
  ]);
  assert.equal(message.stop_reason, "tool_use");
});

// ── 流式翻译：OpenAI SSE → Anthropic SSE ────────────────────────

test("把 OpenAI 文本流翻译成完整的 Anthropic 事件序列", async () => {
  const upstream = sseResponse([
    chunk({ delta: { content: "Hel" } }),
    chunk({ delta: { content: "lo" } }),
    chunk({ delta: {}, finish_reason: "stop" }),
    "data: [DONE]\n\n"
  ]);

  const events = await eventsOf(openAIStreamToAnthropic(upstream, route, { thinkingEnabled: false }));
  const names = events.map((e) => e.event);

  assert.equal(names[0], "message_start");
  assert.equal(names[names.length - 1], "message_stop");
  assert.ok(names.includes("content_block_start"));
  assert.ok(names.includes("content_block_delta"));
  assert.ok(names.includes("content_block_stop"));

  const text = events
    .filter((e) => e.event === "content_block_delta")
    .map((e) => e.data.delta.text)
    .join("");
  assert.equal(text, "Hello");

  const delta = events.find((e) => e.event === "message_delta");
  assert.equal(delta.data.delta.stop_reason, "end_turn");
});

test("工具调用被缓冲后按顺序作为 tool_use 块输出", async () => {
  const upstream = sseResponse([
    chunk({ delta: { tool_calls: [{ index: 0, id: "call_1", function: { name: "Read", arguments: '{"pa' } }] } }),
    chunk({ delta: { tool_calls: [{ index: 0, function: { arguments: 'th":"c"}' } }] } }),
    chunk({ delta: {}, finish_reason: "tool_calls" }),
    "data: [DONE]\n\n"
  ]);

  const events = await eventsOf(openAIStreamToAnthropic(upstream, route, { thinkingEnabled: false }));
  const start = events.find((e) => e.event === "content_block_start");
  assert.equal(start.data.content_block.type, "tool_use");
  assert.equal(start.data.content_block.name, "Read");

  const json = events
    .filter((e) => e.event === "content_block_delta" && e.data.delta.type === "input_json_delta")
    .map((e) => e.data.delta.partial_json)
    .join("");
  assert.deepEqual(JSON.parse(json), { path: "c" });
});

test("上游流被截断时报 error 事件，而不是静默结束", async () => {
  const upstream = sseResponse([chunk({ delta: { content: "partial" } })]);

  const events = await eventsOf(openAIStreamToAnthropic(upstream, route, { thinkingEnabled: false }));
  const error = events.find((e) => e.event === "error");
  assert.ok(error, "没有 [DONE] 的流必须以 error 事件结束");
  assert.equal(error.data.type, "error");
});
