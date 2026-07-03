#!/usr/bin/env bash
# Install / update sing-box from the official SagerNet GitHub releases.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
. "${HERE}/common.sh"

need_root
detect_arch

REPO="SagerNet/sing-box"
VERSION="${SINGBOX_VERSION:-$(gh_latest "$REPO")}"
[ -n "$VERSION" ] || VERSION="v1.9.3"
VNUM="${VERSION#v}"

msg "Installing sing-box ${VERSION} (${ARCH_SB})"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

PKG="sing-box-${VNUM}-linux-${ARCH_SB}"
download "https://github.com/${REPO}/releases/download/${VERSION}/${PKG}.tar.gz" "${TMP}/sb.tar.gz"
tar -xzf "${TMP}/sb.tar.gz" -C "$TMP"
install -m 0755 "${TMP}/${PKG}/sing-box" "$SINGBOX_BIN"

mkdir -p "$SINGBOX_DIR"
if [ ! -s "${SINGBOX_DIR}/config.json" ]; then
  cat > "${SINGBOX_DIR}/config.json" <<'JSON'
{ "log": { "level": "warn" },
  "inbounds": [],
  "outbounds": [ { "type": "direct", "tag": "direct" } ] }
JSON
fi

ok "sing-box installed: $("$SINGBOX_BIN" version 2>/dev/null | head -n1)"
