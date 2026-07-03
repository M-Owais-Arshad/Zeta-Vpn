#!/usr/bin/env bash
# Remove ZetaVPN. Keeps Xray/sing-box binaries by default; pass --purge to also
# remove the cores and all data.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
. "${HERE}/common.sh"
need_root

PURGE=0
[ "${1:-}" = "--purge" ] && PURGE=1

read -rp "Really uninstall ZetaVPN? [y/N] " a
[ "${a,,}" = "y" ] || { echo "Aborted."; exit 0; }

msg "Stopping and disabling services"
for u in zeta-panel zeta-xray zeta-singbox zeta-ws; do
  systemctl disable --now "$u" 2>/dev/null || true
  rm -f "/etc/systemd/system/${u}.service"
done
systemctl daemon-reload

rm -f /usr/local/bin/zeta
crontab -l 2>/dev/null | grep -v 'zeta expire-check' | crontab - 2>/dev/null || true
rm -f /etc/nginx/conf.d/zeta.conf; systemctl reload nginx 2>/dev/null || true

if [ "$PURGE" -eq 1 ]; then
  warn "Purging cores, configs and data"
  rm -f "$XRAY_BIN" "$SINGBOX_BIN"
  rm -rf "$XRAY_DIR" "$SINGBOX_DIR" "$ZETA_CERT_DIR" "$ZETA_HOME"
  rm -f /etc/sysctl.d/99-zeta.conf /etc/ssh/sshd_config.d/zeta.conf
  ok "ZetaVPN fully removed."
else
  ok "ZetaVPN services removed. Data kept in ${ZETA_HOME} (use --purge to delete)."
fi
