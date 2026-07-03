#!/usr/bin/env bash
# ZetaVPN — shared shell helpers sourced by every install/manage script.
# shellcheck disable=SC2034

set -o pipefail

# ---- paths ----
ZETA_HOME="${ZETA_HOME:-/opt/zetavpn}"
ZETA_DATA="${ZETA_HOME}/data"
ZETA_ENV="${ZETA_HOME}/.env"
ZETA_CERT_DIR="${ZETA_CERT_DIR:-/etc/zetavpn/certs}"
XRAY_BIN="/usr/local/bin/xray"
XRAY_DIR="/usr/local/etc/xray"
SINGBOX_BIN="/usr/local/bin/sing-box"
SINGBOX_DIR="/etc/sing-box"

# ---- colours ----
if [ -t 1 ]; then
  C_RESET='\033[0m'; C_DIM='\033[2m'; C_RED='\033[31m'; C_GRN='\033[32m'
  C_YEL='\033[33m'; C_BLU='\033[34m'; C_MAG='\033[35m'; C_CYN='\033[36m'; C_BOLD='\033[1m'
else
  C_RESET=; C_DIM=; C_RED=; C_GRN=; C_YEL=; C_BLU=; C_MAG=; C_CYN=; C_BOLD=
fi

msg()  { printf "${C_CYN}::${C_RESET} %s\n" "$*"; }
ok()   { printf "${C_GRN} ✓${C_RESET} %s\n" "$*"; }
warn() { printf "${C_YEL} !${C_RESET} %s\n" "$*"; }
err()  { printf "${C_RED} ✗${C_RESET} %s\n" "$*" >&2; }
die()  { err "$*"; exit 1; }

banner() {
  printf "${C_MAG}${C_BOLD}"
  cat <<'EOF'
  ███████╗███████╗████████╗ █████╗ ██╗   ██╗██████╗ ███╗   ██╗
  ╚══███╔╝██╔════╝╚══██╔══╝██╔══██╗██║   ██║██╔══██╗████╗  ██║
    ███╔╝ █████╗     ██║   ███████║██║   ██║██████╔╝██╔██╗ ██║
   ███╔╝  ██╔══╝     ██║   ██╔══██║╚██╗ ██╔╝██╔═══╝ ██║╚██╗██║
  ███████╗███████╗   ██║   ██║  ██║ ╚████╔╝ ██║     ██║ ╚████║
  ╚══════╝╚══════╝   ╚═╝   ╚═╝  ╚═╝  ╚═══╝  ╚═╝     ╚═╝  ╚═══╝
EOF
  printf "${C_RESET}${C_DIM}        All-in-one VPN / proxy panel · every protocol${C_RESET}\n\n"
}

need_root() {
  [ "$(id -u)" -eq 0 ] || die "This script must be run as root (use sudo)."
}

detect_os() {
  [ -r /etc/os-release ] || die "Cannot detect OS (/etc/os-release missing)."
  # shellcheck disable=SC1091
  . /etc/os-release
  OS_ID="$ID"
  OS_VER="${VERSION_ID:-}"
  case "$ID" in
    ubuntu|debian) : ;;
    *) warn "Untested OS '$ID' — proceeding, Debian/Ubuntu is recommended." ;;
  esac
}

detect_arch() {
  case "$(uname -m)" in
    x86_64|amd64)   ARCH_XRAY="64";        ARCH_SB="amd64" ;;
    aarch64|arm64)  ARCH_XRAY="arm64-v8a"; ARCH_SB="arm64" ;;
    armv7l)         ARCH_XRAY="arm32-v7a"; ARCH_SB="armv7" ;;
    *) die "Unsupported CPU architecture: $(uname -m)" ;;
  esac
}

# gh_latest <owner/repo> -> prints latest release tag (e.g. v1.8.4)
gh_latest() {
  local repo="$1" tag
  tag="$(curl -fsSL "https://api.github.com/repos/${repo}/releases/latest" 2>/dev/null \
        | grep -m1 '"tag_name"' | cut -d'"' -f4)"
  printf '%s' "$tag"
}

# download <url> <dest>
download() {
  local url="$1" dest="$2"
  msg "Downloading ${url##*/}"
  curl -fL --retry 3 --connect-timeout 20 -o "$dest" "$url" \
    || wget -q -O "$dest" "$url" \
    || die "Download failed: $url"
}

server_ip() {
  curl -fsSL4 https://api.ipify.org 2>/dev/null \
    || curl -fsSL https://ifconfig.me 2>/dev/null \
    || hostname -I | awk '{print $1}'
}

apt_install() {
  DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends "$@" >/dev/null 2>&1 \
    || DEBIAN_FRONTEND=noninteractive apt-get install -y "$@"
}

systemd_enable_now() {
  systemctl daemon-reload
  for unit in "$@"; do
    systemctl enable "$unit" >/dev/null 2>&1 || true
    systemctl restart "$unit" || warn "Failed to start $unit"
  done
}
