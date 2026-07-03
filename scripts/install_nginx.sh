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
${WS_LOCATION}
${PANEL_LOCATION}
}
CONF
fi

nginx -t && systemctl enable nginx >/dev/null 2>&1 && systemctl restart nginx
ok "nginx configured (WS path: ${WS_PATH})"
