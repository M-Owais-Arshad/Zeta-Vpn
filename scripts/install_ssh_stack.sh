#!/usr/bin/env bash
# Install the SSH tunnelling stack: OpenSSH tuning, Dropbear, stunnel (SSH-over-SSL)
# and the SSH-over-WebSocket proxy service.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
. "${HERE}/common.sh"
need_root

# Default ports (override via env before running).
DROPBEAR_PORT_MAIN="${DROPBEAR_PORT_MAIN:-109}"
DROPBEAR_PORT_ALT="${DROPBEAR_PORT_ALT:-143}"
STUNNEL_PORT="${STUNNEL_PORT:-445}"
WS_PORT="${WS_PORT:-8880}"

# Custom pre-auth SSH banner ("message from server" the client sees on connect).
# The panel writes this file (Settings -> SSH banner); sshd/dropbear re-read it
# per connection, so the message updates with no reload. data_dir is
# panel-writable (zetavpn) yet readable by root-run sshd. Must match
# config.py::ssh_banner_file.
SSH_BANNER_FILE="${ZETA_HOME:-/opt/zetavpn}/data/ssh-banner.txt"
mkdir -p "$(dirname "$SSH_BANNER_FILE")"
[ -f "$SSH_BANNER_FILE" ] || : > "$SSH_BANNER_FILE"
chown zetavpn:zetavpn "$SSH_BANNER_FILE" 2>/dev/null || true
chmod 644 "$SSH_BANNER_FILE"

msg "Installing SSH tunnelling stack"
apt_install openssh-server dropbear stunnel4 net-tools

# ---- OpenSSH: allow tunnelling, permit the /bin/false shell for tunnel-only users
grep -qxF '/bin/false' /etc/shells 2>/dev/null || echo '/bin/false' >> /etc/shells
grep -qxF '/usr/sbin/nologin' /etc/shells 2>/dev/null || echo '/usr/sbin/nologin' >> /etc/shells

# ---- ZetaVPN tunnel login shell (post-auth per-user banner) ----
# Replaces /bin/false for tunnel accounts: on connect it prints the account's own
# status file (data used / cap / remaining, expiry, days left) then holds the
# session so port-forwarding stays up. It NEVER runs a command. Port-forwarding
# (`ssh -N`) doesn't invoke the login shell at all, so this can't break tunnelling.
BANNER_SHELL=/usr/local/sbin/zeta-tunnel-shell
# Banner files MUST live OUTSIDE ZETA_HOME: install.sh does `chmod 0750 $ZETA_HOME`,
# so an unprivileged tunnel user can't even traverse into it to reach its file.
# /var/lib is world-traversable; the dir is zetavpn-owned 0755 (panel writes) with
# 0644 files (tunnel user reads). Keep in sync with config.ssh_info_dir + the shell.
SSH_INFO_DIR=/var/lib/zeta-ssh-info
if [ -f "${HERE}/zeta-tunnel-shell" ]; then
  install -m 0755 "${HERE}/zeta-tunnel-shell" "$BANNER_SHELL"
  grep -qxF "$BANNER_SHELL" /etc/shells 2>/dev/null || echo "$BANNER_SHELL" >> /etc/shells
  mkdir -p "$SSH_INFO_DIR"
  chown zetavpn:zetavpn "$SSH_INFO_DIR" 2>/dev/null || true
  chmod 755 "$SSH_INFO_DIR"
  # Migrate existing Zeta SSH accounts (read from the panel DB) onto the banner
  # shell. Root context here, so plain usermod works and targets exactly the
  # accounts the panel manages. Best-effort — never abort the install.
  VENV_PY="${ZETA_HOME:-/opt/zetavpn}/venv/bin/python"
  if [ -x "$VENV_PY" ]; then
    accounts=$(ZETA_HOME="${ZETA_HOME:-/opt/zetavpn}" "$VENV_PY" - 2>/dev/null <<'PY' || true
import os, sys
sys.path.insert(0, os.path.join(os.environ["ZETA_HOME"], "backend"))
try:
    from zeta.db import SessionLocal
    from zeta.models import SSHAccount
    print("\n".join(n for (n,) in SessionLocal().query(SSHAccount.username).all()))
except Exception:
    pass
PY
)
    for u in $accounts; do
      usermod -s "$BANNER_SHELL" "$u" 2>/dev/null || true
    done
  fi
  ok "ZetaVPN tunnel banner shell installed"
fi

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
# Survive mobile reconnect storms: the stock 10:30:100 makes sshd start randomly
# refusing NEW unauthenticated handshakes at just 10 in-flight — the usual
# "server down" report on a busy free-net panel. Raise the unauthenticated
# backlog; MaxSessions bounds channels per connection.
MaxStartups 100:30:200
MaxSessions 20
CONF
# Pre-auth banner (panel-managed; appended after the quoted heredoc so the path
# expands). sshd reads the file per connection, so banner edits need no reload.
echo "Banner ${SSH_BANNER_FILE}" >> /etc/ssh/sshd_config.d/00-zeta.conf
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
# that uncomments-or-appends each key so the main port (109) actually comes up
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
  # 256KB (not the 64KB default): a Dropbear tunnel is bounded by receive_window
  # / RTT, so 64KB caps a single high-RTT intl stream to ~3.5 Mbps well under the
  # link + CPU headroom. 256KB lifts that ~4x per stream; cost is modest per-channel
  # RAM, no stability downside.
  set_kv DROPBEAR_RECEIVE_WINDOW 262144 /etc/default/dropbear
  # -K 60 = server keepalive every 60s (both ports; EXTRA_ARGS applies to the
  # whole daemon). Mirrors OpenSSH's ClientAliveInterval 60 so an idle tunnel on
  # a CGNAT/mobile NAT keeps its mapping alive instead of being silently dropped.
  if grep -q '^DROPBEAR_EXTRA_ARGS=' /etc/default/dropbear; then
    sed -i "s|^DROPBEAR_EXTRA_ARGS=.*|DROPBEAR_EXTRA_ARGS=\"-p ${DROPBEAR_PORT_ALT} -K 60 -b ${SSH_BANNER_FILE}\"|" /etc/default/dropbear
  else
    echo "DROPBEAR_EXTRA_ARGS=\"-p ${DROPBEAR_PORT_ALT} -K 60 -b ${SSH_BANNER_FILE}\"" >> /etc/default/dropbear
  fi
fi
systemctl enable dropbear >/dev/null 2>&1 || true
# The dropbear package's postinst starts the daemon on its default port 22,
# which collides with OpenSSH and fails immediately; a few of those failures
# trip systemd's start-rate-limit ("start request repeated too quickly") so our
# restart below is REFUSED and dropbear ends up failed even though the config is
# correct. Clear that accumulated failure state first (and retry once) so it
# reliably comes up on the configured ports.
systemctl reset-failed dropbear >/dev/null 2>&1 || true
systemctl restart dropbear \
  || { sleep 2; systemctl reset-failed dropbear >/dev/null 2>&1; systemctl restart dropbear; } \
  || warn "dropbear failed to start"
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
socket = a:SO_KEEPALIVE=1
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

# ---- badvpn-udpgw: UDP over the SSH tunnel (QUIC/UDP browsing + gaming) ----
# SSH carries only TCP, so WITHOUT this a client's UDP — including Chrome's QUIC
# to Google, so searches/new sites hang while direct TCP loads — is blackholed.
# badvpn-udpgw relays UDP through the tunnel and fixes that. PREFER the distro
# package (fast, reliable, no build toolchain — badvpn IS in Ubuntu/Debian
# 'universe'); only fall back to a pinned source build if apt doesn't have it.
# Best-effort either way: SSH still works if it's skipped. Binds loopback only
# (reachable ONLY through an established SSH tunnel), so no public attack surface.
if ! [ -x /usr/local/bin/badvpn-udpgw ]; then
  msg "Installing badvpn-udpgw (UDP-over-SSH — fixes QUIC/UDP + gaming)"
  if apt_install badvpn >/dev/null 2>&1 && [ -x /usr/bin/badvpn-udpgw ]; then
    # apt installs it at /usr/bin; point the service's fixed /usr/local/bin path
    # at it so no build toolchain is ever needed on a normal Ubuntu/Debian box.
    ln -sf /usr/bin/badvpn-udpgw /usr/local/bin/badvpn-udpgw
    ok "badvpn-udpgw installed (apt)"
  else
    msg "apt badvpn unavailable — building from source (optional)"
    _had_cmake=1; command -v cmake >/dev/null 2>&1 || _had_cmake=0
    # build-essential (not just gcc) — a minimal box has gcc but NOT libc6-dev, so
    # the linker can't find Scrt1.o/crti.o and even cmake's compiler test fails.
    if apt_install cmake build-essential git >/dev/null 2>&1; then
      _bv=$(mktemp -d)
      # Pin a known-good commit and verify it — mirroring the checksum-or-refuse
      # policy used for mtg/xray/sing-box — so a moved or compromised upstream
      # HEAD can't be built and run as root. Full clone (not --depth 1) so the
      # pinned SHA stays fetchable even once upstream HEAD advances past it.
      BADVPN_COMMIT="07268f02706e78e282e19641b5d1d41e8e89bf31"
      # badvpn's CMakeLists targets cmake 2.6, which cmake >=3.28 (Ubuntu 24.04)
      # hard-rejects — so sed the minimum up to 3.5 before configuring. -fcommon
      # lets gcc >=10 (default -fno-common) link badvpn's tentative definitions.
      if git clone https://github.com/ambrop72/badvpn.git "$_bv/badvpn" >/dev/null 2>&1 \
         && ( cd "$_bv/badvpn" && git checkout -q "$BADVPN_COMMIT" \
              && [ "$(git rev-parse HEAD)" = "$BADVPN_COMMIT" ] ) \
         && ( cd "$_bv/badvpn" \
              && sed -i -E 's/cmake_minimum_required\s*\(\s*VERSION\s+[0-9.]+/cmake_minimum_required(VERSION 3.5/I' CMakeLists.txt \
              && mkdir -p build && cd build \
              && cmake .. -DBUILD_NOTHING_BY_DEFAULT=1 -DBUILD_UDPGW=1 -DCMAKE_C_FLAGS=-fcommon >/dev/null 2>&1 \
              && make >/dev/null 2>&1 ) \
         && [ -x "$_bv/badvpn/build/udpgw/badvpn-udpgw" ]; then
        install -m 0755 "$_bv/badvpn/build/udpgw/badvpn-udpgw" /usr/local/bin/badvpn-udpgw
        ok "badvpn-udpgw installed (source)"
      else
        warn "badvpn-udpgw build failed — SSH works, just no UDP/QUIC gateway"
      fi
      rm -rf "$_bv"
      # Only purge cmake if WE installed it — never `apt-get autoremove` (would
      # sweep unrelated orphans on a shared box). Purging cmake alone is safe.
      [ "$_had_cmake" = 0 ] && { apt-get purge -y cmake >/dev/null 2>&1 || true; }
    else
      warn "badvpn build deps unavailable — skipping UDPGW (SSH still works)"
    fi
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
# One UDP socket per gaming flow; the stock 1024 soft-fd default would silently
# drop new flows on a busy box. Match the other data-plane units.
LimitNOFILE=1000000

[Install]
WantedBy=multi-user.target
UNIT
  systemctl daemon-reload 2>/dev/null || true
  systemctl enable --now zeta-badvpn >/dev/null 2>&1 || warn "zeta-badvpn failed to start"
  ok "badvpn UDPGW on 127.0.0.1:7300 (unit: zeta-badvpn)"
fi

ok "SSH stack ready"
