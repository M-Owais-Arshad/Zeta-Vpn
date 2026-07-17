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
import socket

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

    def acquire_slot(self) -> bool:
        """Reserve a GLOBAL slot before the client IP is known.

        The real client IP is only recoverable after the (up-to-10s) handshake
        read, but a connection must be counted BEFORE that read — otherwise a
        flood of clients that connect and never send bytes parks unbounded
        pre-handshake sockets/coroutines, defeating the cap. Per-IP is charged
        later, once the IP is parsed, via charge_ip().
        """
        if self.total >= self.max_total:
            return False
        self.total += 1
        return True

    def charge_ip(self, ip: str) -> bool:
        """Attribute an already-reserved slot to `ip`, enforcing the per-IP cap.

        Returns False if `ip` is already at the cap — the caller then rejects
        and releases the global slot (passing ip=None, since nothing was
        charged to this IP).
        """
        if self.per_ip.get(ip, 0) >= self.max_per_ip:
            return False
        self.per_ip[ip] = self.per_ip.get(ip, 0) + 1
        return True

    def release(self, ip: str | None) -> None:
        self.total = max(0, self.total - 1)
        if ip is None:  # slot was reserved but never charged to an IP
            return
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
            # Bound the write too: an unbounded drain() on a peer that vanished
            # mid-transfer (no FIN/RST) blocks until the kernel's ~15-min
            # TCP_RETRIES2 window, pinning this connection's global + per-IP slot
            # the whole time. Cap it at idle_timeout so a stalled writer is
            # reclaimed on the same timescale as an idle reader.
            try:
                await asyncio.wait_for(writer.drain(), timeout=idle_timeout)
            except asyncio.TimeoutError:
                log.info("closing stalled connection (drain blocked %ss)", idle_timeout)
                break
    except (ConnectionResetError, BrokenPipeError, asyncio.IncompleteReadError):
        pass
    finally:
        with_suppress(writer.close)


def with_suppress(fn) -> None:
    try:
        fn()
    except Exception:  # noqa: BLE001
        pass


def enable_keepalive(writer: asyncio.StreamWriter) -> None:
    """Turn on TCP keepalive for a stream's socket.

    A peer that disappears without a FIN/RST (mobile radio loss, app killed
    mid-session) is otherwise only reclaimed at the kernel's ~15-minute
    TCP_RETRIES2 timeout — holding its global/per-IP slot the entire time.
    Keepalive probes detect the dead peer in ~2 minutes (60s idle + 4×15s) and
    let the OS drop the socket, freeing the slot. Best-effort: platforms without
    a given TCP_KEEP* option simply skip it."""
    sock = writer.get_extra_info("socket")
    if sock is None:
        return
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
        for name, val in (("TCP_KEEPIDLE", 60), ("TCP_KEEPINTVL", 15), ("TCP_KEEPCNT", 4)):
            opt = getattr(socket, name, None)
            if opt is not None:
                sock.setsockopt(socket.IPPROTO_TCP, opt, val)
    except OSError:
        pass


_LOOPBACK = {"127.0.0.1", "::1", "localhost"}


def _client_ip(raw: bytes, peer_ip: str) -> str:
    """The IP to charge the per-IP cap against.

    When nginx fronts us the peer is always 127.0.0.1, so every user would
    share one 127.0.0.1 bucket and the per-IP cap would collapse into a single
    global budget. Pull the real client from the headers nginx sets — but ONLY
    when the peer is loopback (i.e. actually nginx), so a direct :8880 client
    can't spoof its IP to dodge the cap.

    Trust order matters: nginx sets `X-Real-IP $remote_addr`, which OVERWRITES
    any client-supplied value, so it's authoritative — prefer it. It also sets
    `X-Forwarded-For $proxy_add_x_forwarded_for`, which PREPENDS the client's
    own (spoofable) XFF ahead of the real remote_addr, so the trustworthy value
    is the RIGHTMOST element, never the leftmost. Taking the leftmost element
    (as before) let a client set `X-Forwarded-For: <random>` per handshake and
    land in a fresh per-IP bucket every time, bypassing the cap entirely.
    """
    if peer_ip not in _LOOPBACK:
        return peer_ip
    try:
        head = raw.split(b"\r\n\r\n", 1)[0].decode("latin-1", "replace")
    except Exception:  # noqa: BLE001
        return peer_ip
    xff = xreal = None
    for line in head.split("\r\n")[1:]:
        name, _, value = line.partition(":")
        n, v = name.strip().lower(), value.strip()
        if n == "x-forwarded-for" and v:
            xff = v.split(",")[-1].strip()  # rightmost = the addr nginx appended
        elif n == "x-real-ip" and v:
            xreal = v
    return xreal or xff or peer_ip


async def handle(client_r, client_w, backend_host, backend_port, response, limiter, idle_timeout) -> None:
    peer = client_w.get_extra_info("peername")
    peer_ip = peer[0] if peer else "unknown"

    # Reserve a GLOBAL slot up-front — before the (up-to-10s) handshake read —
    # so connections that never send bytes are bounded by the global cap
    # instead of parking unbounded pre-handshake sockets. The real per-IP charge
    # happens after we parse the client IP (which needs the handshake headers).
    if not limiter.acquire_slot():
        log.warning("rejecting connection from %s: global connection limit reached (max=%d)",
                    peer_ip, limiter.max_total)
        with_suppress(client_w.close)
        return
    # Detect a vanished client fast so its slot is freed in ~2 min, not ~15.
    enable_keepalive(client_w)

    ip = None
    try:
        # Read (and keep) the client's HTTP handshake so we can recover the real
        # client IP from X-Forwarded-For when nginx fronts us. The request is
        # then discarded (injector-style): after the 101 the tunnel carries raw
        # SSH, not this HTTP request.
        raw = b""
        try:
            raw = await asyncio.wait_for(client_r.read(4096), timeout=10)
        except asyncio.TimeoutError:
            pass
        ip = _client_ip(raw, peer_ip)
        if not limiter.charge_ip(ip):
            log.warning("rejecting connection from %s: per-ip limit reached (max=%d)",
                        ip, limiter.max_per_ip)
            ip = None  # nothing charged to this IP — release() only frees the global slot
            with_suppress(client_w.close)
            return

        try:
            client_w.write(response.encode("latin-1"))
            await client_w.drain()
            backend_r, backend_w = await asyncio.open_connection(backend_host, backend_port)
            enable_keepalive(backend_w)
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
