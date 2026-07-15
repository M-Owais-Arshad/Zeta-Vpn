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
# Filename sorts BEFORE cloud-image drop-ins (e.g. 60-cloudimg-settings.conf).
# sshd uses the FIRST obtained value per keyword, so `PasswordAuthentication yes`
# here overrides the cloud image's default of `no` — essential, because SSH
# tunnelling accounts authenticate with username+password, not keys. Without
# this every generated SSH account fails on OpenSSH with "Permission denied
# (publickey)". Drop any older-named copy so it can't shadow this one.
rm -f /etc/ssh/sshd_config.d/zeta.conf
cat > /etc/ssh/sshd_config.d/00-zeta.conf <<'CONF'
# Managed by ZetaVPN — SSH tunnelling (loads before cloud-image defaults)
PasswordAuthentication yes
KbdInteractiveAuthentication yes
AllowTcpForwarding yes
GatewayPorts yes
PermitTunnel yes
ClientAliveInterval 60
ClientAliveCountMax 3
CONF
# Validate before reloading so a bad drop-in can't lock out SSH.
if sshd -t 2>/dev/null; then
  systemctl reload ssh 2>/dev/null || systemctl restart ssh 2>/dev/null || systemctl restart sshd 2>/dev/null || true
else
  warn "sshd config test failed — leaving SSH untouched"
fi
ok "OpenSSH configured for tunnelling (password auth enabled)"

# ---- Dropbear (lightweight SSH on extra ports) ----
# The systemd unit runs `dropbear -p "$DROPBEAR_PORT" -W "$DROPBEAR_RECEIVE_WINDOW"
# $DROPBEAR_EXTRA_ARGS`. Ubuntu ships /etc/default/dropbear with those first two
# COMMENTED OUT (`#DROPBEAR_PORT=22`), so a naive `s/^DROPBEAR_PORT=.*/…/` matches
# nothing — the main port never binds and `-p ""`/`-W ""` go empty. Set a helper
# that uncomments-or-appends each key so the main port (149) actually comes up
# alongside the alt port (143).
if [ -f /etc/default/dropbear ]; then
  set_kv() { # key value file
    if grep -qE "^#?${1}=" "$3"; then
      sed -i "s|^#\?${1}=.*|${1}=${2}|" "$3"
    else
      echo "${1}=${2}" >> "$3"
    fi
  }
  set_kv NO_START 0 /etc/default/dropbear
  set_kv DROPBEAR_PORT "${DROPBEAR_PORT_MAIN}" /etc/default/dropbear
  set_kv DROPBEAR_RECEIVE_WINDOW 65536 /etc/default/dropbear
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

# ---- badvpn-udpgw: UDP over the SSH tunnel (gaming/voice) ----
# Not packaged in apt, so built from source. Best-effort: if the build tools or
# the build itself fail, the SSH stack still works — UDPGW is simply skipped and
# the install never breaks over it. Binds loopback only (reachable ONLY through
# an established SSH tunnel), so it adds no public attack surface.
if ! [ -x /usr/local/bin/badvpn-udpgw ]; then
  msg "Building badvpn-udpgw (UDP-over-SSH for gaming) — optional"
  _had_cmake=1; command -v cmake >/dev/null 2>&1 || _had_cmake=0
  if apt_install cmake gcc make git >/dev/null 2>&1; then
    _bv=$(mktemp -d)
    if git clone --depth 1 https://github.com/ambrop72/badvpn.git "$_bv/badvpn" >/dev/null 2>&1 \
       && ( cd "$_bv/badvpn" && mkdir -p build && cd build \
            && cmake .. -DBUILD_NOTHING_BY_DEFAULT=1 -DBUILD_UDPGW=1 -DCMAKE_POLICY_VERSION_MINIMUM=3.5 >/dev/null 2>&1 \
            && make >/dev/null 2>&1 ) \
       && [ -x "$_bv/badvpn/build/udpgw/badvpn-udpgw" ]; then
      install -m 0755 "$_bv/badvpn/build/udpgw/badvpn-udpgw" /usr/local/bin/badvpn-udpgw
      ok "badvpn-udpgw installed"
    else
      warn "badvpn-udpgw build failed — SSH works, just no UDP/gaming gateway"
    fi
    rm -rf "$_bv"
    [ "$_had_cmake" = 0 ] && { apt-get purge -y cmake >/dev/null 2>&1 || true; apt-get autoremove -y >/dev/null 2>&1 || true; }
  else
    warn "badvpn build deps unavailable — skipping UDPGW (SSH still works)"
  fi
fi
if [ -x /usr/local/bin/badvpn-udpgw ]; then
  cat > /etc/systemd/system/zeta-badvpn.service <<'UNIT'
[Unit]
Description=ZetaVPN BadVPN UDPGW (UDP over the SSH tunnel — gaming/voice)
After=network.target

[Service]
ExecStart=/usr/local/bin/badvpn-udpgw --loglevel 0 --listen-addr 127.0.0.1:7300 --max-clients 500 --max-connections-for-client 50
Restart=always
RestartSec=3
MemoryHigh=64M

[Install]
WantedBy=multi-user.target
UNIT
  systemctl daemon-reload 2>/dev/null || true
  systemctl enable --now zeta-badvpn >/dev/null 2>&1 || warn "zeta-badvpn failed to start"
  ok "badvpn UDPGW on 127.0.0.1:7300 (unit: zeta-badvpn)"
fi

ok "SSH stack ready"
