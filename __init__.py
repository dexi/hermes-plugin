"""Hermes plugin entry for Dexi.

``hermes plugins install dexi/hermes-plugin`` clones this repository to
``~/.hermes/plugins/dexi/`` and Hermes' memory-provider loader imports the
repo root as ``_hermes_user_memory.dexi`` — with that synthetic parent
registered in ``sys.modules`` first, so the relative import below resolves.
The implementation lives in ``hermes_dexi/`` (also the pip package). The
literal ``MemoryProvider`` / ``register_memory_provider`` tokens in this file
keep Hermes' text-scan discovery recognizing the directory as a provider.
"""
from __future__ import annotations

try:
    from .hermes_dexi import DexiMemoryProvider, register, register_cli  # MemoryProvider subclass
except ImportError:  # imported without a parent package (tests, ad-hoc tooling)
    from hermes_dexi import DexiMemoryProvider, register, register_cli  # type: ignore

__all__ = ["DexiMemoryProvider", "register", "register_cli"]
