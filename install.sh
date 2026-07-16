#!/usr/bin/env bash
###############################################################################
#  ZetaVPN — one-command installer
#  ZetaVPN by Muhammad Owais · (c) 2026 · AGPL-3.0
#
#  Fresh Debian/Ubuntu VPS, as root:
#     bash <(curl -fsSL https://raw.githubusercontent.com/<you>/zetavpn/main/install.sh)
#
#  Or from a local checkout:
#     sudo ./install.sh
#
#  Non-interactive example:
#     ZETA_DOMAIN=vpn.example.com ZETA_ADMIN_USERNAME=admin \
#     ZETA_ADMIN_PASSWORD=secret ./install.sh --yes
###############################################################################
set -euo pipefail

# Resolve where this script lives so we can find the rest of the repo.
SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

ZETA_HOME="${ZETA_HOME:-/opt/zetavpn}"
ZETA_REPO="${ZETA_REPO:-https://github.com/M-Owais-Arshad/Zeta-Vpn.git}"
ZETA_BRANCH="${ZETA_BRANCH:-main}"
PANEL_PORT="${PANEL_PORT:-2096}"
WS_PORT="${WS_PORT:-8880}"
DROPBEAR_PORT_MAIN="${DROPBEAR_PORT_MAIN:-149}"
DROPBEAR_PORT_ALT="${DROPBEAR_PORT_ALT:-143}"
STUNNEL_PORT="${STUNNEL_PORT:-445}"
ASSUME_YES=0
if [ "${1:-}" = "--yes" ] || [ "${ZETA_YES:-}" = "1" ]; then ASSUME_YES=1; fi

# Source helpers from wherever they are (local checkout preferred).
if [ -f "${SELF_DIR}/scripts/common.sh" ]; then
  # shellcheck disable=SC1091
  . "${SELF_DIR}/scripts/common.sh"
  SOURCE_MODE="local"
else
  SOURCE_MODE="remote"
fi

# When bootstrapped via curl the helpers aren't local yet; define a minimal set.
if [ "$SOURCE_MODE" = "remote" ]; then
  C_RESET='\033[0m'; C_CYN='\033[36m'; C_GRN='\033[32m'; C_YEL='\033[33m'; C_RED='\033[31m'
  msg()  { printf "${C_CYN}::${C_RESET} %s\n" "$*"; }
  ok()   { printf "${C_GRN} ✓${C_RESET} %s\n" "$*"; }
  warn() { printf "${C_YEL} !${C_RESET} %s\n" "$*"; }
  die()  { printf "${C_RED} ✗${C_RESET} %s\n" "$*" >&2; exit 1; }
  need_root() { [ "$(id -u)" -eq 0 ] || die "Run as root."; }
  detect_os() { . /etc/os-release; OS_ID="$ID"; }
  banner() { printf "\n  Z E T A V P N   installer\n\n"; }
  apt_install() { DEBIAN_FRONTEND=noninteractive apt-get install -y "$@"; }
fi

need_root
banner
detect_os

# --------------------------------------------------------------------------- #
# 1. Base dependencies
# --------------------------------------------------------------------------- #
msg "Updating package index & installing base dependencies"
export DEBIAN_FRONTEND=noninteractive
apt-get update -y >/dev/null 2>&1 || apt-get update -y
apt_install curl wget git unzip jq openssl ca-certificates \
            python3 python3-venv python3-pip vnstat >/dev/null 2>&1 || \
  apt_install curl wget git unzip jq openssl ca-certificates python3 python3-venv python3-pip
ok "Base dependencies installed"

# --------------------------------------------------------------------------- #
# 2. Fetch / copy the source into ZETA_HOME
# --------------------------------------------------------------------------- #
if [ -d "${SELF_DIR}/backend" ] && [ -d "${SELF_DIR}/frontend" ]; then
  if [ "${SELF_DIR}" != "${ZETA_HOME}" ]; then
    msg "Installing source from local checkout to ${ZETA_HOME}"
    mkdir -p "$ZETA_HOME"
    cp -a "${SELF_DIR}/." "${ZETA_HOME}/"
  fi
else
  if [ -d "${ZETA_HOME}/.git" ]; then
    msg "Updating existing ZetaVPN checkout"
    git -C "$ZETA_HOME" fetch --depth 1 origin "$ZETA_BRANCH" && git -C "$ZETA_HOME" reset --hard "origin/${ZETA_BRANCH}"
  else
    msg "Cloning ${ZETA_REPO} (${ZETA_BRANCH})"
    rm -rf "$ZETA_HOME"
    git clone --depth 1 -b "$ZETA_BRANCH" "$ZETA_REPO" "$ZETA_HOME"
  fi
fi
cd "$ZETA_HOME"
chmod +x scripts/*.sh bin/zeta 2>/dev/null || true
# Re-source the full helpers now that they exist.
# shellcheck disable=SC1091
. "${ZETA_HOME}/scripts/common.sh"
ok "Source ready in ${ZETA_HOME}"

# --------------------------------------------------------------------------- #
# 3. Python environment
# --------------------------------------------------------------------------- #
msg "Creating Python virtual environment"
python3 -m venv "${ZETA_HOME}/venv"
"${ZETA_HOME}/venv/bin/pip" install --quiet --upgrade pip wheel
"${ZETA_HOME}/venv/bin/pip" install --quiet -r "${ZETA_HOME}/backend/requirements.txt"
ok "Panel dependencies installed"

# --------------------------------------------------------------------------- #
# 3b. Dedicated non-root service user
# --------------------------------------------------------------------------- #
# The panel and both proxy cores run as this account, not root (see
# systemd/zeta-*.service) — a bug in the HTTP-facing app can no longer be
# leveraged into arbitrary-file-write-as-root just because the process
# itself was root.
if ! id -u zetavpn >/dev/null 2>&1; then
  useradd --system --no-create-home --shell /usr/sbin/nologin zetavpn
  ok "Created system user 'zetavpn'"
fi

mkdir -p "$ZETA_DATA" "$ZETA_CERT_DIR"

# --------------------------------------------------------------------------- #
# 4. Generate .env (first run only)
# --------------------------------------------------------------------------- #
SERVER_IP="$(server_ip)"
if [ ! -f "$ZETA_ENV" ]; then
  SECRET="$(openssl rand -hex 32)"
  # 18 hex chars (RESEARCH.md's recommended floor for the secret URL path).
  WEBPATH="${ZETA_WEB_BASE_PATH:-zeta-$(openssl rand -hex 9)}"
  ADMIN_USER="${ZETA_ADMIN_USERNAME:-admin}"
  # Over-request bytes before filtering to non-alphanumeric-stripped base64:
  # `+`/`/`/`=` removal can otherwise leave fewer than the intended length.
  ADMIN_PASS="${ZETA_ADMIN_PASSWORD:-$(openssl rand -base64 32 | tr -dc 'A-Za-z0-9' | head -c 16)}"
  cat > "$ZETA_ENV" <<ENV
# ZetaVPN environment — generated by install.sh
ZETA_SECRET_KEY=${SECRET}
ZETA_PORT=${PANEL_PORT}
ZETA_WEB_BASE_PATH=${WEBPATH}
ZETA_SERVER_ADDRESS=${SERVER_IP}
ZETA_SERVER_DOMAIN=${ZETA_DOMAIN:-}
ZETA_ADMIN_USERNAME=${ADMIN_USER}
ZETA_ADMIN_PASSWORD=${ADMIN_PASS}
ZETA_WS_PORT=${WS_PORT}
ENV
  chmod 600 "$ZETA_ENV"
  ok "Configuration written to ${ZETA_ENV}"
else
  warn "Existing ${ZETA_ENV} kept (delete it to regenerate credentials)."
fi
# shellcheck disable=SC1091
set -a; . "$ZETA_ENV"; set +a
# Re-runs don't re-export the operator's env vars, but the installer's own
# consumers read ZETA_DOMAIN / WS_PORT — restore them from what we persisted so
# a plain `./install.sh` re-run keeps the HTTPS vhost and the custom WS port
# instead of silently reverting to plain-HTTP / :8880.
ZETA_DOMAIN="${ZETA_DOMAIN:-${ZETA_SERVER_DOMAIN:-}}"
WS_PORT="${WS_PORT:-${ZETA_WS_PORT:-8880}}"

# --------------------------------------------------------------------------- #
# 5. Install cores + system components
# --------------------------------------------------------------------------- #
bash "${ZETA_HOME}/scripts/install_xray.sh"
bash "${ZETA_HOME}/scripts/install_singbox.sh"
DROPBEAR_PORT_MAIN="$DROPBEAR_PORT_MAIN" DROPBEAR_PORT_ALT="$DROPBEAR_PORT_ALT" \
  STUNNEL_PORT="$STUNNEL_PORT" WS_PORT="$WS_PORT" bash "${ZETA_HOME}/scripts/install_ssh_stack.sh"
bash "${ZETA_HOME}/scripts/tune_bbr.sh"

# --------------------------------------------------------------------------- #
# 5b. Ownership + least-privilege sudo for the 'zetavpn' service user
# --------------------------------------------------------------------------- #
msg "Granting 'zetavpn' the exact access the panel needs (no more)"
mkdir -p /var/log/zetavpn
chown -R zetavpn:zetavpn "$ZETA_HOME" /var/log/zetavpn "$XRAY_DIR" "$SINGBOX_DIR" "$ZETA_CERT_DIR"
chmod 750 "$ZETA_HOME"

# Modern sudo rejects wildcards in command *arguments* (only the command path
# may glob), so a sudoers rule alone can't safely say "useradd with any
# username/date but nothing else". zeta-privileged is the one fixed-path
# command sudoers grants; it validates arguments itself before touching a
# real privileged command. Must stay root-owned, not writable by zetavpn —
# if 'zetavpn' could edit it, sudo access to run it would be full root.
install -m 0755 -o root -g root "${ZETA_HOME}/scripts/zeta-privileged" /usr/local/sbin/zeta-privileged
install -m 0755 -o root -g root "${ZETA_HOME}/scripts/zeta-tuning.sh" /usr/local/sbin/zeta-tuning
install -m 0755 -o root -g root "${ZETA_HOME}/scripts/zeta-tgproxy.sh" /usr/local/sbin/zeta-tgproxy

command -v sudo >/dev/null 2>&1 || apt_install sudo
cat > /etc/sudoers.d/zetavpn-panel <<'SUDOERS'
# Managed by ZetaVPN — least-privilege delegation so the panel (running as
# 'zetavpn', see zeta-panel.service) can still manage SSH tunnel accounts and
# reload proxy/SSH-stack services without running the whole app as root.
# Regenerated on every install/update — do not hand-edit.
zetavpn ALL=(root) NOPASSWD: /usr/local/sbin/zeta-privileged
SUDOERS
chmod 0440 /etc/sudoers.d/zetavpn-panel
visudo -cf /etc/sudoers.d/zetavpn-panel || die "Generated sudoers rule failed validation — aborting for safety."
ok "Least-privilege sudo rule installed (/etc/sudoers.d/zetavpn-panel)"

if [ -n "${ZETA_DOMAIN:-}" ]; then
  ZETA_DOMAIN="$ZETA_DOMAIN" bash "${ZETA_HOME}/scripts/install_ssl.sh" || warn "SSL step failed; continuing without a cert."
fi
PANEL_PORT="$PANEL_PORT" WS_PORT="$WS_PORT" ZETA_DOMAIN="${ZETA_DOMAIN:-}" bash "${ZETA_HOME}/scripts/install_nginx.sh"
PANEL_PORT="$PANEL_PORT" DROPBEAR_PORT_MAIN="$DROPBEAR_PORT_MAIN" DROPBEAR_PORT_ALT="$DROPBEAR_PORT_ALT" \
  STUNNEL_PORT="$STUNNEL_PORT" WS_PORT="$WS_PORT" bash "${ZETA_HOME}/scripts/firewall.sh" \
  || warn "Firewall step reported problems."

# --------------------------------------------------------------------------- #
# 6. systemd units + CLI
# --------------------------------------------------------------------------- #
msg "Installing systemd services"
# zeta-panel.service / zeta-ws.service hardcode the default /opt/zetavpn path
# and zeta-ws.service hardcodes the default WS port — rewrite both to the
# actual configured values so a custom ZETA_HOME/WS_PORT still works.
TMP_UNITS="$(mktemp -d)"
for f in "${ZETA_HOME}/systemd/"zeta-*.service; do
  sed -e "s#/opt/zetavpn#${ZETA_HOME}#g" -e "s#--listen 0.0.0.0:8880#--listen 0.0.0.0:${WS_PORT}#" \
    "$f" > "${TMP_UNITS}/$(basename "$f")"
done
install -m 0644 "${TMP_UNITS}/"zeta-*.service /etc/systemd/system/
rm -rf "$TMP_UNITS"
install -m 0755 "${ZETA_HOME}/bin/zeta" /usr/local/bin/zeta
systemctl daemon-reload
systemctl enable --now zeta-panel.service
systemctl enable --now zeta-xray.service
# Do NOT enable sing-box at boot: it would start a second Go core (~35MB RSS)
# on every reboot even on Xray-only / SSH-only boxes, serving nothing. The
# panel enables + starts it on demand when the first Hysteria2/TUIC inbound is
# created and disables it again when the last one is removed (core/singbox.apply).
systemctl disable zeta-singbox.service >/dev/null 2>&1 || true
systemctl enable --now zeta-ws.service
ok "Services installed and started"

# --- Low-memory hardening (<=768MB) ---------------------------------------- #
# Cap the always-on heavy units and shrink the WS proxy via systemd drop-ins,
# and add a swapfile, so the stack survives on a 256-512MB box. Drop-ins mean
# larger boxes are completely unaffected (no cap is written there).
MEM_KB="$(awk '/^MemTotal:/{print $2}' /proc/meminfo 2>/dev/null || echo 0)"
if [ "${MEM_KB:-0}" -gt 0 ] && [ "$MEM_KB" -le 786432 ]; then
  msg "Low-memory box (~$((MEM_KB/1024))MB) — applying lean caps + swap"
  _lowmem_drop() {  # <unit> <extra [Service] lines>
    mkdir -p "/etc/systemd/system/$1.d"
    printf '[Service]\n%s\n' "$2" > "/etc/systemd/system/$1.d/lowmem.conf"
  }
  # MemoryHigh is a SOFT cap (throttle reclaim, never OOM-kill); MemoryMax hard.
  _lowmem_drop zeta-panel.service "MemoryHigh=150M"$'\n'"MemoryMax=190M"
  _lowmem_drop zeta-xray.service "MemoryHigh=96M"
  _lowmem_drop zeta-singbox.service "MemoryHigh=96M"
  # ws-proxy: bound concurrent tunnels so pipe buffers can't blow the RAM budget
  # (2000x2x64KB = 250MB+ at the code default). Reset ExecStart before overriding.
  _lowmem_drop zeta-ws.service "MemoryHigh=48M"$'\n'"ExecStart="$'\n'"ExecStart=/usr/bin/python3 ${ZETA_HOME}/proxies/ws-proxy.py --listen 0.0.0.0:${WS_PORT} --backend 127.0.0.1:22 --max-connections 300 --max-per-ip 10"
  systemctl daemon-reload
  systemctl restart zeta-ws.service 2>/dev/null || true
  # Swapfile — the OOM valve vm.swappiness=10 (zeta-tuning) already assumes.
  if [ ! -e /swapfile ] && ! swapon --show=NAME --noheadings 2>/dev/null | grep -q .; then
    fallocate -l 1G /swapfile 2>/dev/null || dd if=/dev/zero of=/swapfile bs=1M count=1024 2>/dev/null || true
    if [ -e /swapfile ]; then
      chmod 600 /swapfile; mkswap /swapfile >/dev/null 2>&1 || true
      swapon /swapfile 2>/dev/null || true
      grep -q '^/swapfile ' /etc/fstab 2>/dev/null || echo '/swapfile none swap sw 0 0' >> /etc/fstab
    fi
  fi
  ok "Low-memory caps + swap applied"
fi

# Install the cron helper that expires SSH accounts.
( crontab -l 2>/dev/null | grep -v 'zeta expire-check'; \
  echo "*/15 * * * * /usr/local/bin/zeta expire-check >/dev/null 2>&1" ) | crontab - 2>/dev/null || true

# --------------------------------------------------------------------------- #
# 7. Summary
# --------------------------------------------------------------------------- #
BASE="/${ZETA_WEB_BASE_PATH:-}"
ADMIN_USER_SHOW="${ZETA_ADMIN_USERNAME:-admin}"
ADMIN_PASS_SHOW="${ZETA_ADMIN_PASSWORD:-<see ${ZETA_DATA}/initial_admin.txt>}"
ACCESS_HOST="${ZETA_DOMAIN:-$SERVER_IP}"
if [ -n "${ZETA_DOMAIN:-}" ] && [ -f "${ZETA_CERT_DIR}/fullchain.pem" ]; then
  PANEL_URL="https://${ACCESS_HOST}${BASE}/"
else
  # No domain/cert: nginx still fronts the panel on plain :80 (see
  # install_nginx.sh) — the panel itself binds 127.0.0.1 and PANEL_PORT is
  # never opened in the firewall, so this is the only reachable URL.
  PANEL_URL="http://${SERVER_IP}${BASE}/"
fi

cat <<SUMMARY

$(ok "ZetaVPN installation complete!")

  ┌───────────────────────────────────────────────
  │  Panel URL : ${PANEL_URL}
  │  Username  : ${ADMIN_USER_SHOW}
  │  Password  : ${ADMIN_PASS_SHOW}
  ├───────────────────────────────────────────────
  │  CLI menu  : run  'zeta'
  │  SSH ports : 22 · ${DROPBEAR_PORT_MAIN}/${DROPBEAR_PORT_ALT} (dropbear) · ${STUNNEL_PORT} (SSL) · ${WS_PORT} (WS)
  │  Config    : ${ZETA_ENV}
  └───────────────────────────────────────────────

  Next: set your domain under Settings, add inbounds, create clients.
SUMMARY
