"""Hermes CLI discovery shim: Hermes imports ``<plugin_root>/cli.py`` as
``_hermes_user_memory.dexi.cli`` (parents pre-registered, root ``__init__``
not executed) to find ``register_cli``. Real implementation: hermes_dexi/cli.py."""
from __future__ import annotations

try:
    from .hermes_dexi.cli import register_cli, run
except ImportError:  # imported without a parent package
    from hermes_dexi.cli import register_cli, run  # type: ignore

__all__ = ["register_cli", "run"]
