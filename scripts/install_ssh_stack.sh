#!/usr/bin/env bash
# Install the SSH tunnelling stack: OpenSSH tuning, Dropbear, stunnel (SSH-over-SSL)
# and the SSH-over-WebSocket proxy service.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
. "${HERE}/common.sh"
need_root

# Default ports (override via env before running).
DROPBEAR_PORT_MAIN="${DROPBEAR_PORT_MAIN:-149}"
DROPBEAR_PORT_ALT="${DROPBEAR_PORT_ALT:-143}"
STUNNEL_PORT="${STUNNEL_PORT:-445}"
WS_PORT="${WS_PORT:-8880}"

msg "Installing SSH tunnelling stack"
apt_install openssh-server dropbear stunnel4 net-tools

# ---- OpenSSH: allow tunnelling, permit the /bin/false shell for tunnel-only users
grep -qxF '/bin/false' /etc/shells 2>/dev/null || echo '/bin/false' >> /etc/shells
grep -qxF '/usr/sbin/nologin' /etc/shells 2>/dev/null || echo '/usr/sbin/nologin' >> /etc/shells
mkdir -p /etc/ssh/sshd_config.d
cat > /etc/ssh/sshd_config.d/zeta.conf <<'CONF'
# Managed by ZetaVPN — SSH tunnelling
AllowTcpForwarding yes
GatewayPorts yes
PermitTunnel yes
ClientAliveInterval 60
ClientAliveCountMax 3
CONF
systemctl restart ssh 2>/dev/null || systemctl restart sshd 2>/dev/null || true
ok "OpenSSH configured for tunnelling"

# ---- Dropbear (lightweight SSH on extra ports) ----
if [ -f /etc/default/dropbear ]; then
  sed -i "s/^NO_START=.*/NO_START=0/" /etc/default/dropbear
  sed -i "s/^DROPBEAR_PORT=.*/DROPBEAR_PORT=${DROPBEAR_PORT_MAIN}/" /etc/default/dropbear
  if grep -q '^DROPBEAR_EXTRA_ARGS=' /etc/default/dropbear; then
    sed -i "s|^DROPBEAR_EXTRA_ARGS=.*|DROPBEAR_EXTRA_ARGS=\"-p ${DROPBEAR_PORT_ALT}\"|" /etc/default/dropbear
  else
    echo "DROPBEAR_EXTRA_ARGS=\"-p ${DROPBEAR_PORT_ALT}\"" >> /etc/default/dropbear
  fi
fi
systemctl enable dropbear >/dev/null 2>&1 || true
systemctl restart dropbear || warn "dropbear failed to start"
ok "Dropbear listening on ${DROPBEAR_PORT_MAIN}, ${DROPBEAR_PORT_ALT}"

# ---- stunnel (SSH-over-SSL/TLS) ----
if [ ! -f /etc/stunnel/stunnel.pem ]; then
  openssl req -new -x509 -days 3650 -nodes \
    -subj "/C=US/ST=Zeta/L=Zeta/O=ZetaVPN/CN=$(hostname)" \
    -out /etc/stunnel/stunnel.pem -keyout /etc/stunnel/stunnel.pem >/dev/null 2>&1
  chmod 600 /etc/stunnel/stunnel.pem
fi
cat > /etc/stunnel/stunnel.conf <<CONF
; Managed by ZetaVPN — SSH over SSL/TLS
pid = /var/run/stunnel4.pid
cert = /etc/stunnel/stunnel.pem
client = no
socket = a:SO_REUSEADDR=1
socket = l:TCP_NODELAY=1
socket = r:TCP_NODELAY=1

[ssh-ssl]
accept = ${STUNNEL_PORT}
connect = 127.0.0.1:${DROPBEAR_PORT_ALT}
CONF
[ -f /etc/default/stunnel4 ] && sed -i 's/^ENABLED=.*/ENABLED=1/' /etc/default/stunnel4
systemctl enable stunnel4 >/dev/null 2>&1 || true
systemctl restart stunnel4 || warn "stunnel4 failed to start"
ok "stunnel (SSH-over-SSL) listening on ${STUNNEL_PORT}"

msg "WebSocket SSH proxy will listen on ${WS_PORT} (unit: zeta-ws)"
ok "SSH stack ready"
