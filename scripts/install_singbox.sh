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

# Best-effort integrity check against the release's published checksums.txt
# (goreleaser's default naming) — same verify-or-warn-and-skip approach as
# install_xray.sh, so a MITM'd/corrupted download is refused rather than
# silently installed (the pattern behind CVE-2025-29331 in a sibling panel).
CHECKSUMS_URL="https://github.com/${REPO}/releases/download/${VERSION}/sing-box_${VNUM}_checksums.txt"
if curl -fsSL "$CHECKSUMS_URL" -o "${TMP}/checksums.txt" 2>/dev/null; then
  want="$(grep -F "${PKG}.tar.gz" "${TMP}/checksums.txt" 2>/dev/null | awk '{print $1}' | head -n1)"
  if [ -n "$want" ]; then
    got="$(sha256sum "${TMP}/sb.tar.gz" | awk '{print $1}')"
    [ "$want" = "$got" ] && ok "SHA256 verified" || die "SHA256 mismatch — refusing to install (want ${want}, got ${got})"
  else
    warn "Could not find ${PKG}.tar.gz in checksums.txt — skipping verification"
  fi
else
  warn "No checksums.txt published — skipping checksum verification"
fi

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
