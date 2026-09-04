# Dexi for Hermes Agent

Your [Dexi](https://dexi.net) notes library — clipped web pages, emailed articles, RSS entries, and typed notes — as [Hermes Agent](https://github.com/NousResearch/hermes-agent)'s long-term memory.

Two ways to use it, pick one:

| | MCP server (`mcp_servers.dexi`) | **Memory provider (this plugin)** |
|---|---|---|
| Tools | all fourteen Dexi tools as `mcp_dexi_*` | compact `dexi_*` set (search / get / list / save / append / tags / folders / bases / reviews) |
| Auto-recall before each turn (`<dexi-context>`) | — | ✅ |
| System-prompt guidance | — | ✅ |
| Session digest note (opt-in) | — | ✅ |
| Skills (`dexi:capture` / `dexi:recall` / `dexi:review`) | — | ✅ |
| Works alongside another memory provider | ✅ | ✗ (Hermes allows one external provider) |
| Auth | OAuth, browser or paste-back | same — shares the token cache |

The MCP-server route needs no install: see [dexi.net/mcp/hermes](https://dexi.net/mcp/hermes). Read on for the provider.

## Install

```bash
hermes plugins install dexi/hermes-plugin --enable
hermes memory setup            # pick "dexi"; or set memory.provider: dexi in ~/.hermes/config.yaml
hermes dexi login              # one-time OAuth: opens a browser, or paste the redirect URL back
hermes memory status
```

Or as a pip package (entry-point discovery, e.g. inside a container image):

```bash
pip install git+https://github.com/dexi/hermes-plugin.git
```

Then in `~/.hermes/config.yaml`:

```yaml
memory:
  provider: dexi
```

Requires `mcp>=1.26,<2` and `httpx` (both already present in a Hermes install with the `mcp` extra).

### Headless / VPS

`hermes dexi login` prints the authorize URL. Open it anywhere, sign in to Dexi, approve (you can restrict the connection to one folder or tag on that page), and paste the final redirect URL — or just its `?code=…&state=…` part — back into the terminal. Tokens are cached under `~/.hermes/mcp-tokens/dexi*.json`; a gateway started later reuses them silently. If you already have an `mcp_servers.dexi` entry, the provider reuses its token — no second approval.

## What it does

- **`prefetch`** — before each non-trivial turn, semantic search over your notes for the incoming message (plus a keyword pass when it contains a `#hashtag` or a "quoted phrase"); hits above the similarity floor are injected as a `<dexi-context>` block of titles + snippets. Never full bodies — the model calls `dexi_get`/`full_text` when it wants one. Best-effort, ~2.5 s budget, failures inject nothing.
- **Tools** — `dexi_search` (hybrid keyword+semantic, `full_text` option), `dexi_get`, `dexi_list` (source/tag/folder/period/`since`), `dexi_save`, `dexi_append`, `dexi_tags`, `dexi_folders`, `dexi_bases` + `dexi_query_base` (the user's saved database views over `key:: value` properties), `dexi_reviews_due`, `dexi_review_grade`. Each forwards to Dexi's MCP tool of the same purpose; each accepts an optional `intent` sentence for Dexi's aggregate tool analytics.
- **Session digest** (off by default) — one note per session, written at session end / switch / pre-compress / shutdown, never per turn: the questions asked, the last answer, and the session id, tagged `#hermes`. Deterministic; no LLM call in the plugin. Idempotent per session.
- **Not done, on purpose** — no note per turn, no mirroring of `MEMORY.md`/`USER.md`, no o2b-style rules/provenance/rollback. Dexi is your notes app; the agent is a reader and an occasional, deliberate writer.

## What leaves your device

Only when the corresponding feature runs:

| Feature | Data sent to `mcp.dexi.net` |
|---|---|
| Auto-recall (`auto_recall`, default on) | the current user message (≤500 chars) as a search query |
| Tools | the arguments the model passes (query text, note text you asked it to save) |
| Session digest (`session_digest`, default **off**) | your session's user messages (first ~240 chars each, up to 12) + the last answer (~1,200 chars) |

Nothing else — no full transcripts, no `MEMORY.md`, no tool call history. Everything is under your Dexi account, visible in the app, deletable there. Set `read_only: true` to make the connection incapable of writing at all (Hermes requests only the `notes:read` scope; the write tools disappear).

## Configuration

`~/.hermes/dexi.json` (created by `hermes memory setup`; every key optional; `DEXI_<KEY>` env vars override):

| Key | Default | Meaning |
|---|---|---|
| `auto_recall` | `true` | inject relevant notes before each turn |
| `recall_results` | `5` | max notes injected |
| `recall_min_similarity` | `0.55` | semantic similarity floor (0–1) |
| `session_digest` | `false` | write one digest note per session |
| `digest_tag` | `#hermes` | tag on digest notes (`\w` chars only — Dexi's hashtag syntax) |
| `read_only` | `false` | request `notes:read` only; hide write tools |
| `prefetch_timeout` | `2.5` | seconds; recall is skipped past this |
| `tool_timeout` | `30` | seconds per explicit tool call |
| `mcp_url` | `https://mcp.dexi.net/mcp` | override for self-hosted/dev |

CLI (only while `memory.provider` is `dexi`): `hermes dexi status` (config + live probe), `hermes dexi login`, `hermes dexi logout`, `hermes dexi config`.

Manage or revoke the grant itself in Dexi → **Settings → Connected apps** — you can also narrow it to one folder/tag there; the change applies on the next request.

## Development

```bash
pip install -e ".[dev]"
pytest                                # provider driven through the Hermes hook surface with a fake bridge
DEXI_MCP_BEARER_TOKEN=dxm_… DEXI_MCP_URL=http://localhost:8001/mcp python -c '…'   # real bridge, no Hermes
```

Layout: `hermes_dexi/provider.py` (the `MemoryProvider`), `bridge.py` (streamable-HTTP MCP client on a background loop, OAuth via Hermes' `tools.mcp_oauth_manager`), `schemas.py` (static tool schemas — Hermes reads them before `initialize()`), `digest.py`, `config.py`, `cli.py`; root `__init__.py`/`cli.py`/`plugin.yaml` are the shims Hermes loads from a cloned plugin dir; `skills/` are the three plugin skills.

Source of truth is `hermes-plugin/` in the private `dexi/dexi` monorepo; this repository is a mirror. Tool docs: [docs.dexi.net/mcp/tools](https://docs.dexi.net/mcp/tools). MIT licensed.
