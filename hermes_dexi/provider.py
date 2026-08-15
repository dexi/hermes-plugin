"""Dexi as a Hermes Agent memory provider.

What this adds over a plain ``mcp_servers.dexi`` entry is the *hooks*:
recall before each turn (``prefetch`` → Dexi semantic search injected as
``<dexi-context>``), a system-prompt block, and — opt-in — one distilled
digest note per session (``on_session_end`` / ``on_pre_compress`` /
``shutdown``). Tools are a compact ``dexi_*`` set forwarding to Dexi's MCP
tools over the bridge; every read/write inherits the user's OAuth grant,
scopes, and any per-connection folder/tag restriction set on Dexi's consent
page.

Deliberately NOT done: a note per turn (``sync_turn`` only buffers), and
mirroring Hermes' MEMORY.md/USER.md writes — Dexi is the user's own notes
library, not a fact store.
"""
from __future__ import annotations

import json
import logging
import re
import threading
from typing import Any

from . import config as cfgmod
from . import digest as digestmod
from . import schemas
from ._base import MemoryProvider, is_trivial_prompt
from .bridge import Bridge, BridgeError, BridgeTimeoutError, McpHttpBridge

logger = logging.getLogger("hermes_dexi")

_HASHTAG = re.compile(r"(?<!\w)#\w+")
_QUOTED = re.compile(r'"[^"]{3,}"')


def _tool_error(message: str, **extra: Any) -> str:
    try:  # Hermes' canonical error envelope when available
        from tools.registry import tool_error  # type: ignore

        return tool_error(message, **extra)
    except Exception:  # noqa: BLE001
        out = {"error": str(message)[:2000]}
        out.update(extra)
        return json.dumps(out, ensure_ascii=False)


def _structured(result: Any) -> dict[str, Any]:
    if isinstance(result, dict) and isinstance(result.get("structuredContent"), dict):
        return result["structuredContent"]
    return {}


def _text(result: Any) -> str:
    if not isinstance(result, dict):
        return ""
    content = result.get("content")
    if isinstance(content, list) and content:
        first = content[0]
        if isinstance(first, dict) and isinstance(first.get("text"), str):
            return first["text"]
    return ""


def _with_url(item: dict[str, Any]) -> dict[str, Any]:
    if isinstance(item, dict) and item.get("id"):
        item = dict(item)
        item["url"] = cfgmod.note_url(str(item["id"]))
    return item


class DexiMemoryProvider(MemoryProvider):
    """Your Dexi notes as Hermes' long-term memory."""

    PROVIDER_NAME = cfgmod.PLUGIN_NAME

    def __init__(self, bridge: Bridge | None = None) -> None:
        self._bridge_override = bridge
        self._bridge: Bridge | None = bridge
        self._cfg: dict[str, Any] = dict(cfgmod.DEFAULTS)
        self._hermes_home: str | None = None
        self._session_id: str = ""
        self._platform: str = ""
        self._turns: list[tuple[str, str]] = []
        self._digested_sessions: set[str] = set()
        self._lock = threading.Lock()
        self._threads: list[threading.Thread] = []
        self._prefetch_cache: tuple[str, str] | None = None
        self._last_recall_count = 0

    # -- required surface ----------------------------------------------------

    @property
    def name(self) -> str:
        return self.PROVIDER_NAME

    def is_available(self) -> bool:
        # No credentials to check: OAuth happens lazily on first use, and an
        # unauthenticated bridge just surfaces an error on the call.
        return True

    def unavailable_reason(self) -> str:
        return ""

    def initialize(self, session_id: str, **kwargs: Any) -> None:
        self._session_id = session_id or ""
        self._hermes_home = kwargs.get("hermes_home") or self._hermes_home
        self._platform = str(kwargs.get("platform") or "")
        self._cfg = cfgmod.load_config(self._hermes_home)
        with self._lock:
            self._turns = []
        self._prefetch_cache = None
        if self._bridge_override is not None:
            self._bridge = self._bridge_override
        elif self._bridge is None:
            self._bridge = McpHttpBridge(
                self._cfg["mcp_url"],
                server_name=cfgmod.SERVER_NAME,
                oauth_config=cfgmod.oauth_config(self._cfg),
                hermes_home=self._hermes_home,
            )
        # Lazy: no network here. Boot must never wait on Dexi or a browser.

    def get_tool_schemas(self) -> list[dict[str, Any]]:
        return schemas.tool_schemas(read_only=bool(self._cfg.get("read_only")))

    def handle_tool_call(self, tool_name: str, args: dict[str, Any], **_kw: Any) -> str:
        args = dict(args or {})
        if tool_name not in schemas.TOOL_NAMES:
            return _tool_error(f"unknown dexi tool: {tool_name}")
        if self._cfg.get("read_only") and tool_name in {t["name"] for t in schemas.WRITE_TOOLS}:
            return _tool_error("This Dexi connection is read-only (dexi.json: read_only=true).")
        handler = getattr(self, f"_tool_{tool_name[len('dexi_'):]}")
        try:
            return json.dumps(handler(args), ensure_ascii=False, default=str)
        except BridgeTimeoutError as exc:
            return _tool_error(str(exc))
        except BridgeError as exc:
            return _tool_error(str(exc))
        except Exception as exc:  # noqa: BLE001 - never raise into the agent loop
            logger.debug("dexi tool %s failed", tool_name, exc_info=True)
            return _tool_error(f"{type(exc).__name__}: {exc}")

    # -- setup wizard --------------------------------------------------------

    def get_config_schema(self) -> list[dict[str, Any]]:
        return [
            {
                "key": "auto_recall",
                "description": "Recall relevant Dexi notes before each turn and inject them as context.",
                "default": "true",
                "choices": ["true", "false"],
            },
            {
                "key": "session_digest",
                "description": "Write one digest note per session to Dexi (tagged, searchable later). "
                "Sends the session's questions and last answer to your Dexi account.",
                "default": "false",
                "choices": ["true", "false"],
            },
            {
                "key": "read_only",
                "description": "Request read-only access (notes:read); Hermes can search but never write.",
                "default": "false",
                "choices": ["true", "false"],
            },
        ]

    def save_config(self, values: dict[str, Any], hermes_home: str) -> None:
        cfgmod.save_config(values, hermes_home)
        self._cfg = cfgmod.load_config(hermes_home)

    def backup_paths(self) -> list[str]:
        return [str(cfgmod.config_path(self._hermes_home))]

    # -- lifecycle hooks -----------------------------------------------------

    def system_prompt_block(self) -> str:
        lines = [
            "# Dexi",
            "The user's Dexi notes library is connected as memory: typed notes, clipped web "
            "pages, emailed articles, and RSS entries. Before answering questions about the "
            "user's own material, research, or past decisions, search it with dexi_search "
            "(hybrid keyword+semantic; full_text=true to read bodies) — relevant notes are "
            "also pre-loaded in <dexi-context> when available. Cite notes by title with their url.",
        ]
        if self._cfg.get("read_only"):
            lines.append("This connection is read-only.")
        else:
            lines.append(
                "Save with dexi_save only when the user asks or when a distilled fact/decision "
                "is clearly worth keeping — short noun-phrase title, plain text, 1-3 existing "
                "#hashtags (dexi_tags). Prefer dexi_append over near-duplicate notes."
            )
        if self._cfg.get("session_digest"):
            lines.append(
                f"A {self._cfg.get('digest_tag', '#hermes')} digest note of this session is written "
                "to Dexi automatically when it ends."
            )
        return "\n".join(lines)

    def prefetch(self, query: str, *, session_id: str = "", **_kw: Any) -> str:
        if not self._cfg.get("auto_recall") or is_trivial_prompt(query):
            self._last_recall_count = 0
            return ""
        cached = self._prefetch_cache
        if cached is not None and cached[0] == query:
            self._prefetch_cache = None
            return cached[1]
        return self._recall(query)

    def queue_prefetch(self, query: str, *, session_id: str = "", **_kw: Any) -> None:
        if not self._cfg.get("auto_recall") or is_trivial_prompt(query):
            return

        def _work() -> None:
            try:
                self._prefetch_cache = (query, self._recall(query))
            except Exception:  # noqa: BLE001
                pass

        self._spawn(_work)

    def recall_status(self):  # -> Optional[RecallStatus]
        try:  # pragma: no cover - Hermes-only type
            from agent.memory_provider import RecallStatus  # type: ignore

            return RecallStatus(provider_label="Dexi", count=self._last_recall_count)
        except Exception:  # noqa: BLE001
            return None

    def sync_turn(self, user_content: str, assistant_content: str, *,
                  session_id: str = "", messages: list | None = None, **_kw: Any) -> None:
        # No network on the turn path — buffer for the end-of-session digest only.
        if not self._cfg.get("session_digest"):
            return
        u = digestmod.clean(user_content or "")
        a = digestmod.clean(assistant_content or "")
        if not (u or a):
            return
        with self._lock:
            self._turns.append((u, a))

    def on_session_end(self, messages: list, **_kw: Any) -> None:
        self._flush_digest_async(self._session_id)

    def on_session_switch(self, new_session_id: str, **kw: Any) -> None:
        old = self._session_id
        self._flush_digest_async(old)
        self._session_id = new_session_id or ""
        with self._lock:
            self._turns = []

    def on_pre_compress(self, messages: list, **_kw: Any) -> str:
        # Save what we have before context is compacted, but keep buffering:
        # the session isn't over. Dedup by session id prevents a second note.
        self._flush_digest_async(self._session_id, keep_buffer=True)
        return ""

    def on_memory_write(self, action: str, target: str, content: str,
                        metadata: dict | None = None, **_kw: Any) -> None:
        return None  # MEMORY.md/USER.md are Hermes' own; Dexi doesn't mirror them.

    def shutdown(self) -> None:
        self._flush_digest_async(self._session_id)
        for t in list(self._threads):
            t.join(timeout=5.0)
        self._threads = []
        if self._bridge is not None and self._bridge_override is None:
            try:
                self._bridge.stop()
            except Exception:  # noqa: BLE001
                pass

    # -- recall --------------------------------------------------------------

    def _recall(self, query: str) -> str:
        n = int(self._cfg.get("recall_results") or 5)
        floor = float(self._cfg.get("recall_min_similarity") or 0.0)
        timeout = float(self._cfg.get("prefetch_timeout") or 2.5)
        q = query.strip()[:500]
        try:
            sem = _structured(self._call("semantic_search", {
                "query": q, "size": n, "intent": "auto-recall before answering",
            }, timeout=timeout))
        except Exception:  # noqa: BLE001 - recall is best-effort
            self._last_recall_count = 0
            return ""
        items: list[dict[str, Any]] = [
            i for i in (sem.get("items") or [])
            if float(i.get("similarity") or 0) >= floor
        ]
        # A hashtag or quoted phrase in the prompt is a keyword signal worth a
        # second, cheap query.
        if _HASHTAG.search(q) or _QUOTED.search(q):
            try:
                kw = _structured(self._call("search_notes", {
                    "query": q[:200], "size": n, "intent": "auto-recall before answering",
                }, timeout=timeout))
                seen = {i.get("id") for i in items}
                for i in kw.get("items") or []:
                    if i.get("id") not in seen:
                        items.append(i)
            except Exception:  # noqa: BLE001
                pass
        items = items[:n]
        self._last_recall_count = len(items)
        if not items:
            return ""
        lines = ["<dexi-context>",
                 "Notes from the user's Dexi library that may be relevant (use dexi_get for full text):"]
        for i in items:
            title = (i.get("title") or "(untitled)").strip()
            snippet = (i.get("snippet") or "").strip().replace("\n", " ")
            tags = " ".join(i.get("tags") or [])
            meta = " · ".join(x for x in (i.get("source"), tags) if x)
            lines.append(f"- {title} [{i.get('id')}]" + (f" ({meta})" if meta else "")
                         + (f": {snippet}" if snippet else ""))
        lines.append("</dexi-context>")
        return "\n".join(lines)

    # -- digest --------------------------------------------------------------

    def _flush_digest_async(self, session_id: str, *, keep_buffer: bool = False) -> None:
        if not self._cfg.get("session_digest") or self._cfg.get("read_only"):
            return
        with self._lock:
            turns = list(self._turns)
            if not keep_buffer:
                self._turns = []
        if not turns:
            return
        key = session_id or "no-session"
        if key in self._digested_sessions:
            return
        self._digested_sessions.add(key)

        def _work() -> None:
            title, text = digestmod.build_digest(
                turns, tag=str(self._cfg.get("digest_tag") or "#hermes"),
                session_id=session_id, platform=self._platform,
            )
            if not text:
                return
            try:
                self._call("create_note", {"title": title, "text": text,
                                           "intent": "session digest"},
                           timeout=float(self._cfg.get("tool_timeout") or 30.0))
            except Exception:  # noqa: BLE001
                logger.debug("dexi digest write failed", exc_info=True)
                self._digested_sessions.discard(key)

        self._spawn(_work)

    def _spawn(self, fn) -> None:
        self._threads = [t for t in self._threads if t.is_alive()]
        t = threading.Thread(target=fn, daemon=True)
        self._threads.append(t)
        t.start()

    # -- bridge --------------------------------------------------------------

    def _call(self, name: str, args: dict[str, Any], *, timeout: float | None = None) -> dict[str, Any]:
        if self._bridge is None:
            raise BridgeError("Dexi memory provider not initialized")
        args = {k: v for k, v in (args or {}).items() if v is not None}
        result = self._bridge.call_tool(name, args, timeout=timeout)
        if isinstance(result, dict) and result.get("isError"):
            raise BridgeError(_text(result) or f"{name} failed")
        return result

    def _tool_timeout(self) -> float:
        return float(self._cfg.get("tool_timeout") or 30.0)

    # -- tools ---------------------------------------------------------------

    def _tool_search(self, args: dict[str, Any]) -> dict[str, Any]:
        query = str(args.get("query") or "").strip()
        if not query:
            raise BridgeError("query is required")
        mode = args.get("mode") or "hybrid"
        size = int(args.get("size") or 8)
        full_text = bool(args.get("full_text"))
        intent = args.get("intent")
        t = self._tool_timeout()
        merged: dict[str, dict[str, Any]] = {}
        order: list[str] = []
        if mode in ("hybrid", "semantic"):
            sem = _structured(self._call("semantic_search", {
                "query": query[:500], "size": size, "full_text": full_text, "intent": intent,
            }, timeout=t))
            for i in sem.get("items") or []:
                i = dict(i)
                i["match"] = "semantic"
                merged[i["id"]] = i
                order.append(i["id"])
        if mode in ("hybrid", "keyword"):
            kw = _structured(self._call("search_notes", {
                "query": query[:200], "size": size, "full_text": full_text, "intent": intent,
            }, timeout=t))
            for i in kw.get("items") or []:
                if i["id"] in merged:
                    merged[i["id"]]["match"] = "both"
                else:
                    i = dict(i)
                    i["match"] = "keyword"
                    merged[i["id"]] = i
                    order.append(i["id"])
        items = [_with_url(merged[i]) for i in order][:size]
        return {"items": items, "count": len(items), "mode": mode}

    def _tool_get(self, args: dict[str, Any]) -> dict[str, Any]:
        note = _structured(self._call("get_note", {
            "note_id": args.get("note_id"), "intent": args.get("intent"),
        }, timeout=self._tool_timeout()))
        return _with_url(note)

    def _tool_list(self, args: dict[str, Any]) -> dict[str, Any]:
        out = _structured(self._call("list_notes", {
            k: args.get(k) for k in
            ("source", "tag", "folder", "period", "since", "sort", "page", "size", "full_text", "intent")
        }, timeout=self._tool_timeout()))
        out = dict(out)
        out["items"] = [_with_url(i) for i in out.get("items") or []]
        return out

    def _tool_save(self, args: dict[str, Any]) -> dict[str, Any]:
        note = _structured(self._call("create_note", {
            "title": args.get("title") or "", "text": args.get("text") or "",
            "intent": args.get("intent"),
        }, timeout=self._tool_timeout()))
        return {"id": note.get("id"), "title": note.get("title"),
                "url": cfgmod.note_url(str(note.get("id"))), "tags": note.get("tags")}

    def _tool_append(self, args: dict[str, Any]) -> dict[str, Any]:
        note = _structured(self._call("update_note", {
            "note_id": args.get("note_id"), "text": args.get("text") or "",
            "mode": "append", "intent": args.get("intent"),
        }, timeout=self._tool_timeout()))
        return {"id": note.get("id"), "title": note.get("title"),
                "url": cfgmod.note_url(str(note.get("id"))), "updated": note.get("updated")}

    def _tool_tags(self, args: dict[str, Any]) -> dict[str, Any]:
        return _structured(self._call("list_tags", {
            "kind": args.get("kind") or "hashtag", "limit": args.get("limit") or 50,
            "intent": args.get("intent"),
        }, timeout=self._tool_timeout()))

    def _tool_folders(self, args: dict[str, Any]) -> dict[str, Any]:
        return _structured(self._call("list_folders", {"intent": args.get("intent")},
                                      timeout=self._tool_timeout()))

    def _tool_reviews_due(self, args: dict[str, Any]) -> dict[str, Any]:
        out = _structured(self._call("get_due_reviews", {
            "limit": args.get("limit") or 10, "intent": args.get("intent"),
        }, timeout=self._tool_timeout()))
        out = dict(out)
        out["items"] = [_with_url(i) for i in out.get("items") or []]
        return out

    def _tool_review_grade(self, args: dict[str, Any]) -> dict[str, Any]:
        return _structured(self._call("grade_review", {
            "note_id": args.get("note_id"), "grade": args.get("grade"),
            "intent": args.get("intent"),
        }, timeout=self._tool_timeout()))
