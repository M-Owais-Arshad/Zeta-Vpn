#!/usr/bin/env bash
# Issue a TLS certificate for a domain via acme.sh (Let's Encrypt / ZeroSSL) and
# install it into ZetaVPN's cert directory. Used by TLS protocols and nginx.
#
#   ZETA_DOMAIN=vpn.example.com ./install_ssl.sh
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
. "${HERE}/common.sh"
need_root

DOMAIN="${ZETA_DOMAIN:-}"
EMAIL="${ZETA_SSL_EMAIL:-admin@${DOMAIN:-example.com}}"
[ -n "$DOMAIN" ] || die "Set ZETA_DOMAIN=your.domain.com before running."

msg "Issuing certificate for ${DOMAIN}"
apt_install socat cron

ACME_HOME="/root/.acme.sh"
if [ ! -x "${ACME_HOME}/acme.sh" ]; then
  curl -fsSL https://get.acme.sh | sh -s email="$EMAIL"
fi
ACME="${ACME_HOME}/acme.sh"

"$ACME" --set-default-ca --server letsencrypt >/dev/null 2>&1 || true

# Free port 80 for standalone validation, then restore nginx afterwards.
NGINX_WAS_ACTIVE=0
if systemctl is-active --quiet nginx; then NGINX_WAS_ACTIVE=1; systemctl stop nginx; fi

if "$ACME" --issue -d "$DOMAIN" --standalone --keylength ec-256 --force; then
  mkdir -p "$ZETA_CERT_DIR"
  # zeta-xray/zeta-singbox run as the unprivileged 'zetavpn' user and need to
  # read these files. acme.sh's cron renewal re-runs as root and would reset
  # ownership back to root:root, silently breaking TLS on the next renewal —
  # the reloadcmd re-chowns on every run (initial issue and every renewal).
  "$ACME" --install-cert -d "$DOMAIN" --ecc \
    --fullchain-file "${ZETA_CERT_DIR}/fullchain.pem" \
    --key-file "${ZETA_CERT_DIR}/privkey.pem" \
    --reloadcmd "chown zetavpn:zetavpn '${ZETA_CERT_DIR}'/*.pem 2>/dev/null; chmod 600 '${ZETA_CERT_DIR}'/*.pem 2>/dev/null; systemctl restart zeta-xray zeta-singbox nginx 2>/dev/null || true"
  chown zetavpn:zetavpn "${ZETA_CERT_DIR}"/*.pem 2>/dev/null || true
  chmod 600 "${ZETA_CERT_DIR}/privkey.pem"
  ok "Certificate installed to ${ZETA_CERT_DIR}"
else
  err "Certificate issuance failed — check DNS points to this server and 80/tcp is open."
  [ "$NGINX_WAS_ACTIVE" -eq 1 ] && systemctl start nginx
  exit 1
fi

[ "$NGINX_WAS_ACTIVE" -eq 1 ] && systemctl start nginx
ok "SSL ready for ${DOMAIN}"
