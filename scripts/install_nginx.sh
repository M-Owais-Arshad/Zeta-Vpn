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

WS_LOCATION=$(cat <<CONF
    location ${WS_PATH} {
        proxy_pass http://127.0.0.1:${WS_PORT};
        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host \$host;
        proxy_read_timeout 3600s;
        proxy_send_timeout 3600s;
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

if [ -n "$DOMAIN" ] && [ -f "${ZETA_CERT_DIR}/fullchain.pem" ]; then
  msg "Configuring HTTPS vhost for ${DOMAIN}"
  cat > /etc/nginx/conf.d/zeta.conf <<CONF
server {
    listen 80;
    server_name ${DOMAIN};
    # WS-family inbounds stay reachable on plain :80 even in domain/TLS mode
    # (nginx picks the most specific matching location, so these paths win
    # over the catch-all redirect below regardless of file order); anything
    # else gets redirected to https as usual.
    include ${ZETA_INBOUNDS_INCLUDE};
    location / { return 301 https://\$host\$request_uri; }
}
server {
    listen 443 ssl http2;
    server_name ${DOMAIN};
    ssl_certificate     ${ZETA_CERT_DIR}/fullchain.pem;
    ssl_certificate_key ${ZETA_CERT_DIR}/privkey.pem;
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers HIGH:!aNULL:!MD5;
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
${PANEL_LOCATION}
}
CONF
fi

nginx -t && systemctl enable nginx >/dev/null 2>&1 && systemctl restart nginx
ok "nginx configured (WS path: ${WS_PATH})"
