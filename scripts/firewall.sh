#!/usr/bin/env bash
# Open the ports ZetaVPN uses. Uses ufw when available; the ruleset is
# intentionally permissive on proxy ports because inbounds can be created on
# arbitrary ports from the panel. NO fail2ban: ZetaVPN targets the gaming/
# free-net community, where users fumble credentials constantly and must never
# be locked out — SSH stays reachable from anywhere, like a stock VPS.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
. "${HERE}/common.sh"
need_root

# Same env vars + defaults as install_ssh_stack.sh / install_nginx.sh — a
# custom port there must open the matching port here, not a hardcoded one.
DROPBEAR_PORT_MAIN="${DROPBEAR_PORT_MAIN:-109}"
DROPBEAR_PORT_ALT="${DROPBEAR_PORT_ALT:-143}"
STUNNEL_PORT="${STUNNEL_PORT:-445}"
WS_PORT="${WS_PORT:-8880}"

msg "Configuring firewall (stock-friendly SSH — no fail2ban banning)"

if command -v ufw >/dev/null 2>&1 || apt_install ufw; then
  # Only wipe the ruleset on the FIRST run. A re-run (install.sh is a supported
  # re-entry point) must NOT reset, or it deletes the custom-port allows the
  # panel added for inbounds at CRUD time — silently making those inbounds
  # unreachable. `ufw default`/`allow` below are already idempotent no-ops.
  _ufw_marker=/var/lib/zetavpn/.ufw-initialized
  if [ ! -f "$_ufw_marker" ]; then
    ufw --force reset >/dev/null 2>&1 || true
    mkdir -p "$(dirname "$_ufw_marker")" && : > "$_ufw_marker"
  fi
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

# No fail2ban. If a previous ZetaVPN install (or the OS image) left a jail that
# could lock a tunnel user out of their own VPS, remove OUR jail and clear any
# stale bans so SSH behaves like a stock box — reachable from anywhere. We don't
# uninstall the fail2ban package (something else may use it); we just stop it
# from banning ZetaVPN's SSH users.
rm -f /etc/fail2ban/jail.d/zeta-sshd.conf
if command -v fail2ban-client >/dev/null 2>&1; then
  fail2ban-client unban --all >/dev/null 2>&1 || true
  systemctl reload fail2ban >/dev/null 2>&1 || systemctl restart fail2ban >/dev/null 2>&1 || true
fi
ok "SSH reachable from anywhere (no fail2ban lockouts)"
