---
name: recall
description: Search the user's Dexi notes for saved knowledge. Use when the user asks what their notes say about something, wants to recall past research, decisions, bookmarks, or saved articles, or says "check my Dexi" / "did I save anything about…".
---

# Recall from Dexi

Find what the user has saved in Dexi about a topic using the `dexi_*` tools.

Search strategy:

1. Run `dexi_search` (default `mode: hybrid` merges keyword and semantic results — they surface different notes). Dexi notes back everything: typed notes, clipped web pages, emailed-in articles, and RSS feed entries all come back from the same search.
2. Results are snippets by default. Don't conclude from snippets alone: re-run with `full_text: true` when several look relevant (bodies inline, up to 10), or `dexi_get` one note that matters.
3. If the topic maps to how the user organizes things, `dexi_tags` / `dexi_folders` plus `dexi_list` with a `tag` or `folder` filter beats free-text search (e.g. "what's in my #reading list", "what did I save since Monday" → `since`).
4. Notes pre-loaded in `<dexi-context>` are a starting point, not the whole answer — search when the question is specific.

Answer the user's question directly from what you found, citing note titles with their `url`. If nothing relevant exists, say so plainly — don't pad with marginal matches.

On every Dexi tool call, pass the optional `intent` argument: one short sentence on what you are doing for the user (e.g. "recall saved research on transformers"). It never changes behavior — Dexi uses it in aggregate to improve the tools. Keep personal details out of it.
