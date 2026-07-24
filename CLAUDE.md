# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

A collection of Claude Code **skills**, each living under `skills/<skill-name>/`. Skills are not a buildable application — there is no build step, test suite, or linter. A skill is a packaged set of instructions plus copyable artifacts that Claude Code loads on demand.

When a skill is invoked, Claude reads its `SKILL.md` and follows the workflow described there. The prose (mostly Chinese) is the product; treat edits to `SKILL.md` and `references/*.md` as user-facing documentation.

## Skill layout

Every skill follows this structure:

- `SKILL.md` — entry point. Must start with frontmatter `name` + `description` (the description is what Claude uses to decide when to load the skill, so keep it specific and trigger-rich). The body is the workflow, written as numbered steps that reference the files below rather than inlining everything.
- `references/*.md` — deeper docs linked from `SKILL.md` (configuration, deployment, client-integration, troubleshooting). Split by concern; `SKILL.md` stays scannable.
- `scripts/*.sh` — executable helpers, runnable standalone with `bash <skill-dir>/scripts/<name>.sh`. They `set -euo pipefail` and validate their own preconditions (deps present, files present, args provided).
- `templates/*` — files the user copies into their own project (`worker-proxy.js`, `wrangler.jsonc`). These contain **placeholders, never real secrets**.

## The cloudflare-ai-proxy skill

Deploys a Cloudflare Worker that proxies AI API requests to multiple upstream providers behind a single secret-token-authenticated endpoint. Core mechanics, all defined in `templates/worker-proxy.js`:

- **Routing via the `model` field**: `"model:provider"` picks a channel explicitly; bare `"model"` falls back to `DEFAULT_FALLBACK`.
- **Aliases**: a `DEFAULT_FALLBACK` key not present in `MODELS` is an alias (e.g. `Model-A` → `model-a:provider-a`). `resolveRoute` lowercases the model name and `proxyRequest` rewrites `body.model` to the canonical name before forwarding — upstream providers are case-sensitive, so this rewrite is load-bearing.
- **`/v1/models`** returns a payload that carries both OpenAI and Anthropic fields so either client format can parse it.
- Requests **must** hit `/v1/messages` (Anthropic) or `/v1/chat/completions` (OpenAI) — hitting `/` passes through to the upstream root and returns its homepage. This is the single most common failure and is documented in `references/troubleshooting.md`.

The shell scripts (`register-subdomain.sh`, `pull-remote.sh`) read the wrangler OAuth token and Account ID from the wrangler config file (`~/Library/Preferences/.wrangler/config/default.toml` on macOS, with Linux fallbacks) and call the Cloudflare API directly via `curl` + `python3` for JSON parsing — they do not shell out to `wrangler` for those calls.

## Conventions when editing

- Keep docs in the existing language (Chinese for this repo's prose).
- Never hardcode real API keys, worker names, or subdomains in templates or `SKILL.md` — always placeholders. The `Do not` section at the bottom of `SKILL.md` states this explicitly.
- When changing `worker-proxy.js` behavior, update `SKILL.md`, `references/configuration.md`, and `references/troubleshooting.md` together — the alias-rewrite contract in particular is described in all three.