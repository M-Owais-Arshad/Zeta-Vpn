#!/usr/bin/env bash
# Remove ZetaVPN. Keeps Xray/sing-box binaries by default; pass --purge to also
# remove the cores, SSH-stack config, firewall/fail2ban rules and all data.
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
for u in zeta-panel zeta-xray zeta-singbox zeta-ws zeta-badvpn zeta-mtproxy zeta-bot zeta-gaming-tune; do
  systemctl disable --now "$u" 2>/dev/null || true
  rm -f "/etc/systemd/system/${u}.service"
done
systemctl daemon-reload

rm -f /usr/local/bin/zeta
crontab -l 2>/dev/null | grep -v 'zeta expire-check' | crontab - 2>/dev/null || true
rm -f /etc/nginx/conf.d/zeta.conf /etc/nginx/conf.d/zeta-gzip.conf /etc/nginx/zeta-inbounds.conf
systemctl reload nginx 2>/dev/null || true
# The panel process is already gone (units removed above), so its sudo
# delegation is dead weight either way — always drop it. Revert the elite
# gaming tuning FIRST (while its helper still exists), so we never leave the
# kernel permanently tuned after the panel that managed it is gone, then drop
# every privileged helper install.sh laid down (not just zeta-privileged).
if [ -x /usr/local/sbin/zeta-tuning ]; then
  /usr/local/sbin/zeta-tuning revert >/dev/null 2>&1 || true
fi
rm -f /etc/sudoers.d/zetavpn-panel \
      /usr/local/sbin/zeta-privileged \
      /usr/local/sbin/zeta-tuning \
      /usr/local/sbin/zeta-tgproxy
# The gaming sysctl drop-in is re-applied by systemd-sysctl on EVERY boot,
# independently of any ZetaVPN unit — removing the service alone doesn't stop
# it, so delete the file explicitly (revert above normally does, this is
# belt-and-suspenders). Older builds named it 99-zeta-gaming.conf.
rm -f /etc/sysctl.d/99-zzz-zeta-gaming.conf /etc/sysctl.d/99-zeta-gaming.conf

if [ "$PURGE" -eq 1 ]; then
  warn "Purging cores, SSH-stack config, firewall rules and data"
  rm -f "$XRAY_BIN" "$SINGBOX_BIN"
  rm -rf "$XRAY_DIR" "$SINGBOX_DIR" "$ZETA_CERT_DIR" "$ZETA_HOME" /var/log/zetavpn
  rm -f /etc/sysctl.d/99-zeta.conf /etc/ssh/sshd_config.d/zeta.conf /etc/ssh/sshd_config.d/00-zeta.conf

  # v1.3 artifacts: the MTProto proxy (mtg binary + /etc/mtg, which holds the
  # live FakeTLS secret at rest), the UDPGW gateway, and the tuning snapshot
  # backups. Removing the units above doesn't touch these on-disk files.
  rm -f /usr/local/bin/mtg /usr/local/bin/badvpn-udpgw
  rm -rf /etc/mtg /var/backups/zeta-tune
  # Reverted sysctls take effect on the next boot regardless; re-apply now so
  # the box is back to its stock kernel tuning immediately.
  sysctl --system >/dev/null 2>&1 || true

  # SSH stack: stop the services and drop ZetaVPN's own config for them.
  # The dropbear/stunnel4 *packages* are left installed (apt remove could
  # affect something unrelated to ZetaVPN) but disabled, since the config
  # that made them useful for tunnelling is gone.
  systemctl disable --now dropbear 2>/dev/null || true
  systemctl disable --now stunnel4 2>/dev/null || true
  rm -f /etc/stunnel/stunnel.conf /etc/stunnel/stunnel.pem
  # Revert install_ssh_stack.sh's sed edits to the dropbear package config
  # (harmless while the service is disabled, but leaving it means a later
  # `apt install --reinstall dropbear` or manual re-enable would silently
  # come back up on ZetaVPN's ports instead of the package default).
  if [ -f /etc/default/dropbear ]; then
    sed -i "s/^NO_START=.*/NO_START=1/" /etc/default/dropbear
    sed -i '/^DROPBEAR_EXTRA_ARGS=/d' /etc/default/dropbear
  fi

  # Revert exactly the ufw rules firewall.sh added (same ports/defaults it
  # uses — a custom install may have used different SSH-stack ports, which
  # this can't know in retrospect; best-effort).
  if command -v ufw >/dev/null 2>&1; then
    # 8443 = the MTProto proxy's default port (ZETA_MTPROXY_PORT).
    for p in 22 80 443 109 149 143 445 8880 8443; do
      ufw delete allow "${p}/tcp" >/dev/null 2>&1 || true
    done
    ufw delete allow 443/udp >/dev/null 2>&1 || true
  fi
  rm -f /etc/fail2ban/jail.d/zeta-sshd.conf
  systemctl restart fail2ban 2>/dev/null || true

  # Dedicated service account (see install.sh step 3b).
  userdel -r zetavpn 2>/dev/null || true

  ok "ZetaVPN fully removed."
else
  ok "ZetaVPN services removed. Data kept in ${ZETA_HOME} (use --purge to delete)."
fi
