#!/usr/bin/env bash
# Install nginx as a reverse proxy: fronts the panel and exposes the SSH-over-WS
# path (so it can ride a CDN like Cloudflare). TLS is wired in when a domain +
# certificate are available.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
. "${HERE}/common.sh"
need_root

PANEL_PORT="${PANEL_PORT:-2096}"
WS_PORT="${WS_PORT:-8880}"
DOMAIN="${ZETA_DOMAIN:-}"
WS_PATH="${WS_PATH:-/zeta-ws}"

msg "Installing nginx reverse proxy"
apt_install nginx
rm -f /etc/nginx/sites-enabled/default 2>/dev/null || true
mkdir -p /etc/nginx/conf.d

# Compress the panel's text assets (JS/CSS/JSON/HTML) — meaningfully smaller
# transfers on mobile/slow links, negligible CPU cost on a small VPS since
# these are static files served rarely relative to proxy traffic (which
# isn't touched — TLS proxy bytes aren't compressible anyway).
# "gzip on;" itself is edited in-place in nginx.conf (uncommenting it if
# needed) rather than repeated in conf.d/: recent Debian/Ubuntu ship it
# active by default, and a second "gzip on;" in a separate file makes nginx
# refuse to start ("directive is duplicate"); older images that ship it
# commented-out need this sed to actually turn compression on at all.
if grep -qE '^\s*#\s*gzip\s+on;' /etc/nginx/nginx.conf; then
  sed -i -E 's/^(\s*)#\s*(gzip\s+on;)/\1\2/' /etc/nginx/nginx.conf
elif ! grep -qE '^\s*gzip\s+on;' /etc/nginx/nginx.conf; then
  sed -i '/http {/a\    gzip on;' /etc/nginx/nginx.conf
fi
cat > /etc/nginx/conf.d/zeta-gzip.conf <<'CONF'
gzip_comp_level 5;
gzip_min_length 512;
gzip_types text/css application/javascript text/javascript application/json text/plain image/svg+xml;
CONF

# WebSocket / SSH-over-WS capacity. EVERY WS-family inbound and the SSH-over-WS
# front are proxied through nginx, and each live tunnel holds 2 slots (the client
# connection + the 127.0.0.1 upstream). Debian's stock `worker_connections 768`
# caps that at ~384 concurrent tunnels, after which nginx REFUSES new/reconnecting
# clients ("worker_connections are not enough") — a silent connect-failure on a
# busy free-net panel. Raise the ceiling far above anything a small VPS will push
# and lift the matching fd limits (a connection slot needs a file descriptor, and
# systemd caps a unit's fds independently of nginx's own directive).
if grep -qE '^\s*worker_connections\s' /etc/nginx/nginx.conf; then
  sed -i -E 's/^(\s*)worker_connections\s+[0-9]+;/\1worker_connections 16384;/' /etc/nginx/nginx.conf
else
  sed -i -E '/events[[:space:]]*\{/a\    worker_connections 16384;' /etc/nginx/nginx.conf
fi
grep -qE '^\s*worker_rlimit_nofile\s' /etc/nginx/nginx.conf \
  || sed -i '1i worker_rlimit_nofile 65535;' /etc/nginx/nginx.conf
mkdir -p /etc/systemd/system/nginx.service.d
cat > /etc/systemd/system/nginx.service.d/20-zeta-nofile.conf <<'UNIT'
[Service]
LimitNOFILE=65535
UNIT
systemctl daemon-reload 2>/dev/null || true

# The panel (core/nginx.py) regenerates this with one `location <path> { ... }`
# block per WS-family inbound, so every such inbound shares :80 with the
# panel/WS-proxy instead of trying to bind it directly (which would just
# collide with nginx). Must exist before nginx's first start or the `include`
# below fails the whole config; the panel owns its contents from here on.
#
# Deliberately NOT under /etc/nginx/conf.d/: nginx.conf's own default
# `include conf.d/*.conf;` (http-context) would ALSO pick up any *.conf file
# placed there and parse it at the http{} level — invalid for a file of bare
# `location {}` blocks, which are only legal inside a `server {}`. Living
# outside conf.d/ means the only place this gets included is the explicit
# `include` below, in the right context.
ZETA_INBOUNDS_INCLUDE="/etc/nginx/zeta-inbounds.conf"
rm -f /etc/nginx/conf.d/zeta-inbounds.conf  # migrate away from the old (broken) location
[ -f "$ZETA_INBOUNDS_INCLUDE" ] || cat > "$ZETA_INBOUNDS_INCLUDE" <<'CONF'
# Managed by ZetaVPN — regenerated whenever a WS-family inbound changes.
CONF
# The panel (running as 'zetavpn', not root) writes this file directly on
# every inbound change and then asks nginx to reload via sudo — it needs
# write access to exactly this one file, nothing else under /etc/nginx/.
if id -u zetavpn >/dev/null 2>&1; then
  chown zetavpn:zetavpn "$ZETA_INBOUNDS_INCLUDE"
fi

# Explicit SSH-over-WebSocket path (the recommended, unambiguous route). Also
# feeds the ws-proxy a real client IP (X-Real-IP/X-Forwarded-For) so its
# per-IP connection cap keys on the actual user, not the 127.0.0.1 nginx hop.
WS_LOCATION=$(cat <<CONF
    location ${WS_PATH} {
        proxy_pass http://127.0.0.1:${WS_PORT};
        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_read_timeout 3600s;
        proxy_send_timeout 3600s;
        # Stream the tunnel like a direct bind (no nginx buffering): buffering
        # adds latency + a periodic "speed drops then recovers" sawtooth on WS
        # proxy/tunnel traffic, and stalls the SSH-over-WS banner after the 101.
        proxy_buffering off;
        proxy_request_buffering off;
        tcp_nodelay on;
    }
CONF
)

PANEL_LOCATION=$(cat <<CONF
    location / {
        proxy_pass http://127.0.0.1:${PANEL_PORT};
        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }
CONF
)

# http-context map: on the plain-:80 catch-all, a request carrying a WebSocket
# Upgrade header goes to the SSH-over-WS proxy (so bug-host / DarkTunnel clients
# tunnel SSH on ANY path — or no path — with no TLS); everything else is the panel.
cat > /etc/nginx/conf.d/zeta-upstream.conf <<CONF
map \$http_upgrade \$zeta_root_upstream {
    default  http://127.0.0.1:${WS_PORT};
    ""       http://127.0.0.1:${PANEL_PORT};
}
CONF

# Upgrade-aware catch-all for the plain-HTTP :80 server: WebSocket -> SSH-WS
# proxy (path-free), everything else -> panel.
ROOT_LOCATION_HTTP=$(cat <<CONF
    location / {
        proxy_pass \$zeta_root_upstream;
        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_read_timeout 3600s;
        proxy_send_timeout 3600s;
        proxy_buffering off;
        proxy_request_buffering off;
        tcp_nodelay on;
    }
CONF
)

# Same idea for the :80 server in TLS mode, but a normal (non-WebSocket) request
# is redirected to HTTPS instead of served over plain HTTP. A WebSocket handshake
# on any path (bug-host GET /) is piped to the SSH-WS proxy — this is what makes
# SSH-over-WS work on :80 even with a domain + cert (the old catch-all 301'd it).
ROOT_LOCATION_TLS80=$(cat <<CONF
    location / {
        if (\$http_upgrade = "") { return 301 https://\$host\$request_uri; }
        proxy_pass http://127.0.0.1:${WS_PORT};
        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_read_timeout 3600s;
        proxy_send_timeout 3600s;
        # Stream the tunnel like a direct bind (no nginx buffering): buffering
        # adds latency + a periodic "speed drops then recovers" sawtooth on WS
        # proxy/tunnel traffic, and stalls the SSH-over-WS banner after the 101.
        proxy_buffering off;
        proxy_request_buffering off;
        tcp_nodelay on;
    }
CONF
)

if [ -n "$DOMAIN" ] && [ -f "${ZETA_CERT_DIR}/fullchain.pem" ]; then
  msg "Configuring HTTPS vhost for ${DOMAIN}"
  cat > /etc/nginx/conf.d/zeta.conf <<CONF
server {
    listen 80;
    server_name ${DOMAIN};
    # WS-family inbounds, the SSH-WS path, and any bug-host WebSocket handshake
    # stay reachable on plain :80 even in TLS mode (most-specific location wins);
    # a normal browser request (no Upgrade header) is redirected to HTTPS.
    include ${ZETA_INBOUNDS_INCLUDE};
${WS_LOCATION}
${ROOT_LOCATION_TLS80}
}
server {
    listen 443 ssl http2;
    server_name ${DOMAIN};
    ssl_certificate     ${ZETA_CERT_DIR}/fullchain.pem;
    ssl_certificate_key ${ZETA_CERT_DIR}/privkey.pem;
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers HIGH:!aNULL:!MD5;
    # Parity with :80 — VLESS-WS paths and /zeta-ws also work TLS-terminated here.
    include ${ZETA_INBOUNDS_INCLUDE};
${WS_LOCATION}
${PANEL_LOCATION}
}
CONF
else
  warn "No domain/cert — serving panel + WS over plain HTTP (port 80)."
  cat > /etc/nginx/conf.d/zeta.conf <<CONF
server {
    listen 80 default_server;
    server_name _;
    include ${ZETA_INBOUNDS_INCLUDE};
${WS_LOCATION}
${ROOT_LOCATION_HTTP}
}
CONF
fi

# Don't chain `nginx -t && ... && restart` — under `set -e` a failing test is a
# non-final && element and is exempt from errexit, so a broken config would
# print success while nginx never (re)starts and the panel is unreachable.
if nginx -t; then
  systemctl enable nginx >/dev/null 2>&1 || true
  if systemctl restart nginx; then
    ok "nginx configured (WS path: ${WS_PATH})"
  else
    warn "nginx restart failed — panel may be unreachable"
  fi
else
  warn "nginx config test failed — NOT restarting nginx; panel may be unreachable"
fi
