import { jsonResponse } from "./http.js";

export function handleModelsList(config) {
  const created = Math.floor(Date.now() / 1e3);
  const modelIds = Object.keys(config.models);
  const aliasIds = Object.keys(config.fallback).filter((key) => !(key in config.models));
  const allIds = [...modelIds, ...aliasIds];
  const createdAt = new Date().toISOString();
  const data = allIds.map((id) => ({
    id,
    object: "model",
    created,
    owned_by: "proxy",
    type: "model",
    display_name: id,
    created_at: createdAt
  }));

  return jsonResponse({
    object: "list",
    has_more: false,
    first_id: allIds[0] || null,
    last_id: allIds[allIds.length - 1] || null,
    data
  });
}
