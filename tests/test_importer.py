"""``hermes dexi import`` — discovery, section planning, manifest and runner
against the fake bridge; no network."""
from __future__ import annotations

import json
from types import SimpleNamespace

from hermes_dexi import cli
from hermes_dexi import config as cfgmod
from hermes_dexi.bridge import BridgeError, FakeBridge
from hermes_dexi.importer import (
    ImportState, Section, SourceFile, discover_memory_files, plan_import, run_import, split_sections,
)

MEMORY_MD = """# Long-term memory

Preamble the agent wrote before any section.

## Preferences
- Likes terse answers
- Timezone: Pacific

## Projects
### Atlas
API rewrite, due Q1.

## Empty section
"""


def _workspace(tmp_path, files):
    for rel, content in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    return tmp_path


def test_split_sections_title_and_levels():
    title, sections = split_sections(MEMORY_MD)
    assert title == "Long-term memory"
    assert [s.heading for s in sections] == [None, "Preferences", "Projects"]
    assert sections[0].body == "Preamble the agent wrote before any section."
    assert sections[2].body == "### Atlas\nAPI rewrite, due Q1."
    assert split_sections("just facts\nmore facts") == (None, [Section(None, "just facts\nmore facts")])
    assert split_sections("\n\n") == (None, [])
    # several level-1 headings → none is the title; a lone level-2 heading is a section
    assert split_sections("# A\none\n# B\ntwo") == (None, [Section("A", "one"), Section("B", "two")])
    assert split_sections("## Name\nMaya") == (None, [Section("Name", "Maya")])


def test_discover_memory_layout(tmp_path):
    ws = _workspace(tmp_path, {
        "MEMORY.md": MEMORY_MD, "USER.md": "## Name\nMaya", "AGENTS.md": "not memory", "SOUL.md": "not memory",
        "memory/2026-10-14.md": "# 2026-10-14\n- asked about Railway ports", "memory/2026-10-15.md": "- pgvector",
        "memory/notes.txt": "ignored", "memory/.hidden.md": "ignored",
    })
    assert [f.rel_path for f in discover_memory_files(ws)] == [
        "MEMORY.md", "USER.md", "memory/2026-10-14.md", "memory/2026-10-15.md"]
    assert [f.rel_path for f in discover_memory_files(ws, all_files=True)] == [
        "AGENTS.md", "MEMORY.md", "SOUL.md", "USER.md", "memory/2026-10-14.md", "memory/2026-10-15.md"]
    mem = _workspace(tmp_path / "h", {"memories/MEMORY.md": "facts", "memories/USER.md": "profile", "memories/x.md": "x"})
    assert [f.rel_path for f in discover_memory_files(mem / "memories")] == ["MEMORY.md", "USER.md", "x.md"]
    assert discover_memory_files(mem / "memories" / "USER.md") == [SourceFile("USER.md", "profile")]
    assert discover_memory_files(tmp_path / "nope") == []


def test_plan_curated_file_sections():
    plan = plan_import([SourceFile("MEMORY.md", MEMORY_MD)], agent_tag="#hermes")
    assert [n.title for n in plan] == ["Memory", "Memory: Preferences", "Memory: Projects"]
    assert [n.key for n in plan] == ["MEMORY.md", "MEMORY.md#preferences", "MEMORY.md#projects"]
    assert plan[1].text == ("#hermes #memory #imported Memory imported from MEMORY.md.\n\n"
                            "- Likes terse answers\n- Timezone: Pacific\n\nimported_from:: MEMORY.md")
    assert len(plan[1].hash) == 16


def test_plan_daily_logs_user_profile_and_dedupe():
    plan = plan_import([
        SourceFile("memory/2026-10-14.md", "# 2026-10-14\n## Morning\n- asked about Railway ports"),
        SourceFile("memory/2026-10-15.md", "- asked about pgvector"),
        SourceFile("USER.md", "## Name\nMaya"),
        SourceFile("memory/empty.md", "\n"),
    ], agent_tag="#memory")
    assert [n.title for n in plan] == ["Memory log 2026-10-14", "Memory log 2026-10-15", "User profile: Name"]
    assert plan[0].text == ("#memory #imported Memory imported from memory/2026-10-14.md.\n\n"
                            "## Morning\n- asked about Railway ports\n\n"
                            "logged:: 2026-10-14\nimported_from:: memory/2026-10-14.md")
    assert "logged:: 2026-10-15" in plan[1].text
    big = plan_import([SourceFile("m.md", "## Same\na\n## Same\n" + "b" * 40_000)], agent_tag="#hermes")
    assert [n.key for n in big] == ["m.md#same", "m.md#same-2"]
    assert len(big[1].text) <= 30_000 and big[1].text.endswith("…")


def _plan(body="## A\none\n## B\ntwo"):
    return plan_import([SourceFile("MEMORY.md", body)], agent_tag="#hermes")


def test_run_import_creates_skips_and_updates(tmp_path):
    counter = {"n": 0}

    def handler(name, args):
        if name == "create_note":
            counter["n"] += 1
            return {"id": f"note-{counter['n']}", "title": args["title"]}
        if name == "update_note":
            return {"id": args["note_id"]}
        raise AssertionError(name)

    bridge = FakeBridge(handler=handler)
    state = ImportState(tmp_path / "dexi-imports.json")
    lines: list[str] = []
    s = run_import(bridge, _plan(), state, out=lines.append, note_url=cfgmod.note_url)
    assert (s.created, s.updated, s.unchanged, s.failed) == (2, 0, 0, 0)
    creates = [c for c in bridge.calls if c[0] == "create_note"]
    assert [c[1]["title"] for c in creates] == ["Memory: A", "Memory: B"]
    assert creates[0][1]["intent"] == "memory import"
    assert lines[0].endswith(cfgmod.note_url("note-1"))
    on_disk = json.loads((tmp_path / "dexi-imports.json").read_text())
    assert on_disk["entries"]["MEMORY.md#a"]["note_id"] == "note-1"

    # unchanged on re-run, even from a fresh state object reading the manifest
    s = run_import(bridge, _plan(), ImportState(tmp_path / "dexi-imports.json"), out=lines.append)
    assert (s.created, s.updated, s.unchanged, s.failed) == (0, 0, 2, 0)
    assert len([c for c in bridge.calls if c[0] == "create_note"]) == 2

    changed = _plan("## A\none, revised\n## B\ntwo")
    s = run_import(bridge, changed, state, out=lines.append)
    assert (s.created, s.updated, s.unchanged, s.failed) == (0, 1, 1, 0)
    upd = [c for c in bridge.calls if c[0] == "update_note"]
    assert len(upd) == 1
    assert upd[0][1]["note_id"] == "note-1" and upd[0][1]["mode"] == "replace" and "one, revised" in upd[0][1]["text"]
    assert state.get("MEMORY.md#a")["hash"] == changed[0].hash


def test_run_import_dry_run_and_failures():
    bridge = FakeBridge(handler=lambda name, args: (_ for _ in ()).throw(AssertionError("network")))
    state = ImportState(None)
    lines: list[str] = []
    s = run_import(bridge, _plan(), state, dry_run=True, out=lines.append)
    assert (s.created, s.updated, s.unchanged, s.failed) == (2, 0, 0, 0)
    assert bridge.calls == [] and "would create: Memory: A" in lines[0]

    def flaky(name, args):
        if name == "create_note" and args["title"] == "Memory: A":
            return BridgeError("note cap reached")
        return {"id": "new-1"}

    s = run_import(FakeBridge(handler=flaky), _plan(), state, out=lines.append)
    assert (s.created, s.updated, s.unchanged, s.failed) == (1, 0, 0, 1)
    assert any("FAILED: Memory: A — note cap reached" in l for l in lines)
    assert state.get("MEMORY.md#a") is None and state.get("MEMORY.md#b")["note_id"] == "new-1"


def test_cli_import_dry_run_and_no_files(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setattr(cli, "_hermes_home", lambda: None)
    assert cfgmod.memories_dir(None) == tmp_path / "memories"
    rc = cli.run(SimpleNamespace(dexi_command="import", path=None, tag=None, all_files=False, dry_run=True))
    assert rc == 1 and "no memory files under" in capsys.readouterr().out
    _workspace(tmp_path, {"memories/MEMORY.md": "## Likes\nterse answers", "memories/USER.md": "## Name\nMaya"})
    rc = cli.run(SimpleNamespace(dexi_command="import", path=None, tag="OpenClaw", all_files=False, dry_run=True))
    out = capsys.readouterr().out
    assert rc == 0
    assert "2 file(s) → 2 note(s)" in out and "would create: Memory: Likes" in out and "would create 2, update 0" in out
    assert not (tmp_path / "dexi-imports.json").exists()
    # read-only connections refuse a real import but still allow a dry run
    cfgmod.save_config({"read_only": True}, tmp_path)
    rc = cli.run(SimpleNamespace(dexi_command="import", path=None, tag=None, all_files=False, dry_run=False))
    assert rc == 1 and "read-only" in capsys.readouterr().out
