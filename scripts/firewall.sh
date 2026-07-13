#!/usr/bin/env bash
# Open the ports ZetaVPN uses and install fail2ban for SSH brute-force protection.
# Uses ufw when available; the ruleset is intentionally permissive on proxy ports
# because inbounds can be created on arbitrary ports from the panel.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
. "${HERE}/common.sh"
need_root

# Same env vars + defaults as install_ssh_stack.sh / install_nginx.sh — a
# custom port there must open the matching port here, not a hardcoded one.
DROPBEAR_PORT_MAIN="${DROPBEAR_PORT_MAIN:-149}"
DROPBEAR_PORT_ALT="${DROPBEAR_PORT_ALT:-143}"
STUNNEL_PORT="${STUNNEL_PORT:-445}"
WS_PORT="${WS_PORT:-8880}"

msg "Configuring firewall + fail2ban"
apt_install fail2ban || warn "fail2ban install skipped"

if command -v ufw >/dev/null 2>&1 || apt_install ufw; then
  ufw --force reset >/dev/null 2>&1 || true
  ufw default deny incoming >/dev/null
  ufw default allow outgoing >/dev/null
  # Core management / web. The panel itself binds 127.0.0.1 (nginx always
  # fronts it, TLS or plain :80) so PANEL_PORT is deliberately NOT opened
  # publicly here — that would only expose an unencrypted bypass around nginx.
  for p in 22 80 443; do ufw allow "${p}/tcp" >/dev/null; done
  # SSH stack: dropbear (main + alt ports), stunnel (SSH-over-SSL), SSH-over-WS
  for p in "$DROPBEAR_PORT_MAIN" "$DROPBEAR_PORT_ALT" "$STUNNEL_PORT" "$WS_PORT"; do
    ufw allow "${p}/tcp" >/dev/null
  done
  # QUIC-family (Hysteria2 / TUIC) commonly on UDP
  ufw allow 443/udp >/dev/null
  ufw --force enable >/dev/null
  ok "ufw enabled (management + SSH + proxy ports open)"
else
  warn "ufw unavailable — skipping firewall rules"
fi

# Basic fail2ban jail for sshd.
if [ -d /etc/fail2ban ]; then
  cat > /etc/fail2ban/jail.d/zeta-sshd.conf <<'CONF'
[sshd]
enabled = true
maxretry = 5
findtime = 600
bantime = 3600
CONF
  systemctl enable fail2ban >/dev/null 2>&1 || true
  systemctl restart fail2ban 2>/dev/null || true
  ok "fail2ban protecting sshd"
fi
