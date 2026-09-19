export class ConfigError extends Error {
  constructor(message) {
    super(message);
    this.name = "ConfigError";
  }
}

// OpenAI 上游的能力开关住在 src/protocol.js 的 OPENAI_CAPABILITIES —— 模板有意
// 不把它做成配置项，所以这里不再有 default capabilities 表。

const configCache = new WeakMap();
const ALLOWED_PROTOCOLS = new Set(["anthropic", "openai"]);
const ALLOWED_AUTH_TYPES = new Set(["bearer", "api-key"]);

export function loadConfig(env) {
  if (env && typeof env === "object" && configCache.has(env)) {
    return configCache.get(env);
  }

  const providers = jsonParse("PROVIDERS", env.PROVIDERS);
  const models = jsonParse("MODELS", env.MODELS);
  const fallback = jsonParse("DEFAULT_FALLBACK", env.DEFAULT_FALLBACK);
  // 模板没有内置别名表：MODEL_ALIASES 不配置就是空表。这样就不会出现
  // "别名指向一个本部署并不存在的模型" 这种幽灵路由。
  const aliases = jsonParseOptional("MODEL_ALIASES", env.MODEL_ALIASES, {});

  const config = { providers, models, fallback, aliases };
  validateConfig(config);

  // Everything below is derived purely from the validated config, and
  // loadConfig is already cached per env, so building it once keeps the
  // per-request path to a Map lookup instead of re-walking the alias table
  // and recompiling glob regexes on every call.
  config.aliasIndex = buildAliasIndex(aliases);
  config.entriesByModel = buildRouteIndex(providers, models);

  if (typeof env.SECRET_TOKEN !== "string" || env.SECRET_TOKEN.trim() === "") {
    throw new ConfigError("Secret SECRET_TOKEN is not configured");
  }

  if (env && typeof env === "object") configCache.set(env, config);
  return config;
}

function jsonParseOptional(name, raw, defaultValue) {
  if (raw == null || (typeof raw === "string" && raw.trim() === "")) return defaultValue;
  return jsonParse(name, raw);
}

function jsonParse(name, raw) {
  // JSON-typed bindings arrive as objects; plain-text bindings are parsed here.
  if (typeof raw === "object" && raw !== null) return raw;
  if (typeof raw !== "string" || raw.trim() === "") {
    throw new ConfigError(`Env var ${name} is missing or empty`);
  }
  try {
    return JSON.parse(raw);
  } catch {
    throw new ConfigError(`Env var ${name} is not valid JSON`);
  }
}

function validateConfig(config) {
  assertRecord("PROVIDERS", config.providers);
  assertRecord("MODELS", config.models);
  assertRecord("DEFAULT_FALLBACK", config.fallback);
  assertRecord("MODEL_ALIASES", config.aliases);

  for (const [providerId, provider] of Object.entries(config.providers)) {
    if (!providerId.trim()) throw new ConfigError("Provider id must not be empty");
    if (!isRecord(provider)) throw new ConfigError(`Provider "${providerId}" must be an object`);
    if (typeof provider.baseUrl !== "string" || provider.baseUrl.trim() === "") {
      throw new ConfigError(`Provider "${providerId}" must define a non-empty baseUrl`);
    }
    try {
      const baseUrl = new URL(provider.baseUrl);
      if (!/^https?:$/.test(baseUrl.protocol) || baseUrl.search || baseUrl.hash) {
        throw new Error("must be an absolute HTTP(S) URL without query or hash");
      }
    } catch (err) {
      throw new ConfigError(`Provider "${providerId}" has an invalid baseUrl: ${err.message}`);
    }
    validateProtocol(provider.protocol, `Provider "${providerId}"`);
    validateAuthType(provider.authType, `Provider "${providerId}"`);
    validateOptionalString(provider.key_ref, `Provider "${providerId}" key_ref`);
    validateOptionalString(provider.apiKey, `Provider "${providerId}" apiKey`);
  }

  for (const [modelId, entries] of Object.entries(config.models)) {
    if (!modelId.trim()) throw new ConfigError("Model id must not be empty");
    if (!Array.isArray(entries) || entries.length === 0) {
      throw new ConfigError(`Model "${modelId}" must have at least one provider entry`);
    }
    const seenProviders = new Set();
    for (const entry of entries) {
      if (!isRecord(entry)) throw new ConfigError(`Model "${modelId}" entries must be objects`);
      if (typeof entry.provider_id !== "string" || entry.provider_id.trim() === "") {
        throw new ConfigError(`Model "${modelId}" has an invalid provider_id`);
      }
      const providerId = entry.provider_id.trim();
      const providerKey = findProviderKey(config.providers, providerId);
      if (!providerKey) {
        throw new ConfigError(`Model "${modelId}" references unknown provider "${providerId}"`);
      }
      const providerKeyLower = providerKey.toLowerCase();
      if (seenProviders.has(providerKeyLower)) {
        throw new ConfigError(`Model "${modelId}" contains duplicate provider "${providerId}"`);
      }
      seenProviders.add(providerKeyLower);
      validateProtocol(entry.protocol, `Model "${modelId}" provider "${providerId}"`);
      validateOptionalString(entry.token_ref, `Model "${modelId}" token_ref`);
      validateOptionalString(entry.auth_token, `Model "${modelId}" auth_token`);
      validateOptionalString(entry.api_key, `Model "${modelId}" api_key`);
    }
  }

  for (const [modelId, target] of Object.entries(config.fallback)) {
    const parsed = parseRouteTarget(target, `DEFAULT_FALLBACK.${modelId}`);
    if (!config.models[parsed.model]) {
      throw new ConfigError(`DEFAULT_FALLBACK.${modelId} references unknown model "${parsed.model}"`);
    }
    if (!hasProvider(config.models[parsed.model], parsed.provider)) {
      throw new ConfigError(`DEFAULT_FALLBACK.${modelId} references unavailable provider "${parsed.provider}"`);
    }
  }

  for (const [pattern, target] of Object.entries(config.aliases)) {
    if (!pattern.trim()) throw new ConfigError("MODEL_ALIASES contains an empty pattern");
    if ((pattern.match(/\*/g) || []).length > 1) {
      throw new ConfigError(`MODEL_ALIASES pattern "${pattern}" may contain only one *`);
    }
    const parsed = parseAliasTarget(target, `MODEL_ALIASES.${pattern}`);
    // 别名一律严格校验：模板没有内置表，任何一条别名都是用户写的，
    // 指向不存在的模型属于配置错误，应该在加载时就报出来。
    if (!config.models[parsed.model]) {
      throw new ConfigError(`MODEL_ALIASES.${pattern} references unknown model "${parsed.model}"`);
    }
    if (parsed.provider && !hasProvider(config.models[parsed.model], parsed.provider)) {
      throw new ConfigError(`MODEL_ALIASES.${pattern} references unavailable provider "${parsed.provider}"`);
    }
  }
}

function assertRecord(name, value) {
  if (!isRecord(value)) throw new ConfigError(`Env var ${name} must be a JSON object`);
}

function isRecord(value) {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function validateOptionalString(value, label) {
  if (value != null && (typeof value !== "string" || value.trim() === "")) {
    throw new ConfigError(`${label} must be a non-empty string`);
  }
}

function validateProtocol(value, label) {
  if (value != null && (typeof value !== "string" || !ALLOWED_PROTOCOLS.has(value))) {
    throw new ConfigError(`${label} protocol must be "anthropic" or "openai"`);
  }
}

function validateAuthType(value, label) {
  if (value != null && (typeof value !== "string" || !ALLOWED_AUTH_TYPES.has(value))) {
    throw new ConfigError(`${label} authType must be "bearer" or "api-key"`);
  }
}

function parseRouteTarget(raw, label) {
  if (typeof raw !== "string" || raw.trim() === "") {
    throw new ConfigError(`${label} must be formatted as model:provider`);
  }
  const parts = raw.trim().split(":");
  if (parts.length !== 2 || !parts[0].trim() || !parts[1].trim()) {
    throw new ConfigError(`${label} must be formatted as model:provider`);
  }
  return { model: parts[0].trim().toLowerCase(), provider: parts[1].trim().toLowerCase() };
}

function parseAliasTarget(raw, label) {
  if (typeof raw !== "string" || raw.trim() === "") {
    throw new ConfigError(`${label} must be a model or model:provider`);
  }
  const parts = raw.trim().split(":");
  if (parts.length > 2 || !parts[0].trim() || (parts.length === 2 && !parts[1].trim())) {
    throw new ConfigError(`${label} must be a model or model:provider`);
  }
  return {
    model: parts[0].trim().toLowerCase(),
    provider: parts.length === 2 ? parts[1].trim().toLowerCase() : null
  };
}

function findProviderKey(providers, providerId) {
  return Object.keys(providers).find((key) => key.toLowerCase() === providerId.trim().toLowerCase());
}

function hasProvider(entries, providerId) {
  return entries.some((entry) => entry.provider_id.toLowerCase() === providerId.toLowerCase());
}

export function resolveRoute(env, config, modelField) {
  if (typeof modelField !== "string" || modelField.trim() === "") {
    return { error: `Invalid "model" field; expected a non-empty string.` };
  }
  const parts = modelField.trim().split(":");
  if (parts.length > 2 || !parts[0].trim()) {
    return { error: `Invalid model route "${modelField}"; expected model or model:provider.` };
  }

  const requestedModel = parts[0].trim().toLowerCase();
  const baseModel = requestedModel.replace(/\[[^\]]+\]$/, "");
  const providerId = parts[1]?.replace(/\[[^\]]+\]$/, "").trim().toLowerCase();
  const alias = resolveModelAlias(config.aliasIndex, requestedModel)
    || resolveModelAlias(config.aliasIndex, baseModel);
  const modelName = alias?.model || (config.models[requestedModel] ? requestedModel : baseModel);

  let resolvedProvider = providerId || alias?.provider;
  if (!resolvedProvider) {
    const fallback = config.fallback[modelName];
    if (!fallback) {
      return { error: `Unknown model "${modelName}". No fallback configured.` };
    }
    resolvedProvider = parseRouteTarget(fallback, `DEFAULT_FALLBACK.${modelName}`).provider;
  }

  const modelConfig = config.models[modelName];
  if (!modelConfig) return { error: `Unknown model "${modelName}".` };
  const entry = config.entriesByModel?.get(modelName)?.get(resolvedProvider)
    || modelConfig.find((candidate) => candidate.provider_id.toLowerCase() === resolvedProvider);
  if (!entry) {
    const available = modelConfig.map((candidate) => candidate.provider_id).join(", ");
    return {
      error: `Provider "${resolvedProvider}" is not available for model "${modelName}". Available: ${available}`
    };
  }

  const providerKey = findProviderKey(config.providers, entry.provider_id);
  const provider = providerKey ? config.providers[providerKey] : null;
  if (!provider) return { error: `Unknown provider "${entry.provider_id}".` };

  return {
    providerId: providerKey,
    baseUrl: provider.baseUrl,
    apiKey: resolveCredential(env, entry, provider),
    authType: provider.authType || "bearer",
    modelName,
    upstreamProtocol: entry.protocol || provider.protocol || "anthropic"
  };
}

// Patterns are compiled once here instead of on every request. Aliases are
// already known-valid: validateConfig parses every target at load time.
function buildAliasIndex(aliases) {
  const exact = new Map();
  const globs = [];
  if (!isRecord(aliases)) return { exact, globs };
  for (const [pattern, target] of Object.entries(aliases)) {
    const parsed = parseAliasTarget(target, `MODEL_ALIASES.${pattern}`);
    const entry = { model: parsed.model, provider: parsed.provider };
    if (pattern.includes("*")) {
      // Score on the original pattern, matching the previous behaviour.
      globs.push({ pattern, score: pattern.replaceAll("*", "").length, regex: compileGlob(pattern.toLowerCase()), entry });
    } else if (!exact.has(pattern.toLowerCase())) {
      exact.set(pattern.toLowerCase(), entry);
    }
  }
  // Highest score wins; Array.prototype.sort is stable, so ties keep the
  // declaration order of MODEL_ALIASES.
  globs.sort((a, b) => b.score - a.score);
  return { exact, globs };
}

function compileGlob(lowerPattern) {
  const escaped = lowerPattern.replace(/[.+?^${}()|[\]\\]/g, "\\$&");
  return new RegExp(`^${escaped.replaceAll("*", ".*")}$`);
}

function resolveModelAlias(index, requestedModel) {
  if (!index) return null;
  const hit = index.exact.get(requestedModel);
  if (hit) return hit;
  for (const glob of index.globs) {
    if (glob.regex.test(requestedModel)) return glob.entry;
  }
  return null;
}

// 模型 -> (渠道 id -> 条目) 的索引。建一次，之后 resolveRoute 的渠道查找
// 就是一次 Map 查询，而不是每次请求线性扫描渠道数组。
function buildRouteIndex(providers, models) {
  const entriesByModel = new Map();
  for (const [modelName, entries] of Object.entries(models)) {
    if (!Array.isArray(entries)) continue;
    const byProvider = new Map();
    for (const entry of entries) {
      if (!isRecord(entry) || typeof entry.provider_id !== "string") continue;
      if (!findProviderKey(providers, entry.provider_id)) continue;
      byProvider.set(entry.provider_id.toLowerCase(), entry);
    }
    entriesByModel.set(modelName, byProvider);
  }
  return entriesByModel;
}

// Strict on *_ref (missing secret -> loud 500); legacy literal fields are only
// used when no *_ref is present.
function resolveCredential(env, entry, provider) {
  if (entry.token_ref != null) {
    const v = env[entry.token_ref];
    if (typeof v !== "string" || v.trim() === "") {
      throw new ConfigError(`Secret "${entry.token_ref}" (referenced by token_ref) is not configured`);
    }
    return v;
  }
  if (entry.auth_token || entry.api_key) return entry.auth_token || entry.api_key;
  if (provider.key_ref != null) {
    const v = env[provider.key_ref];
    if (typeof v !== "string" || v.trim() === "") {
      throw new ConfigError(`Secret "${provider.key_ref}" (referenced by key_ref) is not configured`);
    }
    return v;
  }
  if (provider.apiKey) return provider.apiKey;
  throw new ConfigError(`Missing credential for provider "${entry.provider_id}"`);
}
