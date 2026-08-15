"""DexiMemoryProvider driven through the Hermes hook surface with a fake
bridge — no network, no Hermes install required (the base class falls back
to a local stand-in outside Hermes)."""
from __future__ import annotations

import json
import time

import pytest

from hermes_dexi import register
from hermes_dexi import config as cfgmod
from hermes_dexi.bridge import BridgeError, FakeBridge
from hermes_dexi.digest import build_digest, clean
from hermes_dexi.provider import DexiMemoryProvider
from hermes_dexi.schemas import ALL_TOOLS, READ_TOOLS, TOOL_NAMES


def _item(i, sim=None, **extra):
    d = {"id": f"00000000-0000-0000-0000-00000000000{i}", "title": f"note {i}",
         "snippet": f"body {i}", "tags": ["#t"], "source": "note", "source_url": None,
         "created": "2026-08-14T00:00:00+00:00", "updated": "2026-08-14T00:00:00+00:00"}
    if sim is not None:
        d["similarity"] = sim
    d.update(extra)
    return d


def canned(name, args):
    if name == "semantic_search":
        return {"items": [_item(1, 0.9), _item(2, 0.7), _item(3, 0.3)]}
    if name == "search_notes":
        return {"items": [_item(2), _item(4)], "total": 2, "page": 1}
    if name == "get_note":
        return _item(1, text="full body")
    if name == "list_notes":
        return {"items": [_item(5)], "total": 1, "page": 1}
    if name == "create_note":
        return _item(9, title=args.get("title"), text=args.get("text"))
    if name == "update_note":
        return _item(1, updated="2026-08-15T00:00:00+00:00")
    if name == "list_tags":
        return {"tags": [{"tag": "#t", "count": 3}]}
    if name == "list_folders":
        return {"folders": [{"name": "Research", "note_count": 2}], "unfiled_count": 7}
    if name == "get_due_reviews":
        return {"items": [_item(6, text="q")], "due_count": 1}
    if name == "grade_review":
        return {"id": args["note_id"], "grade": args["grade"], "due_at": "x"}
    raise AssertionError(f"unexpected tool {name}")


@pytest.fixture()
def provider(tmp_path):
    bridge = FakeBridge(handler=canned)
    p = DexiMemoryProvider(bridge=bridge)
    p.initialize("sess-1", hermes_home=str(tmp_path), platform="cli")
    return p, bridge


def _wait_threads(p, timeout=3.0):
    deadline = time.time() + timeout
    while any(t.is_alive() for t in p._threads) and time.time() < deadline:
        time.sleep(0.01)


# --- registration / schemas -----------------------------------------------------


def test_register_wires_provider_and_skills():
    class Ctx:
        def __init__(self):
            self.provider = None
            self.skills = []

        def register_memory_provider(self, provider):
            self.provider = provider

        def register_skill(self, name, path, description=""):
            self.skills.append((name, path.name, description))

    ctx = Ctx()
    register(ctx)
    assert isinstance(ctx.provider, DexiMemoryProvider)
    assert ctx.provider.name == "dexi"
    assert [s[0] for s in ctx.skills] == ["capture", "recall", "review"]
    assert all(s[1] == "SKILL.md" for s in ctx.skills)


def test_register_tolerates_minimal_ctx():
    register(object())  # no register_* methods → no error


def test_tool_schemas_shape(provider):
    p, _ = provider
    schemas = p.get_tool_schemas()
    assert [s["name"] for s in schemas] == [t["name"] for t in ALL_TOOLS]
    for s in schemas:
        assert s["parameters"]["type"] == "object"
        assert "intent" in s["parameters"]["properties"]
        assert s["name"].startswith("dexi_")


def test_read_only_hides_write_tools(tmp_path):
    cfgmod.save_config({"read_only": True}, tmp_path)
    p = DexiMemoryProvider(bridge=FakeBridge(handler=canned))
    p.initialize("s", hermes_home=str(tmp_path))
    assert [s["name"] for s in p.get_tool_schemas()] == [t["name"] for t in READ_TOOLS]
    out = json.loads(p.handle_tool_call("dexi_save", {"text": "x"}))
    assert "read-only" in out["error"]
    assert "This connection is read-only." in p.system_prompt_block()


def test_is_available_without_network(provider):
    p, bridge = provider
    assert p.is_available() is True
    assert bridge.calls == []  # initialize + is_available never touch the bridge


# --- tools ------------------------------------------------------------------------


def test_search_hybrid_merges_and_dedupes(provider):
    p, bridge = provider
    out = json.loads(p.handle_tool_call("dexi_search", {"query": "budget", "intent": "t"}))
    ids = [i["id"][-1] for i in out["items"]]
    assert ids == ["1", "2", "3", "4"]  # semantic order first, then keyword extras
    by = {i["id"][-1]: i for i in out["items"]}
    assert by["1"]["match"] == "semantic"
    assert by["2"]["match"] == "both"
    assert by["4"]["match"] == "keyword"
    assert by["1"]["url"].endswith("/dashboard/notes/" + by["1"]["id"])
    assert [c[0] for c in bridge.calls] == ["semantic_search", "search_notes"]
    assert bridge.calls[0][1]["intent"] == "t"


def test_search_modes(provider):
    p, bridge = provider
    p.handle_tool_call("dexi_search", {"query": "x", "mode": "keyword"})
    assert [c[0] for c in bridge.calls] == ["search_notes"]
    bridge.calls.clear()
    p.handle_tool_call("dexi_search", {"query": "x", "mode": "semantic", "full_text": True})
    assert [c[0] for c in bridge.calls] == ["semantic_search"]
    assert bridge.calls[0][1]["full_text"] is True


def test_search_requires_query(provider):
    p, _ = provider
    assert "query" in json.loads(p.handle_tool_call("dexi_search", {}))["error"]


def test_get_list_tags_folders(provider):
    p, bridge = provider
    got = json.loads(p.handle_tool_call("dexi_get", {"note_id": "abc"}))
    assert got["text"] == "full body" and got["url"]
    lst = json.loads(p.handle_tool_call("dexi_list", {"since": "2026-08-13", "sort": "updated", "tag": None}))
    assert lst["items"][0]["url"]
    sent = bridge.calls[-1][1]
    assert sent == {"since": "2026-08-13", "sort": "updated"}  # None-valued args dropped
    assert json.loads(p.handle_tool_call("dexi_tags", {}))["tags"][0]["tag"] == "#t"
    assert json.loads(p.handle_tool_call("dexi_folders", {}))["unfiled_count"] == 7


def test_save_and_append(provider):
    p, bridge = provider
    out = json.loads(p.handle_tool_call("dexi_save", {"title": "T", "text": "body #t"}))
    assert out["url"].endswith(out["id"]) and out["title"] == "T"
    assert bridge.calls[-1] == ("create_note", {"title": "T", "text": "body #t"})
    out = json.loads(p.handle_tool_call("dexi_append", {"note_id": "n1", "text": "more"}))
    assert bridge.calls[-1][1] == {"note_id": "n1", "text": "more", "mode": "append"}
    assert out["updated"].startswith("2026-08-15")


def test_reviews(provider):
    p, bridge = provider
    due = json.loads(p.handle_tool_call("dexi_reviews_due", {}))
    assert due["due_count"] == 1 and due["items"][0]["url"]
    graded = json.loads(p.handle_tool_call("dexi_review_grade", {"note_id": "n", "grade": 3}))
    assert graded["grade"] == 3


def test_unknown_tool_and_bridge_errors_are_json(provider):
    p, bridge = provider
    assert "unknown" in json.loads(p.handle_tool_call("dexi_nope", {}))["error"]

    def failing(name, args):
        return {"content": [{"type": "text", "text": "Note not found"}], "isError": True}

    bridge.handler = failing
    out = json.loads(p.handle_tool_call("dexi_get", {"note_id": "x"}))
    assert out["error"] == "Note not found"

    bridge.handler = lambda n, a: BridgeError("boom")
    assert json.loads(p.handle_tool_call("dexi_tags", {}))["error"] == "boom"


# --- hooks ------------------------------------------------------------------------


def test_prefetch_injects_context_above_floor(provider):
    p, bridge = provider
    ctx = p.prefetch("what did I decide about the budget?")
    assert ctx.startswith("<dexi-context>") and ctx.endswith("</dexi-context>")
    assert "note 1" in ctx and "note 2" in ctx
    assert "note 3" not in ctx  # similarity 0.3 < floor 0.55
    assert [c[0] for c in bridge.calls] == ["semantic_search"]
    assert bridge.calls[0][1]["size"] == 5
    assert p._last_recall_count == 2


def test_prefetch_adds_keyword_pass_for_hashtags(provider):
    p, bridge = provider
    ctx = p.prefetch("anything in #t about budgets?")
    assert [c[0] for c in bridge.calls] == ["semantic_search", "search_notes"]
    assert "note 4" in ctx  # keyword-only hit merged in


def test_prefetch_skips_trivial_and_disabled(provider, tmp_path):
    p, bridge = provider
    assert p.prefetch("hi") == "" and p.prefetch("/help") == "" and p.prefetch("") == ""
    assert bridge.calls == []
    cfgmod.save_config({"auto_recall": False}, tmp_path)
    p.initialize("s2", hermes_home=str(tmp_path))
    assert p.prefetch("real question about budgets") == ""
    assert bridge.calls == []


def test_prefetch_swallows_bridge_failure(provider):
    p, bridge = provider
    bridge.handler = lambda n, a: BridgeError("down")
    assert p.prefetch("real question about budgets") == ""


def test_queue_prefetch_warms_next_prefetch(provider):
    p, bridge = provider
    p.queue_prefetch("what about the budget?")
    _wait_threads(p)
    assert len(bridge.calls) == 1
    ctx = p.prefetch("what about the budget?")
    assert "note 1" in ctx
    assert len(bridge.calls) == 1  # served from the warm cache, no second call
    p.prefetch("what about the budget?")
    assert len(bridge.calls) == 2  # cache is single-use


def test_system_prompt_block(provider):
    p, _ = provider
    block = p.system_prompt_block()
    assert block.startswith("# Dexi") and "dexi_search" in block and "dexi_save" in block


def test_no_digest_by_default(provider):
    p, bridge = provider
    p.sync_turn("question", "answer")
    p.on_session_end([])
    p.shutdown()
    assert [c[0] for c in bridge.calls] == []


def test_session_digest_once_per_session(tmp_path):
    cfgmod.save_config({"session_digest": True, "digest_tag": "hermes"}, tmp_path)
    bridge = FakeBridge(handler=canned)
    p = DexiMemoryProvider(bridge=bridge)
    p.initialize("sess-9", hermes_home=str(tmp_path), platform="telegram")
    p.sync_turn("hi", "hello")  # trivial but still a turn
    p.sync_turn("How do Railway healthchecks pick the port? <dexi-context>x</dexi-context>",
                "They probe the PORT variable.")
    p.on_pre_compress([])  # early save, keeps buffering
    _wait_threads(p)
    p.on_session_end([])
    p.shutdown()
    _wait_threads(p)
    creates = [c for c in bridge.calls if c[0] == "create_note"]
    assert len(creates) == 1
    args = creates[0][1]
    assert args["title"].startswith("Hermes session ") and "hi" in args["title"]
    assert args["text"].startswith("#hermes Session digest written by Hermes Agent (telegram)")
    assert "Railway healthchecks" in args["text"] and "<dexi-context>" not in args["text"]
    assert "Session: sess-9" in args["text"]
    assert args["intent"] == "session digest"


def test_session_switch_flushes_old_and_resets(tmp_path):
    cfgmod.save_config({"session_digest": True}, tmp_path)
    bridge = FakeBridge(handler=canned)
    p = DexiMemoryProvider(bridge=bridge)
    p.initialize("a", hermes_home=str(tmp_path))
    p.sync_turn("first session question", "ans")
    p.on_session_switch("b")
    _wait_threads(p)
    assert len([c for c in bridge.calls if c[0] == "create_note"]) == 1
    p.on_session_end([])  # session b had no turns → nothing written
    p.shutdown()
    _wait_threads(p)
    assert len([c for c in bridge.calls if c[0] == "create_note"]) == 1


def test_digest_disabled_when_read_only(tmp_path):
    cfgmod.save_config({"session_digest": True, "read_only": True}, tmp_path)
    bridge = FakeBridge(handler=canned)
    p = DexiMemoryProvider(bridge=bridge)
    p.initialize("a", hermes_home=str(tmp_path))
    p.sync_turn("q", "a")
    p.on_session_end([])
    p.shutdown()
    assert bridge.calls == []


# --- config / digest units --------------------------------------------------------


def test_config_roundtrip_and_env(tmp_path, monkeypatch):
    assert cfgmod.load_config(tmp_path)["auto_recall"] is True
    cfgmod.save_config({"auto_recall": "false", "recall_results": "3", "digest_tag": "agent"}, tmp_path)
    cfg = cfgmod.load_config(tmp_path)
    assert cfg["auto_recall"] is False and cfg["recall_results"] == 3 and cfg["digest_tag"] == "#agent"
    # Dexi hashtags are \w runs — "#hermes-e2e" would parse as "#hermes"
    assert cfgmod.normalize_tag("#Hermes-e2e") == "#hermes_e2e"
    assert cfgmod.normalize_tag("") == "#hermes"
    monkeypatch.setenv("DEXI_MCP_URL", "http://localhost:8001/mcp")
    assert cfgmod.load_config(tmp_path)["mcp_url"] == "http://localhost:8001/mcp"
    assert cfgmod.oauth_config({"read_only": True})["scope"] == "notes:read"
    assert "scope" not in cfgmod.oauth_config({"read_only": False})


def test_build_digest_empty_and_clean():
    assert build_digest([], tag="#h") == ("", "")
    assert build_digest([("", "only assistant")], tag="#h") == ("", "")
    assert clean("a  <dexi-context>zzz</dexi-context>\n b") == "a b"
    title, text = build_digest([("q" * 100, "a")], tag="#h", session_id="s")
    assert title.endswith("…") and len(title) < 100
    assert text.startswith("#h Session digest")


def test_provider_config_schema_has_no_secrets(provider):
    p, _ = provider
    schema = p.get_config_schema()
    assert {f["key"] for f in schema} == {"auto_recall", "session_digest", "read_only"}
    assert not any(f.get("secret") for f in schema)
    p.save_config({"session_digest": "true"}, str(p._hermes_home))
    assert p._cfg["session_digest"] is True
