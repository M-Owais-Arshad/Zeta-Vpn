#!/usr/bin/env bash
# Install / update Xray-core from the official XTLS GitHub releases.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
. "${HERE}/common.sh"

need_root
detect_arch

REPO="XTLS/Xray-core"
VERSION="${XRAY_VERSION:-$(gh_latest "$REPO")}"
[ -n "$VERSION" ] || VERSION="v1.8.24"

msg "Installing Xray-core ${VERSION} (${ARCH_XRAY})"
command -v unzip >/dev/null 2>&1 || apt_install unzip

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
BASE_URL="https://github.com/${REPO}/releases/download/${VERSION}/Xray-linux-${ARCH_XRAY}.zip"
download "$BASE_URL" "${TMP}/xray.zip"

# Best-effort integrity check against the published .dgst (SHA256).
if curl -fsSL "${BASE_URL}.dgst" -o "${TMP}/xray.dgst" 2>/dev/null; then
  want="$(grep -iE 'sha2?-?256' "${TMP}/xray.dgst" | grep -oiE '[0-9a-f]{64}' | head -n1)"
  if [ -n "$want" ]; then
    got="$(sha256sum "${TMP}/xray.zip" | awk '{print $1}')"
    [ "$want" = "$got" ] && ok "SHA256 verified" || die "SHA256 mismatch — refusing to install (want ${want}, got ${got})"
  else
    warn "Could not parse SHA256 from .dgst — skipping verification"
  fi
else
  warn "No .dgst published — skipping checksum verification"
fi

unzip -oq "${TMP}/xray.zip" -d "${TMP}/xray"
install -m 0755 "${TMP}/xray/xray" "$XRAY_BIN"

mkdir -p "$XRAY_DIR" /usr/local/share/xray
for geo in geoip.dat geosite.dat; do
  [ -f "${TMP}/xray/${geo}" ] && install -m 0644 "${TMP}/xray/${geo}" "/usr/local/share/xray/${geo}"
done

# Minimal placeholder config so the unit can start before the panel renders one.
if [ ! -s "${XRAY_DIR}/config.json" ]; then
  cat > "${XRAY_DIR}/config.json" <<'JSON'
{ "log": { "loglevel": "warning" },
  "inbounds": [],
  "outbounds": [ { "protocol": "freedom", "tag": "direct" } ] }
JSON
fi

ok "Xray-core installed: $("$XRAY_BIN" version 2>/dev/null | head -n1)"
