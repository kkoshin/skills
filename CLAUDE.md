# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

A collection of Claude Code **skills**, each living under `skills/<skill-name>/`. Skills are not a buildable application — there is no build step, test suite, or linter. A skill is a packaged set of instructions plus copyable artifacts that Claude Code loads on demand.

When a skill is invoked, Claude reads its `SKILL.md` and follows the workflow described there. The prose (mostly Chinese) is the product; treat edits to `SKILL.md` and `references/*.md` as user-facing documentation.

## Skill layout

Every skill follows this structure:

- `SKILL.md` — entry point. Must start with frontmatter `name` + `description` (the description is what Claude uses to decide when to load the skill, so keep it specific and trigger-rich). The body is the workflow, written as numbered steps that reference the files below rather than inlining everything.
- `README.md` — per-skill README for humans (rendered on GitHub and skills.sh): what the skill does, install, structure, license. The `SKILL.md` remains the machine-facing entry point.
- `references/*.md` — deeper docs linked from `SKILL.md` (configuration, deployment, client-integration, troubleshooting). Split by concern; `SKILL.md` stays scannable.
- `scripts/*` — executable helpers, runnable standalone (`bash <skill-dir>/scripts/<name>.sh` for `.sh`; `python3 <skill-dir>/scripts/<name>.py` for `.py`). Shell scripts `set -euo pipefail` and validate their own preconditions (deps present, files present, args provided). Python helpers are stdlib-only and read credentials from the environment, never from a file in the skill dir.
- `templates/*` — optional; files the user copies into their own project (for cloudflare-ai-proxy: a multi-module Worker under `src/`, plus `wrangler.toml`, `package.json`, `test/`). These contain **placeholders, never real secrets**.

## The cloudflare-ai-proxy skill

Deploys a Cloudflare Worker that puts multiple upstream providers behind one **Anthropic Messages** endpoint with secret-token auth. The template is a multi-module Worker under `templates/src/`, mirroring the layout of the `anthropic-api` repo it was derived from; all configuration lives in `templates/wrangler.toml` `[vars]` and is read via `env`.

- **Downstream is Anthropic-only.** `POST /v1/messages` is the sole inference route; `/v1/chat/completions` returns **404**. OpenAI compatibility exists only as an *upstream* protocol.
- **Routing via the `model` field**: `"model:provider"` picks a channel explicitly; bare `"model"` falls back to `DEFAULT_FALLBACK`. Array order in `MODELS` never triggers failover.
- **Protocol conversion**: a channel with `protocol = "openai"` gets its Anthropic request converted to OpenAI Chat Completions and its response/SSE converted back (`src/protocol.js`, `src/streaming.js`). `protocol = "anthropic"` (default) passes through.
- **Aliases** live in `MODEL_ALIASES` — a separate table from `DEFAULT_FALLBACK`, with exactly one `*` allowed per key. Exact beats glob; longer glob pattern beats shorter; ties go to declaration order. The template ships **no** built-in aliases, so an unconfigured `MODEL_ALIASES` means Claude model names 400.
- **The model name is lowercased before being sent upstream**, so an upstream whose model ids are case-sensitive cannot be expressed — a real limitation, documented rather than hidden.
- **`/v1/models`** lists `MODELS` keys plus `DEFAULT_FALLBACK` keys absent from `MODELS`. `MODEL_ALIASES` entries are deliberately **not** listed.
- **Config is validated per request, not at deploy time** — a bad `token_ref` deploys fine and only 500s when a request routes to that model. Errors are 400/403/500/502 and 500s never leak variable values.

The shell scripts read the wrangler OAuth token and Account ID from the wrangler config file (`~/Library/Preferences/.wrangler/config/default.toml` on macOS, with Linux fallbacks) and call the Cloudflare API directly via `curl` + `python3` for JSON parsing — they do not shell out to `wrangler` for those calls.

## Conventions when editing

- Keep docs in the existing language (Chinese for this repo's prose).
- Never hardcode real API keys, worker names, or subdomains in templates or `SKILL.md` — always placeholders. The `Do not` section at the bottom of `SKILL.md` states this explicitly.
- When changing template behavior (`templates/src/*`), update `SKILL.md`, `references/configuration.md`, and `references/troubleshooting.md` together — the `[vars]` contract and the alias-precedence rules in particular are described in all three.