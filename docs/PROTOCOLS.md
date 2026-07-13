# ZetaVPN — Protocols

## Proxy protocols

| Protocol | Core | Transports | Security | Credential | Best for |
|---|---|---|---|---|---|
| **VLESS** | Xray | tcp, ws, grpc, httpupgrade, xhttp | none, tls, **reality** | UUID | Default. REALITY needs no domain/cert and resists DPI. |
| **VMess** | Xray | tcp, ws, grpc, httpupgrade | none, tls | UUID | Max client compatibility; great behind a CDN over WS. |
| **Trojan** | Xray | tcp, ws, grpc | tls, reality | password | Looks like plain HTTPS. |
| **Shadowsocks** | Xray | tcp (+udp) | none | password | Lightweight; use the 2022 AEAD ciphers. |
| **SOCKS5** | Xray | tcp | none | user/pass | LAN/local use — never expose unauthenticated. |
| **HTTP** | Xray | tcp | none, tls | user/pass | HTTP CONNECT proxy. |
| **Hysteria2** | sing-box | udp (QUIC) | tls | password | Lossy / high-latency links. Needs a TLS cert. |
| **TUIC v5** | sing-box | udp (QUIC) | tls | uuid + password | Fast UDP relay with 0-RTT. Needs a TLS cert. |

### Choosing one

- **No domain yet?** Use **VLESS + REALITY** — the panel auto-generates the X25519 keypair and
  shortId, and borrows a real site's TLS handshake (default `www.apple.com`). Nothing to buy.
- **Behind Cloudflare / a CDN?** Use **VLESS-WS-TLS** or **VMess-WS-TLS** and point the CDN at your
  WS path.
- **Unstable mobile network?** Add **Hysteria2** (needs your domain's cert).
- **Want it to look like HTTPS?** **Trojan-TLS**.

Reference inbound JSON for each is in [`config/templates/`](../config/templates).

## Transports (Xray `network`)

- **tcp** — plain or with a header; pairs with REALITY for Vision flow.
- **ws** — WebSocket; CDN-friendly. Set a `path` and optional `Host`.
- **grpc** — multiplexed; set a `serviceName`.
- **httpupgrade** — lighter WS alternative.
- **xhttp** — the modern split-HTTP transport (successor to splithttp).

## Security layers

- **tls** — standard TLS; provide `serverName` + certificate/key paths
  (`/etc/zetavpn/certs/fullchain.pem` after `install_ssl.sh`).
- **reality** — TLS camouflage with no real certificate; the panel manages `privateKey`/`publicKey`,
  `shortIds`, `dest` and `serverNames`. Strongest against active probing, especially with
  `flow: xtls-rprx-vision` on raw TCP (ZetaVPN sets this automatically for VLESS+REALITY+TCP).

## SSH / tunnelling stack

Installed by `scripts/install_ssh_stack.sh`; accounts are created from **SSH Accounts** in the panel
(or `zeta` CLI) as real Linux users with an expiry date and a non-interactive shell (`/bin/false`,
tunnelling only).

| Service | Default port | Notes |
|---|---|---|
| OpenSSH | 22 | Tunnelling enabled (`AllowTcpForwarding`, `GatewayPorts`) |
| Dropbear | 143, 149 | Lightweight SSH |
| stunnel (SSH-over-SSL) | 445 | Wraps Dropbear in TLS |
| SSH-over-WebSocket (`zeta-ws`) | 8880 | Ride a CDN via nginx WS path `/zeta-ws` |

**SSH-over-WS-over-CDN:** point Cloudflare (or nginx) at the `/zeta-ws` path, which upgrades to the
`zeta-ws` proxy and pipes to the local SSH server — so SSH traffic looks like ordinary WebSocket
traffic to a CDN edge.

> Roadmap (config plumbing ready): SlowDNS/DNSTT, BadVPN udpgw, OpenVPN, WireGuard, ShadowTLS,
> AnyTLS. See [RESEARCH.md](RESEARCH.md) §5 for the full stack.
