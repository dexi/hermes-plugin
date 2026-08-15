"""Sync bridge to Dexi's remote MCP server.

One background thread owns an asyncio loop and a long-lived MCP
``ClientSession`` over streamable HTTP; callers use ``call_tool`` from any
thread with a timeout. Auth is OAuth 2.1 through Hermes' own helper
(``tools.mcp_oauth_manager`` / ``tools.mcp_oauth``), so the browser
loopback + paste-back flow, token cache (``<hermes_home>/mcp-tokens/dexi*``)
and refresh are exactly what an ``mcp_servers.dexi`` entry gets — and the two
share tokens. Outside Hermes (tests, dev) a static bearer can be supplied
via ``DEXI_MCP_BEARER_TOKEN``.

The connection is lazy: nothing touches the network until the first call,
so provider registration and ``is_available()`` stay offline. A dropped
transport is torn down and rebuilt on the next call.
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import contextlib
import json
import logging
import os
import threading
import time
from typing import Any, Protocol, runtime_checkable

logger = logging.getLogger("hermes_dexi.bridge")


class BridgeError(RuntimeError):
    """A tool call could not be completed (transport, auth, or server error)."""


class BridgeTimeoutError(BridgeError):
    pass


@runtime_checkable
class Bridge(Protocol):
    def start(self) -> None: ...
    def call_tool(self, name: str, args: dict[str, Any], *, timeout: float | None = None) -> dict[str, Any]: ...
    def list_tools(self, *, timeout: float | None = None) -> list[dict[str, Any]]: ...
    def stop(self) -> None: ...


def _root_cause(exc: BaseException | None) -> str:
    """Innermost message of (possibly nested) ExceptionGroups — what the user
    actually needs to see, e.g. the 401 rather than 'unhandled errors in a
    TaskGroup'."""
    seen = 0
    while exc is not None and seen < 10:
        seen += 1
        subs = getattr(exc, "exceptions", None)
        if subs:
            exc = subs[0]
            continue
        cause = exc.__cause__ or exc.__context__
        if isinstance(exc, (OSError,)) or not cause:
            break
        exc = cause
    if exc is None:
        return ""
    msg = str(exc).strip()
    if "401" in msg or "Unauthorized" in msg:
        return "unauthorized (401) — the Dexi token is missing, expired, or revoked; run `hermes dexi login`"
    return f"{type(exc).__name__}: {msg}" if msg else type(exc).__name__


def _result_to_dict(result: Any) -> dict[str, Any]:
    """MCP ``CallToolResult`` → plain dict {content, structuredContent, isError}."""
    out: dict[str, Any] = {"content": [], "isError": bool(getattr(result, "isError", False))}
    for block in getattr(result, "content", None) or []:
        text = getattr(block, "text", None)
        if isinstance(text, str):
            out["content"].append({"type": "text", "text": text})
    structured = getattr(result, "structuredContent", None)
    if isinstance(structured, dict):
        out["structuredContent"] = structured
    elif out["content"]:
        # fastmcp servers put JSON in the first text block when no schema
        try:
            parsed = json.loads(out["content"][0]["text"])
            if isinstance(parsed, dict):
                out["structuredContent"] = parsed
        except (ValueError, TypeError, KeyError):
            pass
    return out


class McpHttpBridge:
    """Persistent streamable-HTTP MCP client on a private event-loop thread."""

    def __init__(self, url: str, *, server_name: str = "dexi",
                 oauth_config: dict[str, Any] | None = None,
                 hermes_home: str | None = None,
                 connect_timeout: float = 30.0) -> None:
        self.url = url
        self.server_name = server_name
        self.oauth_config = dict(oauth_config or {})
        self.hermes_home = hermes_home
        self.connect_timeout = connect_timeout
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._session: Any = None
        self._connected = threading.Event()
        self._connect_error: BaseException | None = None
        self._stop_evt: asyncio.Event | None = None
        self._lock = threading.Lock()
        self._auth_provider: Any = None

    # -- lifecycle -----------------------------------------------------------

    def start(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._connected.clear()
            self._connect_error = None
            self._loop = asyncio.new_event_loop()
            self._thread = threading.Thread(
                target=self._thread_main, name="hermes-dexi-mcp", daemon=True
            )
            self._thread.start()

    def stop(self) -> None:
        with self._lock:
            loop, thread, stop_evt = self._loop, self._thread, self._stop_evt
            self._loop = self._thread = self._stop_evt = None
        if loop is None or thread is None:
            return
        if stop_evt is not None:
            with contextlib.suppress(RuntimeError):
                loop.call_soon_threadsafe(stop_evt.set)
        thread.join(timeout=5.0)
        self._session = None
        self._connected.clear()

    def _thread_main(self) -> None:
        loop = self._loop
        assert loop is not None
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._serve())
        except BaseException as exc:  # noqa: BLE001 - surface to callers, never crash the host
            self._connect_error = exc
            logger.debug("dexi bridge thread ended: %r", exc)
        finally:
            self._session = None
            self._connected.set()  # release waiters; they'll see no session
            with contextlib.suppress(Exception):
                loop.run_until_complete(loop.shutdown_asyncgens())
            loop.close()

    # -- connection ----------------------------------------------------------

    def _build_auth(self) -> Any:
        """Hermes' OAuth provider for this server (shared with mcp_servers.dexi),
        else a static bearer for non-Hermes environments."""
        if self._auth_provider is not None:
            return self._auth_provider
        try:
            from tools.mcp_oauth_manager import get_manager  # type: ignore

            provider = get_manager().get_or_build_provider(
                self.server_name, self.url, self.oauth_config or None
            )
            if provider is not None:
                self._auth_provider = provider
                return provider
        except ImportError:
            pass
        except Exception as exc:  # noqa: BLE001 - fall through to the legacy helper
            logger.debug("mcp_oauth_manager unavailable: %r", exc)
        try:
            from tools.mcp_oauth import build_oauth_auth  # type: ignore

            provider = build_oauth_auth(self.server_name, self.url, self.oauth_config or None)
            if provider is not None:
                self._auth_provider = provider
                return provider
        except ImportError:
            pass
        token = os.environ.get("DEXI_MCP_BEARER_TOKEN", "").strip()
        if token:
            import httpx

            class _Bearer(httpx.Auth):
                def auth_flow(self, request):
                    request.headers["Authorization"] = f"Bearer {token}"
                    yield request

            self._auth_provider = _Bearer()
            return self._auth_provider
        raise BridgeError(
            "No way to authenticate to Dexi: run inside Hermes (OAuth via "
            "`hermes dexi login`) or set DEXI_MCP_BEARER_TOKEN."
        )

    async def _serve(self) -> None:
        import httpx
        from mcp import ClientSession

        self._stop_evt = asyncio.Event()
        auth = self._build_auth()
        timeout = httpx.Timeout(self.connect_timeout, read=300.0)
        try:
            from mcp.client.streamable_http import streamable_http_client  # mcp >= 1.24
        except ImportError:  # pragma: no cover - older SDKs
            streamable_http_client = None
        try:
            if streamable_http_client is not None:
                async with httpx.AsyncClient(auth=auth, timeout=timeout, follow_redirects=True) as http:
                    # Yields (read, write, get_session_id) on mcp 1.x, (read, write) on 2.x
                    async with streamable_http_client(self.url, http_client=http) as streams:
                        r, w = streams[0], streams[1]
                        async with ClientSession(r, w) as session:
                            await asyncio.wait_for(session.initialize(), timeout=self.connect_timeout)
                            self._session = session
                            self._connected.set()
                            await self._stop_evt.wait()
            else:  # pragma: no cover
                from mcp.client.streamable_http import streamablehttp_client

                async with streamablehttp_client(self.url, auth=auth, timeout=timeout) as streams:
                    r, w = streams[0], streams[1]
                    async with ClientSession(r, w) as session:
                        await asyncio.wait_for(session.initialize(), timeout=self.connect_timeout)
                        self._session = session
                        self._connected.set()
                        await self._stop_evt.wait()
        finally:
            self._session = None

    # -- calls ---------------------------------------------------------------

    def _ensure_session(self, timeout: float | None) -> Any:
        if self._thread is None or not self._thread.is_alive():
            # First use, or the transport died — (re)start.
            self.start()
        deadline = time.monotonic() + (timeout if timeout is not None else 300.0)
        while not self._connected.wait(timeout=0.05):
            if time.monotonic() > deadline:
                raise BridgeTimeoutError(
                    "Timed out connecting to Dexi (first connect may be waiting for the "
                    "OAuth approval in your browser — run `hermes dexi login` to complete it)."
                )
        session = self._session
        if session is None:
            err = self._connect_error
            # Let the next call retry from scratch.
            self.stop()
            raise BridgeError(f"Dexi MCP connection failed: {_root_cause(err)}" if err else
                              "Dexi MCP connection closed")
        return session

    def _run(self, coro_factory, timeout: float | None) -> Any:
        session = self._ensure_session(timeout)
        loop = self._loop
        assert loop is not None
        fut = asyncio.run_coroutine_threadsafe(coro_factory(session), loop)
        try:
            return fut.result(timeout=timeout)
        except concurrent.futures.TimeoutError:
            fut.cancel()
            raise BridgeTimeoutError(f"Dexi call timed out after {timeout}s")
        except Exception as exc:  # noqa: BLE001
            # Transport-level failure → drop the session so the next call reconnects.
            name = type(exc).__name__
            if "Closed" in name or "Connect" in name or "Transport" in name or "Stream" in name:
                self.stop()
            raise BridgeError(_root_cause(exc)) from exc

    def call_tool(self, name: str, args: dict[str, Any], *, timeout: float | None = None) -> dict[str, Any]:
        async def _call(session):
            return await session.call_tool(name, args or {})

        result = self._run(_call, timeout)
        return _result_to_dict(result)

    def list_tools(self, *, timeout: float | None = None) -> list[dict[str, Any]]:
        async def _list(session):
            return await session.list_tools()

        result = self._run(_list, timeout)
        tools = []
        for t in getattr(result, "tools", None) or []:
            tools.append({
                "name": t.name,
                "description": t.description or "",
                "inputSchema": getattr(t, "inputSchema", None) or {"type": "object", "properties": {}},
            })
        return tools


class FakeBridge:
    """In-memory stand-in for tests: canned results keyed by tool name, or a
    handler callable ``(name, args) -> dict``."""

    def __init__(self, handler=None, results: dict[str, Any] | None = None) -> None:
        self.handler = handler
        self.results = dict(results or {})
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.started = 0
        self.stopped = 0

    def start(self) -> None:
        self.started += 1

    def stop(self) -> None:
        self.stopped += 1

    def call_tool(self, name: str, args: dict[str, Any], *, timeout: float | None = None) -> dict[str, Any]:
        self.calls.append((name, dict(args or {})))
        if self.handler is not None:
            out = self.handler(name, args or {})
        else:
            out = self.results.get(name, {})
        if isinstance(out, Exception):
            raise out
        if isinstance(out, dict) and "structuredContent" not in out and "content" not in out:
            out = {"content": [{"type": "text", "text": json.dumps(out)}],
                   "structuredContent": out, "isError": False}
        return out

    def list_tools(self, *, timeout: float | None = None) -> list[dict[str, Any]]:
        return []
