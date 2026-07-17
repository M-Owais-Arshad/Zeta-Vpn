#!/usr/bin/env bash
# Front :80 and :443 with HAProxy so raw SSH, WebSocket/HTTP and TLS all SHARE
# those ports — the "every protocol on every common port" layout the gaming/
# free-net panels use. HAProxy sniffs each new connection and routes:
#   :80  -> raw SSH (client stays silent, waiting for the banner)  -> sshd
#           anything that speaks first (HTTP / WebSocket)          -> nginx
#   :443 -> TLS ClientHello                                        -> nginx (https)
#           raw SSH (silent)                                       -> sshd
# nginx runs on loopback 8080/8443 (proxy_protocol) behind HAProxy, so it still
# sees the real client IP. sshd stays on 22.
#
# Returns 0 only if HAProxy is actually live on 80/443; the caller reverts nginx
# to the public ports on any non-zero exit, so a box without HAProxy is never
# left with an unbound 80/443.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
. "${HERE}/common.sh"
need_root

SSH_PORT="${SSH_PORT:-22}"

msg "Installing HAProxy multiplexer (raw SSH + WS + TLS share :80/:443)"
apt_install haproxy || { warn "haproxy package unavailable — skipping multiplexer"; exit 1; }

cat > /etc/haproxy/haproxy.cfg <<CONF
global
    log /dev/log local0
    maxconn 20000

defaults
    log     global
    mode    tcp
    option  dontlognull
    timeout connect 10s
    timeout client  1h
    timeout server  1h

# :80  — an SSH client opens with the literal "SSH-2.0-..." banner; route that
# to sshd, and everything else (HTTP / WebSocket / bug-host payloads) to nginx.
frontend fe_http
    bind 0.0.0.0:80
    tcp-request inspect-delay 3s
    tcp-request content accept if { req.len ge 4 }
    tcp-request content accept if WAIT_END
    acl is_ssh req.payload(0,4) -m bin 5353482d
    use_backend bk_ssh if is_ssh
    default_backend bk_web

# :443 — TLS ClientHello => nginx (terminates TLS for panel + WS-TLS inbounds);
# an "SSH-" banner (or anything non-TLS) => sshd, so raw SSH shares 443 too.
frontend fe_https
    bind 0.0.0.0:443
    tcp-request inspect-delay 3s
    tcp-request content accept if { req.ssl_hello_type 1 }
    tcp-request content accept if { req.len ge 4 }
    tcp-request content accept if WAIT_END
    acl is_ssh req.payload(0,4) -m bin 5353482d
    use_backend bk_websec if { req.ssl_hello_type 1 }
    use_backend bk_ssh if is_ssh
    default_backend bk_ssh

backend bk_ssh
    server sshd 127.0.0.1:${SSH_PORT}

backend bk_web
    server nginx 127.0.0.1:8080 send-proxy

backend bk_websec
    server nginx 127.0.0.1:8443 send-proxy
CONF

if ! haproxy -c -f /etc/haproxy/haproxy.cfg >/dev/null 2>&1; then
  warn "HAProxy config test failed — not starting it:"
  haproxy -c -f /etc/haproxy/haproxy.cfg 2>&1 | tail -3
  exit 1
fi

systemctl enable haproxy >/dev/null 2>&1 || true
systemctl restart haproxy 2>/dev/null || true
sleep 1
if systemctl is-active --quiet haproxy; then
  ok "HAProxy live on :80/:443 — raw SSH, WebSocket and TLS all multiplexed"
  exit 0
fi
warn "HAProxy did not come up (check 'journalctl -u haproxy') — caller will revert"
exit 1
