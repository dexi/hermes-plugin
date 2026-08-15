"""Dexi memory provider for Hermes Agent.

Loaded in-process by Hermes; ``register(ctx)`` wires the provider and the
three skills. Also the pip entry point (``hermes_agent.memory_providers``).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .cli import register_cli
from .config import PLUGIN_NAME
from .provider import DexiMemoryProvider

__version__ = "0.1.0"

SKILLS_DIR = Path(__file__).resolve().parent.parent / "skills"
_SKILLS = (
    ("capture", "Save distilled knowledge to the user's Dexi notes."),
    ("recall", "Search the user's Dexi notes before answering from memory."),
    ("review", "Run a spaced-repetition review session over due Dexi notes."),
)


def register(ctx: Any) -> None:
    """Register the memory provider (+ skills). Tolerates minimal test contexts."""
    method = getattr(ctx, "register_memory_provider", None)
    if callable(method):
        try:
            method(DexiMemoryProvider())
        except Exception:  # noqa: BLE001 - never break plugin loading
            pass
    reg_skill = getattr(ctx, "register_skill", None)
    if callable(reg_skill):
        for name, description in _SKILLS:
            path = SKILLS_DIR / name / "SKILL.md"
            if path.is_file():
                try:
                    reg_skill(name, path, description)
                except Exception:  # noqa: BLE001
                    pass


__all__ = ["DexiMemoryProvider", "PLUGIN_NAME", "register", "register_cli", "__version__"]
