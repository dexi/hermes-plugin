"""Soft import of Hermes' ``MemoryProvider`` ABC with a local stand-in.

``agent.memory_provider`` only exists inside a Hermes install. The provider
must still import (and be unit-testable) outside one, so fall back to a
minimal base whose optional hooks are no-ops. Inside Hermes the real ABC is
used, so the abstract surface is enforced there.
"""
from __future__ import annotations

from typing import Any


class _FallbackMemoryProviderBase:
    def unavailable_reason(self) -> str:
        return ""

    def system_prompt_block(self) -> str:
        return ""

    def prefetch(self, query: str, *, session_id: str = "", **_kw: Any) -> str:
        return ""

    def queue_prefetch(self, query: str, *, session_id: str = "", **_kw: Any) -> None:
        return None

    def sync_turn(self, user_content: str, assistant_content: str, *,
                  session_id: str = "", messages: list | None = None, **_kw: Any) -> None:
        return None

    def on_turn_start(self, turn_number: int, message: str, **_kw: Any) -> None:
        return None

    def on_session_end(self, messages: list, **_kw: Any) -> None:
        return None

    def on_session_switch(self, new_session_id: str, **_kw: Any) -> None:
        return None

    def on_pre_compress(self, messages: list, **_kw: Any) -> str:
        return ""

    def on_memory_write(self, action: str, target: str, content: str,
                        metadata: dict | None = None, **_kw: Any) -> None:
        return None

    def get_config_schema(self) -> list[dict[str, Any]]:
        return []

    def save_config(self, values: dict[str, Any], hermes_home: str) -> None:
        return None

    def backup_paths(self) -> list[str]:
        return []

    def shutdown(self) -> None:
        return None


try:  # pragma: no cover - only inside a Hermes install
    from agent.memory_provider import MemoryProvider as _HermesMemoryProvider

    MemoryProvider: type = _HermesMemoryProvider
    HAS_HERMES_ABC = True
except Exception:  # noqa: BLE001 - any import failure means "not in Hermes"
    MemoryProvider = _FallbackMemoryProviderBase
    HAS_HERMES_ABC = False


def is_trivial_prompt(text: str | None) -> bool:
    """Hermes' own gate when available; a conservative local copy otherwise."""
    try:  # pragma: no cover
        from agent.memory_provider import is_trivial_prompt as _hermes_gate

        return bool(_hermes_gate(text))
    except Exception:  # noqa: BLE001
        pass
    if not text:
        return True
    stripped = text.strip()
    if not stripped or stripped.startswith("/"):
        return True
    words = stripped.rstrip("!.?").lower().split()
    return len(words) <= 2 and words[0] in {
        "hi", "hello", "hey", "yo", "thanks", "thank", "ok", "okay", "yes", "no",
        "sure", "cool", "great", "nice", "bye", "good", "morning", "evening",
    }
