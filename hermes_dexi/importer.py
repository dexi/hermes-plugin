"""``hermes dexi import`` — move an agent's native memory files into Dexi as
notes, deterministically (no LLM call). Port of openclaw-plugin/src/import.ts.

Layouts understood: Hermes (``~/.hermes/memories/MEMORY.md`` + ``USER.md``),
OpenClaw memory-core (``<workspace>/MEMORY.md`` plus daily
``<workspace>/memory/YYYY-MM-DD.md`` logs), or with ``--all`` any folder of
markdown. A daily log becomes one note; a curated file becomes one note per
top-level section so a long MEMORY.md does not turn into a single
unsearchable blob. Every note carries the source agent's tag, the shared
#memory tag and #imported, plus ``imported_from::`` (and ``logged::`` for
daily logs) properties.

Idempotent: a manifest (``<hermes_home>/dexi-imports.json``) maps each
file/section to the note it became and a content hash, so re-running adds
only new files and rewrites sections whose text changed instead of
duplicating them.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .bridge import Bridge, BridgeError
from .digest import SHARED_MEMORY_TAG

IMPORT_TAG = "#imported"
# Per-note body cap — memory files are small (Hermes caps MEMORY.md at ~2k
# chars); daily logs can grow, so leave room but never ship a megabyte.
MAX_NOTE_CHARS = 30_000

_DAILY_RE = re.compile(r"(?:^|/)(\d{4}-\d{2}-\d{2})\.md$", re.I)
_CURATED = {"memory.md", "user.md"}
_MEMORY_DIRS = {"memory", "memories"}
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")


@dataclass(frozen=True)
class SourceFile:
    rel_path: str
    content: str


@dataclass(frozen=True)
class PlannedNote:
    key: str
    title: str
    text: str
    source: str
    hash: str


@dataclass(frozen=True)
class Section:
    heading: str | None
    body: str


def _walk(root: Path) -> list[Path]:
    out: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith(".") and d != "node_modules")
        for name in filenames:
            if not name.startswith(".") and name.lower().endswith(".md"):
                out.append(Path(dirpath) / name)
    return out


def discover_memory_files(root: str | os.PathLike, *, all_files: bool = False) -> list[SourceFile]:
    """Files to import under ``root`` (a file or a directory). Without
    ``all_files``, a directory yields only the memory layout: root-level
    MEMORY.md / USER.md and every markdown file under a ``memory/`` or
    ``memories/`` subfolder (or the whole tree when ``root`` itself is one of
    those). Agent config files sharing an OpenClaw workspace (AGENTS.md,
    SOUL.md, TOOLS.md…) are never memory."""
    abs_root = Path(root).expanduser().resolve()
    if not abs_root.exists():
        return []

    def read(file: Path, base: Path) -> SourceFile | None:
        try:
            return SourceFile(file.relative_to(base).as_posix(), file.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError):
            return None

    if abs_root.is_file():
        f = read(abs_root, abs_root.parent)
        return [f] if f else []
    files: list[Path] = []
    if all_files or abs_root.name.lower() in _MEMORY_DIRS:
        files = _walk(abs_root)
    else:
        for entry in abs_root.iterdir():
            if entry.is_file() and entry.name.lower() in _CURATED:
                files.append(entry)
            elif entry.is_dir() and entry.name.lower() in _MEMORY_DIRS:
                files.extend(_walk(entry))
    files.sort(key=lambda p: p.relative_to(abs_root).as_posix())
    return [f for f in (read(p, abs_root) for p in files) if f is not None]


def split_sections(content: str) -> tuple[str | None, list[Section]]:
    """Split a curated file at its highest heading level. A lone ``# Title``
    line at the top is the file's title, not a section (a lone ``## Heading``
    is a section); text before the first section heading stays as a preamble."""
    lines = content.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    headings: list[tuple[int, int, str]] = []
    for i, line in enumerate(lines):
        m = _HEADING_RE.match(line)
        if m:
            headings.append((i, len(m.group(1)), m.group(2)))
    file_title: str | None = None
    start = 0
    first_content = next((i for i, l in enumerate(lines) if l.strip()), -1)
    if headings:
        i0, level0, text0 = headings[0]
        if level0 == 1 and i0 == first_content and not any(lv == 1 for _, lv, _ in headings[1:]):
            file_title = text0
            start = i0 + 1
            headings = headings[1:]
    if not headings:
        body = "\n".join(lines[start:]).strip()
        return file_title, ([Section(None, body)] if body else [])
    level = min(lv for _, lv, _ in headings)
    cuts = [h for h in headings if h[1] == level]
    sections: list[Section] = []
    preamble = "\n".join(lines[start:cuts[0][0]]).strip()
    if preamble:
        sections.append(Section(None, preamble))
    for idx, (i, _, text) in enumerate(cuts):
        end = cuts[idx + 1][0] if idx + 1 < len(cuts) else len(lines)
        body = "\n".join(lines[i + 1:end]).strip()
        if body:
            sections.append(Section(text, body))
    return file_title, sections


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-") or "section"


def _file_label(rel_path: str, file_title: str | None) -> str:
    base = rel_path.rsplit("/", 1)[-1]
    lower = base.lower()
    if lower == "memory.md":
        return "Memory"
    if lower == "user.md":
        return "User profile"
    return file_title or re.sub(r"\.md$", "", base, flags=re.I)


def _tag_line(agent_tag: str) -> str:
    seen: list[str] = []
    for t in (agent_tag, SHARED_MEMORY_TAG, IMPORT_TAG):
        if t not in seen:
            seen.append(t)
    return " ".join(seen)


def _finish(text: str) -> str:
    return text if len(text) <= MAX_NOTE_CHARS else text[: MAX_NOTE_CHARS - 1].rstrip() + "…"


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def plan_import(files: list[SourceFile], *, agent_tag: str) -> list[PlannedNote]:
    """Pure: source files → the notes they become. ``agent_tag`` names the
    agent whose memory this was (#hermes, #openclaw…), not the one importing."""
    out: list[PlannedNote] = []
    seen: set[str] = set()

    def unique(key: str) -> str:
        k, n = key, 2
        while k in seen:
            k = f"{key}-{n}"
            n += 1
        seen.add(k)
        return k

    for f in files:
        content = f.content.replace("\r\n", "\n").replace("\r", "\n").strip()
        if not content:
            continue
        daily = _DAILY_RE.search(f.rel_path)
        if daily:
            date = daily.group(1)
            file_title, sections = split_sections(content)
            body = "\n\n".join(f"## {s.heading}\n{s.body}" if s.heading else s.body for s in sections) or content
            text = _finish("\n".join([f"{_tag_line(agent_tag)} Memory imported from {f.rel_path}.", "", body, "",
                                      f"logged:: {date}", f"imported_from:: {f.rel_path}"]))
            title = f"Memory log {date} — {file_title}" if file_title and file_title != date else f"Memory log {date}"
            out.append(PlannedNote(unique(f.rel_path), title, text, f.rel_path, _hash(text)))
            continue
        file_title, sections = split_sections(content)
        label = _file_label(f.rel_path, file_title)
        for s in sections:
            title = f"{label}: {s.heading}" if s.heading else label
            text = _finish("\n".join([f"{_tag_line(agent_tag)} Memory imported from {f.rel_path}.", "", s.body, "",
                                      f"imported_from:: {f.rel_path}"]))
            key = unique(f"{f.rel_path}#{_slug(s.heading)}" if s.heading else f.rel_path)
            out.append(PlannedNote(key, title[:200], text, f.rel_path, _hash(text)))
    return out


# ---------------------------------------------------------------------------
# Manifest


class ImportState:
    """``<hermes_home>/dexi-imports.json`` — key → {note_id, hash, title, imported_at}.
    ``path=None`` keeps everything in memory (tests)."""

    def __init__(self, path: Path | None) -> None:
        self.path = path
        self._entries: dict[str, dict[str, Any]] = {}
        if path is not None:
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                entries = data.get("entries") if isinstance(data, dict) else None
                if isinstance(entries, dict):
                    self._entries = entries
            except (OSError, ValueError):
                pass

    @staticmethod
    def for_home(hermes_home: str | os.PathLike | None) -> "ImportState":
        home = Path(hermes_home) if hermes_home else Path(os.environ.get("HERMES_HOME", "~/.hermes"))
        return ImportState(home.expanduser() / "dexi-imports.json")

    def get(self, key: str) -> dict[str, Any] | None:
        return self._entries.get(key)

    def set(self, key: str, record: dict[str, Any]) -> None:
        self._entries[key] = record
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps({"version": 1, "entries": self._entries}, indent=2), encoding="utf-8")
        os.replace(tmp, self.path)
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Runner


@dataclass
class ImportSummary:
    created: int = 0
    updated: int = 0
    unchanged: int = 0
    failed: int = 0


def _structured(result: Any) -> dict[str, Any]:
    if isinstance(result, dict):
        sc = result.get("structuredContent")
        if isinstance(sc, dict):
            return sc
    return {}


def run_import(bridge: Bridge, plan: list[PlannedNote], state: ImportState, *, dry_run: bool = False,
               out: Callable[[str], None] = print, timeout: float = 60.0,
               note_url: Callable[[str], str] | None = None) -> ImportSummary:
    summary = ImportSummary()
    for n in plan:
        prev = state.get(n.key)
        if prev and prev.get("hash") == n.hash:
            summary.unchanged += 1
            continue
        verb = "update" if prev else "create"
        if dry_run:
            out(f"  would {verb}: {n.title}  ({n.source}, {len(n.text)} chars)")
            if prev:
                summary.updated += 1
            else:
                summary.created += 1
            continue
        try:
            stamp = datetime.now(timezone.utc).isoformat()
            if prev:
                result = bridge.call_tool("update_note", {"note_id": prev["note_id"], "title": n.title, "text": n.text,
                                                          "mode": "replace", "intent": "memory import (changed section)"},
                                          timeout=timeout)
                if isinstance(result, dict) and result.get("isError"):
                    raise BridgeError(f"update_note failed for {n.title}")
                state.set(n.key, {"note_id": prev["note_id"], "hash": n.hash, "title": n.title, "imported_at": stamp})
                summary.updated += 1
                out(f"  updated: {n.title}")
            else:
                result = bridge.call_tool("create_note", {"title": n.title, "text": n.text, "intent": "memory import"},
                                          timeout=timeout)
                if isinstance(result, dict) and result.get("isError"):
                    raise BridgeError(f"create_note failed for {n.title}")
                note_id = str(_structured(result).get("id") or "")
                if not note_id:
                    raise BridgeError(f"create_note returned no id for {n.title}")
                state.set(n.key, {"note_id": note_id, "hash": n.hash, "title": n.title, "imported_at": stamp})
                summary.created += 1
                out(f"  created: {n.title} — {note_url(note_id) if note_url else note_id}")
        except BridgeError as exc:
            summary.failed += 1
            out(f"  FAILED: {n.title} — {exc}")
    return summary
