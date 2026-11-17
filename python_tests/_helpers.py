"""Helpers for the compatibility tests that talk to a live ``ToneBackend`` server.

A small toolkit on top of ``TTSServer``/``TTSClient`` so each test reads as intent, not
transport plumbing. Stdlib + websockets only: no mlx, no numpy.
"""

from __future__ import annotations

import asyncio
import contextlib

from tts_server.client import TTSClient
from tts_server.server import ServerConfig, TTSServer


@contextlib.asynccontextmanager
async def running_server(backend):
    """Run ``backend`` behind a TTSServer on an ephemeral loopback TCP port."""
    srv = TTSServer(
        backend,
        ServerConfig(host="127.0.0.1", port=0, reject_browser_origins=False),
    )
    await srv.start()
    try:
        yield srv
    finally:
        await srv.shutdown()


@contextlib.asynccontextmanager
async def connected_client(srv: TTSServer):
    """Connect a TTSClient to a running server; yields ``(client, hello)``."""
    port = srv.listening_port()
    assert port is not None
    c = TTSClient(host="127.0.0.1", port=port)
    hello = await c.connect()
    try:
        yield c, hello
    finally:
        await c.close()


async def next_event(client: TTSClient, types, *, timeout: float = 3.0) -> dict:
    """Return the next server event whose ``type`` is in ``types``."""
    if isinstance(types, str):
        types = {types}

    async def _read() -> dict:
        async for ev in client.events():
            if ev.get("type") in types:
                return ev
        raise AssertionError(f"socket closed before any of {types} arrived")

    return await asyncio.wait_for(_read(), timeout)
