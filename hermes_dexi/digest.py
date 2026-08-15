"""Deterministic session digest — no LLM call inside the plugin.

Turns the buffered (user, assistant) exchanges of one Hermes session into
one Dexi note: what was asked, what was concluded, and the tail of the
conversation. Kept deliberately plain: it's a searchable breadcrumb the user
can find later ("what did I ask Hermes about X?"), not a memory extraction.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

MAX_TURNS_LISTED = 12
MAX_LINE = 240
MAX_TOTAL = 6000
_WS = re.compile(r"\s+")
_CONTEXT_BLOCK = re.compile(r"<dexi-context>.*?</dexi-context>", re.S)
_TAG_BLOCK = re.compile(r"<[a-z][\w-]*-context>.*?</[a-z][\w-]*-context>", re.S)


def clean(text: str) -> str:
    text = _CONTEXT_BLOCK.sub("", text or "")
    text = _TAG_BLOCK.sub("", text)
    return _WS.sub(" ", text).strip()


def _first_line(text: str, limit: int = MAX_LINE) -> str:
    text = clean(text)
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def build_digest(turns: list[tuple[str, str]], *, tag: str, session_id: str = "",
                 now: datetime | None = None, platform: str = "") -> tuple[str, str]:
    """Return (title, text) for the digest note. Empty title/text if there is
    nothing worth saving (no non-trivial user turn)."""
    turns = [(clean(u), clean(a)) for u, a in turns]
    turns = [(u, a) for u, a in turns if u or a]
    if not turns:
        return "", ""
    first_user = next((u for u, _ in turns if u), "")
    if not first_user:
        return "", ""
    when = (now or datetime.now(timezone.utc)).astimezone()
    stamp = when.strftime("%Y-%m-%d %H:%M")
    title = f"Hermes session {when.strftime('%Y-%m-%d')} — {_first_line(first_user, 60)}"

    lines = [
        f"{tag} Session digest written by Hermes Agent"
        + (f" ({platform})" if platform else "")
        + f" on {stamp}.",
        "",
        "Asked:",
    ]
    for u, _ in turns[:MAX_TURNS_LISTED]:
        if u:
            lines.append(f"- {_first_line(u)}")
    if len(turns) > MAX_TURNS_LISTED:
        lines.append(f"- … and {len(turns) - MAX_TURNS_LISTED} more turns")
    last_answer = next((a for _, a in reversed(turns) if a), "")
    if last_answer:
        lines += ["", "Last answer:", _first_line(last_answer, 1200)]
    if session_id:
        lines += ["", f"Session: {session_id}"]
    text = "\n".join(lines)
    if len(text) > MAX_TOTAL:
        text = text[: MAX_TOTAL - 1].rstrip() + "…"
    return title, text
