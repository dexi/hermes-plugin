"""Provider configuration: ``<hermes_home>/dexi.json`` plus env overrides.

Non-secret only — there is no API key. Auth is OAuth against Dexi's MCP
server, and the tokens live where Hermes keeps every MCP server's tokens
(``<hermes_home>/mcp-tokens/dexi*.json``, managed by Hermes itself).
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

PLUGIN_NAME = "dexi"
SERVER_NAME = "dexi"  # mcp-tokens key; shared with an `mcp_servers.dexi` entry
DEFAULT_MCP_URL = "https://mcp.dexi.net/mcp"
APP_URL = "https://app.dexi.net"

DEFAULTS: dict[str, Any] = {
    "mcp_url": DEFAULT_MCP_URL,
    # Recall relevant notes before each turn and inject them as context.
    "auto_recall": True,
    "recall_results": 5,
    # Similarity floor for semantic hits injected by prefetch (0-1).
    "recall_min_similarity": 0.55,
    # Write one distilled digest note per session (opt-in: text leaves the device).
    "session_digest": False,
    "digest_tag": "#hermes",
    # Request notes:read only; every write tool then errors on Dexi's side.
    "read_only": False,
    # Per-call timeouts (seconds). Prefetch is on the hot path.
    "prefetch_timeout": 2.5,
    "tool_timeout": 30.0,
}

_BOOL_KEYS = {"auto_recall", "session_digest", "read_only"}
_FLOAT_KEYS = {"recall_min_similarity", "prefetch_timeout", "tool_timeout"}
_INT_KEYS = {"recall_results"}


def config_path(hermes_home: str | os.PathLike | None) -> Path:
    home = Path(hermes_home) if hermes_home else Path(os.environ.get("HERMES_HOME", "~/.hermes"))
    return home.expanduser() / f"{PLUGIN_NAME}.json"


def _coerce(key: str, value: Any) -> Any:
    if key in _BOOL_KEYS:
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on"}
        return bool(value)
    if key in _FLOAT_KEYS:
        return float(value)
    if key in _INT_KEYS:
        return int(value)
    return value


def load_config(hermes_home: str | os.PathLike | None) -> dict[str, Any]:
    cfg = dict(DEFAULTS)
    path = config_path(hermes_home)
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 - a corrupt file must not break boot
            data = {}
        if isinstance(data, dict):
            for key, value in data.items():
                if key in DEFAULTS and value is not None:
                    try:
                        cfg[key] = _coerce(key, value)
                    except (TypeError, ValueError):
                        pass
    # Env overrides (DEXI_MCP_URL, DEXI_AUTO_RECALL, ...) for scripted setups.
    for key in DEFAULTS:
        env = os.environ.get(f"DEXI_{key.upper()}")
        if env is not None and env != "":
            try:
                cfg[key] = _coerce(key, env)
            except (TypeError, ValueError):
                pass
    cfg["digest_tag"] = normalize_tag(cfg.get("digest_tag"))
    return cfg


def normalize_tag(raw: Any) -> str:
    """Dexi hashtags are ``\\w`` runs (``#hermes-e2e`` parses as ``#hermes``),
    lowercase; coerce the configured tag so it round-trips as one tag."""
    tag = re.sub(r"[^\w]+", "_", str(raw or "").strip().lstrip("#")).strip("_").lower()
    return f"#{tag}" if tag else "#hermes"


def save_config(values: dict[str, Any], hermes_home: str | os.PathLike | None) -> None:
    path = config_path(hermes_home)
    path.parent.mkdir(parents=True, exist_ok=True)
    existing: dict[str, Any] = {}
    if path.is_file():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                existing = loaded
        except Exception:  # noqa: BLE001
            existing = {}
    for key, value in (values or {}).items():
        if key in DEFAULTS and value is not None and value != "":
            existing[key] = _coerce(key, value)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(existing, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def oauth_config(cfg: dict[str, Any]) -> dict[str, Any]:
    """The ``oauth:`` block Hermes' OAuth helper understands."""
    out: dict[str, Any] = {"client_name": "Hermes Agent (Dexi memory)"}
    if cfg.get("read_only"):
        out["scope"] = "notes:read"
    return out


def note_url(note_id: str) -> str:
    return f"{APP_URL}/dashboard/notes/{note_id}"
