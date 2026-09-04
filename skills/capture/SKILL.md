---
name: capture
description: Save something to the user's Dexi notes. Use when the user wants to save, capture, note down, or remember something — a finding, decision, code snippet, summary, or link — or says "add this to Dexi" / "save that to my notes".
---

# Capture to Dexi

Save the current finding, decision, or content the user pointed at as a Dexi note using the `dexi_save` tool.

How to write the note:

1. **Distill, don't dump.** A note should be the useful conclusion, not a transcript. For a debugging finding: the symptom, root cause, and fix. For a decision: what was decided and why. For research or a meeting: the takeaways, not the play-by-play. Short excerpts or code snippets are fine when they're the point; never paste pages of material.
2. **Title**: a short, specific noun phrase the user could find again later (e.g. "Railway healthchecks probe PORT, not the domain target port").
3. **Tags**: write 1–3 relevant `#hashtags` inline in the body — Dexi parses them into tags automatically. Reuse the user's existing vocabulary (call `dexi_tags` if unsure rather than inventing near-duplicates).
4. **Body**: plain text. `[[Wiki Links]]` to related note titles are supported and encouraged when you know a related note exists.
5. **Properties**: when the note has structured facts the user might filter on later (a status, a rating, a due date, an author), write them as `key:: value` lines at the start of the body — `status:: reading`, `due:: 2026-09-10`, `rating:: 4`. Dexi parses them into typed properties that bases can filter and sort. Reuse keys the user already has (`dexi_query_base` / `dexi_bases` show them) rather than inventing near-duplicates.
6. **Existing note on the topic?** Run `dexi_search` first; if one clearly matches, `dexi_append` to it instead of creating a near-duplicate.

Call `dexi_save` with the title and body, then confirm to the user with the note title and the `url` from the result.

If the call fails because the note limit was reached, relay the message and mention upgrading at https://dexi.net/pricing — don't retry.

On every Dexi tool call, pass the optional `intent` argument: one short sentence on what you are doing for the user (e.g. "save the deployment fix we found"). It never changes behavior — Dexi uses it in aggregate to improve the tools. Keep personal details out of it.
