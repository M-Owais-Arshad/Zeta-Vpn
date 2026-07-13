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


class Limiter:
    """Global + per-IP concurrent connection cap.

    Without this, a single client (or a botnet) can open connections as fast
    as the OS accepts them — each one spawns a backend SSH connection plus
    two long-lived pipe tasks — exhausting file descriptors well before
    LimitNOFILE=1000000 is a meaningful ceiling. Plain ints are safe here
    with no lock: asyncio is single-threaded/cooperative and nothing awaits
    between a check and its matching increment/decrement.
    """

    def __init__(self, max_total: int, max_per_ip: int) -> None:
        self.max_total = max_total
        self.max_per_ip = max_per_ip
        self.total = 0
        self.per_ip: dict[str, int] = {}

    def try_acquire(self, ip: str) -> bool:
        if self.total >= self.max_total or self.per_ip.get(ip, 0) >= self.max_per_ip:
            return False
        self.total += 1
        self.per_ip[ip] = self.per_ip.get(ip, 0) + 1
        return True

    def release(self, ip: str) -> None:
        self.total = max(0, self.total - 1)
        remaining = self.per_ip.get(ip, 0) - 1
        if remaining <= 0:
            self.per_ip.pop(ip, None)
        else:
            self.per_ip[ip] = remaining


async def pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter, idle_timeout: float) -> None:
    try:
        while True:
            try:
                data = await asyncio.wait_for(reader.read(65536), timeout=idle_timeout)
            except asyncio.TimeoutError:
                log.info("closing idle connection (no data for %ss)", idle_timeout)
                break
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


async def handle(client_r, client_w, backend_host, backend_port, response, limiter, idle_timeout) -> None:
    peer = client_w.get_extra_info("peername")
    ip = peer[0] if peer else "unknown"

    if not limiter.try_acquire(ip):
        log.warning("rejecting connection from %s: connection limit reached (total=%d, per-ip max=%d)",
                    ip, limiter.total, limiter.max_per_ip)
        with_suppress(client_w.close)
        return

    try:
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
            pipe(client_r, backend_w, idle_timeout),
            pipe(backend_r, client_w, idle_timeout),
            return_exceptions=True,
        )
    finally:
        limiter.release(ip)


async def main() -> None:
    ap = argparse.ArgumentParser(description="ZetaVPN SSH-over-WebSocket proxy")
    ap.add_argument("--listen", default="0.0.0.0:8880", help="host:port to listen on")
    ap.add_argument("--backend", default="127.0.0.1:22", help="backend SSH host:port")
    ap.add_argument("--response", default=DEFAULT_RESPONSE, help="handshake response line")
    ap.add_argument("--max-connections", type=int, default=2000, help="global concurrent connection cap")
    ap.add_argument("--max-per-ip", type=int, default=40, help="per-source-IP concurrent connection cap")
    ap.add_argument("--idle-timeout", type=float, default=600, help="close a pipe after this many seconds with no data")
    args = ap.parse_args()

    lhost, lport = args.listen.rsplit(":", 1)
    bhost, bport = args.backend.rsplit(":", 1)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    limiter = Limiter(args.max_connections, args.max_per_ip)

    server = await asyncio.start_server(
        lambda r, w: handle(r, w, bhost, int(bport), args.response, limiter, args.idle_timeout),
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
