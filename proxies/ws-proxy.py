#!/usr/bin/env python3
"""ZetaVPN SSH-over-WebSocket / HTTP-Upgrade proxy.

A tiny, dependency-free asyncio TCP proxy of the kind used by tunnelling clients
(HTTP Injector, HTTP Custom, etc.). It answers the client's initial HTTP request
with a ``101 Switching Protocols`` response, then transparently pipes the
connection to a backend SSH server (OpenSSH or Dropbear). Put it behind nginx +
TLS (or Cloudflare) to get SSH-over-WS-over-CDN.

Usage:
    ws-proxy.py --listen 0.0.0.0:8880 --backend 127.0.0.1:22
"""

from __future__ import annotations

import argparse
import asyncio
import logging

log = logging.getLogger("zeta-ws")

DEFAULT_RESPONSE = "HTTP/1.1 101 Switching Protocols\r\n\r\n"


async def pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while True:
            data = await reader.read(65536)
            if not data:
                break
            writer.write(data)
            await writer.drain()
    except (ConnectionResetError, BrokenPipeError, asyncio.IncompleteReadError):
        pass
    finally:
        with_suppress(writer.close)


def with_suppress(fn) -> None:
    try:
        fn()
    except Exception:  # noqa: BLE001
        pass


async def handle(client_r, client_w, backend_host, backend_port, response) -> None:
    peer = client_w.get_extra_info("peername")
    try:
        # Read (and discard) the client's HTTP handshake, then switch protocols.
        try:
            await asyncio.wait_for(client_r.read(4096), timeout=10)
        except asyncio.TimeoutError:
            pass
        client_w.write(response.encode("latin-1"))
        await client_w.drain()

        backend_r, backend_w = await asyncio.open_connection(backend_host, backend_port)
    except OSError as exc:
        log.warning("backend connect failed for %s: %s", peer, exc)
        with_suppress(client_w.close)
        return

    await asyncio.gather(
        pipe(client_r, backend_w),
        pipe(backend_r, client_w),
        return_exceptions=True,
    )


async def main() -> None:
    ap = argparse.ArgumentParser(description="ZetaVPN SSH-over-WebSocket proxy")
    ap.add_argument("--listen", default="0.0.0.0:8880", help="host:port to listen on")
    ap.add_argument("--backend", default="127.0.0.1:22", help="backend SSH host:port")
    ap.add_argument("--response", default=DEFAULT_RESPONSE, help="handshake response line")
    args = ap.parse_args()

    lhost, lport = args.listen.rsplit(":", 1)
    bhost, bport = args.backend.rsplit(":", 1)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    server = await asyncio.start_server(
        lambda r, w: handle(r, w, bhost, int(bport), args.response),
        lhost or "0.0.0.0",
        int(lport),
    )
    log.info("ZetaVPN WS proxy listening on %s -> %s", args.listen, args.backend)
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
