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

# fail2ban jail for sshd — tuned for a TUNNELLING box, not a plain server.
# ZetaVPN intentionally keeps password SSH open on :22 so tunnel clients (HTTP
# Injector/Custom, etc.) can use it — and those clients routinely send non-SSH
# payloads to :22 that sshd logs as "banner exchange: invalid format". With the
# default aggressive matching, that handshake NOISE counts as auth failures and
# fail2ban bans the user's real IP after a few connections — so they suddenly
# can't reach the VPS from normal WiFi and can only get in THROUGH the VPN
# (whose exit is the server's own IP, which isn't banned). `mode = normal`
# counts ONLY genuine credential brute-force (Failed password / invalid user),
# not that tunnel handshake noise. Loopback is ALWAYS ignored: SSH-over-WS
# reaches sshd via the ws-proxy from 127.0.0.1, so banning it would lock out
# every WS-SSH user at once. Set ZETA_FAIL2BAN_IGNOREIP (space-separated
# IPs/CIDRs) to also permanently whitelist your own home/office address.
# Policy is a light rate-limit, not a lockout: 5 wrong passwords -> that IP is
# banned for just 5 minutes, then it clears itself, so a fumbling user (or the
# admin) is never stuck out — while random brute-force bots still get throttled.
if [ -d /etc/fail2ban ]; then
  cat > /etc/fail2ban/jail.d/zeta-sshd.conf <<CONF
[sshd]
enabled  = true
mode     = normal
maxretry = 5
findtime = 600
bantime  = 300
ignoreip = 127.0.0.1/8 ::1 ${ZETA_FAIL2BAN_IGNOREIP:-}
CONF
  systemctl enable fail2ban >/dev/null 2>&1 || true
  systemctl restart fail2ban 2>/dev/null || true
  # Clear any stale bans so (re-)running the installer restores access for an
  # admin who locked their own IP out with a tunnel app before this tuning.
  fail2ban-client unban --all >/dev/null 2>&1 || true
  ok "fail2ban protecting sshd (tunnel-friendly: real brute-force only)"
fi
