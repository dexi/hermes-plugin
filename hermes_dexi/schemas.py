"""OpenAI-function-shaped schemas for the ``dexi_*`` tools.

Static on purpose: Hermes builds its tool routing table from
``get_tool_schemas()`` at provider registration, before ``initialize()`` —
so these can't depend on a live ``tools/list``. Each tool forwards to a Dexi
MCP tool (backend/app/mcp/tools.py); keep descriptions/params in sync with
that file and docs/mcp/tools.mdx.
"""
from __future__ import annotations

from typing import Any

INTENT = {
    "type": "string",
    "description": "One short sentence on what you're doing for the user (optional; "
    "helps improve Dexi's tools, never changes behavior; keep personal details out).",
}


def _tool(name: str, description: str, properties: dict[str, Any],
          required: list[str] | None = None) -> dict[str, Any]:
    props = dict(properties)
    props["intent"] = INTENT
    return {
        "name": name,
        "description": description,
        "parameters": {
            "type": "object",
            "properties": props,
            "required": list(required or []),
        },
    }


SEARCH = _tool(
    "dexi_search",
    "Search the user's Dexi notes — typed notes, clipped web pages/bookmarks, emailed "
    "articles, and RSS entries. mode=hybrid (default) merges keyword and semantic "
    "results; keyword = exact words; semantic = by meaning. Returns snippets by default; "
    "pass full_text=true (≤10 results) to read bodies inline instead of calling dexi_get "
    "per note.",
    {
        "query": {"type": "string", "description": "What to look for (words or a natural-language description)."},
        "mode": {"type": "string", "enum": ["hybrid", "keyword", "semantic"], "default": "hybrid"},
        "size": {"type": "integer", "minimum": 1, "maximum": 20, "default": 8},
        "full_text": {"type": "boolean", "default": False},
    },
    ["query"],
)

GET = _tool(
    "dexi_get",
    "Fetch one Dexi note in full (title, complete text, tags, source kind and URL) by id.",
    {"note_id": {"type": "string", "description": "Note UUID from a search/list result."}},
    ["note_id"],
)

LIST = _tool(
    "dexi_list",
    "Browse the user's Dexi notes newest-first with filters — for 'what did I save this "
    "week / since yesterday / in #reading / in the Research folder' questions. Use "
    "dexi_search for a topic.",
    {
        "source": {"type": "string", "enum": ["all", "bookmark", "email", "feed", "note"], "default": "all"},
        "tag": {"type": "string", "description": '"#hashtag", "@mention", or a bare word matching either.'},
        "folder": {"type": "string", "description": 'Folder name (case-insensitive) or "unfiled". See dexi_folders.'},
        "period": {"type": "string", "enum": ["today", "yesterday", "week"]},
        "since": {"type": "string", "description": "ISO 8601 date/datetime lower bound, e.g. 2026-08-13 or 2026-08-13T09:00:00Z."},
        "sort": {"type": "string", "enum": ["created", "updated"], "default": "created"},
        "page": {"type": "integer", "minimum": 1, "default": 1},
        "size": {"type": "integer", "minimum": 1, "maximum": 50, "default": 10},
        "full_text": {"type": "boolean", "default": False},
    },
)

SAVE = _tool(
    "dexi_save",
    "Create a new Dexi note. Save distilled knowledge the user will want later — a "
    "decision, a fact, a summary — not raw conversation. Use a short noun-phrase title, "
    "plain text body, and 1-3 inline #hashtags (check dexi_tags first to reuse the "
    "user's existing tags). Returns the note id and URL.",
    {
        "title": {"type": "string", "description": "Short noun-phrase title."},
        "text": {"type": "string", "description": "Plain-text body; #hashtags and [[Wiki Links]] are recognized."},
    },
    ["text"],
)

APPEND = _tool(
    "dexi_append",
    "Append plain text to an existing Dexi note (keeps its formatting). Prefer this over "
    "creating near-duplicate notes when the topic already has one.",
    {
        "note_id": {"type": "string"},
        "text": {"type": "string", "description": "Text to add at the end of the note."},
    },
    ["note_id", "text"],
)

TAGS = _tool(
    "dexi_tags",
    "List the user's tags with counts (hashtags by default; kind=mention for @mentions). "
    "Call before saving so new notes reuse existing tags.",
    {
        "kind": {"type": "string", "enum": ["hashtag", "mention"], "default": "hashtag"},
        "limit": {"type": "integer", "minimum": 1, "maximum": 200, "default": 50},
    },
)

FOLDERS = _tool(
    "dexi_folders",
    "List the user's folders with note counts, plus how many notes are unfiled.",
    {},
)

REVIEWS_DUE = _tool(
    "dexi_reviews_due",
    "Spaced-repetition: notes due for review now (only if the user has set up review "
    "tags in Dexi). Quiz the user title-first, then reveal, then grade with "
    "dexi_review_grade.",
    {"limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 10}},
)

REVIEW_GRADE = _tool(
    "dexi_review_grade",
    "Record a review grade for a due note: 1=Again, 2=Hard, 3=Good, 4=Easy.",
    {
        "note_id": {"type": "string"},
        "grade": {"type": "integer", "minimum": 1, "maximum": 4},
    },
    ["note_id", "grade"],
)

READ_TOOLS: list[dict[str, Any]] = [SEARCH, GET, LIST, TAGS, FOLDERS, REVIEWS_DUE]
WRITE_TOOLS: list[dict[str, Any]] = [SAVE, APPEND, REVIEW_GRADE]
ALL_TOOLS: list[dict[str, Any]] = READ_TOOLS + WRITE_TOOLS
TOOL_NAMES = {t["name"] for t in ALL_TOOLS}


def tool_schemas(*, read_only: bool = False) -> list[dict[str, Any]]:
    return list(READ_TOOLS if read_only else ALL_TOOLS)
