"""``hermes dexi {status,login,logout,config}`` — surfaced only while the
provider is the active ``memory.provider``.

``login`` runs the OAuth flow interactively (browser or paste-back) so a
headless gateway can be authorized once from a terminal; ``logout`` clears
the cached tokens Hermes holds for the ``dexi`` server.
"""
from __future__ import annotations

import json
from typing import Any

from . import config as cfgmod
from .bridge import BridgeError, McpHttpBridge


def register_cli(subparser: Any) -> None:
    subs = subparser.add_subparsers(dest="dexi_command")
    subs.add_parser("status", help="Show the Dexi memory provider status and probe the connection.")
    subs.add_parser("login", help="Authorize Hermes with your Dexi account (browser or paste-back).")
    subs.add_parser("logout", help="Forget the cached Dexi OAuth tokens on this machine.")
    subs.add_parser("config", help="Print the effective Dexi provider configuration.")
    subparser.set_defaults(func=run)


def _hermes_home() -> str | None:
    try:  # pragma: no cover - Hermes-only
        from hermes_constants import get_hermes_home  # type: ignore

        return str(get_hermes_home())
    except Exception:  # noqa: BLE001
        return None


def run(args: Any) -> int:
    command = getattr(args, "dexi_command", None)
    if command == "status":
        return _status(probe=True)
    if command == "login":
        return _login()
    if command == "logout":
        return _logout()
    if command == "config":
        return _status(probe=False)
    print("usage: hermes dexi {status,login,logout,config}")
    return 0


def _print_config(cfg: dict[str, Any], home: str | None) -> None:
    print(f"config_path: {cfgmod.config_path(home)}")
    for key in sorted(cfgmod.DEFAULTS):
        print(f"{key + ':':<24}{cfg[key]}")


def _status(*, probe: bool) -> int:
    home = _hermes_home()
    cfg = cfgmod.load_config(home)
    print("provider:    dexi")
    _print_config(cfg, home)
    if not probe:
        return 0
    bridge = McpHttpBridge(cfg["mcp_url"], server_name=cfgmod.SERVER_NAME,
                           oauth_config=cfgmod.oauth_config(cfg), hermes_home=home)
    try:
        out = bridge.call_tool("list_folders", {"intent": "hermes dexi status"}, timeout=20.0)
    except BridgeError as exc:
        print(f"connection:  FAILED — {exc}")
        print("hint:        run `hermes dexi login` to authorize (opens a browser, or paste the redirect URL back)")
        return 1
    finally:
        bridge.stop()
    structured = out.get("structuredContent") or {}
    folders = structured.get("folders") or []
    print(f"connection:  ok ({len(folders)} folders, {structured.get('unfiled_count', '?')} unfiled notes)")
    return 0


def _login() -> int:
    home = _hermes_home()
    cfg = cfgmod.load_config(home)
    bridge = McpHttpBridge(cfg["mcp_url"], server_name=cfgmod.SERVER_NAME,
                           oauth_config=cfgmod.oauth_config(cfg), hermes_home=home)
    try:
        try:  # pragma: no cover - Hermes-only: allow prompts even without a TTY heuristic
            from tools.mcp_oauth import force_interactive_oauth  # type: ignore
        except Exception:  # noqa: BLE001
            import contextlib

            force_interactive_oauth = contextlib.nullcontext
        with force_interactive_oauth():
            # Ten minutes: Dexi's authorize request expires then anyway.
            out = bridge.call_tool("list_folders", {"intent": "hermes dexi login"}, timeout=600.0)
    except BridgeError as exc:
        print(f"login failed: {exc}")
        return 1
    finally:
        bridge.stop()
    structured = out.get("structuredContent") or {}
    print(f"authorized — {len(structured.get('folders') or [])} folders visible. "
          f"Manage this connection at {cfgmod.APP_URL}/dashboard/settings (Connected apps).")
    return 0


def _logout() -> int:
    home = _hermes_home()
    try:  # pragma: no cover - Hermes-only
        try:
            from tools.mcp_oauth_manager import get_manager  # type: ignore

            get_manager().remove(cfgmod.SERVER_NAME, hermes_home=home)
        except ImportError:
            from tools.mcp_oauth import remove_oauth_tokens  # type: ignore

            remove_oauth_tokens(cfgmod.SERVER_NAME, hermes_home=home)
        print("cached Dexi tokens removed. Revoke the grant itself in Dexi → Settings → Connected apps.")
        return 0
    except ImportError:
        print(json.dumps({"error": "not running inside Hermes; nothing to remove"}))
        return 1
